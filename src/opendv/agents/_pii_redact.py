"""Telemetry PII redaction helpers for CLI/SDK agent runners."""

from __future__ import annotations

import re
from collections import deque

from opendv.agents._pii_patterns import (
    _CREDENTIAL_SUB_PATTERNS,
    _RE_ABA_CTX,
    _RE_ABA_DIGITS,
    _RE_ABA_PHRASE,
    _RE_CC_PAN,
    _RE_EIN,
    _RE_EMAIL,
    _RE_END_CC_PAN,
    _RE_END_EIN,
    _RE_END_IBAN,
    _RE_END_PHONE,
    _RE_END_SSN,
    _RE_IBAN,
    _RE_PHONE,
    _RE_SSN,
    _cc_pan_spans,
    _find_pan_spans,
    _is_valid_aba,
    _is_valid_iban,
)

# Fast-path guard so PII-only text skips 10 separate credential regex scans.
_RE_CRED_HINT = re.compile(r"(?i)(?:sk-|dapi|AIza|ya29\.|bearer|basic|api[-_]key|key=)")
_RE_ABA_HINT = re.compile(r"(?i)aba|routing|rtn")


def _trailing_pii_end(combined: str, min_len: int) -> int:
    if m_cc := _RE_CC_PAN.match(combined):
        for start, end in _cc_pan_spans(m_cc.group(0)):
            if start == 0 and end >= min_len:
                return end
    for rx in (_RE_PHONE, _RE_IBAN):
        m = rx.match(combined)
        if m and m.end() >= min_len:
            return m.end()
    return 0


def _bound_forward(text: str, start: int, span: int = 80, max_span: int = 120) -> str:
    end = min(len(text), start + span)
    if end < len(text) and end > start and text[end - 1].isalnum():
        cap = min(len(text), start + max_span)
        while end < cap and text[end].isalnum():
            end += 1
    return text[start:end]


def _scrub_iban_match(match: re.Match[str], full_text: str) -> str:
    raw = match.group(0)
    if " " not in raw:
        return "[REDACTED]"
    parts = raw.split(" ")
    tail = _bound_forward(full_text, match.end())
    raw_valid = _is_valid_iban(raw)
    valid_split = fallback_split = ""
    for idx in range(len(parts) - 1, 0, -1):
        prefix = " ".join(parts[:idx])
        if len(prefix.replace(" ", "")) < 15:
            break
        suffix = " ".join(parts[idx:])
        is_valid_pfx = _is_valid_iban(prefix)
        end = _trailing_pii_end(suffix + tail, len(suffix))
        if is_valid_pfx and end:
            return "[REDACTED] " + suffix
        if not fallback_split and end and (not raw_valid or end > len(suffix)):
            fallback_split = "[REDACTED] " + suffix
        if not valid_split and is_valid_pfx:
            valid_split = "[REDACTED] " + suffix
    return fallback_split or ("[REDACTED]" if raw_valid else (valid_split or "[REDACTED]"))


def _scrub_cc_pan(match: re.Match[str]) -> str:
    raw = match.group(0)
    spans = _cc_pan_spans(raw)
    if not spans:
        return raw
    chunks: list[str] = []
    cur = 0
    for s, e in spans:
        chunks.append(raw[cur:s] + "[REDACTED]")
        cur = e
    return "".join(chunks) + raw[cur:]


def _starts_with_independent_pii(after: str) -> bool:
    if not after or after[0] not in " -":
        return False
    tail = _bound_forward(after, 1, span=56)
    if not tail:
        return False
    if any(rx.match(tail) is not None for rx in (_RE_SSN, _RE_EIN, _RE_PHONE, _RE_IBAN)):
        return True
    if m_cc := _RE_CC_PAN.match(tail):
        raw = m_cc.group(0)
        for s, _e in _cc_pan_spans(raw):
            if s == 0 and raw[0] != "0":
                return True
    return False


def _ends_with_independent_pan(before: str) -> bool:
    if not before or before[-1] not in " -":
        return False
    head = before[:-1]
    if not head or not head[-1].isdigit():
        return False
    lo = len(head) - 1
    while lo > 0 and (len(head) - lo) < 80 and (head[lo - 1].isdigit() or head[lo - 1] in " -"):
        lo -= 1
    if lo > 0 and (head[lo - 1].isdigit() or head[lo - 1] in " -"):
        while lo < len(head) and head[lo] not in " -":
            lo += 1
        while lo < len(head) and head[lo] in " -":
            lo += 1
        if lo >= len(head):
            return False
        head_win = head[lo:]
    else:
        head_win = head[max(0, lo - 6) :]
    if m_cc := _RE_END_CC_PAN.search(head_win):
        raw = m_cc.group(0)
        raw_len = len(raw)
        for s, e in _cc_pan_spans(raw):
            if e == raw_len and raw[s] != "0":
                return True
    return False


def _ends_with_independent_pii(before: str) -> bool:
    if not before or before[-1] not in " -":
        return False
    head_tail = before[-81:-1]
    if head_tail and any(
        rx.search(head_tail) is not None
        for rx in (_RE_END_SSN, _RE_END_EIN, _RE_END_PHONE, _RE_END_IBAN)
    ):
        return True
    return _ends_with_independent_pan(before)


def _should_skip_for_pan(
    text: str,
    m: re.Match[str],
    overlapping: list[tuple[int, int]],
    last_repl_end: int,
) -> bool:
    before_start = max(last_repl_end, m.start() - 128)
    before = ("]" if last_repl_end > 0 and before_start == last_repl_end else "") + text[
        before_start : m.start()
    ]
    after = text[m.end() : min(len(text), m.end() + 128)]
    for ps, pe in overlapping:
        if ps <= m.start() and m.end() <= pe:
            if not (
                (ps == m.start() and _starts_with_independent_pii(after))
                or (pe == m.end() and _ends_with_independent_pii(before))
                or (_ends_with_independent_pii(before) and _starts_with_independent_pii(after))
            ):
                return True
        elif m.start() <= ps < m.end() < pe:
            if not _starts_with_independent_pii(after):
                return True
        elif ps < m.start() < pe <= m.end() and not _ends_with_independent_pii(before):
            return True
    return False


def _sub_skipping_pan_overlaps(rx: re.Pattern[str], text: str) -> str:
    if not rx.search(text):
        return text
    pan_deque = deque(_find_pan_spans(text))
    if not pan_deque:
        return rx.sub("[REDACTED]", text)
    chunks: list[str] = []
    emit_pos = 0
    last_repl_end = 0
    pos = 0
    while (m := rx.search(text, pos)) is not None:
        while pan_deque and pan_deque[0][1] <= m.start():
            pan_deque.popleft()
        overlapping: list[tuple[int, int]] = []
        for ps, pe in pan_deque:
            if ps >= m.end():
                break
            if pe > m.start():
                overlapping.append((ps, pe))
        if overlapping and _should_skip_for_pan(text, m, overlapping, last_repl_end):
            pos = m.start() + 1
            continue
        chunks.append(text[emit_pos : m.start()])
        chunks.append("[REDACTED]")
        emit_pos = m.end()
        last_repl_end = m.end()
        pos = m.end()
        if overlapping:
            while pan_deque and pan_deque[0][1] <= m.end():
                pan_deque.popleft()
            win_limit = m.end() + 44
            cut_end = m.end()
            while pan_deque and pan_deque[0][0] < win_limit:
                _, pe = pan_deque.popleft()
                if pe > cut_end:
                    cut_end = pe
            if (
                cut_end < min(len(text), win_limit)
                and (cut_end > m.end() or text[m.end()] in " -0123456789")
                and text[cut_end] in " -0123456789"
            ):
                cut_end = min(len(text), win_limit)
                if cut_end < len(text) and text[cut_end].isdigit():
                    cap = min(len(text), m.end() + 120)
                    while cut_end < cap and text[cut_end].isdigit():
                        cut_end += 1
            if cut_end > m.end():
                local_spans = [
                    (m.end() + s, m.end() + e)
                    for s, e in _find_pan_spans(text[m.end() : cut_end])
                ]
                pan_deque.extendleft(reversed(local_spans))
    chunks.append(text[emit_pos:])
    return "".join(chunks)


def redact_telemetry_text(text: str) -> str:
    """Scrub <MASKED_PII>, credentials, and raw PII (SSN, email, ABA, EIN, phone, IBAN, PAN)."""
    if not text:
        return text
    out = text.replace("<MASKED_PII>", "[REDACTED]")
    if _RE_CRED_HINT.search(out):
        for rx, repl in _CREDENTIAL_SUB_PATTERNS:
            out = rx.sub(repl, out)
    if _RE_ABA_HINT.search(out):
        out = _RE_ABA_PHRASE.sub("[REDACTED]", out)
        aba_chunks: list[str] = []
        aba_emit = 0
        for match in _RE_ABA_DIGITS.finditer(out):
            if _is_valid_aba(match.group(0)):
                ctx = out[max(0, match.start() - 128) : min(len(out), match.end() + 64)]
                if _RE_ABA_CTX.search(ctx):
                    aba_chunks.append(out[aba_emit : match.start()])
                    aba_chunks.append("[REDACTED]")
                    aba_emit = match.end()
        if aba_chunks:
            aba_chunks.append(out[aba_emit:])
            out = "".join(aba_chunks)
    out = _sub_skipping_pan_overlaps(_RE_SSN, out)
    out = _sub_skipping_pan_overlaps(_RE_EIN, out)
    if "@" in out:
        out = _RE_EMAIL.sub("[REDACTED]", out)
    iban_chunks: list[str] = []
    iban_emit = 0
    pos = 0
    while (m_ib := _RE_IBAN.search(out, pos)) is not None:
        repl = _scrub_iban_match(m_ib, out)
        iban_chunks.append(out[iban_emit : m_ib.start()])
        iban_chunks.append("[REDACTED]")
        if repl.startswith("[REDACTED] "):
            keep_sep = m_ib.end() - (len(repl) - len("[REDACTED] ")) - 1
            iban_emit = keep_sep
            pos = keep_sep
        else:
            iban_emit = m_ib.end()
            pos = m_ib.end()
    if iban_chunks:
        iban_chunks.append(out[iban_emit:])
        out = "".join(iban_chunks)
    out = _sub_skipping_pan_overlaps(_RE_PHONE, out)
    return _RE_CC_PAN.sub(_scrub_cc_pan, out)


__all__ = ["redact_telemetry_text"]
