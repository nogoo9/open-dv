"""V2 Unified Core Enterprise Spine for Asset Management starter packs."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    KeyAuthorityPattern,
    OntologyRelationship,
)
from opendv.models.vault_ir import DecimalType, PortableLogicalType
from opendv.sources.fibo.loader import CANONICAL_FIBO_CORE_SHA256

_DEC_18_4 = DecimalType(kind="DECIMAL", precision=18, scale=4)

_TYPE_HINT_MAP: dict[str, tuple[PortableLogicalType, str]] = {
    "string": ("UTF8_STRING", "VARCHAR"),
    "number": (_DEC_18_4, "DECIMAL(18,4)"),
    "int": ("INT32", "INTEGER"),
    "date": ("DATE32", "DATE"),
    "timestamp": ("TIMESTAMP_UTC_US", "TIMESTAMP"),
}


def make_attr(
    name: str,
    kind: str = "string",
    *,
    is_bk: bool = False,
    nullable: bool | None = None,
    is_pii: bool = False,
    pii_category: str | None = None,
    enum_uri: str | None = None,
) -> ConceptAttribute:
    """Build a strict V2 ConceptAttribute from concise spec."""
    logical_type, sql_hint = _TYPE_HINT_MAP[kind]
    eff_nullable = (not is_bk) if nullable is None else nullable
    return ConceptAttribute(
        name=name,
        logical_type=logical_type,
        sql_type_hint=sql_hint,
        nullable=eff_nullable,
        is_business_key=is_bk,
        is_pii=is_pii,
        pii_category=pii_category,
        enum_uri=enum_uri,
    )


def make_fibo_mapping(
    uri: str,
    *,
    mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] = "skos:exactMatch",
    confidence: Decimal = Decimal("1.0000"),
) -> ExternalMapping:
    """Build a V2 ExternalMapping anchored to the canonical FIBO core pack."""
    return ExternalMapping(
        standard_uri=uri,
        reference_pack_id="fibo-core",
        reference_pack_sha256=CANONICAL_FIBO_CORE_SHA256,
        mapping_type=mapping_type,
        confidence=confidence,
    )


def make_concept(
    slug: str,
    name: str,
    description: str,
    fibo_uri: str,
    attrs: list[ConceptAttribute],
    synonyms: list[str],
    *,
    tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR",
    bkcc: str | None = "ENTERPRISE_SOR",
    patterns: list[KeyAuthorityPattern] | None = None,
    parent_uri: str | None = None,
    mapping_type: Literal["skos:exactMatch", "skos:closeMatch"] = "skos:exactMatch",
    mapping_confidence: Decimal = Decimal("1.0000"),
) -> CanonicalBusinessConcept:
    """Build a strict V2 CanonicalBusinessConcept planned stub."""
    return CanonicalBusinessConcept(
        concept_uri=f"urn:opendv:concept:{slug}",
        concept_name=name,
        description=description,
        parent_concept_uri=parent_uri,
        key_authority_tier=tier,
        key_authority_patterns=patterns or [],
        bkcc_default=bkcc,
        governance_tags=["planned_stub"],
        attributes=attrs,
        synonyms=synonyms,
        external_mappings=[
            make_fibo_mapping(
                fibo_uri,
                mapping_type=mapping_type,
                confidence=mapping_confidence,
            )
        ],
    )


def make_rel(
    slug: str,
    name: str,
    target_link: str,
    source_slug: str,
    target_slug: str,
    grain_semantics: str,
    *,
    cardinality: str = "MANY_TO_MANY",
) -> OntologyRelationship:
    """Build a strict V2 OntologyRelationship."""
    return OntologyRelationship(
        relationship_uri=f"urn:opendv:relationship:{slug}",
        relationship_name=name,
        source_concept_uri=f"urn:opendv:concept:{source_slug}",
        target_concept_uri=f"urn:opendv:concept:{target_slug}",
        cardinality=cardinality,  # type: ignore[arg-type]
        target_link=target_link,
        grain_semantics=grain_semantics,
        description=grain_semantics,
    )


def get_core_spine_concepts() -> list[CanonicalBusinessConcept]:
    """Return the 6 canonical business concepts forming the V2 core spine."""
    fibo = "https://spec.edmcouncil.org/fibo/ontology/"
    return [
        make_concept(
            "legal_entity",
            "LegalEntity",
            "Autonomous legal person, corporation, partnership, or sovereign entity.",
            f"{fibo}BE/LegalEntities/LegalPersons/LegalEntity",
            [
                make_attr("lei", "string", is_bk=True),
                make_attr("legal_name", "string"),
                make_attr("jurisdiction", "string"),
                make_attr("tax_id", "string", is_pii=True, pii_category="TAX_ID"),
            ],
            ["Counterparty", "Company", "Party", "Entity"],
            tier="TIER_1_GLOBAL_STANDARD",
            bkcc="GLEIF_LEI",
            patterns=[
                KeyAuthorityPattern(
                    tier="TIER_1_GLOBAL_STANDARD",
                    authority_name="ISO 17442 LEI",
                    column_patterns=["lei", "lei_code", "cik"],
                    bkcc_policy="GLOBAL_STANDARD_PREFERRED",
                )
            ],
        ),
        make_concept(
            "account",
            "Account",
            "Client, customer, or internal investment account holding asset balances.",
            f"{fibo}FBC/ProductsAndServices/ClientsAndAccounts/CustomerAccount",
            [
                make_attr("account_number", "string", is_bk=True),
                make_attr("account_name", "string"),
                make_attr("currency", "string"),
                make_attr("account_status", "string"),
            ],
            ["CustomerAccount", "ClientAccount", "LedgerAccount"],
            tier="TIER_3_LOCAL_SCOPED",
            bkcc="SOURCE_SYSTEM_SCOPED",
            patterns=[
                KeyAuthorityPattern(
                    tier="TIER_3_LOCAL_SCOPED",
                    authority_name="Local Source Account ID",
                    column_patterns=["account_id", "account_number", "acct_id"],
                    bkcc_policy="MANDATORY_SOURCE_BKCC",
                )
            ],
        ),
        make_concept(
            "agreement",
            "Agreement",
            "Binding bilateral or multilateral contract, mandate, or treaty.",
            f"{fibo}FND/Agreements/Contracts/Contract",
            [
                make_attr("agreement_reference", "string", is_bk=True),
                make_attr("agreement_type", "string"),
                make_attr("effective_date", "date"),
                make_attr("governing_law", "string"),
            ],
            ["Contract", "MasterAgreement", "MandateAgreement"],
        ),
        make_concept(
            "financial_instrument",
            "FinancialInstrument",
            "Tradable or non-tradable asset or contract holding economic value.",
            f"{fibo}FBC/FinancialInstruments/FinancialInstruments/FinancialInstrument",
            [
                make_attr("instrument_id", "string", is_bk=True),
                make_attr("isin", "string"),
                make_attr("cusip", "string"),
                make_attr("sedol", "string"),
                make_attr("instrument_name", "string"),
                make_attr("asset_class", "string"),
            ],
            ["Security", "Asset", "Instrument"],
            tier="TIER_1_GLOBAL_STANDARD",
            bkcc="ISO_6166_ISIN",
            patterns=[
                KeyAuthorityPattern(
                    tier="TIER_1_GLOBAL_STANDARD",
                    authority_name="ISO 6166 ISIN / CUSIP",
                    column_patterns=["isin", "cusip", "sedol", "instrument_id"],
                    bkcc_policy="GLOBAL_STANDARD_PREFERRED",
                )
            ],
        ),
        make_concept(
            "transaction",
            "Transaction",
            "Executed trade, cash payment, transfer, or accounting movement event.",
            f"{fibo}FBC/ProductsAndServices/FinancialProductsAndServices/Trade",
            [
                make_attr("transaction_id", "string", is_bk=True),
                make_attr("transaction_date", "date"),
                make_attr("quantity", "number"),
                make_attr("amount", "number"),
                make_attr("currency", "string"),
            ],
            ["Trade", "Deal", "FinancialTransaction"],
        ),
        make_concept(
            "document",
            "Document",
            "Structured or unstructured digital or physical document artifact.",
            f"{fibo}FND/Agreements/Contracts/ContractDocument",
            [
                make_attr("document_id", "string", is_bk=True),
                make_attr("file_name", "string"),
                make_attr("sha256", "string"),
                make_attr("mime_type", "string"),
                make_attr("byte_size", "int"),
            ],
            ["File", "Artifact", "Documentation"],
            mapping_type="skos:closeMatch",
            mapping_confidence=Decimal("0.8500"),
        ),
    ]


def get_core_spine_relationships() -> list[OntologyRelationship]:
    """Return the 6 foundational Unit-of-Work link relationships of the spine."""
    return [
        make_rel(
            "account_holder",
            "AccountHolder",
            "lnk_account_holder",
            "account",
            "legal_entity",
            "Legal entity officially holds or owns customer portfolio account.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "contract_party",
            "ContractParty",
            "lnk_contract_party",
            "agreement",
            "legal_entity",
            "Legal entity participates as official contracting party to agreement.",
        ),
        make_rel(
            "account_agreement",
            "AccountAgreement",
            "lnk_account_agreement",
            "account",
            "agreement",
            "Customer account is formally governed by legal agreement contract.",
        ),
        make_rel(
            "transaction_instrument",
            "TransactionInstrument",
            "lnk_transaction_instrument",
            "transaction",
            "financial_instrument",
            "Financial transaction executes trade allocation for financial instrument.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "transaction_account",
            "TransactionAccount",
            "lnk_transaction_account",
            "transaction",
            "account",
            "Financial transaction movement debits or credits designated client account.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "agreement_document",
            "AgreementDocument",
            "lnk_agreement_document",
            "agreement",
            "document",
            "Agreement is formally evidenced or recorded by executed document.",
        ),
    ]


def get_core_spine_pack() -> GraphAOntology:
    """Construct the Core Spine V2 GraphAOntology instance."""
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="core_spine",
        concepts=get_core_spine_concepts(),
        relationships=get_core_spine_relationships(),
        enumerations=[],
    )


__all__ = [
    "get_core_spine_concepts",
    "get_core_spine_pack",
    "get_core_spine_relationships",
    "make_attr",
    "make_concept",
    "make_fibo_mapping",
    "make_rel",
]
