"""Hold resolution and ontology delta application engine (ONT-04)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import yaml

from opendv_agent_harness.telemetry.translator import build_observability_event_dict
from opendv_contracts.dtos.ontology import (
    AcceptOntologyDeltaOption,
    CustomOntologyDeltaOption,
    GeneralHoldOption,
    OntologyDecision,
    OntologyDelta,
    RejectOntologyDeltaOption,
    StewardHoldOption,
)
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import (
    canonicalize_graph_a_dict,
    compute_ontology_sha256,
)
from opendv_service.ontology.applier import apply_ontology_delta
from opendv_service.ontology.validator import validate_ontology_delta
from opendv_telemetry import ContentAddressableStore


@dataclass
class OntologyResolutionResult:
    """Outcome of resolving an ontology-related steward hold."""

    new_ontology: GraphAOntology | None
    new_graph_a_sha256: str | None
    decision: OntologyDecision
    resumed_stage: str
    status: Literal["ACCEPTED", "REJECTED"]
    action: str


def resolve_ontology_hold(
    *,
    base_ontology: GraphAOntology,
    option: StewardHoldOption,
    run_id: str,
    hold_id: str,
    steward_id: str = "steward",
    cas_store: ContentAddressableStore | None = None,
    deployed_contract: NoMistakeDataVault2ContractV5 | dict[str, Any] | None = None,
    proposal_id: str | None = None,
    ledger: Any | None = None,
    target_version: str | None = None,
) -> OntologyResolutionResult:
    """Resolve an ontology hold with a typed StewardHoldOption.

    1. Validates delta against base ontology and deployed contract.
    2. Applies delta to create Graph A vN+1.
    3. Writes canonical JSON and YAML representations of Graph A vN+1 to CAS under graph_a_sha256.
    4. Creates and records an OntologyDecision in the telemetry ledger.
    5. Returns resolution outcome indicating resumed stage and status.
    """
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_sha256 = compute_ontology_sha256(base_ontology)

    if isinstance(option, (AcceptOntologyDeltaOption, CustomOntologyDeltaOption)):
        delta: OntologyDelta = (
            option.delta if isinstance(option, AcceptOntologyDeltaOption) else option.custom_delta
        )
        accepted_op_ids: list[str] | None = (
            option.accepted_op_ids
            if isinstance(option, AcceptOntologyDeltaOption) and option.accepted_op_ids
            else None
        )

        # 1. Validate delta against base ontology and deployed contract
        if accepted_op_ids is not None:
            sub_ops = [op for op in delta.operations if op.op_id in set(accepted_op_ids)]
            delta_to_validate = OntologyDelta(
                delta_id=delta.delta_id,
                parent_sha256=delta.parent_sha256,
                domain_namespace=delta.domain_namespace,
                base_ontology_version=delta.base_ontology_version,
                target_ontology_version=delta.target_ontology_version,
                operations=sub_ops,
                metadata=delta.metadata,
            )
        else:
            delta_to_validate = delta

        val_res = validate_ontology_delta(
            base_ontology, delta_to_validate, deployed_contract=deployed_contract
        )
        if not val_res.valid:
            violations_str = "; ".join(val_res.violations)
            raise ValueError(f"Cannot accept invalid ontology delta: {violations_str}")

        # 2. Apply delta
        decision_id = f"dec-ont-{uuid.uuid4().hex[:10]}"
        new_ontology = apply_ontology_delta(
            base=base_ontology,
            delta=delta,
            accepted_op_ids=accepted_op_ids,
            decision_id=decision_id,
            target_version=target_version,
            deployed_contract=deployed_contract,
        )
        new_sha256 = compute_ontology_sha256(new_ontology)

        # 3. Write Graph A vN+1 to CAS under graph_a_sha256
        if cas_store is not None:
            # Canonical JCS representation (matches new_sha256)
            jcs_bytes = dumps_jcs_bytes(canonicalize_graph_a_dict(new_ontology))
            cas_store.put(jcs_bytes)

            # Also YAML representation
            yaml_bytes = yaml.safe_dump(
                new_ontology.model_dump(mode="json", exclude_none=True),
                sort_keys=False,
            ).encode("utf-8")
            cas_store.put(yaml_bytes)

        # 4. Formulate OntologyDecision
        effective_accepted_ops = (
            accepted_op_ids
            if accepted_op_ids is not None
            else [op.op_id for op in delta.operations]
        )
        effective_rejected_ops = [
            op.op_id for op in delta.operations if op.op_id not in effective_accepted_ops
        ]
        computed_delta_sha = (
            hashlib.sha256(dumps_jcs_bytes(delta.model_dump(mode="json", exclude_none=True)))
            .hexdigest()
            .lower()
        )
        delta_sha = getattr(option, "delta_sha256", None) or computed_delta_sha

        decision = OntologyDecision(
            decision_id=decision_id,
            proposal_id=proposal_id,
            run_id=run_id,
            hold_id=hold_id,
            action="ACCEPT",
            base_ontology_sha256=base_sha256,
            accepted_op_ids=effective_accepted_ops,
            rejected_op_ids=effective_rejected_ops,
            applied_delta_sha256=delta_sha,
            resulting_ontology_sha256=new_sha256,
            decided_by=steward_id,
            steward_notes=option.steward_note,
            notes=option.steward_note,
            timestamp=now_iso,
            created_at_utc=now_iso,
        )

        # 5. Emit into telemetry ledger if emitter provided
        if ledger is not None:
            evt = build_observability_event_dict(
                event_id=f"evt-{decision_id}",
                run_id=run_id,
                trace_id=uuid.uuid4().hex,
                span_id=uuid.uuid4().hex[:16],
                parent_span_id=None,
                timestamp_utc=now_iso,
                stage_id="STAGE_05_AXI_DV_STATIC_GATES",
                workbench_view_id="WB-06",
                viz_id="VIZ-GATE-02",
                agent_persona="vault-first-mate",
                status="PASS",
                headline=(
                    f"Steward '{steward_id}' accepted ontology delta -> "
                    f"v{new_ontology.ontology_version}."
                ),
                business_impact=(
                    f"Evolved Graph A from {base_ontology.ontology_version} to "
                    f"{new_ontology.ontology_version} ({len(effective_accepted_ops)} ops applied)."
                ),
                data_classification="INTERNAL",
                rule_ids_evaluated=["ONT-EVO-001", "DV-G4-001"],
                summary_metrics={
                    "hold_id": hold_id,
                    "decision_id": decision_id,
                    "action": "ACCEPT",
                    "accepted_ops_count": len(effective_accepted_ops),
                },
                context_refs={
                    "base_ontology_sha256": base_sha256,
                    "resulting_ontology_sha256": new_sha256,
                },
            )
            ledger.emit(evt)

        return OntologyResolutionResult(
            new_ontology=new_ontology,
            new_graph_a_sha256=new_sha256,
            decision=decision,
            resumed_stage="STAGE_03_SHIP_VAULT_PASS1",
            status="ACCEPTED",
            action="APPROVE",
        )

    elif isinstance(option, RejectOntologyDeltaOption):
        decision_id = f"dec-ont-{uuid.uuid4().hex[:10]}"
        rejected_ops = option.rejected_op_ids if option.rejected_op_ids else []
        notes = f"{option.reason}; {option.steward_note}".strip("; ")

        decision = OntologyDecision(
            decision_id=decision_id,
            proposal_id=proposal_id,
            run_id=run_id,
            hold_id=hold_id,
            action="REJECT",
            base_ontology_sha256=base_sha256,
            accepted_op_ids=[],
            rejected_op_ids=rejected_ops,
            applied_delta_sha256=None,
            resulting_ontology_sha256=base_sha256,
            decided_by=steward_id,
            steward_notes=notes,
            notes=notes,
            timestamp=now_iso,
            created_at_utc=now_iso,
        )

        if ledger is not None:
            evt = build_observability_event_dict(
                event_id=f"evt-{decision_id}",
                run_id=run_id,
                trace_id=uuid.uuid4().hex,
                span_id=uuid.uuid4().hex[:16],
                parent_span_id=None,
                timestamp_utc=now_iso,
                stage_id="STAGE_05_AXI_DV_STATIC_GATES",
                workbench_view_id="WB-06",
                viz_id="VIZ-GATE-02",
                agent_persona="vault-first-mate",
                status="REJECT-WITH-TRACE",
                headline=f"Steward '{steward_id}' rejected ontology delta: {option.reason}",
                business_impact="Ontology delta rejected; pipeline wave remains halted.",
                data_classification="INTERNAL",
                rule_ids_evaluated=["DV-G4-001"],
                summary_metrics={
                    "hold_id": hold_id,
                    "decision_id": decision_id,
                    "action": "REJECT",
                },
                context_refs={
                    "base_ontology_sha256": base_sha256,
                },
            )
            ledger.emit(evt)

        return OntologyResolutionResult(
            new_ontology=None,
            new_graph_a_sha256=None,
            decision=decision,
            resumed_stage="",
            status="REJECTED",
            action="REJECT",
        )

    elif isinstance(option, GeneralHoldOption):
        decision_id = f"dec-gen-{uuid.uuid4().hex[:10]}"
        action_name = option.action.upper()
        is_approve = action_name in {"APPROVE", "ANSWER", "ACCEPT"}
        decision = OntologyDecision(
            decision_id=decision_id,
            proposal_id=proposal_id,
            run_id=run_id,
            hold_id=hold_id,
            action=option.action,
            base_ontology_sha256=base_sha256,
            accepted_op_ids=[],
            rejected_op_ids=[],
            applied_delta_sha256=None,
            resulting_ontology_sha256=base_sha256,
            decided_by=steward_id,
            steward_notes=option.steward_note,
            notes=option.steward_note,
            timestamp=now_iso,
            created_at_utc=now_iso,
        )
        return OntologyResolutionResult(
            new_ontology=None,
            new_graph_a_sha256=None,
            decision=decision,
            resumed_stage="STAGE_03_SHIP_VAULT_PASS1" if is_approve else "",
            status="ACCEPTED" if is_approve else "REJECTED",
            action="APPROVE" if is_approve else "REJECT",
        )

    else:
        raise TypeError(f"Unsupported StewardHoldOption type: {type(option)}")


__all__ = [
    "OntologyResolutionResult",
    "resolve_ontology_hold",
]
