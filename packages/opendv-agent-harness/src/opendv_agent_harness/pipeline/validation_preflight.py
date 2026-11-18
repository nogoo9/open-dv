"""Pre-flight deterministic validation for pipeline inputs and harness configuration.

Conforms to ADR-017, ADR-018, ADR-020, REQ-CFG-01, and
docs/25-data-driven-single-run-pipeline-design.md.
Enforces fail-closed path containment, zero literal secrets in credentials_ref,
and cryptographic VaultBlueprint SHA-256 lineage verification.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pydantic
import yaml

from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_agent_harness.pipeline.sandbox_compiler import (
    _check_credentials_ref,
    _validate_path_containment,
)
from opendv_agent_harness.pipeline.spec import (
    DeterministicValidationReport,
    PipelineInputSpec,
    PipelineSourceFeedInput,
    PipelineUnstructuredDocInput,
)
from opendv_agent_harness.registry import list_available_harnesses
from opendv_contracts.errors import OpenDVError
from opendv_core.models.vault_blueprint import (
    VaultBlueprint,
    compute_contract_sha256,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_sources.models.specs import SourceSpec
from opendv_sources.registry.source_registry import get_source_registry

_PARSE_EXC = (pydantic.ValidationError, KeyError, ValueError, TypeError, yaml.YAMLError, OSError)


def _first_non_empty(*items: Any) -> str:
    for item in items:
        if item and str(item).strip():
            return str(item).strip()
    return ""


def _parse_yaml_or_json_content(content: str) -> dict[str, Any]:
    """Parse inline YAML or JSON content without touching the filesystem."""
    if not content or not isinstance(content, str):
        return {}
    data = yaml.safe_load(content)
    return data if isinstance(data, dict) else {}


def _load_yaml_or_json(content_or_path: str | Path) -> dict[str, Any]:
    if isinstance(content_or_path, Path):
        text = content_or_path.read_text(encoding="utf-8")
    elif isinstance(content_or_path, str):
        text = content_or_path
        is_inline = "\n" in content_or_path or content_or_path.strip().startswith(("{", "[", "---"))
        if not is_inline:
            try:
                p = Path(content_or_path)
                if p.is_file():
                    text = p.read_text(encoding="utf-8")
            except (OSError, ValueError):
                text = content_or_path
    else:
        text = str(content_or_path)
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}



def validate_pipeline_inputs(
    input_spec: PipelineInputSpec | dict[str, Any] | str | Path,
    harness_setup: HarnessSetupConfig | dict[str, Any] | str | Path | None = None,
    *,
    base_dir: Path | None = None,
    workspace_root: Path | None = None,
) -> DeterministicValidationReport:
    """Deterministically validate PipelineInputSpec, HarnessSetupConfig, ontology, and data."""
    diagnostics: list[dict[str, Any]] = []
    checked_items = 0

    ws_root = (
        workspace_root or Path(os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd())
    ).resolve()
    eff_base = (base_dir or ws_root).resolve()

    def _diag(code: str, target: str, msg: str, severity: str = "ERROR") -> None:
        diagnostics.append({"code": code, "severity": severity, "target": target, "message": msg})

    # 1. Spec parsing
    checked_items += 1
    spec: PipelineInputSpec
    if isinstance(input_spec, PipelineInputSpec):
        spec = input_spec
    else:
        try:
            data = input_spec if isinstance(input_spec, dict) else _load_yaml_or_json(input_spec)
            spec = PipelineInputSpec.model_validate(data)
        except _PARSE_EXC as exc:
            _diag("INPUT_SPEC_SCHEMA_INVALID", "input_spec", str(exc))
            return DeterministicValidationReport(
                stage="INPUT_VALIDATION",
                passed=False,
                checked_items_count=checked_items,
                diagnostics=diagnostics,
            )

    # 2. Harness setup
    checked_items += 1
    setup: HarnessSetupConfig
    if harness_setup is None:
        setup = HarnessSetupConfig()
    elif isinstance(harness_setup, HarnessSetupConfig):
        setup = harness_setup
    else:
        try:
            raw_setup = (
                harness_setup
                if isinstance(harness_setup, dict)
                else _load_yaml_or_json(harness_setup)
            )
            setup = HarnessSetupConfig.model_validate(raw_setup)
        except _PARSE_EXC as exc:
            _diag("HARNESS_SETUP_INVALID", "harness_setup", str(exc))
            setup = HarnessSetupConfig()

    avail_harnesses = list_available_harnesses()
    if setup.provider not in avail_harnesses and setup.provider not in {
        "antigravity",
        "claude",
        "agy",
    }:
        _diag(
            "UNKNOWN_HARNESS_PROVIDER",
            "harness_setup.provider",
            f"Unknown provider '{setup.provider}'.",
        )

    eff_auth_mode = (
        setup.auth.mode
        if (setup.auth.mode != "none" and setup.auth.auth_mode == "none")
        else setup.auth.auth_mode
    )
    eff_require_preflight = bool(
        setup.auth.require_credentials_present or setup.auth.require_preflight
    )
    is_claude_family = setup.provider in {"claude", "claude-cli", "claude-code"}

    valid_claude_cli_modes = {
        "cli_session",
        "api_key",
        "oauth_token",
        "vertex",
        "foundry",
        "auto",
        "none",
    }
    if setup.provider in {"claude-cli", "claude-code"}:
        if eff_auth_mode not in valid_claude_cli_modes:
            _diag(
                "HARNESS_AUTH_INVALID",
                "harness_setup.auth.mode",
                f"Invalid auth mode '{eff_auth_mode}' for provider '{setup.provider}'.",
            )
        if eff_require_preflight:
            import shutil

            claude_bin = (
                (setup.extra_provider_options or {}).get("claude_binary")
                or os.environ.get("OPENDV_CLAUDE_CLI_BIN")
                or shutil.which("claude")
            )
            if not claude_bin or (
                not Path(str(claude_bin)).is_file() and not shutil.which(str(claude_bin))
            ):
                _diag(
                    "HARNESS_PROVIDER_UNAVAILABLE",
                    "harness_setup.provider",
                    (
                        f"Provider '{setup.provider}' requires the 'claude' CLI executable on "
                        "PATH or in OPENDV_CLAUDE_CLI_BIN when require_preflight=True."
                    ),
                )

    if eff_auth_mode in {"cli_session", "ambient_cli"} and setup.provider in {
        "antigravity",
        "claude",
    }:
        import importlib.util

        sdk_pkg = "google.antigravity" if setup.provider == "antigravity" else "claude_agent_sdk"
        try:
            sdk_spec = importlib.util.find_spec(sdk_pkg)
        except (ImportError, ModuleNotFoundError, ValueError):
            sdk_spec = None
        if sdk_spec is None:
            _diag(
                "HARNESS_AUTH_MISSING",
                "harness_setup.auth.auth_mode",
                (
                    f"Auth mode '{eff_auth_mode}' for provider '{setup.provider}' "
                    f"requires SDK package '{sdk_pkg}'."
                ),
            )

    if eff_auth_mode == "aws_bedrock" and not is_claude_family:
        _diag(
            "HARNESS_AUTH_INCOMPATIBLE",
            "harness_setup.auth.auth_mode",
            (
                f"Auth mode 'aws_bedrock' is only supported for provider 'claude', "
                f"got provider '{setup.provider}'."
            ),
        )
    if eff_require_preflight:
        eff_env = {**os.environ, **setup.env}
        if eff_auth_mode in {"api_key_env", "api_key"} and setup.provider not in {
            "claude-cli",
            "claude-code",
        }:
            target_var = setup.auth.api_key_env_var or (
                "ANTHROPIC_API_KEY" if is_claude_family else "GEMINI_API_KEY"
            )
            if not eff_env.get(target_var):
                _diag(
                    "HARNESS_AUTH_MISSING",
                    f"harness_setup.auth.api_key_env_var:{target_var}",
                    (
                        f"Required API key environment variable '{target_var}' is missing "
                        "or empty in os.environ and harness_setup.env."
                    ),
                )
        elif eff_auth_mode in {"vertex_adc", "vertex"}:
            gcp_project = (
                setup.auth.gcp_project
                or eff_env.get("GOOGLE_CLOUD_PROJECT")
                or eff_env.get("ANTHROPIC_VERTEX_PROJECT_ID")
            )
            if not gcp_project:
                _diag(
                    "HARNESS_AUTH_MISSING",
                    "harness_setup.auth.gcp_project",
                    "GCP project ID is required for vertex_adc auth mode.",
                )
        elif eff_auth_mode == "aws_bedrock":
            aws_reg = (
                setup.auth.aws_region
                or eff_env.get("AWS_REGION")
                or eff_env.get("AWS_DEFAULT_REGION")
            )
            if not aws_reg:
                _diag(
                    "HARNESS_AUTH_MISSING",
                    "harness_setup.auth.aws_region",
                    "AWS region is required for aws_bedrock auth mode.",
                )

    # 2b. Ontology Policy availability
    raw_ontology_policy = getattr(spec, "ontology_policy", "off")
    if str(raw_ontology_policy).strip().lower() not in {"off", "", "disabled", "false", "none"}:
        checked_items += 1
        try:
            from opendv_contracts.errors import OntologyRecommenderUnavailableError
            from opendv_service.ontology.policy import OntologyPolicy

            pol = OntologyPolicy.from_value(raw_ontology_policy)
            pol.validate_availability()
        except OntologyRecommenderUnavailableError as exc:
            _diag("ONTOLOGY_RECOMMENDER_UNAVAILABLE", "ontology_policy", str(exc))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            _diag("ONTOLOGY_POLICY_INVALID", "ontology_policy", str(exc))

    # 3. Blueprint / Ontology / Contract validation
    checked_items += 1
    for p_name, p_val in (
        ("graph_a_ontology_path", getattr(spec, "graph_a_ontology_path", None)),
        ("ontology_path", getattr(spec, "ontology_path", None)),
        ("blueprint_path", getattr(spec, "blueprint_path", None)),
        ("initial_contract_path", getattr(spec, "initial_contract_path", None)),
        ("output_dir", getattr(spec, "output_dir", None)),
    ):
        if p_val and isinstance(p_val, (str, Path)):
            raw_p = Path(p_val)
            if ".." in raw_p.parts or ".." in str(p_val):
                _diag(
                    "PATH_TRAVERSAL_REJECTED",
                    p_name,
                    f"Path traversal ('..') is strictly forbidden: {p_val}",
                )
                continue
            try:
                res = (eff_base / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()
                tmp_res = Path(tempfile.gettempdir()).resolve()
                if not (
                    res.is_relative_to(eff_base)
                    or res.is_relative_to(ws_root)
                    or res.is_relative_to(tmp_res)
                ):
                    _diag("PATH_TRAVERSAL_REJECTED", p_name, f"Path '{p_val}' escapes workspace.")
            except (ValueError, OSError, RuntimeError):
                _diag("PATH_TRAVERSAL_REJECTED", p_name, f"Invalid path '{p_val}'.")

    bp = spec.blueprint
    if not bp and (spec.blueprint_path or spec.blueprint_content):
        try:
            if spec.blueprint_content:
                bp_data = _parse_yaml_or_json_content(spec.blueprint_content)
            elif spec.blueprint_path:
                bp_data = _load_yaml_or_json(Path(spec.blueprint_path))
            else:
                bp_data = {}
            bp = VaultBlueprint.model_validate(bp_data)
        except _PARSE_EXC as exc:
            _diag("BLUEPRINT_LOAD_FAILED", "blueprint", f"Failed loading VaultBlueprint: {exc}")

    if bp is not None:
        try:
            expected_ont_sha = compute_ontology_sha256(bp.ontology)
            if bp.lineage.ontology_sha256 != expected_ont_sha:
                _diag(
                    "BLUEPRINT_HASH_MISMATCH",
                    "blueprint.lineage.ontology_sha256",
                    (
                        "Lineage ontology hash mismatch: "
                        f"{bp.lineage.ontology_sha256} vs {expected_ont_sha}"
                    ),
                )
            if bp.contract is not None:
                expected_cnt_sha = compute_contract_sha256(bp.contract)
                if bp.lineage.contract_sha256 and bp.lineage.contract_sha256 != expected_cnt_sha:
                    _diag(
                        "BLUEPRINT_HASH_MISMATCH",
                        "blueprint.lineage.contract_sha256",
                        (
                            "Lineage contract hash mismatch: "
                            f"{bp.lineage.contract_sha256} vs {expected_cnt_sha}"
                        ),
                    )
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            _diag("BLUEPRINT_VERIFICATION_FAILED", "blueprint", str(exc))
    elif spec.graph_a_source or spec.graph_a_ontology_path:
        from opendv_sources import load_graph_a_source

        g_inp = (
            spec.graph_a_source if spec.graph_a_source is not None else spec.graph_a_ontology_path
        )
        try:
            load_graph_a_source(g_inp, base_dir=eff_base, workspace_root=ws_root)
        except (*_PARSE_EXC, RuntimeError) as exc:
            code = (
                "PATH_TRAVERSAL_REJECTED"
                if "Path traversal" in str(exc)
                else "ONTOLOGY_SCHEMA_INVALID"
            )
            _diag(code, "graph_a_ontology_path", str(exc))
    elif not spec.ontology and not spec.ontology_path and not spec.ontology_content:
        waves_list = getattr(spec, "waves", None) or []
        has_ont = False
        for w in waves_list:
            opts = getattr(w, "options", {})
            if opts.get("ontology") or opts.get("ontology_path"):
                has_ont = True
        if not has_ont:
            _diag(
                "ONTOLOGY_MISSING",
                "ontology",
                "No GraphAOntology or VaultBlueprint provided in input spec.",
            )

    # Check unstructured without relational source
    is_unstr_only = getattr(spec, "unstructured_only", False)
    has_prior_contract = bool(
        getattr(spec, "initial_contract", None)
        or getattr(spec, "initial_contract_path", None)
        or getattr(spec, "initial_contract_content", None)
        or getattr(spec, "blueprint", None)
        or getattr(spec, "blueprint_path", None)
        or getattr(spec, "blueprint_content", None)
    )
    if not is_unstr_only and not has_prior_contract:
        msg = (
            "Unstructured documents onboarded before any relational "
            "source has been onboarded."
        )
        waves_list = getattr(spec, "waves", None) or []
        if waves_list:
            rel_seen = False
            for w in sorted(waves_list, key=lambda x: getattr(x, "wave_index", 0)):
                if getattr(w, "sources", []):
                    rel_seen = True
                if getattr(w, "unstructured_documents", []) and not rel_seen:
                    _diag(
                        "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE",
                        f"waves[{getattr(w, 'wave_index', 0)}]",
                        msg,
                    )
                    break
        elif spec.unstructured_documents and not spec.sources:
            _diag("UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE", "sources", msg)

    # 4. Sources validation
    waves_list = getattr(spec, "waves", None) or []
    all_sources: list[PipelineSourceFeedInput] = (
        [s for w in waves_list for s in getattr(w, "sources", [])]
        if waves_list
        else list(spec.sources)
    )

    for idx, src in enumerate(all_sources):
        checked_items += 1
        cred_err = _check_credentials_ref(src.credentials_ref)
        if cred_err:
            _diag("LITERAL_SECRET_DETECTED", f"sources[{idx}].credentials_ref", cred_err)

        for opt_k in ("token", "api_key", "secret", "password"):
            if opt_k in src.options and not str(src.options[opt_k]).startswith(("env:", "secret:")):
                _diag(
                    "LITERAL_SECRET_DETECTED",
                    f"sources[{idx}].options.{opt_k}",
                    f"Literal secret in options.{opt_k}. Must use 'env:' or 'secret:'.",
                )

        bks = getattr(src, "business_key_columns", []) or getattr(src, "business_keys", [])
        for bk in bks:
            if isinstance(bk, str) and not bk.strip():
                _diag(
                    "MISSING_BUSINESS_KEY_COLUMNS",
                    f"sources[{idx}].business_key_columns",
                    "Blank business key column.",
                )

        dp = getattr(src, "data_path", None) or src.uri
        if dp and not str(dp).startswith(
            ("sqlite://", "databricks://", "uc://", "duckdb://", "http://", "https://", "cas://")
        ):
            valid, resolved_p, err_msg = _validate_path_containment(str(dp), eff_base, ws_root)
            if not valid:
                _diag(
                    "PATH_TRAVERSAL_REJECTED", f"sources[{idx}].data_path", err_msg or "Path escape"
                )
            elif resolved_p and not resolved_p.exists() and not src.options.get("allow_missing"):
                _diag(
                    "SOURCE_FILE_MISSING",
                    f"sources[{idx}].data_path",
                    f"Source file '{dp}' not found.",
                )
            elif (
                resolved_p
                and resolved_p.is_file()
                and getattr(src, "adapter", "file") in ("file", None, "")
            ):
                try:
                    ad = get_source_registry().get_tabular_adapter(
                        "file",
                        workspace_root=ws_root,
                        allowed_roots=[eff_base, ws_root],
                        allow_temp=True,
                    )
                    desc = ad.describe(SourceSpec(adapter="file", uri=str(resolved_p)))
                    file_cols = {c.name for c in desc.columns}
                    fk_keys = (
                        list(src.foreign_key_mappings.keys())
                        if isinstance(getattr(src, "foreign_key_mappings", None), dict)
                        else []
                    )
                    declared = (
                        set(getattr(src, "business_key_columns", []) or [])
                        | set(getattr(src, "surrogate_key_columns", []) or [])
                        | set(getattr(src, "pii_columns", []) or [])
                        | set(fk_keys)
                    )
                    missing = sorted([c for c in declared if c and c not in file_cols])
                    if missing:
                        _diag(
                            "DECLARED_COLUMNS_MISSING",
                            f"sources[{idx}].columns",
                            f"Declared columns missing from source: {missing}",
                        )
                except (KeyError, ValueError, OSError, RuntimeError, OpenDVError):
                    pass

    # 5. Documents validation
    waves_list = getattr(spec, "waves", None) or []
    all_docs: list[PipelineUnstructuredDocInput] = (
        [d for w in waves_list for d in getattr(w, "unstructured_documents", [])]
        if waves_list
        else list(spec.unstructured_documents)
    )

    for idx, doc in enumerate(all_docs):
        checked_items += 1
        fp = getattr(doc, "document_path", None) or getattr(doc, "file_path", None)
        has_inline_content = bool(
            getattr(doc, "content", None) and str(doc.content).strip()
        )
        if fp and (not has_inline_content or ".." in str(fp)):
            valid, resolved_p, err_msg = _validate_path_containment(str(fp), eff_base, ws_root)
            if not valid:
                _diag(
                    "PATH_TRAVERSAL_REJECTED",
                    f"documents[{idx}].document_path",
                    err_msg or "Path escape",
                )
            elif resolved_p and not resolved_p.exists() and not getattr(doc, "content", None):
                _diag(
                    "SOURCE_FILE_MISSING",
                    f"documents[{idx}].document_path",
                    f"Document '{fp}' not found.",
                )
            elif resolved_p and resolved_p.is_file():
                doc_text = (
                    resolved_p.read_text(encoding="utf-8", errors="ignore")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                )
                doc_len = len(doc_text)
                for cand in getattr(doc, "extracted_candidates", []):
                    c_end = getattr(cand, "char_end", 0)
                    c_start = getattr(cand, "char_start", 0)
                    if c_end > doc_len or c_start < 0:
                        _diag(
                            "SPAN_OUT_OF_BOUNDS",
                            f"documents[{idx}].candidates[{cand.candidate_id}]",
                            f"Span [{c_start}, {c_end}] out of bounds for doc len {doc_len}",
                        )
                    elif cand.verbatim_quote is not None:
                        actual_slice = doc_text[c_start:c_end]
                        norm_quote = cand.verbatim_quote.replace("\r\n", "\n").replace("\r", "\n")
                        if actual_slice != norm_quote:
                            _diag(
                                "SPAN_QUOTE_MISMATCH",
                                f"documents[{idx}].candidates[{cand.candidate_id}]",
                                f"Quote mismatch: '{norm_quote}' != '{actual_slice}'",
                            )

    passed = not any(d.get("severity") == "ERROR" for d in diagnostics)
    return DeterministicValidationReport(
        stage="INPUT_VALIDATION",
        passed=passed,
        checked_items_count=checked_items,
        diagnostics=diagnostics,
    )


__all__ = [
    "_first_non_empty",
    "_load_yaml_or_json",
    "_validate_path_containment",
    "validate_pipeline_inputs",
]
