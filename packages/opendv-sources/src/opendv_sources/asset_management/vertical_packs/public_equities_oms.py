"""Public Equities / OMS Vertical Pack."""

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
            grain_semantics=(
                "Trading order targets execution in specific target financial instrument."
            ),
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:order_broker",
            relationship_name="OrderBroker",
            target_link="lnk_order_broker",
            endpoints=["urn:opendv:concept:order", "urn:opendv:concept:broker"],
            grain_semantics="Trading order is formally routed to broker for execution.",
        ),
        CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:execution_exchange",
            relationship_name="ExecutionExchange",
            target_link="lnk_execution_exchange",
            endpoints=["urn:opendv:concept:execution", "urn:opendv:concept:exchange"],
            grain_semantics=(
                "Trade execution transaction occurred at specific regulated exchange venue."
            ),
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


__all__ = [
    "get_public_equities_oms_concepts",
    "get_public_equities_oms_pack",
    "get_public_equities_oms_relationships",
]
