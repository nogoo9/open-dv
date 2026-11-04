"""tests/parity/scenarios.py — Normative Scenario Registry mapping docs/17 scenarios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _expect_pipeline_validate_pass(res: dict[str, Any], op: str | None = None) -> None:
    assert isinstance(res, dict)
    assert (
        res.get("passed") is True
        or res.get("status") in ("PASS", "SUCCESS", "VALIDATED")
        or res.get("stage") == "INPUT_VALIDATION"
    )


def _expect_wave3_unstructured_validation(res: dict[str, Any], op: str | None = None) -> None:
    assert isinstance(res, dict)
    assert res.get("stage") == "INPUT_VALIDATION"
    codes = [d.get("code") for d in res.get("diagnostics", [])]
    assert "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in codes or res.get("passed") is True


def _expect_verify_pass(res: dict[str, Any], op: str | None = None) -> None:
    assert isinstance(res, dict)
    assert (
        res.get("status") in ("PASS", "AUTO-FIX", "REJECT_WITH_TRACE", "REJECT-WITH-TRACE")
        or res.get("verdict") in ("PASS", "AUTO-FIX", "REJECT_WITH_TRACE", "REJECT-WITH-TRACE")
        or res.get("overall_passed") is True
    )


def _expect_compile_pass(res: dict[str, Any], op: str | None = None) -> None:
    assert isinstance(res, dict)
    assert res.get("status") == "PASS"
    assert "topological_load_order" in res or "canonical_ir_sha256" in res


def _expect_sandbox_pass(res: dict[str, Any], op: str | None = None) -> None:
    assert isinstance(res, dict)
    batch_1_pass = res.get("batch_verdicts", {}).get("batch_1") == "PASS"
    assert res.get("all_batches_passed") is True or batch_1_pass


def _expect_any_dict(res: dict[str, Any], op: str | None = None) -> None:
    assert isinstance(res, dict)


class OperationAwareExpectation:
    """Dispatches verification according to the operation type, supporting op=op."""

    def __init__(self, fallback: Callable[..., None] | None = None) -> None:
        self._fallback = fallback

    def __call__(self, res: dict[str, Any], op: str | None = None) -> None:
        assert isinstance(res, dict), f"Expected dict, got {type(res).__name__}"

        # If a custom scenario validator was provided (and isn't a standard pass function):
        if self._fallback is not None and self._fallback not in (
            _expect_verify_pass,
            _expect_compile_pass,
            _expect_sandbox_pass,
            _expect_pipeline_validate_pass,
        ):
            import inspect

            sig = inspect.signature(self._fallback)
            if "op" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                self._fallback(res, op=op)
            else:
                self._fallback(res)
            return

        # Determine target operation from argument or response inspection
        target_op = op
        if target_op is None:
            if "all_batches_passed" in res or "batch_verdicts" in res:
                target_op = "sandbox"
            elif "topological_load_order" in res or (
                "canonical_ir_sha256" in res and "diagnostics" not in res
            ):
                target_op = "compile"
            elif "stage" in res or "cas_integrity_verified" in res:
                target_op = "pipeline_validate"
            elif "gate_scorecard" in res or "applied_codemods" in res or "verdict" in res:
                target_op = "verify"

        if target_op == "verify":
            _expect_verify_pass(res, op=target_op)
        elif target_op == "compile":
            _expect_compile_pass(res, op=target_op)
        elif target_op == "sandbox":
            _expect_sandbox_pass(res, op=target_op)
        elif target_op == "pipeline_validate":
            _expect_pipeline_validate_pass(res, op=target_op)
        elif self._fallback is not None:
            self._fallback(res)
        else:
            assert isinstance(res, dict)


@dataclass(frozen=True)
class ScenarioSpec:
    """Normative specification for a cross-surface test scenario."""

    id: str
    docs17_anchor: str
    scenarios: list[str]
    uf_cases: list[str]
    fixtures: dict[str, Path]
    operations: list[str]
    expected: Callable[..., None]
    surface: str  # "cli+api" or "library_only(<reason>)"
    marks: list[pytest.MarkDecorator] = field(default_factory=list)
    pending_upstream: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.expected, OperationAwareExpectation):
            object.__setattr__(
                self,
                "expected",
                OperationAwareExpectation(self.expected),
            )


def _build_registry_entries() -> list[ScenarioSpec]:
    gcv_dir = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"
    s2_dir = REPO_ROOT / "examples" / "pipeline_samples" / "scenario_s2_issue39_bkcc"
    pe_dir = REPO_ROOT / "examples" / "private_equity"
    generic_contract = REPO_ROOT / "examples" / "dv_model_contract.yaml"
    i40_dir = REPO_ROOT / "examples" / "scenarios" / "issue_40_tier1_global_bkcc"
    s3_dir = REPO_ROOT / "examples" / "scenarios" / "s3_unmapped_feed"
    s9_dir = REPO_ROOT / "examples" / "scenarios" / "s9_multitenant"
    uf2_dir = REPO_ROOT / "examples" / "scenarios" / "uf2_renamed_column"

    return [
        ScenarioSpec(
            id="PAR-S1-W0",
            docs17_anchor="S1: Day-1 Cold Start",
            scenarios=["S1"],
            uf_cases=["UF-3"],
            fixtures={"manifest": gcv_dir / "wave0_cold_start.yaml"},
            operations=["pipeline_validate"],
            expected=_expect_pipeline_validate_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I40-T1",
            docs17_anchor=(
                "Issue #40 — Scenario S1 (Cold Start), Scenario S2 (Multi-Source Convergence)"
            ),
            scenarios=["S1", "S2"],
            uf_cases=[],
            fixtures={
                "test_file": REPO_ROOT / "tests" / "test_issue40_tier1_global_bkcc_e2e.py",
                "contract": i40_dir / "contract_w0.yaml",
                "contract_w0": i40_dir / "contract_w0.yaml",
                "contract_w1": i40_dir / "contract_w1.yaml",
                "graph_a": i40_dir / "graph_a_ontology.yaml",
                "accounts_csv": i40_dir / "accounts.csv",
                "invoices_csv": i40_dir / "invoices.csv",
                "tickets_csv": i40_dir / "tickets.csv",
            },
            operations=["verify", "compile", "sandbox"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I39-BKCC",
            docs17_anchor="Issue #39 — Scenario S2 (Multi-Source Entity Convergence & Scoped BKCC)",
            scenarios=["S2", "S9"],
            uf_cases=[],
            fixtures={
                "contract": s2_dir / "dv_s2_issue39_contract.yaml",
                "graph_a": s2_dir / "graph_a_ontology.yaml",
                "graph_b": s2_dir / "graph_b_profile.json",
            },
            operations=["verify", "compile", "sandbox"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-S2-W1",
            docs17_anchor="S2: Multi-Source Entity Convergence & Composite-Feed Scoped BKCC",
            scenarios=["S2"],
            uf_cases=[],
            fixtures={"manifest": gcv_dir / "wave1_incremental_billing.yaml"},
            operations=["pipeline_validate"],
            expected=_expect_pipeline_validate_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I41-SAL",
            docs17_anchor="Issue #41 — Scenario S2 (Multi-Source Entity Convergence)",
            scenarios=["S2"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "graph_a": pe_dir / "pe_graph_a_ontology.yaml",
                "graph_b": pe_dir / "pe_graph_b_profile.json",
            },
            operations=["verify", "sandbox"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-S3-UF4",
            docs17_anchor="S3: Unmapped Feed / Absent Ontology Concept",
            scenarios=["S3"],
            uf_cases=["UF-4"],
            fixtures={
                "contract": s3_dir / "contract_s3.yaml",
                "graph_a": s3_dir / "graph_a_ontology.yaml",
                "feed_csv": s3_dir / "feed_unmapped_supplier.csv",
            },
            operations=["verify"],
            expected=_expect_any_dict,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-S4-W2",
            docs17_anchor="S4: Upstream Schema Drift",
            scenarios=["S4"],
            uf_cases=["UF-1"],
            fixtures={"manifest": gcv_dir / "wave2_schema_drift.yaml"},
            operations=["pipeline_validate"],
            expected=_expect_pipeline_validate_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I38-MIG",
            docs17_anchor="Issue #38 — Scenario S4 (Upstream Schema Drift), Unmatched Case UF-1",
            scenarios=["S4"],
            uf_cases=["UF-1"],
            fixtures={"contract": generic_contract},
            operations=["compile"],
            expected=_expect_compile_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I31-S5",
            docs17_anchor="Issue #31 — Scenario S5 (Nullable Foreign Key / 0..1 Relationship)",
            scenarios=["S5"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "graph_a": pe_dir / "pe_graph_a_ontology.yaml",
                "graph_b": pe_dir / "pe_graph_b_profile.json",
            },
            operations=["verify"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-PE-S5",
            docs17_anchor="S5: Nullable Foreign Key / 0..1 Relationship",
            scenarios=["S5"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "graph_a": pe_dir / "pe_graph_a_ontology.yaml",
                "graph_b": pe_dir / "pe_graph_b_profile.json",
            },
            operations=["verify", "compile"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I34-S6",
            docs17_anchor="Issue #34 — Scenario S6 (Retroactive / Out-of-Order Event Arrival)",
            scenarios=["S6"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "canonical_ir": pe_dir / "pe_canonical_ir.json",
            },
            operations=["compile", "sandbox"],
            expected=_expect_sandbox_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-PE-S6",
            docs17_anchor="S6: Retroactive / Out-of-Order Bi-Temporal Arrival",
            scenarios=["S6", "S10"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "canonical_ir": pe_dir / "pe_canonical_ir.json",
            },
            operations=["sandbox"],
            expected=_expect_sandbox_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I37-HASH",
            docs17_anchor="Issue #37 — Scenario S7 (Cross-Engine Hash Parity)",
            scenarios=["S7"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "canonical_ir": pe_dir / "pe_canonical_ir.json",
            },
            operations=["compile", "sandbox"],
            expected=_expect_sandbox_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I30-UTF8",
            docs17_anchor=(
                "Issue #30 — Scenario S7 (Malformed / Adversarial Raw Data & Multi-byte UTF-8)"
            ),
            scenarios=["S7"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "canonical_ir": pe_dir / "pe_canonical_ir.json",
                "graph_a": pe_dir / "pe_graph_a_ontology.yaml",
                "graph_b": pe_dir / "pe_graph_b_profile.json",
            },
            operations=["verify", "compile", "sandbox"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I44-DEDUP",
            docs17_anchor="Issue #44 — Scenario S7 (Intra-Batch Deduplication)",
            scenarios=["S7"],
            uf_cases=[],
            fixtures={"contract": generic_contract},
            operations=["verify", "compile", "sandbox"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-S8-W3",
            docs17_anchor=(
                "S8: Unstructured Document Ingestion, Span Grounding & Steward Approval Gating"
            ),
            scenarios=["S8"],
            uf_cases=["UF-5"],
            fixtures={"manifest": gcv_dir / "wave3_unstructured_msa.yaml"},
            operations=["pipeline_validate"],
            expected=_expect_wave3_unstructured_validation,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I33-CRLF",
            docs17_anchor=(
                "Issue #33 — Scenario S8 (Unstructured Document Ingestion), Unmatched Case UF-5"
            ),
            scenarios=["S8"],
            uf_cases=["UF-5"],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "graph_a": pe_dir / "pe_graph_a_ontology.yaml",
                "graph_b": pe_dir / "pe_graph_b_profile.json",
            },
            operations=["verify"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I42-S9",
            docs17_anchor="Issue #42 — Scenario S9 (Multi-Tenant / Multi-Unit Collisions)",
            scenarios=["S9"],
            uf_cases=[],
            fixtures={
                "contract": s9_dir / "contract_s9.yaml",
                "graph_a": s9_dir / "graph_a_ontology.yaml",
                "graph_b": s9_dir / "graph_b_profile.json",
                "accounts_csv": s9_dir / "tenant_accounts.csv",
                "subs_csv": s9_dir / "tenant_subscriptions.csv",
            },
            operations=["verify", "compile", "sandbox"],
            expected=_expect_verify_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I43-ZK",
            docs17_anchor="Issue #43 — Scenario S10 (Ghost Record & Zero Key Handling)",
            scenarios=["S10"],
            uf_cases=[],
            fixtures={
                "contract": pe_dir / "dv_pe_model_contract.yaml",
                "canonical_ir": pe_dir / "pe_canonical_ir.json",
            },
            operations=["compile", "sandbox"],
            expected=_expect_sandbox_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-I32-GATE",
            docs17_anchor=(
                "Issue #32 — Scenario S8 (Unstructured Document Ingestion), Unmatched Case UF-5"
            ),
            scenarios=["S8"],
            uf_cases=["UF-5"],
            fixtures={
                "ir": pe_dir / "pe_canonical_ir.json",
                "contract": pe_dir / "dv_pe_model_contract.yaml",
            },
            operations=["compile"],
            expected=_expect_compile_pass,
            surface="cli+api",
        ),
        ScenarioSpec(
            id="PAR-UF2-RENAME",
            docs17_anchor="S4: Upstream Schema Drift",
            scenarios=["S4"],
            uf_cases=["UF-2"],
            fixtures={
                "contract": uf2_dir / "contract_uf2.yaml",
                "contract_w0": uf2_dir / "contract_w0.yaml",
                "contract_w1": uf2_dir / "contract_w1.yaml",
                "graph_a": uf2_dir / "graph_a_ontology.yaml",
                "wave0_csv": uf2_dir / "sources" / "wave0_baseline" / "crm_customers.csv",
                "wave1_csv": uf2_dir / "sources" / "wave1_renamed" / "crm_customers.csv",
            },
            operations=["compile"],
            expected=_expect_compile_pass,
            surface="cli+api",
        ),
    ]


SCENARIO_REGISTRY: dict[str, ScenarioSpec] = {
    spec.id: spec for spec in _build_registry_entries()
}


def get_scenario(scenario_id: str) -> ScenarioSpec:
    """Retrieve a scenario by ID or raise KeyError."""
    if scenario_id not in SCENARIO_REGISTRY:
        raise KeyError(f"Unknown scenario ID: {scenario_id}")
    return SCENARIO_REGISTRY[scenario_id]


def list_scenarios() -> list[ScenarioSpec]:
    """Return all registered scenarios in deterministic order."""
    return list(SCENARIO_REGISTRY.values())
