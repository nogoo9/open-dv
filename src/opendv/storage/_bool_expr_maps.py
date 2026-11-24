"""Struct, Map, and JSON-object container unpacking helpers for `_bool_expr_*`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`, and
`_bool_expr_containers`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import SmapType, is_array_ctor
from opendv.storage._bool_expr_vocab import UNKNOWN, same_const_val

EvalLitFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], object
]

_ARRAY_COMPACT_CTOR_CLS = getattr(exp, "ArrayConstructCompact", None)
_JSON_OBJECT_CLS = getattr(exp, "JSONObject", None)
_JSON_KV_CLS = getattr(exp, "JSONKeyValue", None)
_JSON_OBJ_ANON: frozenset[str] = frozenset({
    "JSON_BUILD_OBJECT", "JSONB_BUILD_OBJECT", "JSON_OBJECT", "OBJECT_CONSTRUCT_KEEP_NULL",
})
_MAP_CLASSES: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "Map", None), getattr(exp, "VarMap", None)) if c is not None
)
_MAP_FROM_ENTRIES_CLS = getattr(exp, "MapFromEntries", None)


def extract_struct_field(st: exp.Struct, field_key: str | None) -> list[exp.Expr]:
    out: list[exp.Expr] = []
    for fe in st.expressions:
        key: str | None = None
        val: exp.Expr | None = None
        if isinstance(fe, exp.PropertyEQ):
            if isinstance(fe.this, (exp.Identifier, exp.Var)):
                key = fe.this.name.lower()
            elif isinstance(fe.this, exp.Literal):
                key = str(fe.this.this).lower()
            val = fe.expression
        elif isinstance(fe, exp.Property):
            key = fe.name.lower() if fe.name else None
            val = fe.args.get("value") if isinstance(fe.args.get("value"), exp.Expr) else None
        elif isinstance(fe, exp.Alias):
            key = fe.alias.lower() if fe.alias else None
            val = fe.this
        elif isinstance(fe, exp.Expr):
            val = fe
        if val is not None and (field_key is None or key == field_key):
            out.append(val)
    return out


def _match_map_kv(
    keys: Sequence[exp.Expr], vals: Sequence[exp.Expr], field_key: str | int | None,
) -> list[exp.Expr]:
    if isinstance(field_key, str):
        if len(keys) == len(vals):
            lit_keys = [unwrap_parens(k) for k in keys]
            if all(isinstance(k, exp.Literal) and k.is_string for k in lit_keys):
                return [
                    v
                    for k, v in zip(lit_keys, vals, strict=True)
                    if isinstance(k, exp.Literal) and str(k.this) == field_key
                ]
        return [*vals, exp.Null()]
    return list(vals)


def is_map_ctor(ex_u: exp.Expr | None) -> bool:
    if ex_u is None:
        return False
    return (
        isinstance(ex_u, exp.ToMap)
        or bool(_MAP_CLASSES and isinstance(ex_u, _MAP_CLASSES))
        or (_MAP_FROM_ENTRIES_CLS is not None and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS))
        or (isinstance(ex_u, exp.Anonymous) and ex_u.name.upper() in ("MAP", "MAP_FROM_ENTRIES"))
    )


def match_map_by_key_expr(
    ex_u: exp.Expr | None,
    key_expr: exp.Expr | None,
    field_key: str | None,
    eval_lit_fn: EvalLitFn | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> tuple[list[exp.Expr], bool] | None:
    if not is_map_ctor(ex_u):
        return None
    kv = extract_map_kv_pairs(ex_u)
    if kv is None:
        return [], True
    keys, vals = kv
    if len(keys) != len(vals):
        return list(vals), True
    if key_expr is not None and eval_lit_fn is not None:
        target_k = eval_lit_fn(key_expr, ctes, vis, smap)
        if target_k is None:
            return [], True
        if target_k is UNKNOWN:
            return list(vals), True
        matched: list[exp.Expr] = []
        has_unk = False
        for k, v in zip(keys, vals, strict=True):
            kv_lit = eval_lit_fn(k, ctes, vis, smap)
            if kv_lit is UNKNOWN:
                has_unk = True
                matched.append(v)
            elif kv_lit is not None and same_const_val(kv_lit, target_k):
                matched.append(v)
        return matched, has_unk or not matched
    if field_key is not None:
        out = _match_map_kv(keys, vals, field_key)
        return out, not out
    return list(vals), False


def map_from_entries_kv(arr_node: exp.Expr | None) -> tuple[list[exp.Expr], list[exp.Expr]] | None:
    arr_u = unwrap_parens(arr_node)
    if not is_array_ctor(arr_u) or arr_u is None:
        return None
    keys: list[exp.Expr] = []
    vals: list[exp.Expr] = []
    for item in arr_u.expressions:
        iu = unwrap_parens(item)
        if (
            isinstance(iu, (exp.Tuple, exp.Array))
            or (isinstance(iu, exp.Anonymous) and iu.name.upper() in ("ROW", "STRUCT"))
        ) and len(iu.expressions) == 2:
            keys.append(iu.expressions[0])
            vals.append(iu.expressions[1])
        elif isinstance(iu, exp.Struct) and len(sf := extract_struct_field(iu, None)) == 2:
            keys.append(sf[0])
            vals.append(sf[1])
        else:
            return None
    return keys, vals


def extract_map_keys(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.ToMap):
        st = unwrap_parens(node.this)
        if not isinstance(st, exp.Struct):
            return []
        keys: list[exp.Expr] = []
        for fe in st.expressions:
            if isinstance(fe, exp.PropertyEQ) and fe.this is not None:
                keys.append(fe.this)
            elif isinstance(fe, exp.Alias) and fe.alias:
                keys.append(exp.Literal.string(fe.alias))
        return keys
    if _MAP_CLASSES and isinstance(node, _MAP_CLASSES):
        keys_n = unwrap_parens(node.args.get("keys"))
        if isinstance(keys_n, exp.Array):
            return list(keys_n.expressions)
        return [keys_n] if keys_n is not None else []
    if (_MAP_FROM_ENTRIES_CLS is not None and isinstance(node, _MAP_FROM_ENTRIES_CLS)) or (
        isinstance(node, exp.Anonymous) and node.name.upper() == "MAP_FROM_ENTRIES"
    ):
        arg = node.this if _MAP_FROM_ENTRIES_CLS and isinstance(node, _MAP_FROM_ENTRIES_CLS) else (
            node.expressions[0] if node.expressions else None
        )
        if (kv := map_from_entries_kv(arg)) is not None:
            return kv[0]
        return []
    if isinstance(node, exp.Anonymous) and node.name.upper() == "MAP":
        if len(node.expressions) == 2 and isinstance(
            keys_n := unwrap_parens(node.expressions[0]), exp.Array
        ):
            return list(keys_n.expressions)
        return list(node.expressions[0::2])
    return []


def extract_map_values(node: exp.Expr, field_key: str | int | None) -> list[exp.Expr]:
    if isinstance(node, exp.ToMap):
        kv = extract_map_kv_pairs(node)
        if kv is not None:
            return _match_map_kv(kv[0], kv[1], field_key)
        st = unwrap_parens(node.this)
        if isinstance(st, exp.Struct):
            return extract_struct_field(st, field_key if isinstance(field_key, str) else None)
        return []
    if _MAP_CLASSES and isinstance(node, _MAP_CLASSES):
        keys_n = unwrap_parens(node.args.get("keys"))
        vals_n = unwrap_parens(node.args.get("values"))
        if isinstance(vals_n, exp.Array):
            if isinstance(keys_n, exp.Array):
                return _match_map_kv(keys_n.expressions, vals_n.expressions, field_key)
            vs = list(vals_n.expressions)
            return [*vs, exp.Null()] if isinstance(field_key, str) else vs
        if vals_n is None:
            return []
        return [vals_n, exp.Null()] if isinstance(field_key, str) else [vals_n]
    if (_MAP_FROM_ENTRIES_CLS is not None and isinstance(node, _MAP_FROM_ENTRIES_CLS)) or (
        isinstance(node, exp.Anonymous) and node.name.upper() == "MAP_FROM_ENTRIES"
    ):
        arg = node.this if _MAP_FROM_ENTRIES_CLS and isinstance(node, _MAP_FROM_ENTRIES_CLS) else (
            node.expressions[0] if node.expressions else None
        )
        if (kv := map_from_entries_kv(arg)) is not None:
            return _match_map_kv(kv[0], kv[1], field_key)
        return []
    if isinstance(node, exp.Anonymous) and node.name.upper() == "MAP":
        if len(node.expressions) == 2 and isinstance(
            vals_n := unwrap_parens(node.expressions[1]), exp.Array
        ):
            keys_n = unwrap_parens(node.expressions[0])
            if isinstance(keys_n, exp.Array):
                return _match_map_kv(keys_n.expressions, vals_n.expressions, field_key)
            vs = list(vals_n.expressions)
            return [*vs, exp.Null()] if isinstance(field_key, str) else vs
        return _match_map_kv(node.expressions[0::2], node.expressions[1::2], field_key)
    return []


def unpack_container_ctor(ex_u: exp.Expr | None, field_key: str | int | None) -> list[exp.Expr]:
    if ex_u is None:
        return []
    if is_array_ctor(ex_u):
        if field_key is None:
            return list(ex_u.expressions)
        if isinstance(field_key, int):
            n = len(ex_u.expressions)
            if 0 <= field_key < n:
                return [ex_u.expressions[field_key]]
            if -n <= field_key < 0:
                return [ex_u.expressions[n + field_key], exp.Null()]
            return []
        return []
    if isinstance(ex_u, exp.Struct):
        return extract_struct_field(ex_u, field_key.lower() if isinstance(field_key, str) else None)
    if isinstance(ex_u, exp.ToMap):
        return extract_map_values(ex_u, field_key)
    if _JSON_OBJECT_CLS is not None and isinstance(ex_u, _JSON_OBJECT_CLS):
        jks: list[exp.Expr] = []
        jvs: list[exp.Expr] = []
        for fe in ex_u.expressions:
            if (
                _JSON_KV_CLS is not None
                and isinstance(fe, _JSON_KV_CLS)
                and fe.this is not None
                and fe.expression is not None
            ):
                jks.append(fe.this)
                jvs.append(fe.expression)
            elif isinstance(fe, exp.Expr):
                return [exp.Null()]
        return _match_map_kv(jks, jvs, field_key)
    if isinstance(ex_u, exp.Anonymous) and ex_u.name.upper() in _JSON_OBJ_ANON:
        return _match_map_kv(ex_u.expressions[0::2], ex_u.expressions[1::2], field_key)
    if (
        (_MAP_CLASSES and isinstance(ex_u, _MAP_CLASSES))
        or (_MAP_FROM_ENTRIES_CLS is not None and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS))
        or (isinstance(ex_u, exp.Anonymous) and ex_u.name.upper() in ("MAP", "MAP_FROM_ENTRIES"))
    ):
        return extract_map_values(ex_u, field_key)
    return []


def is_empty_container_ctor(ex_u: exp.Expr | None) -> bool:
    if ex_u is None:
        return False
    if is_array_ctor(ex_u):
        return not ex_u.expressions
    if isinstance(ex_u, exp.Struct):
        return not ex_u.expressions
    if isinstance(ex_u, exp.ToMap):
        st = unwrap_parens(ex_u.this)
        return isinstance(st, exp.Struct) and not st.expressions
    if _MAP_CLASSES and isinstance(ex_u, _MAP_CLASSES):
        vals_n = unwrap_parens(ex_u.args.get("values"))
        return isinstance(vals_n, exp.Array) and not vals_n.expressions
    if (_MAP_FROM_ENTRIES_CLS is not None and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS)) or (
        isinstance(ex_u, exp.Anonymous) and node_is_mfe(ex_u)
    ):
        arg = ex_u.this if _MAP_FROM_ENTRIES_CLS and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS) else (
            ex_u.expressions[0] if ex_u.expressions else None
        )
        kv = map_from_entries_kv(arg)
        return kv is not None and not kv[0]
    if isinstance(ex_u, exp.Anonymous) and ex_u.name.upper() == "MAP":
        if not ex_u.expressions:
            return True
        if len(ex_u.expressions) == 2 and isinstance(
            vals_n := unwrap_parens(ex_u.expressions[1]), exp.Array
        ):
            return not vals_n.expressions
    return False


def node_is_mfe(ex_u: exp.Anonymous) -> bool:
    return ex_u.name.upper() == "MAP_FROM_ENTRIES"


def extract_map_kv_pairs(ex_u: exp.Expr | None) -> tuple[list[exp.Expr], list[exp.Expr]] | None:
    if ex_u is None:
        return None
    if isinstance(ex_u, exp.ToMap):
        st = unwrap_parens(ex_u.this)
        if not isinstance(st, exp.Struct):
            return None
        ks: list[exp.Expr] = []
        vs: list[exp.Expr] = []
        for fe in st.expressions:
            if isinstance(fe, exp.PropertyEQ) and fe.this is not None and fe.expression is not None:
                ks.append(fe.this)
                vs.append(fe.expression)
            elif isinstance(fe, exp.Alias) and fe.alias and fe.this is not None:
                ks.append(exp.Literal.string(fe.alias))
                vs.append(fe.this)
            else:
                return None
        return ks, vs
    if _MAP_CLASSES and isinstance(ex_u, _MAP_CLASSES):
        kn, vn = unwrap_parens(ex_u.args.get("keys")), unwrap_parens(ex_u.args.get("values"))
        if isinstance(kn, exp.Array) and isinstance(vn, exp.Array):
            return list(kn.expressions), list(vn.expressions)
    if (_MAP_FROM_ENTRIES_CLS is not None and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS)) or (
        isinstance(ex_u, exp.Anonymous) and node_is_mfe(ex_u)
    ):
        arg = ex_u.this if _MAP_FROM_ENTRIES_CLS and isinstance(ex_u, _MAP_FROM_ENTRIES_CLS) else (
            ex_u.expressions[0] if ex_u.expressions else None
        )
        return map_from_entries_kv(arg)
    if isinstance(ex_u, exp.Anonymous) and ex_u.name.upper() == "MAP":
        if not ex_u.expressions:
            return [], []
        if len(ex_u.expressions) == 2 and isinstance(
            kn := unwrap_parens(ex_u.expressions[0]), exp.Array
        ) and isinstance(vn := unwrap_parens(ex_u.expressions[1]), exp.Array):
            return list(kn.expressions), list(vn.expressions)
        if len(ex_u.expressions) % 2 == 0:
            return list(ex_u.expressions[0::2]), list(ex_u.expressions[1::2])
    return None


def _entry_struct(k: exp.Expr | None, v: exp.Expr) -> exp.Struct:
    fields: list[exp.Expr] = []
    if k is not None:
        fields.append(exp.PropertyEQ(this=exp.Identifier(this="key", quoted=False), expression=k))
    fields.append(exp.PropertyEQ(this=exp.Identifier(this="value", quoted=False), expression=v))
    return exp.Struct(expressions=fields)


def _zip_pairs(ks: Sequence[exp.Expr], vs: Sequence[exp.Expr]) -> list[exp.Struct]:
    if len(ks) == len(vs):
        return [_entry_struct(k, v) for k, v in zip(ks, vs, strict=True)]
    return [_entry_struct(None, v) for v in vs]


def map_to_entry_structs(node: exp.Expr | None) -> list[exp.Struct] | None:
    u = unwrap_parens(node)
    if u is None:
        return None
    if isinstance(u, exp.ToMap):
        st = unwrap_parens(u.this)
        if not isinstance(st, exp.Struct):
            return None
        out: list[exp.Struct] = []
        for fe in st.expressions:
            if isinstance(fe, exp.PropertyEQ) and fe.expression is not None:
                out.append(_entry_struct(fe.this, fe.expression))
            elif isinstance(fe, exp.Alias) and fe.this is not None:
                out.append(_entry_struct(exp.Literal.string(fe.alias), fe.this))
            elif isinstance(fe, exp.Expr):
                out.append(_entry_struct(None, fe))
        return out
    if _MAP_CLASSES and isinstance(u, _MAP_CLASSES):
        keys_n = unwrap_parens(u.args.get("keys"))
        vals_n = unwrap_parens(u.args.get("values"))
        if isinstance(vals_n, exp.Array):
            ks = keys_n.expressions if isinstance(keys_n, exp.Array) else ()
            return _zip_pairs(ks, vals_n.expressions)
        return [_entry_struct(keys_n, vals_n)] if vals_n is not None else []
    if (_MAP_FROM_ENTRIES_CLS is not None and isinstance(u, _MAP_FROM_ENTRIES_CLS)) or (
        isinstance(u, exp.Anonymous) and node_is_mfe(u)
    ):
        arg = u.this if _MAP_FROM_ENTRIES_CLS and isinstance(u, _MAP_FROM_ENTRIES_CLS) else (
            u.expressions[0] if u.expressions else None
        )
        if (kv := map_from_entries_kv(arg)) is not None:
            return _zip_pairs(kv[0], kv[1])
        return None
    if isinstance(u, exp.Anonymous) and u.name.upper() == "MAP":
        if len(u.expressions) == 2 and isinstance(vn := unwrap_parens(u.expressions[1]), exp.Array):
            kn = unwrap_parens(u.expressions[0])
            ks = kn.expressions if isinstance(kn, exp.Array) else ()
            return _zip_pairs(ks, vn.expressions)
        return _zip_pairs(u.expressions[0::2], u.expressions[1::2])
    if (vals := extract_map_values(u, None)):
        return [_entry_struct(None, v) for v in vals]
    return None


__all__ = [
    "extract_map_keys",
    "extract_map_kv_pairs",
    "extract_map_values",
    "extract_struct_field",
    "is_empty_container_ctor",
    "is_map_ctor",
    "map_from_entries_kv",
    "map_to_entry_structs",
    "match_map_by_key_expr",
    "unpack_container_ctor",
]
