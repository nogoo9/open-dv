"""Polysemy catalog of known ambiguous domain terms with disambiguation options."""

from __future__ import annotations

from typing import Any

KNOWN_POLYSEMIC_DEFINITIONS: dict[str, list[dict[str, Any]]] = {
    "account": [
        {
            "label": "General Ledger Account",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/LedgerAccount"
            ),
            "rationale": (
                "Represents an accounting ledger account recorded in a general ledger "
                "recording debits and credits."
            ),
        },
        {
            "label": "Customer / Client Account",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/CustomerAccount"
            ),
            "rationale": (
                "Represents a business relationship account held by a client with the "
                "financial institution."
            ),
        },
    ],
    "position": [
        {
            "label": "Financial Position",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Position"
            ),
            "rationale": (
                "Represents holding or commitment in a financial asset, currency, or contract."
            ),
        },
        {
            "label": "Equity Position",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/EquityPosition"
            ),
            "rationale": "Represents ownership interest or position in equity instruments.",
        },
    ],
    "facility": [
        {
            "label": "Credit / Loan Facility",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/CreditFacility"
            ),
            "rationale": (
                "Represents a credit arrangement or revolving loan commitment between lender "
                "and borrower."
            ),
        },
        {
            "label": "Physical Facility / Real Estate",
            "fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Places/Facilities/Facility",
            "rationale": (
                "Represents a physical building, plant, data center, or tangible property."
            ),
        },
    ],
    "party": [
        {
            "label": "Autonomous Legal Entity / Legal Person",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
            ),
            "rationale": (
                "Represents an individual person or corporate entity with legal rights and "
                "obligations."
            ),
        },
        {
            "label": "Contractual Party / Counterparty",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractParty"
            ),
            "rationale": (
                "Represents a party bound by or participating in a legal contract or agreement."
            ),
        },
    ],
    "portfolio": [
        {
            "label": "Investment / Securities Portfolio",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/SecurityAssets/Portfolio"
            ),
            "rationale": (
                "Represents an aggregate collection of investments, securities, cash "
                "equivalents, and financial assets owned by an investor or managed under an "
                "investment mandate."
            ),
            "canonical_concept_urn": "urn:opendv:concept:portfolio",
            "disambiguation_question": (
                "Does 'portfolio' refer to an investor/client investment portfolio, a fund "
                "portfolio account holding collective investment assets, or a structured "
                "debt/CDO reference portfolio?"
            ),
        },
        {
            "label": "Fund Portfolio / Vehicle Holdings Account",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/FundPortfolio"
            ),
            "rationale": (
                "Represents the specific portfolio account and basket of investment assets "
                "belonging to a collective investment vehicle or mutual/hedge fund."
            ),
            "canonical_concept_urn": "urn:opendv:concept:fund_portfolio",
            "disambiguation_question": (
                "Does 'portfolio' refer to an investor/client investment portfolio, a fund "
                "portfolio account holding collective investment assets, or a structured "
                "debt/CDO reference portfolio?"
            ),
        },
        {
            "label": "Managed CDO Portfolio / Structured Credit Pool",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/CollateralizedDebtObligations/ManagedCDOPortfolio"
            ),
            "rationale": (
                "Represents a dynamically managed pool or portfolio of reference debt assets "
                "and collateral underlying a collateralized debt obligation (CDO)."
            ),
            "canonical_concept_urn": "urn:opendv:concept:cdo_portfolio",
            "disambiguation_question": (
                "Does 'portfolio' refer to an investor/client investment portfolio, a fund "
                "portfolio account holding collective investment assets, or a structured "
                "debt/CDO reference portfolio?"
            ),
        },
    ],
    "security": [
        {
            "label": "Negotiable Financial Security",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/Security"
            ),
            "rationale": (
                "Represents a fungible, negotiable financial instrument (stock, bond, note) "
                "that holds monetary value and can be traded on exchanges or OTC markets."
            ),
            "canonical_concept_urn": "urn:opendv:concept:financial_instrument",
            "disambiguation_question": (
                "Does 'security' refer to a negotiable financial security (equity/debt "
                "instrument), collateral pledged as security, or a security agreement "
                "contract?"
            ),
        },
        {
            "label": "Collateral / Pledged Security Interest",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/Collateral"
            ),
            "rationale": (
                "Represents an asset pledged by a borrower or counterparty as security to "
                "guarantee fulfillment of a loan or credit obligation."
            ),
            "canonical_concept_urn": "urn:opendv:concept:collateral",
            "disambiguation_question": (
                "Does 'security' refer to a negotiable financial security (equity/debt "
                "instrument), collateral pledged as security, or a security agreement "
                "contract?"
            ),
        },
        {
            "label": "Security Agreement / Collateral Contract",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/SecurityAgreement"
            ),
            "rationale": (
                "Represents a legal contract or security agreement that grants a creditor or "
                "secured party a financial security interest in specified collateral."
            ),
            "canonical_concept_urn": "urn:opendv:concept:security_agreement",
            "disambiguation_question": (
                "Does 'security' refer to a negotiable financial security (equity/debt "
                "instrument), collateral pledged as security, or a security agreement "
                "contract?"
            ),
        },
    ],
    "order": [
        {
            "label": "Trade Lifecycle Stage / Order State",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/TradeLifecycleStage"
            ),
            "rationale": (
                "Represents the initial order placement and routing phase within an order "
                "management system (OMS) prior to trade execution."
            ),
            "canonical_concept_urn": "urn:opendv:concept:order",
            "disambiguation_question": (
                "Does 'order' refer to an order state in the trade lifecycle, a commercial "
                "customer instruction/request, or a fund subscription/redemption order "
                "facility?"
            ),
        },
        {
            "label": "Commercial Request / Business Instruction",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Reporting/Request"
            ),
            "rationale": (
                "Represents a formal customer request, operational instruction, or business "
                "order submitted to a financial services provider."
            ),
            "canonical_concept_urn": "urn:opendv:concept:service_request",
            "disambiguation_question": (
                "Does 'order' refer to an order state in the trade lifecycle, a commercial "
                "customer instruction/request, or a fund subscription/redemption order "
                "facility?"
            ),
        },
        {
            "label": "Fund Order Desk / Subscription Facility",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/FundOrderDesk"
            ),
            "rationale": (
                "Represents the dealing desk or processing facility responsible for receiving "
                "and processing fund unit subscription and redemption orders."
            ),
            "canonical_concept_urn": "urn:opendv:concept:fund_order_desk",
            "disambiguation_question": (
                "Does 'order' refer to an order state in the trade lifecycle, a commercial "
                "customer instruction/request, or a fund subscription/redemption order "
                "facility?"
            ),
        },
    ],
    "trade": [
        {
            "label": "Executed Trade / Transaction Agreement",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Trade"
            ),
            "rationale": (
                "Represents a legally binding transaction agreement resulting from matching "
                "buyers and sellers for financial instruments or commodities."
            ),
            "canonical_concept_urn": "urn:opendv:concept:trade",
            "disambiguation_question": (
                "Does 'trade' refer to an executed transaction/trade agreement, the entire "
                "trade lifecycle, or a unique trade identifier reference?"
            ),
        },
        {
            "label": "Trade Lifecycle Process",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/TradeLifecycle"
            ),
            "rationale": (
                "Represents the complete end-to-end lifecycle of a trade from order entry, "
                "matching, clearing, to final settlement and custody."
            ),
            "canonical_concept_urn": "urn:opendv:concept:trade_lifecycle",
            "disambiguation_question": (
                "Does 'trade' refer to an executed transaction/trade agreement, the entire "
                "trade lifecycle, or a unique trade identifier reference?"
            ),
        },
        {
            "label": "Trade Identifier Reference",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/TradeIdentifier"
            ),
            "rationale": (
                "Represents the authoritative transaction identifier (e.g. USI, UTI, "
                "execution ID) designating a trade across systems and regulatory reports."
            ),
            "canonical_concept_urn": "urn:opendv:concept:trade_identifier",
            "disambiguation_question": (
                "Does 'trade' refer to an executed transaction/trade agreement, the entire "
                "trade lifecycle, or a unique trade identifier reference?"
            ),
        },
    ],
    "fund": [
        {
            "label": "Collective Investment Vehicle / Fund",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/CollectiveInvestmentVehicle"
            ),
            "rationale": (
                "Represents an investment vehicle that pools capital from multiple investors "
                "to invest in a diversified portfolio of securities or assets."
            ),
            "canonical_concept_urn": "urn:opendv:concept:fund",
            "disambiguation_question": (
                "Does 'fund' refer to a collective investment vehicle, liquid currency/cash "
                "reserves, an institutional private equity fund, or an open-end mutual fund?"
            ),
        },
        {
            "label": "Currency Funds / Liquid Monies",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FND/Accounting/CurrencyAmount/Funds"
            ),
            "rationale": (
                "Represents liquid monetary balances, cash reserves, or committed currency "
                "amounts available for disbursement or investment."
            ),
            "canonical_concept_urn": "urn:opendv:concept:cash_funds",
            "disambiguation_question": (
                "Does 'fund' refer to a collective investment vehicle, liquid currency/cash "
                "reserves, an institutional private equity fund, or an open-end mutual fund?"
            ),
        },
        {
            "label": "Private Equity Fund",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/PrivateEquityFund"
            ),
            "rationale": (
                "Represents an alternative investment fund structured as a limited "
                "partnership that invests directly in private operating companies or buyouts."
            ),
            "canonical_concept_urn": "urn:opendv:concept:private_equity_fund",
            "disambiguation_question": (
                "Does 'fund' refer to a collective investment vehicle, liquid currency/cash "
                "reserves, an institutional private equity fund, or an open-end mutual fund?"
            ),
        },
        {
            "label": "Mutual Fund / Retail CIV",
            "fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/MutualFund",
            "rationale": (
                "Represents an open-end investment company or fund that pools retail investor "
                "money to purchase diversified securities."
            ),
            "canonical_concept_urn": "urn:opendv:concept:mutual_fund",
            "disambiguation_question": (
                "Does 'fund' refer to a collective investment vehicle, liquid currency/cash "
                "reserves, an institutional private equity fund, or an open-end mutual fund?"
            ),
        },
    ],
    "benchmark": [
        {
            "label": "Portfolio Benchmark / Performance Index",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/PortfolioBenchmark"
            ),
            "rationale": (
                "Represents a market index or standard against which the performance, return, "
                "and risk of an investment portfolio or fund is measured."
            ),
            "canonical_concept_urn": "urn:opendv:concept:benchmark",
            "disambiguation_question": (
                "Does 'benchmark' refer to a portfolio performance benchmark / hurdle index, "
                "an interest rate benchmark (SOFR/LIBOR), or an observed market reference "
                "rate?"
            ),
        },
        {
            "label": "Interest Rate Benchmark",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/IND/InterestRates/InterestRates/InterestRateBenchmark"
            ),
            "rationale": (
                "Represents a published reference rate (such as SOFR, EURIBOR, or SONIA) used "
                "to determine interest payments for floating-rate debt."
            ),
            "canonical_concept_urn": "urn:opendv:concept:interest_rate_benchmark",
            "disambiguation_question": (
                "Does 'benchmark' refer to a portfolio performance benchmark / hurdle index, "
                "an interest rate benchmark (SOFR/LIBOR), or an observed market reference "
                "rate?"
            ),
        },
        {
            "label": "Market Rate Indicator",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/IND/Indicators/Indicators/MarketRate"
            ),
            "rationale": (
                "Represents an observed or published market rate used across financial "
                "markets for pricing, discounting, and valuation."
            ),
            "canonical_concept_urn": "urn:opendv:concept:market_rate",
            "disambiguation_question": (
                "Does 'benchmark' refer to a portfolio performance benchmark / hurdle index, "
                "an interest rate benchmark (SOFR/LIBOR), or an observed market reference "
                "rate?"
            ),
        },
    ],
    "mandate": [
        {
            "label": "Portfolio Investment Policy / Mandate",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/FundPortfolioInvestmentPolicy"
            ),
            "rationale": (
                "Represents the formal guidelines, asset allocation limits, and investment "
                "constraints established for managing a portfolio or fund."
            ),
            "canonical_concept_urn": "urn:opendv:concept:mandate",
            "disambiguation_question": (
                "Does 'mandate' refer to a fund portfolio investment policy, an "
                "account-specific investment management agreement, or a high-level investment "
                "objective?"
            ),
        },
        {
            "label": "Investment Management Service Agreement",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/AccountSpecificServiceAgreement"
            ),
            "rationale": (
                "Represents the bilateral legal contract between a client and an asset "
                "manager delegating discretionary management of an account."
            ),
            "canonical_concept_urn": "urn:opendv:concept:investment_management_agreement",
            "disambiguation_question": (
                "Does 'mandate' refer to a fund portfolio investment policy, an "
                "account-specific investment management agreement, or a high-level investment "
                "objective?"
            ),
        },
        {
            "label": "Investment Objective",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FND/GoalsAndObjectives/Objectives/InvestmentObjective"
            ),
            "rationale": (
                "Represents the stated financial target, capital growth, income generation, "
                "or risk profile objective defined for an investment strategy."
            ),
            "canonical_concept_urn": "urn:opendv:concept:investment_objective",
            "disambiguation_question": (
                "Does 'mandate' refer to a fund portfolio investment policy, an "
                "account-specific investment management agreement, or a high-level investment "
                "objective?"
            ),
        },
    ],
    "instrument": [
        {
            "label": "Financial Instrument",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/FinancialInstrument"
            ),
            "rationale": (
                "Represents a contract that gives rise to a financial asset of one entity and "
                "a financial liability or equity instrument of another."
            ),
            "canonical_concept_urn": "urn:opendv:concept:financial_instrument",
            "disambiguation_question": (
                "Does 'instrument' refer to a general financial instrument, a cash/money "
                "market instrument, or a derivative instrument?"
            ),
        },
        {
            "label": "Cash Instrument",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/CashInstrument"
            ),
            "rationale": (
                "Represents a financial instrument whose value is determined directly by the "
                "markets, including cash deposits, repos, and money market instruments."
            ),
            "canonical_concept_urn": "urn:opendv:concept:cash_instrument",
            "disambiguation_question": (
                "Does 'instrument' refer to a general financial instrument, a cash/money "
                "market instrument, or a derivative instrument?"
            ),
        },
        {
            "label": "Derivative Instrument",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/DerivativeInstrument"
            ),
            "rationale": (
                "Represents a financial contract whose value is derived from the performance "
                "of underlying assets, interest rates, or indices."
            ),
            "canonical_concept_urn": "urn:opendv:concept:derivative_instrument",
            "disambiguation_question": (
                "Does 'instrument' refer to a general financial instrument, a cash/money "
                "market instrument, or a derivative instrument?"
            ),
        },
    ],
    "yield": [
        {
            "label": "Financial Instrument Yield",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/InstrumentPricing/Yield"
            ),
            "rationale": (
                "Represents the annualized rate of return realized on an investment or bond "
                "expressed as a percentage of market price or face value."
            ),
            "canonical_concept_urn": "urn:opendv:concept:yield",
            "disambiguation_question": (
                "Does 'yield' refer to an instrument's effective annual yield/return, a yield "
                "spread differential, or an equity share dividend yield?"
            ),
        },
        {
            "label": "Yield Spread",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/InstrumentPricing/YieldSpread"
            ),
            "rationale": (
                "Represents the difference between the yields of two financial instruments of "
                "differing credit quality or maturity."
            ),
            "canonical_concept_urn": "urn:opendv:concept:yield_spread",
            "disambiguation_question": (
                "Does 'yield' refer to an instrument's effective annual yield/return, a yield "
                "spread differential, or an equity share dividend yield?"
            ),
        },
        {
            "label": "Share Dividend Yield",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/ShareYield"
            ),
            "rationale": (
                "Represents the financial ratio showing how much a company pays out in "
                "dividends each year relative to its share price."
            ),
            "canonical_concept_urn": "urn:opendv:concept:dividend_yield",
            "disambiguation_question": (
                "Does 'yield' refer to an instrument's effective annual yield/return, a yield "
                "spread differential, or an equity share dividend yield?"
            ),
        },
    ],
    "settlement": [
        {
            "label": "Trade Settlement Process",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Settlement/Settlement"
            ),
            "rationale": (
                "Represents the fulfillment of obligations resulting from a transaction, "
                "involving the transfer of securities against cash (DvP) or free of payment."
            ),
            "canonical_concept_urn": "urn:opendv:concept:settlement",
            "disambiguation_question": (
                "Does 'settlement' refer to the post-trade settlement process, a discrete "
                "settlement lifecycle event, or contractual settlement terms?"
            ),
        },
        {
            "label": "Settlement Event",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Settlement/SettlementEvent"
            ),
            "rationale": (
                "Represents a discrete lifecycle event occurrence when securities or cash are "
                "finalized and transferred at a central securities depository (CSD)."
            ),
            "canonical_concept_urn": "urn:opendv:concept:settlement_event",
            "disambiguation_question": (
                "Does 'settlement' refer to the post-trade settlement process, a discrete "
                "settlement lifecycle event, or contractual settlement terms?"
            ),
        },
        {
            "label": "Contractual Settlement Terms",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/SettlementTerms"
            ),
            "rationale": (
                "Represents the agreed contractual stipulations governing settlement cycle "
                "(e.g. T+1, T+2), currency, and delivery mechanism."
            ),
            "canonical_concept_urn": "urn:opendv:concept:settlement_terms",
            "disambiguation_question": (
                "Does 'settlement' refer to the post-trade settlement process, a discrete "
                "settlement lifecycle event, or contractual settlement terms?"
            ),
        },
    ],
    "coupon": [
        {
            "label": "Coupon Payment Distribution",
            "fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/CouponPayment",
            "rationale": (
                "Represents a periodic cashflow or interest payment distributed by the bond "
                "issuer to bondholders according to the debt agreement."
            ),
            "canonical_concept_urn": "urn:opendv:concept:coupon_payment",
            "disambiguation_question": (
                "Does 'coupon' refer to a bond coupon payment distribution event, a coupon "
                "payment schedule, or contractual coupon interest terms?"
            ),
        },
        {
            "label": "Coupon Schedule",
            "fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/CouponSchedule",
            "rationale": (
                "Represents the parametric schedule defining the calendar frequency, record "
                "dates, and payment dates for debt coupon distributions."
            ),
            "canonical_concept_urn": "urn:opendv:concept:coupon_schedule",
            "disambiguation_question": (
                "Does 'coupon' refer to a bond coupon payment distribution event, a coupon "
                "payment schedule, or contractual coupon interest terms?"
            ),
        },
        {
            "label": "Bond Coupon Terms",
            "fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/BondCoupon",
            "rationale": (
                "Represents the stated interest rate or coupon characteristics (fixed, "
                "floating, stepped, or zero) attached to a debt security."
            ),
            "canonical_concept_urn": "urn:opendv:concept:bond_coupon",
            "disambiguation_question": (
                "Does 'coupon' refer to a bond coupon payment distribution event, a coupon "
                "payment schedule, or contractual coupon interest terms?"
            ),
        },
    ],
    "spread": [
        {
            "label": "Bid-Ask Spread",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/InstrumentPricing/BidAskSpread"
            ),
            "rationale": (
                "Represents the difference between the highest price a buyer is willing to "
                "pay and the lowest price a seller is willing to accept for an asset."
            ),
            "canonical_concept_urn": "urn:opendv:concept:bid_ask_spread",
            "disambiguation_question": (
                "Does 'spread' refer to a trading bid-ask spread, a credit/yield spread over "
                "benchmark, or a broader market spread indicator?"
            ),
        },
        {
            "label": "Yield Spread",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/InstrumentPricing/YieldSpread"
            ),
            "rationale": (
                "Represents the yield differential reflecting credit risk premium between a "
                "debt instrument and a risk-free benchmark bond."
            ),
            "canonical_concept_urn": "urn:opendv:concept:yield_spread",
            "disambiguation_question": (
                "Does 'spread' refer to a trading bid-ask spread, a credit/yield spread over "
                "benchmark, or a broader market spread indicator?"
            ),
        },
        {
            "label": "Market Spread Indicator",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/IND/Indicators/Indicators/MarketSpread"
            ),
            "rationale": (
                "Represents an observed market spread index or indicator across broader asset "
                "classes, funding markets, or swap rates."
            ),
            "canonical_concept_urn": "urn:opendv:concept:market_spread",
            "disambiguation_question": (
                "Does 'spread' refer to a trading bid-ask spread, a credit/yield spread over "
                "benchmark, or a broader market spread indicator?"
            ),
        },
    ],
    "collateral": [
        {
            "label": "Pledged Collateral Asset",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/Collateral"
            ),
            "rationale": (
                "Represents an asset or property pledged by a borrower or counterparty to "
                "secure repayment of a loan or financial obligation."
            ),
            "canonical_concept_urn": "urn:opendv:concept:collateral",
            "disambiguation_question": (
                "Does 'collateral' refer to a pledged financial or physical asset, a legal "
                "collateral agreement, or a collateralized loan facility?"
            ),
        },
        {
            "label": "Collateral Agreement / Pledge Contract",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/CollateralAgreement"
            ),
            "rationale": (
                "Represents the legal contract granting a lender or pledgee an enforceable "
                "security interest in specified collateral assets."
            ),
            "canonical_concept_urn": "urn:opendv:concept:collateral_agreement",
            "disambiguation_question": (
                "Does 'collateral' refer to a pledged financial or physical asset, a legal "
                "collateral agreement, or a collateralized loan facility?"
            ),
        },
        {
            "label": "Collateralized Loan Facility",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/Loans/CollateralizedLoan"
            ),
            "rationale": (
                "Represents a loan or credit facility that is secured by collateral pledged "
                "by the borrower."
            ),
            "canonical_concept_urn": "urn:opendv:concept:collateralized_loan",
            "disambiguation_question": (
                "Does 'collateral' refer to a pledged financial or physical asset, a legal "
                "collateral agreement, or a collateralized loan facility?"
            ),
        },
    ],
    "nav": [
        {
            "label": "Net Asset Value Calculation Method",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/CollectiveInvestmentVehicles/NetAssetValueCalculationMethod"
            ),
            "rationale": (
                "Represents the valuation policy and accounting method used by a fund "
                "administrator to calculate total assets minus total liabilities."
            ),
            "canonical_concept_urn": "urn:opendv:concept:nav_calculation_method",
            "disambiguation_question": (
                "Does 'nav' refer to the fund accounting valuation calculation method, or the "
                "per-share net asset value price of fund units?"
            ),
        },
        {
            "label": "Fund Unit Net Asset Value Price",
            "fibo_uri": (
                "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/InstrumentPricing/SecurityPrice"
            ),
            "rationale": (
                "Represents the per-share monetary price or net asset value calculated for "
                "dealing, subscription, and redemption of fund units."
            ),
            "canonical_concept_urn": "urn:opendv:concept:nav_price",
            "disambiguation_question": (
                "Does 'nav' refer to the fund accounting valuation calculation method, or the "
                "per-share net asset value price of fund units?"
            ),
        },
    ],
}

__all__ = ["KNOWN_POLYSEMIC_DEFINITIONS"]
