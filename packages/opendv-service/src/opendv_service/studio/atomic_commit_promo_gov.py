"""Promotion governance verification (ONT-EVO-001 + ONT-EVO-002 + local waiver replay)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    OntologyDelta,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveRelationshipOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    StewardWaiverRecord,
)
from opendv_core.genesis import GENESIS_VERSION, create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.ontology.validator import (
    ContractViolationError,
    GovernanceValidator,
    StructuralValidator,
    build_workspace_deployment_provider,
)
from opendv_service.studio.atomic_commit_audit import load_version_ontology
from opendv_service.studio.atomic_commit_errors import (
    HistoricalContractRationaleRequiredError,
    HistoricalContractWarningUnacknowledgedError,
)
from opendv_service.studio.atomic_commit_grounding import (
    _LocalWaiverVerifier,
    collect_touched_concept_uris,
    parse_semver_tuple,
    verify_ont_evo_002_post_state_grounding,
)
from opendv_service.studio.session_manager import SessionMetadata, StudioSessionManager
from opendv_service.studio.workspace_security import WorkspaceSecurityManager


def collect_locally_waived_session_concepts(
    security_manager: WorkspaceSecurityManager,
    session_dir: Path,
    session: SessionMetadata,
) -> set[str]:
    """Collect concept URIs covered by locally-signed admin waivers in session history."""
    versions_dir = session_dir / "versions"
    if not versions_dir.is_dir():
        return set()

    version_entries: list[tuple[tuple[int, int, int], Path]] = []
    for vdir in versions_dir.iterdir():
        if not vdir.is_dir():
            continue
        v_clean = vdir.name.lstrip("v")
        try:
            v_tuple = parse_semver_tuple(v_clean)
        except ValueError:
            continue
        version_entries.append((v_tuple, vdir))

    version_entries.sort(key=lambda item: item[0])
    waived_uris: set[str] = set()
    for _v_tuple, vdir in version_entries:
        delta_path = vdir / "delta.json"
        if not delta_path.is_file():
            continue
        try:
            v_delta = OntologyDelta.model_validate_json(delta_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            continue

        try:
            v_parent = load_version_ontology(
                session_dir, session, v_delta.base_version or GENESIS_VERSION
            )
        except FileNotFoundError:
            v_parent = create_genesis_ontology()

        waived_uris -= collect_touched_concept_uris(v_parent, v_delta)

        raw_waiver_items: list[dict[str, Any]] = []
        wfile = vdir / "waivers.json"
        if wfile.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                wloaded = json.loads(wfile.read_text(encoding="utf-8"))
                if isinstance(wloaded, list):
                    raw_waiver_items.extend(x for x in wloaded if isinstance(x, dict))
                elif isinstance(wloaded, dict) and isinstance(wloaded.get("waivers"), list):
                    raw_waiver_items.extend(
                        x for x in wloaded["waivers"] if isinstance(x, dict)
                    )
        probe_no_target = v_delta.model_copy(update={"target_ontology_version": None})
        probe_no_target.__pydantic_fields_set__.discard("target_ontology_version")
        meta_delta_shas: list[str] = [
            v_delta.canonical_sha256(),
            probe_no_target.canonical_sha256(),
        ]
        for fname in ("meta.json", "metadata.json", "evaluation.json"):
            fpath = vdir / fname
            if fpath.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    floaded = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(floaded, dict):
                        for dkey in ("delta_sha256", "submitted_delta_sha256"):
                            dval = floaded.get(dkey)
                            if isinstance(dval, str) and len(dval.strip()) == 64:
                                meta_delta_shas.append(dval.strip().lower())
                        if isinstance(floaded.get("waivers"), list):
                            raw_waiver_items.extend(
                                x for x in floaded["waivers"] if isinstance(x, dict)
                            )
                        if isinstance(floaded.get("waiver"), dict):
                            raw_waiver_items.append(floaded["waiver"])

        v_delta_sha = v_delta.canonical_sha256()
        ver_waiver_verifier = _LocalWaiverVerifier(
            security_manager, accepted_delta_shas=meta_delta_shas
        )
        for raw_w in raw_waiver_items:
            with contextlib.suppress(ValidationError, ValueError):
                w_rec = StewardWaiverRecord.model_validate(raw_w)
                if (
                    w_rec.authorized_role == "admin"
                    and w_rec.rationale
                    and len(w_rec.rationale.strip()) >= 10
                    and ver_waiver_verifier.verify_waiver(
                        w_rec,
                        delta_sha256=v_delta_sha,
                        base_sha256=v_delta.base_sha256,
                    )
                ):
                    waived_uris.update(w_rec.target_concept_uris)

    return waived_uris


def verify_promotion_governance(
    session_manager: StudioSessionManager,
    security_manager: WorkspaceSecurityManager,
    governance_validator: GovernanceValidator | None,
    session_dir: Path,
    session: SessionMetadata,
    existing_ws_ont: GraphAOntology | None,
    head_ont: GraphAOntology,
    *,
    target_contract_path: Path | None = None,
    acknowledge_historical_contract_warning: bool = False,
    historical_contract_rationale: str | None = None,
    waived_concepts_loader: Any | None = None,
) -> None:
    """Enforce ONT-EVO-001 + ONT-EVO-002 on `head_ont` prior to workspace promotion."""
    clean_hist_rationale = (historical_contract_rationale or "").strip()
    if acknowledge_historical_contract_warning and len(clean_hist_rationale) < 10:
        raise HistoricalContractRationaleRequiredError(
            "Historical contract rationale must be at least 10 characters."
        )

    StructuralValidator().validate(head_ont)

    dep_prov = (
        getattr(governance_validator, "deployment_provider", None)
        if governance_validator is not None
        else None
    )
    if dep_prov is None and target_contract_path is not None:
        dep_prov = build_workspace_deployment_provider(target_contract_path.parent)
    if dep_prov is None:
        dep_prov = build_workspace_deployment_provider(
            getattr(session_manager, "deployment_workspace_root", None)
            or getattr(session_manager, "workspace_root", None)
        )

    ws_grandfathered: set[str] = set()
    if existing_ws_ont is not None:
        ws_semver = parse_semver_tuple(existing_ws_ont.ontology_version)
        head_semver = parse_semver_tuple(head_ont.ontology_version)
        is_identical_snapshot = compute_ontology_sha256(
            existing_ws_ont
        ) == compute_ontology_sha256(head_ont)
        if not is_identical_snapshot and ws_semver >= head_semver:
            if not acknowledge_historical_contract_warning:
                raise HistoricalContractWarningUnacknowledgedError(
                    f"Workspace ontology version '{existing_ws_ont.ontology_version}' "
                    f"is >= session head '{head_ont.ontology_version}'; explicit warning "
                    "acknowledgement required."
                )
            if len(clean_hist_rationale) < 10:
                raise HistoricalContractRationaleRequiredError(
                    "Historical contract rationale must be at least 10 characters."
                )

        ws_concepts = {c.concept_uri: c for c in existing_ws_ont.canonical_business_concepts}
        head_concepts = {c.concept_uri: c for c in head_ont.canonical_business_concepts}
        ws_rels = {r.relationship_uri: r for r in existing_ws_ont.canonical_relationships}
        head_rels = {r.relationship_uri: r for r in head_ont.canonical_relationships}

        rel_touched_uris: set[str] = set()
        for r_uri, ws_r in ws_rels.items():
            head_r = head_rels.get(r_uri)
            if head_r is None or (
                getattr(head_r, "deprecated", False) and not getattr(ws_r, "deprecated", False)
            ):
                for ep in getattr(ws_r, "endpoints", ()) or ():
                    if ep:
                        rel_touched_uris.add(str(ep))
                if getattr(ws_r, "source_concept_uri", None):
                    rel_touched_uris.add(str(ws_r.source_concept_uri))
                if getattr(ws_r, "target_concept_uri", None):
                    rel_touched_uris.add(str(ws_r.target_concept_uri))

        for u, ws_c in ws_concepts.items():
            head_c = head_concepts.get(u)
            if (
                head_c is not None
                and u not in rel_touched_uris
                and ws_c.model_dump(mode="json") == head_c.model_dump(mode="json")
            ):
                ws_grandfathered.add(u)

        # ONT-EVO-001: Check breaking mutations against deployed contracts & SemVer major bump
        diff_ops: list[Any] = []
        for removed_u in sorted(set(ws_concepts) - set(head_concepts)):
            diff_ops.append(
                RemoveConceptOp.model_construct(op_type="remove_concept", concept_uri=removed_u)
            )
        for removed_r in sorted(set(ws_rels) - set(head_rels)):
            diff_ops.append(
                RemoveRelationshipOp.model_construct(
                    op_type="remove_relationship",
                    relationship_uri=removed_r,
                    relationship_name=getattr(ws_rels[removed_r], "relationship_name", "")
                    or "",
                )
            )
        for u in sorted(set(ws_concepts) & set(head_concepts)):
            ws_c = ws_concepts[u]
            head_c = head_concepts[u]
            ws_attr_map = {a.attribute_name: a for a in (ws_c.attributes or [])}
            head_attr_map = {a.attribute_name: a for a in (head_c.attributes or [])}
            ws_attrs = set(ws_attr_map)
            head_attrs = set(head_attr_map)
            for removed_attr in sorted(ws_attrs - head_attrs):
                diff_ops.append(
                    RemoveAttributeOp.model_construct(
                        op_type="remove_attribute",
                        concept_uri=u,
                        attribute_name=removed_attr,
                    )
                )
            for added_attr in sorted(head_attrs - ws_attrs):
                attr_obj = head_attr_map[added_attr]
                if getattr(attr_obj, "is_optional", True) is False:
                    diff_ops.append(
                        AddAttributeOp.model_construct(
                            op_type="add_attribute",
                            concept_uri=u,
                            attribute_name=attr_obj.attribute_name,
                            data_type=attr_obj.data_type,
                            is_optional=False,
                        )
                    )
            for common_attr in sorted(ws_attrs & head_attrs):
                if (
                    getattr(ws_attr_map[common_attr], "is_optional", True) is not False
                    and getattr(head_attr_map[common_attr], "is_optional", True) is False
                ):
                    attr_obj = head_attr_map[common_attr]
                    diff_ops.append(
                        AddAttributeOp.model_construct(
                            op_type="add_attribute",
                            concept_uri=u,
                            attribute_name=attr_obj.attribute_name,
                            data_type=attr_obj.data_type,
                            is_optional=False,
                        )
                    )
            if (
                ws_c.parent_concept_uri is not None
                and ws_c.parent_concept_uri != head_c.parent_concept_uri
            ):
                diff_ops.append(
                    SetParentConceptOp.model_construct(
                        op_type="set_parent_concept",
                        concept_uri=u,
                        parent_concept_uri=head_c.parent_concept_uri,
                    )
                )
            if ws_c.elm_archetype is not None and ws_c.elm_archetype != head_c.elm_archetype:
                diff_ops.append(
                    SetConceptElmArchetypeOp.model_construct(
                        op_type="set_concept_elm_archetype",
                        concept_uri=u,
                        elm_archetype=head_c.elm_archetype,
                    )
                )

        if diff_ops:
            if dep_prov is not None:
                synth_delta = OntologyDelta.model_construct(
                    delta_id="promo-ont-evo-001-check",
                    base_ontology_version=existing_ws_ont.ontology_version,
                    target_ontology_version=head_ont.ontology_version,
                    operations=diff_ops,
                )
                GovernanceValidator(
                    deployment_provider=dep_prov,
                    strict_verifier_required=False,
                ).validate_deployed_contracts(existing_ws_ont, synth_delta)

            if head_semver > ws_semver:
                ws_major = ws_semver[0]
                head_major = head_semver[0]
                if head_major <= ws_major:
                    raise ContractViolationError(
                        f"ONT_EVO_CONTRACT_VIOLATION: Promoting '{head_ont.ontology_version}' "
                        f"over '{existing_ws_ont.ontology_version}' mutates existing contract "
                        "elements destructively without a MAJOR SemVer bump."
                    )

    waived_in_session = (
        waived_concepts_loader(session_dir, session)
        if waived_concepts_loader is not None
        else collect_locally_waived_session_concepts(security_manager, session_dir, session)
    )
    concepts_needing_path_a = {
        c.concept_uri
        for c in head_ont.canonical_business_concepts
        if c.concept_uri not in ws_grandfathered and c.concept_uri not in waived_in_session
    }
    if concepts_needing_path_a:
        verifier = (
            governance_validator.grounding_verifier
            if governance_validator is not None
            and governance_validator.grounding_verifier is not None
            else get_default_grounding_verifier()
        )
        probe_delta = OntologyDelta(
            delta_id="promo-ont-evo-002-check",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            operations=[],
        )
        verify_ont_evo_002_post_state_grounding(
            create_genesis_ontology(),
            probe_delta,
            head_ont,
            security_manager=security_manager,
            grounding_verifier=verifier,
            explicit_touched_uris=concepts_needing_path_a,
        )
