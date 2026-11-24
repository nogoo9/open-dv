"""Strict literal-only -1/-2 Zero-Key SYSTEM_SEED verifier for DV-AST-007.

Strictly Layer 2a: imports only sqlglot and stdlib.
"""

from __future__ import annotations

import re

from sqlglot import exp

_ZERO_HEX_64: frozenset[str] = frozenset({"0" * 64, "f" * 64})
_TO_BINARY_TYPE: type[exp.Expr] | None = getattr(exp, "ToBinary", None)
_FROM_HEX_TYPE: type[exp.Expr] | None = getattr(exp, "FromHex", None)
_KEY_COL_PREFIXES: tuple[str, ...] = ("hk_", "fk_", "m_hk_", "d_hk_")
_GHOST_TS_RE = re.compile(
    r"^1900-01-01(?:[T ]00:00:00(?:\.0+)?)?(?:Z|[+-]00:?00)?\Z",
    re.IGNORECASE,
)


def _unwrap_parens(node: exp.Expr | None) -> exp.Expr | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _is_zero_key_expr(node: exp.Expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, (exp.Alias, exp.Paren, exp.Cast, exp.TryCast, exp.Unhex, exp.Lower)):
        return _is_zero_key_expr(node.this)
    if isinstance(node, exp.Convert):
        return _is_zero_key_expr(node.args.get("expression"))
    if _TO_BINARY_TYPE is not None and isinstance(node, _TO_BINARY_TYPE):
        fmt = node.args.get("format")
        if fmt is not None and not (
            isinstance(fmt, exp.Literal) and fmt.is_string and str(fmt.this).upper() == "HEX"
        ):
            return False
        return _is_zero_key_expr(node.this)
    if _FROM_HEX_TYPE is not None and isinstance(node, _FROM_HEX_TYPE):
        return _is_zero_key_expr(node.this)
    if isinstance(node, exp.Decode):
        cs = node.args.get("charset")
        if cs is not None and not (
            isinstance(cs, exp.Literal) and cs.is_string and str(cs.this).lower() == "hex"
        ):
            return False
        return _is_zero_key_expr(node.this)
    if isinstance(node, exp.Repeat):
        inner = _unwrap_parens(node.this)
        times = _unwrap_parens(node.args.get("times"))
        return (
            isinstance(inner, exp.Literal)
            and inner.is_string
            and str(inner.this).lower() in ("00", "ff")
            and isinstance(times, exp.Literal)
            and not times.is_string
            and str(times.this) == "32"
        )
    if isinstance(node, exp.Anonymous):
        fname = node.name.upper()
        args = node.expressions
        if fname in ("REPEAT", "REPLICATE") and len(args) == 2:
            a0, a1 = _unwrap_parens(args[0]), _unwrap_parens(args[1])
            return (
                isinstance(a0, exp.Literal)
                and a0.is_string
                and str(a0.this).lower() in ("00", "ff")
                and isinstance(a1, exp.Literal)
                and not a1.is_string
                and str(a1.this) == "32"
            )
        if fname in ("TO_BINARY", "FROM_HEX", "DECODE", "UNHEX", "HEX_DECODE_STRING"):
            if len(args) == 1:
                return _is_zero_key_expr(args[0])
            if len(args) == 2 and fname in ("TO_BINARY", "DECODE"):
                a1 = _unwrap_parens(args[1])
                if isinstance(a1, exp.Literal) and a1.is_string and str(a1.this).upper() == "HEX":
                    return _is_zero_key_expr(args[0])
        return False
    if isinstance(node, exp.HexString) or (isinstance(node, exp.Literal) and node.is_string):
        return str(node.this).lower().removeprefix("0x") in _ZERO_HEX_64
    return False


def _unwrap_seed_casts(node: exp.Expr | None) -> exp.Expr | None:
    while node is not None:
        if isinstance(node, (exp.Alias, exp.Paren, exp.Cast, exp.TryCast)):
            node = node.this
        elif isinstance(node, exp.Convert):
            node = node.args.get("expression")
        else:
            break
    return node


def _is_ghost_ts_expr(node: exp.Expr | None) -> bool:
    inner = _unwrap_seed_casts(node)
    if inner is None:
        return False
    if isinstance(inner, exp.Anonymous) and inner.name.upper() == "PARSEDATETIME64BESTEFFORT":
        inner = _unwrap_parens(inner.expressions[0]) if inner.expressions else None
    return (
        isinstance(inner, exp.Literal)
        and inner.is_string
        and bool(_GHOST_TS_RE.match(str(inner.this)))
    )


def _is_empty_collection_expr(inner: exp.Expr) -> bool:
    if isinstance(inner, (exp.Array, exp.Struct)):
        return not inner.expressions
    if isinstance(inner, exp.ToMap):
        return isinstance(inner.this, exp.Struct) and not inner.this.expressions
    if isinstance(inner, (exp.Map, exp.VarMap)):
        keys = inner.args.get("keys")
        vals = inner.args.get("values")
        return (
            isinstance(keys, exp.Array)
            and not keys.expressions
            and isinstance(vals, exp.Array)
            and not vals.expressions
        )
    if isinstance(inner, exp.ParseJSON):
        return (
            isinstance(inner.this, exp.Literal)
            and inner.this.is_string
            and str(inner.this.this) in ("[]", "{}")
        )
    return False


def _is_ghost_default_expr(col_name: str, node: exp.Expr | None) -> bool:
    if _is_zero_key_expr(node) or _is_ghost_ts_expr(node):
        return True
    inner = _unwrap_seed_casts(node)
    if inner is None:
        return False
    if isinstance(inner, exp.Null):
        return True
    if isinstance(inner, exp.Boolean):
        return inner.this is False
    if _is_empty_collection_expr(inner):
        return True
    if isinstance(inner, exp.Neg) and isinstance(inner.this, exp.Literal):
        return not inner.this.is_string and str(inner.this.this) in ("1", "2")
    if isinstance(inner, exp.Literal):
        if not inner.is_string:
            return str(inner.this) in ("-1", "-2", "0")
        val = str(inner.this)
        return val in ("UNKNOWN", "ERROR", "{}", "[]") or (
            col_name == "bkcc" and val == "GLOBAL"
        )
    return False


def _is_valid_seed_col_value(col_name: str, node: exp.Expr | None) -> bool:
    c = col_name.lower()
    if c.startswith(_KEY_COL_PREFIXES):
        return _is_zero_key_expr(node)
    if c in ("rec_src", "record_source"):
        inner = _unwrap_parens(node.this if isinstance(node, exp.Alias) else node)
        return (
            isinstance(inner, exp.Literal)
            and inner.is_string
            and str(inner.this) == "SYSTEM_SEED"
        )
    if c in ("load_dts", "applied_dts"):
        return _is_ghost_ts_expr(node)
    return _is_ghost_default_expr(c, node)


def _validate_seed_pairs(pairs: list[tuple[str | None, exp.Expr]]) -> bool:
    if not pairs:
        return False
    has_rec_src = False
    has_hk = False
    for col_name, expr in pairs:
        if not col_name or not _is_valid_seed_col_value(col_name, expr):
            return False
        c = col_name.lower()
        if c in ("rec_src", "record_source"):
            has_rec_src = True
        if c.startswith("hk_"):
            has_hk = True
    return has_rec_src and has_hk


def _extract_literal_seed_select(sel: exp.Expr | None) -> exp.Select | None:
    if not isinstance(sel, exp.Select):
        return None
    if (
        sel.args.get("with_") is not None
        or sel.args.get("from_") is not None
        or sel.args.get("joins")
        or sel.args.get("where") is not None
        or sel.args.get("group") is not None
        or sel.args.get("having") is not None
        or sel.args.get("qualify") is not None
        or sel.args.get("prewhere") is not None
        or sel.args.get("laterals")
        or sel.args.get("pivots")
        or sel.args.get("sample")
        or sel.args.get("match") is not None
    ):
        return None
    if any(
        isinstance(n, (exp.Table, exp.Column, exp.Select, exp.Subquery))
        for n in sel.walk()
        if n is not sel
    ):
        return None
    return sel


def _build_vsel_alias_map(
    vsel: exp.Select,
) -> tuple[list[tuple[str, exp.Expr]], dict[str, exp.Expr]] | None:
    cols: list[tuple[str, exp.Expr]] = []
    cmap: dict[str, exp.Expr] = {}
    for p in vsel.expressions:
        if not isinstance(p, exp.Alias) or not p.alias:
            return None
        al = p.alias.lower()
        if al in cmap:
            return None
        cols.append((al, p.this))
        cmap[al] = p.this
    return cols, cmap


def _has_col_alias_list(node: exp.Expr | None) -> bool:
    if node is None:
        return False
    alias_node = node.args.get("alias")
    return isinstance(alias_node, exp.TableAlias) and bool(alias_node.columns)


def _has_unsafe_subquery_args(node: exp.Subquery) -> bool:
    return any(
        bool(node.args.get(k))
        for k in (
            "with_",
            "joins",
            "where",
            "group",
            "having",
            "qualify",
            "prewhere",
            "laterals",
            "pivots",
            "sample",
            "match",
        )
    )


def is_zero_key_system_seed(
    stmt: exp.Expr,
    target_names: set[str] | frozenset[str] = frozenset(),
) -> bool:
    """Return True iff stmt is strictly a literal-only -1/-2 Zero-Key SYSTEM_SEED seed statement."""
    if stmt.args.get("with_") is not None or not target_names:
        return False
    if any(bool(ta.columns) for ta in stmt.find_all(exp.TableAlias)):
        return False
    if (
        stmt.find(exp.Pivot, exp.TableSample, exp.Lateral, exp.Unnest, exp.MatchRecognize)
        is not None
    ):
        return False

    if isinstance(stmt, exp.Insert):
        outer = stmt.expression
        if not isinstance(outer, exp.Select) or outer.args.get("with_") is not None:
            return False
        if (
            outer.args.get("group") is not None
            or outer.args.get("having") is not None
            or outer.args.get("qualify") is not None
            or outer.args.get("prewhere") is not None
            or outer.args.get("laterals")
            or outer.args.get("pivots")
            or outer.args.get("sample")
            or outer.args.get("match") is not None
        ):
            return False

        if not isinstance(stmt.this, exp.Schema):
            return False
        target_cols = [c.name.lower() for c in stmt.this.expressions]
        if not target_cols or any(not c for c in target_cols):
            return False

        from_ = outer.args.get("from_")
        outer_pairs: list[tuple[str | None, exp.Expr]] = []
        if from_ is None:
            if outer.args.get("joins") or outer.args.get("where") is not None:
                return False
            vsel = _extract_literal_seed_select(outer)
            if vsel is None:
                return False
            for p in vsel.expressions:
                if isinstance(p, exp.Alias):
                    outer_pairs.append((p.alias.lower() if p.alias else None, p.this))
                else:
                    outer_pairs.append((None, p))
        elif (
            isinstance(from_, exp.From)
            and isinstance(from_.this, exp.Subquery)
            and not _has_unsafe_subquery_args(from_.this)
            and not _has_col_alias_list(from_.this)
        ):
            vsel = _extract_literal_seed_select(from_.this.this)
            if vsel is None:
                return False
            built = _build_vsel_alias_map(vsel)
            if built is None:
                return False
            sub_cols, sub_map = built
            src_alias = (from_.this.alias or "").lower()
            used_sub_cols: set[str] = set()
            for proj in outer.expressions:
                if isinstance(proj, exp.Star):
                    if outer.args.get("joins") or any(
                        bool(proj.args.get(k)) for k in ("except_", "replace", "rename", "ilike")
                    ):
                        return False
                    outer_pairs.extend(sub_cols)
                    used_sub_cols.update(sub_map)
                    continue
                if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                    if (
                        any(
                            bool(proj.this.args.get(k))
                            for k in ("except_", "replace", "rename", "ilike")
                        )
                        or proj.db
                        or (proj.table and (not src_alias or proj.table.lower() != src_alias))
                    ):
                        return False
                    outer_pairs.extend(sub_cols)
                    used_sub_cols.update(sub_map)
                    continue
                inner_p = proj.this if isinstance(proj, exp.Alias) else proj
                if (
                    not isinstance(inner_p, exp.Column)
                    or isinstance(inner_p.this, exp.Star)
                    or not inner_p.name
                ):
                    return False
                if inner_p.db or (
                    inner_p.table and (not src_alias or inner_p.table.lower() != src_alias)
                ):
                    return False
                cname = inner_p.name.lower()
                if cname not in sub_map:
                    return False
                used_sub_cols.add(cname)
                eff_alias = (
                    proj.alias.lower() if isinstance(proj, exp.Alias) and proj.alias else cname
                )
                outer_pairs.append((eff_alias, sub_map[cname]))

            for cname, expr in sub_cols:
                if cname not in used_sub_cols and not _is_valid_seed_col_value(cname, expr):
                    return False

            for j in outer.args.get("joins") or ():
                if (
                    not isinstance(j, exp.Join)
                    or (j.side or "").upper() not in ("", "LEFT")
                    or (j.kind or "").upper() != "ANTI"
                    or not isinstance(j.this, exp.Table)
                    or j.this.args.get("joins")
                    or (j.this.name or "").lower() not in target_names
                ):
                    return False
            w = outer.args.get("where")
            if w is not None:
                w_inner = _unwrap_parens(w.this if isinstance(w, exp.Where) else w)
                if not isinstance(w_inner, exp.Not) or not isinstance(w_inner.this, exp.Exists):
                    return False
                if any(
                    (t.name or "").lower() not in target_names
                    for t in w_inner.find_all(exp.Table)
                ):
                    return False
        else:
            return False

        if len(target_cols) != len(outer_pairs):
            return False
        final_pairs: list[tuple[str | None, exp.Expr]] = [
            (tcol, expr) for tcol, (_, expr) in zip(target_cols, outer_pairs, strict=True)
        ]
        return _validate_seed_pairs(final_pairs)

    if isinstance(stmt, exp.Merge):
        using = stmt.args.get("using")
        if (
            not isinstance(using, exp.Subquery)
            or _has_unsafe_subquery_args(using)
            or _has_col_alias_list(using)
        ):
            return False
        vsel = _extract_literal_seed_select(using.this)
        if vsel is None:
            return False
        built = _build_vsel_alias_map(vsel)
        if built is None:
            return False
        sub_cols, sub_map = built
        using_alias = (using.alias or "").lower()

        if any((t.name or "").lower() not in target_names for t in stmt.find_all(exp.Table)):
            return False
        if stmt.find(exp.Update, exp.Delete) is not None:
            return False

        whens_node = stmt.args.get("whens")
        whens = (
            [w for w in whens_node.expressions if isinstance(w, exp.When)]
            if isinstance(whens_node, exp.Whens)
            else list(stmt.find_all(exp.When))
        )
        insert_whens = [
            w
            for w in whens
            if (then_n := w.args.get("then")) is not None
            and (isinstance(then_n, exp.Insert) or then_n.find(exp.Insert) is not None)
        ]
        if not insert_whens:
            return False
        for w in whens:
            then_n = w.args.get("then")
            if isinstance(then_n, exp.Var) and str(then_n.this).upper() == "DELETE":
                return False
        merge_used_cols: set[str] = set()
        for w in insert_whens:
            then_n = w.args.get("then")
            if then_n is None:
                return False
            ins = then_n if isinstance(then_n, exp.Insert) else then_n.find(exp.Insert)
            if not isinstance(ins, exp.Insert):
                return False
            if not isinstance(ins.this, (exp.Tuple, exp.Schema)) or not ins.this.expressions:
                return False
            tcols: list[str] = []
            for c in ins.this.expressions:
                if not isinstance(c, (exp.Column, exp.Identifier)) or not c.name:
                    return False
                tcols.append(c.name.lower())
            if isinstance(ins.expression, exp.Tuple):
                val_exprs = list(ins.expression.expressions)
            elif (
                isinstance(ins.expression, exp.Values)
                and len(ins.expression.expressions) == 1
                and isinstance(ins.expression.expressions[0], exp.Tuple)
            ):
                val_exprs = list(ins.expression.expressions[0].expressions)
            else:
                return False
            if len(tcols) != len(val_exprs):
                return False
            pairs: list[tuple[str | None, exp.Expr]] = []
            for tcol, v_raw in zip(tcols, val_exprs, strict=True):
                v_un = _unwrap_parens(v_raw)
                if isinstance(v_un, exp.Tuple) and len(v_un.expressions) == 1:
                    v_un = _unwrap_parens(v_un.expressions[0])
                if isinstance(v_un, exp.Column):
                    if v_un.db or (
                        v_un.table and (not using_alias or v_un.table.lower() != using_alias)
                    ):
                        return False
                    cname = v_un.name.lower()
                    if cname not in sub_map:
                        return False
                    merge_used_cols.add(cname)
                    pairs.append((tcol, sub_map[cname]))
                elif v_un is not None:
                    pairs.append((tcol, v_un))
                else:
                    return False
            if not _validate_seed_pairs(pairs):
                return False
        for cname, expr in sub_cols:
            if cname not in merge_used_cols and not _is_valid_seed_col_value(cname, expr):
                return False
        return True

    return False


__all__ = ["is_zero_key_system_seed"]
