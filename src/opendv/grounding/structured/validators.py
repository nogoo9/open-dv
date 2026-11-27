"""Domain-neutral structural format and check-digit validators (Layer-2a)."""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable, Mapping, Sequence

from opendv.grounding.kernel import load_iso_codes
from opendv.grounding.kernel.pii_core import is_valid_aba_routing as _pii_aba_routing

_LEI_RE = re.compile(r"^[A-Z0-9]{18}\d{2}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_CUSIP_RE = re.compile(r"^[A-Z0-9*@#]{8}\d$")
_SEDOL_RE = re.compile(r"^[BCDFGHJKLMNPQRSTVWXYZ0-9]{6}\d$")
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$")
_FIGI_RE = re.compile(r"^BBG[B-DF-HJ-NP-TV-Z0-9]{8}\d$")
_UUID_V4_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
_SEDOL_WEIGHTS: tuple[int, ...] = (1, 3, 1, 7, 3, 9)
_CUSIP_SYMBOL_MAP: dict[str, int] = {"*": 36, "@": 37, "#": 38}


def _expand_alnum_digits(text: str) -> str:
    parts: list[str] = []
    for ch in text:
        if ch.isdigit():
            parts.append(ch)
        else:
            parts.append(str(ord(ch) - 55))
    return "".join(parts)


def _mod97_alnum(text: str) -> int:
    return int(_expand_alnum_digits(text)) % 97


def _mod10_alnum_check(body: str, check_char: str) -> bool:
    total = 0
    for idx, ch in enumerate(body):
        if ch.isdigit():
            val = int(ch)
        elif ch in _CUSIP_SYMBOL_MAP:
            val = _CUSIP_SYMBOL_MAP[ch]
        else:
            val = ord(ch) - 55
        if idx % 2 == 1:
            val *= 2
        total += (val // 10) + (val % 10)
    expected = (10 - (total % 10)) % 10
    return expected == int(check_char)


def is_valid_lei(value: str) -> bool:
    """Validate ISO 17442 20-char Legal Entity Identifier via Mod 97-10."""
    s = value.strip()
    return bool(_LEI_RE.fullmatch(s)) and _mod97_alnum(s) == 1


def is_valid_isin(value: str) -> bool:
    """Validate ISO 6166 12-char ISIN via character expansion and Luhn Mod-10."""
    s = value.strip()
    if not _ISIN_RE.fullmatch(s):
        return False
    digits = [int(ch) for ch in _expand_alnum_digits(s)]
    total = 0
    for idx, val in enumerate(reversed(digits)):
        if idx % 2 == 1:
            doubled = val * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += val
    return total % 10 == 0


def is_valid_cusip(value: str) -> bool:
    """Validate 9-char CUSIP identifier via Mod-10 double-add-double check digit."""
    s = value.strip()
    return bool(_CUSIP_RE.fullmatch(s)) and _mod10_alnum_check(s[:-1], s[-1])


def is_valid_sedol(value: str) -> bool:
    """Validate 7-char SEDOL identifier via weighted Mod-10 check digit."""
    s = value.strip()
    if not _SEDOL_RE.fullmatch(s):
        return False
    total = 0
    for weight, ch in zip(_SEDOL_WEIGHTS, s[:-1], strict=True):
        val = int(ch) if ch.isdigit() else (ord(ch) - 55)
        total += weight * val
    expected = (10 - (total % 10)) % 10
    return expected == int(s[-1])


def is_valid_iban(value: str) -> bool:
    """Validate International Bank Account Number via ISO 7064 Mod 97-10."""
    s = re.sub(r"\s+", "", value.strip())
    if not _IBAN_RE.fullmatch(s):
        return False
    return _mod97_alnum(s[4:] + s[:4]) == 1


def is_valid_aba_routing(value: str) -> bool:
    """Validate 9-digit US ABA routing number via [3,7,1] Mod-10 checksum."""
    return _pii_aba_routing(value.strip())


def is_valid_swift_bic(value: str) -> bool:
    """Validate 8- or 11-char ISO 9362 SWIFT/BIC with ISO 3166-1 country check."""
    s = value.strip()
    if not _BIC_RE.fullmatch(s):
        return False
    return s[4:6] in load_iso_codes().country_alpha2


def is_valid_figi(value: str) -> bool:
    """Validate 12-char OpenFIGI identifier starting with BBG via Mod-10 check digit."""
    s = value.strip()
    return bool(_FIGI_RE.fullmatch(s)) and _mod10_alnum_check(s[:-1], s[-1])


def is_valid_iso_4217_ccy(value: str) -> bool:
    """Validate 3-letter uppercase ISO 4217 currency code."""
    s = value.strip()
    return len(s) == 3 and s in load_iso_codes().currency_codes


def is_valid_iso_3166_country(value: str) -> bool:
    """Validate ISO 3166-1 alpha-2 or alpha-3 country code."""
    s = value.strip()
    iso = load_iso_codes()
    return s in iso.country_alpha2 or s in iso.country_alpha3


def is_valid_uuid_v4(value: str) -> bool:
    """Validate canonical RFC 4122 UUID version 4 string."""
    return bool(_UUID_V4_RE.fullmatch(value.strip()))


def is_valid_iso_date(value: str) -> bool:
    """Validate strict YYYY-MM-DD calendar date."""
    s = value.strip()
    if not _ISO_DATE_RE.fullmatch(s):
        return False
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def is_valid_iso_timestamp(value: str) -> bool:
    """Validate ISO-8601 date-time timestamp (rejecting bare calendar dates)."""
    s = value.strip()
    if not _ISO_TIMESTAMP_RE.fullmatch(s):
        return False
    try:
        datetime.datetime.fromisoformat(s)
        return True
    except ValueError:
        return False


PATTERN_VALIDATORS: Mapping[str, Callable[[str], bool]] = {
    "iso_17442_lei": is_valid_lei,
    "iso_6166_isin": is_valid_isin,
    "cusip": is_valid_cusip,
    "sedol": is_valid_sedol,
    "iban": is_valid_iban,
    "aba_routing": is_valid_aba_routing,
    "swift_bic": is_valid_swift_bic,
    "figi": is_valid_figi,
    "iso_4217_ccy": is_valid_iso_4217_ccy,
    "iso_3166_country": is_valid_iso_3166_country,
    "uuid_v4": is_valid_uuid_v4,
    "iso_date": is_valid_iso_date,
    "iso_timestamp": is_valid_iso_timestamp,
}

STRUCTURAL_PATTERN_KEYS: tuple[str, ...] = tuple(PATTERN_VALIDATORS.keys())


def compute_structural_patterns(values: Sequence[str]) -> dict[str, float]:
    """Compute per-validator match ratios in [0.0, 1.0] over cleaned non-null values."""
    total = len(values)
    if total == 0:
        return {}
    patterns: dict[str, float] = {}
    for key, validator in PATTERN_VALIDATORS.items():
        matches = sum(1 for val in values if validator(val))
        if matches > 0:
            patterns[key] = min(1.0, max(0.0, float(matches) / float(total)))
    return patterns


__all__ = [
    "PATTERN_VALIDATORS",
    "STRUCTURAL_PATTERN_KEYS",
    "compute_structural_patterns",
    "is_valid_aba_routing",
    "is_valid_cusip",
    "is_valid_figi",
    "is_valid_iban",
    "is_valid_isin",
    "is_valid_iso_3166_country",
    "is_valid_iso_4217_ccy",
    "is_valid_iso_date",
    "is_valid_iso_timestamp",
    "is_valid_lei",
    "is_valid_sedol",
    "is_valid_swift_bic",
    "is_valid_uuid_v4",
]
