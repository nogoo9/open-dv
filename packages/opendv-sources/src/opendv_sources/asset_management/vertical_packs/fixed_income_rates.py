"""Fixed Income / Rates Vertical Pack."""

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
            grain_semantics="Issuing legal entity issues bond debt security financial instrument.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_coupon",
            relationship_name="BondCoupon",
            target_link="lnk_bond_coupon",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:coupon_payment"],
            grain_semantics="Fixed income bond pays periodic coupon interest distribution payment.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_benchmark",
            relationship_name="BondBenchmark",
            target_link="lnk_bond_benchmark",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:rate_benchmark"],
            grain_semantics=(
                "Floating rate bond index references reference interest rate benchmark."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:bond_instrument",
            relationship_name="BondInstrument",
            target_link="lnk_bond_instrument",
            endpoints=["urn:opendv:concept:bond", "urn:opendv:concept:financial_instrument"],
            grain_semantics=(
                "Bond security specializes canonical financial instrument core spine concept."
            ),
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


__all__ = [
    "get_fixed_income_rates_concepts",
    "get_fixed_income_rates_pack",
    "get_fixed_income_rates_relationships",
]
