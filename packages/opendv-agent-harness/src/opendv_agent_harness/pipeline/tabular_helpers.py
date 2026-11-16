"""Helper utilities for tabular feed discovery, profiling, and DuckDB staging."""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb

from opendv_agent_harness.pipeline.spec import (
    Stage01FeedDiscoveryItem,
)
from opendv_sources.models.specs import SourceSpec
from opendv_sources.registry.scheme_registry import get_scheme_registry
from opendv_sources.registry.source_registry import get_source_registry

HEX_64_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


def quote_ident(ident: str) -> str:
    """Quote a SQL identifier."""
    return f'"{ident.replace(chr(34), chr(34) * 2)}"'


def validate_identifier(ident: str) -> str:
    """Validate identifier."""
    if not ident or any(c in ident for c in ("\x00", "\r", "\n")):
        raise ValueError(f"Invalid SQL identifier: {ident!r}")
    return ident


def _to_source_target(
    src: SourceSpec | dict[str, Any] | None, path: Path | None
) -> Path | str | SourceSpec:
    if isinstance(src, SourceSpec):
        return src.model_copy(update={"uri": str(path)}) if path else src
    if isinstance(src, dict):
        return SourceSpec.model_validate({**src, **({"uri": str(path)} if path else {})})
    assert path is not None
    return path


def _enforce_server_ssrf(
    host_or_uri: str | None, allowed_hosts: Sequence[str] | set[str] | None
) -> None:
    if not host_or_uri or not host_or_uri.strip():
        return
    cleaned = host_or_uri.strip()
    raw = (
        (urlparse(cleaned).hostname or cleaned.split("://")[-1].split("/")[0])
        if "://" in cleaned
        else cleaned.split("/")[0]
    )
    norm = raw.strip().lower()
    if not norm:
        return
    if not allowed_hosts:
        raise ValueError(
            f"Remote host '{cleaned}' rejected: server has no configured allowed_hosts. "
            "Configure allowed_hosts or OPENDV_ALLOWED_HOSTS (SSRF protection)."
        )
    allowed = {h.split("://")[-1].rstrip("/").split("/")[0].strip().lower() for h in allowed_hosts}
    if norm not in allowed:
        raise ValueError(f"Remote host '{cleaned}' is not in allowed hosts: {sorted(allowed)}.")


def _make_col_descs(
    columns: Sequence[Any], bks: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    pk = set(bks or ())
    res: list[dict[str, Any]] = []
    for c in columns:
        nm = getattr(c, "name", str(c[0]) if isinstance(c, (tuple, list)) else str(c))
        pt = getattr(
            c,
            "physical_type",
            str(c[1]) if isinstance(c, (tuple, list)) and len(c) > 1 else "VARCHAR",
        )
        ipk = getattr(c, "is_primary_key", False) or nm in pk
        res.append({
            "name": nm,
            "physical_type": pt,
            "is_primary_key": ipk,
            "governance_tags": getattr(c, "governance_tags", {}),
        })
    return res


def _make_fk_hints(fks: Sequence[Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for fk in fks:
        if hasattr(fk, "model_dump"):
            d = fk.model_dump()
            if not d.get("fk_column"):
                d["fk_column"] = d.get("source_column") or (
                    d.get("source_columns", [None])[0] if d.get("source_columns") else None
                )
            hints.append(d)
        elif hasattr(fk, "target_table"):
            sc = (
                getattr(fk, "source_column", None)
                or (fk.source_columns[0] if getattr(fk, "source_columns", None) else None)
                or getattr(fk, "target_column", None)
            )
            tc = getattr(fk, "target_column", None)
            tcs = getattr(fk, "target_columns", None) or ([tc] if tc else [])
            hints.append({
                "fk_column": sc,
                "source_column": sc,
                "source_columns": getattr(fk, "source_columns", None) or [],
                "target_table": fk.target_table,
                "target_column": tc,
                "target_columns": tcs,
                "constraint_name": getattr(fk, "constraint_name", None),
            })
        elif isinstance(fk, dict):
            d = dict(fk)
            if not d.get("fk_column"):
                d["fk_column"] = d.get("source_column") or (
                    d.get("source_columns", [None])[0] if d.get("source_columns") else None
                )
            hints.append(d)
    return hints


def _collect_roots(*roots: Any) -> list[Path]:
    res: list[Path] = []
    for r in roots:
        if r and (p := Path(r).resolve()).exists() and p not in res:
            res.append(p)
    return res


def _load_table_into_duckdb(
    conn: duckdb.DuckDBPyConnection,
    view_name: str,
    file_path: Path | str | SourceSpec,
    *,
    workspace_root: Path | None = None,
    allowed_roots: list[Path] | None = None,
    allow_temp: bool = True,
    is_server: bool = False,
    allowed_hosts: Sequence[str] | set[str] | None = None,
) -> tuple[int, list[str]]:
    ws = Path(workspace_root or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()
    spec = (
        file_path.model_copy(deep=True)
        if isinstance(file_path, SourceSpec)
        else SourceSpec(adapter="file", uri=str(Path(file_path).resolve()))
    )
    p_parent = (
        Path(spec.uri).resolve().parent
        if (workspace_root is None and spec.uri and Path(spec.uri).is_file())
        else None
    )
    eff_allowed = _collect_roots(
        *(allowed_roots or ()), ws / "state/artifacts", Path("state/artifacts"), p_parent
    )
    reg = get_source_registry()
    adapter = reg.get_tabular_adapter(
        spec.adapter,
        workspace_root=ws,
        allowed_roots=eff_allowed or None,
        allow_temp=allow_temp,
        is_server=is_server,
        allowed_hosts=allowed_hosts,
    )
    adapter.register(spec, conn, view_name)
    qv = quote_ident(view_name)
    cols = [str(r[0]) for r in conn.execute(f"DESCRIBE {qv}").fetchall()]
    cnt_row = conn.execute(f"SELECT COUNT(*) FROM {qv}").fetchall()
    return int(cnt_row[0][0]) if cnt_row and cnt_row[0] else 0, cols


def _calc_col_stats(
    conn: duckdb.DuckDBPyConnection, vname: str, cols: list[str], row_cnt: int
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    qv = quote_ident(vname)
    for c in cols:
        nn = 1.0
        if row_cnt > 0:
            with contextlib.suppress(Exception):
                nn = float(
                    conn.execute(f"SELECT COUNT({quote_ident(c)}) FROM {qv}").fetchone()[0]
                ) / float(row_cnt)
        stats[c] = {"column_name": c, "non_null_ratio": nn, "null_rate": 1.0 - nn}
    return stats


def _safe_load_view(
    conn: duckdb.DuckDBPyConnection,
    clean_ss: str,
    clean_tn: str,
    spec_or_path: Any,
    ws: Path,
    eff_base: Path,
    is_server: bool,
    allowed_hosts: Sequence[str] | set[str] | None,
    fallback_bks: list[str],
) -> tuple[str, int, list[str]]:
    vname = f"bronze_{clean_ss}_{clean_tn}" if clean_ss else f"bronze_{clean_tn}"
    validate_identifier(vname)
    try:
        row_cnt, cols = _load_table_into_duckdb(
            conn,
            vname,
            spec_or_path,
            workspace_root=ws,
            allowed_roots=[eff_base, ws],
            allow_temp=True,
            is_server=is_server,
            allowed_hosts=allowed_hosts,
        )
        if f"bronze_{clean_tn}" != vname:
            with contextlib.suppress(Exception):
                conn.execute(
                    f"CREATE OR REPLACE VIEW {quote_ident(f'bronze_{clean_tn}')} "
                    f"AS SELECT * FROM {quote_ident(vname)}"
                )
    except (duckdb.Error, OSError, ValueError, RuntimeError):
        row_cnt, cols = 0, fallback_bks
    return vname, row_cnt, cols


def _record_item(
    items: list[Stage01FeedDiscoveryItem],
    profiles: dict[str, Any],
    *,
    ss: str,
    tname: str,
    cols: list[str],
    row_cnt: int,
    pks: list[str],
    fks: list[dict[str, Any]],
    col_descs: list[dict[str, Any]],
    col_stats: dict[str, Any],
    gov_tags: dict[str, Any] | None = None,
    extra_keys: Sequence[str] = (),
) -> None:
    items.append(
        Stage01FeedDiscoveryItem(
            source_system=ss,
            table_name=tname,
            column_count=len(cols),
            row_count=row_cnt,
            primary_keys=pks,
            foreign_keys=fks,
            column_descriptors=col_descs,
        )
    )
    entry = {
        "row_count": row_cnt,
        "columns": cols,
        "primary_keys": pks,
        "governance_tags": gov_tags or {},
        "column_stats": col_stats,
        **col_stats,
    }
    for k in (f"{ss}.{tname}", tname, *extra_keys):
        if k:
            profiles[k] = entry


def _resolve_cas_spec(
    u: str, spec: SourceSpec, fmt: str | None, cas_store: Any, eff_base: Path
) -> SourceSpec:
    raw = u[6:].split("?")[0].split("#")[0].strip()
    h = raw.split("/")[0].strip()
    if not HEX_64_RE.match(h):
        raise ValueError(f"Invalid CAS URI '{u}': expected 64-hex SHA-256 digest, got '{h}'")
    cdir = (
        Path(cas_store.base_dir).resolve()
        if cas_store and hasattr(cas_store, "base_dir")
        else (eff_base / "state/artifacts")
    )
    p = next((p for c in (h.upper(), h.lower(), h) if (p := cdir / c).exists()), None)
    if p is None:
        return spec
    adp = (
        spec.adapter
        or (get_scheme_registry().resolve_adapter(u) if not spec.adapter else spec.adapter)
    )
    efmt = spec.format or fmt
    if not adp and efmt:
        adp = (
            "file"
            if efmt in {"csv", "tsv", "parquet", "json", "jsonl", "ndjson"}
            else (
                "sqlite"
                if efmt in {"sqlite", "sqlite3", "db"}
                else ("duckdb" if efmt in {"duckdb", "ddb"} else None)
            )
        )
    upd: dict[str, Any] = {"uri": str(p.resolve())}
    if adp:
        upd["adapter"] = adp
    if efmt:
        upd["format"] = efmt
    return spec.model_copy(update=upd)


def _extract_pushdown_stats(
    cat_adapter: Any, spec: Any, tname: str, ns: Any, fallback_cnt: int
) -> dict[str, Any]:
    with contextlib.suppress(Exception):
        from opendv_sources.models.specs import SamplingPolicy, TableRef

        res = cat_adapter.profile_pushdown(
            spec,
            TableRef(table_name=tname, namespace=ns),
            spec.sampling_policy or SamplingPolicy(),
        )
        if res and res.column_stats:
            tot = getattr(res, "total_row_count", 0) or fallback_cnt
            for m in res.column_stats.values():
                if isinstance(m, dict):
                    nn = m.get("non_null_ratio")
                    nr = m.get("null_rate", m.get("null_ratio"))
                    if nn is None:
                        nn = (
                            1.0 - (float(m["null_count"]) / float(tot))
                            if "null_count" in m and tot > 0
                            else ((1.0 - float(nr)) if nr is not None else 1.0)
                        )
                    m["non_null_ratio"] = float(nn)
                    m["null_rate"] = float(1.0 - float(nn) if nr is None else nr)
            return res.column_stats
    return {}


__all__ = [
    "HEX_64_RE",
    "_calc_col_stats",
    "_collect_roots",
    "_enforce_server_ssrf",
    "_extract_pushdown_stats",
    "_load_table_into_duckdb",
    "_make_col_descs",
    "_make_fk_hints",
    "_record_item",
    "_resolve_cas_spec",
    "_safe_load_view",
    "_to_source_target",
    "quote_ident",
    "validate_identifier",
]
