"""Pre-flight helpers for single-run pipeline execution: readiness, CAS, and DTO coercion."""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness import HarnessSetupConfig
from opendv_agent_harness.pipeline.runner import run_agentic_vault_pipeline
from opendv_contracts.dtos.blueprint import VaultBlueprintDTO
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.paths import PathContainmentError, resolve_workspace_path
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


def _is_hex64(val: Any) -> bool:
    return bool(isinstance(val, str) and re.fullmatch(r"[0-9a-fA-F]{64}", val.strip()))


def _load_session_manifest_map(
    session_id: str | None,
    state: Any | None,
) -> dict[str, dict[str, Any]]:
    if not session_id:
        return {}
    ws_root = getattr(state, "workspace_root", None) or getattr(state, "repo_root", None)
    by_key: dict[str, dict[str, Any]] = {}
    for s_root in _candidate_studio_roots(state):
        with contextlib.suppress(Exception):
            mgr = StudioSessionManager(studio_root=s_root, workspace_root=ws_root)
            mfile = mgr.get_session_dir(session_id) / "inputs" / "manifest.json"
            if not mfile.is_file():
                continue
            mdata = json.loads(mfile.read_text(encoding="utf-8"))
            for item in mdata.get("files") or []:
                if not isinstance(item, dict) or item.get("tombstoned"):
                    continue
                sha = str(item.get("sha256") or "").strip().lower()
                sid = str(item.get("source_id") or "").strip()
                if sha:
                    by_key[sha] = item
                if sid:
                    by_key[sid] = item
            if by_key:
                return by_key
    return by_key


def _sniff_cas_blob_format(cand_p: Path) -> str:
    with contextlib.suppress(OSError), open(cand_p, "rb") as bf:
        hd = bf.read(4096)
        if hd.startswith(b"SQLite format 3\x00"):
            return "sqlite"
        if len(hd) >= 12 and hd[8:12] == b"DUCK":
            return "duckdb"
        if hd.startswith(b"PAR1"):
            return "parquet"
        txt = hd.decode("utf-8", errors="ignore").strip()
        if txt.startswith(("{", "[")):
            return "json"
        if "\t" in txt:
            return "tsv"
        if "," in txt or "\n" in txt:
            return "csv"
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
    manifest_map = _load_session_manifest_map(session_id, state)
    resolved: list[dict[str, Any]] = []
    for s in sources_list:
        s_dict = _to_dict(s)
        c_hash = _extract_cas_hash(str(s_dict.get("uri") or ""))
        if c_hash is not None:
            cand_p = _find_cas_blob_path(c_hash, cas_store, session_id=session_id, state=state)
            if cand_p is not None:
                s_opts = dict(s_dict.get("options") or {})
                m_entry = manifest_map.get(c_hash) or manifest_map.get(
                    str(s_dict.get("feed_id") or "")
                )
                m_fname = str((m_entry or {}).get("filename") or "").strip()
                m_mime = str((m_entry or {}).get("mime_type") or "").lower()
                m_adp = str((m_entry or {}).get("connector_adapter") or "").lower()

                sniffed = _sniff_cas_blob_format(cand_p)
                raw_fmt = (
                    s_dict.get("format")
                    or s_opts.get("source_format")
                    or s_opts.get("format")
                    or (Path(m_fname).suffix.lower().lstrip(".") if m_fname else "")
                    or sniffed
                )
                norm_fmt = "parquet" if raw_fmt == "pq" else str(raw_fmt or "").lower().strip(".")
                is_sqlite = (
                    sniffed == "sqlite"
                    or s_dict.get("adapter") == "sqlite"
                    or m_adp == "sqlite"
                    or "sqlite" in m_mime
                )
                is_duckdb = (
                    sniffed == "duckdb"
                    or s_dict.get("adapter") == "duckdb"
                    or m_adp == "duckdb"
                    or "duckdb" in m_mime
                    or norm_fmt in {"duckdb", "ddb"}
                )

                s_sys = s_dict.get("source_system") or "cas_store"
                opt_tbl = str(s_opts.get("table_name") or "").strip()
                opt_src = str(s_opts.get("source_name") or "").strip()
                raw_tbl = str(s_dict.get("table_name") or "").strip()
                raw_src = str(s_dict.get("source_name") or "").strip()
                m_stem = Path(m_fname).stem if m_fname else ""
                logical_tbl = next(
                    (
                        cand
                        for cand in (
                            raw_tbl,
                            opt_tbl,
                            m_stem,
                            Path(opt_src).stem if opt_src else "",
                            Path(raw_src).stem if raw_src else "",
                            s_sys,
                            str(s_dict.get("feed_id") or ""),
                        )
                        if cand and not _is_hex64(cand)
                    ),
                    "table",
                )
                logical_src = next(
                    (
                        cand
                        for cand in (raw_src, opt_src, m_fname, logical_tbl)
                        if cand and not _is_hex64(cand)
                    ),
                    logical_tbl,
                )

                s_opts["source_system"] = s_sys
                s_opts.setdefault("table_name", logical_tbl)
                s_opts.setdefault("source_name", logical_src)
                if norm_fmt and not is_sqlite and not is_duckdb:
                    s_opts.setdefault("source_format", norm_fmt)
                    s_dict.setdefault("format", norm_fmt)
                s_dict["options"] = s_opts
                s_dict["source_system"] = s_sys
                s_dict["table_name"] = logical_tbl
                s_dict["source_name"] = logical_src

                if is_sqlite:
                    s_dict["uri"] = f"sqlite:///{cand_p.as_posix()}"
                    s_dict["adapter"] = "sqlite"
                elif is_duckdb:
                    s_dict["uri"] = f"duckdb:///{cand_p.as_posix()}"
                    s_dict["adapter"] = "duckdb"
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


def _load_preflight_mapping(
    raw_mapping: Any,
    raw_content: Any,
    raw_path: Any,
    state: Any | None,
) -> dict[str, Any]:
    text: str | None = None
    if isinstance(raw_path, str) and raw_path.strip():
        ws_root = (
            getattr(state, "repo_root", None)
            or getattr(state, "workspace_root", None)
            or Path.cwd()
        )
        with contextlib.suppress(PathContainmentError, OSError, UnicodeDecodeError):
            safe_p = resolve_workspace_path(
                raw_path.strip(), workspace_root=Path(ws_root), allow_temp=True
            )
            if safe_p.is_file():
                text = safe_p.read_text(encoding="utf-8")
        if not text:
            return {}
    elif isinstance(raw_content, str) and raw_content.strip():
        text = raw_content
    elif isinstance(raw_mapping, str) and raw_mapping.strip():
        text = raw_mapping
    elif isinstance(raw_mapping, dict):
        return raw_mapping
    if text:
        with contextlib.suppress(yaml.YAMLError, ValueError, TypeError):
            loaded = yaml.safe_load(text)
            if isinstance(loaded, dict):
                return loaded
    return {}


def _req_field(req: Any, name: str) -> Any:
    return req.get(name) if isinstance(req, dict) else getattr(req, name, None)


def _effective_harness_setup(req: Any, state: Any | None) -> HarnessSetupConfig:
    """Resolve active HarnessSetupConfig from request override or PlatformStateContainer."""
    hc_path = _req_field(req, "harness_config_path")
    if isinstance(hc_path, str) and hc_path.strip():
        hc_dict = _load_preflight_mapping(None, None, hc_path, state)
        if hc_dict:
            with contextlib.suppress(ValueError, TypeError):
                return HarnessSetupConfig.model_validate(hc_dict)
        return HarnessSetupConfig()
    req_hc = _req_field(req, "harness_config")
    if isinstance(req_hc, HarnessSetupConfig):
        return req_hc
    if isinstance(req_hc, dict):
        return HarnessSetupConfig.model_validate(req_hc)
    if state is not None and hasattr(state, "get_active_harness_setup_dict"):
        raw = state.get_active_harness_setup_dict()
        if isinstance(raw, dict):
            return HarnessSetupConfig.model_validate(raw)
    setup = getattr(state, "harness_setup", None)
    if setup is None and state is not None and hasattr(state, "get_harness_setup"):
        setup = state.get_harness_setup()
    return setup if isinstance(setup, HarnessSetupConfig) else HarnessSetupConfig()


def _extract_mode_and_policy(req: Any, state: Any | None) -> tuple[str, str]:
    input_spec = _load_preflight_mapping(
        _req_field(req, "input_spec"),
        _req_field(req, "input_spec_content"),
        _req_field(req, "input_spec_path"),
        state,
    )
    options = getattr(req, "options", None) or {}
    if not isinstance(options, dict):
        options = {}
    config = getattr(req, "config", None) or {}
    if not isinstance(config, dict):
        config = {}

    raw_mode = (
        _req_field(req, "mode")
        or input_spec.get("mode")
        or options.get("mode")
        or config.get("mode")
        or "auto"
    )
    clean_mode = str(raw_mode).strip().lower()

    prov_ovr = _req_field(req, "provider_override")
    ovr_policy = (
        prov_ovr.get("stage_output_policy")
        if isinstance(prov_ovr, dict)
        else getattr(prov_ovr, "stage_output_policy", None)
    )
    gov_pol = _req_field(req, "governance_policy") or input_spec.get("governance_policy")
    gov_policy = (
        gov_pol.get("stage_output_policy")
        if isinstance(gov_pol, dict)
        else getattr(gov_pol, "stage_output_policy", None)
    )
    state_policy = _effective_harness_setup(req, state).stage_output_policy
    raw_policy = (
        _req_field(req, "stage_output_policy")
        or input_spec.get("stage_output_policy")
        or gov_policy
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

    setup = _effective_harness_setup(req, state)
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
