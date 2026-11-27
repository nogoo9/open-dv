"""Unit tests for M1 Layer-1 Document IR contracts, SourceBlob, PII helpers & ProfileBundle."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import opendv.models as models_pkg
from opendv.grounding.kernel import pii_core
from opendv.models import (
    DOC_SOURCE_KINDS,
    REDACTED_LEAK_RE,
    ChunkRecord,
    DocumentFormat,
    DocumentMap,
    ErrorCode,
    OpenDVError,
    OutlineNode,
    PageMapSpec,
    ParsedDocumentIR,
    ProfileBundle,
    SourceBlob,
    compute_canonical_text_sha256,
    dumps_jcs,
    ensure_no_redacted_placeholder,
    has_redacted_placeholder,
    is_page_scanned_or_low_density,
    mask_pii_text,
    normalize_page_text,
    strip_pii_spans,
)
from opendv.models import document_ir as ir_mod
from opendv.models import pii_tokenizer
from opendv.models.document_ir import (
    DOCUMENT_FORMAT_BY_EXTENSION,
    DocumentBlock,
    DocumentOutlineNode,
    InspectResult,
    NormalizedBBox,
    OcrBox,
    PageSlice,
    PageSpan,
    ParseOptions,
    ParseProvenance,
    ParseSpecResponse,
    ParseWarning,
    ParsingCapabilitiesSnapshot,
    RenderPageResult,
    SpilloverRef,
    ValidationDiagnostic,
    infer_document_format,
    sanitize_source_uri,
)


def test_document_format_constants_and_inference() -> None:
    """DOC_SOURCE_KINDS, extension mapping, and infer_document_format behave deterministically."""
    expected_kinds = frozenset({"pdf", "markdown", "docx", "domain_note"})
    assert expected_kinds == DOC_SOURCE_KINDS
    assert DOCUMENT_FORMAT_BY_EXTENSION == {
        ".pdf": "pdf",
        ".docx": "docx",
        ".md": "markdown",
        ".markdown": "markdown",
        ".txt": "domain_note",
    }
    assert infer_document_format("report.PDF") == "pdf"
    assert infer_document_format(Path("spec.docx")) == "docx"
    assert infer_document_format("notes.md") == "markdown"
    assert infer_document_format("notes.MARKDOWN") == "markdown"
    assert infer_document_format("domain.txt") == "domain_note"
    assert infer_document_format("table.csv") is None


def test_text_normalization_sha256_and_scan_density() -> None:
    """Normalization, SHA-256 hashing, and scan-density predicates match legacy invariants."""
    assert normalize_page_text("a\r\nb\rc\t\td  e") == "a\nb\nc d e"
    sha_crlf = compute_canonical_text_sha256("hello\r\nworld\t\t1")
    sha_lf = compute_canonical_text_sha256("hello\nworld 1")
    assert sha_crlf == sha_lf
    assert sha_crlf == hashlib.sha256(b"hello\nworld 1").hexdigest()

    # Scanned boundary conditions: (<50 & >=1 image) or (<15)
    assert is_page_scanned_or_low_density(49, 1) is True
    assert is_page_scanned_or_low_density(50, 1) is False
    assert is_page_scanned_or_low_density(14, 0) is True
    assert is_page_scanned_or_low_density(15, 0) is False


def test_source_blob_from_path_and_errors(tmp_path: Path) -> None:
    """SourceBlob.from_path hashes files lazily, preserves source, and rejects missing/oversized."""
    missing = tmp_path / "missing.pdf"
    with pytest.raises(OpenDVError) as exc_missing:
        SourceBlob.from_path(missing)
    assert exc_missing.value.error_code == ErrorCode.RESOURCE_NOT_FOUND

    sample = tmp_path / "sample.txt"
    payload = b"hello unstructured world"
    sample.write_bytes(payload)

    with SourceBlob.from_path(sample) as blob:
        assert blob.is_spooled_temp is False
        assert blob.byte_size == len(payload)
        assert blob.binary_sha256 == hashlib.sha256(payload).hexdigest()
        assert blob.filename_hint == "sample.txt"
        assert blob.path == sample
    assert sample.is_file()

    with pytest.raises(OpenDVError) as exc_limit:
        SourceBlob.from_path(sample, max_bytes=len(payload) - 1)
    assert exc_limit.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED


def test_source_blob_from_bytes_lifecycle_and_partial_write_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SourceBlob.from_bytes enforces max_bytes, cleans up on __exit__, and unlinks partial writes."""
    payload = b"%PDF-1.4\nhello"
    with pytest.raises(OpenDVError) as exc_limit:
        SourceBlob.from_bytes(payload, filename_hint="doc.pdf", max_bytes=5)
    assert exc_limit.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED

    with SourceBlob.from_bytes(payload, filename_hint="nested/report.pdf") as blob:
        spooled_path = blob.path
        assert blob.is_spooled_temp is True
        assert spooled_path.is_file()
        assert spooled_path.suffix == ".pdf"
        assert spooled_path.read_bytes() == payload
        assert blob.uri_sanitized == "bytes://report.pdf"
        assert blob.binary_sha256 == hashlib.sha256(payload).hexdigest()
    assert not spooled_path.exists()

    # Simulate an OSError mid-write (partial write) and verify WinError-32-safe cleanup
    created_paths: list[Path] = []
    real_ntf = tempfile.NamedTemporaryFile

    class _FailingSpool:
        def __init__(self, **kwargs: Any) -> None:
            self._inner = real_ntf(dir=tmp_path, **kwargs)
            self.name = self._inner.name
            created_paths.append(Path(self.name))

        def __enter__(self) -> _FailingSpool:
            self._inner.__enter__()
            return self

        def write(self, data: bytes) -> int:
            self._inner.write(data[:4])
            raise OSError("simulated disk full during partial write")

        def flush(self) -> None:
            self._inner.flush()

        def __exit__(self, *args: object) -> None:
            self._inner.__exit__(*args)

    monkeypatch.setattr(ir_mod.tempfile, "NamedTemporaryFile", _FailingSpool)

    with pytest.raises(OpenDVError) as exc_io:
        SourceBlob.from_bytes(payload, filename_hint="fail.pdf")
    assert exc_io.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert len(created_paths) == 1
    assert not created_paths[0].exists()


def test_sanitize_source_uri() -> None:
    """sanitize_source_uri strips userinfo, query, and fragments without breaking Windows paths."""
    assert (
        sanitize_source_uri("https://user:secret@example.com/docs/a.pdf?token=abc#page=2")
        == "https://example.com/docs/a.pdf"
    )
    assert sanitize_source_uri(r"C:\workspace\docs\a.pdf") == r"C:\workspace\docs\a.pdf"


def test_normalized_bbox_clamping_jcs_and_errors() -> None:
    """NormalizedBBox clamps/orders points, eliminates -0.0 for JCS, and rejects invalid dims."""
    bbox = NormalizedBBox.from_points(-10.0, 50.0, 250.0, 10.0, width_pt=200.0, height_pt=100.0)
    assert (bbox.x0, bbox.y0, bbox.x1, bbox.y1) == (0.0, 0.1, 1.0, 0.5)

    neg_zero = NormalizedBBox(x0=-0.0, y0=0.1234567, x1=0.5, y1=0.9876543)
    dumped = dumps_jcs(neg_zero.model_dump())
    assert "-0" not in dumped
    assert neg_zero.y0 == 0.123457
    assert neg_zero.y1 == 0.987654

    with pytest.raises(ValueError, match="width_pt and height_pt"):
        NormalizedBBox.from_points(0.0, 0.0, 10.0, 10.0, width_pt=0.0, height_pt=100.0)
    with pytest.raises(ValueError, match="finite"):
        NormalizedBBox.from_points(float("nan"), 0.0, 10.0, 10.0, width_pt=10.0, height_pt=10.0)
    with pytest.raises(ValidationError):
        NormalizedBBox(x0=0.8, y0=0.1, x1=0.2, y1=0.5)
    with pytest.raises(ValidationError):
        NormalizedBBox(x0=0.1, y0=0.1, x1=0.5, y1=0.5, extra_field=1)  # type: ignore[call-arg]


def test_page_span_and_ir_models_invariants() -> None:
    """PageSpan, DocumentOutlineNode, DocumentBlock, ChunkRecord, and RenderPageResult enforce invariants."""
    with pytest.raises(ValidationError):
        PageSpan(page_number=1, char_start=0, char_end=None)
    with pytest.raises(ValidationError):
        PageSpan(page_number=1, char_start=None, char_end=5)
    with pytest.raises(ValidationError):
        PageSpan(page_number=1, char_start=10, char_end=5)

    span_bbox_only = PageSpan(
        page_number=1,
        bbox=NormalizedBBox(x0=0.1, y0=0.1, x1=0.9, y1=0.2),
    )
    assert span_bbox_only.char_start is None and span_bbox_only.char_end is None

    with pytest.raises(ValidationError):
        DocumentOutlineNode(node_id="n1", title="Bad", level=1, start_page=3, end_page=2)

    child = DocumentOutlineNode(node_id="n2", title="Child", level=2, start_page=1, end_page=1)
    root = DocumentOutlineNode(
        node_id="n1", title="Root", level=1, start_page=1, end_page=2, children=[child]
    )
    assert root.children[0].node_id == "n2"

    with pytest.raises(ValidationError):
        DocumentBlock(block_id="b1", kind="paragraph", reading_order=0, spans=[], text="empty")

    page = PageSlice(
        page_number=1,
        slice_kind="physical_page",
        renderable=True,
        canonical_text="Section 1",
        canonical_text_sha256=compute_canonical_text_sha256("Section 1"),
        raw_text_length=9,
        non_ws_chars=8,
        image_block_count=0,
        needs_ocr=False,
        extracted_via="native_text",
        width_pt=612.0,
        height_pt=792.0,
        rotation_deg=0,
    )
    block = DocumentBlock(
        block_id="b1",
        kind="heading",
        reading_order=0,
        spans=[PageSpan(page_number=1, char_start=0, char_end=9)],
        heading_level=1,
        text="Section 1",
    )
    prov = ParseProvenance(
        parser_id="liteparse",
        parser_version="2.21.3",
        source_uri_sanitized="bytes://test.pdf",
        binary_sha256="a" * 64,
        format_id="pdf",
        page_count=1,
        warnings=[ParseWarning(code="W1", message="minor", page_number=1)],
    )
    ir = ParsedDocumentIR(provenance=prov, pages=[page], outline=[root], blocks=[block])
    assert ParsedDocumentIR.model_validate_json(ir.model_dump_json()) == ir

    chunk = ChunkRecord(
        chunk_id="c1",
        doc_sha256="a" * 64,
        format_id="pdf",
        page_number=1,
        slice_kind="physical_page",
        canonical_text_sha256=page.canonical_text_sha256,
        heading_path=["Root"],
        block_id="b1",
        block_kind="heading",
        reading_order=0,
        text="Section 1",
    )
    chunk_json = json.loads(chunk.model_dump_json())
    assert set(chunk_json.keys()) == {
        "chunk_id",
        "doc_sha256",
        "format_id",
        "page_number",
        "slice_kind",
        "canonical_text_sha256",
        "heading_path",
        "block_id",
        "block_kind",
        "reading_order",
        "text",
        "char_start",
        "char_end",
        "bbox_json",
        "table_json",
        "redacted",
    }
    with pytest.raises(ValidationError):
        ChunkRecord(**{**chunk.model_dump(), "char_start": 1, "char_end": None})

    render_res = RenderPageResult(
        page_number=1,
        width=800,
        height=600,
        dpi=150,
        spillover=SpilloverRef(
            spill_path="out.png",
            byte_size=128,
            sha256="b" * 64,
            format="png",
        ),
    )
    assert "bytes" not in str(RenderPageResult.model_fields)
    assert render_res.spillover is not None and render_res.spillover.spilled is True

    # Remaining DTO sanity checks
    opts = ParseOptions()
    assert opts.max_pages == 1000 and opts.dpi == 150
    box = OcrBox(10, 20, 100, 40, "hello")
    assert (box.x0, box.text) == (10, "hello")
    diag = ValidationDiagnostic(
        severity="error", code="E1", json_pointer="/pages/0", message="bad"
    )
    assert diag.severity == "error"
    inspect_res = InspectResult(
        source_uri_sanitized="bytes://a.pdf",
        binary_sha256="a" * 64,
        byte_size=10,
        format_id="pdf",
        page_count=1,
        digital_pages=[1],
        renderable=True,
    )
    assert inspect_res.scanned_pages == []
    caps = ParsingCapabilitiesSnapshot(
        liteparse_version="2.21.3",
        docx_available=True,
        pillow_version="13.0.0",
        pytesseract_available=False,
        tesseract_binary=None,
    )
    spec = ParseSpecResponse(
        schemas={"ParsedDocumentIR": {}},
        formats=["pdf", "docx", "markdown", "domain_note"],
        projectors=["document_map", "markdown", "chunks"],
        capabilities=caps,
    )
    assert spec.capabilities.docx_available is True


def test_pii_consolidation_and_reexports() -> None:
    """pii_tokenizer owns placeholder/stripping helpers and pii_core re-exports identical objects."""
    assert pii_core.REDACTED_LEAK_RE is pii_tokenizer.REDACTED_LEAK_RE
    assert pii_core.strip_pii_spans is pii_tokenizer.strip_pii_spans
    assert pii_core.has_redacted_placeholder is pii_tokenizer.has_redacted_placeholder
    assert pii_core.ensure_no_redacted_placeholder is pii_tokenizer.ensure_no_redacted_placeholder

    masked = mask_pii_text("Contact alice@example.com or 123-45-6789")
    assert masked == "Contact <MASKED_PII> or <MASKED_PII>"
    assert has_redacted_placeholder(masked) is True
    with pytest.raises(OpenDVError) as exc_leak:
        ensure_no_redacted_placeholder(masked, context="UnitTest")
    assert exc_leak.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK

    stripped = strip_pii_spans("Contact alice@example.com or [REDACTED_SSN]")
    assert stripped == "Contact or"
    assert not has_redacted_placeholder(stripped)


def test_profile_bundle_document_maps_and_models_exports() -> None:
    """ProfileBundle includes document_maps and opendv.models exports expected compatibility APIs."""
    empty_bundle = ProfileBundle()
    assert empty_bundle.document_maps == []

    dmap = DocumentMap(
        doc_id="doc-1",
        filename="guide.pdf",
        format="pdf",
        page_count=1,
        outline=[OutlineNode(title="Intro", page_start=1, level=1)],
        pages=[
            PageMapSpec(
                page_number=1,
                char_count=12,
                has_images=False,
                is_scanned_or_low_density=False,
                ocr_box_count=0,
                canonical_text_sha256=compute_canonical_text_sha256("Hello world!"),
            )
        ],
    )
    bundle = ProfileBundle(document_maps=[dmap])
    assert ProfileBundle.model_validate_json(bundle.model_dump_json()).document_maps == [dmap]

    expected_exports: dict[str, object] = {
        "DocumentFormat": DocumentFormat,
        "ChunkRecord": ChunkRecord,
        "ParsedDocumentIR": ParsedDocumentIR,
        "SourceBlob": SourceBlob,
        "DOC_SOURCE_KINDS": DOC_SOURCE_KINDS,
        "REDACTED_LEAK_RE": REDACTED_LEAK_RE,
        "normalize_page_text": normalize_page_text,
        "compute_canonical_text_sha256": compute_canonical_text_sha256,
        "is_page_scanned_or_low_density": is_page_scanned_or_low_density,
        "ensure_no_redacted_placeholder": ensure_no_redacted_placeholder,
        "has_redacted_placeholder": has_redacted_placeholder,
        "strip_pii_spans": strip_pii_spans,
    }
    for name, obj in expected_exports.items():
        assert hasattr(models_pkg, name)
        assert getattr(models_pkg, name) is obj
