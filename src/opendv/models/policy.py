"""CEGAR verification policies, harness budgets, and monotonic energy calculation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CegarPolicy = Literal["symbolic_only", "fast", "balanced", "strict"]
ModelTier = Literal["FRONTIER_CRITIC", "STANDARD_WORKER"]
V8Mode = Literal["off", "on_demand", "auto"]


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


__all__ = [
    "CEGAR_POLICY_SPECS",
    "STEP4_DEFAULT_BUDGET",
    "CegarPolicy",
    "CegarPolicySpec",
    "HarnessBudget",
    "ModelTier",
    "V8Mode",
    "compute_cegar_energy",
]
