"""Unified Core Enterprise Spine for Asset Management.

Provides universal enterprise invariants: LegalEntity, Account, Agreement,
FinancialInstrument, Transaction, and Document, along with core Unit-of-Work links.
"""

from __future__ import annotations

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
)


def get_core_spine_concepts() -> list[CanonicalBusinessConcept]:
    """Return the 6 canonical business concepts forming the core spine."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:legal_entity",
            concept_name="LegalEntity",
            elm_archetype="WHO_LEGAL_ENTITY",
            definition="Autonomous legal person, corporation, partnership, or sovereign entity.",
            target_hub="hub_legal_entity",
            attributes=[
                ConceptAttribute(attribute_name="legal_name", data_type="string"),
                ConceptAttribute(attribute_name="jurisdiction", data_type="string"),
                ConceptAttribute(attribute_name="lei", data_type="string"),
                ConceptAttribute(attribute_name="tax_id", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Counterparty", "Company", "Party", "Entity"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:account",
            concept_name="Account",
            elm_archetype="WHAT_ACCOUNT",
            definition="Client, customer, or internal investment account holding asset balances.",
            target_hub="hub_account",
            attributes=[
                ConceptAttribute(attribute_name="account_number", data_type="string"),
                ConceptAttribute(attribute_name="account_name", data_type="string"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
                ConceptAttribute(attribute_name="account_status", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/CustomerAccount",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["CustomerAccount", "ClientAccount", "LedgerAccount"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:agreement",
            concept_name="Agreement",
            elm_archetype="WHAT_AGREEMENT",
            definition="Binding bilateral or multilateral contract, mandate, or treaty.",
            target_hub="hub_agreement",
            attributes=[
                ConceptAttribute(attribute_name="agreement_reference", data_type="string"),
                ConceptAttribute(attribute_name="agreement_type", data_type="string"),
                ConceptAttribute(attribute_name="effective_date", data_type="string"),
                ConceptAttribute(attribute_name="governing_law", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Contract", "MasterAgreement", "MandateAgreement"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:financial_instrument",
            concept_name="FinancialInstrument",
            elm_archetype="WHAT_FINANCIAL_INSTRUMENT",
            definition="Tradable or non-tradable asset or contract holding economic value.",
            target_hub="hub_financial_instrument",
            attributes=[
                ConceptAttribute(attribute_name="instrument_id", data_type="string"),
                ConceptAttribute(attribute_name="isin", data_type="string"),
                ConceptAttribute(attribute_name="cusip", data_type="string"),
                ConceptAttribute(attribute_name="sedol", data_type="string"),
                ConceptAttribute(attribute_name="instrument_name", data_type="string"),
                ConceptAttribute(attribute_name="asset_class", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/FinancialInstrument",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Security", "Asset", "Instrument"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:transaction",
            concept_name="Transaction",
            elm_archetype="EVENT_FINANCIAL_TRANSACTION",
            definition="Executed trade, cash payment, transfer, or accounting movement event.",
            target_hub="hub_transaction",
            attributes=[
                ConceptAttribute(attribute_name="transaction_id", data_type="string"),
                ConceptAttribute(attribute_name="transaction_date", data_type="string"),
                ConceptAttribute(attribute_name="quantity", data_type="number"),
                ConceptAttribute(attribute_name="amount", data_type="number"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Trade",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Trade", "Deal", "FinancialTransaction"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:document",
            concept_name="Document",
            elm_archetype="RECORD_DOCUMENT",
            definition="Structured or unstructured digital or physical document artifact.",
            target_hub="hub_document",
            attributes=[
                ConceptAttribute(attribute_name="document_id", data_type="string"),
                ConceptAttribute(attribute_name="file_name", data_type="string"),
                ConceptAttribute(attribute_name="sha256", data_type="string"),
                ConceptAttribute(attribute_name="mime_type", data_type="string"),
                ConceptAttribute(attribute_name="byte_size", data_type="number"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Documents/Document",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["File", "Artifact", "Documentation"],
        ),
    ]


def get_core_spine_relationships() -> list[CanonicalRelationship]:
    """Return the 6 foundational Unit-of-Work link relationships of the spine."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:account_holder",
            relationship_name="AccountHolder",
            target_link="lnk_account_holder",
            endpoints=["urn:opendv:concept:account", "urn:opendv:concept:legal_entity"],
            grain_semantics="Legal entity officially holds or owns customer portfolio account.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:contract_party",
            relationship_name="ContractParty",
            target_link="lnk_contract_party",
            endpoints=["urn:opendv:concept:agreement", "urn:opendv:concept:legal_entity"],
            grain_semantics="Legal entity participates as official contracting party to agreement.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:account_agreement",
            relationship_name="AccountAgreement",
            target_link="lnk_account_agreement",
            endpoints=["urn:opendv:concept:account", "urn:opendv:concept:agreement"],
            grain_semantics="Customer account is formally governed by legal agreement contract.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:transaction_instrument",
            relationship_name="TransactionInstrument",
            target_link="lnk_transaction_instrument",
            endpoints=[
                "urn:opendv:concept:transaction",
                "urn:opendv:concept:financial_instrument",
            ],
            grain_semantics=(
                "Financial transaction executes trade allocation for designated "
                "financial instrument."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:transaction_account",
            relationship_name="TransactionAccount",
            target_link="lnk_transaction_account",
            endpoints=["urn:opendv:concept:transaction", "urn:opendv:concept:account"],
            grain_semantics=(
                "Financial transaction movement debits or credits designated client account."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:agreement_document",
            relationship_name="AgreementDocument",
            target_link="lnk_agreement_document",
            endpoints=["urn:opendv:concept:agreement", "urn:opendv:concept:document"],
            grain_semantics="Agreement is formally evidenced or recorded by executed document.",
        ),
    ]


def get_core_spine_pack() -> GraphAOntology:
    """Construct the Core Spine GraphAOntology instance."""
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="core_spine",
        canonical_business_concepts=get_core_spine_concepts(),
        canonical_relationships=get_core_spine_relationships(),
        reference_enumerations=[],
    )


__all__ = [
    "get_core_spine_concepts",
    "get_core_spine_pack",
    "get_core_spine_relationships",
]
