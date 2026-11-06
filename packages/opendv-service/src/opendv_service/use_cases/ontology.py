"""Ontology use cases: suggestion, validation, diffing, and steward decisions (IP-1, IP-6)."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import uuid
from pathlib import Path

import yaml

from opendv_contracts.dtos.ontology import (
    OntologyDecision,
    OntologyDecisionRequest,
    OntologyDecisionResponse,
    OntologyDiff,
    OntologyDiffRequest,
    OntologyProposal,
    OntologySuggestionRequest,
    OntologyValidateDeltaRequest,
    OntologyValidationResult,
)
from opendv_service.errors import InputValidationError
from opendv_service.ontology.applier import apply_ontology_delta
from opendv_service.ontology.diff import ontology_diff
from opendv_service.ontology.hashing import compute_ontology_sha256
from opendv_service.ontology.loader import (
    load_base_ontology,
    load_deployed_contract,
    load_ontology_delta,
)
from opendv_service.ontology.registry import invoke_recommender
from opendv_service.ontology.validator import validate_ontology_delta
from opendv_service.state import StateStore


def suggest_ontology_sync(
    state: StateStore,
    req: OntologySuggestionRequest,
    *,
    workspace_root: Path | None = None,
) -> OntologyProposal:
    """Synchronous entry point for ontology proposal generation."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                invoke_recommender(req, recommender_id=req.recommender_id),
            ).result()

    return asyncio.run(invoke_recommender(req, recommender_id=req.recommender_id))


async def suggest_ontology_async(
    state: StateStore,
    req: OntologySuggestionRequest,
    *,
    workspace_root: Path | None = None,
) -> OntologyProposal:
    """Asynchronous entry point for ontology proposal generation."""
    return await invoke_recommender(req, recommender_id=req.recommender_id)


def execute_validate_delta(
    state: StateStore,
    req: OntologyValidateDeltaRequest,
    *,
    workspace_root: Path | None = None,
) -> OntologyValidationResult:
    """Deterministically validate an ontology delta against base Graph A and contract."""
    delta_source = req.delta or req.delta_content or req.delta_path
    if delta_source is None:
        raise InputValidationError("Missing required delta, delta_content, or delta_path")

    base_source = req.base_ontology or req.base_ontology_content or req.base_ontology_path
    if base_source is None:
        raise InputValidationError(
            "Missing required base_ontology, base_ontology_content, or base_ontology_path"
        )

    contract_source = req.deployed_contract or req.contract_content or req.contract_path

    delta = load_ontology_delta(delta_source, workspace_root=workspace_root)
    base = load_base_ontology(base_source, workspace_root=workspace_root)
    contract = load_deployed_contract(contract_source, workspace_root=workspace_root)

    return validate_ontology_delta(base, delta, deployed_contract=contract)


def execute_ontology_diff(
    state: StateStore,
    req: OntologyDiffRequest,
    *,
    workspace_root: Path | None = None,
) -> OntologyDiff:
    """Compute structural difference between two Graph A ontologies."""
    base_source = req.base_ontology or req.base_ontology_content or req.base_ontology_path
    if base_source is None:
        raise InputValidationError(
            "Missing required base_ontology, base_ontology_content, or base_ontology_path"
        )

    target_source = req.target_ontology or req.target_ontology_content or req.target_ontology_path
    if target_source is None:
        raise InputValidationError(
            "Missing required target_ontology, target_ontology_content, or target_ontology_path"
        )

    base = load_base_ontology(base_source, workspace_root=workspace_root)
    target = load_base_ontology(target_source, workspace_root=workspace_root)

    return ontology_diff(base, target)


def execute_ontology_decision(
    state: StateStore,
    req: OntologyDecisionRequest,
    *,
    workspace_root: Path | None = None,
) -> OntologyDecisionResponse:
    """Record steward decision and optionally apply accepted delta to generate Graph A vN+1."""
    now_utc = datetime.datetime.now(datetime.UTC).isoformat()
    decision_id = f"dec-ont-{uuid.uuid4().hex[:12]}"
    action = req.action.upper()

    base_source = req.base_ontology or req.base_ontology_content or req.base_ontology_path
    base_sha: str | None = None
    if base_source is not None:
        try:
            base = load_base_ontology(base_source, workspace_root=workspace_root)
            base_sha = compute_ontology_sha256(base)
        except (ValueError, TypeError, OSError):
            base = None
    else:
        base = None

    if action == "REJECT":
        decision = OntologyDecision(
            decision_id=decision_id,
            proposal_id=req.proposal_id,
            run_id=req.run_id,
            hold_id=req.hold_id,
            action="REJECT",
            base_ontology_sha256=base_sha,
            rejected_op_ids=req.rejected_op_ids or [],
            decided_by=req.decided_by,
            steward_notes=req.steward_notes,
            created_at_utc=now_utc,
        )
        return OntologyDecisionResponse(
            status="COMPLETED",
            decision=decision,
        )

    # Action == "ACCEPT"
    delta_source = req.delta or req.delta_content or req.delta_path
    if delta_source is None:
        raise InputValidationError(
            "Cannot accept ontology delta: missing delta, delta_content, or delta_path"
        )

    if base is None:
        raise InputValidationError("Cannot accept ontology delta: missing or invalid base_ontology")

    contract_source = req.deployed_contract or req.contract_content or req.contract_path
    contract = load_deployed_contract(contract_source, workspace_root=workspace_root)

    delta = load_ontology_delta(delta_source, workspace_root=workspace_root)

    # 1. Validate delta against base and contract
    val_result = validate_ontology_delta(base, delta, deployed_contract=contract)
    if not val_result.valid:
        violations_str = "; ".join(val_result.violations)
        raise InputValidationError(
            f"Cannot accept invalid delta violating evolution locks: {violations_str}"
        )

    # 2. Determine accepted operations
    if req.accepted_op_ids is not None:
        accepted_ops = req.accepted_op_ids
    else:
        accepted_ops = [op.op_id for op in delta.operations]

    # 3. Apply delta deterministically
    resulting = apply_ontology_delta(
        base,
        delta,
        accepted_op_ids=accepted_ops,
        decision_id=decision_id,
    )

    resulting_dict = resulting.model_dump(mode="json", exclude_none=True)
    resulting_yaml = yaml.safe_dump(resulting_dict, sort_keys=False)
    resulting_bytes = resulting_yaml.encode("utf-8")
    resulting_sha = hashlib.sha256(resulting_bytes).hexdigest().lower()

    # 4. Store in CAS
    if hasattr(state, "cas_store") and state.cas_store is not None:
        state.cas_store.put(resulting_bytes)
    if hasattr(state, "cas") and state.cas is not None:
        state.cas.put(resulting_bytes)

    # 5. Build decision record
    decision = OntologyDecision(
        decision_id=decision_id,
        proposal_id=req.proposal_id,
        run_id=req.run_id,
        hold_id=req.hold_id,
        action="ACCEPT",
        base_ontology_sha256=base_sha,
        accepted_op_ids=accepted_ops,
        rejected_op_ids=req.rejected_op_ids or [],
        applied_delta_sha256=hashlib.sha256(
            delta.model_dump_json().encode("utf-8")
        ).hexdigest().lower(),
        resulting_ontology_sha256=resulting_sha,
        decided_by=req.decided_by,
        steward_notes=req.steward_notes,
        created_at_utc=now_utc,
    )

    return OntologyDecisionResponse(
        status="COMPLETED",
        decision=decision,
        resulting_ontology_yaml=resulting_yaml,
        resulting_ontology_sha256=resulting_sha,
        resulting_ontology_version=resulting.ontology_version,
        validation=val_result,
    )


__all__ = [
    "execute_ontology_decision",
    "execute_ontology_diff",
    "execute_validate_delta",
    "suggest_ontology_async",
    "suggest_ontology_sync",
]
