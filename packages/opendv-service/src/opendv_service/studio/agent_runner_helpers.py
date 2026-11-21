"""Helper utilities for StudioAgentRunner: PII masking, credential checks, budget holds, prompts."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final, Literal

from opendv_agent_harness.hold_manager import StewardHoldManager
from opendv_agent_harness.models import HarnessStopReason
from opendv_agent_harness.pipeline.config import HarnessSetupConfig
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_core.config import CoreModelingConfig, get_core_modeling_config
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.state.platform_config_probe import is_module_available

DEFAULT_AGENT_TIMEOUT_SECONDS: Final[float] = 300.0
DETERMINISTIC_FALLBACK_SLA_SECONDS: Final[float] = 2.5
MAX_PROMPT_SAMPLE_VALUES_PER_COL: Final[int] = 5

_PII_COL_HINTS: Final[tuple[str, ...]] = (
    "email",
    "ssn",
    "tax_id",
    "tin",
    "phone",
    "passport",
    "dob",
    "birth",
    "secret",
)
_BOUNDED_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9])"
)
_BOUNDED_SSN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])\d{3}-\d{2}-\d{4}(?![A-Za-z0-9])"
)
_BOUNDED_EIN_RE: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9])\d{2}-\d{7}(?![A-Za-z0-9])")
_BUDGET_STOP_REASONS: Final[frozenset[HarnessStopReason]] = frozenset(
    {
        HarnessStopReason.TOKEN_BUDGET_EXCEEDED,
        HarnessStopReason.COST_BUDGET_EXCEEDED,
        HarnessStopReason.MAX_TURNS_EXCEEDED,
        HarnessStopReason.MAX_MODEL_CALLS_EXCEEDED,
        HarnessStopReason.MAX_TOOL_CALLS_EXCEEDED,
    }
)


def _mask_pii_text(text: str) -> str:
    """Mask PII tokens using (?<![A-Za-z0-9]) boundaries."""
    out = _BOUNDED_EMAIL_RE.sub("<MASKED_PII>", text)
    out = _BOUNDED_SSN_RE.sub("<MASKED_PII>", out)
    return _BOUNDED_EIN_RE.sub("<MASKED_PII>", out)


def serialize_and_mask_telemetry(val: Any) -> str | None:
    """Serialize a prompt or structured/raw response payload and mask PII tokens."""
    if val is None:
        return None
    if hasattr(val, "model_dump_json"):
        raw = str(val.model_dump_json())
    elif isinstance(val, dict | list):
        raw = json.dumps(val, ensure_ascii=False, default=str)
    else:
        raw = str(val)
    return _mask_pii_text(raw) if raw else None


def _has_provider_credentials(setup: HarnessSetupConfig, *, require_sdk: bool = True) -> bool:
    """Check CLI binary or API credentials readiness for `setup.provider`."""
    provider = setup.provider
    if provider in {"agy", "agy-cli"}:
        return bool(shutil.which("agy"))
    if provider in {"claude-cli", "claude-code"}:
        return bool(shutil.which("claude"))
    is_claude = provider == "claude"
    env_var = setup.auth.api_key_env_var or ("ANTHROPIC_API_KEY" if is_claude else "GEMINI_API_KEY")
    has_key = bool((setup.env.get(env_var) or os.environ.get(env_var, "")).strip())
    if not has_key:
        return False
    if require_sdk:
        mods = (
            ("claude_agent_sdk", "anthropic")
            if is_claude
            else ("google.antigravity", "google.genai")
        )
        return is_module_available(*mods)
    return True


def _run_coro_sync(coro: Any, timeout: float | None = None) -> Any:
    """Run an async coroutine safely from synchronous worker threads."""

    async def _with_timeout() -> Any:
        if timeout is not None and timeout > 0:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _with_timeout()).result()


def resolve_budget_hold(
    *,
    hold_mgr: StewardHoldManager,
    hold_id: str,
    action: Literal["raise_budget_and_resume", "abort"],
    new_max_output_tokens: int | None = None,
    new_max_cost_usd: float | None = None,
    resolution_notes: str | None = None,
) -> dict[str, Any]:
    """Resolve a budget/timeout Steward Hold by raising limits or aborting."""
    norm_action = "APPROVE" if action == "raise_budget_and_resume" else "REJECT"
    rec = hold_mgr.resolve_hold(
        hold_id,
        action=norm_action,
        selected_option_id=action,
        resolution_notes=resolution_notes or f"Resolved budget hold via {action}.",
    )
    status_val = str(rec.status)
    return {
        "action": action,
        "hold_id": hold_id,
        "hold_status": status_val,
        "record_status": str(rec.status),
        "updated_budget": {
            "max_output_tokens": new_max_output_tokens,
            "max_cost_usd": new_max_cost_usd,
        },
    }


def _chunk_source_key(chk: EvidenceChunk, meta: dict[str, Any]) -> str:
    return str(meta.get("source_id") or meta.get("filename") or chk.document_id)


def _is_table_bearing_chunk(chk: EvidenceChunk, meta: dict[str, Any]) -> bool:
    return bool(
        chk.is_table
        or meta.get("is_table") is True
        or meta.get("table_data")
        or str(meta.get("layout_complexity") or "").upper() == "TABULAR"
    )


def _score_prose_chunk(text: str, *, is_duplicate: bool) -> int:
    stripped = text.strip()
    if len(stripped) < 20 or is_duplicate:
        return -100
    unique_tokens = len(set(re.findall(r"[A-Za-z0-9_]{3,}", stripped)))
    has_struct = 15 if any(sym in stripped for sym in (":", "|", "- ")) else 0
    dot_penalty = 60 if "....." in stripped else 0
    return unique_tokens + has_struct - dot_penalty


def _truncate_chunk_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "... [truncated]"
    keep = max(1, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


def _select_budgeted_chunks(
    evidence_chunks: Sequence[EvidenceChunk],
    cfg: CoreModelingConfig,
    remaining_chars: int,
) -> list[dict[str, Any]]:
    """Prioritize table chunks & small inline notes, then pack diverse non-boilerplate prose."""
    eligible: list[tuple[int, EvidenceChunk, dict[str, Any], str, str]] = []
    source_chars: dict[str, int] = {}
    norm_counts: dict[str, int] = {}

    for idx, chk in enumerate(evidence_chunks):
        meta = dict(chk.metadata or {})
        if meta.get("do_not_send_to_agent") is True:
            continue
        masked = _mask_pii_text(chk.text or "")
        skey = _chunk_source_key(chk, meta)
        source_chars[skey] = source_chars.get(skey, 0) + len(masked)
        norm = " ".join(masked.lower().split())
        norm_counts[norm] = norm_counts.get(norm, 0) + 1
        eligible.append((idx, chk, meta, skey, masked))

    tier1: list[dict[str, Any]] = []
    tier2_by_source: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for idx, chk, meta, skey, masked in eligible:
        fname = str(meta.get("filename") or "").lower()
        is_tbl = _is_table_bearing_chunk(chk, meta)
        is_small_note = (
            source_chars.get(skey, 0) <= cfg.small_inline_note_max_chars
            and not fname.endswith(".pdf")
        )
        max_len = (
            cfg.small_inline_note_max_chars
            if is_small_note
            else cfg.max_agent_prompt_chunk_chars
        )
        entry = {
            "chunk_id": chk.chunk_id,
            "source_label": meta.get("filename") or chk.document_id,
            "page_number": chk.page_number,
            "text": _truncate_chunk_text(masked, max_len),
        }
        if is_tbl or is_small_note:
            tier1.append(entry)
        else:
            norm = " ".join(masked.lower().split())
            score = _score_prose_chunk(masked, is_duplicate=norm_counts.get(norm, 0) > 1)
            tier2_by_source.setdefault(skey, []).append((-score, idx, entry))

    ordered_tier2: list[dict[str, Any]] = []
    queues = [
        sorted(items, key=lambda item: (item[0], item[1]))
        for items in tier2_by_source.values()
    ]
    pos = 0
    while True:
        added_any = False
        for q in queues:
            if pos < len(q):
                ordered_tier2.append(q[pos][2])
                added_any = True
        if not added_any:
            break
        pos += 1

    packed: list[dict[str, Any]] = []
    used_chars = 0
    for candidate in [*tier1, *ordered_tier2]:
        if len(packed) >= cfg.max_agent_prompt_chunks:
            break
        entry_len = len(json.dumps(candidate, separators=(",", ":"), ensure_ascii=False)) + 1
        if used_chars + entry_len > remaining_chars:
            if packed:
                continue
            break
        packed.append(candidate)
        used_chars += entry_len
    return packed


def build_safe_agent_prompt(
    *,
    base_ontology: GraphAOntology | None,
    evidence_chunks: Sequence[EvidenceChunk],
    tables: Sequence[TableStructure],
    clarification_answers: dict[str, Any],
    fibo_modules: Sequence[str],
    steering_prompt: str | None,
    config: CoreModelingConfig | None = None,
) -> str:
    """Build budgeted 3-persona prompt excluding taint & capping <= 5 PII-masked samples/col."""
    cfg = config or get_core_modeling_config()

    safe_tables: list[dict[str, Any]] = []
    for tbl in tables:
        meta = dict(tbl.metadata or {})
        if meta.get("do_not_send_to_agent") is True:
            continue
        explicit_pii = {str(c).lower() for c in (meta.get("pii_columns") or [])}
        for col_meta in meta.get("columns") or []:
            if isinstance(col_meta, dict) and col_meta.get("pii") is True:
                cname = str(col_meta.get("column_name") or col_meta.get("name") or "").strip()
                if cname:
                    explicit_pii.add(cname.lower())
        headers = list(tbl.headers or [])
        pii_indices = {
            idx
            for idx, h in enumerate(headers)
            if h.lower() in explicit_pii or any(hint in h.lower() for hint in _PII_COL_HINTS)
        }
        capped_rows: list[list[str]] = []
        for row in list(tbl.rows or [])[:MAX_PROMPT_SAMPLE_VALUES_PER_COL]:
            masked_row: list[str] = []
            for idx, val in enumerate(row):
                sval = str(val) if val is not None else ""
                if idx in pii_indices:
                    masked_row.append("<MASKED_PII>")
                else:
                    masked_row.append(_mask_pii_text(sval))
            capped_rows.append(masked_row)
        safe_tables.append(
            {
                "table_id": tbl.table_id,
                "caption": _mask_pii_text(tbl.caption or ""),
                "headers": headers,
                "sample_rows": capped_rows,
            }
        )

    existing_concepts = (
        [c.concept_uri for c in base_ontology.canonical_business_concepts]
        if base_ontology is not None
        else []
    )
    base_version = base_ontology.ontology_version if base_ontology is not None else "0.0.0"
    base_sha256 = compute_ontology_sha256(base_ontology) if base_ontology is not None else ""
    header_text = (
        "Execute @term-scout -> @domain-clarifier -> @ontology-architect to propose a "
        "Graph A OntologyProposal. Use canonical URN prefixes: urn:ontology:entity:<slug> "
        "for concepts, urn:ontology:relationship:<slug> for relationships, and "
        "urn:ontology:enum:<slug> for enumerations. Every concept must have Path A FIBO "
        "(skos:exactMatch/skos:closeMatch >= 0.65) or Path B empirical grounding "
        "(STRUCTURED_SCHEMA / DOCUMENT_OBSERVED with EvidenceRef). "
        f"Use exact base_ontology_version='{base_version}' and "
        f"base_ontology_sha256='{base_sha256}' "
        "(and matching delta.base_ontology_version and delta.parent_sha256).\n"
    )
    payload: dict[str, Any] = {
        "persona_chain": ["@term-scout", "@domain-clarifier", "@ontology-architect"],
        "domain_namespace": base_ontology.domain_namespace if base_ontology else "general",
        "base_ontology_version": base_version,
        "base_ontology_sha256": base_sha256,
        "existing_concepts": existing_concepts,
        "fibo_modules": list(fibo_modules),
        "clarification_answers": clarification_answers,
        "steering_prompt": _mask_pii_text(steering_prompt) if steering_prompt else None,
        "tables": safe_tables,
        "chunks": [],
    }
    base_len = len(header_text) + len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )
    remaining_chars = max(0, cfg.max_agent_prompt_chars - base_len)
    payload["chunks"] = _select_budgeted_chunks(evidence_chunks, cfg, remaining_chars)
    rendered = header_text + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    while len(rendered) > cfg.max_agent_prompt_chars and payload["chunks"]:
        payload["chunks"].pop()
        rendered = header_text + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return rendered
