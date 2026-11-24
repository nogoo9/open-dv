"""JSON literal extraction and NULLIF context resolution for `_bool_expr_*`.

Strictly Layer 2a: imports only json, sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, and `_bool_expr_vocab`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    ResolvedItem,
    SmapType,
    extend_cte_nodes,
    is_array_ctor,
    is_known_container_ctor,
    is_unknown_container_or_lambda,
    merge_select_smap,
)
from opendv.storage._bool_expr_maps import (
    _JSON_KV_CLS,
    _JSON_OBJ_ANON,
    _JSON_OBJECT_CLS,
    unpack_container_ctor,
)
from opendv.storage._bool_expr_vocab import (
    SELECT_DISALLOWED_CLAUSES,
    UNKNOWN,
    int_float_lossy,
    same_scalar_kind,
    unpack_json_path_steps,
)

EvalLitFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], object
]
ProvItem = tuple[exp.Expr, dict[str, exp.CTE], frozenset[str], SmapType | None, bool]
_JSON_SEG_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(-?\d+|\*|\"[^\"]*\"|'[^']*')\]")


def _json_val_to_expr(val: object) -> exp.Expr:
    if val is None:
        return exp.Null()
    if isinstance(val, bool):
        return exp.Boolean(this=val)
    if isinstance(val, (int, float)):
        return exp.Literal.number(val)
    if isinstance(val, str):
        return exp.Literal.string(val)
    if isinstance(val, list):
        return exp.Array(expressions=[_json_val_to_expr(x) for x in val])
    if isinstance(val, dict):
        return exp.JSONObject(
            expressions=[
                exp.JSONKeyValue(
                    this=exp.Literal.string(str(k)),
                    expression=_json_val_to_expr(v),
                )
                for k, v in val.items()
            ]
        )
    return exp.Null()


def _unwrap_json_lit(
    ex_u: exp.Expr | None,
    eval_lit_fn: EvalLitFn | None = None,
    ec: dict[str, exp.CTE] | None = None,
    ev: frozenset[str] = frozenset(),
    es: SmapType | None = None,
) -> tuple[str | None, bool]:
    cur = unwrap_parens(ex_u)
    while cur is not None:
        if type(cur) is exp.Cast and isinstance(dt := cur.args.get("to"), exp.DataType):
            if dt.is_type(
                exp.DataType.Type.JSON, exp.DataType.Type.JSONB, *exp.DataType.TEXT_TYPES,
            ):
                cur = unwrap_parens(cur.this)
                continue
            return None, False
        if isinstance(cur, exp.ParseJSON):
            cur = unwrap_parens(cur.this)
            continue
        break
    if isinstance(cur, exp.Null):
        return None, True
    if isinstance(cur, exp.Literal) and cur.is_string:
        return str(cur.this), False
    if eval_lit_fn is not None and cur is not None:
        val = eval_lit_fn(cur, ec if ec is not None else {}, ev, es)
        if val is None:
            return None, True
        if isinstance(val, str):
            return val, False
    return None, False


def is_json_array_literal(
    node: exp.Expr | None,
    eval_lit_fn: EvalLitFn | None = None,
    ec: dict[str, exp.CTE] | None = None,
    ev: frozenset[str] = frozenset(),
    es: SmapType | None = None,
) -> bool:
    raw, is_null = _unwrap_json_lit(node, eval_lit_fn, ec, ev, es)
    if is_null or raw is None:
        return False
    try:
        return isinstance(json.loads(raw), list)
    except ValueError:
        return False


def parse_json_path_arg(
    arg1: exp.Expr | None,
    eval_lit_fn: EvalLitFn | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[str | int | None] | None:
    u1 = unwrap_parens(arg1)
    if u1 is None:
        return None
    if isinstance(u1, exp.JSONPath):
        return unpack_json_path_steps(u1)
    s_val: object = (
        eval_lit_fn(u1, ctes, vis, smap)
        if eval_lit_fn is not None
        else (str(u1.this) if isinstance(u1, exp.Literal) and u1.is_string else UNKNOWN)
    )
    if not isinstance(s_val, str) or not s_val.startswith("$"):
        return None
    if s_val == "$":
        return []
    steps: list[str | int | None] = []
    pos = 1
    while pos < len(s_val):
        m = _JSON_SEG_RE.match(s_val, pos)
        if m is None:
            return None
        pos = m.end()
        if m.group(1) is not None:
            steps.append(m.group(1))
        else:
            sub = m.group(2)
            if sub == "*":
                steps.append(None)
            elif (sub.startswith('"') and sub.endswith('"')) or (
                sub.startswith("'") and sub.endswith("'")
            ):
                steps.append(sub[1:-1])
            else:
                steps.append(int(sub))
    return steps


def _json_scalar_to_str(val: object) -> exp.Expr:
    if val is None:
        return exp.Null()
    if isinstance(val, bool):
        return exp.Literal.string("true" if val else "false")
    if isinstance(val, (int, float, str)):
        return exp.Literal.string(str(val))
    return exp.Literal.string(json.dumps(val))


def eval_json_path_raw(
    ex_u: exp.Expr | None,
    steps: Sequence[str | int | None],
    ec: dict[str, exp.CTE],
    ev: frozenset[str],
    es: SmapType | None,
    eval_lit_fn: EvalLitFn | None = None,
) -> list[object] | None:
    raw, is_null = _unwrap_json_lit(ex_u, eval_lit_fn, ec, ev, es)
    if is_null:
        return [None]
    if raw is None:
        return None
    try:
        data: object = json.loads(raw)
    except ValueError:
        return [None]
    cur: list[object] = [data]
    for fk in steps:
        nxt: list[object] = []
        for item in cur:
            if fk is None:
                if isinstance(item, list):
                    nxt.extend(item)
                else:
                    nxt.append(None)
            elif isinstance(fk, int):
                if isinstance(item, list):
                    if 0 <= fk < len(item):
                        nxt.append(item[fk])
                    elif -len(item) <= fk < 0:
                        nxt.append(item[len(item) + fk])
                        nxt.append(None)
                    else:
                        nxt.append(None)
                else:
                    nxt.append(None)
            elif isinstance(item, dict):
                nxt.append(item.get(fk))
            else:
                nxt.append(None)
        cur = nxt
    return cur or [None]


def eval_json_literal_extract(
    ex_u: exp.Expr | None,
    steps: Sequence[str | int | None],
    ec: dict[str, exp.CTE],
    ev: frozenset[str],
    es: SmapType | None,
    *,
    scalar_only: bool = False,
    eval_lit_fn: EvalLitFn | None = None,
) -> list[ResolvedItem] | None:
    raw_vals = eval_json_path_raw(ex_u, steps, ec, ev, es, eval_lit_fn=eval_lit_fn)
    if raw_vals is None:
        return None
    conv = _json_scalar_to_str if scalar_only else _json_val_to_expr
    return [(conv(v), ec, ev, es) for v in raw_vals]


def resolve_nullif_operands(
    expr: exp.Expr | None,
    smap: SmapType | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    resolve_col_fn: Callable[..., list[ResolvedItem]],
    resolve_src_fn: Callable[..., list[ResolvedItem]],
    depth: int = 0,
) -> list[ResolvedItem]:
    u = unwrap_parens(expr)
    if u is None or depth > 12:
        return []
    if isinstance(u, exp.Column):
        if not smap or not (res := resolve_col_fn(u, smap, ctes, vis)):
            return [(u, ctes, vis, smap)]
        return [
            item
            for ex, ec, ev, es in res
            for item in resolve_nullif_operands(
                ex, es, ec, ev, resolve_col_fn, resolve_src_fn, depth + 1,
            )
        ]
    if isinstance(u, exp.Subquery):
        if any(u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES):
            return [(u, ctes, vis, smap)]
        return resolve_nullif_operands(
            u.this, smap, extend_cte_nodes(u, ctes), vis,
            resolve_col_fn, resolve_src_fn, depth + 1,
        )
    if isinstance(u, exp.Select):
        if any(u.args.get(k) for k in SELECT_DISALLOWED_CLAUSES):
            return [(u, ctes, vis, smap)]
        nc, sel_smap, _, _ = merge_select_smap(u, ctes, smap)
        cols = resolve_src_fn(u, None, nc, vis, sel_smap)
        if cols:
            return [
                item
                for ex, ec, ev, es in cols
                for item in resolve_nullif_operands(
                    ex, es, ec, ev, resolve_col_fn, resolve_src_fn, depth + 1,
                )
            ]
        return [(u, nc, vis, sel_smap)]
    if isinstance(u, exp.SetOperation):
        nc = extend_cte_nodes(u, ctes)
        return (
            resolve_nullif_operands(
                u.this, smap, nc, vis, resolve_col_fn, resolve_src_fn, depth + 1,
            )
            + resolve_nullif_operands(
                u.expression, smap, nc, vis, resolve_col_fn, resolve_src_fn, depth + 1,
            )
        )
    return [(u, ctes, vis, smap)]


def nullif_eq(
    ai: ResolvedItem,
    bj: ResolvedItem,
    eval_lit_fn: EvalLitFn,
) -> bool:
    a, ac, av, asm = ai
    b, bc, bv, bsm = bj
    if a == b and (not isinstance(a, exp.Column) or asm is bsm):
        return True
    la = eval_lit_fn(a, ac, av, asm)
    lb = eval_lit_fn(b, bc, bv, bsm)
    if (
        la is not UNKNOWN
        and lb is not UNKNOWN
        and la is not None
        and lb is not None
        and same_scalar_kind(la, lb)
    ):
        return int_float_lossy(la, lb) or bool(la == lb)
    return False


def extract_json_extract_items_prov(
    cur: Sequence[ProvItem],
    steps: Sequence[str | int | None],
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    *,
    is_scalar_only: bool,
    ev_fn: EvalLitFn | None,
    unwrap_fn: Callable[
        [exp.Expr, dict[str, exp.CTE], frozenset[str], SmapType | None], list[ResolvedItem]
    ],
) -> list[ProvItem]:
    cur_t: list[ProvItem] = list(cur)
    final_out: list[ProvItem] = []
    for idx, fk in enumerate(steps):
        nxt: list[ProvItem] = []
        saw_ctor = False
        for ex, ec, ev, es, from_ctor in cur_t:
            ex_u = unwrap_parens(ex)
            if isinstance(ex_u, exp.Null):
                nxt.append((ex_u, ec, ev, es, from_ctor))
            elif isinstance(fk, int) and is_array_ctor(ex_u) and ex_u is not None:
                els = ex_u.expressions
                n_els = len(els)
                if 0 <= fk < n_els:
                    nxt.append((els[fk], ec, ev, es, True))
                elif -n_els <= fk < 0:
                    nxt.append((els[n_els + fk], ec, ev, es, True))
                    saw_ctor = True
                else:
                    saw_ctor = True
            elif isinstance(fk, int) and is_known_container_ctor(ex_u):
                saw_ctor = True
            elif (vals := unpack_container_ctor(ex_u, fk)):
                nxt.extend((val, ec, ev, es, True) for val in vals)
            elif is_known_container_ctor(ex_u):
                saw_ctor = True
            elif not from_ctor and (
                jvals := eval_json_literal_extract(
                    ex_u, steps[idx:], ec, ev, es, scalar_only=is_scalar_only, eval_lit_fn=ev_fn,
                )
            ) is not None:
                for jx, jc, jv, js in jvals:
                    jx_u = unwrap_parens(jx)
                    if not is_scalar_only and isinstance(jx_u, exp.Literal) and jx_u.is_string:
                        final_out.append((
                            exp.Literal.string(json.dumps(str(jx_u.this))), jc, jv, js, True,
                        ))
                    else:
                        final_out.append((jx, jc, jv, js, False))
            elif is_unknown_container_or_lambda(ex_u):
                nxt.append((ex, ec, ev, es, from_ctor))
            elif from_ctor or not isinstance(ex_u, exp.Column):
                saw_ctor = True
        if saw_ctor:
            nxt.append((exp.Null(), ctes, vis, smap, False))
        if idx + 1 < len(steps):
            cur_t = [
                (*it, fc) for vx, vc, vv, vs, fc in nxt for it in unwrap_fn(vx, vc, vv, vs)
            ]
        else:
            cur_t = nxt
    out: list[ProvItem] = []
    for ex, ec, ev, es, fc in cur_t + final_out:
        ex_u = unwrap_parens(ex)
        if not is_scalar_only and fc and isinstance(ex_u, exp.Literal) and ex_u.is_string:
            out.append((exp.Literal.string(json.dumps(str(ex_u.this))), ec, ev, es, True))
        else:
            out.append((ex, ec, ev, es, fc))
    return out


def extract_json_extract_items(
    cur: list[ResolvedItem],
    steps: Sequence[str | int | None],
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    *,
    is_scalar_only: bool,
    ev_fn: EvalLitFn | None,
    unwrap_fn: Callable[
        [exp.Expr, dict[str, exp.CTE], frozenset[str], SmapType | None], list[ResolvedItem]
    ],
) -> list[ResolvedItem]:
    return [
        it[:4]
        for it in extract_json_extract_items_prov(
            [(*c, False) for c in cur],
            steps,
            ctes,
            vis,
            smap,
            is_scalar_only=is_scalar_only,
            ev_fn=ev_fn,
            unwrap_fn=unwrap_fn,
        )
    ]


def _json_ctor_key_lits(
    arg0: exp.Expr,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    eval_lit_fn: EvalLitFn | None,
) -> list[exp.Expr] | None:
    u0 = unwrap_parens(arg0)
    raw_keys: list[exp.Expr] | None = None
    if _JSON_OBJECT_CLS is not None and isinstance(u0, _JSON_OBJECT_CLS):
        raw_keys = []
        for fe in u0.expressions:
            if _JSON_KV_CLS is None or not isinstance(fe, _JSON_KV_CLS) or fe.this is None:
                return None
            raw_keys.append(fe.this)
    elif isinstance(u0, exp.Anonymous) and u0.name.upper() in _JSON_OBJ_ANON:
        if len(u0.expressions) % 2 != 0:
            return None
        raw_keys = list(u0.expressions[0::2])
    if raw_keys is None:
        return None
    seen: set[str] = set()
    out: list[exp.Expr] = []
    for k_expr in raw_keys:
        if eval_lit_fn is not None:
            kv = eval_lit_fn(k_expr, ctes, vis, smap)
        else:
            ku = unwrap_parens(k_expr)
            kv = str(ku.this) if isinstance(ku, exp.Literal) and ku.is_string else UNKNOWN
        if not isinstance(kv, str) or kv in seen:
            return None
        seen.add(kv)
        out.append(exp.Literal.string(kv))
    return out


def resolve_json_keys_items(
    arg0: exp.Expr | None,
    arg1: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    eval_lit_fn: EvalLitFn | None,
) -> list[ResolvedItem]:
    if arg0 is None:
        return [(exp.Null(), ctes, vis, smap)]
    steps: list[str | int | None] = []
    if arg1 is not None:
        parsed = parse_json_path_arg(arg1, eval_lit_fn, ctes, vis, smap)
        if parsed is None:
            return [(exp.Null(), ctes, vis, smap)]
        steps = parsed
    raw_vals = eval_json_path_raw(arg0, steps, ctes, vis, smap, eval_lit_fn=eval_lit_fn)
    if raw_vals is None:
        if (
            not steps
            and (key_lits := _json_ctor_key_lits(arg0, ctes, vis, smap, eval_lit_fn)) is not None
        ):
            return [(exp.Array(expressions=key_lits), ctes, vis, smap)]
        return [(exp.Null(), ctes, vis, smap)]
    return [
        (
            exp.Array(expressions=[exp.Literal.string(str(k)) for k in v])
            if isinstance(v, dict)
            else exp.Null(),
            ctes, vis, smap,
        )
        for v in raw_vals
    ]


__all__ = [
    "ProvItem",
    "eval_json_literal_extract",
    "eval_json_path_raw",
    "extract_json_extract_items",
    "extract_json_extract_items_prov",
    "is_json_array_literal",
    "nullif_eq",
    "parse_json_path_arg",
    "resolve_json_keys_items",
    "resolve_nullif_operands",
]
