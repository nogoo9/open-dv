"""Private Equity / Buyout Vertical Pack."""

from __future__ import annotations

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
)
from opendv_sources.asset_management.spine import (
    get_core_spine_concepts,
    get_core_spine_relationships,
)


def get_private_equity_buyout_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Private Equity / Buyout."""
    hub_pfx = "hub_"
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:fund",
            concept_name="Fund",
            elm_archetype="CORE_CONCEPT",
            definition="Closed-end private equity partnership or fund investment vehicle.",
            target_hub=f"{hub_pfx}fund",
            attributes=[
                ConceptAttribute(attribute_name="fund_code", data_type="string"),
                ConceptAttribute(attribute_name="fund_name", data_type="string"),
                ConceptAttribute(attribute_name="vintage_year", data_type="number"),
                ConceptAttribute(attribute_name="target_size", data_type="number"),
                ConceptAttribute(attribute_name="fund_currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/PrivateEquityFund",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["PEFund", "PartnershipVehicle", "InvestmentFund"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:limited_partner",
            concept_name="LimitedPartner",
            elm_archetype="CORE_CONCEPT",
            definition="Institutional or high-net-worth investor committing fund capital.",
            target_hub=f"{hub_pfx}limited_partner",
            attributes=[
                ConceptAttribute(attribute_name="lp_code", data_type="string"),
                ConceptAttribute(attribute_name="lp_name", data_type="string"),
                ConceptAttribute(attribute_name="lp_type", data_type="string"),
                ConceptAttribute(attribute_name="tax_domicile", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["LP", "FundInvestor", "LimitedPartnerInvestor"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:capital_call",
            concept_name="CapitalCall",
            elm_archetype="CORE_CONCEPT",
            definition="Capital drawdown notice issued to fund limited partners.",
            target_hub="hub_capital_call",
            attributes=[
                ConceptAttribute(attribute_name="call_id", data_type="string"),
                ConceptAttribute(attribute_name="call_date", data_type="string"),
                ConceptAttribute(attribute_name="due_date", data_type="string"),
                ConceptAttribute(attribute_name="called_amount", data_type="number"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Accounting/CurrencyAmount/Funds",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["DrawdownNotice", "CapitalDrawdown", "SubscriptionCall"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:distribution",
            concept_name="Distribution",
            elm_archetype="CORE_CONCEPT",
            definition="Cash or in-kind capital return distributed by fund to investors.",
            target_hub="hub_distribution",
            attributes=[
                ConceptAttribute(attribute_name="distribution_id", data_type="string"),
                ConceptAttribute(attribute_name="dist_date", data_type="string"),
                ConceptAttribute(attribute_name="distribution_type", data_type="string"),
                ConceptAttribute(attribute_name="dist_amount", data_type="number"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/FundCashDistributionPolicy",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["FundDistribution", "CapitalReturn", "DividendDistribution"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:portfolio_company",
            concept_name="PortfolioCompany",
            elm_archetype="CORE_CONCEPT",
            definition="Operating enterprise acquired or invested in by a private equity fund.",
            target_hub="hub_portfolio_company",
            attributes=[
                ConceptAttribute(attribute_name="company_id", data_type="string"),
                ConceptAttribute(attribute_name="company_name", data_type="string"),
                ConceptAttribute(attribute_name="industry_sector", data_type="string"),
                ConceptAttribute(attribute_name="acquisition_date", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/BusinessEntity",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Portco", "OperatingCompany", "TargetCompany"],
        ),
    ]


def get_private_equity_buyout_relationships() -> list[CanonicalRelationship]:
    """Relationships for Private Equity / Buyout."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:fund_lp",
            relationship_name="FundLP",
            target_link="lnk_fund_lp",
            endpoints=["urn:opendv:concept:fund", "urn:opendv:concept:limited_partner"],
            grain_semantics="Limited partner commits capital to private equity fund.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:capital_call_fund",
            relationship_name="CapitalCallFund",
            target_link="lnk_capital_call_fund",
            endpoints=["urn:opendv:concept:capital_call", "urn:opendv:concept:fund"],
            grain_semantics="Investment fund issues capital drawdown call notice to partners.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:distribution_fund",
            relationship_name="DistributionFund",
            target_link="lnk_distribution_fund",
            endpoints=["urn:opendv:concept:distribution", "urn:opendv:concept:fund"],
            grain_semantics="Fund issues cash or stock distribution to partners.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:fund_investment",
            relationship_name="FundInvestment",
            target_link="lnk_fund_investment",
            endpoints=["urn:opendv:concept:fund", "urn:opendv:concept:portfolio_company"],
            grain_semantics="Fund holds equity investment in operating portfolio company.",
        ),
    ]


def get_private_equity_buyout_pack(include_spine: bool = True) -> GraphAOntology:
    """Construct Private Equity / Buyout GraphAOntology."""
    concepts = get_private_equity_buyout_concepts()
    relationships = get_private_equity_buyout_relationships()
    if include_spine:
        concepts = [*get_core_spine_concepts(), *concepts]
        relationships = [*get_core_spine_relationships(), *relationships]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="private_equity_buyout",
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=[],
    )


__all__ = [
    "get_private_equity_buyout_concepts",
    "get_private_equity_buyout_pack",
    "get_private_equity_buyout_relationships",
]
