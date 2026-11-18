"""Asset Management Starter Pack Registry and Loader."""

from __future__ import annotations

from typing import Final

from opendv_contracts.dtos.starter_packs import (
    AssetManagementPackId,
    StarterPackListResponse,
    StarterPackMetadata,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_sources.asset_management.presets import (
    compose_asset_management_ontology,
    get_all_domains_pack,
)
from opendv_sources.asset_management.spine import get_core_spine_pack
from opendv_sources.asset_management.vertical_packs import (
    get_custody_clearing_pack,
    get_fixed_income_rates_pack,
    get_multi_asset_ibor_pack,
    get_private_credit_direct_lending_pack,
    get_private_equity_buyout_pack,
    get_public_equities_oms_pack,
)

PACK_REGISTRY_METADATA: Final[dict[AssetManagementPackId, StarterPackMetadata]] = {
    AssetManagementPackId.CORE_SPINE: StarterPackMetadata(
        pack_id=AssetManagementPackId.CORE_SPINE,
        display_name="Unified Core Enterprise Spine",
        description=(
            "Universal enterprise invariants: LegalEntity, Account, Agreement, "
            "FinancialInstrument, Transaction, Document."
        ),
        domain_namespace="core_spine",
        standards_references=["FIBO", "ISO 17442 LEI", "ISO 13616 IBAN", "ISO 6166 ISIN"],
        concept_count=6,
        relationship_count=6,
        concepts=[
            "LegalEntity",
            "Account",
            "Agreement",
            "FinancialInstrument",
            "Transaction",
            "Document",
        ],
        dependencies=[],
        version="1.0.0",
    ),
    AssetManagementPackId.PUBLIC_EQUITIES_OMS: StarterPackMetadata(
        pack_id=AssetManagementPackId.PUBLIC_EQUITIES_OMS,
        display_name="Public Equities / OMS",
        description=(
            "Order Management System, trade lifecycle, execution routing, brokers, "
            "and exchange venues."
        ),
        domain_namespace="public_equities_oms",
        standards_references=["FIX Protocol", "FIBO SEC/FBC", "ISO 10383 MIC"],
        concept_count=4,
        relationship_count=4,
        concepts=["Order", "Execution", "Broker", "Exchange"],
        dependencies=[AssetManagementPackId.CORE_SPINE],
        version="1.0.0",
    ),
    AssetManagementPackId.FIXED_INCOME_RATES: StarterPackMetadata(
        pack_id=AssetManagementPackId.FIXED_INCOME_RATES,
        display_name="Fixed Income / Rates",
        description=(
            "Debt securities, yield curves, coupon payment distribution, and interest rate "
            "benchmarks."
        ),
        domain_namespace="fixed_income_rates",
        standards_references=["FIBO SEC/Debt", "FIBO IND", "ISDA"],
        concept_count=4,
        relationship_count=4,
        concepts=["Bond", "YieldCurve", "CouponPayment", "RateBenchmark"],
        dependencies=[AssetManagementPackId.CORE_SPINE],
        version="1.0.0",
    ),
    AssetManagementPackId.MULTI_ASSET_IBOR: StarterPackMetadata(
        pack_id=AssetManagementPackId.MULTI_ASSET_IBOR,
        display_name="Multi-Asset / IBOR",
        description=(
            "Investment Book of Record, multi-asset portfolios, position balances, cash, "
            "and valuations."
        ),
        domain_namespace="multi_asset_ibor",
        standards_references=["FIBO SEC/SecurityAssets", "FIBO FBC/Position", "GIPS"],
        concept_count=4,
        relationship_count=4,
        concepts=["Portfolio", "Position", "CashBalance", "Valuation"],
        dependencies=[AssetManagementPackId.CORE_SPINE],
        version="1.0.0",
    ),
    AssetManagementPackId.PRIVATE_EQUITY_BUYOUT: StarterPackMetadata(
        pack_id=AssetManagementPackId.PRIVATE_EQUITY_BUYOUT,
        display_name="Private Equity / Buyout",
        description=(
            "Alternative investment partnerships, fund structures, LPs, capital calls, "
            "and portfolio companies."
        ),
        domain_namespace="private_equity_buyout",
        standards_references=["ILPA Standards", "FIBO SEC/Funds", "Invest Europe"],
        concept_count=5,
        relationship_count=4,
        concepts=["Fund", "LimitedPartner", "CapitalCall", "Distribution", "PortfolioCompany"],
        dependencies=[AssetManagementPackId.CORE_SPINE],
        version="1.0.0",
    ),
    AssetManagementPackId.PRIVATE_CREDIT_DIRECT_LENDING: StarterPackMetadata(
        pack_id=AssetManagementPackId.PRIVATE_CREDIT_DIRECT_LENDING,
        display_name="Private Credit / Direct Lending",
        description=(
            "Bilateral and syndicated loan facilities, corporate borrowers, pledged collateral, "
            "and debt covenants."
        ),
        domain_namespace="private_credit_direct_lending",
        standards_references=["LSTA Standards", "FIBO FBC/DebtAndEquities", "LMA"],
        concept_count=4,
        relationship_count=4,
        concepts=["CreditFacility", "Borrower", "Collateral", "Covenant"],
        dependencies=[AssetManagementPackId.CORE_SPINE],
        version="1.0.0",
    ),
    AssetManagementPackId.CUSTODY_CLEARING: StarterPackMetadata(
        pack_id=AssetManagementPackId.CUSTODY_CLEARING,
        display_name="Custody / Clearing",
        description=(
            "Depository safekeeping, custody accounts, settlement instructions (SWIFT MT541/543), "
            "and corporate actions."
        ),
        domain_namespace="custody_clearing",
        standards_references=["ISO 15022", "ISO 20022", "SWIFT MT", "FIBO FBC/Settlement"],
        concept_count=4,
        relationship_count=3,
        concepts=["CustodyAccount", "Custodian", "SettlementInstruction", "CorporateAction"],
        dependencies=[AssetManagementPackId.CORE_SPINE],
        version="1.0.0",
    ),
    AssetManagementPackId.ALL_DOMAINS: StarterPackMetadata(
        pack_id=AssetManagementPackId.ALL_DOMAINS,
        display_name="1-Click All Domains Preset",
        description=(
            "Comprehensive composite ontology unifying Core Spine and all 6 Asset Management "
            "vertical packs (31 CBCs, 29 Links, 7 Reference Enums)."
        ),
        domain_namespace="asset_management",
        standards_references=[
            "FIBO",
            "ISO 17442",
            "ISO 13616",
            "ISO 6166",
            "FIX",
            "ILPA",
            "LSTA",
            "ISO 20022",
        ],
        concept_count=31,
        relationship_count=29,
        concepts=[
            "LegalEntity",
            "Account",
            "Agreement",
            "FinancialInstrument",
            "Transaction",
            "Document",
            "Order",
            "Execution",
            "Broker",
            "Exchange",
            "Bond",
            "YieldCurve",
            "CouponPayment",
            "RateBenchmark",
            "Portfolio",
            "Position",
            "CashBalance",
            "Valuation",
            "Fund",
            "LimitedPartner",
            "CapitalCall",
            "Distribution",
            "PortfolioCompany",
            "CreditFacility",
            "Borrower",
            "Collateral",
            "Covenant",
            "CustodyAccount",
            "Custodian",
            "SettlementInstruction",
            "CorporateAction",
        ],
        dependencies=[],
        version="1.0.0",
    ),
}


class AssetManagementStarterPackRegistry:
    """Registry providing discovery, metadata, and composition of starter packs."""

    @staticmethod
    def list_packs() -> list[StarterPackMetadata]:
        """Return metadata for all registered packs."""
        return list(PACK_REGISTRY_METADATA.values())

    @staticmethod
    def list_response() -> StarterPackListResponse:
        """Return API list response payload."""
        return StarterPackListResponse(
            packs=AssetManagementStarterPackRegistry.list_packs(),
            default_preset=AssetManagementPackId.ALL_DOMAINS,
        )

    @staticmethod
    def get_metadata(pack_id: AssetManagementPackId | str) -> StarterPackMetadata:
        """Retrieve metadata for a specific pack."""
        clean_id = AssetManagementPackId(str(pack_id).strip().lower())
        if clean_id not in PACK_REGISTRY_METADATA:
            raise KeyError(f"Unknown starter pack ID: {pack_id}")
        return PACK_REGISTRY_METADATA[clean_id]

    @staticmethod
    def load_pack(
        pack_id: AssetManagementPackId | str, *, include_spine: bool = True
    ) -> GraphAOntology:
        """Load a standalone or composite pack by identifier."""
        clean_id = AssetManagementPackId(str(pack_id).strip().lower())
        match clean_id:
            case AssetManagementPackId.CORE_SPINE:
                return get_core_spine_pack()
            case AssetManagementPackId.PUBLIC_EQUITIES_OMS:
                return get_public_equities_oms_pack(include_spine=include_spine)
            case AssetManagementPackId.FIXED_INCOME_RATES:
                return get_fixed_income_rates_pack(include_spine=include_spine)
            case AssetManagementPackId.MULTI_ASSET_IBOR:
                return get_multi_asset_ibor_pack(include_spine=include_spine)
            case AssetManagementPackId.PRIVATE_EQUITY_BUYOUT:
                return get_private_equity_buyout_pack(include_spine=include_spine)
            case AssetManagementPackId.PRIVATE_CREDIT_DIRECT_LENDING:
                return get_private_credit_direct_lending_pack(include_spine=include_spine)
            case AssetManagementPackId.CUSTODY_CLEARING:
                return get_custody_clearing_pack(include_spine=include_spine)
            case AssetManagementPackId.ALL_DOMAINS:
                return get_all_domains_pack()
            case _:
                raise ValueError(f"Unsupported starter pack ID: {pack_id}")

    @staticmethod
    def compose_packs(
        pack_ids: list[AssetManagementPackId | str],
        *,
        version: str = "1.0.0",
        domain_namespace: str = "asset_management",
    ) -> GraphAOntology:
        """Compose multiple packs into a unified GraphAOntology."""
        clean_ids = [str(p).strip().lower() for p in pack_ids]
        return compose_asset_management_ontology(
            version=version,
            domain_namespace=domain_namespace,
            include_packs=clean_ids,
        )


__all__ = [
    "PACK_REGISTRY_METADATA",
    "AssetManagementStarterPackRegistry",
]
