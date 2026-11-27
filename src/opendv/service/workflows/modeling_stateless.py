"""Zero-disk stateless modeling service helpers (spec, profile, verify, run)."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from opendv.agents import AgentHarnessPort
from opendv.agents.pack_loader import verify_workspace_override_trust
from opendv.modeling import (
    DVModelingRequest,
    DVModelingResult,
    ModelingHarnessPort,
    VerifyDraftRequest,
    VerifyDraftResult,
    enforce_v7_tier_lock,
    model_vault,
    verify_draft_vault,
)
from opendv.modeling.models import compute_preferences_sha256
from opendv.modeling.prompts import (
    resolve_modeling_guide,
    validate_business_context_hints,
)
from opendv.modeling.verify_mode import (
    _build_remediation_hints,
    _inject_extra_v1_diags,
    validate_source_rows_pii_tokenized,
)
from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences, PkContainmentOverlap
from opendv.models.ontology import GraphAOntology
from opendv.models.project import TrustedWorkspaceSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import DraftVaultIR
from opendv.models.wire_dtos_vault import ModelSpecResp, ProfileBundle
from opendv.service.os_platform import resolve_user_state_dir, validate_workspace_containment
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.workflows._modeling_stateless_helpers import (
    build_inv05_failure_result,
    build_model_spec_payload,
    coerce_dict,
    coerce_profile_bundle,
    extract_inv05_hubs,
    is_inv05_validation_error,
    make_stateless_source_id,
    merge_profile_bundle,
    parse_source_entries,
    read_trusted_guide_file,
    validate_preferences_governance,
)
from opendv.service.workflows._run_adapters import (
    ServiceModelingPackLoader,
    resolve_harness,
    wrap_modeling_harness,
)
from opendv.service.workflows._source_docs import DOC_SOURCE_KINDS, as_source_kind
from opendv.sources import (
    compute_pk_containment_overlaps,
    get_source_adapter,
    profile_source_with_samples,
    validate_databricks_host,
    validate_databricks_options,
)
from opendv.storage.prover.duckdb_prover import DuckDbProverAdapter
from opendv.verifier import survey_modeling_hints


def get_model_spec(
    part: Literal["all", "schema", "guide", "preferences"] = "all",
    *,
    workspace_root: Path | str | None = None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
) -> ModelSpecResp:
    """Return annotated modeling schemas, examples, guide markdown, and preferences."""
    return build_model_spec_payload(
        part,
        workspace_root=workspace_root,
        trusted_workspaces=trusted_workspaces,
    )


def profile_sources_stateless(
    sources: Sequence[str | Mapping[str, Any]] = (),
    *,
    sources_file: Path | str | None = None,
    ontology: GraphAOntology | Mapping[str, Any] | Path | str | None = None,
    proving_sample_rows: int = 200,
    real_row_proving: bool = True,
    max_questions: int | None = 5,
    offset: int = 0,
    answered_question_ids: Sequence[str] = (),
    fk_turnover_by_table: Mapping[str, Mapping[str, float]] | None = None,
    workspace_root: Path | str | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
) -> ProfileBundle:
    """Profile structured sources statelessly with shared HMAC salt, overlaps, and survey."""
    entries = parse_source_entries(sources, sources_file)
    ont_obj: GraphAOntology | None = None
    if isinstance(ontology, GraphAOntology):
        ont_obj = ontology
    elif ontology is not None:
        raw_ont = coerce_dict(ontology, label="ontology")
        try:
            ont_obj = GraphAOntology.model_validate(raw_ont)
        except ValidationError as exc:
            raise OpenDVError(
                f"Invalid GraphAOntology: {exc}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from exc

    shared_salt = secrets.token_hex(32)
    used_ids: set[str] = set()
    profiles: list[SourceProfile] = []
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    sample_flags: dict[str, bool] = {}

    for idx, item in enumerate(entries):
        kind = as_source_kind(item["kind"])
        if kind in DOC_SOURCE_KINDS:
            raise OpenDVError(
                f"Stateless structured profiler does not support document kind '{kind}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        get_source_adapter(kind)
        uri_str = item["uri"]
        if not uri_str:
            raise OpenDVError(
                f"Empty URI/path for source at index {idx}.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        if kind == "databricks_uc":
            validate_databricks_options(
                item["options"], item["secret_refs"], require_secret_ref=True
            )
            validate_databricks_host(uri_str, resolve_dns=True)
            resolved_uri = uri_str
        else:
            cand = Path(uri_str)
            if not cand.is_absolute() and workspace_root is not None:
                cand = Path(workspace_root) / cand
            if allowed_roots is not None:
                cand = validate_workspace_containment(cand, allowed_roots=allowed_roots)
            else:
                cand = cand.resolve()
            if not cand.is_file():
                raise OpenDVError(
                    f"Source file '{uri_str}' does not exist.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )
            resolved_uri = str(cand)

        sid = make_stateless_source_id(item["source_id"], resolved_uri, idx, used_ids)
        prof, s_rows, s_flags = profile_source_with_samples(
            sid,
            kind,
            resolved_uri,
            options=item["options"],
            secret_refs=item["secret_refs"],
            selected_tables=item["selected_tables"],
            profile_timeout_seconds=item["profile_timeout_seconds"],
            proving_sample_rows=proving_sample_rows,
            real_row_proving=real_row_proving,
            pii_salt=shared_salt,
        )
        profiles.append(prof)
        rows_by_table.update(s_rows)
        sample_flags.update(s_flags)

    overlaps: list[PkContainmentOverlap] = []
    if profiles and rows_by_table:
        overlaps = compute_pk_containment_overlaps(
            profiles,
            rows_by_table,
            table_sample_flags=sample_flags,
        )
        if overlaps:
            by_left: dict[str, list[PkContainmentOverlap]] = {}
            for ov in overlaps:
                by_left.setdefault(ov.left_source_id, []).append(ov)
            profiles = [
                p.model_copy(
                    update={
                        "pk_containment_overlaps": list(
                            {
                                (
                                    o.left_table_id,
                                    o.left_col,
                                    o.right_table_id,
                                    o.right_col,
                                ): o
                                for o in (*p.pk_containment_overlaps, *by_left.get(p.source_id, ()))
                            }.values()
                        )
                    }
                )
                for p in profiles
            ]

    questions, counts = survey_modeling_hints(
        profiles,
        ontology=ont_obj,
        source_rows_by_table=rows_by_table,
        answered_question_ids=answered_question_ids,
        fk_turnover_by_table=fk_turnover_by_table,
        max_questions=max_questions,
        offset=offset,
    )
    return ProfileBundle(
        profiles=profiles,
        pk_containment_overlaps=overlaps,
        source_rows_by_table=rows_by_table,
        table_sample_flags=sample_flags,
        questions=questions,
        total_candidates=counts.get("total_candidates", len(questions)),
        returned_count=counts.get("returned_count", len(questions)),
        offset=counts.get("offset", max(0, offset)),
        remaining_count=counts.get("remaining_count", 0),
        by_priority={k: counts[k] for k in ("P1", "P2", "P3", "P4") if k in counts},
    )


def verify_draft_stateless(
    request_data: VerifyDraftRequest | Mapping[str, Any] | Path | str,
    draft_data: DraftVaultIR | Mapping[str, Any] | Path | str | None = None,
    *,
    profile_bundle: ProfileBundle | Mapping[str, Any] | Path | str | None = None,
    dry_run: bool = False,
) -> VerifyDraftResult | dict[str, Any]:
    """Run zero-LLM Mode-1 draft verification with structured INV-05 handling."""
    bundle = coerce_profile_bundle(profile_bundle)
    if isinstance(request_data, VerifyDraftRequest):
        req_dict = request_data.model_dump()
    else:
        req_dict = coerce_dict(request_data, label="request_data")

    req_dict = merge_profile_bundle(req_dict, bundle)
    raw_draft_obj: Any = draft_data if draft_data is not None else req_dict.get("draft_ir")
    if raw_draft_obj is None:
        raise OpenDVError(
            "draft_ir is required for stateless verification.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    inv05_diags: list[VerifierDiagnostic] = []
    cleaned_draft: DraftVaultIR | None = None
    raw_draft_dict: dict[str, Any] = {}
    if isinstance(raw_draft_obj, DraftVaultIR):
        cleaned_draft = raw_draft_obj
    else:
        raw_draft_dict = coerce_dict(raw_draft_obj, label="draft_data")
        try:
            cleaned_draft = DraftVaultIR.model_validate(raw_draft_dict)
        except ValidationError as exc:
            if not is_inv05_validation_error(exc, raw_draft_dict):
                raise OpenDVError(
                    f"Invalid DraftVaultIR: {exc}",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from exc
            offending = extract_inv05_hubs(raw_draft_dict)
            inv05_diags = [
                VerifierDiagnostic(
                    gate="V1",
                    rule_id="INV-05",
                    severity="BLOCKER",
                    entity_name=hname,
                    waivable=False,
                    message=f"Hub {hname} violates INV-05: Hub attributes must be empty.",
                )
                for hname in offending
            ]
            stripped_dict = dict(raw_draft_dict)
            stripped_dict["hubs"] = [
                {**dict(h), "attributes": []} if isinstance(h, Mapping) else h
                for h in raw_draft_dict.get("hubs", ())
            ]
            try:
                cleaned_draft = DraftVaultIR.model_validate(stripped_dict)
            except ValidationError:
                raw_prefs = req_dict.get("modeling_preferences")
                prefs = (
                    ModelingPreferences.model_validate(raw_prefs)
                    if isinstance(raw_prefs, Mapping)
                    else ModelingPreferences()
                )
                return build_inv05_failure_result(raw_draft_dict, inv05_diags, prefs=prefs)

    req_dict["draft_ir"] = cleaned_draft
    try:
        req = VerifyDraftRequest.model_validate(req_dict)
    except ValidationError as exc:
        raise OpenDVError(
            f"Invalid VerifyDraftRequest: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc

    if req.business_context_hints and req.business_context_hints.strip():
        raise OpenDVError(
            "business_context_hints is forbidden in Mode-1 verify.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if req.guide_markdown and req.guide_markdown.strip():
        raise OpenDVError(
            "guide_markdown is forbidden in Mode-1 verify.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    validate_preferences_governance(req.modeling_preferences, req.policy)
    validate_source_rows_pii_tokenized(req.profiles, req.source_rows_by_table)

    if dry_run:
        if inv05_diags:
            return build_inv05_failure_result(
                raw_draft_dict or cleaned_draft.model_dump(),
                inv05_diags,
                prefs=req.modeling_preferences,
            )
        return {
            "dry_run": True,
            "valid": True,
            "mode": "verify",
            "policy": req.policy,
            "preferences_sha256": compute_preferences_sha256(req.modeling_preferences),
            "table_count": sum(len(p.tables) for p in req.profiles),
            "real_row_proving": bool(
                req.real_row_proving if req.real_row_proving is not None else True
            ),
        }

    prover = req.prover if req.policy == "symbolic_only" else (req.prover or DuckDbProverAdapter())
    result = verify_draft_vault(req, prover=prover)
    if not inv05_diags:
        return result

    upd_report = _inject_extra_v1_diags(result.verifier_report, inv05_diags, request=req)
    hints = _build_remediation_hints(upd_report)
    upd_report = upd_report.model_copy(update={"remediation_hints": hints})
    return result.model_copy(
        update={
            "status": "UNCERTIFIED_DRAFT",
            "next_action": "REPAIR_DRAFT_IR",
            "evolution_lock": None,
            "verifier_report": upd_report,
            "remediation_hints": hints,
            "deterministic_energy": upd_report.deterministic_energy,
            "final_energy_e": upd_report.energy_e,
        }
    )


async def run_model_stateless(
    request_data: DVModelingRequest | Mapping[str, Any] | Path | str,
    *,
    profile_bundle: ProfileBundle | Mapping[str, Any] | Path | str | None = None,
    guide_path: Path | str | None = None,
    dry_run: bool = False,
    workspace_root: Path | str | None = None,
    state_dir: Path | str | None = None,
    injected_harness: AgentHarnessPort | ModelingHarnessPort | None = None,
) -> DVModelingResult | dict[str, Any]:
    """Execute Mode-2 managed CEGAR modeling statelessly without creating .opendv/."""
    eff_state_dir = resolve_user_state_dir(state_dir)
    ws_settings = SettingsStore(user_state_dir=eff_state_dir).load_settings()
    ws_root = (
        Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
    )
    verify_workspace_override_trust(ws_root, ws_settings.trusted_workspaces)

    guide_override: str | None = None
    if guide_path is not None:
        guide_override = read_trusted_guide_file(guide_path, ws_root)

    bundle = coerce_profile_bundle(profile_bundle)
    if isinstance(request_data, DVModelingRequest):
        req = request_data
        if bundle is not None:
            upd: dict[str, Any] = {}
            if not req.profiles:
                upd["profiles"] = list(bundle.profiles)
            if req.source_rows_by_table is None and bundle.source_rows_by_table:
                upd["source_rows_by_table"] = bundle.source_rows_by_table
            if req.table_sample_flags is None and bundle.table_sample_flags:
                upd["table_sample_flags"] = bundle.table_sample_flags
            if upd:
                req = req.model_copy(update=upd)
    else:
        raw_req = merge_profile_bundle(coerce_dict(request_data, label="request_data"), bundle)
        try:
            req = DVModelingRequest.model_validate(raw_req)
        except ValidationError as exc:
            raise OpenDVError(
                f"Invalid DVModelingRequest: {exc}",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            ) from exc

    enforce_v7_tier_lock(req.policy, req.v7_critic_tier)
    validate_business_context_hints(req.business_context_hints)
    validate_preferences_governance(req.modeling_preferences, req.policy)
    validate_source_rows_pii_tokenized(req.profiles, req.source_rows_by_table)

    pack_loader = req.pack_loader or ServiceModelingPackLoader(
        workspace_root=ws_root,
        trusted_workspaces=ws_settings.trusted_workspaces,
    )
    guide_text, guide_sha = resolve_modeling_guide(
        guide_override if guide_override is not None else req.guide_markdown,
        pack_loader,
    )

    if dry_run:
        return {
            "dry_run": True,
            "valid": True,
            "mode": "run",
            "policy": req.policy,
            "v7_critic_tier": req.v7_critic_tier,
            "preferences_sha256": compute_preferences_sha256(req.modeling_preferences),
            "guide_sha256": guide_sha,
            "table_count": sum(len(p.tables) for p in req.profiles),
            "real_row_proving": bool(
                req.real_row_proving if req.real_row_proving is not None else True
            ),
        }

    eff_overrides = req.model_overrides or (ws_settings.model_overrides or None)
    if injected_harness is not None:
        adapted_harness = wrap_modeling_harness(injected_harness)
    elif req.harness is not None:
        adapted_harness = wrap_modeling_harness(req.harness)
    else:
        secret_store = KeyringSecretStore(ws_root, state_dir=eff_state_dir)
        raw_harness = resolve_harness(
            ws_settings,
            secret_store,
            model_overrides=eff_overrides,
        )
        adapted_harness = wrap_modeling_harness(raw_harness)

    prover = req.prover if req.policy == "symbolic_only" else (req.prover or DuckDbProverAdapter())
    eff_req = req.model_copy(
        update={
            "harness": adapted_harness,
            "pack_loader": pack_loader,
            "prover": prover,
            "guide_markdown": guide_text,
            "model_overrides": eff_overrides,
        }
    )
    return await model_vault(eff_req, harness=adapted_harness, prover=prover)


__all__ = [
    "get_model_spec",
    "profile_sources_stateless",
    "run_model_stateless",
    "verify_draft_stateless",
]
