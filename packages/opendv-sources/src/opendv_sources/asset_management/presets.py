"""1-Click All Domains Composite Preset for Asset Management.

Unifies the Core Enterprise Spine and all 6 Vertical Starter Packs:
- 31 Canonical Business Concepts
- 29 Canonical Relationships
- 7 Reference Enumerations
Validates DAG connectivity and preserves SHA-256 canonical hash determinism.
"""

from __future__ import annotations

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_sources.asset_management.spine import (
    get_core_spine_concepts,
    get_core_spine_relationships,
)
from opendv_sources.asset_management.vertical_packs import (
    get_custody_clearing_concepts,
    get_custody_clearing_relationships,
    get_fixed_income_rates_concepts,
    get_fixed_income_rates_relationships,
    get_multi_asset_ibor_concepts,
    get_multi_asset_ibor_relationships,
    get_private_credit_direct_lending_concepts,
    get_private_credit_direct_lending_relationships,
    get_private_equity_buyout_concepts,
    get_private_equity_buyout_relationships,
    get_public_equities_oms_concepts,
    get_public_equities_oms_relationships,
)


def get_all_domains_reference_enumerations() -> list[ReferenceEnumeration]:
    """Return the 7 authoritative reference enumerations across AM domains."""
    return [
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:currency_code",
            target_reference_table="ref_currency_code",
            allowed_codes=["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD"],
            enumeration_name="CurrencyCode",
            description="ISO 4217 major currencies used in investment book of record.",
        ),
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:order_side",
            target_reference_table="ref_order_side",
            allowed_codes=["BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"],
            enumeration_name="OrderSide",
            description="Trading order direction or side classification.",
        ),
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:execution_type",
            target_reference_table="ref_execution_type",
            allowed_codes=["FILL", "PARTIAL_FILL", "CANCEL", "REJECT"],
            enumeration_name="ExecutionType",
            description="FIX protocol execution transaction status type.",
        ),
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:rate_benchmark_type",
            target_reference_table="ref_rate_benchmark_type",
            allowed_codes=["SOFR", "EURIBOR", "SONIA", "TONA", "ESTR"],
            enumeration_name="RateBenchmarkType",
            description="Global interest rate benchmark identifier codes.",
        ),
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:covenant_type",
            target_reference_table="ref_covenant_type",
            allowed_codes=[
                "MAX_LEVERAGE",
                "MIN_INTEREST_COVERAGE",
                "MIN_LIQUIDITY",
                "REPORTING_DELIVERY",
            ],
            enumeration_name="CovenantType",
            description="Private credit maintenance and incurrence covenant test categories.",
        ),
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:settlement_status",
            target_reference_table="ref_settlement_status",
            allowed_codes=["PENDING", "SETTLED", "FAILED", "CANCELLED"],
            enumeration_name="SettlementStatus",
            description="Post-trade clearing and custody settlement lifecycle status.",
        ),
        ReferenceEnumeration(
            enum_uri="urn:opendv:enum:corporate_action_type",
            target_reference_table="ref_corporate_action_type",
            allowed_codes=["CASH_DIVIDEND", "STOCK_DIVIDEND", "STOCK_SPLIT", "MERGER", "SPINOFF"],
            enumeration_name="CorporateActionType",
            description="Mandatory and voluntary corporate action event types.",
        ),
    ]


def compose_asset_management_ontology(
    *,
    version: str = "1.0.0",
    domain_namespace: str = "asset_management",
    include_packs: list[str] | None = None,
) -> GraphAOntology:
    """Compose requested Asset Management packs into a single valid GraphAOntology.

    If include_packs is None or includes 'all_domains', unifies spine + all 6 verticals.
    """
    all_known = {
        "core_spine",
        "public_equities_oms",
        "fixed_income_rates",
        "multi_asset_ibor",
        "private_equity_buyout",
        "private_credit_direct_lending",
        "custody_clearing",
        "all_domains",
    }
    targets = set(include_packs or ["all_domains"])
    if "all_domains" in targets:
        targets = all_known - {"all_domains"}

    # Always include core spine
    concept_map: dict[str, CanonicalBusinessConcept] = {}
    rel_map: dict[str, CanonicalRelationship] = {}

    for c in get_core_spine_concepts():
        concept_map[c.concept_uri] = c
    for r in get_core_spine_relationships():
        rel_map[r.relationship_uri] = r

    if "public_equities_oms" in targets:
        for c in get_public_equities_oms_concepts():
            concept_map[c.concept_uri] = c
        for r in get_public_equities_oms_relationships():
            rel_map[r.relationship_uri] = r

    if "fixed_income_rates" in targets:
        for c in get_fixed_income_rates_concepts():
            concept_map[c.concept_uri] = c
        for r in get_fixed_income_rates_relationships():
            rel_map[r.relationship_uri] = r

    if "multi_asset_ibor" in targets:
        for c in get_multi_asset_ibor_concepts():
            concept_map[c.concept_uri] = c
        for r in get_multi_asset_ibor_relationships():
            rel_map[r.relationship_uri] = r

    if "private_equity_buyout" in targets:
        for c in get_private_equity_buyout_concepts():
            concept_map[c.concept_uri] = c
        for r in get_private_equity_buyout_relationships():
            rel_map[r.relationship_uri] = r

    if "private_credit_direct_lending" in targets:
        for c in get_private_credit_direct_lending_concepts():
            concept_map[c.concept_uri] = c
        for r in get_private_credit_direct_lending_relationships():
            rel_map[r.relationship_uri] = r

    if "custody_clearing" in targets:
        for c in get_custody_clearing_concepts():
            concept_map[c.concept_uri] = c
        for r in get_custody_clearing_relationships():
            rel_map[r.relationship_uri] = r

    concepts = list(concept_map.values())
    relationships = list(rel_map.values())
    enums = get_all_domains_reference_enumerations()

    # Validate DAG endpoints
    known_concept_uris = set(concept_map.keys())
    for r in relationships:
        for ep in r.endpoints:
            if ep not in known_concept_uris:
                raise ValueError(
                    f"DANGLING_RELATIONSHIP_ENDPOINT: Relationship '{r.relationship_uri}' "
                    f"references endpoint '{ep}' which is not in active concepts"
                )

    ont = GraphAOntology(
        ontology_version=version,
        domain_namespace=domain_namespace,
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=enums,
    )
    return ont


def get_all_domains_pack() -> GraphAOntology:
    """Construct the authoritative 1-click All Domains composite GraphAOntology."""
    return compose_asset_management_ontology(
        version="1.0.0",
        domain_namespace="asset_management",
    )


def compute_pack_hash(pack: GraphAOntology) -> str:
    """Compute deterministic RFC 8785 canonical JCS SHA-256 for starter pack."""
    return compute_ontology_sha256(pack)


__all__ = [
    "compose_asset_management_ontology",
    "compute_pack_hash",
    "get_all_domains_pack",
    "get_all_domains_reference_enumerations",
]
