"""Public markets V2 Asset Management vertical starter packs (Equities, Rates, IBOR)."""

from __future__ import annotations

from opendv.models.ontology import CanonicalBusinessConcept, OntologyRelationship
from opendv.sources.starter_packs.spine import make_attr, make_concept, make_rel

_FIBO = "https://spec.edmcouncil.org/fibo/ontology/"


def public_equities_oms() -> tuple[
    list[CanonicalBusinessConcept], list[OntologyRelationship], list[str]
]:
    concepts = [
        make_concept(
            "order",
            "Order",
            "Client or portfolio manager order to buy or sell securities.",
            f"{_FIBO}FBC/ProductsAndServices/FinancialProductsAndServices/TradeLifecycleStage",
            [
                make_attr("order_id", "string", is_bk=True),
                make_attr("order_type", "string"),
                make_attr("side", "string", enum_uri="urn:opendv:enum:order_side"),
                make_attr("quantity", "number"),
                make_attr("limit_price", "number"),
                make_attr("order_status", "string"),
            ],
            ["TradeOrder", "ClientOrder", "OMSOrder"],
        ),
        make_concept(
            "execution",
            "Execution",
            "Trade execution or partial fill returned by a broker or market center.",
            f"{_FIBO}FBC/ProductsAndServices/FinancialProductsAndServices/Trade",
            [
                make_attr("execution_id", "string", is_bk=True),
                make_attr("exec_price", "number"),
                make_attr("exec_quantity", "number"),
                make_attr("exec_time", "timestamp"),
                make_attr("liquidity_indicator", "string"),
            ],
            ["Fill", "TradeFill", "ExecutionReport"],
        ),
        make_concept(
            "broker",
            "Broker",
            "Broker-dealer firm providing trade execution, clearing, or liquidity.",
            f"{_FIBO}FBC/ProductsAndServices/FinancialProductsAndServices/Broker",
            [
                make_attr("broker_code", "string", is_bk=True),
                make_attr("broker_name", "string"),
                make_attr("mpid", "string"),
                make_attr("rating", "string"),
            ],
            ["ExecutingBroker", "BrokerDealer", "CounterpartyBroker"],
        ),
        make_concept(
            "exchange",
            "Exchange",
            "Regulated financial exchange, ATS, or Multilateral Trading Facility.",
            f"{_FIBO}FBC/FunctionalEntities/Markets/Exchange",
            [
                make_attr("mic_code", "string", is_bk=True),
                make_attr("exchange_name", "string"),
                make_attr("operating_mic", "string"),
                make_attr("country_code", "string"),
            ],
            ["MarketCenter", "TradingVenue", "ExecutionVenue"],
            tier="TIER_1_GLOBAL_STANDARD",
            bkcc="ISO_10383_MIC",
        ),
    ]
    rels = [
        make_rel(
            "order_execution",
            "OrderExecution",
            "lnk_order_execution",
            "order",
            "execution",
            "Order is filled through one or more executions.",
            cardinality="ONE_TO_MANY",
        ),
        make_rel(
            "order_instrument",
            "OrderInstrument",
            "lnk_order_instrument",
            "order",
            "financial_instrument",
            "Trading order targets execution in designated financial instrument.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "order_broker",
            "OrderBroker",
            "lnk_order_broker",
            "order",
            "broker",
            "Trading order is routed to broker for execution.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "execution_exchange",
            "ExecutionExchange",
            "lnk_execution_exchange",
            "execution",
            "exchange",
            "Trade execution occurred at specific regulated exchange venue.",
            cardinality="MANY_TO_ONE",
        ),
    ]
    return concepts, rels, ["urn:opendv:enum:order_side", "urn:opendv:enum:execution_type"]


def fixed_income_rates() -> tuple[
    list[CanonicalBusinessConcept], list[OntologyRelationship], list[str]
]:
    concepts = [
        make_concept(
            "bond",
            "Bond",
            "Debt security requiring issuer to repay principal and interest.",
            f"{_FIBO}SEC/Debt/Bonds/Bond",
            [
                make_attr("bond_id", "string", is_bk=True),
                make_attr("coupon_rate", "number"),
                make_attr("maturity_date", "date"),
                make_attr("day_count_convention", "string"),
                make_attr("seniority", "string"),
            ],
            ["FixedIncomeSecurity", "DebtSecurity", "Note"],
            parent_uri="urn:opendv:concept:financial_instrument",
            tier="TIER_1_GLOBAL_STANDARD",
            bkcc="ISO_6166_ISIN",
        ),
        make_concept(
            "yield_curve",
            "YieldCurve",
            "Term structure of interest rates across tenors for a credit curve.",
            f"{_FIBO}IND/Indicators/Indicators/TermStructure",
            [
                make_attr("curve_id", "string", is_bk=True),
                make_attr("curve_name", "string"),
                make_attr("currency", "string"),
                make_attr("curve_type", "string"),
            ],
            ["TermStructure", "InterestRateCurve", "ZeroCurve"],
        ),
        make_concept(
            "coupon_payment",
            "CouponPayment",
            "Periodic interest cashflow distribution on debt securities.",
            f"{_FIBO}SEC/Debt/Bonds/CouponPaymentTerms",
            [
                make_attr("payment_id", "string", is_bk=True),
                make_attr("payment_date", "date"),
                make_attr("coupon_amount", "number"),
                make_attr("currency", "string"),
            ],
            ["CouponDistribution", "InterestPayment"],
        ),
        make_concept(
            "rate_benchmark",
            "RateBenchmark",
            "Published reference interest rate index (e.g. SOFR, EURIBOR).",
            f"{_FIBO}IND/InterestRates/InterestRates/InterestRateBenchmark",
            [
                make_attr("benchmark_code", "string", is_bk=True),
                make_attr("benchmark_name", "string"),
                make_attr("tenor", "string"),
                make_attr("rate_type", "string"),
            ],
            ["InterestRateBenchmark", "ReferenceRate", "IndexRate"],
        ),
    ]
    rels = [
        make_rel(
            "bond_issuer",
            "BondIssuer",
            "lnk_bond_issuer",
            "bond",
            "legal_entity",
            "Issuing legal entity issues bond debt security.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "bond_coupon",
            "BondCoupon",
            "lnk_bond_coupon",
            "bond",
            "coupon_payment",
            "Fixed income bond pays periodic coupon distribution.",
            cardinality="ONE_TO_MANY",
        ),
        make_rel(
            "bond_benchmark",
            "BondBenchmark",
            "lnk_bond_benchmark",
            "bond",
            "rate_benchmark",
            "Floating rate bond references published interest rate benchmark.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "bond_instrument",
            "BondInstrument",
            "lnk_bond_instrument",
            "bond",
            "financial_instrument",
            "Bond security specializes canonical financial instrument concept.",
            cardinality="ONE_TO_ONE",
        ),
    ]
    return concepts, rels, ["urn:opendv:enum:rate_benchmark_type"]


def multi_asset_ibor() -> tuple[
    list[CanonicalBusinessConcept], list[OntologyRelationship], list[str]
]:
    concepts = [
        make_concept(
            "portfolio",
            "Portfolio",
            "Aggregate holding container for investments managed under a mandate.",
            f"{_FIBO}FND/OwnershipAndControl/Ownership/Portfolio",
            [
                make_attr("portfolio_code", "string", is_bk=True),
                make_attr("portfolio_name", "string"),
                make_attr("base_currency", "string", enum_uri="urn:opendv:enum:currency_code"),
                make_attr("strategy", "string"),
            ],
            ["InvestmentPortfolio", "FundPortfolio", "AccountPortfolio"],
        ),
        make_concept(
            "position",
            "Position",
            "Point-in-time holding balance of an instrument in a portfolio.",
            f"{_FIBO}FBC/ProductsAndServices/FinancialProductsAndServices/Position",
            [
                make_attr("position_id", "string", is_bk=True),
                make_attr("position_date", "date"),
                make_attr("quantity", "number"),
                make_attr("market_value", "number"),
                make_attr("currency", "string"),
            ],
            ["SecurityPosition", "Holding", "PortfolioPosition"],
        ),
        make_concept(
            "cash_balance",
            "CashBalance",
            "Liquid cash or money market holding in a specific currency.",
            f"{_FIBO}FND/Accounting/CurrencyAmount/Funds",
            [
                make_attr("balance_id", "string", is_bk=True),
                make_attr("as_of_date", "date"),
                make_attr("settled_amount", "number"),
                make_attr("pending_amount", "number"),
                make_attr("currency", "string"),
            ],
            ["CashPosition", "CurrencyBalance", "LiquidityBalance"],
        ),
        make_concept(
            "valuation",
            "Valuation",
            "Pricing and mark-to-market valuation observation event.",
            f"{_FIBO}FBC/FinancialInstruments/InstrumentPricing/SecurityPrice",
            [
                make_attr("valuation_id", "string", is_bk=True),
                make_attr("valuation_date", "date"),
                make_attr("price", "number"),
                make_attr("pricing_source", "string"),
                make_attr("currency", "string"),
            ],
            ["PricingEvent", "MarkToMarket", "SecurityPrice"],
        ),
    ]
    rels = [
        make_rel(
            "portfolio_position",
            "PortfolioPosition",
            "lnk_portfolio_position",
            "portfolio",
            "position",
            "Investment portfolio contains aggregated position holding records.",
            cardinality="ONE_TO_MANY",
        ),
        make_rel(
            "position_instrument",
            "PositionInstrument",
            "lnk_position_instrument",
            "position",
            "financial_instrument",
            "Portfolio position represents holding of designated financial instrument.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "portfolio_account",
            "PortfolioAccount",
            "lnk_portfolio_account",
            "portfolio",
            "account",
            "Investment portfolio maps to master custody accounting account.",
        ),
        make_rel(
            "position_valuation",
            "PositionValuation",
            "lnk_position_valuation",
            "position",
            "valuation",
            "Position holding valuation is priced via daily market event.",
            cardinality="ONE_TO_MANY",
        ),
    ]
    return concepts, rels, ["urn:opendv:enum:currency_code"]


__all__ = [
    "fixed_income_rates",
    "multi_asset_ibor",
    "public_equities_oms",
]
