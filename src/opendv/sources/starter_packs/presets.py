"""V2 Asset Management domain starter pack presets, enumerations, and registry."""

from __future__ import annotations

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import sha256_jcs
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    GraphAOntology,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.wire_dtos_discovery import StarterPackPreview, StarterPackSummary
from opendv.sources.starter_packs.spine import (
    get_core_spine_concepts,
    get_core_spine_pack,
    get_core_spine_relationships,
)
from opendv.sources.starter_packs.verticals import VERTICAL_BUILDERS

STARTER_PACK_TITLES: dict[str, tuple[str, str]] = {
    "core_spine": ("core_spine", "Unified Core Enterprise Spine"),
    "public_equities_oms": ("public_equities_oms", "Public Equities / OMS"),
    "fixed_income_rates": ("fixed_income_rates", "Fixed Income / Rates"),
    "multi_asset_ibor": ("multi_asset_ibor", "Multi-Asset / IBOR"),
    "private_equity_buyout": ("private_equity_buyout", "Private Equity / Buyout"),
    "private_credit_direct_lending": (
        "private_credit_direct_lending",
        "Private Credit / Direct Lending",
    ),
    "custody_clearing": ("custody_clearing", "Custody / Clearing"),
    "all_domains": ("asset_management", "1-Click All Domains Preset"),
}

STARTER_PACK_IDS: tuple[str, ...] = tuple(STARTER_PACK_TITLES.keys())


def _enum(
    slug: str, name: str, table: str, codes: dict[str, str], desc: str
) -> OntologyEnumeration:
    return OntologyEnumeration(
        enum_uri=f"urn:opendv:enum:{slug}",
        enumeration_name=name,
        target_reference_table=table,
        codes=codes,
        description=desc,
    )


def get_all_domains_reference_enumerations() -> list[OntologyEnumeration]:
    """Return the 7 authoritative reference enumerations across Asset Management."""
    return [
        _enum(
            "currency_code",
            "CurrencyCode",
            "ref_currency_code",
            {
                "USD": "US Dollar",
                "EUR": "Euro",
                "GBP": "Pound Sterling",
                "JPY": "Japanese Yen",
                "CHF": "Swiss Franc",
                "CAD": "Canadian Dollar",
                "AUD": "Australian Dollar",
            },
            "ISO 4217 major currencies used in investment book of record.",
        ),
        _enum(
            "order_side",
            "OrderSide",
            "ref_order_side",
            {
                "BUY": "Buy",
                "SELL": "Sell",
                "SELL_SHORT": "Sell Short",
                "BUY_TO_COVER": "Buy To Cover",
            },
            "Trading order direction or side classification.",
        ),
        _enum(
            "execution_type",
            "ExecutionType",
            "ref_execution_type",
            {
                "FILL": "Fill",
                "PARTIAL_FILL": "Partial Fill",
                "CANCEL": "Cancel",
                "REJECT": "Reject",
            },
            "FIX protocol execution transaction status type.",
        ),
        _enum(
            "rate_benchmark_type",
            "RateBenchmarkType",
            "ref_rate_benchmark_type",
            {
                "SOFR": "Secured Overnight Financing Rate",
                "EURIBOR": "Euro Interbank Offered Rate",
                "SONIA": "Sterling Overnight Index Average",
                "TONA": "Tokyo Overnight Average Rate",
                "ESTR": "Euro Short-Term Rate",
            },
            "Global interest rate benchmark identifier codes.",
        ),
        _enum(
            "covenant_type",
            "CovenantType",
            "ref_covenant_type",
            {
                "MAX_LEVERAGE": "Maximum Leverage Ratio",
                "MIN_INTEREST_COVERAGE": "Minimum Interest Coverage",
                "MIN_LIQUIDITY": "Minimum Liquidity",
                "REPORTING_DELIVERY": "Reporting Delivery",
            },
            "Private credit maintenance and incurrence covenant test categories.",
        ),
        _enum(
            "settlement_status",
            "SettlementStatus",
            "ref_settlement_status",
            {
                "PENDING": "Pending",
                "SETTLED": "Settled",
                "FAILED": "Failed",
                "CANCELLED": "Cancelled",
            },
            "Post-trade clearing and custody settlement lifecycle status.",
        ),
        _enum(
            "corporate_action_type",
            "CorporateActionType",
            "ref_corporate_action_type",
            {
                "CASH_DIVIDEND": "Cash Dividend",
                "STOCK_DIVIDEND": "Stock Dividend",
                "STOCK_SPLIT": "Stock Split",
                "MERGER": "Merger",
                "SPINOFF": "Spinoff",
            },
            "Mandatory and voluntary corporate action event types.",
        ),
    ]


def _enums_by_uri() -> dict[str, OntologyEnumeration]:
    return {e.enum_uri: e for e in get_all_domains_reference_enumerations()}


def compose_starter_packs(
    pack_ids: list[str] | None = None,
    *,
    version: str = "1.0.0",
    domain_namespace: str = "asset_management",
) -> GraphAOntology:
    """Compose requested Asset Management packs into a single V2 GraphAOntology."""
    targets = [p.strip().lower() for p in (pack_ids or ["all_domains"])]
    if "all_domains" in targets:
        targets = ["core_spine", *VERTICAL_BUILDERS.keys()]

    for t in targets:
        if t not in STARTER_PACK_TITLES:
            raise OpenDVError(
                f"Unknown starter pack ID: '{t}'",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )

    concept_map: dict[str, CanonicalBusinessConcept] = {
        c.concept_uri: c for c in get_core_spine_concepts()
    }
    rel_map: dict[str, OntologyRelationship] = {
        r.relationship_uri: r for r in get_core_spine_relationships()
    }
    enum_catalog = _enums_by_uri()
    enum_map: dict[str, OntologyEnumeration] = {}

    for t in targets:
        if t == "core_spine":
            continue
        c_list, r_list, e_uris = VERTICAL_BUILDERS[t]()
        for c in c_list:
            concept_map[c.concept_uri] = c
        for r in r_list:
            rel_map[r.relationship_uri] = r
        for e_uri in e_uris:
            enum_map[e_uri] = enum_catalog[e_uri]

    if set(targets) >= set(VERTICAL_BUILDERS.keys()):
        for e in get_all_domains_reference_enumerations():
            enum_map[e.enum_uri] = e

    return GraphAOntology(
        ontology_version=version,
        domain_namespace=domain_namespace,
        concepts=list(concept_map.values()),
        relationships=list(rel_map.values()),
        enumerations=list(enum_map.values()),
    )


def get_starter_pack_ontology(pack_id: str, *, include_spine: bool = True) -> GraphAOntology:
    """Load one of the 8 V2 starter packs by identifier."""
    clean_id = pack_id.strip().lower()
    if clean_id not in STARTER_PACK_TITLES:
        raise OpenDVError(
            f"Unknown starter pack ID: '{pack_id}'",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    if clean_id == "core_spine":
        return get_core_spine_pack()
    if clean_id == "all_domains":
        return compose_starter_packs(["all_domains"], domain_namespace="asset_management")

    c_list, r_list, e_uris = VERTICAL_BUILDERS[clean_id]()
    concepts = [*get_core_spine_concepts(), *c_list] if include_spine else c_list
    rels = [*get_core_spine_relationships(), *r_list] if include_spine else r_list
    enum_catalog = _enums_by_uri()
    enums = [enum_catalog[u] for u in e_uris]
    ns, _ = STARTER_PACK_TITLES[clean_id]
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace=ns,
        concepts=concepts,
        relationships=rels,
        enumerations=enums,
    )


def list_starter_packs() -> list[StarterPackSummary]:
    """Return StarterPackSummary metadata for all 8 V2 starter packs."""
    summaries: list[StarterPackSummary] = []
    for pack_id, (ns, title) in STARTER_PACK_TITLES.items():
        ont = get_starter_pack_ontology(pack_id)
        summaries.append(
            StarterPackSummary(
                pack_id=pack_id,
                domain_namespace=ns,
                title=title,
                hub_count=len(ont.concepts),
            )
        )
    return summaries


def preview_starter_pack(pack_id: str) -> StarterPackPreview:
    """Return StarterPackPreview for a pack_id (RESOURCE_NOT_FOUND if unknown)."""
    ont = get_starter_pack_ontology(pack_id)
    return StarterPackPreview(pack_id=pack_id.strip().lower(), ontology=ont)


def compute_starter_pack_sha256(ontology: GraphAOntology) -> str:
    """Compute canonical RFC 8785 JCS SHA-256 over a V2 GraphAOntology."""
    return sha256_jcs(ontology.model_dump(mode="json"))


__all__ = [
    "STARTER_PACK_IDS",
    "STARTER_PACK_TITLES",
    "compose_starter_packs",
    "compute_starter_pack_sha256",
    "get_all_domains_reference_enumerations",
    "get_starter_pack_ontology",
    "list_starter_packs",
    "preview_starter_pack",
]
