"""Wide-catalog deterministic clustering, single-turn parallel sharding, and merge."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import ValidationError

from opendv.modeling.ports import (
    ModelingEventSink,
    ModelingHarnessPort,
    ModelingPackLoader,
    ModelingTurnRequest,
    ModelingTurnResult,
    resolve_model_for_agent,
)
from opendv.modeling.prompts import _load_pack_text, build_ship_vault_prompt
from opendv.modeling.token_utils import (
    DEFAULT_CONTEXT_SOFT_CEILING_TOKENS,
    compact_json_dumps,
    count_tokens,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences, RemediationHint, TableCluster
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CegarPolicy, HarnessBudget
from opendv.models.project import EvolutionLockSpec, HorizonAssumptionSpec, OntologyProposalSpec
from opendv.models.sources import ColumnProfile, SourceProfile, TableProfile
from opendv.models.vault_ir import DraftBridgeSpec, DraftHubSpec, DraftShardIR, DraftVaultIR
from opendv.models.wire_dtos_discovery import HoldAnswersReq
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.verifier.l1_codemods import inject_unresolved_triage, merge_draft_shards
from opendv.verifier.ports import VerifierReport

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SHARD_SFX_RE = re.compile(r"(?:_p(?:art)?\d+|_\d{2,8}|_shard\d+)$", re.IGNORECASE)
_MAX_PROJ_OUT = 6_500


def preallocate_canonical_hubs(ontology: GraphAOntology) -> list[str]:
    """Derive deterministic canonical Hub names from Graph A concepts."""
    return list(dict.fromkeys(
        f"hub_{slug}" for c in ontology.concepts
        if (slug := _SLUG_RE.sub("_", c.concept_name.strip().lower()).strip("_"))
    ))


def _est_tok(tables: Sequence[TableProfile]) -> int:
    return count_tokens(compact_json_dumps([
        {"id": t.table_id,
         "cols": [(c.col_name, c.sql_type, c.pk_ord, c.fk_ref) for c in t.columns]}
        for t in tables
    ]))


def _pinned(c: ColumnProfile, t: TableProfile, fk_cols: set[str], hubs: set[str]) -> bool:
    if c.is_pk or c.pk_ord is not None or c.fk_ref is not None:
        return True
    if t.best_non_surrogate_bk_candidate_col == c.col_name:
        return True
    low = c.col_name.lower()
    return low in fk_cols or any(low in (s, f"{s}_id", f"{s}_key", f"{s}_code") for s in hubs)


def _split_wide_table(
    tbl: TableProfile, *, max_cols: int, tok_cap: int, fk_cols: set[str], hubs: set[str]
) -> list[TableProfile]:
    if len(tbl.columns) <= max_cols and _est_tok([tbl]) <= tok_cap:
        return [tbl]
    pin = [c for c in tbl.columns if _pinned(c, tbl, fk_cols, hubs)]
    pin_set = {c.col_name for c in pin}
    rest = [c for c in tbl.columns if c.col_name not in pin_set]
    if not rest:
        step = max(1, max_cols)
        return [tbl.model_copy(update={"columns": tbl.columns[i : i + step]})
                for i in range(0, len(tbl.columns), step)]
    if len(pin) >= max_cols:
        pin = pin[: max(1, max_cols // 3)]
        ps = {c.col_name for c in pin}
        rest = [c for c in tbl.columns if c.col_name not in ps]
    room = max(1, max_cols - len(pin))
    out: list[TableProfile] = []
    idx = 0
    while idx < len(rest):
        take = room
        cand = tbl.model_copy(update={"columns": [*pin, *rest[idx : idx + take]]})
        while take > 1 and _est_tok([cand]) > tok_cap:
            take = max(1, take // 2)
            cand = tbl.model_copy(update={"columns": [*pin, *rest[idx : idx + take]]})
        out.append(cand)
        idx += take
    return out


def _fallback_groups(tables: Sequence[TableProfile]) -> list[tuple[str, list[TableProfile]]]:
    if not tables:
        return []
    if any(t.cluster_id for t in tables):
        by_c: dict[str, list[TableProfile]] = defaultdict(list)
        for t in sorted(tables, key=lambda x: x.table_id):
            by_c[t.cluster_id or "cluster_default"].append(t)
        return sorted(by_c.items())

    n2id = {t.table_name: t.table_id for t in tables}
    stems: dict[str, list[str]] = defaultdict(list)
    adj: dict[str, set[str]] = {t.table_id: set() for t in tables}
    for t in tables:
        stems[_SHARD_SFX_RE.sub("", t.table_name.strip().lower()) or t.table_name.lower()].append(
            t.table_id
        )
        for c in t.columns:
            if c.fk_ref:
                tgt = n2id.get(c.fk_ref.split(".")[-2] if "." in c.fk_ref else c.fk_ref)
                if tgt and tgt != t.table_id:
                    adj[t.table_id].add(tgt)
                    adj[tgt].add(t.table_id)
    for ids in stems.values():
        for o in ids[1:]:
            adj[ids[0]].add(o)
            adj[o].add(ids[0])
    by_id = {t.table_id: t for t in tables}
    vis: set[str] = set()
    groups: list[tuple[str, list[TableProfile]]] = []
    for tid in sorted(by_id):
        if tid in vis:
            continue
        stk, comp = [tid], []
        vis.add(tid)
        while stk:
            cur = stk.pop()
            comp.append(by_id[cur])
            for nb in sorted(adj[cur]):
                if nb not in vis:
                    vis.add(nb)
                    stk.append(nb)
        comp.sort(key=lambda x: x.table_id)
        groups.append((f"cluster_{len(groups) + 1:03d}", comp))
    return groups


def resolve_cluster_assignments(
    profiles: Sequence[SourceProfile],
    clusters: Sequence[tuple[str, Sequence[str]] | TableCluster] = (),
    *,
    max_tables_per_shard: int = 40,
    max_cols_per_shard: int = 150,
    token_threshold: int = DEFAULT_CONTEXT_SOFT_CEILING_TOKENS,
    preallocated_hubs: Sequence[str] = (),
) -> list[tuple[str, list[TableProfile]]]:
    """Resolve deterministic (cluster_id, tables) batches bounded by tables, cols, and tokens."""
    t_id = {t.table_id: t for p in profiles for t in p.tables}
    t_nm = {t.table_name: t for p in profiles for t in p.tables}
    if not t_id:
        return []
    raw_groups: list[tuple[str, list[TableProfile]]] = []
    if clusters:
        seen: set[str] = set()
        norm = [(c.cluster_id, c.table_ids) if isinstance(c, TableCluster) else c for c in clusters]
        for cid, tids in sorted(norm, key=lambda x: x[0]):
            hit = [
                m for tid in tids
                if (m := t_id.get(tid) or t_nm.get(tid)) is not None
                and m.table_id not in seen and not seen.add(m.table_id)  # type: ignore[func-returns-value]
            ]
            if hit:
                raw_groups.append((cid, hit))
        rem = [t for tid, t in sorted(t_id.items()) if tid not in seen]
        if rem:
            raw_groups.append(("cluster_unassigned", rem))
    else:
        raw_groups = _fallback_groups(list(t_id.values()))

    fk_cols = {nm for t in t_id.values() for c in t.columns if c.fk_ref
               for nm in (c.fk_ref.split(".")[-1].lower(), c.col_name.lower())}
    hubs = {h.removeprefix("hub_").lower() for h in preallocated_hubs}
    bounded: list[tuple[str, list[TableProfile]]] = []
    for cid, grp in raw_groups:
        exp = [sl for t in grp for sl in _split_wide_table(
            t, max_cols=max_cols_per_shard, tok_cap=token_threshold, fk_cols=fk_cols, hubs=hubs
        )]
        batches: list[list[TableProfile]] = []
        cur: list[TableProfile] = []
        ccols = 0
        for sl in exp:
            sc = len(sl.columns)
            if cur and (len(cur) + 1 > max_tables_per_shard or ccols + sc > max_cols_per_shard
                        or _est_tok([*cur, sl]) > token_threshold):
                batches.append(cur)
                cur, ccols = [sl], sc
            else:
                cur.append(sl)
                ccols += sc
        if cur:
            batches.append(cur)
        if len(batches) == 1:
            bounded.append((cid, batches[0]))
        else:
            bounded.extend((f"{cid}__part{i:02d}", b) for i, b in enumerate(batches, start=1))
    return bounded


def scale_shard_budget(
    base_budget: HarnessBudget, shard_tables: Sequence[TableProfile], *, num_shards: int
) -> HarnessBudget:
    """Scale per-shard output max_tokens dynamically (<= 6,500 ceiling) and local retries."""
    _ = num_shards
    cols = sum(len(t.columns) for t in shard_tables)
    proj = min(_MAX_PROJ_OUT, max(1_024, cols * 45 + 512))
    return base_budget.model_copy(update={
        "max_turns": max(2, min(base_budget.max_turns, 3)),
        "max_tokens": min(base_budget.max_tokens, proj),
    })


def preserve_post_merge_draft(
    merged: DraftVaultIR, *, prior_draft_ir: DraftVaultIR | None = None,
    frozen_shared_hubs: Mapping[str, Sequence[str]] | None = None,
) -> DraftVaultIR:
    """Preserve multi-shard Bridges, Hub survivorship priority, and frozen Hub key order."""
    if prior_draft_ir is None and not frozen_shared_hubs:
        return merged
    hnames = {h.hub_name for h in merged.hubs}
    lnames = {k.link_name for k in merged.links}
    hubs: list[DraftHubSpec] = list(merged.hubs)
    if prior_draft_ir is not None:
        pmap = {h.hub_name: h for h in prior_draft_ir.hubs}
        hubs = [
            h.model_copy(update={"survivorship_priority": list(dict.fromkeys(
                [*pmap[h.hub_name].survivorship_priority, *h.survivorship_priority]
            ))})
            if h.hub_name in pmap and pmap[h.hub_name].survivorship_priority else h
            for h in hubs
        ]
    if frozen_shared_hubs:
        reord: list[DraftHubSpec] = []
        for h in hubs:
            fc = list(frozen_shared_hubs.get(h.hub_name, ()))
            if fc and h.business_keys:
                oi = {c: i for i, c in enumerate(fc)}
                bks = sorted(h.business_keys,
                             key=lambda b: (oi.get(b.col_name, 999), b.table_id, b.col_name))
                h = h.model_copy(update={"business_keys": bks})
            reord.append(h)
        hubs = reord

    bridges: list[DraftBridgeSpec] = list(merged.bridges)
    if prior_draft_ir is not None and prior_draft_ir.bridges:
        seen = {b.bridge_name for b in bridges}
        bridges.extend(
            b for b in prior_draft_ir.bridges
            if b.bridge_name not in seen and b.root_hub in hnames
            and all(hop.link_name in lnames for hop in b.hops)
        )
    return merged.model_copy(update={"hubs": hubs, "bridges": bridges})


async def _run_turn_retry[T: (DraftShardIR, DraftVaultIR)](
    *, harness: ModelingHarnessPort, req: ModelingTurnRequest,
    schema: type[T], sink: ModelingEventSink | None, max_retries: int = 2,
) -> tuple[T, ModelingTurnResult, int]:
    active = req
    for n in range(max_retries + 1):
        try:
            raw = await harness.run_turn(active, event_sink=sink)
            res = (
                raw if isinstance(raw, ModelingTurnResult)
                else ModelingTurnResult.model_validate(raw)
            )
            out = (res.structured_output if isinstance(res.structured_output, schema)
                   else schema.model_validate(res.structured_output))
            return out, res, n
        except OpenDVError as exc:
            if n >= max_retries or exc.error_code != ErrorCode.AGENT_EXECUTION_FAILED:
                raise
            err = str(exc)
        except (ValidationError, ValueError) as exc:
            if n >= max_retries:
                raise OpenDVError(
                    f"Structured output validation failed for {schema.__name__}: {exc}",
                    error_code=ErrorCode.AGENT_EXECUTION_FAILED, run_id=req.run_id,
                ) from exc
            err = str(exc)
        active = req.model_copy(update={
            "prompt": f"{req.prompt}\n\nINVALID OUTPUT RETRY:\n{err[:240]}\n"
                      "Return ONLY one valid compact JSON object."
        })
    raise OpenDVError(f"Exceeded {max_retries} retries.",
                      error_code=ErrorCode.AGENT_EXECUTION_FAILED, run_id=req.run_id)


def _collect_props(res: ModelingTurnResult, wave_number: int) -> list[OntologyProposalSpec]:
    out: list[OntologyProposalSpec] = []
    for ev in res.tool_events:
        raw = ev.get("ontology_proposals")
        if not isinstance(raw, list):
            continue
        for idx, item in enumerate(raw):
            if isinstance(item, OntologyProposalSpec):
                out.append(item.model_copy(update={"status": "PENDING_STEWARD"}))
            elif isinstance(item, dict):
                merged = {"wave_number": wave_number, "status": "PENDING_STEWARD", **item}
                merged.setdefault("proposal_id", f"prop-w{wave_number}-{idx + 1}")
                out.append(OntologyProposalSpec.model_validate(merged))
    return out


async def run_ship_vault_synthesis(
    *, run_id: str, project_id: str, wave_number: int, attempt: int,
    ontology: GraphAOntology, profiles: Sequence[SourceProfile],
    clusters: Sequence[tuple[str, Sequence[str]] | TableCluster] = (),
    locks: EvolutionLockSpec | None = None, prior_report: VerifierReport | None = None,
    hints: Sequence[str] = (), hold_answers: HoldAnswersReq | None = None,
    policy: CegarPolicy = "balanced", budget: HarnessBudget, harness: ModelingHarnessPort,
    pack_loader: ModelingPackLoader | None = None,
    model_overrides: Mapping[str, str] | None = None,
    event_sink: ModelingEventSink | None = None,
    prior_v7_verdict: SemanticCriticVerdict | None = None,
    prior_v8_verdict: HorizonCriticVerdict | None = None,
    prior_draft_ir: DraftVaultIR | None = None,
    modeling_preferences: ModelingPreferences | None = None,
    business_context_hints: str | None = None, guide_markdown: str | None = None,
    remediation_hints: Sequence[RemediationHint] = (),
    horizon_assumptions: Sequence[HorizonAssumptionSpec] = (),
    frozen_shared_hubs: Mapping[str, Sequence[str]] | None = None,
) -> tuple[DraftVaultIR, list[OntologyProposalSpec], int, int, Literal["reported", "estimated"]]:
    """Execute @ship-vault in single-turn or parallel sharded mode with triage preservation."""
    sys_inst = _load_pack_text("ship-vault", pack_loader)
    pre_hubs = preallocate_canonical_hubs(ontology)
    resolved = resolve_model_for_agent(
        harness.provider_name, "STANDARD_WORKER", agent_id="ship-vault",
        model_overrides=model_overrides, cegar_policy=policy,
    )
    ship_ovr = resolved.model_id if resolved.source != "default" else None
    batches = resolve_cluster_assignments(profiles, clusters, preallocated_hubs=pre_hubs)
    total_tables = sum(len(p.tables) for p in profiles)
    total_cols = sum(len(t.columns) for p in profiles for t in p.tables)

    def _prompt(cid: str | None = None, ctbls: Sequence[TableProfile] = ()) -> str:
        return build_ship_vault_prompt(
            project_id=project_id, wave_number=wave_number, ontology=ontology,
            profiles=profiles, locks=locks, prior_report=prior_report, hints=hints,
            hold_answers=hold_answers, preallocated_hubs=pre_hubs, cluster_id=cid,
            cluster_tables=ctbls, prior_v7_verdict=prior_v7_verdict,
            modeling_preferences=modeling_preferences,
            business_context_hints=business_context_hints, guide_markdown=guide_markdown,
            pack_loader=pack_loader, remediation_hints=remediation_hints,
            horizon_assumptions=horizon_assumptions, prior_v8_verdict=prior_v8_verdict,
            prior_draft_ir=prior_draft_ir, frozen_shared_hubs=frozen_shared_hubs,
        )

    def _req(
        prompt: str, schema: type[DraftShardIR] | type[DraftVaultIR],
        tb: HarnessBudget, cid: str | None = None, cols: int = total_cols,
    ) -> ModelingTurnRequest:
        return ModelingTurnRequest(
            run_id=run_id, agent_id="ship-vault", step="step6", prompt=prompt,
            system_instructions=sys_inst, output_schema=schema, model_tier="STANDARD_WORKER",
            model_override=ship_ovr, cegar_policy=policy, role_kind="none", allowed_tools=[],
            budget=tb, attempt=attempt, cluster_id=cid,
            metadata={"model_overrides": dict(model_overrides or {}),
                      "preallocated_hubs": pre_hubs, "sharded": cid is not None,
                      "projected_output_tokens": min(_MAX_PROJ_OUT, max(1_024, cols * 45 + 512)),
                      "shard_count": len(batches)},
        )

    full_prompt: str | None = None
    should_shard = len(batches) > 1 or total_tables > 40 or total_cols > 150
    if not should_shard:
        full_prompt = _prompt()
        if count_tokens(full_prompt) > DEFAULT_CONTEXT_SOFT_CEILING_TOKENS and batches:
            should_shard = True

    if should_shard and batches:
        ct_map = {cid: [t.table_id for t in c_tbls] for cid, c_tbls in batches}
        outs = await asyncio.gather(*(
            _run_turn_retry(
                harness=harness,
                req=_req(_prompt(cid, c_tbls), DraftShardIR,
                         scale_shard_budget(budget, c_tbls, num_shards=len(batches)),
                         cid, sum(len(t.columns) for t in c_tbls)),
                schema=DraftShardIR, sink=event_sink,
            )
            for cid, c_tbls in batches
        ))
        merged = merge_draft_shards(
            [o[0] for o in outs], project_id=project_id,
            ontology_version=ontology.ontology_version, wave_number=wave_number,
            cluster_tables_by_id=ct_map,
        )
        merged = preserve_post_merge_draft(
            inject_unresolved_triage(merged, profiles),
            prior_draft_ir=prior_draft_ir, frozen_shared_hubs=frozen_shared_hubs,
        )
        props = [p for o in outs for p in _collect_props(o[1], wave_number)]
        src: Literal["reported", "estimated"] = (
            "reported" if any(o[1].token_count_source == "reported" for o in outs) else "estimated"
        )
        return merged, props, 1, sum(o[1].total_tokens for o in outs), src

    draft, res, failed = await _run_turn_retry(
        harness=harness, req=_req(full_prompt or _prompt(), DraftVaultIR, budget),
        schema=DraftVaultIR, sink=event_sink,
    )
    draft = preserve_post_merge_draft(
        inject_unresolved_triage(draft, profiles),
        prior_draft_ir=prior_draft_ir, frozen_shared_hubs=frozen_shared_hubs,
    )
    tok_src: Literal["reported", "estimated"] = (
        "reported" if res.token_count_source == "reported" else "estimated"
    )
    return (
        draft,
        _collect_props(res, wave_number),
        res.turns_used + failed,
        res.total_tokens,
        tok_src,
    )


__all__ = [
    "preallocate_canonical_hubs", "preserve_post_merge_draft",
    "resolve_cluster_assignments", "run_ship_vault_synthesis", "scale_shard_budget",
]
