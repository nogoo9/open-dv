"""Lambda binding, higher-order transforms, single-row checks, and fail-closed guards.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`,
`_bool_expr_containers`, and `_bool_expr_extract`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    ResolvedItem,
    SmapType,
    container_func_name,
    extend_cte_nodes,
    is_array_ctor,
    is_known_container_ctor,
    is_single_row_const_rel,
    is_unknown_container_or_lambda,
    merge_select_smap,
)
from opendv.storage._bool_expr_quantiles import extract_array_cast_elem
from opendv.storage._bool_expr_vocab import (
    KNOWN_CONTAINER_ANON_CTORS,
    SAFE_CONTAINER_SCALAR_FUNCS,
    SELECT_DISALLOWED_CLAUSES,
)

ResFn = Callable[[exp.Expr | None], list[ResolvedItem]]
EvalBoolFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], bool | None
]
EvalLitFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None], object
]


def _subst_lambda_node(node: exp.Expr, active: dict[str, exp.Expr]) -> exp.Expr:
    if isinstance(node, exp.Lambda):
        inner = {
            p.name.lower()
            for p in node.expressions
            if isinstance(p, exp.Identifier) and p.name
        }
        sub = {k: v for k, v in active.items() if k not in inner}
        cp_lam = node.copy()
        if node.this is not None and sub:
            cp_lam.set("this", _subst_lambda_node(node.this, sub))
        return cp_lam
    if isinstance(node, exp.Column):
        if not node.db and not node.catalog:
            if not node.table and node.name.lower() in active:
                return active[node.name.lower()].copy()
            if node.table and node.table.lower() in active:
                return exp.Dot(this=active[node.table.lower()].copy(), expression=node.this.copy())
        return node.copy()
    if isinstance(node, exp.Identifier):
        p = node.parent
        ak = node.arg_key
        blocked_types = (
            exp.PropertyEQ, exp.Property, exp.ColumnDef, exp.Alias, exp.TableAlias, exp.Table,
        )
        blocked_pos = (
            (isinstance(p, exp.Dot) and ak == "expression")
            or (isinstance(p, blocked_types) and ak in ("this", "alias"))
            or (isinstance(p, exp.Lambda) and ak == "expressions")
        )
        if not blocked_pos and node.name.lower() in active:
            return active[node.name.lower()].copy()
        return node.copy()
    cp = node.copy()
    for k, val in list(node.args.items()):
        if isinstance(val, exp.Expr):
            cp.set(k, _subst_lambda_node(val, active))
        elif isinstance(val, list):
            cp.set(
                k,
                [_subst_lambda_node(it, active) if isinstance(it, exp.Expr) else it for it in val],
            )
    return cp


def lambda_param_names(lam: exp.Lambda) -> list[str]:
    out: list[str] = []
    for p in lam.expressions:
        if (isinstance(p, exp.Identifier) and p.name) or (
            isinstance(p, exp.Column) and not p.table and p.name
        ):
            out.append(p.name.lower())
        else:
            return []
    return out


def instantiate_lambda_1(
    lam: exp.Lambda, elem_items: Sequence[ResolvedItem], fallback: ResolvedItem,
) -> list[ResolvedItem] | None:
    pnames = lambda_param_names(lam)
    if lam.this is None or not pnames:
        return None
    items = list(elem_items) if elem_items else [fallback]
    idx_col = exp.Column(this=exp.Identifier(this="__lambda_idx"))
    out: list[ResolvedItem] = []
    for ex, ec, ev, es in items[:256]:
        bindings: dict[str, exp.Expr] = {pnames[0]: ex}
        for extra_p in pnames[1:]:
            bindings[extra_p] = idx_col
        out.append((_subst_lambda_node(lam.this, bindings), ec, ev, es))
    if len(items) > 256:
        fb_bind: dict[str, exp.Expr] = {pnames[0]: fallback[0]}
        for extra_p in pnames[1:]:
            fb_bind[extra_p] = idx_col
        out.append((_subst_lambda_node(lam.this, fb_bind), fallback[1], fallback[2], fallback[3]))
    return out


def instantiate_lambda_reduce(
    lam: exp.Lambda,
    acc_items: Sequence[ResolvedItem],
    elem_items: Sequence[ResolvedItem],
    fallback: ResolvedItem,
) -> list[ResolvedItem] | None:
    pnames = lambda_param_names(lam)
    if lam.this is None or len(pnames) != 2:
        return None
    accs = list(acc_items) if acc_items else [fallback]
    elems = list(elem_items) if elem_items else [fallback]
    fb_elem = exp.Column(this=exp.Identifier(this="__elem"))
    out: list[ResolvedItem] = []
    for ax, ac, av, asm in accs[:4]:
        for bx, *_ in elems[:16]:
            out.append((_subst_lambda_node(lam.this, {pnames[0]: ax, pnames[1]: bx}), ac, av, asm))
    if len(elems) > 1 or len(accs) > 4:
        for bx, *_ in elems[:256]:
            out.append((
                _subst_lambda_node(lam.this, {pnames[0]: fallback[0], pnames[1]: bx}),
                fallback[1], fallback[2], fallback[3],
            ))
        for bx, *_ in elems[256:2048]:
            out.append((
                _subst_lambda_node(lam.this, {pnames[0]: fallback[0], pnames[1]: bx}),
                fallback[1], fallback[2], fallback[3],
            ))
        out.append((
            _subst_lambda_node(lam.this, {pnames[0]: fallback[0], pnames[1]: fb_elem}),
            fallback[1], fallback[2], fallback[3],
        ))
        if len(elems) > 2048 or len(accs) > 16:
            out.extend((
                (exp.Null(), fallback[1], fallback[2], fallback[3]),
                (exp.Boolean(this=True), fallback[1], fallback[2], fallback[3]),
            ))
    return out


def _unpack_elems(items: list[ResolvedItem]) -> tuple[list[ResolvedItem], bool, list[ResolvedItem]]:
    elems: list[ResolvedItem] = []
    has_empty = False
    null_items: list[ResolvedItem] = []
    for ex, ec, ev, es in items:
        ex_u = unwrap_parens(ex)
        if ex_u is None:
            continue
        if isinstance(ex_u, exp.Null):
            null_items.append((ex_u, ec, ev, es))
        elif is_array_ctor(ex_u):
            if ex_u.expressions:
                elems.extend((el, ec, ev, es) for el in ex_u.expressions)
            else:
                has_empty = True
        elif (el_cast := extract_array_cast_elem(ex_u)) is not None:
            elems.append((el_cast, ec, ev, es))
        else:
            elems.append((ex, ec, ev, es))
    return elems, has_empty, null_items


def apply_lambda_filter(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    eval_bool: EvalBoolFn | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    lam = unwrap_parens(args[1]) if len(args) >= 2 else None
    if not isinstance(lam, exp.Lambda):
        fb_n = (args[1] if len(args) >= 2 and args[1] is not None else args[0]) if args else None
        return [(fb_n, ctes, vis, smap)] if fb_n is not None else []
    arr_res = res_fn(args[0])
    fb = (exp.Column(this=exp.Identifier(this="__elem")), ctes, vis, smap)
    if eval_bool is None or not arr_res:
        if instantiate_lambda_1(lam, (), fb) is None:
            return [(lam, ctes, vis, smap)]
        return arr_res
    out: list[ResolvedItem] = []
    for ex, ec, ev, es in arr_res:
        ex_u = unwrap_parens(ex)
        if isinstance(ex_u, exp.Null):
            out.append((ex_u, ec, ev, es))
        elif is_array_ctor(ex_u) and ex_u is not None:
            kept: list[exp.Expr] = []
            def_kept: list[exp.Expr] = []
            has_uncertain = False
            for el in ex_u.expressions:
                inst = instantiate_lambda_1(lam, [(el, ec, ev, es)], (el, ec, ev, es))
                if inst is None:
                    return [(lam, ctes, vis, smap)]
                pv = eval_bool(inst[0][0], ec, ev, es)
                if pv is True:
                    kept.append(el)
                    def_kept.append(el)
                elif pv is None:
                    kept.append(el)
                    has_uncertain = True
            out.append((exp.Array(expressions=kept), ec, ev, es))
            if len(def_kept) < len(kept):
                out.append((exp.Array(expressions=def_kept), ec, ev, es))
            if has_uncertain and len(kept) > 1:
                out.extend((exp.Array(expressions=[el]), ec, ev, es) for el in kept)
        else:
            if instantiate_lambda_1(lam, [(ex, ec, ev, es)], (ex, ec, ev, es)) is None:
                return [(lam, ctes, vis, smap)]
            out.append((ex, ec, ev, es))
    return out


def apply_lambda_transform(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    lam = unwrap_parens(args[1]) if len(args) >= 2 else None
    if not isinstance(lam, exp.Lambda):
        fb_n = (args[1] if len(args) >= 2 and args[1] is not None else args[0]) if args else None
        return [(fb_n, ctes, vis, smap)] if fb_n is not None else []
    elems, has_empty, null_items = _unpack_elems(res_fn(args[0]))
    fb = (exp.Column(this=exp.Identifier(this="__elem")), ctes, vis, smap)
    if instantiate_lambda_1(lam, (), fb) is None:
        return [(lam, ctes, vis, smap)]
    out: list[ResolvedItem] = list(null_items)
    if has_empty:
        out.append((exp.Array(expressions=[]), ctes, vis, smap))
    if elems:
        inst = instantiate_lambda_1(lam, elems, fb)
        if inst is None:
            return [(lam, ctes, vis, smap)]
        out.append((exp.Array(expressions=[it[0] for it in inst]), ctes, vis, smap))
    return out


def _lit_to_expr(val: object) -> exp.Expr | None:
    if val is None:
        return exp.Null()
    if isinstance(val, bool):
        return exp.Boolean(this=val)
    if isinstance(val, (int, float)):
        return exp.Literal.number(val)
    if isinstance(val, str):
        return exp.Literal.string(val)
    return None


def _finish_reduce(
    items: list[ResolvedItem],
    args: tuple[exp.Expr | None, ...],
    fb: ResolvedItem,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
) -> list[ResolvedItem]:
    if len(args) >= 4 and args[3] is not None:
        f_lam = unwrap_parens(args[3])
        if not isinstance(f_lam, exp.Lambda):
            return [(args[3], ctes, vis, smap)]
        f_inst = instantiate_lambda_1(f_lam, items, fb)
        if f_inst is None:
            return [(f_lam, ctes, vis, smap)]
        return f_inst
    return items


def apply_lambda_reduce(
    args: tuple[exp.Expr | None, ...],
    res_fn: ResFn,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    eval_lit: EvalLitFn | None = None,
) -> list[ResolvedItem]:
    m_lam = unwrap_parens(args[2]) if len(args) >= 3 else None
    if not isinstance(m_lam, exp.Lambda):
        fb_n = (args[2] if len(args) >= 3 and args[2] is not None else args[0]) if args else None
        return [(fb_n, ctes, vis, smap)] if fb_n is not None else []
    elems, has_empty, null_items = _unpack_elems(res_fn(args[0]))
    fb = (exp.Column(this=exp.Identifier(this="__acc")), ctes, vis, smap)
    acc_items = res_fn(args[1]) if args[1] is not None else elems
    empty_out: list[ResolvedItem] = list(null_items)
    if has_empty:
        if args[1] is None:
            empty_out.append((exp.Null(), ctes, vis, smap))
        else:
            empty_out.extend(_finish_reduce(acc_items, args, fb, ctes, vis, smap))
    if not elems:
        return empty_out or [(exp.Null(), ctes, vis, smap)]
    r_items: list[ResolvedItem] | None = None
    m_pnames = lambda_param_names(m_lam)
    if (
        (args[1] is None or len(acc_items) == 1)
        and elems
        and len(elems) <= 1024
        and eval_lit is not None
        and m_lam.this is not None
        and len(m_pnames) == 2
    ):
        step_elems = elems if args[1] is not None else elems[1:]
        cur_acc = acc_items[0] if args[1] is not None else elems[0]
        folded = True
        for ex, *_ in step_elems:
            step_e = _subst_lambda_node(m_lam.this, {m_pnames[0]: cur_acc[0], m_pnames[1]: ex})
            val = eval_lit(step_e, cur_acc[1], cur_acc[2], cur_acc[3])
            lit_e = _lit_to_expr(val)
            if lit_e is None:
                folded = False
                break
            cur_acc = (lit_e, cur_acc[1], cur_acc[2], cur_acc[3])
        if folded:
            r_items = [cur_acc]
    if r_items is None:
        m_inst = instantiate_lambda_reduce(m_lam, acc_items, elems, fb)
        if m_inst is None:
            return [(m_lam, ctes, vis, smap)]
        r_items = m_inst
    r_items = _finish_reduce(r_items, args, fb, ctes, vis, smap)
    if args[1] is None and len(elems) == 1:
        r_items = [*r_items, *elems]
    return [*r_items, *empty_out] if empty_out else r_items


def try_node_unsafe(
    node: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    eval_lit_fn: EvalLitFn,
    resolve_col_fn: Callable[
        [exp.Column, SmapType, dict[str, exp.CTE], frozenset[str]], list[ResolvedItem]
    ],
    depth: int = 0,
) -> bool:
    if node is None:
        return False
    if depth > 12:
        return True
    if isinstance(node, (exp.Cast, exp.TryCast)):
        dt = node.args.get("to")
        if isinstance(dt, exp.DataType) and dt.is_type(*exp.DataType.INTEGER_TYPES):
            return True
    elif isinstance(node, exp.Convert) and isinstance(node.this, exp.DataType):
        if node.this.is_type(*exp.DataType.INTEGER_TYPES):
            return True
    elif isinstance(node, exp.Subquery):
        return try_node_unsafe(
            node.this, extend_cte_nodes(node, ctes), vis, smap, eval_lit_fn, resolve_col_fn,
            depth + 1,
        )
    elif isinstance(node, exp.Select):
        nc, sel_smap, _, _ = merge_select_smap(node, ctes, smap)
        return any(
            try_node_unsafe(ch, nc, vis, sel_smap, eval_lit_fn, resolve_col_fn, depth + 1)
            for ch in node.iter_expressions()
        )
    elif isinstance(node, exp.Table) and not node.db and not node.catalog and node.name:
        cn = node.name.lower()
        if cn in ctes and cn not in vis:
            return try_node_unsafe(
                ctes[cn].this, ctes, vis | {cn}, None, eval_lit_fn, resolve_col_fn, depth + 1,
            )
    elif smap and isinstance(node, exp.Column):
        res = resolve_col_fn(node, smap, ctes, vis)
        if any(
            try_node_unsafe(ex, c2, v2, s2, eval_lit_fn, resolve_col_fn, depth + 1)
            for ex, c2, v2, s2 in res
        ):
            return True
    sv = eval_lit_fn(node, ctes, vis, smap)
    if isinstance(sv, int) and not isinstance(sv, bool) and abs(sv) > 2147483647:
        return True
    return any(
        try_node_unsafe(ch, ctes, vis, smap, eval_lit_fn, resolve_col_fn, depth)
        for ch in node.iter_expressions()
    )


__all__ = [
    "KNOWN_CONTAINER_ANON_CTORS",
    "SAFE_CONTAINER_SCALAR_FUNCS",
    "SELECT_DISALLOWED_CLAUSES",
    "apply_lambda_filter",
    "apply_lambda_reduce",
    "apply_lambda_transform",
    "container_func_name",
    "instantiate_lambda_1",
    "instantiate_lambda_reduce",
    "is_known_container_ctor",
    "is_single_row_const_rel",
    "is_unknown_container_or_lambda",
    "lambda_param_names",
    "try_node_unsafe",
]
