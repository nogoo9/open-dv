"""Headless LibreOffice Conversion Document Adapter (Task 4B.4 / Issue #204).

Converts Microsoft Office and OpenDocument formats (.docx, .pptx, .odt, .xlsx, .doc, .ppt, .xls)
to PDF using headless LibreOffice (`soffice`) in an isolated user installation profile,
then delegates to LiteParseDocumentAdapter for high-performance layout and text extraction.
When `soffice` is missing from the host system, raises typed OfficeConversionUnavailableError
(HTTP 501 / exit 1).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ingestion import (
    IngestionResult,
    TaintClassification,
)
from opendv_sources.adapters.liteparse_adapter import (
    InvalidDocumentError,
    LiteParseDocumentAdapter,
)
from opendv_sources.exceptions import (
    DocumentExtractionError,
    OfficeConversionUnavailableError,
)
from opendv_sources.ingestion.adapter import BaseDocumentAdapter

logger = logging.getLogger(__name__)

_OFFICE_EXTENSION_MIME_MAP: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".doc": "application/msword",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xls": "application/vnd.ms-excel",
}


def find_soffice_binary(custom_path: str | None = None) -> str | None:
    """Locate the LibreOffice `soffice` binary on PATH or standard install directories.

    Args:
        custom_path: Optional explicit binary path provided by the caller or configuration.

    Returns:
        Full path to executable if found and executable, else None.
    """
    if custom_path:
        path_obj = Path(custom_path)
        if path_obj.is_file() and os.access(path_obj, os.X_OK):
            return str(path_obj.resolve())
        # On Windows, is_file is sufficient even if X_OK isn't strictly POSIX
        if path_obj.is_file() and os.name == "nt":
            return str(path_obj.resolve())

    # Check environment variables
    env_soffice = os.environ.get("SOFFICE_PATH") or os.environ.get("LIBREOFFICE_PATH")
    if env_soffice:
        env_path = Path(env_soffice)
        if env_path.is_file():
            return str(env_path.resolve())

    # Check PATH via shutil.which
    for bin_name in ("soffice", "libreoffice", "soffice.exe", "libreoffice.exe"):
        which_path = shutil.which(bin_name)
        if which_path:
            return which_path

    return None


class LibreOfficeDocumentAdapter(BaseDocumentAdapter):
    """Adapter converting Office documents to PDF via headless LibreOffice,
    then parsing via LiteParseDocumentAdapter.
    """

    supported_mime_types: Sequence[str] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/msword",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-excel",
    )
    supported_extensions: Sequence[str] = (
        ".docx",
        ".pptx",
        ".odt",
        ".xlsx",
        ".doc",
        ".ppt",
        ".xls",
        ".odp",
        ".ods",
    )

    def __init__(
        self,
        *,
        soffice_path: str | None = None,
        timeout_seconds: float = 60.0,
        pdf_adapter: LiteParseDocumentAdapter | None = None,
        **liteparse_kwargs: Any,
    ) -> None:
        """Initialize LibreOfficeDocumentAdapter.

        Args:
            soffice_path: Optional explicit path to the `soffice` executable.
            timeout_seconds: Subprocess timeout in seconds for conversion.
            pdf_adapter: Optional pre-configured LiteParseDocumentAdapter instance.
            **liteparse_kwargs: Keyword arguments passed to fallback LiteParseDocumentAdapter.
        """
        self.soffice_path = soffice_path
        self.timeout_seconds = timeout_seconds
        self.pdf_adapter = pdf_adapter or LiteParseDocumentAdapter(**liteparse_kwargs)

    @property
    def adapter_name(self) -> str:
        return "libreoffice"

    def is_available(self) -> bool:
        """Check if LibreOffice binary is available on the system."""
        return find_soffice_binary(self.soffice_path) is not None

    def parse_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        """Convert raw office document bytes to PDF and parse via LiteParseDocumentAdapter.

        Args:
            data: Raw office file bytes (.docx, .pptx, .odt, etc.).
            filename: Original document filename.
            mime_type: Optional explicit MIME media type.
            **kwargs: Extra parameters including source_sha256, document_id, taint.

        Returns:
            Structured IngestionResult from intermediate PDF parsing.

        Raises:
            InvalidDocumentError: If document bytes are empty.
            OfficeConversionUnavailableError: If LibreOffice `soffice` binary is missing (HTTP 501).
            DocumentExtractionError: If conversion fails or outputs corrupted intermediate PDF.
        """
        if not data:
            raise InvalidDocumentError(f"Office document '{filename}' is empty (0 bytes)")

        actual_sha256 = hashlib.sha256(data).hexdigest()
        caller_sha256 = kwargs.pop("source_sha256", None)
        if caller_sha256 and caller_sha256 != actual_sha256:
            raise DocumentExtractionError(
                f"Supplied source_sha256 '{caller_sha256}' does not match computed "
                f"digest '{actual_sha256}' for '{filename}'"
            )

        soffice_bin = find_soffice_binary(self.soffice_path)
        if not soffice_bin:
            raise OfficeConversionUnavailableError(
                "OFFICE_CONVERSION_UNAVAILABLE",
                details={"filename": filename, "requested": mime_type or filename},
            )

        ext = Path(filename).suffix.lower()
        default_mime = _OFFICE_EXTENSION_MIME_MAP.get(ext, "application/octet-stream")
        resolved_mime = mime_type or default_mime

        doc_id = str(kwargs.get("document_id") or f"doc_{actual_sha256[:16]}")
        taint_raw = kwargs.get("taint", TaintClassification.INTERNAL)
        taint = TaintClassification(taint_raw) if taint_raw else TaintClassification.INTERNAL

        # Execute headless conversion in an isolated temporary profile
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            profile_dir = tmp_dir / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            profile_uri = profile_dir.as_uri()

            out_dir = tmp_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            safe_stem = re.sub(r"[^\w\-.]", "_", Path(filename).stem) or "document"
            safe_filename = f"{safe_stem}_{secrets.token_hex(4)}{ext}"
            input_path = tmp_dir / safe_filename
            input_path.write_bytes(data)

            cmd = [
                soffice_bin,
                "--headless",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(input_path),
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise OfficeConversionUnavailableError(
                    "OFFICE_CONVERSION_UNAVAILABLE",
                    details={"error": str(exc), "binary": soffice_bin},
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise DocumentExtractionError(
                    f"LibreOffice conversion timed out after {self.timeout_seconds}s for "
                    f"'{filename}'"
                ) from exc

            if proc.returncode != 0:
                stderr_msg = proc.stderr.decode("utf-8", errors="replace").strip()
                raise DocumentExtractionError(
                    f"LibreOffice conversion failed on '{filename}' with exit "
                    f"code {proc.returncode}: {stderr_msg}"
                )

            # Locate the generated PDF
            expected_pdf = out_dir / f"{input_path.stem}.pdf"
            if not expected_pdf.is_file():
                # Search for any PDF generated in out_dir
                generated_pdfs = list(out_dir.glob("*.pdf"))
                if generated_pdfs:
                    expected_pdf = generated_pdfs[0]
                else:
                    raise DocumentExtractionError(
                        f"LibreOffice conversion completed successfully but output PDF was not "
                        f"found in '{out_dir}'"
                    )

            pdf_bytes = expected_pdf.read_bytes()
            if not pdf_bytes:
                raise DocumentExtractionError(
                    f"LibreOffice produced an empty PDF (0 bytes) for '{filename}'"
                )

            # Delegate to LiteParseDocumentAdapter
            # Pass kwargs without source_sha256 so LiteParse parses the intermediate PDF bytes
            liteparse_kwargs = dict(kwargs)
            liteparse_kwargs["document_id"] = doc_id
            liteparse_kwargs["taint"] = taint

            pdf_result = self.pdf_adapter.parse_bytes(
                pdf_bytes,
                filename=f"{Path(filename).stem}.pdf",
                mime_type="application/pdf",
                **liteparse_kwargs,
            )

            # Re-ground result to original office document provenance
            metadata = dict(pdf_result.metadata)
            metadata.update(
                {
                    "office_converted": True,
                    "conversion_engine": "libreoffice",
                    "original_filename": filename,
                    "original_mime_type": resolved_mime,
                    "intermediate_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                }
            )

            return IngestionResult(
                document_id=doc_id,
                filename=filename,
                mime_type=resolved_mime,
                total_pages=pdf_result.total_pages,
                total_characters=pdf_result.total_characters,
                chunks=pdf_result.chunks,
                page_map=pdf_result.page_map,
                tables=pdf_result.tables,
                taint=taint,
                source_sha256=actual_sha256,
                normalized_text=pdf_result.normalized_text,
                metadata=metadata,
            )


__all__ = [
    "LibreOfficeDocumentAdapter",
    "find_soffice_binary",
]
