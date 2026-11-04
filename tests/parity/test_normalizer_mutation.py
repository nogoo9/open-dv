"""tests/parity/test_normalizer_mutation.py — Mutation tests proving divergence detection."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tests.parity.normalize import normalize_for_parity


@pytest.fixture
def baseline_verify_payload() -> dict[str, Any]:
    return {
        "status": "PASS",
        "mode": "local",
        "run_id": "run-20261104-001",
        "created_at": "2026-11-04T12:00:00Z",
        "duration_ms": 142.5,
        "gates": {
            "G1": {"status": "PASSED", "rules": ["DV-COL-001", "DV-COL-013"]},
            "G2": {"status": "PASSED", "rules": ["DV-G2-001"]},
        },
        "canonical_ir_sha256": "452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8",
        "table_count": 3,
        "sql_sample": "SELECT hk_customer FROM raw_vault.hub_customer",
        "trace_id": "trace-abc-123",
        "workspace_path": "C:\\Users\\testuser\\workspace\\contract.yaml",
    }


def test_normalizer_identity(baseline_verify_payload: dict[str, Any]) -> None:
    """Assert identical payloads produce identical normalized outputs."""
    p1 = copy.deepcopy(baseline_verify_payload)
    p2 = copy.deepcopy(baseline_verify_payload)
    assert normalize_for_parity(p1) == normalize_for_parity(p2)


def test_normalizer_allows_volatile_variations(
    baseline_verify_payload: dict[str, Any],
) -> None:
    """Assert variations strictly within the allowlist normalize to identical outputs."""
    p1 = copy.deepcopy(baseline_verify_payload)
    p2 = copy.deepcopy(baseline_verify_payload)

    p2["mode"] = "remote"
    p2["run_id"] = "run-different-999"
    p2["created_at"] = "2026-11-05T08:30:00Z"
    p2["duration_ms"] = 899.2
    p2["trace_id"] = "trace-xyz-789"
    p2["workspace_path"] = "/tmp/other_user/workspace/contract.yaml"

    assert normalize_for_parity(p1) == normalize_for_parity(p2)


@pytest.mark.parametrize(
    ("mutation_key", "mutation_val"),
    [
        ("status", "FAIL"),
        (
            "canonical_ir_sha256",
            "6b7c7180b62164f31f14eebcd16cc5fd2b0ce58143de66a4b67521113ec5d8b9",
        ),
        ("table_count", 4),
        ("sql_sample", "SELECT hk_customer FROM raw_vault.hub_customer_mutated"),
    ],
)
def test_normalizer_detects_mutations(
    baseline_verify_payload: dict[str, Any],
    mutation_key: str,
    mutation_val: Any,
) -> None:
    """Assert non-allowlisted mutations strictly cause divergence and assertion failure."""
    p1 = copy.deepcopy(baseline_verify_payload)
    p2 = copy.deepcopy(baseline_verify_payload)
    p2[mutation_key] = mutation_val

    assert normalize_for_parity(p1) != normalize_for_parity(p2)


def test_normalizer_detects_rule_id_mutation(
    baseline_verify_payload: dict[str, Any],
) -> None:
    """Assert mutation of rule IDs causes divergence."""
    p1 = copy.deepcopy(baseline_verify_payload)
    p2 = copy.deepcopy(baseline_verify_payload)
    p2["gates"]["G1"]["rules"][1] = "DV-COL-014"

    assert normalize_for_parity(p1) != normalize_for_parity(p2)


def test_normalizer_float_rounding(baseline_verify_payload: dict[str, Any]) -> None:
    """Assert float rounding up to 6 decimal places eliminates epsilon jitter."""
    p1 = copy.deepcopy(baseline_verify_payload)
    p2 = copy.deepcopy(baseline_verify_payload)
    p1["entropy"] = 3.1415926535
    p2["entropy"] = 3.1415929999
    # Rounded to 6 decimal places: both become 3.141593
    assert normalize_for_parity(p1) == normalize_for_parity(p2)

    # Significant float change (> 1e-6) must diverge
    p3 = copy.deepcopy(baseline_verify_payload)
    p3["entropy"] = 3.141650
    assert normalize_for_parity(p1) != normalize_for_parity(p3)
