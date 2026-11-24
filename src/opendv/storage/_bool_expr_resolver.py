"""Subquery/CTE/VALUES/UNNEST/LATERAL/Struct column resolver for `_bool_expr_guard.py`.

Strictly Layer 2a: imports only sqlglot, stdlib, `_ast_stmt_rules`, and `_bool_expr_containers`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_containers import (
    ColEntry,
    ResolvedItem,
    ScopedSmap,
    SmapType,
    alias_cols,
    apply_alias_cols,
    extend_cte_nodes,
    flatten_set_op,
    is_array_ctor,
    merge_select_smap,
    smap_scopes,
    values_row_cells,
)
from opendv.storage._bool_expr_maps import extract_struct_field

ContainerFn = Callable[
    [exp.Expr | None, dict[str, exp.CTE], frozenset[str], SmapType | None, int],
    list[ResolvedItem],
]
_IMPLICIT_VALUES_COL_RE = re.compile(r"^(col|column|c)(\d+)$", re.IGNORECASE)


def _implicit_values_col_idx(col_name: str) -> int | None:
    m = _IMPLICIT_VALUES_COL_RE.match(col_name)
    if m is None:
        return None
    prefix, num = m.group(1).lower(), int(m.group(2))
    return num if prefix == "col" else (num - 1 if num >= 1 else None)


def _is_star_expr(node: exp.Expr) -> bool:
    return isinstance(node, exp.Star) or (
        isinstance(node, exp.Column) and isinstance(node.this, exp.Star)
    )


def _proj_out_name(p: exp.Expr) -> str:
    if isinstance(p, exp.Alias):
        return p.alias.lower()
    return p.name.lower() if isinstance(p, exp.Column) else ""


def _build_scoped_smap(
    scopes: tuple[tuple[dict[str, exp.Expr], bool], ...],
) -> ScopedSmap:
    merged: dict[str, exp.Expr] = {}
    for sc, _ in reversed(scopes):
        merged.update(sc)
    return ScopedSmap(merged, scopes=scopes)


def smap_without_tgt(smap: SmapType, tgt: exp.Expr) -> SmapType:
    scopes = tuple(
        ({k: v for k, v in sc.items() if v is not tgt}, opq)
        for sc, opq in smap_scopes(smap)
    )
    return _build_scoped_smap(scopes)


def _unpack_array_expr(
    ue: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None,
    depth: int = 0,
    container_fn: ContainerFn | None = None,
) -> list[ResolvedItem]:
    ue_u = unwrap_parens(ue)
    if ue_u is None or depth > 12:
        return []
    if container_fn is not None:
        resolved = container_fn(ue_u, ctes, vis, smap, depth + 1)
    elif is_array_ctor(ue_u):
        resolved = [(ue_u, ctes, vis, smap)]
    elif isinstance(ue_u, exp.Column):
        resolved = resolve_col_from_smap(ue_u, smap, ctes, vis) if smap else []
    elif isinstance(ue_u, (exp.Subquery, exp.Select)):
        cols = expand_cols(ue_u, ctes, vis, smap)
        resolved = cols[0][1] or [] if len(cols) == 1 and cols[0][1] is not None else []
    else:
        resolved = [(ue_u, ctes, vis, smap)]
    out: list[ResolvedItem] = []
    for ex, ec, ev, es in resolved:
        ex_u = unwrap_parens(ex)
        if ex_u is None:
            continue
        if is_array_ctor(ex_u):
            out.extend((el, ec, ev, es) for el in ex_u.expressions)
        elif not isinstance(ex_u, exp.Column):
            out.append((ex, ec, ev, es))
    return out


def expand_cols(
    src: exp.Expr | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    outer_smap: SmapType | None = None,
    container_fn: ContainerFn | None = None,
) -> list[ColEntry]:
    src = unwrap_parens(src)
    if src is None:
        return []
    if isinstance(src, (exp.CTE, exp.Subquery)):
        cols = expand_cols(src.this, extend_cte_nodes(src, ctes), vis, outer_smap, container_fn)
        return apply_alias_cols(cols, alias_cols(src))
    if isinstance(src, exp.Lateral):
        cols = expand_cols(src.this, ctes, vis, outer_smap, container_fn)
        return apply_alias_cols(cols, alias_cols(src))
    if isinstance(src, exp.Unnest):
        if not src.expressions:
            return []
        multi = len(src.expressions) > 1
        ucols: list[ColEntry] = [
            (
                f"__u_{idx}" if multi else "unnest",
                _unpack_array_expr(ue, ctes, vis, outer_smap, 0, container_fn),
            )
            for idx, ue in enumerate(src.expressions)
        ]
        return apply_alias_cols(ucols, alias_cols(src))
    if isinstance(src, exp.Table):
        if not src.db and not src.catalog and src.name:
            cn = src.name.lower()
            if cn in ctes and cn not in vis:
                cols = expand_cols(ctes[cn], ctes, vis | {cn}, outer_smap, container_fn)
                return apply_alias_cols(cols, alias_cols(src))
        return [(None, None)]
    if (vrows := values_row_cells(src)) is not None:
        if not vrows or not vrows[0] or any(len(r) != len(vrows[0]) for r in vrows):
            return []
        vcols: list[ColEntry] = [
            (f"__v_{i}", [(r[i], ctes, vis, outer_smap) for r in vrows])
            for i in range(len(vrows[0]))
        ]
        return apply_alias_cols(vcols, alias_cols(src))
    if isinstance(src, exp.SetOperation):
        by_name = bool(src.args.get("by_name"))
        branches = flatten_set_op(src, ctes, by_name=by_name)
        if not branches:
            return []
        bcols = [expand_cols(b, cc, vis, outer_smap, container_fn) for b, cc in branches]
        if any(not bc for bc in bcols):
            return []
        if by_name:
            col_names: list[str] = []
            seen_nm: set[str] = set()
            for bc in bcols:
                for nm, items in bc:
                    if nm is None or items is None:
                        return [(None, None)]
                    if nm not in seen_nm:
                        seen_nm.add(nm)
                        col_names.append(nm)
            merged_bn: list[ColEntry] = []
            for nm in col_names:
                combined_bn: list[ResolvedItem] = []
                for bc in bcols:
                    hits = [
                        it for cnm, cits in bc if cnm == nm and cits is not None for it in cits
                    ]
                    combined_bn.extend(hits or ((exp.Null(), ctes, vis, None),))
                merged_bn.append((nm, combined_bn))
            return merged_bn
        merged: list[ColEntry] = []
        for idx, (nm, _items) in enumerate(bcols[0]):
            if any(idx >= len(bc) or bc[idx][1] is None for bc in bcols):
                merged.append((None, None))
                break
            merged.append((nm, [it for bc in bcols for it in (bc[idx][1] or ())]))
        return merged
    if isinstance(src, exp.Select):
        nc, smap, own_smap, _ = merge_select_smap(src, ctes, outer_smap)
        out: list[ColEntry] = []
        for p in src.expressions:
            if _is_star_expr(p):
                star_n = p.this if isinstance(p, exp.Column) else p
                qt = p.table.lower() if isinstance(p, exp.Column) and p.table else ""
                tgts = ([own_smap[qt]] if qt in own_smap else []) if qt else list(own_smap.values())
                if not tgts:
                    out.append((None, None))
                excl = (
                    {(e.name or "").lower() for e in (star_n.args.get("except_") or ()) if e.name}
                    if isinstance(star_n, exp.Star)
                    else set()
                )
                repl = (
                    {
                        r.alias.lower(): resolve_select_expr(
                            src, unwrap_parens(r.this) or r.this, nc, vis, smap, container_fn,
                        )
                        for r in (star_n.args.get("replace") or ())
                        if isinstance(r, exp.Alias) and r.alias and r.this is not None
                    }
                    if isinstance(star_n, exp.Star)
                    else {}
                )
                ren = (
                    {
                        (r.this.name or "").lower(): r.alias.lower()
                        for r in (star_n.args.get("rename") or ())
                        if isinstance(r, exp.Alias)
                        and isinstance(r.this, (exp.Column, exp.Identifier))
                        and r.this.name
                        and r.alias
                    }
                    if isinstance(star_n, exp.Star)
                    else {}
                )
                matched_repl: set[str] = set()
                for t in tgts:
                    t_outer = (
                        smap_without_tgt(smap, t)
                        if isinstance(t, (exp.Lateral, exp.Unnest, exp.Subquery))
                        else None
                    )
                    for cnm, citems in expand_cols(t, nc, vis, t_outer, container_fn):
                        if cnm is not None and cnm in excl:
                            continue
                        if cnm is not None and cnm in repl:
                            matched_repl.add(cnm)
                            citems = repl[cnm]
                        out.append((ren.get(cnm, cnm) if cnm is not None else None, citems))
                for r_nm, r_items in repl.items():
                    if r_nm not in matched_repl:
                        out.append((ren.get(r_nm, r_nm), r_items))
            else:
                ue = unwrap_parens(p.this if isinstance(p, exp.Alias) else p)
                items = (
                    resolve_select_expr(src, ue, nc, vis, smap, container_fn)
                    if ue is not None
                    else []
                )
                out.append((_proj_out_name(p), items))
        return out
    return []


def resolve_source_col(
    src: exp.Expr | None,
    col_name: str | None,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    outer_smap: SmapType | None = None,
    container_fn: ContainerFn | None = None,
) -> list[ResolvedItem]:
    src_u = unwrap_parens(src)
    cols = expand_cols(src_u, ctes, vis, outer_smap, container_fn)
    if not cols:
        return []
    if col_name is None:
        return cols[0][1] or [] if len(cols) == 1 else []
    for nm, items in cols:
        if items is not None and nm == col_name:
            return items
    if (
        isinstance(src_u, exp.Unnest)
        and len(cols) == 1
        and not alias_cols(src_u)
        and (al := (src_u.alias or "").lower())
        and col_name == al
    ):
        return cols[0][1] or []
    if (vidx := _implicit_values_col_idx(col_name)) is not None and 0 <= vidx < len(cols):
        nm, items = cols[vidx]
        if items is not None and nm == f"__v_{vidx}":
            return items
    return []


def _walk_struct_fields(
    items: list[ResolvedItem],
    fields: Sequence[str],
    depth: int = 0,
    container_fn: ContainerFn | None = None,
) -> list[ResolvedItem]:
    cur = items
    for fk in fields:
        nxt: list[ResolvedItem] = []
        for ex, ec, ev, es in cur:
            ex_u = unwrap_parens(ex)
            if ex_u is None:
                continue
            if isinstance(ex_u, (exp.Struct, exp.ToMap)):
                resolved: list[ResolvedItem] = [(ex_u, ec, ev, es)]
            elif container_fn is not None and depth < 8:
                resolved = container_fn(ex_u, ec, ev, es, depth + 1)
            elif isinstance(ex_u, exp.Column) and es and depth < 8:
                res = resolve_col_from_smap(ex_u, es, ec, ev, container_fn)
                nxt.extend(_walk_struct_fields(res, (fk,), depth + 1, container_fn))
                continue
            else:
                resolved = []
            for rx, rc, rv, rs in resolved:
                rx_u = unwrap_parens(rx)
                if isinstance(rx_u, exp.Struct):
                    nxt.extend((fv, rc, rv, rs) for fv in extract_struct_field(rx_u, fk))
                elif isinstance(rx_u, exp.ToMap) and isinstance(
                    st := unwrap_parens(rx_u.this), exp.Struct
                ):
                    nxt.extend((fv, rc, rv, rs) for fv in extract_struct_field(st, fk))
        cur = nxt
        if not cur:
            break
    return cur


def resolve_col_from_smap(
    col: exp.Column,
    smap: SmapType,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    container_fn: ContainerFn | None = None,
) -> list[ResolvedItem]:
    if isinstance(col.this, exp.Star):
        return []
    parts = [p.name.lower() for p in col.parts if p.name]
    if not parts:
        return []

    scopes = smap_scopes(smap)

    def _tgt_outer(idx: int, tgt: exp.Expr) -> SmapType | None:
        if not isinstance(tgt, (exp.Lateral, exp.Unnest, exp.Subquery)):
            return None
        tail = scopes[idx:]
        sc0 = {k: v for k, v in tail[0][0].items() if v is not tgt}
        return _build_scoped_smap(((sc0, tail[0][1]), *tail[1:]))

    if len(parts) >= 2:
        for idx, (sc, _opq) in enumerate(scopes):
            if (tgt := sc.get(parts[0])) is None:
                continue
            outer_t = _tgt_outer(idx, tgt)
            items = resolve_source_col(tgt, parts[1], ctes, vis, outer_t, container_fn)
            f_idx = 2
            tgt_u = unwrap_parens(tgt)
            if (
                not items
                and isinstance(tgt_u, exp.Unnest)
                and not alias_cols(tgt_u)
                and len(tgt_u.expressions) == 1
            ):
                items = resolve_source_col(tgt, None, ctes, vis, outer_t, container_fn)
                f_idx = 1
            if items:
                return (
                    _walk_struct_fields(items, parts[f_idx:], 0, container_fn)
                    if f_idx < len(parts)
                    else items
                )
            cols = expand_cols(tgt_u, ctes, vis, outer_t, container_fn)
            if not cols or any(nm is None or ci is None for nm, ci in cols):
                return []

    for idx, (sc, is_opq) in enumerate(scopes):
        matches = [
            res
            for s in sc.values()
            if (res := resolve_source_col(s, parts[0], ctes, vis, _tgt_outer(idx, s), container_fn))
        ]
        if matches:
            items = [item for m in matches for item in m]
            return (
                _walk_struct_fields(items, parts[1:], 0, container_fn)
                if len(parts) > 1
                else items
            )
        if is_opq:
            return []
    return []


def resolve_select_expr(
    sel: exp.Select,
    proj: exp.Expr,
    ctes: dict[str, exp.CTE],
    vis: frozenset[str],
    smap: SmapType | None = None,
    container_fn: ContainerFn | None = None,
) -> list[ResolvedItem]:
    eff_smap = merge_select_smap(sel, ctes, None)[1] if smap is None else smap
    if isinstance(proj, exp.Column) and (
        res := resolve_col_from_smap(proj, eff_smap, ctes, vis, container_fn)
    ):
        return res
    return [(proj, ctes, vis, eff_smap)]


__all__ = [
    "ContainerFn",
    "expand_cols",
    "resolve_col_from_smap",
    "resolve_select_expr",
    "resolve_source_col",
    "smap_without_tgt",
]
