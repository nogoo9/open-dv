"""Normative Data Vault 2.0 constants for Open-DV (v6.0).

Defines Zero Keys, Ghost Record sentinels, Algorithm A.5 tokens, and BKCC defaults
in accordance with docs/01-core-modelling-spec.md (§3.3, §4, §6.1).
"""

from __future__ import annotations

from typing import Final

# Sentinel Hub Hash Keys (Zero Keys) for referential integrity without NULL FKs
ZERO_KEY_UNKNOWN: Final[str] = "0" * 64
ZERO_KEY_ERROR: Final[str] = "f" * 64
ZERO_KEY_ERROR_UPPER: Final[str] = "F" * 64

# Canonical business key tokens for Zero Key records
ZERO_KEY_UNKNOWN_TOKEN: Final[str] = "-1"
ZERO_KEY_ERROR_TOKEN: Final[str] = "-2"
ZERO_KEY_REC_SRC: Final[str] = "SYSTEM_ZERO_KEY"

# Ghost Record sentinels for Point-in-Time (PIT) inner-join completeness
GHOST_DTS: Final[str] = "1900-01-01 00:00:00"
GHOST_REC_SRC: Final[str] = "SYSTEM_GHOST"

# Algorithm A.5 length-prefixed SHA-256 hashing constants
NULL_TOKEN: Final[str] = "-1:^^"
DEFAULT_DELIMITER: Final[str] = "||"
SHA256_HEX_LENGTH: Final[int] = 64

# Default Business Key Collision Code (BKCC) namespaces
GLOBAL_BKCC: Final[str] = "GLOBAL"
SYSTEM_BKCC: Final[str] = "SYSTEM"

__all__ = [
    "DEFAULT_DELIMITER",
    "GHOST_DTS",
    "GHOST_REC_SRC",
    "GLOBAL_BKCC",
    "NULL_TOKEN",
    "SHA256_HEX_LENGTH",
    "SYSTEM_BKCC",
    "ZERO_KEY_ERROR",
    "ZERO_KEY_ERROR_TOKEN",
    "ZERO_KEY_ERROR_UPPER",
    "ZERO_KEY_REC_SRC",
    "ZERO_KEY_UNKNOWN",
    "ZERO_KEY_UNKNOWN_TOKEN",
]
