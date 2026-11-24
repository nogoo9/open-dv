"""Scalar NULL-strictness, aggregate nullability, and window-frame helpers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import ResolvedItem, SmapType, param_agg_q_args
from opendv.storage._bool_expr_vocab import CH_AGG_COMBINATORS


class CheckFn(Protocol):
    def __call__(
        self,
        nd: exp.Expr | None,
        an: bool,
        ctes: dict[str, exp.CTE],
        vis: frozenset[str],
        smap: SmapType | None,
        *,
        ab: bool = True,
    ) -> bool: ...


class CheckListFn(Protocol):
    def __call__(
        self,
        its: Sequence[exp.Expr | None],
        an: bool,
        ctes: dict[str, exp.CTE],
        vis: frozenset[str],
        smap: SmapType | None,
        *,
        ab: bool = True,
    ) -> bool: ...


class CheckResFn(Protocol):
    def __call__(
        self,
        its: list[ResolvedItem],
        an: bool,
        *,
        ab: bool = True,
    ) -> bool: ...


def _clses(*names: str) -> tuple[type[exp.Expr], ...]:
    return tuple(c for n in names if (c := getattr(exp, n, None)) is not None)


_PARAM_AGG_CLS = getattr(exp, "ParameterizedAgg", None)
_CH_AGG_CLSES = _clses("ParameterizedAgg", "CombinedAggFunc")

NULL_STRICT_SCALAR_FUNCS: frozenset[str] = frozenset({
    "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "BTRIM",
    "ABS", "LENGTH", "CHAR_LENGTH", "CHARACTER_LENGTH", "OCTET_LENGTH", "BIT_LENGTH",
    "REPLACE", "ROUND", "CEIL", "CEILING", "FLOOR", "TRUNC",
    "LEFT", "RIGHT", "SUBSTR", "SUBSTRING", "REGEXP_REPLACE",
    "DATE_TRUNC", "DATETRUNC", "STRFTIME", "TIME_TO_STR", "STRPTIME",
    "EXTRACT", "DATE_PART", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
    "QUARTER", "WEEK", "DAYOFWEEK", "DAYOFYEAR", "EPOCH", "TIME_TO_UNIX", "UNIX_SECONDS",
    "DATE_DIFF", "DATEDIFF", "DATE_ADD", "DATE_SUB", "MAKE_DATE", "DATE_FROM_PARTS",
    "TIMESTAMP_FROM_PARTS", "TO_DATE", "TO_TIMESTAMP",
    "REGEXP_EXTRACT", "REGEXP_EXTRACT_ALL",
    "SPLIT", "STR_SPLIT", "STRING_SPLIT", "STR_SPLIT_REGEX", "STRING_SPLIT_REGEX",
    "STRING_TO_ARRAY", "URL_ENCODE", "URL_DECODE", "BASE64", "TO_BASE64", "FROM_BASE64",
    "SIN", "COS", "TAN", "ASIN", "ACOS", "ATAN", "ATAN2", "SINH", "COSH", "TANH",
    "COT", "RADIANS", "DEGREES", "TO_JSON", "ARRAY_TO_JSON", "ROW_TO_JSON",
    "SPLIT_PART", "INSTR", "STRPOS", "POSITION", "STR_POSITION",
    "MD5", "SHA1", "SHA2", "SHA256", "SHA512",
    "PRINTF", "FORMAT", "REVERSE", "REPEAT", "XOR",
    "SQRT", "CBRT", "POWER", "POW", "LOG", "LOG10", "LOG2", "LN", "EXP", "SIGN", "MOD",
    "LPAD", "RPAD", "TRANSLATE", "ASCII", "CHR", "HEX", "UNHEX",
    "STARTS_WITH", "ENDS_WITH", "PREFIX", "SUFFIX",
    "REGEXP_LIKE", "REGEXP_CONTAINS", "REGEXP_ILIKE", "REGEXP_FULL_MATCH", "REGEXP_MATCHES",
    "CONTAINS", "CONTAINS_SUBSTR",
})

_NON_ARG_EXPR_TYPES: tuple[type[exp.Expr], ...] = (
    exp.DataType, exp.Var, exp.Identifier, exp.Order,
)


_SAMPLE_STAT_AGG_CLSES = _clses(
    "Stddev", "StddevSamp", "Variance", "VarianceSamp",
    "Skewness", "Kurtosis", "CovarSamp", "Corr",
)
_SAMPLE_STAT_AGG_NAMES: frozenset[str] = frozenset({
    "STDDEV", "STDDEV_SAMP", "STDDEVSAMP", "VARIANCE", "VAR_SAMP", "VARSAMP", "VAR", "STDEV",
    "SKEWNESS", "SKEWPOP", "SKEWSAMP", "KURTOSIS", "KURTOSIS_POP", "KURTPOP", "KURTSAMP",
    "COVAR_SAMP", "COVARSAMP", "CORR", "SEM",
    "REGR_AVGX", "REGR_AVGY", "REGR_INTERCEPT", "REGR_R2",
    "REGR_SLOPE", "REGR_SXX", "REGR_SXY", "REGR_SYY",
})
_NULLABLE_ARG_AGG_CLSES = _clses(
    "Sum", "Avg", "Median", "Quantile", "PercentileCont", "PercentileDisc",
    "StddevPop", "VariancePop", "GroupConcat", "ArgMax", "ArgMin",
    "LogicalAnd", "LogicalOr", "BoolxorAgg", "CountIf",
    "BitwiseAndAgg", "BitwiseOrAgg", "BitwiseXorAgg",
    "CovarPop", "ApproxTopK", "ApproxQuantile", "Mode", "Entropy",
)
_NULLABLE_ARG_AGG_NAMES: frozenset[str] = frozenset({
    "SUM", "AVG", "MEAN", "MEDIAN", "MODE", "QUANTILE", "QUANTILE_CONT", "QUANTILE_DISC",
    "PERCENTILE_CONT", "PERCENTILE_DISC", "PRODUCT", "STRING_AGG", "STRINGAGG", "GROUP_CONCAT",
    "LISTAGG", "STR_AGG", "WM_CONCAT", "BIT_AND", "BIT_OR", "BIT_XOR",
    "BIT_AND_AGG", "BIT_OR_AGG", "BIT_XOR_AGG", "STDDEV_POP", "STDDEVPOP", "VAR_POP", "VARPOP",
    "VARP", "STDEVP", "MAX_BY", "MIN_BY", "ARG_MAX", "ARG_MIN", "ARGMAX", "ARGMIN",
    "ARG_MAX_NULL", "ARG_MAX_NULLS_LAST", "ARG_MIN_NULL", "ARG_MIN_NULLS_LAST",
    "BOOL_AND", "BOOL_OR", "EVERY", "LOGICAL_AND", "LOGICAL_OR", "BOOLAND",
    "BOOLOR", "BOOLAND_AGG", "BOOLOR_AGG", "BOOLXOR_AGG",
    "BOOL_AND_AGG", "BOOL_OR_AGG", "BOOL_XOR_AGG", "COUNT_IF", "COUNTIF",
    "ANY", "ANYHEAVY", "ANY_HEAVY", "SOME", "COVAR_POP", "COVARPOP", "MAD", "FAVG", "FSUM",
    "KAHAN_SUM", "SUMKAHAN", "SUM_NO_OVERFLOW", "RESERVOIR_QUANTILE", "APPROX_QUANTILE",
    "APPROX_PERCENTILE", "APPROX_TOP_K", "BITSTRING_AGG", "DECIMAL_AVERAGE", "HISTOGRAM",
    "HISTOGRAM_EXACT", "LTTB", "GEOMEAN", "GEOMETRIC_MEAN", "ENTROPY", "COMBINE_AGGR",
    "QUANTILEEXACT", "QUANTILE_EXACT", "QUANTILEEXACTLOW", "QUANTILEEXACTHIGH",
    "QUANTILEEXACTWEIGHTED", "QUANTILETDIGEST", "QUANTILETDIGESTWEIGHTED",
    "QUANTILEBFLOAT16", "QUANTILETIMING", "QUANTILEDD", "QUANTILES",
    "MEDIANEXACT", "MEDIANEXACTLOW", "MEDIANEXACTHIGH", "MEDIANEXACTWEIGHTED",
    "MEDIANTIMING", "MEDIANTIMINGWEIGHTED", "MEDIANDETERMINISTIC",
    "MEDIANTDIGEST", "MEDIANTDIGESTWEIGHTED", "MEDIANBFLOAT16",
    "MEDIANBFLOAT16WEIGHTED", "MEDIANDD", "MEDIANINTERPOLATEDWEIGHTED",
    "AVGWEIGHTED", "TOPKWEIGHTED",
    "ANYLAST", "ANY_LAST", "MIN", "MAX", "FIRST", "LAST", "FIRST_VALUE", "LAST_VALUE",
    "NTH_VALUE", "ANY_VALUE", "ARBITRARY", "SINGLE_VALUE", "SINGLEVALUE",
    "ARRAY_CONCAT_AGG", "ARRAY_UNION_AGG", "ARRAY_UNIQUE_AGG", "CHECKSUM_AGG",
    "GROUPBITAND", "GROUPBITOR", "GROUPBITXOR", "HASH_AGG", "HLL", "HLL_ACCUMULATE",
    "HLL_COMBINE", "HLL_CREATE_SKETCH", "HLL_ESTIMATE", "JSONB_AGG", "JSON_AGG",
    "JSON_ARRAYAGG", "JSON_GROUP_ARRAY", "JSON_GROUP_OBJECT", "JSON_OBJECTAGG",
    "MAP_AGG", "MULTIMAP_AGG", "OBJECT_AGG", "VARIANT_GROUP_OBJECT", "XMLAGG",
})
_NON_NULL_AGG_NAMES: frozenset[str] = frozenset({
    "COUNT", "COUNT_STAR", "COUNT_BIG", "REGR_COUNT", "ARRAY_AGG", "LIST",
    "COLLECT_LIST", "COLLECT_SET", "TOPK", "SUMMAP",
    "GROUPARRAY", "GROUP_ARRAY", "GROUPUNIQARRAY", "GROUP_UNIQ_ARRAY",
    "APPROX_COUNT_DISTINCT", "UNIQ", "UNIQEXACT", "UNIQCOMBINED", "UNIQHLL12",
})
ALL_KNOWN_AGG_NAMES: frozenset[str] = (
    _SAMPLE_STAT_AGG_NAMES | _NULLABLE_ARG_AGG_NAMES | _NON_NULL_AGG_NAMES
)


def split_ch_combinators(
    fn: str,
    extra_names: frozenset[str] = frozenset(),
    *,
    allow_quantile_prefix: bool | None = None,
) -> tuple[str, bool, int, bool]:
    if allow_quantile_prefix is None:
        allow_quantile_prefix = fn.startswith(("QUANTILE", "MEDIAN"))
    names = ALL_KNOWN_AGG_NAMES | extra_names if extra_names else ALL_KNOWN_AGG_NAMES
    if fn in names:
        return fn, False, 0, False
    cur, has_or_null, has_or_default, if_cnt = fn, False, False, 0
    while True:
        matched = False
        for suf in CH_AGG_COMBINATORS:
            if cur.endswith(suf) and len(cur) > len(suf):
                cur = cur[: -len(suf)]
                if suf == "ORNULL":
                    has_or_null = True
                elif suf == "ORDEFAULT":
                    has_or_default = True
                elif suf == "IF":
                    if_cnt += 1
                if cur in names:
                    return cur, has_or_null, if_cnt, has_or_default
                matched = True
                break
        if not matched:
            if has_or_null or (allow_quantile_prefix and cur.startswith(("QUANTILE", "MEDIAN"))):
                return cur, has_or_null, if_cnt, has_or_default
            return fn, False, 0, False


def _func_name(node: exp.Expr) -> str:
    if isinstance(node, (exp.Anonymous, exp.AnonymousAggFunc)) or (
        _CH_AGG_CLSES and isinstance(node, _CH_AGG_CLSES)
    ):
        raw = node.this
        nm = raw.name if isinstance(raw, exp.Expr) else (raw or node.name)
        return str(nm).upper()
    if isinstance(node, exp.Func):
        try:
            return node.sql_name().upper().replace("J_S_O_N_", "JSON_")
        except NotImplementedError:
            return type(node).__name__.upper()
    return ""


def expr_value_args(cur: exp.Expr) -> list[exp.Expr]:
    if _PARAM_AGG_CLS is not None and isinstance(cur, _PARAM_AGG_CLS):
        raw = cur.args.get("params") or cur.expressions
        return [
            x
            for x in raw
            if isinstance(x, exp.Expr) and not isinstance(x, _NON_ARG_EXPR_TYPES)
        ]
    if isinstance(cur, (exp.Anonymous, exp.AnonymousAggFunc)) or (
        _CH_AGG_CLSES and isinstance(cur, _CH_AGG_CLSES)
    ):
        return [x for x in cur.expressions if x is not None]
    out: list[exp.Expr] = []
    for val in cur.args.values():
        if isinstance(val, exp.Expr) and not isinstance(val, _NON_ARG_EXPR_TYPES):
            out.append(val)
        elif isinstance(val, list):
            out.extend(
                it
                for it in val
                if isinstance(it, exp.Expr) and not isinstance(it, _NON_ARG_EXPR_TYPES)
            )
    return out


def resolve_window_spec(cur: exp.Window) -> tuple[exp.Expr | None, bool]:
    if cur.args.get("spec") is not None:
        return cur.args.get("spec"), True
    al0 = cur.args.get("alias")
    if not isinstance(al0, exp.Identifier) or not al0.name:
        return None, True
    p = cur.parent
    while p is not None and not isinstance(p, exp.Select):
        p = p.parent
    if not isinstance(p, exp.Select):
        return None, False
    win_map: dict[str, exp.Window] = {}
    for w in p.args.get("windows") or ():
        if isinstance(w, exp.Window) and isinstance(w.this, exp.Identifier) and w.this.name:
            win_map[w.this.name.lower()] = w
    w_cur: exp.Window = cur
    seen: set[str] = set()
    for _ in range(8):
        if w_cur.args.get("spec") is not None:
            return w_cur.args.get("spec"), True
        al = w_cur.args.get("alias")
        if not isinstance(al, exp.Identifier) or not al.name:
            return None, True
        key = al.name.lower()
        if key in seen or key not in win_map:
            return None, False
        seen.add(key)
        w_cur = win_map[key]
    return None, False


def is_nullable_agg_expr(
    cur: exp.Expr,
    arg_null_fn: Callable[[exp.Expr], bool],
    cond_false_fn: Callable[[exp.Expr], bool] | None = None,
) -> bool:
    fn = _func_name(cur)
    is_q_node = bool(_CH_AGG_CLSES and isinstance(cur, _CH_AGG_CLSES)) or (
        isinstance(cur, exp.Anonymous) and fn.startswith(("QUANTILE", "MEDIAN"))
    )
    base_fn, has_or_null, if_cnt, has_or_default = split_ch_combinators(
        fn, allow_quantile_prefix=is_q_node,
    )
    if (
        has_or_null and (base_fn in ALL_KNOWN_AGG_NAMES or isinstance(cur, exp.AggFunc))
    ) or (isinstance(cur, exp.AggFunc) and fn.endswith("ORNULL")):
        return True
    if has_or_default and not has_or_null:
        return False
    if (_SAMPLE_STAT_AGG_CLSES and isinstance(cur, _SAMPLE_STAT_AGG_CLSES)) or (
        base_fn in _SAMPLE_STAT_AGG_NAMES
    ):
        return True
    if (
        (_NULLABLE_ARG_AGG_CLSES and isinstance(cur, _NULLABLE_ARG_AGG_CLSES))
        or base_fn in _NULLABLE_ARG_AGG_NAMES
        or (base_fn.startswith(("QUANTILE", "MEDIAN")) and is_q_node)
    ):
        vargs = expr_value_args(cur)
        return (
            any(arg_null_fn(a) for a in vargs)
            or any(arg_null_fn(a) for a in param_agg_q_args(cur))
            or bool(
                if_cnt
                and len(vargs) >= if_cnt
                and base_fn not in ("SUM", "COUNT", "COUNTIF", "COUNT_IF")
                and cond_false_fn
                and any(cond_false_fn(c) for c in vargs[-if_cnt:])
            )
        )
    return False


_BIN_ARITH_CLSES: tuple[type[exp.Expr], ...] = (
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod,
    *_clses(
        "IntDiv", "Pow", "BitwiseAnd", "BitwiseOr",
        "BitwiseXor", "BitwiseLeftShift", "BitwiseRightShift",
    ),
)
_UNARY_ARITH_CLSES: tuple[type[exp.Expr], ...] = (exp.Neg, *_clses("BitwiseNot"))


def null_strict_arg_nodes(
    cur: exp.Expr, *, is_container_fn: Callable[[exp.Expr | None], bool] | None = None,
) -> list[exp.Expr] | None:
    if isinstance(cur, _UNARY_ARITH_CLSES):
        return [cur.this] if cur.this is not None else []
    if isinstance(cur, _BIN_ARITH_CLSES):
        return [x for x in (cur.this, cur.expression) if x is not None]
    if isinstance(cur, exp.DPipe):
        if is_container_fn is not None and (
            is_container_fn(cur.this) or is_container_fn(cur.expression)
        ):
            return None
        return [x for x in (cur.this, cur.expression) if x is not None]
    if isinstance(cur, exp.ConcatWs) or (
        isinstance(cur, exp.Anonymous) and cur.name.upper() == "CONCAT_WS"
    ):
        return [cur.expressions[0]] if cur.expressions else []
    if isinstance(cur, exp.Concat) and not cur.args.get("coalesce"):
        return [x for x in cur.expressions if x is not None]
    if isinstance(cur, exp.Anonymous) and cur.name.upper() in NULL_STRICT_SCALAR_FUNCS:
        return [x for x in cur.expressions if x is not None]
    if isinstance(cur, exp.Func) and _func_name(cur) in NULL_STRICT_SCALAR_FUNCS:
        return expr_value_args(cur)
    return None


def is_null_strict_anon_or_func(cur: exp.Expr) -> bool:
    u = unwrap_parens(cur)
    if u is None:
        return False
    return _func_name(u) in NULL_STRICT_SCALAR_FUNCS


__all__ = [
    "ALL_KNOWN_AGG_NAMES",
    "NULL_STRICT_SCALAR_FUNCS",
    "CheckFn",
    "CheckListFn",
    "CheckResFn",
    "expr_value_args",
    "is_null_strict_anon_or_func",
    "is_nullable_agg_expr",
    "null_strict_arg_nodes",
    "resolve_window_spec",
    "split_ch_combinators",
]
