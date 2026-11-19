"""Pre-flight helpers for single-run pipeline execution: readiness, CAS, and DTO coercion."""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_agent_harness import HarnessSetupConfig
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_contracts.dtos.blueprint import VaultBlueprintDTO
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.seams import get_default_pipeline_runner
from opendv_service.state.platform_config_probe import probe_providers_readiness_list
from opendv_service.studio.job_runners_evidence import (
    build_studio_ingestion_service,
    load_active_session_evidence,
)
from opendv_service.studio.session_manager import StudioSessionManager


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


def _to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    return dict(item)


def _candidate_studio_roots(state: Any | None) -> list[Path]:
    raw_cands: list[Path] = []
    env_root = os.environ.get("OPENDV_STUDIO_ROOT", "").strip()
    if env_root:
        raw_cands.append(Path(env_root))
    ws_root = getattr(state, "workspace_root", None) or getattr(state, "repo_root", None)
    if ws_root is not None:
        raw_cands.append(Path(ws_root) / ".opendv" / "ontology_studio")
    s_dir = getattr(state, "state_dir", None) or getattr(state, "storage_root", None)
    if s_dir is not None:
        raw_cands.append(Path(s_dir).parent / "ontology_studio")

    roots: list[Path] = []
    seen: set[Path] = set()
    for cand in raw_cands:
        resolved = cand.resolve()
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _extract_cas_hash(uri: str) -> str | None:
    clean = str(uri or "").strip()
    if not clean.startswith("cas://"):
        return None
    raw_target = clean[len("cas://") :].split("?")[0].split("#")[0].strip()
    c_hash = raw_target.split("/")[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", c_hash):
        return None
    return c_hash


def _find_cas_blob_path(
    c_hash: str,
    cas_store: Any | None,
    *,
    session_id: str | None = None,
    state: Any | None = None,
) -> Path | None:
    if cas_store is not None and hasattr(cas_store, "base_dir"):
        cas_base = Path(cas_store.base_dir).resolve()
        cands = [
            cas_base / c_hash.upper(),
            cas_base / c_hash.lower(),
            cas_base / c_hash[:2] / c_hash[2:],
        ]
        found = next((c for c in cands if c.is_file()), None)
        if found is not None:
            return found

    if session_id:
        ws_root = getattr(state, "workspace_root", None) or getattr(state, "repo_root", None)
        for s_root in _candidate_studio_roots(state):
            with contextlib.suppress(Exception):
                mgr = StudioSessionManager(studio_root=s_root, workspace_root=ws_root)
                cand_p = mgr.get_cas(session_id).get_path(c_hash, verify=False)
                if cand_p.is_file():
                    return cand_p
    return None


def _read_studio_chunks_text(
    session_id: str | None,
    state: Any | None,
    c_hash: str,
    doc_id: str | None,
) -> str:
    if not session_id:
        return ""
    ws_root = getattr(state, "workspace_root", None) or getattr(state, "repo_root", None)
    for s_root in _candidate_studio_roots(state):
        with contextlib.suppress(Exception):
            mgr = StudioSessionManager(studio_root=s_root, workspace_root=ws_root)
            s_dir = mgr.get_session_dir(session_id)
            if not (s_dir / "evidence" / "chunks.jsonl").is_file():
                continue
            chunks, _ = load_active_session_evidence(s_dir)
            texts = [
                c.text
                for c in chunks
                if c.text
                and c.text.strip()
                and not (c.metadata or {}).get("do_not_send_to_agent")
                and (
                    (c_hash and (c.source_sha256 or "").lower() == c_hash)
                    or (
                        doc_id
                        and (
                            c.document_id == doc_id or (c.metadata or {}).get("source_id") == doc_id
                        )
                    )
                )
            ]
            if texts:
                return "\n\n".join(texts)
    return ""


def _extract_doc_text(cand_p: Path, title: str, mime: str) -> str:
    with contextlib.suppress(Exception):
        svc = build_studio_ingestion_service(ocr_mode="off")
        res = svc.ingest_bytes(
            data=cand_p.read_bytes(),
            filename=title or cand_p.name,
            mime_type=mime or None,
            ocr_mode="off",
        )
        parts = [c.text for c in (res.chunks or []) if c.text and c.text.strip()]
        if parts:
            return "\n\n".join(parts)
    if mime.startswith("text/") or Path(title).suffix.lower() in {
        ".md",
        ".markdown",
        ".txt",
        ".html",
        ".htm",
        ".rtf",
    }:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            return cand_p.read_text(encoding="utf-8", errors="ignore")
    return ""


def resolve_cas_sources_list(
    sources_list: Sequence[Any] | None,
    cas_store: Any | None,
    *,
    session_id: str | None = None,
    state: Any | None = None,
) -> list[dict[str, Any]]:
    """Resolve cas:// URIs in source feed definitions to staged filesystem or sqlite paths."""
    if not sources_list:
        return []
    resolved: list[dict[str, Any]] = []
    for s in sources_list:
        s_dict = _to_dict(s)
        c_hash = _extract_cas_hash(str(s_dict.get("uri") or ""))
        if c_hash is not None:
            cand_p = _find_cas_blob_path(c_hash, cas_store, session_id=session_id, state=state)
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


def resolve_cas_unstructured_docs_list(
    docs_list: Sequence[Any] | None,
    cas_store: Any | None,
    *,
    session_id: str | None = None,
    state: Any | None = None,
) -> list[dict[str, Any]]:
    """Resolve cas:// URIs in unstructured documents and hydrate extracted text when blank."""
    if not docs_list:
        return []
    resolved: list[dict[str, Any]] = []
    for d in docs_list:
        d_dict = _to_dict(d)
        src_obj = d_dict.get("source")
        meta_obj = d_dict.get("metadata")
        raw_uri = next(
            (
                str(val).strip()
                for val in (
                    d_dict.get("document_path"),
                    d_dict.get("file_path"),
                    src_obj.get("uri") if isinstance(src_obj, dict) else None,
                    meta_obj.get("uri") if isinstance(meta_obj, dict) else None,
                )
                if val and str(val).strip().startswith("cas://")
            ),
            "",
        )
        c_hash = _extract_cas_hash(raw_uri)
        if c_hash is not None:
            cand_p = _find_cas_blob_path(c_hash, cas_store, session_id=session_id, state=state)
            if cand_p is not None:
                d_dict["document_path"] = cand_p.as_posix()
                d_dict["file_path"] = cand_p.as_posix()
                if not str(d_dict.get("content") or "").strip():
                    doc_id = str(
                        d_dict.get("doc_id")
                        or (meta_obj.get("source_id") if isinstance(meta_obj, dict) else "")
                        or ""
                    )
                    title = str(
                        (meta_obj.get("title") if isinstance(meta_obj, dict) else "") or cand_p.name
                    )
                    mime = str(d_dict.get("mime_type") or "").split(";")[0].strip().lower()
                    content = _read_studio_chunks_text(
                        session_id, state, c_hash, doc_id or None
                    ) or _extract_doc_text(cand_p, title, mime)
                    if content.strip():
                        d_dict["content"] = content
        resolved.append(d_dict)
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
    state_policy = (
        getattr(state_setup, "stage_output_policy", "strict") if state_setup else "strict"
    )
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
            f"Configured agent provider '{env_provider}' is not ready "
            "(missing CLI binary or API key).",
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
        # Allow fallback only if explicit_prov matches default setup provider
        if clean_prov != getattr(setup, "provider", ""):
            raise OpenDVError(
                f"Requested agent provider '{clean_prov}' is not available "
                "(missing CLI binary or API key).",
                error_code=ErrorCode.AGENT_NOT_CONFIGURED,
            )

    if any(p.available for p in readiness):
        return

    raise OpenDVError(
        "No configured LLM agent provider or CLI binary is available for strict "
        "pipeline execution. Configure GEMINI_API_KEY / ANTHROPIC_API_KEY, "
        "install agy/claude CLI, or use mode='deterministic'.",
        error_code=ErrorCode.AGENT_NOT_CONFIGURED,
    )


__all__ = [
    "ensure_pipeline_agent_ready",
    "resolve_cas_sources_list",
    "resolve_cas_unstructured_docs_list",
    "to_vault_blueprint_dto",
]
