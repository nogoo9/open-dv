"""Unit tests for CoreModelingConfig declarative defaults and environment overrides (WS1.3).

Validates:
- Normative Data Vault 2.0 and Algorithm A.9a thresholds
- Clean environment variable loading with OPENDV_ prefix
- Validation bounds (thresholds in [0.0, 1.0], max_sandbox_rows >= 1)
- Immutability of the frozen configuration
- Singleton accessor get_core_modeling_config()
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_core.config import CoreModelingConfig, get_core_modeling_config
from opendv_core.constants import (
    DEFAULT_DELIMITER,
    GHOST_DTS,
    GHOST_REC_SRC,
    GLOBAL_BKCC,
    NULL_TOKEN,
    SHA256_HEX_LENGTH,
    SYSTEM_BKCC,
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
)


def test_core_modeling_config_defaults() -> None:
    """Validate all normative defaults match Open-DV modeling specs."""
    cfg = CoreModelingConfig()

    # Drift thresholds
    assert cfg.levenshtein_rename_threshold == 0.85
    assert cfg.jaccard_similarity_threshold == 0.90
    assert cfg.companion_satellite_volatility_tier == "ADDITIVE_DRIFT_COMPANION"

    # Modeling thresholds
    assert cfg.business_key_uniqueness_threshold == 1.0
    assert cfg.sparse_fk_null_threshold == 0.95
    assert cfg.min_string_length_for_bk == 1
    assert cfg.max_sandbox_rows == 10_000

    # System table exclusions
    assert "sqlite_stat" in cfg.system_table_prefixes
    assert "sqlite_sequence" in cfg.system_table_prefixes
    assert "information_schema" in cfg.system_table_prefixes

    # Algorithm A.5 hashing & sentinels
    assert cfg.default_delimiter == DEFAULT_DELIMITER
    assert cfg.null_token == NULL_TOKEN
    assert cfg.sha256_hex_length == SHA256_HEX_LENGTH
    assert cfg.zero_key_unknown == ZERO_KEY_UNKNOWN
    assert cfg.zero_key_error == ZERO_KEY_ERROR
    assert cfg.ghost_dts == GHOST_DTS
    assert cfg.ghost_rec_src == GHOST_REC_SRC
    assert cfg.global_bkcc == GLOBAL_BKCC
    assert cfg.system_bkcc == SYSTEM_BKCC


def test_core_modeling_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate that environment variables cleanly override default thresholds."""
    monkeypatch.setenv("OPENDV_LEVENSHTEIN_RENAME_THRESHOLD", "0.78")
    monkeypatch.setenv("OPENDV_JACCARD_SIMILARITY_THRESHOLD", "0.88")
    monkeypatch.setenv("OPENDV_MAX_SANDBOX_ROWS", "25000")
    monkeypatch.setenv("OPENDV_COMPANION_SATELLITE_VOLATILITY_TIER", "CUSTOM_COMPANION")
    monkeypatch.setenv("OPENDV_SYSTEM_TABLE_PREFIXES", "sqlite_stat,custom_sys_")

    cfg = CoreModelingConfig()
    assert cfg.levenshtein_rename_threshold == 0.78
    assert cfg.jaccard_similarity_threshold == 0.88
    assert cfg.max_sandbox_rows == 25000
    assert cfg.companion_satellite_volatility_tier == "CUSTOM_COMPANION"
    assert cfg.system_table_prefixes == ("sqlite_stat", "custom_sys_")


def test_core_modeling_config_from_env_mapping() -> None:
    """Validate from_env method with explicit dictionary."""
    custom_env = {
        "OPENDV_LEVENSHTEIN_RENAME_THRESHOLD": "0.92",
        "OPENDV_MAX_SANDBOX_ROWS": "500",
    }
    cfg = CoreModelingConfig.from_env(custom_env)
    assert cfg.levenshtein_rename_threshold == 0.92
    assert cfg.max_sandbox_rows == 500
    # Other values remain defaults
    assert cfg.jaccard_similarity_threshold == 0.90


def test_core_modeling_config_immutability() -> None:
    """Validate that CoreModelingConfig is frozen and cannot be mutated."""
    cfg = CoreModelingConfig()
    with pytest.raises(ValidationError):
        cfg.levenshtein_rename_threshold = 0.50  # type: ignore[misc]


def test_core_modeling_config_validation_bounds() -> None:
    """Validate that threshold values outside [0.0, 1.0] or invalid integers are rejected."""
    # Negative threshold
    with pytest.raises(ValidationError):
        CoreModelingConfig(levenshtein_rename_threshold=-0.1)

    # Threshold > 1.0
    with pytest.raises(ValidationError):
        CoreModelingConfig(jaccard_similarity_threshold=1.5)

    # max_sandbox_rows < 1
    with pytest.raises(ValidationError):
        CoreModelingConfig(max_sandbox_rows=0)


def test_get_core_modeling_config_singleton() -> None:
    """Validate singleton getter returns identical instance."""
    cfg1 = get_core_modeling_config()
    cfg2 = get_core_modeling_config()
    assert cfg1 is cfg2
