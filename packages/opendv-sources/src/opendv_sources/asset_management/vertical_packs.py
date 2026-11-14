"""Vertical Starter Packs for Asset Management domains.

Defines the 6 specialized composable packs:
1. Public Equities / OMS (public_equities_oms)
2. Fixed Income / Rates (fixed_income_rates)
3. Multi-Asset / IBOR (multi_asset_ibor)
4. Private Equity / Buyout (private_equity_buyout)
5. Private Credit / Direct Lending (private_credit_direct_lending)
6. Custody / Clearing (custody_clearing)
"""

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

# ---------------------------------------------------------------------------
# Pack 1: Public Equities / OMS
# ---------------------------------------------------------------------------


def get_public_equities_oms_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Public Equities / Order Management System."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:order",
            concept_name="Order",
            elm_archetype="CORE_CONCEPT",
            definition="Client or portfolio manager order to buy or sell securities.",
            target_hub="hub_order",
            attributes=[
                ConceptAttribute(attribute_name="order_id", data_type="string"),
                ConceptAttribute(attribute_name="order_type", data_type="string"),
                ConceptAttribute(attribute_name="side", data_type="string"),
                ConceptAttribute(attribute_name="quantity", data_type="number"),
                ConceptAttribute(attribute_name="limit_price", data_type="number"),
                ConceptAttribute(attribute_name="order_status", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/TradeLifecycleStage",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["TradeOrder", "ClientOrder", "OMSOrder"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:execution",
            concept_name="Execution",
            elm_archetype="CORE_CONCEPT",
            definition="Trade execution or partial fill returned by a broker or market center.",
            target_hub="hub_execution",
            attributes=[
                ConceptAttribute(attribute_name="execution_id", data_type="string"),
                ConceptAttribute(attribute_name="exec_price", data_type="number"),
                ConceptAttribute(attribute_name="exec_quantity", data_type="number"),
                ConceptAttribute(attribute_name="exec_time", data_type="string"),
                ConceptAttribute(attribute_name="liquidity_indicator", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Trade",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["Fill", "TradeFill", "ExecutionReport"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:broker",
            concept_name="Broker",
            elm_archetype="CORE_CONCEPT",
            definition="Broker-dealer firm providing trade execution, clearing, or liquidity.",
            target_hub="hub_broker",
            attributes=[
                ConceptAttribute(attribute_name="broker_code", data_type="string"),
                ConceptAttribute(attribute_name="broker_name", data_type="string"),
                ConceptAttribute(attribute_name="mpid", data_type="string"),
                ConceptAttribute(attribute_name="rating", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/Broker",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["ExecutingBroker", "BrokerDealer", "CounterpartyBroker"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:exchange",
            concept_name="Exchange",
            elm_archetype="CORE_CONCEPT",
            definition="Regulated financial exchange, ATS, or Multilateral Trading Facility.",
            target_hub="hub_exchange",
            attributes=[
                ConceptAttribute(attribute_name="mic_code", data_type="string"),
                ConceptAttribute(attribute_name="exchange_name", data_type="string"),
                ConceptAttribute(attribute_name="operating_mic", data_type="string"),
                ConceptAttribute(attribute_name="country_code", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/SecuritiesIssuance/Exchange",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["MarketCenter", "TradingVenue", "ExecutionVenue"],
        ),
    ]


def get_public_equities_oms_relationships() -> list[CanonicalRelationship]:
    """Relationships for Public Equities / OMS."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:order_execution",
            relationship_name="OrderExecution",
            target_link="lnk_order_execution",
            endpoints=["urn:opendv:concept:order", "urn:opendv:concept:execution"],
            grain_semantics="Order is filled through one or more executions.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:order_instrument",
            relationship_name="OrderInstrument",
            target_link="lnk_order_instrument",
            endpoints=["urn:opendv:concept:order", "urn:opendv:concept:financial_instrument"],
            grain_semantics="Order targets a specific financial instrument.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:order_broker",
            relationship_name="OrderBroker",
            target_link="lnk_order_broker",
            endpoints=["urn:opendv:concept:order", "urn:opendv:concept:broker"],
            grain_semantics="Order is routed to broker for execution.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:execution_exchange",
            relationship_name="ExecutionExchange",
            target_link="lnk_execution_exchange",
            endpoints=["urn:opendv:concept:execution", "urn:opendv:concept:exchange"],
            grain_semantics="Execution occurred at specific exchange venue.",
        ),
    ]


def get_public_equities_oms_pack(include_spine: bool = True) -> GraphAOntology:
    """Construct Public Equities / OMS GraphAOntology."""
    concepts = get_public_equities_oms_concepts()
    relationships = get_public_equities_oms_relationships()
    if include_spine:
        concepts = [*get_core_spine_concepts(), *concepts]
        relationships = [*get_core_spine_relationships(), *relationships]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="public_equities_oms",
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:order_side",
                target_reference_table="ref_order_side",
                allowed_codes=["BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"],
            ),
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:execution_type",
                target_reference_table="ref_execution_type",
                allowed_codes=["FILL", "PARTIAL_FILL", "CANCEL", "REJECT"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Pack 2: Fixed Income / Rates
# ---------------------------------------------------------------------------


def get_fixed_income_rates_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Fixed Income / Rates."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:bond",
            concept_name="Bond",
            elm_archetype="CORE_CONCEPT",
            definition="Debt security requiring issuer to repay principal and interest.",
            target_hub="hub_bond",
            parent_concept_uri="urn:opendv:concept:financial_instrument",
            attributes=[
                ConceptAttribute(attribute_name="bond_id", data_type="string"),
                ConceptAttribute(attribute_name="coupon_rate", data_type="number"),
                ConceptAttribute(attribute_name="maturity_date", data_type="string"),
                ConceptAttribute(attribute_name="day_count_convention", data_type="string"),
                ConceptAttribute(attribute_name="seniority", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["FixedIncomeSecurity", "DebtSecurity", "Note"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:yield_curve",
            concept_name="YieldCurve",
            elm_archetype="CORE_CONCEPT",
            definition="Term structure of interest rates across tenors for a given credit curve.",
            target_hub="hub_yield_curve",
            attributes=[
                ConceptAttribute(attribute_name="curve_id", data_type="string"),
                ConceptAttribute(attribute_name="curve_name", data_type="string"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
                ConceptAttribute(attribute_name="curve_type", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/IND/Indicators/Indicators/TermStructure",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["TermStructure", "InterestRateCurve", "ZeroCurve"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:coupon_payment",
            concept_name="CouponPayment",
            elm_archetype="CORE_CONCEPT",
            definition="Periodic interest cashflow distribution on debt securities.",
            target_hub="hub_coupon_payment",
            attributes=[
                ConceptAttribute(attribute_name="payment_id", data_type="string"),
                ConceptAttribute(attribute_name="payment_date", data_type="string"),
                ConceptAttribute(attribute_name="coupon_amount", data_type="number"),
                ConceptAttribute(attribute_name="currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/CouponPayment",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["CouponDistribution", "InterestPayment"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:rate_benchmark",
            concept_name="RateBenchmark",
            elm_archetype="CORE_CONCEPT",
            definition="Published reference interest rate index (e.g. SOFR, EURIBOR).",
            target_hub="hub_rate_benchmark",
            attributes=[
                ConceptAttribute(attribute_name="benchmark_code", data_type="string"),
                ConceptAttribute(attribute_name="benchmark_name", data_type="string"),
                ConceptAttribute(attribute_name="tenor", data_type="string"),
                ConceptAttribute(attribute_name="rate_type", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/IND/InterestRates/InterestRates/InterestRateBenchmark",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["InterestRateBenchmark", "ReferenceRate", "IndexRate"],
        ),
    ]


def get_fixed_income_rates_relationships() -> list[CanonicalRelationship]:
    """Relationships for Fixed Income / Rates."""
    return [
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_issuer",
            relationship_name="BondIssuer",
            target_link="lnk_bond_issuer",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:legal_entity"],
            grain_semantics="Legal entity issues bond debt security.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_coupon",
            relationship_name="BondCoupon",
            target_link="lnk_bond_coupon",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:coupon_payment"],
            grain_semantics="Bond pays periodic coupon interest payment.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_benchmark",
            relationship_name="BondBenchmark",
            target_link="lnk_bond_benchmark",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:rate_benchmark"],
            grain_semantics="Floating rate bond references rate benchmark.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_instrument",
            relationship_name="BondInstrument",
            target_link="lnk_bond_instrument",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:financial_instrument"],
            grain_semantics="Bond specializes financial instrument spine concept.",
        ),
    ]


def get_fixed_income_rates_pack(include_spine: bool = True) -> GraphAOntology:
    """Construct Fixed Income / Rates GraphAOntology."""
    concepts = get_fixed_income_rates_concepts()
    relationships = get_fixed_income_rates_relationships()
    if include_spine:
        concepts = [*get_core_spine_concepts(), *concepts]
        relationships = [*get_core_spine_relationships(), *relationships]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="fixed_income_rates",
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:opendv:enum:rate_benchmark_type",
                target_reference_table="ref_rate_benchmark_type",
                allowed_codes=["SOFR", "EURIBOR", "SONIA", "TONA", "ESTR"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Pack 3: Multi-Asset / IBOR
# ---------------------------------------------------------------------------


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
            grain_semantics="Portfolio contains position holding.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:position_instrument",
            relationship_name="PositionInstrument",
            target_link="lnk_position_instrument",
            endpoints=["urn:opendv:concept:position", "urn:opendv:concept:financial_instrument"],
            grain_semantics="Position represents holding of financial instrument.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:portfolio_account",
            relationship_name="PortfolioAccount",
            target_link="lnk_portfolio_account",
            endpoints=["urn:opendv:concept:portfolio", "urn:opendv:concept:account"],
            grain_semantics="Portfolio is mapped to master account.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:position_valuation",
            relationship_name="PositionValuation",
            target_link="lnk_position_valuation",
            endpoints=["urn:opendv:concept:position", "urn:opendv:concept:valuation"],
            grain_semantics="Position holding is priced via valuation event.",
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


# ---------------------------------------------------------------------------
# Pack 4: Private Equity / Buyout
# ---------------------------------------------------------------------------


def get_private_equity_buyout_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Private Equity / Buyout."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:fund",
            concept_name="Fund",
            elm_archetype="CORE_CONCEPT",
            definition="Closed-end private equity partnership or fund investment vehicle.",
            target_hub="hub_fund",
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
            target_hub="hub_limited_partner",
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
            grain_semantics="Fund issues capital drawdown call notice.",
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


# ---------------------------------------------------------------------------
# Pack 5: Private Credit / Direct Lending
# ---------------------------------------------------------------------------


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
            grain_semantics="Borrower entity executes credit facility.",
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


# ---------------------------------------------------------------------------
# Pack 6: Custody / Clearing
# ---------------------------------------------------------------------------


def get_custody_clearing_concepts() -> list[CanonicalBusinessConcept]:
    """Concepts for Custody / Clearing."""
    return [
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:custody_account",
            concept_name="CustodyAccount",
            elm_archetype="CORE_CONCEPT",
            definition="Safekeeping account holding client securities and cash at a custodian.",
            target_hub="hub_custody_account",
            attributes=[
                ConceptAttribute(attribute_name="custody_account_no", data_type="string"),
                ConceptAttribute(attribute_name="account_name", data_type="string"),
                ConceptAttribute(attribute_name="segregation_type", data_type="string"),
                ConceptAttribute(attribute_name="account_currency", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/CustomerAccount",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["SafekeepingAccount", "DepositoryAccount"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:custodian",
            concept_name="Custodian",
            elm_archetype="CORE_CONCEPT",
            definition="Institution holding securities and assets in safekeeping for clients.",
            target_hub="hub_custodian",
            attributes=[
                ConceptAttribute(attribute_name="custodian_code", data_type="string"),
                ConceptAttribute(attribute_name="custodian_name", data_type="string"),
                ConceptAttribute(attribute_name="bic", data_type="string"),
                ConceptAttribute(attribute_name="depository_member_id", data_type="string"),
            ],
            external_mappings=[
                ExternalMapping(
                    scheme="fibo",
                    target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/FundDepositary",
                    confidence=1.0,
                    predicate="skos:exactMatch",
                )
            ],
            synonyms=["CustodianBank", "Depository", "SubCustodian"],
        ),
        CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:settlement_instruction",
            concept_name="SettlementInstruction",
            elm_archetype="CORE_CONCEPT",
            definition="Settlement instruction (SSI) or trade settlement delivery message.",
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
            grain_semantics="Legal entity holds securities custody account.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:custody_account_custodian",
            relationship_name="CustodyAccountCustodian",
            target_link="lnk_custody_account_custodian",
            endpoints=["urn:opendv:concept:custody_account", "urn:opendv:concept:custodian"],
            grain_semantics="Custodian bank administers custody account.",
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
    "get_fixed_income_rates_concepts",
    "get_fixed_income_rates_pack",
    "get_fixed_income_rates_relationships",
    "get_multi_asset_ibor_concepts",
    "get_multi_asset_ibor_pack",
    "get_multi_asset_ibor_relationships",
    "get_private_credit_direct_lending_concepts",
    "get_private_credit_direct_lending_pack",
    "get_private_credit_direct_lending_relationships",
    "get_private_equity_buyout_concepts",
    "get_private_equity_buyout_pack",
    "get_private_equity_buyout_relationships",
    "get_public_equities_oms_concepts",
    "get_public_equities_oms_pack",
    "get_public_equities_oms_relationships",
]
