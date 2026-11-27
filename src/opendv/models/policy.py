"""CEGAR verification policies, harness budgets, and monotonic energy calculation."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

CegarPolicy = Literal["symbolic_only", "fast", "balanced", "strict"]
ModelTier = Literal["FRONTIER_CRITIC", "STANDARD_WORKER"]
V8Mode = Literal["off", "on_demand", "auto"]

VALID_V6B_RULE_IDS: frozenset[str] = frozenset(
    {
        *(f"ONT-AP-00{i}" for i in range(1, 6)),
        *(f"DV-AP-{i:03d}" for i in range(1, 13)),
    }
)
BALANCED_BLOCKING_V6B_RULES: frozenset[str] = frozenset(
    {"DV-AP-001", "DV-AP-003", "DV-AP-009", "DV-AP-010", "DV-AP-011"}
)
AGENT_PACK_IDS: tuple[str, ...] = (
    "ontology-first-mate",
    "table-cluster-scout",
    "page-range-scout",
    "ship-vault",
    "dv-semantic-critic",
    "dv-horizon-critic",
)
VALID_MODEL_OVERRIDE_TARGETS: frozenset[str] = frozenset(
    {*(str(t) for t in get_args(ModelTier)), *AGENT_PACK_IDS}
)


class HarnessBudget(BaseModel):
    """Execution budget ceilings for Step 4 and Step 6 agentic runs."""

    model_config = ConfigDict(extra="forbid")

    max_turns: int = Field(ge=1)
    max_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    max_broker_calls: int = Field(default=200, ge=1)
    max_tool_payload_bytes: int = Field(default=65536, ge=1)


STEP4_DEFAULT_BUDGET = HarnessBudget(
    max_turns=24,
    max_tokens=300_000,
    timeout_seconds=600,
    max_broker_calls=200,
    max_tool_payload_bytes=65536,
)


class CegarPolicySpec(BaseModel):
    """Resolved specification for a Step 6 CEGAR verification policy."""

    model_config = ConfigDict(extra="forbid")

    policy: CegarPolicy
    k_max: int = Field(ge=0)
    v7_floor: int | None = None
    v7_critic_tier: ModelTier | None = None
    v7_tier_locked: bool = False
    v8_mode: V8Mode
    require_zero_unwaived_v6b: bool = False
    blocking_v6b_rules: frozenset[str] = Field(default_factory=frozenset)
    allow_preference_exemptions: bool = True
    budget: HarnessBudget


CEGAR_POLICY_SPECS: dict[CegarPolicy, CegarPolicySpec] = {
    "symbolic_only": CegarPolicySpec(
        policy="symbolic_only",
        k_max=0,
        v7_floor=None,
        v7_critic_tier=None,
        v7_tier_locked=False,
        v8_mode="off",
        require_zero_unwaived_v6b=False,
        blocking_v6b_rules=frozenset(),
        allow_preference_exemptions=True,
        budget=HarnessBudget(max_turns=1, max_tokens=60_000, timeout_seconds=120),
    ),
    "fast": CegarPolicySpec(
        policy="fast",
        k_max=2,
        v7_floor=None,
        v7_critic_tier=None,
        v7_tier_locked=False,
        v8_mode="on_demand",
        require_zero_unwaived_v6b=False,
        blocking_v6b_rules=frozenset(),
        allow_preference_exemptions=True,
        budget=HarnessBudget(max_turns=24, max_tokens=250_000, timeout_seconds=600),
    ),
    "balanced": CegarPolicySpec(
        policy="balanced",
        k_max=4,
        v7_floor=90,
        v7_critic_tier="FRONTIER_CRITIC",
        v7_tier_locked=False,
        v8_mode="auto",
        require_zero_unwaived_v6b=False,
        blocking_v6b_rules=BALANCED_BLOCKING_V6B_RULES,
        allow_preference_exemptions=True,
        budget=HarnessBudget(max_turns=40, max_tokens=500_000, timeout_seconds=1_200),
    ),
    "strict": CegarPolicySpec(
        policy="strict",
        k_max=6,
        v7_floor=95,
        v7_critic_tier="FRONTIER_CRITIC",
        v7_tier_locked=True,
        v8_mode="auto",
        require_zero_unwaived_v6b=True,
        blocking_v6b_rules=VALID_V6B_RULE_IDS,
        allow_preference_exemptions=False,
        budget=HarnessBudget(max_turns=60, max_tokens=800_000, timeout_seconds=1_800),
    ),
}


def compute_cegar_energy(
    v1_v6_fail: int,
    v6b_fail: int,
    *,
    policy: CegarPolicy = "balanced",
    v7_score: int | None = None,
    v7_floor: int | None = None,
) -> int:
    """Compute monotonic CEGAR defect energy E(IR_k).

    Formula:
        E(IR_k) = 100 * |V1..V6_fail| + 10 * |V6b_fail| + max(0, v7_floor - v7)
    When `v7_floor is None` (e.g. `fast`) or `v7_score is None` (V7 skipped/not yet run),
    the V7 floor penalty term contributes `0`.
    """
    if v1_v6_fail < 0 or v6b_fail < 0:
        raise ValueError("Failure counts must be non-negative")
    effective_floor = v7_floor if v7_floor is not None else CEGAR_POLICY_SPECS[policy].v7_floor
    v7_penalty = 0
    if effective_floor is not None and v7_score is not None:
        v7_penalty = max(0, effective_floor - v7_score)
    return 100 * v1_v6_fail + 10 * v6b_fail + v7_penalty


def compute_deterministic_energy(v1_v6_fail: int, blocking_v6b_fail: int) -> int:
    """Compute deterministic energy from V1-V6 and policy-blocking V6b failure counts."""
    if v1_v6_fail < 0 or blocking_v6b_fail < 0:
        raise ValueError("Failure counts must be non-negative")
    return 100 * v1_v6_fail + 10 * blocking_v6b_fail


__all__ = [
    "AGENT_PACK_IDS",
    "BALANCED_BLOCKING_V6B_RULES",
    "CEGAR_POLICY_SPECS",
    "STEP4_DEFAULT_BUDGET",
    "VALID_MODEL_OVERRIDE_TARGETS",
    "VALID_V6B_RULE_IDS",
    "CegarPolicy",
    "CegarPolicySpec",
    "HarnessBudget",
    "ModelTier",
    "V8Mode",
    "compute_cegar_energy",
    "compute_deterministic_energy",
]
