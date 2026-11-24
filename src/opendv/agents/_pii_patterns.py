"""Compiled PII regex patterns and PAN/IBAN/ABA validation helpers."""

from __future__ import annotations

import re

_HEX_CTL = r"\\x(?:[01][0-9a-fA-F]|7[fF])"
_UNI_CTL = r"\\u00(?:[01][0-9a-fA-F]|7[fF])"
_T_START = rf"(?:(?<![A-Za-z0-9])|(?<=\\[ntrbf])|(?<={_HEX_CTL})|(?<={_UNI_CTL}))"
_D_START = (
    rf"(?:(?<!\d)(?<!\\x)(?<!\\x[0-9a-fA-F])"
    rf"(?<!\\u)(?<!\\u[0-9a-fA-F])(?<!\\u[0-9a-fA-F]{{2}})(?<!\\u[0-9a-fA-F]{{3}})"
    rf"|(?<={_HEX_CTL})|(?<={_UNI_CTL}))"
)
_T_END = r"(?![A-Za-z0-9])"

_RE_SSN = re.compile(rf"{_T_START}\d{{3}}-\d{{2}}-\d{{4}}{_T_END}")
_RE_EIN = re.compile(rf"{_T_START}\d{{2}}-\d{{7}}{_T_END}")
_RE_EMAIL = re.compile(
    rf"(?:(?<![A-Za-z0-9._%+-])"
    rf"(?<!\\(?=[ntrbf]|x(?:[01][0-9a-fA-F]|7[fF])|u00(?:[01][0-9a-fA-F]|7[fF])))"
    rf"|(?<=\\[ntrbf])|(?<={_HEX_CTL})|(?<={_UNI_CTL}))"
    rf"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{{2,}}{_T_END}"
)
_RE_ABA_PHRASE = re.compile(
    rf"(?i){_T_START}(?:aba|routing|rtn){_T_END}"
    rf"(?:\s+(?:routing|number|no|#))?\s*[:#]?\s*\d{{9}}{_T_END}"
)
_RE_ABA_DIGITS = re.compile(rf"{_T_START}\d{{9}}{_T_END}")
_RE_ABA_CTX = re.compile(
    rf"(?:(?i:{_T_START}(?:aba|routing|rtn)){_T_END}"
    rf"|{_T_START}(?:(?:aba|routing|rtn|Aba|Routing|Rtn)(?=[A-Z])|(?:ABA|ROUTING|RTN)(?=[A-Z][a-z]))"
    rf"|(?<=[a-z0-9])(?:(?:Aba|Routing|Rtn)(?:{_T_END}|(?=[A-Z]))|(?:ABA|ROUTING|RTN)(?:{_T_END}|(?=[A-Z][a-z]))))"
)
_RE_PHONE = re.compile(
    rf"{_D_START}(?:\+1[-.\s]?|1[-.\s])?(?:\(\d{{3}}\)[-.\s]?|\d{{3}}[-.\s])\d{{3}}[-.\s]\d{{4}}(?!\d)"
)
_RE_IBAN = re.compile(rf"{_T_START}[A-Z]{{2}}\d{{2}}(?: ?[A-Z0-9]){{11,30}}(?![A-Za-z0-9]|-\d)")
_RE_CC_PAN = re.compile(rf"{_D_START}\d+(?:[ -]\d+)*(?!\d)")
_RE_END_CC_PAN = re.compile(rf"{_D_START}\d+(?:[ -]\d+)*$")
_RE_END_SSN = re.compile(rf"{_RE_SSN.pattern}$")
_RE_END_EIN = re.compile(rf"{_RE_EIN.pattern}$")
_RE_END_PHONE = re.compile(rf"{_RE_PHONE.pattern}$")
_RE_END_IBAN = re.compile(rf"{_T_START}[A-Z]{{2}}\d{{2}}(?: ?[A-Z0-9]){{11,30}}$")


def _is_valid_aba(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit() or digits.startswith(("1900", "2026", "0000")):
        return False
    d = [int(ch) for ch in digits]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def _is_valid_iban(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not (15 <= len(compact) <= 34) or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    return int(digits) % 97 == 1


def _is_valid_luhn(pan: str) -> bool:
    digits = [int(ch) for ch in pan if ch.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    for idx, val in enumerate(reversed(digits)):
        total += (val * 2 - 9 if val * 2 > 9 else val * 2) if idx % 2 == 1 else val
    return total % 10 == 0


def _is_card_network_pan(compact: str) -> bool:
    n = len(compact)
    if compact[0] == "4":
        return n in (13, 16, 19)
    p2 = int(compact[:2])
    if p2 in (34, 37):
        return n == 15
    p3 = int(compact[:3])
    p4 = int(compact[:4])
    if (51 <= p2 <= 55 or 2221 <= p4 <= 2720) and n == 16:
        return True
    if (300 <= p3 <= 305 or p4 == 3095 or p2 in (36, 38, 39)) and 14 <= n <= 19:
        return True
    if (
        compact.startswith("6011")
        or p2 == 65
        or 644 <= p3 <= 649
        or 622126 <= int(compact[:6]) <= 622925
    ) and 16 <= n <= 19:
        return True
    if (3528 <= p4 <= 3589 or p2 == 62 or 2200 <= p4 <= 2204) and 16 <= n <= 19:
        return True
    return (5000 <= p4 <= 5099 or 56 <= p2 <= 58 or p2 in (63, 67)) and 13 <= n <= 19


def _is_standard_pan_shape(lens: tuple[int, ...], compact: str) -> bool:
    if len(lens) == 1 or lens == (4, 4, 4, 4):
        return True
    if lens == (4, 6, 5) and compact[:2] in ("34", "37"):
        return True
    if lens == (4, 6, 4) and (
        compact[:2] in ("36", "38", "39")
        or 300 <= int(compact[:3]) <= 305
        or compact.startswith("3095")
    ):
        return True
    if len(lens) == 4 and lens[:3] == (4, 4, 4) and 1 <= lens[3] <= 3:
        return True
    return len(lens) == 5 and lens[:4] == (4, 4, 4, 4) and 1 <= lens[4] <= 3


def _cc_pan_spans(raw: str) -> list[tuple[int, int]]:
    if len(raw) < 13:
        return []
    parts = re.split(r"[ -]", raw)
    m = len(parts)
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for part in parts:
        starts.append(pos)
        ends.append(pos + len(part))
        pos += len(part) + 1

    cands: list[list[tuple[int, int]]] = [[] for _ in range(m)]
    max_plausible_j = [-1] * m
    for i in range(m):
        digits = 0
        for j in range(i, m):
            digits += len(parts[j])
            if digits > 19:
                break
            if digits >= 13:
                sub = parts[i : j + 1]
                compact = "".join(sub)
                if _is_valid_luhn(compact):
                    lens = tuple(len(x) for x in sub)
                    is_net = _is_card_network_pan(compact)
                    is_std = _is_standard_pan_shape(lens, compact)
                    if (is_net or (is_std and compact[0] != "0")) and j > max_plausible_j[i]:
                        max_plausible_j[i] = j
                    weight = (
                        (100 if is_net else 0)
                        + (30 if is_std else 0)
                        + (10 if len(sub) >= 3 else 0)
                        + digits
                    )
                    cands[i].append((j, weight))

    if not any(cands):
        return []

    bwd = [0] * (m + 1)
    for i in range(m - 1, -1, -1):
        best = bwd[i + 1]
        for j, weight in cands[i]:
            if weight + bwd[j + 1] > best:
                best = weight + bwd[j + 1]
        bwd[i] = best

    fwd = [0] * (m + 1)
    for i in range(m):
        if fwd[i] > fwd[i + 1]:
            fwd[i + 1] = fwd[i]
        for j, weight in cands[i]:
            if fwd[i] + weight > fwd[j + 1]:
                fwd[j + 1] = fwd[i] + weight

    best_total = bwd[0]
    merged: list[tuple[int, int]] = []
    for i in range(m):
        for j, weight in cands[i]:
            if fwd[i] + weight + bwd[j + 1] == best_total:
                if merged and i <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], j))
                else:
                    merged.append((i, j))

    out_spans: list[tuple[int, int]] = []
    checked_until = 0
    for s_i, s_j in merged:
        if out_spans and s_i <= out_spans[-1][1]:
            s_i = out_spans[-1][0]
            s_j = max(out_spans[-1][1], s_j)
            out_spans.pop()
        cur = max(s_i + 1, checked_until)
        while cur <= s_j:
            pj = max_plausible_j[cur]
            if pj > s_j:
                s_j = pj
            cur += 1
        checked_until = max(checked_until, cur)
        out_spans.append((s_i, s_j))
    return [(starts[i], ends[j]) for i, j in out_spans]


def _find_pan_spans(text: str) -> list[tuple[int, int]]:
    return [
        (m.start() + s, m.start() + e)
        for m in _RE_CC_PAN.finditer(text)
        for s, e in _cc_pan_spans(m.group(0))
    ]
