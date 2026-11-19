"""Pre-flight helpers for single-run pipeline execution: strict agent readiness, CAS resolution, and DTO coercion."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness import HarnessSetupConfig
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_contracts.dtos.blueprint import VaultBlueprintDTO
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.seams import get_default_pipeline_runner
from opendv_service.state.platform_config_probe import probe_providers_readiness_list


def to_vault_blueprint_dto(raw_bp: Any) -> VaultBlueprintDTO | None:
    """Coerce raw blueprint dict/model into VaultBlueprintDTO safely."""
    if raw_bp is None or isinstance(raw_bp, VaultBlueprintDTO):
        return raw_bp
    if hasattr(raw_bp, "model_dump"):
        d = raw_bp.model_dump(mode="json")
    elif isinstance(raw_bp, dict):
        d = dict(raw_bp)
    else:
        return None
    clean_d = {k: v for k, v in d.items() if k != "blueprint_version"}
    clean_d["blueprint_version"] = "1.0.0"
    clean_d.pop("schema_version", None)
    try:
        return VaultBlueprintDTO.model_validate(clean_d)
    except (ValueError, TypeError, KeyError):
        return None


def resolve_cas_sources_list(
    sources_list: Sequence[Any] | None,
    cas_store: Any | None,
) -> list[dict[str, Any]]:
    """Resolve cas:// URIs in source feed definitions to staged filesystem or sqlite paths."""
    if not sources_list:
        return []
    if cas_store is None or not hasattr(cas_store, "base_dir"):
        return [
            dict(s)
            if isinstance(s, dict)
            else (s.model_dump(mode="json") if hasattr(s, "model_dump") else dict(s))
            for s in sources_list
        ]
    cas_base = Path(cas_store.base_dir).resolve()
    resolved: list[dict[str, Any]] = []
    for s in sources_list:
        s_dict = (
            dict(s)
            if isinstance(s, dict)
            else (s.model_dump(mode="json") if hasattr(s, "model_dump") else dict(s))
        )
        s_uri = str(s_dict.get("uri") or "").strip()
        if s_uri.startswith("cas://"):
            raw_target = s_uri[len("cas://") :].split("?")[0].split("#")[0].strip()
            c_hash = raw_target.split("/")[0].strip().lower()
            cands = [cas_base / c_hash.upper(), cas_base / c_hash.lower()]
            cand_p = next((c for c in cands if c.is_file()), None)
            if cand_p is not None:
                is_sqlite = False
                with contextlib.suppress(OSError), open(cand_p, "rb") as bf:
                    if bf.read(16).startswith(b"SQLite format 3\x00"):
                        is_sqlite = True
                s_sys = s_dict.get("source_system") or "cas_store"
                s_opts = dict(s_dict.get("options") or {})
                s_opts["source_system"] = s_sys
                s_dict["options"] = s_opts
                s_dict["source_system"] = s_sys
                if is_sqlite:
                    s_dict["uri"] = f"sqlite:///{cand_p.as_posix()}"
                    s_dict["adapter"] = "sqlite"
                else:
                    s_dict["uri"] = cand_p.as_posix()
                    s_dict.setdefault("adapter", "file")
        resolved.append(s_dict)
    return resolved


def _extract_mode_and_policy(req: Any, state: Any | None) -> tuple[str, str]:
    input_spec = getattr(req, "input_spec", None)
    if not isinstance(input_spec, dict) and isinstance(req, dict):
        input_spec = req.get("input_spec")
    options = getattr(req, "options", None) or {}
    if not isinstance(options, dict):
        options = {}
    config = getattr(req, "config", None) or {}
    if not isinstance(config, dict):
        config = {}

    raw_mode = (
        getattr(req, "mode", None)
        or (req.get("mode") if isinstance(req, dict) else None)
        or (input_spec.get("mode") if isinstance(input_spec, dict) else None)
        or options.get("mode")
        or config.get("mode")
        or "auto"
    )
    clean_mode = str(raw_mode).strip().lower()

    prov_ovr = getattr(req, "provider_override", None) or (
        req.get("provider_override") if isinstance(req, dict) else None
    )
    ovr_policy = (
        prov_ovr.get("stage_output_policy")
        if isinstance(prov_ovr, dict)
        else getattr(prov_ovr, "stage_output_policy", None)
    )
    state_setup = getattr(state, "harness_setup", None)
    if state_setup is None and state is not None and hasattr(state, "get_harness_setup"):
        state_setup = state.get_harness_setup()
    state_policy = getattr(state_setup, "stage_output_policy", "strict") if state_setup else "strict"
    raw_policy = (
        getattr(req, "stage_output_policy", None)
        or (req.get("stage_output_policy") if isinstance(req, dict) else None)
        or (input_spec.get("stage_output_policy") if isinstance(input_spec, dict) else None)
        or ovr_policy
        or config.get("stage_output_policy")
        or state_policy
        or "strict"
    )
    return clean_mode, str(raw_policy).strip().lower()


def ensure_pipeline_agent_ready(
    state: Any | None,
    req: Any,
    *,
    pipeline_runner: Any | None = None,
) -> None:
    """Enforce synchronous 422 AGENT_NOT_CONFIGURED before spawning pipeline execution."""
    if pipeline_runner is not None and pipeline_runner is not run_agentic_vault_pipeline:
        return
    if get_default_pipeline_runner() is not run_agentic_vault_pipeline:
        return

    clean_mode, clean_policy = _extract_mode_and_policy(req, state)
    if clean_mode == "deterministic" or clean_policy == "symbolic_completion":
        return

    setup = getattr(state, "harness_setup", None)
    if setup is None and state is not None and hasattr(state, "get_harness_setup"):
        setup = state.get_harness_setup()
    if not isinstance(setup, HarnessSetupConfig):
        setup = HarnessSetupConfig()
    readiness = probe_providers_readiness_list(setup)

    env_provider = os.environ.get("OPENDV_AGENT_PROVIDER", "").strip().lower()
    if env_provider:
        matched = [p for p in readiness if p.provider == env_provider]
        if matched and matched[0].available:
            return
        raise OpenDVError(
            f"Configured agent provider '{env_provider}' is not ready (missing CLI binary or API key).",
            error_code=ErrorCode.AGENT_NOT_CONFIGURED,
        )

    prov_ovr = getattr(req, "provider_override", None) or (
        req.get("provider_override") if isinstance(req, dict) else None
    )
    explicit_prov = (
        prov_ovr
        if isinstance(prov_ovr, str)
        else (
            prov_ovr.get("provider")
            if isinstance(prov_ovr, dict)
            else getattr(prov_ovr, "provider", None)
        )
    )
    if explicit_prov:
        clean_prov = str(explicit_prov).strip().lower()
        matched = [p for p in readiness if p.provider == clean_prov]
        if matched and matched[0].available:
            return
        # Allow fallback to another ready provider only if explicit_prov matches default setup provider
        if clean_prov != getattr(setup, "provider", ""):
            raise OpenDVError(
                f"Requested agent provider '{clean_prov}' is not available (missing CLI binary or API key).",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            )

    if any(p.available for p in readiness):
        return

    raise OpenDVError(
        "No configured LLM agent provider or CLI binary is available for strict pipeline execution. "
        "Configure GEMINI_API_KEY / ANTHROPIC_API_KEY, install agy/claude CLI, or use mode='deterministic'.",
        error_code=ErrorCode.AGENT_NOT_CONFIGURED,
    )


__all__ = [
    "ensure_pipeline_agent_ready",
    "resolve_cas_sources_list",
    "to_vault_blueprint_dto",
]
