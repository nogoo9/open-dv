"""Unicode and Line Ending Normalization Pipeline (Task 4B.1 / Issue #201)."""

from __future__ import annotations

import unicodedata
from array import array
from collections.abc import Sequence


def normalize_line_endings(text: str) -> str:
    """Normalize line endings to standard Unix LF (\\n).

    Converts CRLF (\\r\\n) and CR (\\r) to LF (\\n).
    """
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_unicode_nfc(text: str) -> str:
    """Normalize text into canonical Unicode NFC decomposition/composition."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text)


def normalize_text(text: str) -> str:
    """Execute complete normalization pipeline: LF line endings then Unicode NFC."""
    if not text:
        return ""
    return normalize_unicode_nfc(normalize_line_endings(text))


def build_normalization_offset_map(raw_text: str) -> tuple[str, Sequence[int]]:
    """Normalize raw_text (LF + NFC) and construct an exact monotonic offset map.

    Uses O(1) memory range for normalized text and compact 32-bit unsigned integer arrays
    (array('I')) for remapping, bounding memory overhead to 4 bytes per character.

    Returns:
        tuple[str, Sequence[int]]: (normalized_text, offset_map) where offset_map[i]
        maps character index i in raw_text (0 <= i <= len(raw_text)) to its corresponding
        character offset in normalized_text.
    """
    if not raw_text:
        return "", [0]

    # Fast path: already normalized -> O(1) memory range object
    lf_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    norm_text = unicodedata.normalize("NFC", lf_text)
    if raw_text == norm_text:
        return norm_text, range(len(raw_text) + 1)

    # Phase 1: Map raw_text -> lf_text in O(N) using compact 32-bit integer array
    raw_to_lf = array("I")
    i = 0
    lf_idx = 0
    n_raw = len(raw_text)
    while i < n_raw:
        raw_to_lf.append(lf_idx)
        if i + 1 < n_raw and raw_text[i : i + 2] == "\r\n":
            raw_to_lf.append(lf_idx + 1)
            lf_idx += 1
            i += 2
        elif raw_text[i] == "\r":
            lf_idx += 1
            i += 1
        else:
            lf_idx += 1
            i += 1
    raw_to_lf.append(lf_idx)

    lf_text_str = lf_text
    if lf_text_str == norm_text:
        return norm_text, raw_to_lf

    # Phase 2: Map lf_text -> norm_text by matching normalization clusters
    n_lf = len(lf_text_str)
    n_norm = len(norm_text)
    lf_to_norm = array("I", [0]) * (n_lf + 1)
    i = 0
    j = 0

    while i < n_lf:
        # Fast path for consecutive ASCII code points
        if ord(lf_text_str[i]) < 128 and (i + 1 == n_lf or ord(lf_text_str[i + 1]) < 128):
            lf_to_norm[i] = min(j, n_norm)
            i += 1
            j += 1
            continue

        matched = False
        for k in range(i + 1, min(i + 32, n_lf + 1)):
            c_norm = unicodedata.normalize("NFC", lf_text_str[i:k])
            c_len = len(c_norm)
            if j + c_len <= n_norm and norm_text[j : j + c_len] == c_norm:
                if k < n_lf:
                    expanded = unicodedata.normalize("NFC", lf_text_str[i : k + 1])
                    if len(expanded) <= c_len:
                        continue
                for step in range(i, k):
                    lf_to_norm[step] = min(j, n_norm)
                j += c_len
                i = k
                matched = True
                break
        if not matched:
            # Resynchronize by normalizing to the next starter
            next_starter = i + 1
            while next_starter < n_lf and unicodedata.combining(lf_text_str[next_starter]) != 0:
                next_starter += 1
            seg_norm = unicodedata.normalize("NFC", lf_text_str[i:next_starter])
            search_bound = min(j + 64, n_norm)
            found = norm_text.find(seg_norm, j, search_bound)
            if found != -1:
                seg_target = min(found + len(seg_norm), n_norm)
                for step in range(i, next_starter):
                    lf_to_norm[step] = min(j, n_norm)
                j = seg_target
                i = next_starter
            else:
                lf_to_norm[i] = min(j, n_norm)
                i += 1
                if j < n_norm:
                    j += 1

    lf_to_norm[n_lf] = n_norm
    raw_to_norm = array("I", (min(lf_to_norm[lf_idx], n_norm) for lf_idx in raw_to_lf))
    return norm_text, raw_to_norm


def compute_utf16_offsets_for_points(
    text: str,
    points: Sequence[int],
) -> dict[int, int]:
    """Compute UTF-16 code unit offsets for specific code point offsets.

    Runs in O(N + K log K) time and uses O(K) memory, avoiding allocating
    an O(N) array across the entire document.
    """
    if not points:
        return {}
    sorted_unique = sorted(set(points))
    result: dict[int, int] = {}

    pt_idx = 0
    num_pts = len(sorted_unique)
    accum_utf16 = 0

    while pt_idx < num_pts and sorted_unique[pt_idx] <= 0:
        result[sorted_unique[pt_idx]] = 0
        pt_idx += 1

    for cp_idx, char in enumerate(text):
        while pt_idx < num_pts and sorted_unique[pt_idx] == cp_idx:
            result[sorted_unique[pt_idx]] = accum_utf16
            pt_idx += 1
        accum_utf16 += 2 if ord(char) > 0xFFFF else 1

    while pt_idx < num_pts:
        result[sorted_unique[pt_idx]] = accum_utf16
        pt_idx += 1

    return result


def compute_utf16_code_units(text: str) -> int:
    """Calculate the number of UTF-16 code units required for a string.

    Characters with code points > 0xFFFF require 2 code units (surrogate pair)
    in UTF-16 representation (e.g. browser DOM text nodes).
    """
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def compute_utf16_offset_map(text: str) -> Sequence[int]:
    """Compute cumulative UTF-16 code unit offset map for code points 0..len(text).

    Provides O(1) browser UTF-16 offset lookups in a compact 32-bit unsigned integer array.
    """
    mapping = array("I", [0]) * (len(text) + 1)
    accum = 0
    for i, char in enumerate(text):
        accum += 2 if ord(char) > 0xFFFF else 1
        mapping[i + 1] = accum
    return mapping


def compute_utf16_offset(full_text: str, code_point_offset: int) -> int:
    """Convert a Unicode code point offset into a UTF-16 browser code unit index."""
    if code_point_offset <= 0 or not full_text:
        return 0
    clamped_offset = min(code_point_offset, len(full_text))
    prefix = full_text[:clamped_offset]
    return compute_utf16_code_units(prefix)


__all__ = [
    "build_normalization_offset_map",
    "compute_utf16_code_units",
    "compute_utf16_offset",
    "compute_utf16_offset_map",
    "compute_utf16_offsets_for_points",
    "normalize_line_endings",
    "normalize_text",
    "normalize_unicode_nfc",
]
