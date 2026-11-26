"""Internal helpers for zero-disk stateless modeling workflows."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError

from opendv.agents.pack_loader import load_modeling_guide
from opendv.modeling import DVModelingResult, VerifyDraftResult
from opendv.modeling.models import compute_preferences_sha256
from opendv.modeling.prompts import (
    MAX_BUSINESS_CONTEXT_HINTS_TOKENS,
    MAX_MODELING_GUIDE_TOKENS,
)
from opendv.modeling.token_utils import count_tokens
from opendv.models.diagnostics import VerifierDiagnostic
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.modeling_preferences import ModelingPreferences, RemediationHint
from opendv.models.ontology import GraphAOntology
from opendv.models.policy import CEGAR_POLICY_SPECS, VALID_V6B_RULE_IDS
from opendv.models.project import TrustedWorkspaceSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import (
    ColumnTriageEntry,
    DraftBridgeSpec,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftShardIR,
    DraftVaultIR,
)
from opendv.models.vault_ir_certified import CertifiedVaultIR, SeedRecordsSpec
from opendv.models.wire_dtos_vault import ModelSpecResp, ProfileBundle
from opendv.service.bundles.zip_guard import (
    reject_symlinks_on_path_and_ancestors,
    safe_open_no_follow,
)
from opendv.service.os_platform import validate_workspace_containment
from opendv.sources import validate_source_id_slug
from opendv.verifier.ports import VerifierReport

_SLUG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def load_structured_doc(path_or_raw: Path | str) -> Any:
    """Load JSON or YAML file into Python object."""
    path = Path(path_or_raw)
    if not path.is_file():
        raise OpenDVError(
            f"Input file not found: '{path}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(text)
        if suffix in (".yaml", ".yml"):
            return yaml.safe_load(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise OpenDVError(
            f"Failed to parse input file '{path}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc


def coerce_dict(
    data: Mapping[str, Any] | BaseModel | Path | str,
    *,
    label: str,
) -> dict[str, Any]:
    """Coerce BaseModel, Mapping, or JSON/YAML file path to plain dict."""
    if isinstance(data, BaseModel):
        return data.model_dump()
    if isinstance(data, Mapping):
        return dict(data)
    if isinstance(data, (Path, str)):
        loaded = load_structured_doc(data)
        if isinstance(loaded, dict):
            return loaded
    raise OpenDVError(
        f"{label} must be a JSON/YAML object.",
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
    )


def coerce_profile_bundle(
    bundle: ProfileBundle | Mapping[str, Any] | Path | str | None,
) -> ProfileBundle | None:
    """Validate optional ProfileBundle from DTO, mapping, or file path."""
    if bundle is None:
        return None
    if isinstance(bundle, ProfileBundle):
        return bundle
    raw = coerce_dict(bundle, label="profile_bundle")
    try:
        return ProfileBundle.model_validate(raw)
    except ValidationError as exc:
        raise OpenDVError(
            f"Invalid ProfileBundle: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc


def merge_profile_bundle(raw_req: dict[str, Any], bundle: ProfileBundle | None) -> dict[str, Any]:
    """Merge ProfileBundle profiles, tokenized rows, and sample flags into request dict."""
    if bundle is None:
        return raw_req
    merged = dict(raw_req)
    if not merged.get("profiles"):
        merged["profiles"] = [p.model_dump() for p in bundle.profiles]
    if merged.get("source_rows_by_table") is None and bundle.source_rows_by_table:
        merged["source_rows_by_table"] = {
            k: [dict(r) for r in v] for k, v in bundle.source_rows_by_table.items()
        }
    if merged.get("table_sample_flags") is None and bundle.table_sample_flags:
        merged["table_sample_flags"] = dict(bundle.table_sample_flags)
    return merged


def validate_preferences_governance(prefs: ModelingPreferences, policy: str) -> None:
    """Validate disabled_v6b_blocks against VALID_V6B_RULE_IDS and strict policy lock."""
    bad = [r for r in prefs.disabled_v6b_blocks if r not in VALID_V6B_RULE_IDS]
    if bad:
        raise OpenDVError(
            f"Invalid disabled_v6b_blocks rule ID(s): {bad}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if policy == "strict" and prefs.disabled_v6b_blocks:
        raise OpenDVError(
            "disabled_v6b_blocks must be empty under strict CEGAR policy.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )


def build_model_spec_payload(
    part: Literal["all", "schema", "guide", "preferences"] = "all",
    *,
    workspace_root: Path | str | None = None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
) -> ModelSpecResp:
    """Build ModelSpecResp for schema, guide, preferences, or all."""
    if part not in ("all", "schema", "guide", "preferences"):
        raise OpenDVError(
            f"Invalid model spec part '{part}'; expected all, schema, guide, or preferences.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    schemas: dict[str, Any] = {}
    examples: dict[str, Any] = {}
    constraints: dict[str, Any] = {}
    guide_markdown: str | None = None
    guide_sha256: str | None = None
    preferences: ModelingPreferences | None = None
    preferences_schema: dict[str, Any] = {}

    if part in ("all", "schema"):
        schema_models: tuple[type[BaseModel], ...] = (
            DraftVaultIR,
            DraftHubSpec,
            DraftLinkSpec,
            DraftSatelliteSpec,
            DraftBridgeSpec,
            DraftShardIR,
            ColumnTriageEntry,
            GraphAOntology,
            SourceProfile,
            ProfileBundle,
            ModelingPreferences,
            VerifyDraftResult,
            DVModelingResult,
        )
        schemas = {cls.__name__: cls.model_json_schema() for cls in schema_models}
        examples = {
            "DraftVaultIR": {
                "project_id": "proj-demo",
                "ontology_version": "0.1.0",
                "wave_number": 1,
                "hubs": [
                    {
                        "hub_name": "hub_account",
                        "key_tier": "TIER_1_GLOBAL_STANDARD",
                        "business_keys": [
                            {
                                "table_id": "src-1:accounts",
                                "col_name": "account_code",
                                "source_id": "src-1",
                                "key_tier": "TIER_1_GLOBAL_STANDARD",
                                "bkcc_value": "GLOBAL",
                            }
                        ],
                        "source_bindings": [
                            {
                                "source_id": "src-1",
                                "table_id": "src-1:accounts",
                                "col_mappings": {"account_code": "account_code"},
                            }
                        ],
                        "attributes": [],
                    }
                ],
                "links": [],
                "satellites": [],
                "column_triage": [
                    {
                        "table_id": "src-1:accounts",
                        "col_name": "account_code",
                        "disposition": "MODELED",
                        "target_ref": "hub_account",
                    }
                ],
            }
        }
        constraints = {
            "inv_05": (
                "DraftHubSpec.attributes must be empty; "
                "place descriptive columns in Satellites."
            ),
            "column_triage": (
                "100% of profiled source columns must appear in column_triage "
                "(OMITTED_INTENTIONALLY requires >= 15 non-whitespace chars rationale)."
            ),
            "pii_isolation": "PII columns must be placed in sat_*_pii (sat_kind='PII') or waived.",
            "valid_v6b_rule_ids": sorted(VALID_V6B_RULE_IDS),
            "policies": {
                k: {
                    "k_max": v.k_max,
                    "v7_floor": v.v7_floor,
                    "v8_mode": v.v8_mode,
                    "v7_tier_locked": v.v7_tier_locked,
                }
                for k, v in CEGAR_POLICY_SPECS.items()
            },
            "max_business_context_hints_tokens": MAX_BUSINESS_CONTEXT_HINTS_TOKENS,
            "max_modeling_guide_tokens": MAX_MODELING_GUIDE_TOKENS,
        }

    if part in ("all", "guide"):
        ws_root = Path(workspace_root) if workspace_root is not None else None
        guide_markdown, guide_sha256 = load_modeling_guide(
            workspace_root=ws_root,
            trusted_workspaces=trusted_workspaces,
        )

    if part in ("all", "preferences"):
        preferences = ModelingPreferences()
        preferences_schema = ModelingPreferences.model_json_schema()

    return ModelSpecResp(
        part=part,
        schemas=schemas,
        examples=examples,
        constraints=constraints,
        guide_markdown=guide_markdown,
        guide_sha256=guide_sha256,
        preferences=preferences,
        preferences_schema=preferences_schema,
    )


def parse_source_entries(
    sources: Sequence[str | Mapping[str, Any]],
    sources_file: Path | str | None,
) -> list[dict[str, Any]]:
    """Parse kind:uri strings and --sources-file JSON/YAML specs."""
    raw_entries: list[str | Mapping[str, Any]] = list(sources)
    if sources_file is not None:
        loaded = load_structured_doc(sources_file)
        if isinstance(loaded, dict) and isinstance(loaded.get("sources"), list):
            raw_entries.extend(loaded["sources"])
        elif isinstance(loaded, list):
            raw_entries.extend(loaded)
        else:
            raise OpenDVError(
                "sources_file must contain a JSON/YAML list or {'sources': [...]} mapping.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

    parsed: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw_entries):
        if isinstance(entry, str):
            if ":" not in entry:
                raise OpenDVError(
                    f"Source spec '{entry}' must be formatted as '<kind>:<uri>'.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            kind_raw, uri_raw = entry.split(":", 1)
            parsed.append(
                {
                    "kind": kind_raw.strip(),
                    "uri": uri_raw.strip(),
                    "source_id": None,
                    "options": {},
                    "secret_refs": [],
                    "selected_tables": None,
                    "profile_timeout_seconds": 30,
                }
            )
        elif isinstance(entry, Mapping):
            uri_val = (
                entry.get("uri")
                or entry.get("uri_or_path")
                or entry.get("uri_or_filename")
                or ""
            )
            parsed.append(
                {
                    "kind": str(entry.get("kind") or "").strip(),
                    "uri": str(uri_val).strip(),
                    "source_id": entry.get("source_id"),
                    "options": dict(entry.get("options") or {}),
                    "secret_refs": list(entry.get("secret_refs") or []),
                    "selected_tables": entry.get("selected_tables"),
                    "profile_timeout_seconds": int(entry.get("profile_timeout_seconds") or 30),
                }
            )
        else:
            raise OpenDVError(
                f"Invalid source entry at index {idx}: expected string or mapping.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
    return parsed


def make_stateless_source_id(
    explicit: Any,
    uri: str,
    idx: int,
    used_ids: set[str],
) -> str:
    """Derive a collision-free SAFE_SOURCE_ID_RE slug for stateless profiling."""
    if explicit is not None:
        sid = validate_source_id_slug(str(explicit).strip())
    else:
        stem = _SLUG_SANITIZE_RE.sub("_", Path(uri).stem).strip("._-")[:24]
        base = f"src_{idx + 1}_{stem}" if stem else f"src_{idx + 1}"
        if not base[0].isalnum():
            base = f"s_{base}"
        sid = validate_source_id_slug(base[:64])
    if sid in used_ids:
        sid = validate_source_id_slug(f"{sid[:55]}_{idx + 1}")
    used_ids.add(sid)
    return sid


def extract_inv05_hubs(raw_draft: Mapping[str, Any]) -> list[str]:
    """Return names of raw hub dicts carrying non-empty INV-05 violating attributes."""
    hubs_raw = raw_draft.get("hubs")
    if not isinstance(hubs_raw, list):
        return []
    bad: list[str] = []
    for idx, hub in enumerate(hubs_raw):
        if isinstance(hub, Mapping) and hub.get("attributes"):
            bad.append(str(hub.get("hub_name") or f"hub_{idx}"))
    return bad


def is_inv05_validation_error(exc: ValidationError, raw_draft: Mapping[str, Any]) -> bool:
    """Return True if Pydantic ValidationError was triggered by DraftHubSpec INV-05."""
    for err in exc.errors():
        if "INV-05" in str(err.get("msg", "")):
            return True
    return bool(extract_inv05_hubs(raw_draft))


def build_inv05_failure_result(
    raw_draft: Mapping[str, Any],
    inv05_diags: list[VerifierDiagnostic],
    *,
    prefs: ModelingPreferences,
) -> VerifyDraftResult:
    """Construct structured UNCERTIFIED_DRAFT VerifyDraftResult for INV-05 violations."""
    pid = str(raw_draft.get("project_id") or "proj-stateless")
    ont_ver = str(raw_draft.get("ontology_version") or "0.1.0")
    wave_no = raw_draft.get("wave_number")
    safe_wave = wave_no if isinstance(wave_no, int) and wave_no >= 1 else 1
    empty_draft = DraftVaultIR(project_id=pid, ontology_version=ont_ver, wave_number=safe_wave)
    empty_cert = CertifiedVaultIR(
        project_id=pid,
        ontology_version=ont_ver,
        wave_number=safe_wave,
        seed_records=SeedRecordsSpec(),
    )
    hints = [
        RemediationHint(
            gate="V1",
            rule_id="INV-05",
            entity_name=d.entity_name,
            suggested_fix="Remove descriptive attributes from Hub and place them in a Satellite.",
            waivable=False,
        )
        for d in inv05_diags
    ]
    fail_cnt = len(inv05_diags)
    report = VerifierReport(
        passed=False,
        v1_v6_diagnostics=inv05_diags,
        all_diagnostics=inv05_diags,
        v1_v6_fail_count=fail_cnt,
        energy_e=100 * fail_cnt,
        deterministic_energy=100 * fail_cnt,
        remediation_hints=hints,
    )
    return VerifyDraftResult(
        status="UNCERTIFIED_DRAFT",
        next_action="REPAIR_DRAFT_IR",
        draft_ir=empty_draft,
        certified_ir=empty_cert,
        evolution_lock=None,
        verifier_report=report,
        remediation_hints=hints,
        deterministic_energy=report.deterministic_energy,
        final_energy_e=report.energy_e,
        preferences_sha256=compute_preferences_sha256(prefs),
    )


def read_trusted_guide_file(guide_path: Path | str, ws_root: Path) -> str:
    """Validate containment, no-symlinks, O_NOFOLLOW, and token limit for --guide."""
    cand = Path(guide_path)
    if not cand.is_absolute():
        cand = ws_root / cand
    contained = validate_workspace_containment(cand, allowed_roots=(ws_root,))
    reject_symlinks_on_path_and_ancestors(contained, stop_roots=(ws_root,))
    if not contained.is_file():
        raise OpenDVError(
            f"Modeling guide file not found: '{guide_path}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    fd = safe_open_no_follow(contained, os.O_RDONLY)
    with os.fdopen(fd, "rb") as fh:
        text = fh.read().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if count_tokens(text) > MAX_MODELING_GUIDE_TOKENS:
        raise OpenDVError(
            f"Modeling guide exceeds {MAX_MODELING_GUIDE_TOKENS} cl100k_base tokens.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return text


__all__ = [
    "build_inv05_failure_result",
    "build_model_spec_payload",
    "coerce_dict",
    "coerce_profile_bundle",
    "extract_inv05_hubs",
    "is_inv05_validation_error",
    "load_structured_doc",
    "make_stateless_source_id",
    "merge_profile_bundle",
    "parse_source_entries",
    "read_trusted_guide_file",
    "validate_preferences_governance",
]
