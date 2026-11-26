"""Layer-3 Data Vault 2.0 modeling package (Mode 1 verify, Mode 2 CEGAR, critics, ports)."""

from __future__ import annotations

from opendv.modeling.critics import (
    audit_v6b_justifications,
    run_v7_semantic_critic,
    run_v8_horizon_critic,
)
from opendv.modeling.engine import model_vault
from opendv.modeling.models import (
    CandidateState,
    DVModelingRequest,
    DVModelingResult,
    ModelingResumeInput,
    VerifyDraftRequest,
    VerifyDraftResult,
    WaveEvolutionInput,
)
from opendv.modeling.ports import (
    MODEL_ENV_OVERRIDES,
    MODEL_TIER_DEFAULTS,
    LoadedModelingPack,
    ModelingCheckpointStore,
    ModelingControlReader,
    ModelingEventSink,
    ModelingHarnessPort,
    ModelingPackLoader,
    ModelingScoutDispatcher,
    ModelingTurnRequest,
    ModelingTurnResult,
    ResolvedModelingModel,
    RoleKind,
    enforce_v7_tier_lock,
    resolve_model_for_agent,
)
from opendv.modeling.prompts import scan_fragility_surface
from opendv.modeling.verify_mode import verify_draft_vault
from opendv.models.modeling_preferences import ModelingPreferences

__all__ = [
    "MODEL_ENV_OVERRIDES",
    "MODEL_TIER_DEFAULTS",
    "CandidateState",
    "DVModelingRequest",
    "DVModelingResult",
    "LoadedModelingPack",
    "ModelingCheckpointStore",
    "ModelingControlReader",
    "ModelingEventSink",
    "ModelingHarnessPort",
    "ModelingPackLoader",
    "ModelingPreferences",
    "ModelingResumeInput",
    "ModelingScoutDispatcher",
    "ModelingTurnRequest",
    "ModelingTurnResult",
    "ResolvedModelingModel",
    "RoleKind",
    "VerifyDraftRequest",
    "VerifyDraftResult",
    "WaveEvolutionInput",
    "audit_v6b_justifications",
    "enforce_v7_tier_lock",
    "model_vault",
    "resolve_model_for_agent",
    "run_v7_semantic_critic",
    "run_v8_horizon_critic",
    "scan_fragility_surface",
    "verify_draft_vault",
]
