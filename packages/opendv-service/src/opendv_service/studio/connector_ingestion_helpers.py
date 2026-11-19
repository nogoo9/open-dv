"""Shared helpers for multi-connector ingestion, path containment, CAS staging, and profiling."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Final

import duckdb
import pyarrow as pa

from opendv_contracts.dtos.ingestion import TableStructure, TaintClassification
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.config import CREDENTIALS_REF_RE, FORBIDDEN_SECRET_KEYS
from opendv_core.paths import PathContainmentError, resolve_workspace_path
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_sources.fibo.loader import get_default_fibo_loader

SAMPLE_ROW_CAP: Final[int] = 50
MAX_CONNECTOR_BLOB_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MiB
HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{64}$")
_FORBIDDEN_INTERNAL_PARTS: Final[frozenset[str]] = frozenset({".opendv", ".git"})
_MAGIC_EXT_MAP: Final[dict[bytes, str]] = {
    b"SQLite format 3\x00": "sqlite",
    b"PAR1": "parquet",
}


def validate_connector_payload(payload: dict[str, Any]) -> None:
    """Reject inline secret fields and enforce credentials_ref syntax."""

    def _scan(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).lower().strip() in FORBIDDEN_SECRET_KEYS and v not in (None, ""):
                    raise OpenDVError(
                        f"Raw secret field '{k}' is forbidden; use credentials_ref ('env:...' or 'secret:...').",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    )
                _scan(v)
        elif isinstance(obj, list):
            for item in obj:
                _scan(item)

    _scan(payload)
    creds = payload.get("credentials_ref")
    if (
        creds is not None
        and str(creds).strip() != ""
        and not CREDENTIALS_REF_RE.match(str(creds).strip())
    ):
        raise OpenDVError(
            f"Invalid credentials_ref '{creds}'; expected 'env:<NAME>' or 'secret:<NAME>'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )


def strip_file_uri(raw_uri: str) -> str:
    """Normalize local file:// URI to a plain path string."""
    uri = raw_uri.strip()
    if uri.lower().startswith("file://"):
        uri = uri[7:]
        if uri.startswith("/") and len(uri) > 2 and uri[2] == ":":
            uri = uri[1:]
    return uri


def resolve_connector_local_path(session_manager: StudioSessionManager, raw_uri: str) -> Path:
    """Resolve and strictly contain a local connector path inside workspace_root."""
    cleaned = strip_file_uri(raw_uri)
    if not cleaned:
        raise OpenDVError("Connector 'uri' is required.", error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)

    raw_parts = {part.lower() for part in Path(cleaned).parts}
    if raw_parts & _FORBIDDEN_INTERNAL_PARTS:
        raise PathContainmentError(
            f"Connector path '{raw_uri}' targets forbidden internal directory."
        )

    ws_root = getattr(session_manager, "workspace_root", None)
    if ws_root is None:
        raise PathContainmentError(
            "workspace_root is required for local connector ingestion."
        )

    resolved = resolve_workspace_path(cleaned, workspace_root=Path(ws_root), allow_temp=False)
    resolved_parts = {part.lower() for part in resolved.parts}
    if resolved_parts & _FORBIDDEN_INTERNAL_PARTS:
        raise PathContainmentError(
            f"Resolved connector path '{resolved}' targets forbidden internal directory."
        )
    if not resolved.is_file():
        raise OpenDVError(
            f"Connector source file not found: '{raw_uri}'.", error_code=ErrorCode.SOURCE_NOT_FOUND
        )
    size_bytes = resolved.stat().st_size
    if size_bytes <= 0:
        raise OpenDVError(
            f"Connector source file is empty: '{raw_uri}'.", error_code=ErrorCode.CORRUPT_SOURCE_BLOB
        )
    if size_bytes > MAX_CONNECTOR_BLOB_BYTES:
        raise OpenDVError(
            f"Connector source exceeds max size ({MAX_CONNECTOR_BLOB_BYTES} bytes).",
            error_code=ErrorCode.INVALID_REQUEST,
        )
    return resolved


def infer_cas_extension(raw_bytes: bytes, hint_name: str, adapter: str) -> str:
    """Infer file extension from magic bytes or filename/adapter hint."""
    if adapter in {"", "file", "duckdb", "sqlite"}:
        for magic, ext in _MAGIC_EXT_MAP.items():
            if raw_bytes.startswith(magic):
                return ext
        if raw_bytes[:4] == b"PK\x03\x04":
            return "xlsx"
        if len(raw_bytes) >= 12 and raw_bytes[8:12] == b"DUCK":
            return "duckdb"
    suffix = Path(strip_file_uri(hint_name)).suffix.lower().lstrip(".")
    if suffix:
        return suffix
    return adapter if adapter in {"sqlite", "duckdb", "csv", "tsv", "parquet", "xlsx", "jsonl"} else ""


def resolve_cas_connector_bytes(
    *,
    cas_hash: str,
    session_mgr: StudioSessionManager,
    session_id: str,
    platform_cas: Any | None = None,
) -> tuple[bytes, str]:
    """Load, verify size/digest, and return bytes + canonical lowercase sha256 from CAS."""
    clean_hash = cas_hash.strip()
    if not HEX_64_RE.match(clean_hash):
        raise OpenDVError(
            f"Invalid cas_hash '{cas_hash}'. Expected 64-char SHA-256 hex digest.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    lower_hash = clean_hash.lower()
    raw_bytes: bytes | None = None

    session_cas = session_mgr.get_cas(session_id)
    if hasattr(session_cas, "has") and session_cas.has(lower_hash):
        raw_bytes = session_cas.get(lower_hash)
    elif platform_cas is not None:
        if hasattr(platform_cas, "get_bytes"):
            with contextlib.suppress(FileNotFoundError, KeyError):
                raw_bytes = platform_cas.get_bytes(clean_hash)
        elif hasattr(platform_cas, "get"):
            with contextlib.suppress(FileNotFoundError, KeyError):
                raw_bytes = platform_cas.get(clean_hash)

    if raw_bytes is None:
        raise OpenDVError(
            f"CAS blob '{lower_hash}' not found.", error_code=ErrorCode.SOURCE_NOT_FOUND
        )
    if len(raw_bytes) == 0:
        raise OpenDVError(
            f"CAS blob '{lower_hash}' is empty.", error_code=ErrorCode.CORRUPT_SOURCE_BLOB
        )
    if len(raw_bytes) > MAX_CONNECTOR_BLOB_BYTES:
        raise OpenDVError(
            f"CAS blob exceeds maximum size ({MAX_CONNECTOR_BLOB_BYTES} bytes).",
            error_code=ErrorCode.INVALID_REQUEST,
        )
    actual = hashlib.sha256(raw_bytes).hexdigest().lower()
    if actual != lower_hash:
        raise OpenDVError(
            f"CAS blob integrity mismatch for '{lower_hash}'.",
            error_code=ErrorCode.CORRUPT_SOURCE_BLOB,
        )
    return raw_bytes, lower_hash


@contextlib.contextmanager
def materialize_cas_tempfile(raw_bytes: bytes, ext: str):
    """Materialize CAS blob into an isolated temporary file with cleanup."""
    suffix = f".{ext}" if ext else ".bin"
    fd, tmp_name = tempfile.mkstemp(prefix="opendv_cas_conn_", suffix=suffix)
    tmp_path = Path(tmp_name)
    try:
        os.write(fd, raw_bytes)
        os.close(fd)
        yield tmp_path
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def read_xlsx_rows(path: Path) -> tuple[list[str], list[list[Any]]]:
    """Read header + data rows from the first sheet of an OOXML .xlsx workbook."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root_ss = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root_ss.iter():
                    if si.tag.endswith("}si") or si.tag == "si":
                        shared.append(
                            "".join(
                                t.text or ""
                                for t in si.iter()
                                if t.tag.endswith("}t") or t.tag == "t"
                            )
                        )
            sheet_names = sorted(
                n
                for n in zf.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            )
            if not sheet_names:
                raise OpenDVError(
                    "XLSX workbook contains no worksheets.",
                    error_code=ErrorCode.CORRUPT_SOURCE_BLOB,
                )
            root = ET.fromstring(zf.read(sheet_names[0]))
            rows: list[list[Any]] = []
            for row_el in root.iter():
                if not (row_el.tag.endswith("}row") or row_el.tag == "row"):
                    continue
                row_vals: list[Any] = []
                for c_el in row_el:
                    if not (c_el.tag.endswith("}c") or c_el.tag == "c"):
                        continue
                    ctype = c_el.attrib.get("t", "")
                    val_text = ""
                    for child in c_el:
                        if child.tag.endswith("}v") or child.tag == "v":
                            val_text = child.text or ""
                            break
                        if child.tag.endswith("}is") or child.tag == "is":
                            val_text = "".join(
                                t.text or ""
                                for t in child.iter()
                                if t.tag.endswith("}t") or t.tag == "t"
                            )
                            break
                    if ctype == "s" and val_text.isdigit() and int(val_text) < len(shared):
                        row_vals.append(shared[int(val_text)])
                    elif ctype == "inlineStr":
                        row_vals.append(val_text)
                    elif val_text == "":
                        row_vals.append("")
                    else:
                        try:
                            row_vals.append(float(val_text) if "." in val_text else int(val_text))
                        except ValueError:
                            row_vals.append(val_text)
                if row_vals:
                    rows.append(row_vals)
            if not rows:
                return ["col_1"], []
            headers = [str(h or f"col_{i}") for i, h in enumerate(rows[0])]
            return headers, rows[1:]
    except (zipfile.BadZipFile, ET.ParseError, KeyError, UnicodeDecodeError) as exc:
        raise OpenDVError(
            f"Corrupt or unreadable XLSX workbook '{path.name}': {exc}",
            error_code=ErrorCode.CORRUPT_SOURCE_BLOB,
        ) from exc


def fibo_gaps_for_columns(table_name: str, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect columns lacking strong FIBO concept matches for Path B hints."""
    gaps: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        loader = get_default_fibo_loader()
        for col in columns[:12]:
            cname = str(col.get("column_name") or "")
            if not cname or col.get("surrogate_hint"):
                continue
            clean_term = cname.replace("_", " ").strip()
            hits = loader.search_candidates(clean_term, limit=1)
            best_score = float(hits[0].get("match_score") or 0.0) if hits else 0.0
            if best_score < 0.65:
                gaps.append(
                    {
                        "table_name": table_name,
                        "column_name": cname,
                        "best_fibo_score": round(best_score, 4),
                        "recommendation": "Use Path B STRUCTURED_SCHEMA grounding if domain concept.",
                    }
                )
    return gaps


def build_table_entry(
    *,
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    pk_cols: set[str],
    fk_map: dict[str, dict[str, str]],
    source_id: str,
    source_sha256: str,
) -> TableStructure:
    """Profile a DuckDB table with GraphBProfiler into a canonical TableStructure."""
    profiler = GraphBProfiler(conn)
    col_metrics = profiler.profile_table_columns(table_name)
    quoted_t = f'"{table_name.replace(chr(34), chr(34) * 2)}"'
    sample_raw = conn.execute(f"SELECT * FROM {quoted_t} LIMIT {SAMPLE_ROW_CAP}").fetchall()
    headers = [m.column_name for m in col_metrics]
    sample_rows = [
        ["" if v is None else str(v) for v in row] for row in sample_raw[:SAMPLE_ROW_CAP]
    ]

    rich_cols: list[dict[str, Any]] = []
    for m in col_metrics:
        is_pk = m.column_name in pk_cols
        lname = m.column_name.lower()
        surrogate_hint = bool(
            is_pk
            and (
                lname.endswith(("_sk", "_seq", "_id", "_key"))
                or lname == "id"
                or "INT" in m.physical_type.upper()
            )
        )
        rich_cols.append(
            {
                "column_name": m.column_name,
                "sql_type": m.physical_type,
                "is_primary_key": is_pk,
                "surrogate_hint": surrogate_hint,
                "foreign_key": fk_map.get(m.column_name),
                "null_rate": m.null_rate,
                "shannon_entropy": m.shannon_entropy_bits,
                "shannon_entropy_norm": m.shannon_entropy_norm,
                "uniqueness_ratio": m.uniqueness_ratio,
                "candidate_role": m.recommended_dv2_role or "DESCRIPTIVE_PAYLOAD",
            }
        )

    fibo_gaps = fibo_gaps_for_columns(table_name, rich_cols)
    return TableStructure(
        table_id=f"tbl-{source_sha256[:8]}-{table_name}",
        headers=headers,
        rows=sample_rows,
        caption=table_name,
        taint=TaintClassification.INTERNAL,
        metadata={
            "source_id": source_id,
            "source_sha256": source_sha256,
            "table_name": table_name,
            "columns": rich_cols,
            "fibo_gap_hints": fibo_gaps,
        },
    )


def build_databricks_table_entry(
    table_meta: dict[str, Any], *, source_id: str, source_sha256: str
) -> TableStructure:
    """Convert a Databricks UC inspected table dictionary into a TableStructure."""
    tname = str(table_meta.get("table_name") or "uc_table")
    pk_set = {str(p) for p in (table_meta.get("primary_key") or [])}
    fk_map: dict[str, dict[str, str]] = {}
    for fk in table_meta.get("foreign_keys") or []:
        if isinstance(fk, dict) and fk.get("column"):
            fk_map[str(fk["column"])] = {
                "table": str(fk.get("referenced_table") or fk.get("table") or ""),
                "column": str(fk.get("referenced_column") or fk.get("column") or ""),
            }
    raw_cols = list(table_meta.get("columns") or [])
    headers = [str(c.get("name") or c.get("column_name") or "") for c in raw_cols if isinstance(c, dict)]
    rich_cols: list[dict[str, Any]] = []
    for c in raw_cols:
        if not isinstance(c, dict):
            continue
        cname = str(c.get("name") or c.get("column_name") or "")
        sql_type = str(c.get("data_type") or c.get("sql_type") or "VARCHAR")
        is_pk = cname in pk_set or bool(c.get("is_primary_key"))
        lname = cname.lower()
        surrogate_hint = bool(
            is_pk
            and (
                lname.endswith(("_sk", "_seq", "_id", "_key"))
                or lname == "id"
                or "INT" in sql_type.upper()
            )
        )
        rich_cols.append(
            {
                "column_name": cname,
                "sql_type": sql_type,
                "is_primary_key": is_pk,
                "surrogate_hint": surrogate_hint,
                "foreign_key": fk_map.get(cname),
                "null_rate": 0.0,
                "shannon_entropy": 0.0,
                "candidate_role": "BUSINESS_KEY" if is_pk else "DESCRIPTIVE_PAYLOAD",
                "governance_tags": list(c.get("tags") or c.get("governance_tags") or []),
            }
        )
    return TableStructure(
        table_id=f"tbl-{source_sha256[:8]}-{tname}",
        headers=headers,
        rows=[],
        caption=tname,
        taint=TaintClassification.INTERNAL,
        metadata={
            "source_id": source_id,
            "source_sha256": source_sha256,
            "table_name": tname,
            "columns": rich_cols,
            "governance_tags": list(table_meta.get("governance_tags") or []),
            "fibo_gap_hints": fibo_gaps_for_columns(tname, rich_cols),
        },
    )


def load_duckdb_table_from_rows(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    headers: list[str],
    rows: list[tuple[Any, ...] | list[Any]],
) -> None:
    """Create an in-memory DuckDB table from row tuples using PyArrow registration."""
    cols_dict: dict[str, list[Any]] = {h: [] for h in headers}
    for row in rows:
        for idx, h in enumerate(headers):
            val = row[idx] if idx < len(row) else None
            cols_dict[h].append(val)
    pa_tbl = pa.table(cols_dict)
    conn.register("_tmp_ingest_arrow", pa_tbl)
    quoted_t = f'"{table_name.replace(chr(34), chr(34) * 2)}"'
    conn.execute(f"CREATE TABLE {quoted_t} AS SELECT * FROM _tmp_ingest_arrow")
    conn.unregister("_tmp_ingest_arrow")


def extract_markdown_table_data(text: str) -> dict[str, Any] | None:
    """Parse pipe-delimited Markdown table headers and rows if present."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return None
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows: list[list[str]] = []
    for ln in lines[1:]:
        cells = [cell.strip() for cell in ln.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} for c in cells if c):
            continue
        rows.append(cells)
    return {"headers": headers, "rows": rows}
