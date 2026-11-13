"""DeterministicTestkitRecommender — Test-only Ontology Recommender.

Implements the OntologyRecommender SPI protocol strictly for testing in opendv-testkit
(Decision D9, ONT-05, REQ-MOCK-01). Zero production mock code in core packages.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    EvidenceRef,
    OntologyDelta,
    OntologyProposal,
    OntologySuggestionRequest,
    RefineDefinitionOp,
)
from opendv_contracts.ports.ontology import (
    OntologyRecommendationRequest,
    RecommenderCapabilities,
)

RAW_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RAW_CC_PATTERN = re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b")


def _scan_for_unredacted_pii(data: Any) -> list[str]:
    """Recursively scan a data structure for unredacted PII tokens."""
    violations: list[str] = []
    if isinstance(data, str):
        if RAW_SSN_PATTERN.search(data):
            violations.append(f"Raw SSN detected: '{data[:6]}...'")
        if RAW_CC_PATTERN.search(data):
            violations.append(f"Raw Credit Card detected: '{data[:6]}...'")
    elif isinstance(data, dict):
        for k, v in data.items():
            if (
                str(k).lower() in ("tax_ssn_tin", "ssn", "credit_card", "pan")
                and isinstance(v, str)
                and not v.startswith("[REDACTED_")
                and "***" not in v
            ):
                violations.append(f"Unmasked sensitive field '{k}': '{v}'")
            violations.extend(_scan_for_unredacted_pii(v))
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            violations.extend(_scan_for_unredacted_pii(item))
    return violations


class DeterministicTestkitRecommender:
    """Deterministic, zero-mock testkit recommender conforming to OntologyRecommender SPI."""

    def __init__(
        self,
        recommender_id: str = "testkit-recommender",
        *,
        recommender_version: str = "1.0.0",
        fail_mode: bool = False,
        custom_proposal: OntologyProposal | None = None,
        enforce_redaction_check: bool = True,
    ) -> None:
        self.recommender_id = recommender_id
        self.recommender_version = recommender_version
        self.fail_mode = fail_mode
        self.custom_proposal = custom_proposal
        self.enforce_redaction_check = enforce_redaction_check
        self.call_count = 0
        self.recorded_requests: list[Any] = []

        self.capabilities = RecommenderCapabilities(
            supported_modes=["BOOTSTRAP", "DELTA", "HOLD_RESOLUTION"],
            supported_input_kinds=[
                "profiles",
                "samples",
                "context_documents",
                "unmapped_gaps",
                "deployed_contract",
            ],
            needs_llm=False,
            clearance_level="INTERNAL",
            metadata={"deterministic": True, "provider": "opendv-testkit"},
        )

    def set_fail_mode(self, fail: bool) -> None:
        """Enable or disable simulated inference failure for exception handling tests."""
        self.fail_mode = fail

    def set_custom_proposal(self, proposal: OntologyProposal | None) -> None:
        """Provide an explicit proposal override for fine-grained scenario assertions."""
        self.custom_proposal = proposal

    async def recommend(
        self,
        request: OntologyRecommendationRequest | OntologySuggestionRequest | dict[str, Any],
    ) -> OntologyProposal:
        """Generate a deterministic, additive OntologyProposal given recommendation request."""
        self.call_count += 1
        self.recorded_requests.append(request)

        if self.fail_mode:
            raise RuntimeError("DeterministicTestkitRecommender: simulated inference failure.")

        if self.custom_proposal is not None:
            return self.custom_proposal

        # Normalize request parameters across DTO representations
        mode: str = getattr(request, "mode", None) or "DELTA"
        domain_namespace: str = getattr(request, "domain_namespace", None) or "pe_asset_management"
        base_ontology: Any = getattr(request, "base_ontology", None) or getattr(
            request, "current_ontology", None
        )
        base_ontology_sha256: str | None = getattr(request, "base_ontology_sha256", None)
        focus: Any = getattr(request, "focus", None)
        samples: Any = getattr(request, "samples", None)
        clearance_level: str = getattr(request, "clearance_level", "INTERNAL")

        if isinstance(request, dict):
            mode = request.get("mode", mode)
            domain_namespace = request.get("domain_namespace", domain_namespace)
            base_ontology = request.get("base_ontology", base_ontology)
            base_ontology_sha256 = request.get("base_ontology_sha256", base_ontology_sha256)
            focus = request.get("focus", focus)
            samples = request.get("samples", samples)
            clearance_level = request.get("clearance_level", clearance_level)

        # 1. Enforce RBAC data protection invariant at INTERNAL clearance
        if self.enforce_redaction_check and samples:
            pii_violations = _scan_for_unredacted_pii(samples)
            if pii_violations:
                raise AssertionError(
                    f"Data protection invariant violated: {len(pii_violations)} raw PII "
                    f"tokens detected in sample data at clearance '{clearance_level}': "
                    f"{pii_violations[:3]}"
                )

        now_utc = datetime.datetime.now(datetime.UTC).isoformat()

        # 2. Handle BOOTSTRAP mode: cold-start domain onboarding
        if mode.upper() == "BOOTSTRAP" or base_ontology is None:
            return self._build_bootstrap_proposal(
                domain_namespace=domain_namespace,
                created_at_utc=now_utc,
            )

        # 3. Handle HOLD_RESOLUTION mode: Gate 1 unmapped concept resolution
        if mode.upper() == "HOLD_RESOLUTION":
            return self._build_hold_resolution_proposal(
                domain_namespace=domain_namespace,
                base_ontology=base_ontology,
                base_sha256=base_ontology_sha256,
                focus=focus,
                created_at_utc=now_utc,
            )

        # 4. Handle DELTA mode: advisory evolution recommendations
        return self._build_advisory_delta_proposal(
            domain_namespace=domain_namespace,
            base_ontology=base_ontology,
            base_sha256=base_ontology_sha256,
            focus=focus,
            created_at_utc=now_utc,
        )

    def _build_bootstrap_proposal(
        self,
        domain_namespace: str,
        created_at_utc: str,
    ) -> OntologyProposal:
        """Construct cold-start Graph A v1 proposal with canonical concepts and relationships."""
        if domain_namespace in ("pe_asset_management", "private_equity"):
            ops = [
                AddConceptOp(
                    op_id="op-boot-01",
                    concept_uri="urn:ontology:entity:fund",
                    concept_name="Fund",
                    elm_archetype="WHO_WHAT_INVESTMENT_VEHICLE",
                    definition="A pooled private equity investment vehicle.",
                    target_hub="hub_fund",
                    attributes=["fund_name", "fund_code"],
                ),
                AddConceptOp(
                    op_id="op-boot-02",
                    concept_uri="urn:ontology:entity:portfolio_company",
                    concept_name="PortfolioCompany",
                    elm_archetype="WHO_OPERATING_COMPANY",
                    definition="An operating enterprise receiving fund capital.",
                    target_hub="hub_portfolio_company",
                    attributes=["company_name", "legal_entity_id"],
                ),
                AddRelationshipOp(
                    op_id="op-boot-03",
                    relationship_uri="urn:ontology:relationship:fund_investment",
                    relationship_name="FundInvestment",
                    target_link="lnk_fund_investment",
                    endpoints=["urn:ontology:entity:fund", "urn:ontology:entity:portfolio_company"],
                    grain_semantics="A Fund holds equity investments in a Portfolio Company.",
                ),
                AddReferenceEnumerationOp(
                    op_id="op-boot-04",
                    enum_uri="urn:ontology:enum:investment_stage",
                    name="investment_stage",
                    target_reference_table="ref_investment_stage",
                    allowed_codes=["SEED", "SERIES_A", "GROWTH", "BUYOUT"],
                    values=["SEED", "SERIES_A", "GROWTH", "BUYOUT"],
                    description="Standard private equity investment stage classifications.",
                ),
            ]
        else:
            ops = [
                AddConceptOp(
                    op_id="op-boot-gen-01",
                    concept_uri="urn:ontology:entity:organization",
                    concept_name="Organization",
                    elm_archetype="WHO_LEGAL_ENTITY",
                    definition=f"Primary organizational business entity for {domain_namespace}.",
                    target_hub="hub_organization",
                    attributes=["org_id", "org_name"],
                ),
                AddConceptOp(
                    op_id="op-boot-gen-02",
                    concept_uri="urn:ontology:entity:transaction",
                    concept_name="Transaction",
                    elm_archetype="EVENT_TRANSACTION",
                    definition="Unit of financial or operational activity.",
                    target_hub="hub_transaction",
                    attributes=["tx_id", "amount", "tx_timestamp"],
                ),
                AddRelationshipOp(
                    op_id="op-boot-gen-03",
                    relationship_uri="urn:ontology:relationship:org_transaction",
                    relationship_name="OrgTransaction",
                    target_link="lnk_org_transaction",
                    endpoints=[
                        "urn:ontology:entity:organization",
                        "urn:ontology:entity:transaction",
                    ],
                    grain_semantics="An Organization executes business Transactions.",
                ),
            ]

        delta = OntologyDelta.model_validate(
            {
                "delta_id": "delta-bootstrap-1",
                "domain_namespace": domain_namespace,
                "base_ontology_version": "1.0.0",
                "target_ontology_version": "1.0.0",
                "operations": [op.model_dump(mode="json") for op in ops],
                "metadata": {"mode": "BOOTSTRAP", "author": self.recommender_id},
            },
            context={"allow_bootstrap": True},
        )

        return OntologyProposal(
            proposal_id="prop-bootstrap-1",
            recommender_id=self.recommender_id,
            recommender_version=self.recommender_version,
            delta=delta,
            rationale=f"Cold-start domain bootstrap proposal for namespace '{domain_namespace}'.",
            confidence_score=0.98,
            coverage_before=0.0,
            coverage_after=1.0,
            diagnostics=[f"Generated {len(ops)} bootstrap ontology operations."],
            created_at_utc=created_at_utc,
        )

    def _build_hold_resolution_proposal(
        self,
        domain_namespace: str,
        base_ontology: Any,
        base_sha256: str | None,
        focus: Any,
        created_at_utc: str,
    ) -> OntologyProposal:
        """Construct deterministic hold resolution proposal matching GT-06 or unmapped gaps."""
        focus_str = str(focus or "").lower()
        is_advisor_scenario = (
            "advisor" in focus_str
            or "gt06" in focus_str
            or domain_namespace == "pe_asset_management"
        )
        effective_sha = base_sha256 or (
            "9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02"
            if is_advisor_scenario
            else None
        )

        if is_advisor_scenario:
            ops = [
                AddConceptOp(
                    op_id="op-gt06-01",
                    concept_uri="urn:ontology:entity:advisor",
                    concept_name="Advisor",
                    elm_archetype="WHO_EXTERNAL_ADVISOR",
                    definition="An advisory firm providing deal due diligence and services.",
                    target_hub="hub_advisor",
                    attributes=["advisor_name", "tax_id"],
                    rationale="Deal advisory fees detected in unmapped advisory fee invoices.",
                    confidence=0.95,
                    evidence_refs=[
                        EvidenceRef(
                            evidence_type="column_profile",
                            reference_uri="bronze_advisory_invoices.tax_id",
                            snippet="ADV-9921",
                            confidence=0.95,
                        )
                    ],
                ),
                AddRelationshipOp(
                    op_id="op-gt06-02",
                    relationship_uri="urn:ontology:relationship:fund_advisor",
                    relationship_name="FundAdvisor",
                    target_link="lnk_fund_advisor",
                    endpoints=["urn:ontology:entity:advisor", "urn:ontology:entity:fund"],
                    grain_semantics="A Fund retains external Advisors for due diligence.",
                    rationale="Connect Fund entity with external advisory firm.",
                    confidence=0.92,
                ),
                AddReferenceEnumerationOp(
                    op_id="op-gt06-03",
                    enum_uri="urn:ontology:enum:advisor_role",
                    name="advisor_role",
                    target_reference_table="ref_advisor_role",
                    allowed_codes=["LEGAL", "TAX", "ACCOUNTING", "TECHNICAL_DD"],
                    values=["LEGAL", "TAX", "ACCOUNTING", "TECHNICAL_DD"],
                    description="Advisory engagement service categories.",
                    rationale="Categorize external advisor scope of work.",
                    confidence=0.90,
                ),
                RefineDefinitionOp(
                    op_id="op-gt06-04",
                    target_uri="urn:ontology:entity:fund",
                    concept_uri="urn:ontology:entity:fund",
                    refined_description="Includes co-investment vehicles and syndicates.",
                    additional_attributes=["vintage_year", "target_commitment_usd"],
                    rationale="Clarify vehicle scope under ILPA v2 reporting standard.",
                    confidence=0.98,
                ),
            ]
        else:
            ops = [
                AddConceptOp(
                    op_id="op-hold-res-01",
                    concept_uri="urn:ontology:entity:supplier",
                    concept_name="Supplier",
                    elm_archetype="WHO_SUPPLIER_VENDOR",
                    definition=f"Commercial supplier entity for {domain_namespace}.",
                    target_hub="hub_supplier",
                    attributes=["supplier_id", "supplier_name"],
                    rationale="Gate 1 unmapped feed hold resolution for Supplier.",
                    confidence=0.95,
                )
            ]

        delta = OntologyDelta(
            delta_id="delta-gt06-001" if is_advisor_scenario else f"delta-hold-{self.call_count}",
            parent_sha256=effective_sha,
            domain_namespace=domain_namespace,
            base_ontology_version="1.0.0",
            target_ontology_version="1.1.0",
            operations=ops,
            metadata={"author": self.recommender_id, "benchmark": "PAR-ONT-HOLD"},
        )

        return OntologyProposal(
            proposal_id=(
                "prop-gt06-001" if is_advisor_scenario else f"prop-hold-{self.call_count}"
            ),
            recommender_id=self.recommender_id,
            recommender_version=self.recommender_version,
            base_ontology_sha256=effective_sha or "",
            delta=delta,
            rationale=(
                "Incorporate external deal advisory domain concepts into institutional PE ontology."
                if is_advisor_scenario
                else "Resolve Gate 1 hold via delta."
            ),
            confidence_score=0.94,
            coverage_before=0.8,
            coverage_after=1.0,
            diagnostics=["Resolved unmapped feed bronze_advisory_invoices"],
            created_at_utc=created_at_utc,
        )

    def _build_advisory_delta_proposal(
        self,
        domain_namespace: str,
        base_ontology: Any,
        base_sha256: str | None,
        focus: Any,
        created_at_utc: str,
    ) -> OntologyProposal:
        """Construct advisory evolution delta proposal for IP-2 wave profiling."""
        op_id = f"op-adv-{self.call_count}"
        ops = [
            AddSourceHintOp(
                op_id=op_id,
                concept_uri="urn:ontology:entity:customer",
                source_name="crm_feed",
                source_system="crm",
                table_name="crm_customers",
                column_name="customer_id",
                column_hint="Unique corporate customer business key",
                rationale="Advisory mapping hint discovered from source profiling analysis.",
                confidence=0.88,
            )
        ]

        delta = OntologyDelta(
            delta_id=f"delta-adv-{self.call_count}",
            parent_sha256=base_sha256,
            domain_namespace=domain_namespace,
            base_ontology_version="1.0.0",
            target_ontology_version="1.1.0",
            operations=ops,
            metadata={"mode": "DELTA", "author": self.recommender_id},
        )

        return OntologyProposal(
            proposal_id=f"prop-adv-{self.call_count}",
            recommender_id=self.recommender_id,
            recommender_version=self.recommender_version,
            base_ontology_sha256=base_sha256 or "",
            delta=delta,
            rationale="Advisory column mapping recommendation discovered during profiling.",
            confidence_score=0.88,
            coverage_before=0.85,
            coverage_after=0.95,
            diagnostics=["Advisory proposal attached; existing contract unchanged."],
            created_at_utc=created_at_utc,
        )


__all__ = [
    "DeterministicTestkitRecommender",
]
