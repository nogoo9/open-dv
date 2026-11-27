"""Alternative & custody V2 Asset Management vertical starter packs."""

from __future__ import annotations

from decimal import Decimal

from opendv.models.ontology import CanonicalBusinessConcept, OntologyRelationship
from opendv.sources.starter_packs.spine import make_attr, make_concept, make_rel

_FIBO = "https://spec.edmcouncil.org/fibo/ontology/"


def private_equity_buyout() -> tuple[
    list[CanonicalBusinessConcept], list[OntologyRelationship], list[str]
]:
    concepts = [
        make_concept(
            "fund",
            "Fund",
            "Closed-end private equity partnership or fund investment vehicle.",
            f"{_FIBO}SEC/Funds/Funds/PrivateEquityFund",
            [
                make_attr("fund_code", "string", is_bk=True),
                make_attr("fund_name", "string"),
                make_attr("vintage_year", "int"),
                make_attr("target_size", "number"),
                make_attr("fund_currency", "string"),
            ],
            ["PEFund", "PartnershipVehicle", "InvestmentFund"],
        ),
        make_concept(
            "limited_partner",
            "LimitedPartner",
            "Institutional or high-net-worth investor committing fund capital.",
            "https://www.omg.org/spec/Commons/Organizations/LegalEntity",
            [
                make_attr("lp_code", "string", is_bk=True),
                make_attr("lp_name", "string"),
                make_attr("lp_type", "string"),
                make_attr("tax_domicile", "string"),
            ],
            ["LP", "FundInvestor", "LimitedPartnerInvestor"],
        ),
        make_concept(
            "capital_call",
            "CapitalCall",
            "Capital drawdown notice issued to fund limited partners.",
            f"{_FIBO}FND/Accounting/CurrencyAmount/Funds",
            [
                make_attr("call_id", "string", is_bk=True),
                make_attr("call_date", "date"),
                make_attr("due_date", "date"),
                make_attr("called_amount", "number"),
                make_attr("currency", "string"),
            ],
            ["DrawdownNotice", "CapitalDrawdown", "SubscriptionCall"],
        ),
        make_concept(
            "distribution",
            "Distribution",
            "Cash or in-kind capital return distributed by fund to investors.",
            f"{_FIBO}FND/GoalsAndObjectives/Objectives/DistributionStrategy",
            [
                make_attr("distribution_id", "string", is_bk=True),
                make_attr("dist_date", "date"),
                make_attr("distribution_type", "string"),
                make_attr("dist_amount", "number"),
                make_attr("currency", "string"),
            ],
            ["FundDistribution", "CapitalReturn", "DividendDistribution"],
        ),
        make_concept(
            "portfolio_company",
            "PortfolioCompany",
            "Operating enterprise acquired or invested in by a private equity fund.",
            f"{_FIBO}BE/LegalEntities/LegalPersons/BusinessEntity",
            [
                make_attr("company_id", "string", is_bk=True),
                make_attr("company_name", "string"),
                make_attr("industry_sector", "string"),
                make_attr("acquisition_date", "date"),
            ],
            ["Portco", "OperatingCompany", "TargetCompany"],
        ),
    ]
    rels = [
        make_rel(
            "fund_lp",
            "FundLP",
            "lnk_fund_lp",
            "fund",
            "limited_partner",
            "Limited partner commits capital to private equity fund.",
        ),
        make_rel(
            "capital_call_fund",
            "CapitalCallFund",
            "lnk_capital_call_fund",
            "capital_call",
            "fund",
            "Investment fund issues capital drawdown notice to partners.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "distribution_fund",
            "DistributionFund",
            "lnk_distribution_fund",
            "distribution",
            "fund",
            "Fund issues cash or stock distribution to partners.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "fund_investment",
            "FundInvestment",
            "lnk_fund_investment",
            "fund",
            "portfolio_company",
            "Fund holds equity investment in operating portfolio company.",
        ),
    ]
    return concepts, rels, []


def private_credit_direct_lending() -> tuple[
    list[CanonicalBusinessConcept], list[OntologyRelationship], list[str]
]:
    concepts = [
        make_concept(
            "credit_facility",
            "CreditFacility",
            "Bilateral or syndicated loan facility or credit agreement.",
            f"{_FIBO}FBC/DebtAndEquities/Debt/CreditFacility",
            [
                make_attr("facility_id", "string", is_bk=True),
                make_attr("facility_type", "string"),
                make_attr("total_commitment", "number"),
                make_attr("drawn_amount", "number"),
                make_attr("maturity_date", "date"),
            ],
            ["LoanFacility", "TermLoan", "Revolver"],
        ),
        make_concept(
            "borrower",
            "Borrower",
            "Corporate debtor or sponsor company incurring credit liability.",
            "https://www.omg.org/spec/Commons/Organizations/LegalEntity",
            [
                make_attr("borrower_id", "string", is_bk=True),
                make_attr("borrower_name", "string"),
                make_attr("industry_sector", "string"),
                make_attr("credit_rating", "string"),
            ],
            ["Debtor", "LoanBorrower", "CreditObligor"],
        ),
        make_concept(
            "collateral",
            "Collateral",
            "Asset or property pledged by borrower as security for debt.",
            f"{_FIBO}FBC/DebtAndEquities/Debt/Collateral",
            [
                make_attr("collateral_id", "string", is_bk=True),
                make_attr("collateral_type", "string"),
                make_attr("appraised_value", "number"),
                make_attr("lien_position", "string"),
            ],
            ["PledgedAsset", "SecurityInterest", "LoanCollateral"],
        ),
        make_concept(
            "covenant",
            "Covenant",
            "Financial or operational compliance condition in loan agreement.",
            f"{_FIBO}FND/Agreements/Contracts/Contract",
            [
                make_attr("covenant_id", "string", is_bk=True),
                make_attr("covenant_type", "string", enum_uri="urn:opendv:enum:covenant_type"),
                make_attr("threshold_value", "number"),
                make_attr("test_frequency", "string"),
            ],
            ["LoanCovenant", "MaintenanceCovenant", "FinancialCovenant"],
        ),
    ]
    rels = [
        make_rel(
            "facility_borrower",
            "FacilityBorrower",
            "lnk_facility_borrower",
            "credit_facility",
            "borrower",
            "Corporate borrower executes syndicated debt credit facility.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "facility_collateral",
            "FacilityCollateral",
            "lnk_facility_collateral",
            "credit_facility",
            "collateral",
            "Credit facility is secured by pledged collateral asset.",
        ),
        make_rel(
            "facility_covenant",
            "FacilityCovenant",
            "lnk_facility_covenant",
            "credit_facility",
            "covenant",
            "Credit facility is governed by financial covenant terms.",
            cardinality="ONE_TO_MANY",
        ),
        make_rel(
            "facility_agreement",
            "FacilityAgreement",
            "lnk_facility_agreement",
            "credit_facility",
            "agreement",
            "Credit facility references master credit agreement spine concept.",
            cardinality="MANY_TO_ONE",
        ),
    ]
    return concepts, rels, ["urn:opendv:enum:covenant_type"]


def custody_clearing() -> tuple[
    list[CanonicalBusinessConcept], list[OntologyRelationship], list[str]
]:
    concepts = [
        make_concept(
            "custody_account",
            "CustodyAccount",
            "Securities safekeeping account maintained at a custodian bank.",
            f"{_FIBO}FBC/ProductsAndServices/ClientsAndAccounts/InvestmentAccount",
            [
                make_attr("account_number", "string", is_bk=True),
                make_attr("account_name", "string"),
                make_attr("sub_custodian", "string"),
                make_attr("country_code", "string"),
            ],
            ["SafekeepingAccount", "DepositoryAccount", "CustodialAccount"],
            tier="TIER_3_LOCAL_SCOPED",
            bkcc="CUSTODIAN_SCOPED",
            mapping_type="skos:closeMatch",
            mapping_confidence=Decimal("0.8500"),
        ),
        make_concept(
            "custodian",
            "Custodian",
            "Financial institution holding securities in safekeeping for clients.",
            f"{_FIBO}SEC/Equities/EquityInstruments/Custodian",
            [
                make_attr("custodian_code", "string", is_bk=True),
                make_attr("custodian_name", "string"),
                make_attr("bic_code", "string"),
                make_attr("rating", "string"),
            ],
            ["CustodianBank", "GlobalCustodian", "SubCustodian"],
            tier="TIER_1_GLOBAL_STANDARD",
            bkcc="SWIFT_BIC",
        ),
        make_concept(
            "settlement_instruction",
            "SettlementInstruction",
            "Standing settlement instruction (SSI) or trade delivery notice.",
            f"{_FIBO}FBC/FinancialInstruments/Settlement/Settlement",
            [
                make_attr("instruction_id", "string", is_bk=True),
                make_attr("settlement_date", "date"),
                make_attr("settlement_type", "string"),
                make_attr(
                    "settlement_status", "string", enum_uri="urn:opendv:enum:settlement_status"
                ),
            ],
            ["SSI", "SettlementMessage", "DeliveryInstruction"],
        ),
        make_concept(
            "corporate_action",
            "CorporateAction",
            "Event initiated by security issuer affecting underlying holdings.",
            f"{_FIBO}SEC/Securities/SecuritiesIdentification/SecurityRegistry",
            [
                make_attr("action_id", "string", is_bk=True),
                make_attr(
                    "action_type", "string", enum_uri="urn:opendv:enum:corporate_action_type"
                ),
                make_attr("record_date", "date"),
                make_attr("payable_date", "date"),
            ],
            ["CorpAction", "DividendEvent", "Reorganization"],
        ),
    ]
    rels = [
        make_rel(
            "custody_account_holder",
            "CustodyAccountHolder",
            "lnk_custody_account_holder",
            "custody_account",
            "legal_entity",
            "Legal entity holds institutional securities custody account.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "custody_account_custodian",
            "CustodyAccountCustodian",
            "lnk_custody_account_custodian",
            "custody_account",
            "custodian",
            "Appointed custodian bank administers securities custody account.",
            cardinality="MANY_TO_ONE",
        ),
        make_rel(
            "settlement_custody",
            "SettlementCustody",
            "lnk_settlement_custody",
            "settlement_instruction",
            "custody_account",
            "Settlement instruction delivers to or receives from custody account.",
            cardinality="MANY_TO_ONE",
        ),
    ]
    return concepts, rels, [
        "urn:opendv:enum:settlement_status",
        "urn:opendv:enum:corporate_action_type",
    ]


__all__ = [
    "custody_clearing",
    "private_credit_direct_lending",
    "private_equity_buyout",
]
