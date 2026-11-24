"""Stateless AST container-extraction & transform classifiers for `_bool_expr_resolver.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`, and `_bool_expr_containers`.
"""

from __future__ import annotations

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import is_slice_node
from opendv.storage._bool_expr_quantiles import classify_seq_transform
from opendv.storage._bool_expr_vocab import (
    ARRAY_EXT_ANON,
    classify_list_aggr,
    is_json_extract_node,
    unpack_json_path_steps,
)

_STRUCT_EXTRACT_CLS = getattr(exp, "StructExtract", None)
_ARRAY_FIRST_CLS = getattr(exp, "ArrayFirst", None)
_ARRAY_LAST_CLS = getattr(exp, "ArrayLast", None)
_ARRAY_MAX_CLS = getattr(exp, "ArrayMax", None)
_ARRAY_MIN_CLS = getattr(exp, "ArrayMin", None)
_GET_EXTRACT_CLS = getattr(exp, "GetExtract", None)
_ARRAY_CONCAT_CLS = getattr(exp, "ArrayConcat", None)
_ARRAY_APPEND_CLS = getattr(exp, "ArrayAppend", None)
_ARRAY_PREPEND_CLS = getattr(exp, "ArrayPrepend", None)
_ARRAY_INSERT_CLS = getattr(exp, "ArrayInsert", None)
_ARRAY_INTERSECT_CLS = getattr(exp, "ArrayIntersect", None)
_ARRAY_EXCEPT_CLS = getattr(exp, "ArrayExcept", None)
_ARRAY_FILTER_CLS = getattr(exp, "ArrayFilter", None)
_TRANSFORM_CLS = getattr(exp, "Transform", None)
_REDUCE_CLS = getattr(exp, "Reduce", None)
_ARRAYS_ZIP_CLS = getattr(exp, "ArraysZip", None)
_MAP_CAT_CLS = getattr(exp, "MapCat", None)
_MAP_CONCAT_CLS = getattr(exp, "MapConcat", None)
_MAP_KEYS_CLS = getattr(exp, "MapKeys", None)
_MAP_TO_ENTRIES_CLS = getattr(exp, "MapToEntries", None)
_OBJECT_INSERT_CLS = getattr(exp, "ObjectInsert", None)
_FLATTEN_CLS = getattr(exp, "Flatten", None)
_MAP_FROM_ENTRIES_CLS = getattr(exp, "MapFromEntries", None)
_MAP_CLASSES: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "Map", None), getattr(exp, "VarMap", None)) if c is not None
)


_ARRAY_SLICE_CLS = getattr(exp, "ArraySlice", None)
_GENERATE_SERIES_CLS = getattr(exp, "GenerateSeries", None)


def extract_index_expr(node: exp.Expr) -> tuple[exp.Expr | None, str] | None:
    if isinstance(node, exp.Bracket) and len(node.expressions) == 1:
        mode = "bracket_1b" if node.args.get("offset") == 1 else "bracket_0b"
        return node.expressions[0], mode
    if _ARRAY_FIRST_CLS is not None and isinstance(node, _ARRAY_FIRST_CLS):
        return None, "first"
    if _ARRAY_LAST_CLS is not None and isinstance(node, _ARRAY_LAST_CLS):
        return None, "last"
    if _GET_EXTRACT_CLS is not None and isinstance(node, _GET_EXTRACT_CLS):
        return node.expression, "anon_0b"
    if isinstance(node, exp.Anonymous):
        un = node.name.upper()
        if un in ("ARRAY_FIRST", "LIST_FIRST"):
            return None, "first"
        if un in ("ARRAY_LAST", "LIST_LAST"):
            return None, "last"
        if un in (
            "LIST_EXTRACT", "ELEMENT_AT", "ARRAY_EXTRACT", "LIST_ELEMENT",
        ) and len(node.expressions) >= 2:
            return node.expressions[1], "anon_1b"
        if un == "ARRAYELEMENT" and len(node.expressions) >= 2:
            return node.expressions[1], "ch_1b"
        if un == "GET" and len(node.expressions) >= 2:
            return node.expressions[1], "anon_0b"
    return None


def extract_lookup_key_expr(node: exp.Expr) -> exp.Expr | None:
    if isinstance(node, exp.Bracket) and len(node.expressions) == 1:
        e = node.expressions[0]
        return e if isinstance(e, exp.Expr) else None
    if (_STRUCT_EXTRACT_CLS is not None and isinstance(node, _STRUCT_EXTRACT_CLS)) or (
        _GET_EXTRACT_CLS is not None and isinstance(node, _GET_EXTRACT_CLS)
    ):
        e = node.expression
        return e if isinstance(e, exp.Expr) else None
    if isinstance(node, exp.Anonymous) and node.name.upper() in (
        "STRUCT_EXTRACT", "GET", "ELEMENT_AT", "ARRAY_EXTRACT", "LIST_EXTRACT", "ARRAYELEMENT",
    ):
        e = node.expressions[1] if len(node.expressions) >= 2 else None
        return e if isinstance(e, exp.Expr) else None
    return None


def extract_container_spec(node: exp.Expr) -> tuple[exp.Expr | None, str | None] | None:
    if isinstance(node, exp.Dot):
        fk = (
            node.expression.name
            if isinstance(node.expression, (exp.Identifier, exp.Column)) and node.expression.name
            else None
        )
        return node.this, fk
    if _STRUCT_EXTRACT_CLS is not None and isinstance(node, _STRUCT_EXTRACT_CLS):
        fk = (
            str(node.expression.this)
            if isinstance(node.expression, exp.Literal) and node.expression.is_string
            else None
        )
        return node.this, fk
    if isinstance(node, exp.Bracket):
        fk = (
            str(node.expressions[0].this)
            if len(node.expressions) == 1
            and isinstance(node.expressions[0], exp.Literal)
            and node.expressions[0].is_string
            else None
        )
        return node.this, fk
    if any(
        cls is not None and isinstance(node, cls)
        for cls in (_ARRAY_FIRST_CLS, _ARRAY_LAST_CLS, _ARRAY_MAX_CLS, _ARRAY_MIN_CLS)
    ):
        return node.this, None
    if _GET_EXTRACT_CLS is not None and isinstance(node, _GET_EXTRACT_CLS):
        fk = (
            str(node.expression.this)
            if isinstance(node.expression, exp.Literal) and node.expression.is_string
            else None
        )
        return node.this, fk
    if isinstance(node, exp.Anonymous):
        un = node.name.upper()
        if un in ARRAY_EXT_ANON and node.expressions:
            return node.expressions[0], None
        if un in ("STRUCT_EXTRACT", "GET") and node.expressions:
            fk = (
                str(node.expressions[1].this)
                if len(node.expressions) >= 2
                and isinstance(node.expressions[1], exp.Literal)
                and node.expressions[1].is_string
                else None
            )
            return node.expressions[0], fk
    return None


def classify_container_transform(
    u: exp.Expr,
) -> tuple[str, tuple[exp.Expr | None, ...], str | None] | None:
    if is_slice_node(u):
        if isinstance(u, exp.Bracket):
            sl = next((e for e in u.expressions if isinstance(e, exp.Slice)), None)
            if sl is not None and u.this is not None:
                return "slice_1b", (u.this, sl.this, sl.expression, sl.args.get("step")), None
            return ("pass", (u.this,), None) if u.this is not None else ("empty", (), None)
        if _ARRAY_SLICE_CLS is not None and isinstance(u, _ARRAY_SLICE_CLS):
            sk = "slice_0b" if u.args.get("zero_based") else "slice_1b"
            return (
                (sk, (u.this, u.args.get("start"), u.args.get("end"), u.args.get("step")), None)
                if u.this is not None
                else ("empty", (), None)
            )
        if isinstance(u, exp.Anonymous) and u.expressions:
            return (
                "slice_1b",
                (
                    u.expressions[0],
                    u.expressions[1] if len(u.expressions) >= 2 else None,
                    u.expressions[2] if len(u.expressions) >= 3 else None,
                    u.expressions[3] if len(u.expressions) >= 4 else None,
                ),
                None,
            )
        return ("empty", (), None)
    if _GENERATE_SERIES_CLS is not None and isinstance(u, _GENERATE_SERIES_CLS):
        rk = "range_excl" if u.args.get("is_end_exclusive") else "range_incl"
        return rk, (u.args.get("start"), u.args.get("end"), u.args.get("step")), None
    if isinstance(u, (exp.Explode, exp.Unnest)):
        args = (u.this,) if isinstance(u, exp.Explode) else tuple(u.expressions)
        return "unnest", args, None
    if isinstance(u, exp.DPipe):
        return "dpipe", (u.this, u.expression), None
    if isinstance(u, exp.Concat) and not isinstance(u, exp.ConcatWs):
        return "concat_maybe", tuple(a for a in u.expressions if a is not None), None
    if isinstance(u, exp.Repeat):
        return "repeat", (u.this, u.args.get("times")), None
    if _MAP_KEYS_CLS is not None and isinstance(u, _MAP_KEYS_CLS):
        return "map_keys", (u.this,), None
    if _ARRAY_CONCAT_CLS is not None and isinstance(u, _ARRAY_CONCAT_CLS):
        return "concat", tuple(a for a in (u.this, *u.expressions) if a is not None), None
    if _ARRAY_INTERSECT_CLS is not None and isinstance(u, _ARRAY_INTERSECT_CLS):
        return "intersect", tuple(a for a in (u.this, *u.expressions) if a is not None), None
    if (_MAP_CAT_CLS is not None and isinstance(u, _MAP_CAT_CLS)) or (
        _MAP_CONCAT_CLS is not None and isinstance(u, _MAP_CONCAT_CLS)
    ):
        cat_args = tuple(a for a in (u.this, u.expression, *u.expressions) if a is not None)
        return "concat", cat_args, None
    if _ARRAY_APPEND_CLS is not None and isinstance(u, _ARRAY_APPEND_CLS):
        return "append", (u.this, u.expression), None
    if _ARRAY_INSERT_CLS is not None and isinstance(u, _ARRAY_INSERT_CLS):
        return "append", (u.this, u.expression), None
    if _ARRAY_PREPEND_CLS is not None and isinstance(u, _ARRAY_PREPEND_CLS):
        return "prepend", (u.this, u.expression), None
    if (seq_tx := classify_seq_transform(u)) is not None:
        return seq_tx
    if _ARRAY_FILTER_CLS is not None and isinstance(u, _ARRAY_FILTER_CLS):
        return "filter", (u.this, u.expression), None
    if _TRANSFORM_CLS is not None and isinstance(u, _TRANSFORM_CLS):
        return "transform", (u.this, u.expression), None
    if _REDUCE_CLS is not None and isinstance(u, _REDUCE_CLS):
        r_args = (u.this, u.args.get("initial"), u.args.get("merge"), u.args.get("finish"))
        return "reduce", r_args, None
    if _ARRAYS_ZIP_CLS is not None and isinstance(u, _ARRAYS_ZIP_CLS):
        return "zip", tuple(a for a in (u.this, *u.expressions) if a is not None), None
    if _FLATTEN_CLS is not None and isinstance(u, _FLATTEN_CLS):
        return "flatten", (u.this,), None
    if _MAP_FROM_ENTRIES_CLS is not None and isinstance(u, _MAP_FROM_ENTRIES_CLS):
        return "map_entries", (u.this,), None
    if _MAP_TO_ENTRIES_CLS is not None and isinstance(u, _MAP_TO_ENTRIES_CLS):
        return "map_to_entries", (u.this,), None
    if _OBJECT_INSERT_CLS is not None and isinstance(u, _OBJECT_INSERT_CLS):
        k_n, v_n = u.args.get("key"), u.args.get("value")
        if k_n is not None and v_n is not None:
            return "struct_insert", (u.this, exp.PropertyEQ(this=k_n, expression=v_n)), None
        return "struct_insert", (u.this,), None
    if isinstance(u, exp.If):
        return "if", (u.this, u.args.get("true"), u.args.get("false")), None
    if isinstance(u, exp.Coalesce):
        return "coalesce", tuple(a for a in (u.this, *u.expressions) if a is not None), None
    if isinstance(u, exp.Anonymous):
        un = u.name.upper()
        if un in (
            "ARRAY_CONCAT", "LIST_CONCAT", "ARRAY_CAT", "LIST_CAT", "MAP_CONCAT", "MAP_CAT",
        ):
            return "concat", tuple(u.expressions), None
        if un in ("ARRAY_INTERSECT", "LIST_INTERSECT"):
            return "intersect", tuple(u.expressions), None
        if un in ("ARRAY_APPEND", "LIST_APPEND", "ARRAY_PUSH_BACK", "LIST_PUSH_BACK") and len(
            u.expressions
        ) >= 2:
            return "append", (u.expressions[0], u.expressions[1]), None
        if un in ("ARRAY_INSERT", "LIST_INSERT") and len(u.expressions) >= 3:
            return "append", (u.expressions[0], u.expressions[2]), None
        if un in ("ARRAY_PREPEND", "LIST_PREPEND", "ARRAY_PUSH_FRONT", "LIST_PUSH_FRONT") and len(
            u.expressions
        ) >= 2:
            return "prepend", (u.expressions[0], u.expressions[1]), None
        if un == "CONCAT" and u.expressions:
            return "concat_maybe", tuple(u.expressions), None
        if un == "REPEAT" and u.expressions:
            r_cnt = u.expressions[1] if len(u.expressions) >= 2 else None
            return "repeat", (u.expressions[0], r_cnt), None
        if un == "MAP_KEYS" and u.expressions:
            return "map_keys", (u.expressions[0],), None
        if un in ("LIST_RESIZE", "ARRAY_RESIZE") and u.expressions:
            return (
                "resize",
                (
                    u.expressions[0],
                    u.expressions[1] if len(u.expressions) >= 2 else None,
                    u.expressions[2] if len(u.expressions) >= 3 else None,
                ),
                None,
            )
        if un in ("LIST_FILTER", "ARRAY_FILTER", "FILTER") and len(u.expressions) >= 2:
            return "filter", (u.expressions[0], u.expressions[1]), None
        if un in (
            "LIST_TRANSFORM", "ARRAY_TRANSFORM", "LIST_APPLY", "ARRAY_APPLY", "TRANSFORM",
        ) and len(u.expressions) >= 2:
            return "transform", (u.expressions[0], u.expressions[1]), None
        if un in ("LIST_REDUCE", "ARRAY_REDUCE", "REDUCE") and len(u.expressions) >= 2:
            if len(u.expressions) == 2:
                return "reduce", (u.expressions[0], None, u.expressions[1], None), None
            fin = u.expressions[3] if len(u.expressions) >= 4 else None
            if isinstance(unwrap_parens(u.expressions[1]), exp.Lambda):
                return "reduce", (u.expressions[0], u.expressions[2], u.expressions[1], fin), None
            return "reduce", (u.expressions[0], u.expressions[1], u.expressions[2], fin), None
        if un in ("LIST_ZIP", "ARRAY_ZIP", "ARRAYS_ZIP") and u.expressions:
            return "zip", tuple(u.expressions), None
        if un in ("FLATTEN", "LIST_FLATTEN", "ARRAY_FLATTEN") and u.expressions:
            return "flatten", (u.expressions[0],), None
        if un in (
            "LIST_AGGR", "ARRAY_AGGR", "LIST_AGGREGATE", "ARRAY_AGGREGATE", "AGGREGATE",
        ) and len(u.expressions) >= 2 and classify_list_aggr(u.expressions[1]) == "list":
            return "list_aggr_list", (u.expressions[0],), None
        if un == "MAP_VALUES" and u.expressions:
            return "map_vals", (u.expressions[0],), None
        if un == "MAP_EXTRACT" and len(u.expressions) >= 2:
            return "map_vals", (u.expressions[0], u.expressions[1]), None
        if un == "MAP_FROM_ENTRIES" and u.expressions:
            return "map_entries", (u.expressions[0],), None
        if un == "MAP_ENTRIES" and u.expressions:
            return "map_to_entries", (u.expressions[0],), None
        if un == "STRUCT_INSERT" and u.expressions:
            return "struct_insert", tuple(u.expressions), None
        if un == "OBJECT_INSERT" and len(u.expressions) >= 3:
            pe = exp.PropertyEQ(this=u.expressions[1], expression=u.expressions[2])
            return "struct_insert", (u.expressions[0], pe), None
        if un in ("IF", "IFF", "IIF") and len(u.expressions) >= 2:
            fb = u.expressions[2] if len(u.expressions) >= 3 else None
            return "if", (u.expressions[0], u.expressions[1], fb), None
    return None


__all__ = [
    "classify_container_transform",
    "extract_container_spec",
    "extract_index_expr",
    "extract_lookup_key_expr",
    "is_json_extract_node",
    "unpack_json_path_steps",
]
