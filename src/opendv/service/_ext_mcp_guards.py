"""Security guards for ExternalMcpGateway: SSRF, stdio env, PII scrubbing, doc_page handles."""

from __future__ import annotations

import base64
import contextlib
import io
import ipaddress
import os
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from opendv.agents.cli_subprocess_base import BASE_OS_ENV_ALLOWLIST
from opendv.models.document_ir import ParseOptions, SourceBlob
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.pii_tokenizer import mask_pii_text
from opendv.models.sources import DocumentMap, ExternalMcpServerSpec
from opendv.parsing.redaction import (
    is_ocr_driver_available,
    mask_page_text_with_aba_tokens,
    redact_png_boxes,
    run_pytesseract_ocr,
)
from opendv.service._mcp_ports import (
    OcrBox,
    OcrProviderFn,
    ProjectStatePort,
)
from opendv.service.parsing_svc import ParsingService

DnsResolverFn = Callable[[str, int], list[str]]

_BLOCKED_SSRF_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::/128"),
)

_ALLOWLISTED_STDIO_ENV: tuple[str, ...] = BASE_OS_ENV_ALLOWLIST


def _ssrf_error(msg: str) -> OpenDVError:
    return OpenDVError(msg, error_code=ErrorCode.DATABRICKS_SSRF_BLOCKED)


def is_blocked_external_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Permit loopback sidecars while blocking private, CGNAT, link-local, and reserved IPs."""
    if (
        isinstance(ip_obj, ipaddress.IPv6Address)
        and ip_obj.ipv4_mapped is not None
        and is_blocked_external_ip(ip_obj.ipv4_mapped)
    ):
        return True
    if ip_obj.is_loopback:
        return False
    if any(ip_obj in net for net in _BLOCKED_SSRF_NETS):
        return True
    return bool(
        ip_obj.is_private
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or ip_obj.is_multicast
    )


def _check_ip_host(host: str, orig_host: str) -> bool:
    with contextlib.suppress(ValueError):
        if is_blocked_external_ip(ipaddress.ip_address(host)):
            raise _ssrf_error(f"External MCP IP '{orig_host}' falls in blocked SSRF CIDR.")
        return True
    with contextlib.suppress(OSError):
        ipaddress.IPv4Address(socket.inet_aton(host))
        raise _ssrf_error(f"Non-canonical IP '{orig_host}' blocked by SSRF guard.")
    return False


def validate_external_mcp_url(
    url: str,
    *,
    dns_resolver: DnsResolverFn | None = None,
) -> str:
    """Validate an external MCP HTTP/HTTPS URL against SSRF rules (loopback allowed)."""
    raw = url.strip()
    if (
        not raw
        or "\\" in raw
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw)
    ):
        raise _ssrf_error(f"External MCP URL '{raw}' blocked by SSRF guard.")

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise _ssrf_error(f"Unsupported external MCP URL scheme '{parsed.scheme}'.")
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or "%" in parsed.netloc
    ):
        raise _ssrf_error(f"External MCP URL '{raw}' cannot contain userinfo/authority '@' or '%'.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise _ssrf_error(f"External MCP URL '{raw}' has invalid port.") from exc
    if port is not None and not (1 <= port <= 65535):
        raise _ssrf_error(f"External MCP URL '{raw}' has invalid port {port}.")

    hostname = (parsed.hostname or "").strip()
    bare_host = hostname.rstrip(".")
    if not bare_host:
        raise _ssrf_error(f"External MCP URL '{raw}' is missing a host.")

    if _check_ip_host(bare_host, hostname):
        return raw

    try:
        ascii_host = bare_host.encode("idna").decode("ascii").rstrip(".")
        if not ascii_host:
            raise UnicodeError("empty IDNA host")
        ascii_host.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise _ssrf_error(f"Invalid IDNA host '{hostname}' blocked by SSRF guard.") from exc

    if _check_ip_host(ascii_host, hostname):
        return raw

    resolved_ips: list[str]
    if dns_resolver is not None:
        resolved_ips = dns_resolver(ascii_host, port or (443 if parsed.scheme == "https" else 80))
    else:
        try:
            infos = socket.getaddrinfo(ascii_host, None)
        except OSError:
            infos = []
        resolved_ips = [
            str(item[4][0]).split("%", 1)[0]
            for item in infos
            if len(item) > 4 and item[4]
        ]

    for addr_text in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(addr_text)
        except ValueError:
            continue
        if is_blocked_external_ip(ip_obj):
            raise _ssrf_error(
                f"External MCP host '{hostname}' resolved to blocked SSRF IP '{addr_text}'."
            )
    return raw


async def reject_redirect_response_hook(response: httpx.Response) -> None:
    """httpx async event hook rejecting any 3xx HTTP redirect."""
    if 300 <= response.status_code < 400:
        raise _ssrf_error(
            f"External MCP endpoint attempted HTTP redirect ({response.status_code}), blocked."
        )


def build_external_mcp_stdio_env(
    secret_values: Mapping[str, str],
    *,
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an allowlisted environment for stdio external MCP child processes."""
    src = os.environ if parent_env is None else parent_env
    child_env: dict[str, str] = {}
    for key in _ALLOWLISTED_STDIO_ENV:
        val = src.get(key)
        if val is not None and val != "":
            child_env[key] = val
    for s_key, s_val in secret_values.items():
        if s_val:
            child_env[s_key] = s_val
    return child_env


def scrub_outbound_arguments(
    value: Any,
    *,
    resolved_handle_ids: set[int] | None = None,
    _exempt_b64: bool = False,
) -> Any:
    """Recursively mask PII in outbound MCP arguments while exempting resolved doc_page b64."""
    if isinstance(value, str):
        return value if _exempt_b64 else mask_pii_text(value)
    if isinstance(value, (list, tuple)):
        return [
            scrub_outbound_arguments(item, resolved_handle_ids=resolved_handle_ids)
            for item in value
        ]
    if isinstance(value, dict):
        is_resolved_doc = resolved_handle_ids is not None and id(value) in resolved_handle_ids
        return {
            str(k): scrub_outbound_arguments(
                v,
                resolved_handle_ids=resolved_handle_ids,
                _exempt_b64=(is_resolved_doc and str(k) == "redacted_png_b64"),
            )
            for k, v in value.items()
        }
    return value


def scrub_inbound_text(text: str) -> str:
    """Scrub PII/secret substrings from inbound external MCP responses."""
    return mask_pii_text(text)


def _fit_png_within_broker_budget(png_bytes: bytes, max_png_bytes: int = 45_000) -> bytes:
    if len(png_bytes) <= max_png_bytes:
        return png_bytes
    with Image.open(io.BytesIO(png_bytes)) as src_img:
        gray = src_img.convert("L")
        out = io.BytesIO()
        gray.save(out, format="PNG", optimize=True)
        if out.tell() <= max_png_bytes:
            return out.getvalue()
        pal = gray.convert("P", palette=Image.Palette.ADAPTIVE, colors=16)
        out2 = io.BytesIO()
        pal.save(out2, format="PNG", optimize=True)
        if out2.tell() <= max_png_bytes:
            return out2.getvalue()

        w, h = gray.size
        scale = 0.75
        min_dim = 32
        while True:
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            if max(new_w, new_h) < min_dim:
                break
            resized = gray.resize((new_w, new_h), Image.Resampling.LANCZOS)
            pal_scaled = resized.convert("P", palette=Image.Palette.ADAPTIVE, colors=16)
            out_scaled = io.BytesIO()
            pal_scaled.save(out_scaled, format="PNG", optimize=True)
            if out_scaled.tell() <= max_png_bytes:
                return out_scaled.getvalue()
            scale *= 0.75
            if max(1, int(w * scale)) == new_w and max(1, int(h * scale)) == new_h:
                break

    raise OpenDVError(
        f"Rendered page PNG cannot fit within {max_png_bytes} byte broker budget "
        "even after minimum downscaling.",
        error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
    )


def run_ocr_on_pdf_bytes(
    pdf_bytes: bytes,
    page_number: int,
    *,
    ocr_provider: OcrProviderFn | None = None,
) -> tuple[list[OcrBox], str, bytes]:
    """Render a PDF page via ParsingService, run OCR if configured/available, and redact PNG."""
    with SourceBlob.from_bytes(pdf_bytes, filename_hint="document.pdf") as blob:
        svc = ParsingService(allowed_roots=[blob.path.parent])
        png_bytes, _ = svc.render_document_page(
            blob,
            page_number,
            sink_class="LOCAL_HUMAN_IO",
            redaction="off",
            approve_scan=True,
            options=ParseOptions(dpi=108),
        )

    boxes: list[OcrBox] = []
    ocr_text = ""
    if ocr_provider is not None:
        boxes, ocr_text = ocr_provider(png_bytes, page_number)
    elif is_ocr_driver_available():
        boxes, ocr_text = run_pytesseract_ocr(png_bytes)

    if boxes:
        png_bytes = redact_png_boxes(png_bytes, boxes)
    png_bytes = _fit_png_within_broker_budget(png_bytes)
    return boxes, ocr_text, png_bytes


def _is_unredacted_ocr_allowed(
    state_store: ProjectStatePort,
    project_id: str,
    rec: Mapping[str, Any],
    doc_id: str,
    page_number: int,
) -> bool:
    if bool(rec.get("allow_unredacted_external_ocr")):
        return True
    cfg = rec.get("connector_config")
    opts = getattr(cfg, "options", None) if cfg is not None else None
    if isinstance(opts, Mapping) and bool(opts.get("allow_unredacted_external_ocr")):
        return True
    for ev in state_store.list_events_or_receipts(project_id, record_kind="audit_log"):
        payload = ev.get("payload", ev.get("payload_json", {}))
        if not isinstance(payload, Mapping):
            continue
        if payload.get("event_type") == "SCAN_APPROVED":
            details = payload.get("details", {})
            if (
                isinstance(details, Mapping)
                and details.get("doc_id") == doc_id
                and bool(details.get("allow_unredacted_external_ocr"))
            ):
                pages_str = str(details.get("pages", ""))
                allowed_pages = {p.strip() for p in pages_str.split(",") if p.strip()}
                if bool(allowed_pages) and str(page_number) in allowed_pages:
                    return True
    return False


def resolve_doc_page_handles(
    arguments: dict[str, Any],
    *,
    project_id: str,
    server: ExternalMcpServerSpec,
    state_store: ProjectStatePort,
    source_path_resolver: Callable[[str, str], Path | None] | None = None,
    ocr_provider: OcrProviderFn | None = None,
) -> tuple[dict[str, Any], tuple[str | None, int | None], set[int]]:
    """Resolve any {"$opendv_handle": "doc_page"} objects after gating opt-in and scan consent."""
    doc_meta: list[tuple[str, int]] = []
    resolved_handle_ids: set[int] = set()

    def _walk(node: Any) -> Any:
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if node.get("$opendv_handle") == "doc_page":
            if not server.allow_document_pages:
                raise OpenDVError(
                    f"External MCP server '{server.server_id}' requires allow_document_pages=True.",
                    error_code=ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED,
                )
            doc_id = str(node.get("doc_id") or "")
            raw_page = node.get("page_number")
            if (
                not doc_id
                or isinstance(raw_page, bool)
                or not isinstance(raw_page, int)
                or raw_page < 1
            ):
                raise OpenDVError(
                    "Invalid doc_page handle: requires doc_id and page_number >= 1.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
            rec = state_store.get_source_record(project_id, doc_id)
            if rec.get("status") == "METADATA_ONLY_BINARY_ABSENT":
                raise OpenDVError(
                    f"Source binary absent for '{doc_id}'.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )
            dm_raw = rec.get("document_map")
            if dm_raw is None:
                msg = f"Source '{doc_id}' does not have a DocumentMap."
                raise OpenDVError(msg, error_code=ErrorCode.RESOURCE_NOT_FOUND)
            dm = dm_raw if isinstance(dm_raw, DocumentMap) else DocumentMap.model_validate(dm_raw)
            page_spec = next((p for p in dm.pages if p.page_number == raw_page), None)
            if page_spec is None:
                raise OpenDVError(
                    f"Page {raw_page} not found in document '{doc_id}'.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
            approved = set(rec.get("approved_scan_pages") or dm.approved_scan_pages or ())
            src_path = (
                source_path_resolver(project_id, doc_id)
                if source_path_resolver is not None
                else None
            )
            if src_path is None:
                uri = str(rec.get("uri_or_filename") or "")
                candidate = Path(uri)
                src_path = candidate if candidate.is_file() else None
            if src_path is None or not src_path.is_file():
                raise OpenDVError(
                    f"Source file for '{doc_id}' is missing on disk.",
                    error_code=ErrorCode.SOURCE_BINARY_ABSENT,
                )

            png_b64: str | None = None
            masked_text = ""
            psvc = ParsingService(allowed_roots=[src_path.parent])
            if dm.format == "pdf":
                ocr_text = ""
                if page_spec.is_scanned_or_low_density:
                    if raw_page not in approved:
                        raise OpenDVError(
                            f"Scanned page {raw_page} of '{doc_id}' is not in approved_scan_pages.",
                            error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
                        )
                    pdf_bytes = src_path.read_bytes()
                    boxes, ocr_text, png_bytes = run_ocr_on_pdf_bytes(
                        pdf_bytes, raw_page, ocr_provider=ocr_provider
                    )
                    if len(boxes) < 1 and not _is_unredacted_ocr_allowed(
                        state_store, project_id, rec, doc_id, raw_page
                    ):
                        raise OpenDVError(
                            f"Scanned page {raw_page} of '{doc_id}' lacks OCR boxes and "
                            "allow_unredacted_external_ocr=True.",
                            error_code=ErrorCode.UNREDACTED_SCAN_BLOCKED,
                        )
                else:
                    png_bytes, _ = psvc.render_document_page(
                        src_path,
                        raw_page,
                        sink_class="MODEL_EGRESS",
                        redaction="on",
                        options=ParseOptions(dpi=108),
                    )
                    png_bytes = _fit_png_within_broker_budget(png_bytes)
                png_b64 = base64.b64encode(png_bytes).decode("ascii")
                ir = psvc.parse_document(
                    src_path,
                    options=ParseOptions(pages=[raw_page]),
                    sink_class="MODEL_EGRESS",
                    redaction="on",
                )
                red_txt = next(
                    (pg.canonical_text for pg in ir.pages if pg.page_number == raw_page), ""
                )
                if ocr_text:
                    masked_ocr = mask_page_text_with_aba_tokens(ocr_text)
                    combined = f"{red_txt}\n{masked_ocr}".strip() if red_txt else masked_ocr
                    masked_text = mask_page_text_with_aba_tokens(combined)
                else:
                    masked_text = red_txt
            else:
                ir = psvc.parse_document(
                    src_path,
                    sink_class="MODEL_EGRESS",
                    redaction="on",
                )
                masked_text = next(
                    (pg.canonical_text for pg in ir.pages if pg.page_number == raw_page), ""
                )

            doc_meta.append((doc_id, raw_page))
            resolved_obj: dict[str, Any] = {
                "doc_id": doc_id,
                "page_number": raw_page,
                "redacted_png_b64": png_b64,
                "masked_text": masked_text,
            }
            resolved_handle_ids.add(id(resolved_obj))
            return resolved_obj
        return {str(k): _walk(v) for k, v in node.items()}

    resolved = _walk(arguments)
    first_doc = doc_meta[0] if doc_meta else (None, None)
    return resolved, first_doc, resolved_handle_ids


__all__ = [
    "DnsResolverFn",
    "build_external_mcp_stdio_env",
    "is_blocked_external_ip",
    "reject_redirect_response_hook",
    "resolve_doc_page_handles",
    "run_ocr_on_pdf_bytes",
    "scrub_inbound_text",
    "scrub_outbound_arguments",
    "validate_external_mcp_url",
]
