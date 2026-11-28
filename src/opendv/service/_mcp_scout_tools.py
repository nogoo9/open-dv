"""Step 4 Scout (5 read-only evidence tools) implementations for JobMcpBroker."""

from __future__ import annotations

import base64
import hashlib
import uuid
from typing import TYPE_CHECKING, Any

from opendv.models.document_ir import ParseOptions, SourceBlob, normalize_page_text
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import OcrPageReceipt
from opendv.models.sources import (
    DocumentMap,
    PageTextView,
    SourceProfile,
    TableIndexEntry,
    TableProfile,
)
from opendv.models.wire_dtos_discovery import ExternalMcpToolResult, ImageOcrResult
from opendv.parsing.redaction import mask_page_text_with_aba_tokens
from opendv.service._ext_mcp_guards import run_ocr_on_pdf_bytes
from opendv.service.parsing_svc import ParsingService
from opendv.sources.pii import mask_cell_value

if TYPE_CHECKING:
    from pathlib import Path

    from opendv.service._mcp_tools import BrokerToolContext


def _mask_table_profile(tp: TableProfile) -> TableProfile:
    masked_cols = []
    for col in tp.columns:
        samples = [
            mask_cell_value(v, col_name=col.col_name, column_pii_tags=col.pii_tags)
            for v in col.sample_values[:5]
        ]
        masked_cols.append(col.model_copy(update={"sample_values": samples, "shape_stats": None}))
    return tp.model_copy(update={"columns": masked_cols})


def scout_inspect_table_cluster(ctx: BrokerToolContext, cluster_id: str) -> list[TableProfile]:
    """Return masked TableProfiles belonging to cluster_id."""
    ctx.budget_tracker.check_and_consume(
        {"cluster_id": cluster_id}, tool_name="inspect_table_cluster"
    )
    out: list[TableProfile] = []
    for src in ctx.state_store.list_sources(ctx.project_id):
        rec = ctx.state_store.get_source_record(ctx.project_id, src.source_id)
        t_idx = [
            t if isinstance(t, TableIndexEntry) else TableIndexEntry.model_validate(t)
            for t in (rec.get("tables_index") or [])
        ]
        wanted_tids = {t.table_id for t in t_idx if t.cluster_id == cluster_id}
        if not wanted_tids:
            continue
        prof_raw = rec.get("profile")
        prof = (
            prof_raw
            if isinstance(prof_raw, SourceProfile)
            else SourceProfile.model_validate(prof_raw)
        )
        for tbl in prof.tables:
            if tbl.table_id in wanted_tids:
                out.append(_mask_table_profile(tbl))
    ctx.budget_tracker.check_payload_size(
        [t.model_dump(mode="json") for t in out], label="inspect_table_cluster result"
    )
    return out


def scout_inspect_table_profile(ctx: BrokerToolContext, table_id: str) -> TableProfile:
    """Return a single masked TableProfile (<= 5 sample values/column)."""
    ctx.budget_tracker.check_and_consume({"table_id": table_id}, tool_name="inspect_table_profile")
    for src in ctx.state_store.list_sources(ctx.project_id):
        rec = ctx.state_store.get_source_record(ctx.project_id, src.source_id)
        prof_raw = rec.get("profile")
        prof = (
            prof_raw
            if isinstance(prof_raw, SourceProfile)
            else SourceProfile.model_validate(prof_raw)
        )
        for tbl in prof.tables:
            if tbl.table_id == table_id:
                masked = _mask_table_profile(tbl)
                ctx.budget_tracker.check_payload_size(
                    masked.model_dump(mode="json"), label="inspect_table_profile result"
                )
                return masked
    raise OpenDVError(
        f"Table '{table_id}' not found in project '{ctx.project_id}'.",
        error_code=ErrorCode.RESOURCE_NOT_FOUND,
    )


def _load_doc_record_and_path(
    ctx: BrokerToolContext, doc_id: str
) -> tuple[dict[str, Any], DocumentMap, Path | None]:
    from pathlib import Path

    rec = ctx.state_store.get_source_record(ctx.project_id, doc_id)
    if rec.get("status") == "METADATA_ONLY_BINARY_ABSENT":
        raise OpenDVError(
            f"Source binary absent for '{doc_id}'.",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )
    dm_raw = rec.get("document_map")
    if dm_raw is None:
        raise OpenDVError(
            f"Source '{doc_id}' does not have a DocumentMap.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    dm = dm_raw if isinstance(dm_raw, DocumentMap) else DocumentMap.model_validate(dm_raw)
    src_path = (
        ctx.source_path_resolver(ctx.project_id, doc_id)
        if ctx.source_path_resolver is not None
        else None
    )
    if src_path is None:
        uri = str(rec.get("uri_or_filename") or "")
        cand = Path(uri)
        src_path = cand if cand.is_file() else None
    return rec, dm, src_path


def scout_read_document_pages(
    ctx: BrokerToolContext, doc_id: str, page_start: int, page_end: int
) -> list[PageTextView]:
    """Return masked PageTextView objects for doc_id[page_start..page_end]."""
    ctx.budget_tracker.check_and_consume(
        {"doc_id": doc_id, "page_start": page_start, "page_end": page_end},
        tool_name="read_document_pages",
    )
    if page_start < 1 or page_end < page_start:
        raise OpenDVError(
            f"Invalid page range [{page_start}, {page_end}].",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    rec, dm, src_path = _load_doc_record_and_path(ctx, doc_id)
    approved = set(rec.get("approved_scan_pages") or dm.approved_scan_pages or ())
    end = min(page_end, dm.page_count)
    target_nums = [p.page_number for p in dm.pages if page_start <= p.page_number <= end]

    page_map = ctx.parsed_pages.setdefault(doc_id, {})
    masked_store = getattr(ctx, "masked_doc_pages", None)
    masked_map = masked_store.setdefault(doc_id, {}) if isinstance(masked_store, dict) else {}
    missing = [n for n in target_nums if n not in page_map]
    is_pdf = dm.format == "pdf"
    if missing:
        if src_path is None or not src_path.is_file():
            raise OpenDVError(
                f"Source file for '{doc_id}' is missing on disk.",
                error_code=ErrorCode.SOURCE_BINARY_ABSENT,
            )
        psvc = ctx.parsing_service or ParsingService(allowed_roots=[src_path.parent])
        parse_opts = ParseOptions(pages=missing) if is_pdf else None
        ir = (
            ctx.parsing_service.parse_document(
                SourceBlob.from_path(src_path),
                options=parse_opts,
                sink_class="LOCAL_HUMAN_IO",
                redaction="off",
            )
            if ctx.parsing_service is not None
            else psvc.parse_document(
                src_path,
                options=parse_opts,
                sink_class="LOCAL_HUMAN_IO",
                redaction="off",
            )
        )
        for pg in ir.pages:
            page_map[pg.page_number] = (
                "" if (pg.needs_ocr and pg.page_number not in approved) else pg.canonical_text
            )
        proj = psvc.export_ir(
            ir,
            target="document_map",
            sink_class="MODEL_EGRESS",
            redaction="on",
            doc_id=dm.doc_id,
            filename=dm.filename,
            approved_scan_pages=sorted(approved),
        )
        assert isinstance(proj, dict)
        for v in proj["page_text_views"]:
            masked_map[v.page_number] = (
                "" if (v.is_scanned and not v.scan_approved) else v.masked_text
            )
        if dm.doc_id != doc_id:
            ctx.parsed_pages.setdefault(dm.doc_id, {}).update(page_map)
            if isinstance(masked_store, dict):
                masked_store.setdefault(dm.doc_id, {}).update(masked_map)

    missing_masked = [n for n in target_nums if n not in masked_map]
    if missing_masked:
        if src_path is not None and src_path.is_file():
            psvc = ctx.parsing_service or ParsingService(allowed_roots=[src_path.parent])
            parse_opts = ParseOptions(pages=missing_masked) if is_pdf else None
            ir_red = (
                ctx.parsing_service.parse_document(
                    SourceBlob.from_path(src_path),
                    options=parse_opts,
                    sink_class="MODEL_EGRESS",
                    redaction="on",
                )
                if ctx.parsing_service is not None
                else psvc.parse_document(
                    src_path,
                    options=parse_opts,
                    sink_class="MODEL_EGRESS",
                    redaction="on",
                )
            )
            for pg in ir_red.pages:
                masked_map[pg.page_number] = (
                    "" if (pg.needs_ocr and pg.page_number not in approved) else pg.canonical_text
                )
        else:
            for n in missing_masked:
                masked_map[n] = mask_page_text_with_aba_tokens(page_map.get(n, ""))
        if dm.doc_id != doc_id and isinstance(masked_store, dict):
            masked_store.setdefault(dm.doc_id, {}).update(masked_map)

    views = [
        PageTextView(
            doc_id=dm.doc_id,
            page_number=p.page_number,
            masked_text=(
                ""
                if (p.is_scanned_or_low_density and p.page_number not in approved)
                else masked_map.get(p.page_number, "")
            ),
            is_scanned=p.is_scanned_or_low_density,
            scan_approved=p.page_number in approved,
        )
        for p in dm.pages
        if page_start <= p.page_number <= end
    ]
    ctx.budget_tracker.check_payload_size(
        [v.model_dump(mode="json") for v in views], label="read_document_pages result"
    )
    return views


def scout_view_page_image(
    ctx: BrokerToolContext, doc_id: str, page_number: int
) -> ImageOcrResult:
    """Gate scanned page approval + OCR boxes, redact PNG, and persist OcrPageReceipt."""
    ctx.budget_tracker.check_and_consume(
        {"doc_id": doc_id, "page_number": page_number}, tool_name="view_page_image"
    )
    rec, dm, src_path = _load_doc_record_and_path(ctx, doc_id)
    page_spec = next((p for p in dm.pages if p.page_number == page_number), None)
    if page_spec is None:
        raise OpenDVError(
            f"Page {page_number} not found in '{doc_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    if not page_spec.is_scanned_or_low_density:
        raise OpenDVError(
            f"Page {page_number} of '{doc_id}' is text-dense; use read_document_pages.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    approved = set(rec.get("approved_scan_pages") or dm.approved_scan_pages or ())
    if page_number not in approved:
        raise OpenDVError(
            f"Scanned page {page_number} of '{doc_id}' is not in approved_scan_pages.",
            error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
        )
    if src_path is None or not src_path.is_file():
        raise OpenDVError(
            f"Source binary absent for '{doc_id}'.",
            error_code=ErrorCode.SOURCE_BINARY_ABSENT,
        )

    pdf_bytes = src_path.read_bytes()
    boxes, raw_ocr_text, png_bytes = run_ocr_on_pdf_bytes(
        pdf_bytes, page_number, ocr_provider=ctx.ocr_provider
    )
    if len(boxes) < 1:
        raise OpenDVError(
            f"Scanned page {page_number} of '{doc_id}' has ocr_box_count=0; blocked.",
            error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
        )

    masked_ocr = mask_page_text_with_aba_tokens(normalize_page_text(raw_ocr_text))
    content_sha = hashlib.sha256(normalize_page_text(masked_ocr).encode("utf-8")).hexdigest()
    receipt_id = f"ocrr-{uuid.uuid4().hex[:12]}"
    receipt = OcrPageReceipt(
        receipt_id=receipt_id,
        doc_id=doc_id,
        page_number=page_number,
        ocr_text_masked=masked_ocr,
        content_sha256=content_sha,
        ocr_box_count=len(boxes),
    )
    ctx.state_store.append_event_or_receipt(
        ctx.project_id,
        record_kind="ocr_receipt",
        run_id=ctx.run_id,
        payload=receipt,
    )
    res = ImageOcrResult(
        redacted_png_b64=base64.b64encode(png_bytes).decode("ascii"),
        ocr_text=masked_ocr,
        ocr_box_count=len(boxes),
        receipt_id=receipt_id,
        redaction_applied=True,
    )
    ctx.budget_tracker.check_payload_size(
        res.model_dump(mode="json"), label="view_page_image result"
    )
    return res


async def scout_call_external_mcp(
    ctx: BrokerToolContext,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> ExternalMcpToolResult:
    """Delegate scout external MCP invocation through ExternalMcpGatewayPort."""
    ctx.budget_tracker.check_and_consume(
        {"server_id": server_id, "tool_name": tool_name, "arguments": arguments},
        tool_name="call_external_mcp",
    )
    if ctx.external_gateway is None:
        raise OpenDVError(
            "ExternalMcpGateway is not configured on JobMcpBroker.",
            error_code=ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR,
            run_id=ctx.run_id,
        )
    res = await ctx.external_gateway.invoke_tool(
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        server_id=server_id,
        tool_name=tool_name,
        arguments=arguments,
        is_scout_call=True,
        stage="step4_discovery",
    )
    ctx.budget_tracker.check_payload_size(
        res.model_dump(mode="json"), label="call_external_mcp result"
    )
    return res


__all__ = [
    "scout_call_external_mcp",
    "scout_inspect_table_cluster",
    "scout_inspect_table_profile",
    "scout_read_document_pages",
    "scout_view_page_image",
]
