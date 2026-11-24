"""Boolean-predicate nullability propagation helper for `_bool_expr_guard.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_bool_expr_const`,
`_bool_expr_containers`, and `_bool_expr_funcs`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_const import (
    UNKNOWN,
    eval_const_bool,
    eval_const_lit,
    same_scalar_kind,
)
from opendv.storage._bool_expr_containers import (
    ResolvedItem,
    SmapType,
    alias_cols,
    extend_cte_nodes,
    select_sources,
    smap_scopes,
    values_row_cells,
)
from opendv.storage._bool_expr_funcs import expr_value_args
from opendv.storage._bool_expr_json import nullif_eq, resolve_nullif_operands
from opendv.storage._bool_expr_vocab import NON_FILTER_ARGS, SEL_ALIAS_ARGS, SORT_DIST_ARGS

_NULL_SAFE_EQ_CLS = getattr(exp, "NullSafeEQ", None)
_NULL_SAFE_NEQ_CLS = getattr(exp, "NullSafeNEQ", None)
_ANY_VALUE_CLS = getattr(exp, "AnyValue", None)
_QUANT_CMP_EXPRS: tuple[type[exp.Expr], ...] = (
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike,
)
_PARAM_REF_TYPES: tuple[type[exp.Expr], ...] = tuple(
    c for c in (getattr(exp, "Parameter", None), getattr(exp, "Placeholder", None)) if c is not None
)


def _select_direct_cols(sel: exp.Select) -> set[str]:
    names: set[str] = set()
    for p in sel.expressions:
        if isinstance(p, exp.Alias) and p.alias:
            names.add(p.alias.lower())
        elif isinstance(p, exp.Column) and not isinstance(p.this, exp.Star) and p.name:
            names.add(p.name.lower())
    return names


def _proj_col_names(
    src: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    rec_vis: frozenset[str] = frozenset(),
) -> set[str]:
    u = unwrap_parens(src)
    if u is None:
        return set()
    if ac := alias_cols(u):
        return set(ac)
    if isinstance(u, exp.Subquery):
        return _proj_col_names(u.this, extend_cte_nodes(u, ctes), vis, rec_vis)
    if isinstance(u, exp.Lateral):
        return _proj_col_names(u.this, ctes, vis, rec_vis)
    if isinstance(u, exp.Table):
        if not u.db and not u.catalog and u.name:
            cn = u.name.lower()
            if cn in ctes:
                cte_n = ctes[cn]
                if cac := alias_cols(cte_n):
                    return set(cac)
                if cn in vis:
                    if cn in rec_vis:
                        return set()
                    anc = unwrap_parens(cte_n.this)
                    while isinstance(anc, exp.SetOperation):
                        anc = unwrap_parens(anc.this)
                    return (
                        _proj_col_names(anc, ctes, vis | {cn}, rec_vis | {cn})
                        if anc is not None
                        else set()
                    )
                return _proj_col_names(cte_n.this, ctes, vis | {cn}, rec_vis)
        return set()
    if (vrows := values_row_cells(u)) is not None:
        width = max((len(r) for r in vrows), default=0)
        return {f"col{i}" for i in range(width)} | {f"column{i + 1}" for i in range(width)}
    if isinstance(u, exp.SetOperation):
        nc = extend_cte_nodes(u, ctes)
        return (
            _proj_col_names(u.this, nc, vis, rec_vis)
            | _proj_col_names(u.expression, nc, vis, rec_vis)
        )
    if not isinstance(u, exp.Select):
        return set()
    nc = extend_cte_nodes(u, ctes)
    names = _select_direct_cols(u)
    srcs: SmapType | None = None
    for p in u.expressions:
        if isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star)):
            if srcs is None:
                srcs = select_sources(u)
            qt = p.table.lower() if isinstance(p, exp.Column) and p.table else ""
            tgts = ([srcs[qt]] if qt in srcs else []) if qt else list(srcs.values())
            for t in tgts:
                names.update(alias_cols(t) or _proj_col_names(t, nc, vis, rec_vis))
    return names


def _bound_in_select(
    sel: exp.Select, ctes: dict[str, exp.CTE], vis: frozenset[str],
) -> tuple[set[str], set[str]]:
    srcs = select_sources(sel)
    cols: set[str] = set()
    for src in srcs.values():
        cols.update(alias_cols(src) or _proj_col_names(src, ctes, vis))
    return set(srcs.keys()), cols


def _smap_bindings(
    smap: SmapType | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
) -> tuple[frozenset[str], frozenset[str]]:
    if not smap:
        return frozenset(), frozenset()
    bt: set[str] = set()
    bc: set[str] = set()
    for sc, is_opq in reversed(smap_scopes(smap)):
        if is_opq:
            bc.clear()
        for k, src in sc.items():
            if not _has_external_ref(src, ctes, vis, frozenset(bt), frozenset(bc)):
                if not k.startswith("__anon_"):
                    bt.add(k)
                bc.update(alias_cols(src) or _proj_col_names(src, ctes, vis))
            elif not k.startswith("__anon_"):
                bt.discard(k)
    return frozenset(bt), frozenset(bc)


def _has_external_ref(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str] = frozenset(),
    bt: frozenset[str] = frozenset(),
    bc: frozenset[str] = frozenset(),
    snf: bool = False,
) -> bool:
    if node is None:
        return False
    if _PARAM_REF_TYPES and isinstance(node, _PARAM_REF_TYPES):
        return True
    if isinstance(node, exp.Table):
        if node.db or node.catalog:
            return True
        cn = (node.name or "").lower()
        if cn in ctes:
            if cn in vis:
                return False
            return _has_external_ref(ctes[cn].this, ctes, vis | {cn}, snf=snf)
        return True
    if isinstance(node, exp.Column):
        return node.table.lower() not in bt if node.table else (node.name or "").lower() not in bc
    if isinstance(node, exp.Select):
        nc = extend_cte_nodes(node, ctes)
        sbt, sbc = _bound_in_select(node, nc, vis)
        nbt, nbc = bt | sbt, bc | sbc
        oc = _select_direct_cols(node)
        return any(
            _has_external_ref(
                ch, nc, vis, nbt,
                nbc | oc if ch.arg_key in SEL_ALIAS_ARGS else nbc,
                snf,
            )
            for ch in node.iter_expressions()
            if not (snf and ch.arg_key in NON_FILTER_ARGS)
        )
    if values_row_cells(node) is not None or isinstance(node, (exp.SetOperation, exp.Subquery)):
        nc = extend_cte_nodes(node, ctes)
        oc = (
            _proj_col_names(node.this if isinstance(node, exp.Subquery) else node, nc, vis)
            if not snf and any(node.args.get(k) is not None for k in SORT_DIST_ARGS)
            else set()
        )
        return any(
            _has_external_ref(
                ch, nc, vis, bt, bc | oc if ch.arg_key in SORT_DIST_ARGS else bc, snf,
            )
            for ch in node.iter_expressions()
            if not (snf and ch.arg_key in NON_FILTER_ARGS)
        )
    return any(_has_external_ref(ch, ctes, vis, bt, bc, snf) for ch in node.iter_expressions())


def _has_agg_outside_win(sel: exp.Select) -> bool:
    def _prune(n: exp.Expr) -> bool:
        return n is not sel and isinstance(n, (exp.Select, exp.Subquery, exp.CTE, exp.Window))

    return any(
        isinstance(n, (exp.AggFunc, exp.Filter, exp.WithinGroup, exp.Anonymous))
        for n in sel.walk(prune=_prune)
        if n is not sel
    )


def _is_provably_empty_scalar_query(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> bool:
    u = unwrap_parens(node)
    if isinstance(u, exp.Subquery):
        nc = extend_cte_nodes(u, ctes)
        lim = u.args.get("limit")
        if lim is not None:
            lim_e = lim.expression if isinstance(lim, exp.Limit) else lim
            lv = eval_const_lit(lim_e, nc, vis, smap) if isinstance(lim_e, exp.Expr) else UNKNOWN
            if isinstance(lv, int) and not isinstance(lv, bool) and lv <= 0:
                return True
        return _is_provably_empty_scalar_query(u.this, nc, vis, smap)
    if isinstance(u, exp.Select):
        nc = extend_cte_nodes(u, ctes)
        lim = u.args.get("limit")
        if lim is not None:
            lim_e = lim.expression if isinstance(lim, exp.Limit) else lim
            lv = eval_const_lit(lim_e, nc, vis, smap) if isinstance(lim_e, exp.Expr) else UNKNOWN
            if isinstance(lv, int) and not isinstance(lv, bool) and lv <= 0:
                return True
        if not u.args.get("group") and not u.args.get("having") and not _has_agg_outside_win(u):
            wh = u.args.get("where")
            if wh is not None:
                cond = wh.this if isinstance(wh, exp.Where) else wh
                if isinstance(cond, exp.Expr) and eval_const_bool(cond, nc, vis, smap) is False:
                    return True
            off = u.args.get("offset")
            if off is not None and u.args.get("from_") is None and not u.args.get("joins"):
                off_e = off.expression if isinstance(off, exp.Offset) else off
                ov = (
                    eval_const_lit(off_e, nc, vis, smap)
                    if isinstance(off_e, exp.Expr)
                    else UNKNOWN
                )
                if isinstance(ov, int) and not isinstance(ov, bool) and ov >= 1:
                    return True
    return False


class CheckBoolFn(Protocol):
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


ResolveSrcFn = Callable[
    [exp.Expr | None, str | None, dict[str, exp.CTE], frozenset[str], SmapType | None],
    list[ResolvedItem],
]
UnpackArrFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None],
    tuple[list[ResolvedItem], bool],
]


def _quantified_rhs_target(rhs: exp.Expr | None) -> tuple[bool, exp.Expr | None]:
    u = unwrap_parens(rhs)
    if isinstance(u, (exp.Any, exp.All)):
        return True, unwrap_parens(u.this)
    if isinstance(u, exp.Anonymous) and u.name.upper() in ("ANY", "ALL", "SOME"):
        return True, unwrap_parens(u.expressions[0]) if len(u.expressions) == 1 else None
    if (
        _ANY_VALUE_CLS is not None
        and isinstance(u, _ANY_VALUE_CLS)
        and isinstance(u.parent, _QUANT_CMP_EXPRS)
        and u.arg_key == "expression"
    ):
        return True, unwrap_parens(u.this)
    return False, None


def eval_bool_pred_null(
    cur: exp.Expr,
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckBoolFn,
    resolve_src_fn: ResolveSrcFn,
    unpack_arr_fn: UnpackArrFn | None = None,
) -> bool:
    if not an:
        return False
    if isinstance(cur, (exp.Boolean, exp.Is, exp.Exists)) or (
        (_NULL_SAFE_EQ_CLS is not None and isinstance(cur, _NULL_SAFE_EQ_CLS))
        or (_NULL_SAFE_NEQ_CLS is not None and isinstance(cur, _NULL_SAFE_NEQ_CLS))
    ):
        return False
    if isinstance(cur, exp.Not):
        return w_fn(cur.this, True, ctes, vis, smap, ab=False)
    if isinstance(cur, exp.Escape):
        return w_fn(cur.this, True, ctes, vis, smap, ab=False) or w_fn(
            cur.expression, True, ctes, vis, smap, ab=False,
        )
    if isinstance(cur, exp.Func) and cur.args.get("safe"):
        return eval_const_lit(cur, ctes, vis, smap) in (None, UNKNOWN)
    if isinstance(cur, exp.And):
        if (
            eval_const_lit(cur.this, ctes, vis, smap) is False
            or eval_const_lit(cur.expression, ctes, vis, smap) is False
        ):
            return False
        return w_fn(cur.this, True, ctes, vis, smap, ab=False) or w_fn(
            cur.expression, True, ctes, vis, smap, ab=False,
        )
    if isinstance(cur, exp.Or):
        if (
            eval_const_lit(cur.this, ctes, vis, smap) is True
            or eval_const_lit(cur.expression, ctes, vis, smap) is True
        ):
            return False
        return w_fn(cur.this, True, ctes, vis, smap, ab=False) or w_fn(
            cur.expression, True, ctes, vis, smap, ab=False,
        )
    if isinstance(cur, exp.In):
        if eval_const_lit(cur, ctes, vis, smap) is True:
            return False
        if w_fn(cur.this, True, ctes, vis, smap, ab=False) or any(
            w_fn(e, True, ctes, vis, smap, ab=False) for e in cur.expressions
        ):
            return True
        if (un_n := cur.args.get("unnest")) is not None and w_fn(
            un_n, True, ctes, vis, smap, ab=False,
        ):
            return True
        if (q_n := cur.args.get("query")) is not None:
            res = resolve_src_fn(q_n, None, ctes, vis, smap)
            return any(w_fn(ex, True, cc, nv, sm, ab=False) for ex, cc, nv, sm in res)
        return False
    if isinstance(cur, exp.Between):
        if eval_const_lit(cur, ctes, vis, smap) in (True, False):
            return False
        return any(
            w_fn(a, True, ctes, vis, smap, ab=False)
            for a in (cur.this, cur.args.get("low"), cur.args.get("high"))
        )
    if isinstance(cur, _QUANT_CMP_EXPRS):
        is_q, target = _quantified_rhs_target(cur.expression)
        if is_q:
            if w_fn(cur.this, True, ctes, vis, smap, ab=False):
                return True
            if target is None:
                return True
            if isinstance(target, (exp.Subquery, exp.Select, exp.SetOperation, exp.Values)):
                res = resolve_src_fn(target, None, ctes, vis, smap)
                return any(w_fn(ex, True, cc, nv, sm, ab=False) for ex, cc, nv, sm in res)
            if w_fn(target, True, ctes, vis, smap, ab=False):
                return True
            if unpack_arr_fn is not None:
                its, _ = unpack_arr_fn(target, ctes, vis, smap)
                return any(w_fn(ex, True, cc, nv, sm, ab=False) for ex, cc, nv, sm in its)
            return False
    return any(w_fn(a, True, ctes, vis, smap, ab=False) for a in expr_value_args(cur)) or (
        eval_const_lit(cur, ctes, vis, smap) is None
    )


def eval_nullif_null(
    cur: exp.Nullif,
    an: bool,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    w_fn: CheckBoolFn,
    rcol_fn: Callable[..., list[ResolvedItem]],
    rsrc_fn: ResolveSrcFn,
    *,
    ab: bool = True,
) -> bool:
    if w_fn(cur.this, an, ctes, vis, smap, ab=ab):
        return True
    ua, ub = unwrap_parens(cur.this), unwrap_parens(cur.expression)
    if an and ua is not None and ub is not None:
        if ua == ub:
            return True
        ra = resolve_nullif_operands(ua, smap, ctes, vis, rcol_fn, rsrc_fn)
        rb = resolve_nullif_operands(ub, smap, ctes, vis, rcol_fn, rsrc_fn)
        if ra and rb:
            if any(nullif_eq(ai, bj, eval_const_lit) for ai in ra for bj in rb):
                return True
            for a, ac, av, asm in ra:
                abt, abc = _smap_bindings(asm, ac, av)
                if _has_external_ref(a, ac, av, abt, abc, True):
                    continue
                for b, bc, bv, bsm in rb:
                    bbt, bbc = _smap_bindings(bsm, bc, bv)
                    if not _has_external_ref(b, bc, bv, bbt, bbc, True):
                        if _is_provably_empty_scalar_query(b, bc, bv, bsm):
                            continue
                        la = (
                            None
                            if _is_provably_empty_scalar_query(a, ac, av, asm)
                            else eval_const_lit(a, ac, av, asm)
                        )
                        lb = eval_const_lit(b, bc, bv, bsm)
                        if lb is None:
                            continue
                        if la is UNKNOWN or lb is UNKNOWN or not same_scalar_kind(la, lb):
                            return True
    return False


__all__ = ["eval_bool_pred_null", "eval_nullif_null"]
