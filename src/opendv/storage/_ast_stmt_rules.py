"""Private DDL/statement AST rules for Unified SQL AST Verifier (`sql_ast_verifier.py`).

Strictly Layer 2a: imports only sqlglot and stdlib.
"""

from __future__ import annotations

from sqlglot import exp

RAW_VAULT_PREFIXES: tuple[str, ...] = (
    "hub_", "lnk_", "t_lnk_", "sat_", "mas_", "esat_", "sts_", "rts_",
)
SAT_PREFIXES: tuple[str, ...] = ("sat_", "mas_", "esat_", "sts_", "rts_")
RAW_VAULT_SCHEMAS: frozenset[str] = frozenset({"raw_vault"})

_DESTRUCTIVE_ALTER_ACTIONS = (
    exp.Drop, exp.TruncateTable, exp.RenameColumn, exp.SwapTable,
    exp.ReplaceColumns, exp.DropPartition, exp.ReplacePartition,
)

_FORBIDDEN_CREATE_KINDS: frozenset[str] = frozenset({
    "STAGE", "SECRET", "STORAGE INTEGRATION", "EXTERNAL FUNCTION",
    "INTEGRATION", "PIPE", "EXTERNAL TABLE", "FILE FORMAT", "STREAM",
    "TASK", "EXTERNAL LOCATION", "STORAGE CREDENTIAL", "CONNECTION",
    "FOREIGN TABLE", "DICTIONARY", "ICEBERG TABLE", "EXTERNAL VOLUME",
    "FUNCTION", "PROCEDURE", "MACRO", "AGGREGATE",
    "TRIGGER", "EVENT TRIGGER", "RULE", "POLICY",
})

_FORBIDDEN_DROP_KINDS: frozenset[str] = frozenset({
    "STAGE", "FILE FORMAT", "STORAGE INTEGRATION", "INTEGRATION", "SECRET",
    "PIPE", "EXTERNAL FUNCTION", "EXTERNAL LOCATION", "STORAGE CREDENTIAL",
    "CONNECTION", "FOREIGN TABLE", "DICTIONARY", "ICEBERG TABLE",
    "EXTERNAL VOLUME", "EXTERNAL TABLE", "STREAM", "TASK",
    "FUNCTION", "PROCEDURE", "MACRO", "AGGREGATE",
    "TRIGGER", "EVENT TRIGGER", "RULE", "POLICY",
})

_EXTERNAL_CH_ENGINES: frozenset[str] = frozenset({
    "S3", "S3CLUSTER", "URL", "HDFS", "HDFSCLUSTER", "AZURE",
    "AZUREBLOBSTORAGE", "AZUREBLOBSTORAGECLUSTER", "GCS", "FILE",
    "MYSQL", "POSTGRESQL", "ODBC", "JDBC", "MONGODB", "KAFKA",
    "RABBITMQ", "NATS", "S3QUEUE", "SQLITE", "DELTALAKE", "ICEBERG", "HUDI",
    "EXECUTABLE", "EXECUTABLEPOOL", "REMOTE", "REMOTESECURE", "DISTRIBUTED",
    "DICTIONARY", "MATERIALIZEDPOSTGRESQL", "MATERIALIZEDMYSQL",
    "EXTERNALDISTRIBUTED", "ICEBERGS3", "ICEBERGAZURE", "ICEBERGLOCAL",
    "DELTALAKES3", "DELTALAKEAZURE", "DELTALAKELOCAL", "FILELOG", "AZUREQUEUE",
    "REDIS", "ARROWFLIGHT", "HIVE", "YTSAURUS", "KEEPERMAP", "EMBEDDEDROCKSDB",
})

_FORBIDDEN_CREATE_PROP_NAMES: frozenset[str] = frozenset({
    "EXTERNAL", "EXTERNAL_LOCATION", "DELTA_LOCATION", "STORAGE_LOCATION",
    "LOCATION", "URIS", "URI", "URL", "PATH",
    "STORAGE_URI", "EXTERNAL_VOLUME", "AZURE_SAS_TOKEN",
    "STORAGE_INTEGRATION", "BASE_LOCATION", "CATALOG_INTEGRATION",
})

_FORMAT_TABLE_PREFIXES: frozenset[str] = frozenset({
    "parquet", "delta", "csv", "json", "orc", "avro", "iceberg", "tsv", "ndjson", "jsonl",
    "xlsx", "sqlite", "duckdb", "arrow", "hudi", "text", "binaryfile", "xml", "libsvm",
    "jdbc", "kafka", "socket", "rate",
})

_FILE_EXT_SUFFIXES: tuple[str, ...] = (
    ".parquet", ".csv", ".json", ".jsonl", ".ndjson", ".orc", ".avro", ".txt", ".ipc",
    ".feather", ".arrow", ".xml", ".yaml", ".yml", ".duckdb", ".sqlite", ".db", ".tsv",
    ".xlsx", ".gz", ".bz2", ".xz", ".zst", ".zip", ".tar",
)

_COMPRESSION_SUFFIXES: tuple[str, ...] = (".gz", ".bz2", ".xz", ".zst", ".zip")

_PATH_CHARS: tuple[str, ...] = ("/", "\\", ":", "*", "?")


def func_name(node: exp.Expr) -> str:
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    return node.sql_name().upper() if isinstance(node, exp.Func) else ""


def has_file_extension(name: str) -> bool:
    cur = name.lower()
    if cur.endswith(_FILE_EXT_SUFFIXES):
        return True
    while True:
        stripped = False
        for cext in _COMPRESSION_SUFFIXES:
            if cur.endswith(cext):
                cur = cur[: -len(cext)]
                stripped = True
                if cur.endswith(_FILE_EXT_SUFFIXES):
                    return True
        if not stripped:
            return False


def is_unsafe_external_table_ref(
    table: exp.Table,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    """Return True if Table is a string literal, file path, TVF, or format-qualified ref."""
    for part in (table.this, table.args.get("db"), table.args.get("catalog")):
        if part is None:
            continue
        if isinstance(part, exp.Identifier):
            if not (part.name or "").strip():
                return True
            if string_token_starts and part.meta.get("start", -1) in string_token_starts:
                return True
        else:
            return True

    tname = (table.name or "").lower()
    tdb = (table.db or "").lower()
    tcat = (table.catalog or "").lower()
    if tdb in _FORMAT_TABLE_PREFIXES or tcat in _FORMAT_TABLE_PREFIXES:
        return True
    for part_str in (tname, tdb, tcat):
        if any(ch in part_str for ch in _PATH_CHARS) or has_file_extension(part_str):
            return True
    return bool(
        (tdb or tcat)
        and isinstance(table.this, exp.Identifier)
        and table.this.quoted
        and has_file_extension(".".join(p for p in (tcat, tdb, tname) if p))
    )


def is_unsafe_table_node(
    node: exp.Table,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    if not isinstance(node.this, exp.Identifier) or not (node.this.name or "").strip():
        return True
    return is_unsafe_external_table_ref(node, string_token_starts)


def has_external_copy_args(
    stmt: exp.Copy,
    target: exp.Table | None,
    string_token_starts: frozenset[int] | set[int] = frozenset(),
) -> bool:
    if not stmt.args.get("kind") or target is None or stmt.args.get("params"):
        return True
    if is_unsafe_external_table_ref(target, string_token_starts):
        return True
    return any(
        not isinstance(f, exp.Table)
        or not f.db
        or (f.name or "").upper() in ("PROGRAM", "STDIN", "STDOUT")
        or is_unsafe_external_table_ref(f, string_token_starts)
        for f in (stmt.args.get("files") or ())
    )


def is_raw_vault_table(table_expr: exp.Table | None) -> bool:
    if table_expr is None:
        return False
    db = (table_expr.db or "").lower()
    name = (table_expr.name or "").lower()
    if db in RAW_VAULT_SCHEMAS:
        return True
    if db == "business_vault":
        return not name.startswith(("pit_", "brg_", "vw_"))
    if db in ("staging", "information_mart", "bronze"):
        return False
    return name.startswith((*RAW_VAULT_PREFIXES, "sal_"))


def unwrap_target_table(node: exp.Expr | None) -> exp.Table | None:
    while isinstance(node, exp.Subquery) and not isinstance(
        node.this, (exp.Select, exp.SetOperation)
    ):
        node = node.this
    if isinstance(node, exp.Table):
        return node
    if isinstance(node, exp.Schema) and isinstance(node.this, exp.Table):
        return node.this
    return None


def table_label(table_expr: exp.Table | None) -> str:
    if table_expr is None:
        return "sql_statement"
    if not table_expr.name and table_expr.this is not None:
        return table_expr.sql()[:80]
    return f"{table_expr.db}.{table_expr.name}" if table_expr.db else table_expr.name


def table_has_raw_vault_schema(table_expr: exp.Table | None) -> bool:
    if table_expr is None:
        return False
    parts = (
        (table_expr.name or "").lower(),
        (table_expr.db or "").lower(),
        (table_expr.catalog or "").lower(),
    )
    return any(p in RAW_VAULT_SCHEMAS for p in parts)


def check_drop_schema_or_db(
    stmt: exp.Drop, targets: list[exp.Table], diags: list[tuple[str, str, str]],
) -> None:
    kind_u = str(stmt.args.get("kind") or "").upper()
    if kind_u in _FORBIDDEN_DROP_KINDS or stmt.args.get("iceberg"):
        lbl = table_label(targets[0]) if targets else "sql_ast"
        drop_kind = "ICEBERG TABLE" if stmt.args.get("iceberg") else kind_u
        diags.append(("EVAL-AST-01", lbl, f"DROP {drop_kind} forbidden: {lbl}."))
        return
    if kind_u not in ("DATABASE", "SCHEMA"):
        return
    for t in targets:
        if table_has_raw_vault_schema(t):
            lbl = table_label(t)
            msg = f"DROP {kind_u} forbidden on Z2 Raw Vault schema {lbl}."
            diags.append(("EVAL-AST-01", lbl, msg))


def check_alter_stmt(stmt: exp.Alter, diags: list[tuple[str, str, str]]) -> None:
    a_target = unwrap_target_table(stmt.this)
    src_is_rv, lbl = is_raw_vault_table(a_target), table_label(a_target)
    a_kind = str(stmt.args.get("kind") or "").upper()
    if (
        stmt.args.get("iceberg")
        or a_kind in _FORBIDDEN_CREATE_KINDS
        or _has_forbidden_create_prop(stmt)
    ):
        diags.append(("EVAL-AST-01", lbl, f"ALTER external/iceberg properties forbidden: {lbl}."))
    for act in stmt.args.get("actions") or ():
        if isinstance(act, exp.AlterSet) and act.args.get("location") is not None:
            diags.append(("EVAL-AST-01", lbl, f"ALTER SET LOCATION forbidden: {lbl}."))
        elif isinstance(act, (exp.AlterRename, exp.SwapTable)):
            act_tgt = unwrap_target_table(act.this)
            if src_is_rv or is_raw_vault_table(act_tgt):
                tgt_lbl = table_label(act_tgt) if is_raw_vault_table(act_tgt) else lbl
                kind = "SWAP WITH" if isinstance(act, exp.SwapTable) else "RENAME"
                msg = f"ALTER {kind} forbidden involving Z2 Raw Vault {tgt_lbl}."
                diags.append(("EVAL-AST-01", tgt_lbl, msg))
        elif src_is_rv and (
            isinstance(act, _DESTRUCTIVE_ALTER_ACTIONS)
            or (isinstance(act, exp.AlterColumn) and act.args.get("drop"))
        ):
            kind = "COLUMN DROP" if isinstance(act, exp.AlterColumn) else type(act).__name__
            msg = f"Destructive ALTER {kind} forbidden on Z2 {lbl}."
            diags.append(("EVAL-AST-01", lbl, msg))


def check_detach_stmt(stmt: exp.Detach, diags: list[tuple[str, str, str]]) -> None:
    target = stmt.this
    tbl = unwrap_target_table(target)
    if tbl is not None:
        if table_has_raw_vault_schema(tbl) or is_raw_vault_table(tbl):
            lbl = table_label(tbl)
            diags.append(("EVAL-AST-01", lbl, f"DETACH forbidden on Z2 Raw Vault {lbl}."))
        return
    if isinstance(target, (exp.Var, exp.Identifier)):
        name = str(target.name or target.this or "")
        if name.lower() in RAW_VAULT_SCHEMAS:
            msg = f"DETACH forbidden on Z2 Raw Vault schema '{name}'."
            diags.append(("EVAL-AST-01", name, msg))


def _external_engine_name(stmt: exp.Create) -> str | None:
    eng_prop = getattr(exp, "EngineProperty", None)
    if eng_prop is None:
        return None
    for eng in stmt.find_all(eng_prop):
        e_node = eng.this
        if isinstance(e_node, (exp.Func, exp.Anonymous)):
            ename = func_name(e_node)
        elif isinstance(e_node, (exp.Var, exp.Identifier)):
            ename = str(e_node.name or e_node.this or "").upper()
        else:
            ename = ""
        if ename in _EXTERNAL_CH_ENGINES:
            return ename
    return None


def _has_forbidden_create_prop(stmt: exp.Expr) -> bool:
    for cls_n in (
        "ExternalProperty", "LocationProperty", "IcebergProperty", "ExternalVolumeProperty",
    ):
        cls = getattr(exp, cls_n, None)
        if cls is not None and stmt.find(cls) is not None:
            return True
    for p in stmt.find_all(exp.Property, exp.PropertyEQ):
        if type(p) is exp.Property or isinstance(p, exp.PropertyEQ):
            pname = (
                p.name
                or (p.this.name if isinstance(p.this, (exp.Var, exp.Identifier)) else "")
                or ""
            ).upper().replace(".", "_")
            if pname in _FORBIDDEN_CREATE_PROP_NAMES:
                return True
    return False


def check_create_stmt(stmt: exp.Create, diags: list[tuple[str, str, str]]) -> None:
    c_kind = str(stmt.args.get("kind") or stmt.kind or "").upper()
    is_udf = isinstance(stmt.this, exp.UserDefinedFunction)
    c_target = unwrap_target_table(stmt.this.this if is_udf else stmt.this)
    has_ext_prop = bool(stmt.args.get("iceberg")) or _has_forbidden_create_prop(stmt)
    ext_eng = _external_engine_name(stmt)
    if c_kind in _FORBIDDEN_CREATE_KINDS or is_udf or has_ext_prop or ext_eng is not None:
        lbl = table_label(c_target)
        if c_kind in _FORBIDDEN_CREATE_KINDS or is_udf:
            kind_txt = c_kind or "FUNCTION"
        elif ext_eng is not None:
            kind_txt = f"EXTERNAL ENGINE={ext_eng} {c_kind}"
        else:
            kind_txt = f"EXTERNAL {c_kind}"
        diags.append(("EVAL-AST-01", lbl, f"CREATE {kind_txt} forbidden: {lbl}."))
        return
    is_ctas = "TABLE" in c_kind and (
        stmt.expression is not None or stmt.args.get("clone") is not None
    )
    if (
        stmt.args.get("replace")
        and c_kind in ("SCHEMA", "DATABASE")
        and table_has_raw_vault_schema(c_target)
    ):
        lbl = table_label(c_target)
        msg = f"CREATE OR REPLACE {c_kind} forbidden on Z2 Raw Vault schema {lbl}."
        diags.append(("EVAL-AST-01", lbl, msg))
    elif (
        ((stmt.args.get("replace") and "TABLE" in c_kind) or "VIEW" in c_kind or is_ctas)
        and is_raw_vault_table(c_target)
    ):
        lbl = table_label(c_target)
        kind_lbl = (
            "VIEW"
            if "VIEW" in c_kind
            else ("OR REPLACE TABLE" if stmt.args.get("replace") else "TABLE AS SELECT")
        )
        msg = f"CREATE {kind_lbl} forbidden on Z2 Raw Vault {lbl}."
        diags.append(("EVAL-AST-01", lbl, msg))


_SAFE_FUNC_CLS = getattr(exp, "SafeFunc", None)


def is_safe_or_try_func(cur: exp.Func) -> bool:
    if isinstance(cur, (exp.Concat, exp.ConcatWs, exp.TryCast)):
        return False
    if (_SAFE_FUNC_CLS is not None and isinstance(cur, _SAFE_FUNC_CLS)) or cur.args.get("safe"):
        return True
    if type(cur).__name__.startswith(("Safe", "Try")):
        return True
    try:
        return cur.sql_name().upper().startswith(("SAFE_", "TRY_"))
    except NotImplementedError:
        return False


def unwrap_parens(node: exp.Expr | None) -> exp.Expr | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def collect_source_tables(
    node: exp.Expr | None,
    out: list[tuple[str, exp.Table]],
    *,
    inherited_alias: str = "",
    subqueries_out: list[tuple[str, exp.Subquery]] | None = None,
) -> None:
    if node is None:
        return
    if isinstance(node, (exp.From, exp.Join)):
        collect_source_tables(node.this, out, subqueries_out=subqueries_out)
    elif isinstance(node, exp.Lateral):
        collect_source_tables(
            node.this, out,
            inherited_alias=(inherited_alias or node.alias),
            subqueries_out=subqueries_out,
        )
    elif isinstance(node, exp.Subquery):
        inner, eff_alias = node.this, inherited_alias or node.alias
        if isinstance(inner, (exp.Select, exp.SetOperation)) or node.args.get("with_") is not None:
            if subqueries_out is not None:
                subqueries_out.append((eff_alias, node))
        else:
            collect_source_tables(
                inner, out, inherited_alias=eff_alias, subqueries_out=subqueries_out
            )
    elif isinstance(node, exp.Table):
        keys = [k for k in (node.alias.lower(), inherited_alias.lower()) if k]
        if not keys and node.name:
            keys = [node.name.lower()]
        for k in dict.fromkeys(keys):
            out.append((k, node))
    else:
        return
    for j in node.args.get("joins") or ():
        collect_source_tables(j, out, subqueries_out=subqueries_out)


__all__ = [
    "RAW_VAULT_PREFIXES", "RAW_VAULT_SCHEMAS", "SAT_PREFIXES",
    "check_alter_stmt", "check_create_stmt", "check_detach_stmt", "check_drop_schema_or_db",
    "collect_source_tables", "func_name", "has_external_copy_args", "has_file_extension",
    "is_raw_vault_table", "is_safe_or_try_func", "is_unsafe_external_table_ref",
    "is_unsafe_table_node",
    "table_has_raw_vault_schema", "table_label", "unwrap_parens", "unwrap_target_table",
]
