"""STAGE_01: Tabular Source Discovery."""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from opendv_agent_harness.pipeline.ontology_evolution import (
    emit_stage01_advisory_proposal,
    emit_stage01_ontology_coverage,
    emit_stage05_ontology_proposal_and_options,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineSourceFeedInput,
    PipelineStepReport,
    Stage01FeedDiscoveryItem,
    Stage01SourceDiscoveryOutput,
)
from opendv_agent_harness.pipeline.stages_agentic import (
    _step_tier,
    execute_stage_task,
    resolve_stage_model,
)
from opendv_agent_harness.pipeline.tabular_helpers import (
    _calc_col_stats,
    _collect_roots,
    _enforce_server_ssrf,
    _extract_pushdown_stats,
    _load_table_into_duckdb,
    _make_col_descs,
    _make_fk_hints,
    _record_item,
    _resolve_cas_spec,
    _safe_load_view,
    _to_source_target,
    quote_ident,
)
from opendv_sources.models.descriptors import ResolvedTableSource
from opendv_sources.models.specs import SourceSpec
from opendv_sources.registry.source_registry import get_source_registry
from opendv_sources.spi.catalog import CatalogSourceAdapter


def discover_tabular_feeds(
    sources: Sequence[PipelineSourceFeedInput],
    conn: duckdb.DuckDBPyConnection,
    *,
    workspace_root: Path | None = None,
    base_dir: Path | None = None,
    cas_store: Any | None = None,
    is_server: bool = False,
    allowed_hosts: Sequence[str] | set[str] | None = None,
) -> Stage01SourceDiscoveryOutput:
    ws = Path(workspace_root or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()
    eff_base = (base_dir or ws).resolve()
    reg = get_source_registry()
    eff_is_server = is_server or (os.environ.get("OPENDV_IS_SERVER") in ("1", "true", "True"))
    eff_allowed_hosts = allowed_hosts
    if eff_allowed_hosts is None:
        env_hosts = os.environ.get("OPENDV_ALLOWED_HOSTS") or os.environ.get(
            "OPENDV_ALLOWED_DATABRICKS_HOSTS"
        )
        if env_hosts:
            eff_allowed_hosts = [h.strip().lower() for h in env_hosts.split(",") if h.strip()]

    items: list[Stage01FeedDiscoveryItem] = []
    profile_summaries: dict[str, Any] = {}

    for src in sources:
        options = dict(getattr(src, "options", {}) or {})
        src_format = (
            getattr(src, "format", None)
            or options.get("source_format")
            or options.get("format")
        )
        if src_format:
            src_format = str(src_format).lower().strip().lstrip(".")
        if src.source is not None:
            spec = src.source
            upd = {
                k: v
                for k, v in {
                    "table_selection": getattr(src, "table_selection", None)
                    if spec.table_selection is None
                    else None,
                    "namespace": (
                        getattr(src, "namespace", None) if spec.namespace is None else None
                    ),
                    "format": src_format if not spec.format else None,
                }.items()
                if v is not None
            }
            if upd:
                spec = spec.model_copy(update=upd)
        else:
            spec = SourceSpec(
                uri=src.uri or src.data_path or src.file_path or "",
                adapter=src.adapter,
                format=src_format,
                table_selection=getattr(src, "table_selection", None),
                namespace=getattr(src, "namespace", None),
                options=options,
                credentials_ref=getattr(src, "credentials_ref", None),
            )
        u = src.uri or src.data_path or src.file_path or (spec.uri if spec else "")
        if eff_is_server:
            req_h = options.get("host") or (spec.options.get("host") if spec.options else None)
            if req_h:
                _enforce_server_ssrf(str(req_h), eff_allowed_hosts)
            if u and str(u).startswith(("http://", "https://", "databricks://", "uc://")):
                _enforce_server_ssrf(str(u), eff_allowed_hosts)

        if u and u.startswith("cas://"):
            spec = _resolve_cas_spec(u, spec, src_format, cas_store, eff_base)
        elif u and not u.startswith(
            ("http://", "https://", "sqlite://", "databricks://", "uc://", "duckdb://")
        ):
            p_cand = (eff_base / u).resolve()
            if p_cand.exists():
                spec = spec.model_copy(update={"uri": str(p_cand)})

        if u and spec.adapter in (None, "", "file"):
            with contextlib.suppress(Exception):
                from opendv_sources.registry.scheme_registry import get_scheme_registry

                inferred_ad = get_scheme_registry().resolve_adapter(u)
                if inferred_ad and inferred_ad != spec.adapter:
                    spec = spec.model_copy(update={"adapter": inferred_ad})

        cat_adapter: CatalogSourceAdapter | None = None
        s1_allowed = _collect_roots(
            eff_base,
            ws,
            getattr(cas_store, "base_dir", None),
            eff_base / "state/artifacts",
            ws / "state/artifacts",
            Path("state/artifacts"),
        )
        try:
            adapter_obj = reg.get_tabular_adapter(
                spec.adapter,
                workspace_root=ws,
                allowed_roots=s1_allowed,
                allow_temp=True,
                is_server=eff_is_server,
                allowed_hosts=eff_allowed_hosts,
            )
            if isinstance(adapter_obj, CatalogSourceAdapter):
                cat_adapter = adapter_obj
        except (KeyError, ValueError, OSError, RuntimeError):
            cat_adapter = None

        resolved_tables: list[ResolvedTableSource] = []
        if cat_adapter is not None:
            try:
                resolved_tables = cat_adapter.expand_feed(spec)
            except (ValueError, OSError, RuntimeError):
                resolved_tables = []

        if resolved_tables:
            for rt in resolved_tables:
                clean_ss = re.sub(r"[^a-zA-Z0-9_]", "_", rt.source_system or "source").strip("_")
                clean_tn = re.sub(r"[^a-zA-Z0-9_]", "_", rt.table_name or "table").strip("_")
                vname, row_cnt, cols = _safe_load_view(
                    conn,
                    clean_ss,
                    clean_tn,
                    rt.spec,
                    ws,
                    eff_base,
                    eff_is_server,
                    eff_allowed_hosts,
                    [c.name for c in rt.schema_descriptor.columns],
                )
                if not cols:
                    row_cnt = rt.schema_descriptor.row_count_estimate or 0
                    cols = [c.name for c in rt.schema_descriptor.columns]
                col_stats = _extract_pushdown_stats(
                    cat_adapter, rt.spec, rt.table_name, rt.schema_descriptor.namespace, row_cnt
                ) or _calc_col_stats(conn, vname, cols, row_cnt)
                _record_item(
                    items,
                    profile_summaries,
                    ss=rt.source_system,
                    tname=rt.table_name,
                    cols=cols,
                    row_cnt=row_cnt,
                    pks=rt.schema_descriptor.primary_key_columns,
                    fks=_make_fk_hints(rt.schema_descriptor.foreign_keys),
                    col_descs=_make_col_descs(rt.schema_descriptor.columns),
                    col_stats=col_stats,
                    gov_tags=rt.schema_descriptor.governance_tags,
                    extra_keys=(rt.qualified_name,),
                )
        else:
            ss = src.source_system or "source"
            tname = src.table_name or "table"
            clean_ss = re.sub(r"[^a-zA-Z0-9_]", "_", ss).strip("_")
            clean_tn = re.sub(r"[^a-zA-Z0-9_]", "_", tname).strip("_")
            eff_uri = src.data_path or (
                spec.uri if spec and spec.uri else (src.uri if src.uri else None)
            )
            is_scheme_uri = bool(
                eff_uri
                and (
                    "://" in eff_uri
                    or eff_uri.startswith(("sqlite:", "duckdb:", "uc:", "databricks:", "cas:"))
                )
            )
            target_p = _to_source_target(
                spec, None if (not eff_uri or is_scheme_uri) else Path(eff_uri)
            )
            bks = list(
                getattr(src, "business_key_columns", []) or getattr(src, "business_keys", [])
            )
            vname, row_cnt, cols = _safe_load_view(
                conn,
                clean_ss,
                clean_tn,
                target_p,
                ws,
                eff_base,
                eff_is_server,
                eff_allowed_hosts,
                bks,
            )
            try:
                col_descs = _make_col_descs(
                    conn.execute(f"DESCRIBE {quote_ident(vname)}").fetchall(), bks
                )
            except (duckdb.Error, ValueError, IndexError, TypeError):
                col_descs = _make_col_descs(cols, bks)
            fks = _make_fk_hints(src.foreign_keys)
            if getattr(src, "foreign_key_mappings", None):
                for col, tgt in src.foreign_key_mappings.items():
                    if not any(f.get("fk_column") == col for f in fks):
                        fks.append({
                            "fk_column": col,
                            "target_table": tgt.removeprefix("hub_"),
                            "target_column": col,
                        })
            _record_item(
                items,
                profile_summaries,
                ss=ss,
                tname=tname,
                cols=cols,
                row_cnt=row_cnt,
                pks=bks,
                fks=fks,
                col_descs=col_descs,
                col_stats=_calc_col_stats(conn, vname, cols, row_cnt),
            )

    return Stage01SourceDiscoveryOutput(
        discovered_feeds=items, profile_summaries=profile_summaries
    )


async def execute_stage_01_tabular(
    runner: Any,
    w_idx: int,
    wave_sources: list[Any],
    conn: duckdb.DuckDBPyConnection,
    wave_steps: list[PipelineStepReport],
    step_reports: list[PipelineStepReport],
    artifacts: list[Any],
    rec_errors: list[str],
) -> tuple[Stage01SourceDiscoveryOutput, str | None]:
    s01_id = "STAGE_01_SCOUT_SOURCE"
    s01_persona = "scout-source"
    s01_model = resolve_stage_model(s01_id, s01_persona, runner.harness_setup, runner.harness)
    runner._notify_stage_start(w_idx, s01_id, s01_persona, s01_model)
    runner._emit_stream(f"Starting {s01_id}...")

    s1_out = discover_tabular_feeds(
        wave_sources,
        conn,
        workspace_root=runner.workspace_root,
        base_dir=runner.base_dir,
        cas_store=getattr(runner, "cas_store", None),
        is_server=getattr(runner, "is_server", False),
        allowed_hosts=getattr(runner, "allowed_hosts", None),
    )
    s01_ctx = {
        "sources": [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in wave_sources
        ],
        "discovered_feeds": [
            f.model_dump(mode="json") if hasattr(f, "model_dump") else f
            for f in s1_out.discovered_feeds
        ],
        "profile_summaries": s1_out.profile_summaries,
    }
    s01_metrics, _ = await execute_stage_task(
        w_idx,
        s01_id,
        s01_persona,
        runner.harness,
        runner.on_stream_event,
        runner.run_id,
        context=s01_ctx,
    )
    cov_art, unmapped_tables = emit_stage01_ontology_coverage(
        runner.ontology,
        wave_sources,
        s1_out.discovered_feeds,
        runner.output_dir,
        runner.cas_store,
        wave_index=w_idx,
    )
    if cov_art:
        artifacts.append(cov_art)

    g_sha = runner.graph_a_sha256 or ""
    prop_art, r_errs = await emit_stage01_advisory_proposal(
        runner.input_spec.ontology_policy,
        runner.ontology,
        g_sha,
        runner.output_dir,
        runner.cas_store,
        wave_index=w_idx,
    )
    if prop_art:
        artifacts.append(prop_art)
    if r_errs:
        rec_errors.extend(r_errs)

    step_01 = PipelineStepReport(
        stage_index=1,
        stage_id=s01_id,
        stage_name=s01_id,
        persona=s01_persona,
        model_tier=_step_tier(s01_persona),
        model_used=s01_model,
        status="PASS",
        verdict="PASS",
        duration_ms=15.0,
        summary=f"Discovered {len(s1_out.discovered_feeds)} tabular feeds.",
        metrics=s01_metrics,
        wave_index=w_idx,
    )
    wave_steps.append(step_01)
    step_reports.append(step_01)
    runner._notify_stage_complete(w_idx, step_01)

    if unmapped_tables:
        cov_id = cov_art.artifact_id if cov_art else "art-w0-stage01-ontology-coverage"
        diag_msg = f"DV-G4-001: Unmapped table '{unmapped_tables[0]}'"
        s05_prop, hold_opts = await emit_stage05_ontology_proposal_and_options(
            runner.input_spec.ontology_policy,
            runner.ontology,
            g_sha,
            runner.output_dir,
            runner.cas_store,
            cov_art_id=cov_id,
            diag_msg=diag_msg,
        )
        if s05_prop:
            artifacts.append(s05_prop)
        if (
            runner.harness_setup.steward_hold_policy == "auto_approve_top_candidate"
            and runner.input_spec.ontology_policy != "steward_review"
            and not any("unmapped" in str(t).lower() for t in unmapped_tables)
        ):
            return s1_out, None
        runner.hold_manager.record_hold(
            run_id=runner.run_id,
            task_id=f"task-w{w_idx}-g1-ontology-hold",
            session_id=f"sess-w{w_idx}-g1",
            stage_id="STAGE_05_AXI_DV_STATIC_GATES",
            persona="axi-dv",
            headline=f"Gate 1 ontology gap hold: {diag_msg}",
            escalation_reason="UNMAPPED_ONTOLOGY_CONCEPT",
            rule_id="DV-G4-001",
            options=hold_opts,
        )
        return s1_out, "UNMAPPED_ONTOLOGY_CONCEPT"

    return s1_out, None


__all__ = [
    "_load_table_into_duckdb",
    "_to_source_target",
    "discover_tabular_feeds",
    "execute_stage_01_tabular",
]
