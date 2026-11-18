"""Private Credit / Direct Lending Vertical Pack."""

from __future__ import annotations

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_sources.asset_management.spine import (
    get_core_spine_concepts,
    get_core_spine_relationships,
)


def get_private_credit_direct_lending_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Private Credit / Direct Lending."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:credit_facility",
            concept_name="CreditFacility",
            elm_archetype="CORE_CONCEPT",
            definition="Bilateral or syndicated loan facility or credit agreement.",
            target_hub="hub_credit_facility",
            attributes=[
                ConceptAttribute(attribute_name="facility_id", data_type="string"),
                ConceptAttribute(attribute_name="facility_type", data_type="string"),
                ConceptAttribute(attribute_name="total_commitment", data_type="number"),
                ConceptAttribute(attribute_name="drawn_amount", data_type="number"),
                ConceptAttribute(attribute_name="maturity_date", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/CreditFacility",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["LoanFacility", "TermLoan", "Revolver"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:borrower",
            concept_name="Borrower",
            elm_archetype="CORE_CONCEPT",
            definition="Corporate debtor or sponsor company incurring credit liability.",
            target_hub="hub_borrower",
            attributes=[
                ConceptAttribute(attribute_name="borrower_id", data_type="string"),
                ConceptAttribute(attribute_name="borrower_name", data_type="string"),
                ConceptAttribute(attribute_name="industry_sector", data_type="string"),
                ConceptAttribute(attribute_name="credit_rating", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Debtor", "LoanBorrower", "CreditObligor"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:collateral",
            concept_name="Collateral",
            elm_archetype="CORE_CONCEPT",
            definition="Asset or property pledged by borrower as security for debt obligations.",
            target_hub="hub_collateral",
            attributes=[
                ConceptAttribute(attribute_name="collateral_id", data_type="string"),
                ConceptAttribute(attribute_name="collateral_type", data_type="string"),
                ConceptAttribute(attribute_name="appraised_value", data_type="number"),
                ConceptAttribute(attribute_name="lien_position", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/Collateral",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["PledgedAsset", "SecurityInterest", "LoanCollateral"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:covenant",
            concept_name="Covenant",
            elm_archetype="CORE_CONCEPT",
            definition="Financial or operational compliance condition in loan agreement.",
            target_hub="hub_covenant",
            attributes=[
                ConceptAttribute(attribute_name="covenant_id", data_type="string"),
                ConceptAttribute(attribute_name="covenant_type", data_type="string"),
                ConceptAttribute(attribute_name="threshold_value", data_type="number"),
                ConceptAttribute(attribute_name="test_frequency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["LoanCovenant", "MaintenanceCovenant", "FinancialCovenant"],
        ),
    ]


def get_private_credit_direct_lending_relationships() -> list[CanonicalRelationship]:
    """Relationships for Private Credit / Direct Lending."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:facility_borrower",
            relationship_name="FacilityBorrower",
            target_link="lnk_facility_borrower",
            endpoints=["urn:opendv:concept:credit_facility", "urn:opendv:concept:borrower"],
            grain_semantics=(
                "Corporate borrower entity executes syndicated debt credit facility agreement."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:facility_collateral",
            relationship_name="FacilityCollateral",
            target_link="lnk_facility_collateral",
            endpoints=["urn:opendv:concept:credit_facility", "urn:opendv:concept:collateral"],
            grain_semantics="Credit facility is secured by pledged collateral asset.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:facility_covenant",
            relationship_name="FacilityCovenant",
            target_link="lnk_facility_covenant",
            endpoints=["urn:opendv:concept:credit_facility", "urn:opendv:concept:covenant"],
            grain_semantics="Credit facility is governed by financial covenant terms.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:facility_agreement",
            relationship_name="FacilityAgreement",
            target_link="lnk_facility_agreement",
            endpoints=["urn:opendv:concept:credit_facility", "urn:opendv:concept:agreement"],
            grain_semantics="Credit facility references master credit agreement spine concept.",
        ),
    ]


def get_private_credit_direct_lending_pack(include_spine: bool = True) -> GraphAOntology:
    """Construct Private Credit / Direct Lending GraphAOntology."""
    concepts = get_private_credit_direct_lending_concepts()
    relationships = get_private_credit_direct_lending_relationships()
    if include_spine:
        concepts = [*get_core_spine_concepts(), *concepts]
        relationships = [*get_core_spine_relationships(), *relationships]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="private_credit_direct_lending",
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:covenant_type",
                target_reference_table="ref_covenant_type",
                allowed_codes=[
                    "MAX_LEVERAGE",
                    "MIN_INTEREST_COVERAGE",
                    "MIN_LIQUIDITY",
                    "REPORTING_DELIVERY",
                ],
            ),
        ],
    )


__all__ = [
    "get_private_credit_direct_lending_concepts",
    "get_private_credit_direct_lending_pack",
    "get_private_credit_direct_lending_relationships",
]
