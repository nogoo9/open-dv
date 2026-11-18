"""Custody / Clearing Vertical Pack."""

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


def get_custody_clearing_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Custody / Clearing."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:custody_account",
            concept_name="CustodyAccount",
            elm_archetype="CORE_CONCEPT",
            definition="Securities safekeeping account maintained at a custodian bank.",
            target_hub="hub_custody_account",
            attributes=[
                ConceptAttribute(attribute_name="account_number", data_type="string"),
                ConceptAttribute(attribute_name="account_name", data_type="string"),
                ConceptAttribute(attribute_name="sub_custodian", data_type="string"),
                ConceptAttribute(attribute_name="country_code", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/SecuritiesIdentification/CustodyAccount",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["SafekeepingAccount", "DepositoryAccount", "CustodialAccount"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:custodian",
            concept_name="Custodian",
            elm_archetype="CORE_CONCEPT",
            definition="Financial institution holding securities in safekeeping for clients.",
            target_hub="hub_custodian",
            attributes=[
                ConceptAttribute(attribute_name="custodian_code", data_type="string"),
                ConceptAttribute(attribute_name="custodian_name", data_type="string"),
                ConceptAttribute(attribute_name="bic_code", data_type="string"),
                ConceptAttribute(attribute_name="rating", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/Custodian",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["CustodianBank", "GlobalCustodian", "SubCustodian"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:settlement_instruction",
            concept_name="SettlementInstruction",
            elm_archetype="CORE_CONCEPT",
            definition="Standing settlement instruction (SSI) or trade delivery notification.",
            target_hub="hub_settlement_instruction",
            attributes=[
                ConceptAttribute(attribute_name="instruction_id", data_type="string"),
                ConceptAttribute(attribute_name="settlement_date", data_type="string"),
                ConceptAttribute(attribute_name="settlement_type", data_type="string"),
                ConceptAttribute(attribute_name="settlement_status", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Settlement/Settlement",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["SSI", "SettlementMessage", "DeliveryInstruction"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:corporate_action",
            concept_name="CorporateAction",
            elm_archetype="CORE_CONCEPT",
            definition="Event initiated by security issuer affecting underlying asset holdings.",
            target_hub="hub_corporate_action",
            attributes=[
                ConceptAttribute(attribute_name="action_id", data_type="string"),
                ConceptAttribute(attribute_name="action_type", data_type="string"),
                ConceptAttribute(attribute_name="record_date", data_type="string"),
                ConceptAttribute(attribute_name="payable_date", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/SecuritiesIdentification/SecurityRegistry",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["CorpAction", "DividendEvent", "Reorganization"],
        ),
    ]


def get_custody_clearing_relationships() -> list[CanonicalRelationship]:
    """Relationships for Custody / Clearing."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:custody_account_holder",
            relationship_name="CustodyAccountHolder",
            target_link="lnk_custody_account_holder",
            endpoints=["urn:opendv:concept:custody_account", "urn:opendv:concept:legal_entity"],
            grain_semantics=(
                "Legal entity officially holds registered institutional securities custody account."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:custody_account_custodian",
            relationship_name="CustodyAccountCustodian",
            target_link="lnk_custody_account_custodian",
            endpoints=["urn:opendv:concept:custody_account", "urn:opendv:concept:custodian"],
            grain_semantics=(
                "Appointed custodian bank actively administers institutional "
                "securities custody account."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:settlement_custody",
            relationship_name="SettlementCustody",
            target_link="lnk_settlement_custody",
            endpoints=[
                "urn:opendv:concept:settlement_instruction",
                "urn:opendv:concept:custody_account",
            ],
            grain_semantics="Settlement instruction delivers to or receives from custody account.",
        ),
    ]


def get_custody_clearing_pack(include_spine: bool = True) -> GraphAOntology:
    """Construct Custody / Clearing GraphAOntology."""
    concepts = get_custody_clearing_concepts()
    relationships = get_custody_clearing_relationships()
    if include_spine:
        concepts = [*get_core_spine_concepts(), *concepts]
        relationships = [*get_core_spine_relationships(), *relationships]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="custody_clearing",
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:settlement_status",
                target_reference_table="ref_settlement_status",
                allowed_codes=["PENDING", "SETTLED", "FAILED", "CANCELLED"],
            ),
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:corporate_action_type",
                target_reference_table="ref_corporate_action_type",
                allowed_codes=[
                    "CASH_DIVIDEND",
                    "STOCK_DIVIDEND",
                    "STOCK_SPLIT",
                    "MERGER",
                    "SPINOFF",
                ],
            ),
        ],
    )


__all__ = [
    "get_custody_clearing_concepts",
    "get_custody_clearing_pack",
    "get_custody_clearing_relationships",
]
