"""Multi-Asset / IBOR Vertical Pack."""

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


def get_multi_asset_ibor_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Multi-Asset / Investment Book of Record (IBOR)."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:portfolio",
            concept_name="Portfolio",
            elm_archetype="CORE_CONCEPT",
            definition="Aggregate holding container for investments managed under a mandate.",
            target_hub="hub_portfolio",
            attributes=[
                ConceptAttribute(attribute_name="portfolio_code", data_type="string"),
                ConceptAttribute(attribute_name="portfolio_name", data_type="string"),
                ConceptAttribute(attribute_name="base_currency", data_type="string"),
                ConceptAttribute(attribute_name="strategy", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/SecurityAssets/Portfolio",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["InvestmentPortfolio", "FundPortfolio", "AccountPortfolio"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:position",
            concept_name="Position",
            elm_archetype="CORE_CONCEPT",
            definition="Point-in-time holding balance of an instrument in a portfolio.",
            target_hub="hub_position",
            attributes=[
                ConceptAttribute(attribute_name="position_id", data_type="string"),
                ConceptAttribute(attribute_name="position_date", data_type="string"),
                ConceptAttribute(attribute_name="quantity", data_type="number"),
                ConceptAttribute(attribute_name="market_value", data_type="number"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Position",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["SecurityPosition", "Holding", "PortfolioPosition"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:cash_balance",
            concept_name="CashBalance",
            elm_archetype="CORE_CONCEPT",
            definition="Liquid cash or money market holding in a specific currency.",
            target_hub="hub_cash_balance",
            attributes=[
                ConceptAttribute(attribute_name="balance_id", data_type="string"),
                ConceptAttribute(attribute_name="as_of_date", data_type="string"),
                ConceptAttribute(attribute_name="settled_amount", data_type="number"),
                ConceptAttribute(attribute_name="pending_amount", data_type="number"),
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
            synonyms=["CashPosition", "CurrencyBalance", "LiquidityBalance"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:valuation",
            concept_name="Valuation",
            elm_archetype="CORE_CONCEPT",
            definition="Pricing and mark-to-market valuation observation event.",
            target_hub="hub_valuation",
            attributes=[
                ConceptAttribute(attribute_name="valuation_id", data_type="string"),
                ConceptAttribute(attribute_name="valuation_date", data_type="string"),
                ConceptAttribute(attribute_name="price", data_type="number"),
                ConceptAttribute(attribute_name="pricing_source", data_type="string"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/InstrumentPricing/SecurityPrice",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["PricingEvent", "MarkToMarket", "SecurityPrice"],
        ),
    ]


def get_multi_asset_ibor_relationships() -> list[CanonicalRelationship]:
    """Relationships for Multi-Asset / IBOR."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:portfolio_position",
            relationship_name="PortfolioPosition",
            target_link="lnk_portfolio_position",
            endpoints=["urn:opendv:concept:portfolio", "urn:opendv:concept:position"],
            grain_semantics=(
                "Investment portfolio contains aggregated position holding asset balance records."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:position_instrument",
            relationship_name="PositionInstrument",
            target_link="lnk_position_instrument",
            endpoints=["urn:opendv:concept:position", "urn:opendv:concept:financial_instrument"],
            grain_semantics=(
                "Portfolio position represents balance holding of designated financial instrument."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:portfolio_account",
            relationship_name="PortfolioAccount",
            target_link="lnk_portfolio_account",
            endpoints=["urn:opendv:concept:portfolio", "urn:opendv:concept:account"],
            grain_semantics="Investment portfolio is mapped to master custody accounting account.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:position_valuation",
            relationship_name="PositionValuation",
            target_link="lnk_position_valuation",
            endpoints=["urn:opendv:concept:position", "urn:opendv:concept:valuation"],
            grain_semantics="Position holding valuation is priced via daily market event.",
        ),
    ]


def get_multi_asset_ibor_pack(include_spine: bool = True) -> GraphAOntology:
    """Construct Multi-Asset / IBOR GraphAOntology."""
    concepts = get_multi_asset_ibor_concepts()
    relationships = get_multi_asset_ibor_relationships()
    if include_spine:
        concepts = [*get_core_spine_concepts(), *concepts]
        relationships = [*get_core_spine_relationships(), *relationships]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="multi_asset_ibor",
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:currency_code",
                target_reference_table="ref_currency_code",
                allowed_codes=["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD"],
            ),
        ],
    )


__all__ = [
    "get_multi_asset_ibor_concepts",
    "get_multi_asset_ibor_pack",
    "get_multi_asset_ibor_relationships",
]
