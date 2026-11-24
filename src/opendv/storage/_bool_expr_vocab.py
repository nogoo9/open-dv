"""Static AST/function vocabularies and pure 3VL scalar primitives.

Strictly Layer 2a: imports only sqlglot and stdlib.
"""

from __future__ import annotations

import re

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens

BOOL_PREDICATE_EXPRS: tuple[type[exp.Expr], ...] = tuple(
    c
    for c in (
        exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike,
        exp.And, exp.Or, exp.Not, exp.Is, exp.In, exp.Between, exp.Boolean,
        *(
            getattr(exp, n, None)
            for n in (
                "Escape", "Exists", "RegexpLike", "RegexpILike", "RegexpFullMatch", "SimilarTo",
                "ToBoolean", "NullSafeEQ", "NullSafeNEQ", "Xor", "ArrayContains",
                "ArrayContainsAll", "ArrayOverlaps", "Booland", "Boolor", "Boolnot",
                "JSONBool", "JSONExists", "Glob", "IsNan", "IsInf", "Contains",
                "IsNullValue", "IsArray", "IsAscii", "BoolxorAgg", "ArrayAll",
                "ArrayAny", "ArrayContainedBy", "JSONArrayContains", "JSONBContains",
                "JSONBContainsAllTopKeys", "JSONBContainsAnyTopKeys", "JSONBContainsTopKey",
                "JSONBExists", "JSONBPathExists", "Overlaps", "MatchAgainst",
                "MapContainsKey", "Adjacent", "ExtendsLeft", "ExtendsRight", "Match",
            )
        ),
    )
    if c is not None
)

BOOL_FUNC_NAMES: frozenset[str] = frozenset({
    "REGEXP_CONTAINS", "REGEXP_LIKE", "REGEXP_ILIKE", "REGEXP_FULL_MATCH",
    "REGEXP_MATCHES", "CONTAINS", "CONTAINS_SUBSTR", "STARTS_WITH", "ENDS_WITH",
    "PREFIX", "SUFFIX", "TO_BOOLEAN", "TRY_TO_BOOLEAN", "BOOL", "BOOLEAN",
    "ARRAY_CONTAINS", "ARRAYS_OVERLAP", "ARRAY_OVERLAPS", "LIST_CONTAINS",
    "LIST_HAS", "LIST_HAS_ANY", "LIST_HAS_ALL", "BOOLXOR", "BOOLAND", "BOOLOR",
    "BOOLNOT", "BOOL_AND", "BOOL_OR", "BOOLAND_AGG", "BOOLOR_AGG", "EVERY",
    "BOOL_AND_AGG", "BOOL_OR_AGG", "BOOL_XOR_AGG",
    "LOGICAL_AND", "LOGICAL_OR", "EQUAL_NULL", "ISNAN", "IS_NAN", "ISFINITE",
    "IS_FINITE", "ISINF", "IS_INF", "JSON_EXISTS", "BOOLXOR_AGG",
    "IS_NULL_VALUE", "IS_BOOLEAN", "IS_ARRAY", "IS_OBJECT", "IS_INTEGER",
    "IS_DECIMAL", "IS_DOUBLE", "IS_VARCHAR", "IS_DATE", "IS_TIMESTAMP",
    "ISNULL", "ISNOTNULL", "ARRAY_ALL", "ARRAY_ANY", "JSONB_EXISTS",
    "JSONB_PATH_EXISTS", "LIST_BOOL_OR", "LIST_BOOL_AND", "MAP_CONTAINS",
    "MAP_CONTAINS_KEY", "MAP_CONTAINS_ENTRY", "JSON_VALID", "IS_VALID_JSON",
    "JSON_CONTAINS", "ARRAY_HAS_ANY", "STRUCT_CONTAINS", "STRUCT_HAS",
    "VARIANT_CONTAINS", "VARIANT_EXISTS", "SIGNBIT", "LIKE_ESCAPE",
    "ILIKE_ESCAPE", "NOT_LIKE_ESCAPE", "NOT_ILIKE_ESCAPE", "CAN_CAST_IMPLICITLY",
    "IN_SEARCH_PATH", "ST_INTERSECTS_EXTENT", "IS_HISTOGRAM_OTHER_BIN",
    "IS_VARIANT", "IS_CHAR", "IS_REAL", "IS_TIME", "IS_TIMESTAMP_LTZ",
    "IS_TIMESTAMP_NTZ", "IS_TIMESTAMP_TZ", "IS_BINARY", "IS_DATE_VALUE",
})

NON_NULL_BOOL_FUNCS: frozenset[str] = frozenset({
    "EQUAL_NULL", "ISNULL", "ISNOTNULL", "IS_NULL_VALUE",
})

VALUE_PASSTHROUGH_EXPRS: tuple[type[exp.Expr], ...] = tuple(
    c
    for n in (
        "FirstValue", "LastValue", "NthValue", "Min", "Max", "AnyValue",
        "First", "Last", "Median", "Quantile", "PercentileDisc",
    )
    if (c := getattr(exp, n, None)) is not None
)

LAG_LEAD_EXPRS: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "Lag", None), getattr(exp, "Lead", None)) if c is not None
)

VALUE_PASSTHROUGH_NAMES: frozenset[str] = frozenset({
    "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE",
    "MIN", "MAX", "ANY", "ANY_VALUE", "ANYLAST", "ANY_LAST",
    "ANYHEAVY", "ANY_HEAVY", "FIRST", "LAST", "ARBITRARY",
    "ARG_MAX", "ARG_MIN", "ARGMAX", "ARGMIN", "MAX_BY", "MIN_BY",
    "ARG_MAX_NULL", "ARG_MAX_NULLS_LAST", "ARG_MIN_NULL", "ARG_MIN_NULLS_LAST",
    "MEDIAN", "MODE", "PERCENTILE_DISC", "QUANTILE", "QUANTILE_DISC",
    "SINGLE_VALUE", "SINGLEVALUE",
})

SELECT_DISALLOWED_CLAUSES: tuple[str, ...] = (
    "joins", "where", "group", "having", "qualify", "limit",
    "offset", "distinct", "laterals", "windows", "sample", "pivots", "prewhere",
)
SORT_DIST_ARGS: tuple[str, ...] = ("order", "sort", "distribute", "cluster")
NON_FILTER_ARGS: tuple[str, ...] = ("windows", "distinct", "group", *SORT_DIST_ARGS)
SEL_ALIAS_ARGS: frozenset[str] = frozenset({
    "where", "having", "qualify", "prewhere", *NON_FILTER_ARGS,
})

CH_AGG_COMBINATORS: tuple[str, ...] = (
    "SIMPLESTATE", "IF", "ARRAY", "STATE", "MERGE", "DISTINCT", "ORNULL", "ORDEFAULT", "FOREACH",
)

NULLABLE_EMPTY_CONTAINER_SCALAR_FUNCS: frozenset[str] = frozenset({
    "ARRAY_POSITION", "ARRAY_INDEXOF", "ARRAY_SUM", "ARRAY_AVG", "ARRAY_MEDIAN",
    "ARRAY_MIN", "ARRAY_MAX", "ARRAY_TO_STRING", "ARRAY_JOIN",
    "LIST_POSITION", "LIST_INDEXOF", "LIST_SUM", "LIST_AVG", "LIST_MEDIAN",
    "LIST_MIN", "LIST_MAX", "LIST_TO_STRING",
})

SAFE_CONTAINER_SCALAR_FUNCS: frozenset[str] = frozenset({
    "ARRAY_LENGTH", "ARRAY_SIZE", "ARRAY_CARDINALITY", "ARRAY_POSITION",
    "ARRAY_INDEXOF", "ARRAY_SUM", "ARRAY_AVG", "ARRAY_MEDIAN",
    "ARRAY_TO_STRING", "ARRAY_JOIN",
    "LIST_LENGTH", "LIST_SIZE", "LIST_CARDINALITY", "LIST_COUNT",
    "LIST_POSITION", "LIST_INDEXOF", "LIST_SUM", "LIST_AVG", "LIST_MEDIAN",
    "LIST_TO_STRING",
    "MAP_SIZE", "MAP_CARDINALITY", "MAP_LENGTH",
    "OBJECT_SIZE", "OBJECT_KEYS", "OBJECT_ID",
    "STRUCT_KEYS",
    "JSON_ARRAY_LENGTH", "JSONB_ARRAY_LENGTH", "JSON_KEYS", "JSONB_OBJECT_KEYS",
    "JSON_OBJECT_KEYS", "JSON_TYPE", "JSON_TYPEOF", "JSONB_TYPEOF",
    "JSON_FORMAT", "JSON_PRETTY", "JSONB_PRETTY", "JSON_QUOTE",
})

UNKNOWN_CONTAINER_FUNCS: frozenset[str] = frozenset({
    "PARSE_JSON", "TRY_PARSE_JSON", "FROM_JSON", "FROM_JSON_STRICT",
    "JSON_QUERY", "REMAP_STRUCT", "UNION_EXTRACT", "CONSTANT_OR_NULL", "SHUFFLE",
})

ARRAY_EXT_ANON: frozenset[str] = frozenset({
    "LIST_EXTRACT", "ELEMENT_AT", "ARRAY_EXTRACT", "ARRAY_FIRST",
    "ARRAY_LAST", "LIST_FIRST", "LIST_LAST", "LIST_ELEMENT",
    "LIST_MAX", "LIST_MIN", "ARRAY_MAX", "ARRAY_MIN", "ARRAYELEMENT",
})

ARRAY_PASS_ANON: frozenset[str] = frozenset({
    "SHUFFLE", "ARRAY_DISTINCT", "LIST_DISTINCT",
    "ARRAY_REMOVE", "LIST_REMOVE", "ARRAY_SORT", "LIST_SORT",
    "ARRAY_REVERSE_SORT", "LIST_REVERSE_SORT", "ARRAY_COMPACT",
    "ARRAY_REMOVE_AT", "LIST_REMOVE_AT", "ARRAY_EXCEPT", "LIST_EXCEPT",
})

_JSON_EXTRACT_CLS = getattr(exp, "JSONExtract", None)
_JSON_EXTRACT_SCALAR_CLS = getattr(exp, "JSONExtractScalar", None)
_JSONB_EXTRACT_CLS = getattr(exp, "JSONBExtract", None)
_JSONB_EXTRACT_SCALAR_CLS = getattr(exp, "JSONBExtractScalar", None)
_JSON_PATH_KEY_CLS = getattr(exp, "JSONPathKey", None)
_JSON_PATH_SUB_CLS = getattr(exp, "JSONPathSubscript", None)
_JSON_PATH_WILD_CLS = getattr(exp, "JSONPathWildcard", None)
_INT_SUB_RE = re.compile(r"-?\d+")


def is_json_extract_node(node: exp.Expr) -> bool:
    return any(
        cls is not None and isinstance(node, cls)
        for cls in (
            _JSON_EXTRACT_CLS,
            _JSON_EXTRACT_SCALAR_CLS,
            _JSONB_EXTRACT_CLS,
            _JSONB_EXTRACT_SCALAR_CLS,
        )
    )


def unpack_json_path_steps(node: exp.Expr) -> list[str | int | None] | None:
    path = node if isinstance(node, exp.JSONPath) else node.expression
    if not isinstance(path, exp.JSONPath):
        return None
    steps = [s for s in path.expressions if not isinstance(s, exp.JSONPathRoot)]
    if not steps:
        return [] if any(isinstance(s, exp.JSONPathRoot) for s in path.expressions) else None
    out: list[str | int | None] = []
    for step in steps:
        if _JSON_PATH_KEY_CLS is not None and isinstance(step, _JSON_PATH_KEY_CLS):
            out.append(step.name if step.name else None)
        elif _JSON_PATH_SUB_CLS is not None and isinstance(step, _JSON_PATH_SUB_CLS):
            sub = step.this
            if isinstance(sub, int) and not isinstance(sub, bool):
                out.append(sub)
            elif isinstance(sub, str) and _INT_SUB_RE.fullmatch(sub.strip()):
                out.append(int(sub.strip()))
            elif sub is None or (
                _JSON_PATH_WILD_CLS is not None and isinstance(sub, _JSON_PATH_WILD_CLS)
            ):
                out.append(None)
            else:
                return None
        else:
            return None
    return out


def classify_list_aggr(agg_expr: exp.Expr | None) -> str:
    lit = unwrap_parens(agg_expr)
    if not isinstance(lit, exp.Literal) or not lit.is_string:
        return "unknown"
    name = str(lit.this).upper()
    if name in BOOL_AGGR_NAMES:
        return "bool"
    if name in PASSTHROUGH_AGGR_NAMES:
        return "passthrough"
    if name in ("LIST", "ARRAY_AGG"):
        return "list"
    if name in ("SKEWNESS", "KURTOSIS"):
        return name.lower()
    if name in MIN2_SCALAR_AGGR_NAMES:
        return "min2_scalar"
    if name in NON_BOOL_SCALAR_AGGR_NAMES:
        return "scalar"
    return "unknown"

KNOWN_CONTAINER_ANON_CTORS: frozenset[str] = frozenset({
    "MAP", "MAP_FROM_ENTRIES", "STRUCT_PACK", "NAMED_STRUCT",
    "OBJECT_CONSTRUCT", "OBJECT_CONSTRUCT_KEEP_NULL",
    "JSON_BUILD_OBJECT", "JSONB_BUILD_OBJECT", "JSON_OBJECT",
})

CONTAINER_FUNC_PREFIXES: tuple[str, ...] = (
    "MAP_", "LIST_", "ARRAY_", "OBJECT_", "STRUCT_", "JSON",
)
BOOL_AGGR_NAMES: frozenset[str] = frozenset({
    "BOOL_AND", "BOOL_OR", "EVERY", "LOGICAL_AND", "LOGICAL_OR",
})
PASSTHROUGH_AGGR_NAMES: frozenset[str] = frozenset({
    "FIRST", "LAST", "MIN", "MAX", "ANY_VALUE", "ARBITRARY",
})
MIN2_SCALAR_AGGR_NAMES: frozenset[str] = frozenset({
    "STDDEV", "VARIANCE", "STDDEV_SAMP", "VAR_SAMP", "SKEWNESS", "KURTOSIS",
})
NON_BOOL_SCALAR_AGGR_NAMES: frozenset[str] = frozenset({
    "SUM", "AVG", "MEAN", "MEDIAN", "MODE", "COUNT", "COUNT_STAR",
    "PRODUCT", "STRING_AGG", "GROUP_CONCAT", "LISTAGG",
    "BIT_AND", "BIT_OR", "BIT_XOR", "STDDEV_POP", "VAR_POP", "VAR", "STDEV", "STDEVP",
    "APPROX_COUNT_DISTINCT", "ENTROPY", "SEM", "MAD",
})

UNKNOWN = object()

_BOOL_STR_TRUE: frozenset[str] = frozenset({"true"})
_BOOL_STR_FALSE: frozenset[str] = frozenset({"false"})


def int_float_lossy(a: object, b: object) -> bool:
    return (
        isinstance(a, int)
        and not isinstance(a, bool)
        and isinstance(b, float)
        and abs(a) >= (1 << 53)
    ) or (
        isinstance(b, int)
        and not isinstance(b, bool)
        and isinstance(a, float)
        and abs(b) >= (1 << 53)
    )


def same_scalar_kind(a: object, b: object) -> bool:
    return (type(a) is bool) == (type(b) is bool) and isinstance(a, str) == isinstance(b, str)


def same_const_val(a: object, b: object) -> bool:
    return (a is None and b is None) or (
        a is not None
        and b is not None
        and same_scalar_kind(a, b)
        and not int_float_lossy(a, b)
        and bool(a == b)
    )


def scalar_to_str(iv: object) -> str | None:
    if isinstance(iv, str):
        return iv
    if isinstance(iv, bool):
        return "true" if iv else "false"
    if isinstance(iv, int):
        return str(iv)
    return None


def parse_bool_lit(iv: object, *, allow_int: bool = False) -> object:
    if iv is None or isinstance(iv, bool):
        return iv
    if allow_int and isinstance(iv, int):
        return bool(iv)
    if isinstance(iv, str):
        low = iv.lower()
        if low in _BOOL_STR_TRUE:
            return True
        if low in _BOOL_STR_FALSE:
            return False
    return UNKNOWN


def kleene_and(lb: object, rb: object) -> object:
    if lb is False or rb is False:
        return False
    if lb is True and rb is True:
        return True
    if (lb is None and rb in (True, None)) or (rb is None and lb in (True, None)):
        return None
    return UNKNOWN


def kleene_or(lb: object, rb: object) -> object:
    if lb is True or rb is True:
        return True
    if lb is False and rb is False:
        return False
    if (lb is None and rb in (False, None)) or (rb is None and lb in (False, None)):
        return None
    return UNKNOWN


def kleene_cmp(op: exp.Expr, lv: object, rv: object) -> object:
    if lv is UNKNOWN or rv is UNKNOWN:
        return UNKNOWN
    if isinstance(op, (exp.NullSafeEQ, exp.NullSafeNEQ)):
        is_eq = isinstance(op, exp.NullSafeEQ)
        if lv is None and rv is None:
            return is_eq
        if lv is None or rv is None:
            return not is_eq
        if not same_scalar_kind(lv, rv) or int_float_lossy(lv, rv):
            return UNKNOWN
        return bool(lv == rv) if is_eq else bool(lv != rv)
    if lv is None or rv is None:
        return None
    if not same_scalar_kind(lv, rv) or int_float_lossy(lv, rv):
        return UNKNOWN
    if isinstance(op, exp.EQ):
        return bool(lv == rv)
    if isinstance(op, exp.NEQ):
        return bool(lv != rv)
    if isinstance(lv, (int, float, str)) and isinstance(rv, (int, float, str)):
        if isinstance(op, exp.GT):
            return bool(lv > rv)  # type: ignore[operator]
        if isinstance(op, exp.GTE):
            return bool(lv >= rv)  # type: ignore[operator]
        if isinstance(op, exp.LT):
            return bool(lv < rv)  # type: ignore[operator]
        if isinstance(op, exp.LTE):
            return bool(lv <= rv)  # type: ignore[operator]
    return UNKNOWN


def like_match(val: str, pat: str, *, case_insensitive: bool) -> object:
    if "\\" in pat or len(pat) > 64 or pat.count("%") > 4:
        return UNKNOWN
    parts = [
        "^",
        *((".*" if c == "%" else ("." if c == "_" else re.escape(c))) for c in pat),
        "$",
    ]
    flags = re.DOTALL | (re.IGNORECASE if case_insensitive else 0)
    return bool(re.match("".join(parts), val, flags))


__all__ = [
    "ARRAY_EXT_ANON",
    "ARRAY_PASS_ANON",
    "BOOL_AGGR_NAMES",
    "BOOL_FUNC_NAMES",
    "BOOL_PREDICATE_EXPRS",
    "CH_AGG_COMBINATORS",
    "CONTAINER_FUNC_PREFIXES",
    "KNOWN_CONTAINER_ANON_CTORS",
    "LAG_LEAD_EXPRS",
    "NON_BOOL_SCALAR_AGGR_NAMES",
    "NON_FILTER_ARGS",
    "NON_NULL_BOOL_FUNCS",
    "NULLABLE_EMPTY_CONTAINER_SCALAR_FUNCS",
    "PASSTHROUGH_AGGR_NAMES",
    "SAFE_CONTAINER_SCALAR_FUNCS",
    "SELECT_DISALLOWED_CLAUSES",
    "SEL_ALIAS_ARGS",
    "SORT_DIST_ARGS",
    "UNKNOWN",
    "UNKNOWN_CONTAINER_FUNCS",
    "VALUE_PASSTHROUGH_EXPRS",
    "VALUE_PASSTHROUGH_NAMES",
    "classify_list_aggr",
    "int_float_lossy",
    "is_json_extract_node",
    "kleene_and",
    "kleene_cmp",
    "kleene_or",
    "like_match",
    "parse_bool_lit",
    "same_const_val",
    "same_scalar_kind",
    "scalar_to_str",
    "unpack_json_path_steps",
]
