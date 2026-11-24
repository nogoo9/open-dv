"""Constant-folding helpers for scalar functions, CAST bounds, and CONCAT_WS.

Strictly Layer 2a: imports only math, re, decimal, sqlglot, stdlib, and `_bool_expr_vocab`.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

from sqlglot import exp

from opendv.storage._ast_stmt_rules import unwrap_parens
from opendv.storage._bool_expr_vocab import UNKNOWN, parse_bool_lit, scalar_to_str

_INT_STR_RE = re.compile(r"[+-]?\d+")
_TZ_TEMPORAL_TYPES: tuple[str, ...] = ("TIMESTAMPTZ", "TIMESTAMPLTZ", "TIMESTAMP_TZ", "TIMETZ")


def _is_finite_num(v: object) -> bool:
    return not isinstance(v, bool) and (
        isinstance(v, int) or (isinstance(v, float) and math.isfinite(v))
    )
_SQRT_CLS = getattr(exp, "Sqrt", None)
_POW_CLS = getattr(exp, "Pow", None)
_SIGN_CLS = getattr(exp, "Sign", None)
_ASCII_CLS = getattr(exp, "Ascii", None)
_CHR_CLS = getattr(exp, "Chr", None)
_EXTRACT_CLS = getattr(exp, "Extract", None)

_INT_TYPE_BOUNDS: dict[object, tuple[int, int]] = {
    exp.DataType.Type.TINYINT: (-128, 127),
    exp.DataType.Type.SMALLINT: (-32768, 32767),
    exp.DataType.Type.MEDIUMINT: (-8388608, 8388607),
    exp.DataType.Type.INT: (-2147483648, 2147483647),
    exp.DataType.Type.BIGINT: (-(1 << 63), (1 << 63) - 1),
    exp.DataType.Type.INT128: (-(1 << 127), (1 << 127) - 1),
    exp.DataType.Type.INT256: (-(1 << 255), (1 << 255) - 1),
    exp.DataType.Type.UTINYINT: (0, 255),
    exp.DataType.Type.USMALLINT: (0, 65535),
    exp.DataType.Type.UMEDIUMINT: (0, 16777215),
    exp.DataType.Type.UINT: (0, 4294967295),
    exp.DataType.Type.UBIGINT: (0, (1 << 64) - 1),
    exp.DataType.Type.UINT128: (0, (1 << 128) - 1),
    exp.DataType.Type.UINT256: (0, (1 << 256) - 1),
}


def fold_concat_ws(u: exp.Expr, ev_fn: Callable[[exp.Expr | None], object]) -> object:
    exprs = u.expressions if isinstance(u, (exp.ConcatWs, exp.Anonymous)) else ()
    if not exprs:
        return UNKNOWN
    sep = ev_fn(exprs[0])
    if sep is UNKNOWN:
        return UNKNOWN
    if sep is None:
        return None
    if (sep_s := scalar_to_str(sep)) is None:
        return UNKNOWN
    parts: list[str] = []
    for a in exprs[1:]:
        av = ev_fn(a)
        if av is UNKNOWN:
            return UNKNOWN
        if av is None:
            continue
        if (sv := scalar_to_str(av)) is None:
            return UNKNOWN
        parts.append(sv)
    return sep_s.join(parts)


def fold_cast_val(
    u: exp.Cast, dt: exp.DataType, ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    iv = ev_fn(u.this)
    if dt.is_type(exp.DataType.Type.BOOLEAN):
        return parse_bool_lit(iv, allow_int=True)
    if iv is None:
        return None
    if dt.is_type(*exp.DataType.INTEGER_TYPES):
        ival: int | None = None
        if isinstance(iv, int) and not isinstance(iv, bool):
            ival = iv
        elif (
            isinstance(iv, float)
            and math.isfinite(iv)
            and iv.is_integer()
            and abs(iv) < (1 << 53)
        ):
            ival = int(iv)
        elif isinstance(iv, str) and _INT_STR_RE.fullmatch(iv.strip()):
            try:
                ival = int(iv.strip())
            except ValueError:
                return UNKNOWN
        if ival is not None and (bounds := _INT_TYPE_BOUNDS.get(dt.this)) is not None:
            return ival if bounds[0] <= ival <= bounds[1] else UNKNOWN
        return UNKNOWN
    if dt.is_type(*exp.DataType.REAL_TYPES):
        is_f32 = dt.is_type(exp.DataType.Type.FLOAT)
        try:
            if isinstance(iv, int) and not isinstance(iv, bool):
                if abs(iv) >= (1 << 53):
                    return UNKNOWN
                fv = float(iv)
            elif isinstance(iv, float):
                fv = iv
            elif isinstance(iv, str) and "_" not in iv:
                s = iv.strip()
                if _INT_STR_RE.fullmatch(s) and abs(int(s)) >= (1 << 53):
                    return UNKNOWN
                fv = float(s)
            else:
                return UNKNOWN
            if not math.isfinite(fv):
                return UNKNOWN
            if is_f32 and struct.unpack("!f", struct.pack("!f", fv))[0] != fv:
                return UNKNOWN
            return fv
        except (OverflowError, ValueError):
            return UNKNOWN
    if dt.is_type(*exp.DataType.TEXT_TYPES) and (ts := scalar_to_str(iv)) is not None:
        if not dt.expressions:
            return ts
        if len(dt.expressions) == 1:
            n_val = ev_fn(dt.expressions[0])
            if isinstance(n_val, int) and not isinstance(n_val, bool) and len(ts) <= n_val:
                return ts
    return UNKNOWN


def _trim_val(
    val_e: exp.Expr | None,
    ch_e: exp.Expr | None,
    pos: str,
    ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    sv = ev_fn(val_e)
    if sv is None:
        return None
    if not isinstance(sv, str):
        return UNKNOWN
    chars: str | None = None
    if ch_e is not None:
        cv = ev_fn(ch_e)
        if cv is None:
            return None
        if not isinstance(cv, str):
            return UNKNOWN
        chars = cv
    if pos == "LEADING":
        return sv.lstrip(chars)
    if pos == "TRAILING":
        return sv.rstrip(chars)
    return sv.strip(chars)


def _substr_val(
    val_e: exp.Expr | None,
    st_e: exp.Expr | None,
    ln_e: exp.Expr | None,
    ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    sv = ev_fn(val_e)
    st = ev_fn(st_e)
    ln = ev_fn(ln_e) if ln_e is not None else UNKNOWN
    if sv is None or st is None or (ln_e is not None and ln is None):
        return None
    if isinstance(sv, str) and isinstance(st, int) and not isinstance(st, bool) and st >= 1:
        if ln_e is None:
            return sv[st - 1 :]
        if isinstance(ln, int) and not isinstance(ln, bool) and ln >= 0:
            return sv[st - 1 : st - 1 + ln]
    return UNKNOWN


def _round_val(
    val_e: exp.Expr | None, dec_e: exp.Expr | None, ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    xv = ev_fn(val_e)
    if xv is None:
        return None
    dv: object = 0 if dec_e is None else ev_fn(dec_e)
    if dv is None:
        return None
    if (
        _is_finite_num(xv)
        and abs(xv) < (1 << 53)  # type: ignore[arg-type]
        and isinstance(dv, int)
        and not isinstance(dv, bool)
        and 0 <= dv <= 12
    ):
        if isinstance(xv, float) and Decimal(xv) != Decimal(str(xv)):
            return UNKNOWN
        try:
            d_val = Decimal(str(xv))
            q = Decimal("1") if dv == 0 else Decimal(f"1e{-dv}")
            res_up = d_val.quantize(q, rounding=ROUND_HALF_UP)
            res_even = d_val.quantize(q, rounding=ROUND_HALF_EVEN)
            if res_up != res_even:
                return UNKNOWN
            return float(res_up)
        except InvalidOperation:
            return UNKNOWN
    return UNKNOWN


def _sqrt_val(v: object) -> object:
    if v is None:
        return None
    if _is_finite_num(v) and v >= 0:  # type: ignore[operator]
        if isinstance(v, int) and v >= (1 << 53):
            return UNKNOWN
        try:
            r = math.sqrt(v)  # type: ignore[arg-type]
        except OverflowError:
            return UNKNOWN
        if not math.isfinite(r):
            return UNKNOWN
        if r.is_integer() and int(r) >= (1 << 53):
            return UNKNOWN
        return r
    return UNKNOWN


def _pow_val(lv: object, rv: object) -> object:
    if lv is None or rv is None:
        return None
    if (
        _is_finite_num(lv)
        and _is_finite_num(rv)
        and abs(rv) <= 100  # type: ignore[arg-type]
        and (lv >= 0 or isinstance(rv, int))  # type: ignore[operator]
    ):
        try:
            pv = lv**rv  # type: ignore[operator]
            if isinstance(pv, int):
                return UNKNOWN if abs(pv) >= (1 << 53) else float(pv)
            if isinstance(pv, float) and math.isfinite(pv):
                return pv
        except (OverflowError, ZeroDivisionError, ValueError):
            return UNKNOWN
    return UNKNOWN


def _extract_val(
    part_e: exp.Expr | None,
    src_e: exp.Expr | None,
    ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    if ev_fn(src_e) is None:
        return None
    pu = unwrap_parens(part_e)
    part = (
        pu.name.upper()
        if isinstance(pu, (exp.Var, exp.Identifier))
        else (str(pu.this).upper() if isinstance(pu, exp.Literal) and pu.is_string else "")
    )
    su = unwrap_parens(src_e)
    if not (
        part
        and isinstance(su, exp.Cast)
        and isinstance(dt := su.args.get("to"), exp.DataType)
        and isinstance(lit := unwrap_parens(su.this), exp.Literal)
        and lit.is_string
    ):
        return UNKNOWN
    raw = str(lit.this).strip()
    try:
        if dt.is_type(exp.DataType.Type.DATE):
            d = date.fromisoformat(raw)
            return {"YEAR": d.year, "MONTH": d.month, "DAY": d.day}.get(part, UNKNOWN)
        if _TZ_TEMPORAL_TYPES and dt.is_type(*_TZ_TEMPORAL_TYPES):
            return UNKNOWN
        if dt.is_type(*exp.DataType.TEMPORAL_TYPES) and not dt.is_type(exp.DataType.Type.TIME):
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is not None:
                return UNKNOWN
            if part == "SECOND" and ts.microsecond != 0:
                return UNKNOWN
            return {
                "YEAR": ts.year, "MONTH": ts.month, "DAY": ts.day,
                "HOUR": ts.hour, "MINUTE": ts.minute, "SECOND": ts.second,
            }.get(part, UNKNOWN)
    except ValueError:
        return UNKNOWN
    return UNKNOWN


_UNARY_CLS_MAP: tuple[tuple[type[exp.Expr], str], ...] = tuple(
    (cls, op)
    for cls, op in (
        (exp.Abs, "ABS"), (exp.Floor, "FLOOR"), (exp.Ceil, "CEIL"),
        (exp.Reverse, "REVERSE"), (exp.Upper, "UPPER"), (exp.Lower, "LOWER"),
        (_SQRT_CLS, "SQRT"), (_SIGN_CLS, "SIGN"), (_ASCII_CLS, "ASCII"),
    )
    if cls is not None
)


def _fold_unary(
    op: str, arg: exp.Expr | None, ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    v = ev_fn(arg)
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if (isinstance(v, float) and not math.isfinite(v)) or abs(v) >= (1 << 53):
            return UNKNOWN
        if op == "ABS":
            return abs(v)
        if op in ("FLOOR", "CEIL", "CEILING"):
            return float(math.floor(v) if op == "FLOOR" else math.ceil(v))
        if op == "SIGN":
            if isinstance(v, float):
                return 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)
            return 1 if v > 0 else (-1 if v < 0 else 0)
        if op == "SQRT":
            return _sqrt_val(v)
        if op == "CHR" and isinstance(v, int) and 0 < v < 128:
            return chr(v)
        return UNKNOWN
    if isinstance(v, str):
        if op == "UPPER":
            return v.upper() if v.isascii() else UNKNOWN
        if op == "LOWER":
            return v.lower() if v.isascii() else UNKNOWN
        if op == "REVERSE":
            return v[::-1] if v.isascii() else UNKNOWN
        if op in ("LENGTH", "CHAR_LENGTH", "CHARACTER_LENGTH"):
            return len(v) if v.isascii() else UNKNOWN
        if op == "ASCII":
            return ord(v[0]) if v and ord(v[0]) < 128 else UNKNOWN
    return UNKNOWN


def _replace_val(
    s_e: exp.Expr | None,
    o_e: exp.Expr | None,
    n_e: exp.Expr | None,
    ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    sv, ov, nv = ev_fn(s_e), ev_fn(o_e), ev_fn(n_e)
    if sv is None or ov is None or nv is None:
        return None
    if isinstance(sv, str) and isinstance(ov, str) and isinstance(nv, str):
        return sv if ov == "" else sv.replace(ov, nv)
    return UNKNOWN


def _leftright_val(
    s_e: exp.Expr | None,
    n_e: exp.Expr | None,
    *,
    is_left: bool,
    ev_fn: Callable[[exp.Expr | None], object],
) -> object:
    sv, nv = ev_fn(s_e), ev_fn(n_e)
    if sv is None or nv is None:
        return None
    if isinstance(sv, str) and isinstance(nv, int) and not isinstance(nv, bool) and nv >= 0:
        return "" if nv == 0 else (sv[:nv] if is_left else sv[-nv:])
    return UNKNOWN


def fold_scalar_func(u: exp.Expr, ev_fn: Callable[[exp.Expr | None], object]) -> object:
    for cls, op in _UNARY_CLS_MAP:
        if isinstance(u, cls):
            return _fold_unary(op, u.this, ev_fn)
    if isinstance(u, exp.Round):
        return _round_val(u.this, u.args.get("decimals"), ev_fn)
    if isinstance(u, exp.Trim):
        return _trim_val(u.this, u.expression, (u.args.get("position") or "").upper(), ev_fn)
    if isinstance(u, exp.Substring):
        return _substr_val(u.this, u.args.get("start"), u.args.get("length"), ev_fn)
    if isinstance(u, exp.Replace):
        return _replace_val(u.this, u.expression, u.args.get("replacement"), ev_fn)
    if isinstance(u, (exp.Left, exp.Right)):
        return _leftright_val(u.this, u.expression, is_left=isinstance(u, exp.Left), ev_fn=ev_fn)
    if isinstance(u, exp.Length):
        return UNKNOWN if u.args.get("binary") else _fold_unary("LENGTH", u.this, ev_fn)
    if _POW_CLS is not None and isinstance(u, _POW_CLS):
        return _pow_val(ev_fn(u.this), ev_fn(u.expression))
    if _CHR_CLS is not None and isinstance(u, _CHR_CLS) and u.expressions:
        return _fold_unary("CHR", u.expressions[0], ev_fn)
    if _EXTRACT_CLS is not None and isinstance(u, _EXTRACT_CLS):
        return _extract_val(u.this, u.expression, ev_fn)
    if isinstance(u, exp.Anonymous):
        un = u.name.upper()
        exs = u.expressions
        if len(exs) == 1 and un in (
            "UPPER", "LOWER", "REVERSE", "ABS", "FLOOR", "CEIL", "CEILING",
            "LENGTH", "CHAR_LENGTH", "CHARACTER_LENGTH", "SQRT", "SIGN", "ASCII", "CHR",
        ):
            return _fold_unary(un, exs[0], ev_fn)
        if un in ("TRIM", "BTRIM", "LTRIM", "RTRIM") and 1 <= len(exs) <= 2:
            pos = "LEADING" if un == "LTRIM" else ("TRAILING" if un == "RTRIM" else "")
            return _trim_val(exs[0], exs[1] if len(exs) == 2 else None, pos, ev_fn)
        if un in ("SUBSTR", "SUBSTRING") and 2 <= len(exs) <= 3:
            return _substr_val(exs[0], exs[1], exs[2] if len(exs) == 3 else None, ev_fn)
        if un == "REPLACE" and len(exs) == 3:
            return _replace_val(exs[0], exs[1], exs[2], ev_fn)
        if un in ("LEFT", "RIGHT") and len(exs) == 2:
            return _leftright_val(exs[0], exs[1], is_left=(un == "LEFT"), ev_fn=ev_fn)
        if un == "ROUND" and 1 <= len(exs) <= 2:
            return _round_val(exs[0], exs[1] if len(exs) == 2 else None, ev_fn)
        if un in ("POW", "POWER") and len(exs) == 2:
            return _pow_val(ev_fn(exs[0]), ev_fn(exs[1]))
        if un == "DATE_PART" and len(exs) == 2:
            return _extract_val(exs[0], exs[1], ev_fn)
    return UNKNOWN


__all__ = ["fold_cast_val", "fold_concat_ws", "fold_scalar_func"]
