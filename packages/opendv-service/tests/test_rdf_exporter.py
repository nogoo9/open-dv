"""Unit tests for OWL 2 DL RDF Export (Issue #217 / Task 5B.3)."""

from __future__ import annotations

import json

import pytest
import yaml
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_service.export.rdf_exporter import (
    DV2,
    build_rdf_graph,
    export_ontology,
)


@pytest.fixture
def sample_ontology() -> GraphAOntology:
    """Construct a representative GraphAOntology fixture."""
    party = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:party",
        concept_name="Party",
        definition="A natural person or legal organization.",
        synonyms=["Entity", "Legal Person"],
        target_hub="hub_party",
        external_mappings=[
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/FinancialServiceProvider",
                predicate="skos:exactMatch",
                confidence=0.95,
            )
        ],
        attributes=[
            ConceptAttribute(
                attribute_name="party_name",
                data_type="string",
                description="The legal name of the party.",
            ),
            ConceptAttribute(
                attribute_name="total_exposure",
                data_type="decimal",
                description="Total exposure amount.",
            ),
        ],
    )

    customer = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:customer",
        concept_name="Customer",
        definition="A party that purchases goods or services.",
        target_hub="hub_customer",
        parent_concept_uri="urn:ontology:entity:party",
        synonyms=["Client"],
        attributes=[
            ConceptAttribute(
                attribute_name="credit_limit",
                data_type="decimal",
                description="Maximum allowable credit.",
            )
        ],
    )

    rel = CanonicalRelationship(
        relationship_uri="urn:ontology:relationship:party_guarantor",
        relationship_name="Guarantees",
        endpoints=["urn:ontology:entity:party", "urn:ontology:entity:customer"],
        target_link="lnk_party_guarantor",
        grain_semantics="A party guarantees a customer line of credit.",
    )

    status_enum = ReferenceEnumeration(
        enum_uri="urn:ontology:enum:customer_status",
        target_reference_table="ref_customer_status",
        enumeration_name="CustomerStatus",
        description="Lifecycle status of customer.",
        allowed_codes=["ACTIVE", "SUSPENDED", "CLOSED"],
        code_descriptions={
            "ACTIVE": "Operational customer",
            "SUSPENDED": "Temporarily locked",
            "CLOSED": "Terminated account",
        },
    )

    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="wealth_mgmt",
        enterprise_owner="Global Wealth",
        canonical_business_concepts=[party, customer],
        canonical_relationships=[rel],
        reference_enumerations=[status_enum],
    )


def test_build_rdf_graph_structure(sample_ontology: GraphAOntology) -> None:
    """Verify that build_rdf_graph constructs valid OWL 2 DL triples."""
    graph = build_rdf_graph(sample_ontology)
    assert isinstance(graph, Graph)

    # 1. Ontology declaration and versionIRI
    ont_uri = URIRef("https://open-dv.dev/ontology/wealth_mgmt")
    assert (ont_uri, RDF.type, OWL.Ontology) in graph
    assert (
        ont_uri,
        OWL.versionIRI,
        URIRef("https://open-dv.dev/ontology/wealth_mgmt/v1.0.0"),
    ) in graph

    # 2. Annotation properties declared
    assert (DV2.targetHub, RDF.type, OWL.AnnotationProperty) in graph
    assert (DV2.enterpriseOwner, RDF.type, OWL.AnnotationProperty) in graph

    # 3. Classes
    party_uri = URIRef("urn:ontology:entity:party")
    customer_uri = URIRef("urn:ontology:entity:customer")
    assert (party_uri, RDF.type, OWL.Class) in graph
    assert (customer_uri, RDF.type, OWL.Class) in graph

    # 4. Inheritance (subClassOf)
    assert (customer_uri, RDFS.subClassOf, party_uri) in graph

    # 5. External FIBO mapping via SKOS
    fibo_uri = URIRef(
        "https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/FinancialServiceProvider"
    )
    assert (party_uri, SKOS.exactMatch, fibo_uri) in graph

    # 6. Relationship mapped as ObjectProperty
    rel_uri = URIRef("urn:ontology:relationship:party_guarantor")
    assert (rel_uri, RDF.type, OWL.ObjectProperty) in graph
    assert (rel_uri, RDFS.domain, party_uri) in graph
    assert (rel_uri, RDFS.range, customer_uri) in graph

    # 7. Attributes mapped as DatatypeProperty with XSD range
    name_attr_uri = URIRef("urn:ontology:entity:party#party_name")
    assert (name_attr_uri, RDF.type, OWL.DatatypeProperty) in graph
    assert (name_attr_uri, RDFS.domain, party_uri) in graph
    assert (name_attr_uri, RDFS.range, XSD.string) in graph

    # Attribute with decimal XSD range
    exposure_attr_uri = URIRef("urn:ontology:entity:party#total_exposure")
    assert (exposure_attr_uri, RDF.type, OWL.DatatypeProperty) in graph
    assert (exposure_attr_uri, RDFS.range, XSD.decimal) in graph

    # 8. Enumeration codes
    enum_uri = URIRef("urn:ontology:enum:customer_status")
    assert (enum_uri, RDF.type, RDFS.Datatype) in graph
    assert (enum_uri, DV2.allowedCode, None) in graph


def test_export_ontology_turtle(sample_ontology: GraphAOntology) -> None:
    """Verify export_ontology generates parseable Turtle output."""
    content, media_type = export_ontology(sample_ontology, export_format="turtle")
    assert media_type == "text/turtle"
    assert "@prefix owl: <http://www.w3.org/2002/07/owl#> ." in content

    # Re-parse to verify syntax correctness
    g = Graph()
    g.parse(data=content, format="turtle")
    assert len(g) > 10


def test_export_ontology_jsonld(sample_ontology: GraphAOntology) -> None:
    """Verify export_ontology generates valid JSON-LD output."""
    content, media_type = export_ontology(sample_ontology, export_format="jsonld")
    assert media_type == "application/ld+json"

    parsed = json.loads(content)
    assert isinstance(parsed, (dict, list))

    g = Graph()
    g.parse(data=content, format="json-ld")
    assert len(g) > 10


def test_export_ontology_ntriples(sample_ontology: GraphAOntology) -> None:
    """Verify export_ontology generates valid N-Triples output with deterministic line ordering."""
    content, media_type = export_ontology(sample_ontology, export_format="ntriples")
    assert media_type == "application/n-triples"

    lines = [line for line in content.splitlines() if line.strip()]
    assert lines == sorted(lines)

    g = Graph()
    g.parse(data=content, format="nt")
    assert len(g) > 10


def test_export_ontology_yaml_and_json(sample_ontology: GraphAOntology) -> None:
    """Verify export_ontology formats for YAML and canonical RFC 8785 JSON (CRIT-07)."""
    import hashlib

    from opendv_core.ontology_hashing import compute_ontology_sha256

    yaml_content, yaml_mt = export_ontology(sample_ontology, export_format="yaml")
    assert yaml_mt == "application/yaml"
    assert "!!python" not in yaml_content
    loaded_yaml = yaml.safe_load(yaml_content)
    assert loaded_yaml["domain_namespace"] == "wealth_mgmt"

    expected_sha = compute_ontology_sha256(sample_ontology)
    json_content, json_mt = export_ontology(sample_ontology, export_format="json")
    assert json_mt == "application/json"
    assert hashlib.sha256(json_content.encode("utf-8")).hexdigest() == expected_sha

    for alias_fmt in ("jcs", "rfc8785"):
        jcs_content, jcs_mt = export_ontology(sample_ontology, export_format=alias_fmt)
        assert jcs_mt == "application/json"
        assert jcs_content == json_content

    # Verify dict input also normalizes through GraphAOntology to identical JCS SHA-256
    raw_dict = sample_ontology.model_dump()
    json_from_dict, _ = export_ontology(raw_dict, export_format="json")
    assert json_from_dict == json_content


def test_export_ontology_rejects_malformed_rfc3987_iri(sample_ontology: GraphAOntology) -> None:
    """CRIT-07: Malformed ExternalMapping.target_uri raises typed OpenDVError instead of crash."""
    from opendv_contracts.errors import ErrorCode, OpenDVError

    injected = sample_ontology.model_copy(deep=True)
    injected.canonical_business_concepts[0].external_mappings.append(
        ExternalMapping(
            scheme="fibo",
            target_uri="https://evil.example/a> <https://evil.example/p> <https://evil.example/o",
            predicate="skos:exactMatch",
            confidence=0.90,
        )
    )
    with pytest.raises(OpenDVError) as exc_info:
        export_ontology(injected, export_format="turtle")
    assert exc_info.value.error_code == ErrorCode.INVALID_WORKSPACE_ONTOLOGY


def test_export_ontology_full_predicates_nary_endpoints_and_owl2dl_annotations() -> None:
    """MAJ-09 & MIN-04: Full SKOS/OWL/RDFS IRIs, N-ary endpoints, and OWL 2 DL annotations."""
    c1 = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:fund",
        concept_name="Fund",
        target_hub="hub_fund",
        external_mappings=[
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/Fund",
                predicate="http://www.w3.org/2004/02/skos/core#exactMatch",
                confidence=0.95,
            ),
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Pool",
                predicate="owl:equivalentClass",
                confidence=0.90,
            ),
            ExternalMapping(
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Thing",
                predicate="rdfs:subClassOf",
                confidence=0.80,
            ),
        ],
    )
    c2 = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:investor",
        concept_name="Investor",
        target_hub="hub_investor",
    )
    c3 = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:advisor",
        concept_name="Advisor",
        target_hub="hub_advisor",
    )
    ternary_rel = CanonicalRelationship(
        relationship_uri="urn:ontology:relationship:commitment_deal",
        relationship_name="CommitmentDeal",
        target_link="lnk_commitment_deal",
        endpoints=[
            "urn:ontology:entity:fund",
            "urn:ontology:entity:investor",
            "urn:ontology:entity:advisor",
        ],
        grain_semantics="Ternary fund-investor-advisor commitment.",
    )
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="pe",
        canonical_business_concepts=[c1, c2, c3],
        canonical_relationships=[ternary_rel],
    )
    g = build_rdf_graph(ont)

    fund_u = URIRef("urn:ontology:entity:fund")
    assert (
        fund_u,
        SKOS.exactMatch,
        URIRef("https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Funds/Fund"),
    ) in g
    assert (
        fund_u,
        OWL.equivalentClass,
        URIRef("https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Pool"),
    ) in g
    assert (
        fund_u,
        RDFS.subClassOf,
        URIRef("https://spec.edmcouncil.org/fibo/ontology/FND/Thing"),
    ) in g
    assert (fund_u, SKOS.relatedMatch, None) not in g

    rel_u = URIRef("urn:ontology:relationship:commitment_deal")
    for ep in (
        "urn:ontology:entity:fund",
        "urn:ontology:entity:investor",
        "urn:ontology:entity:advisor",
    ):
        assert (rel_u, DV2.hasEndpoint, URIRef(ep)) in g

    for skos_prop in (
        SKOS.prefLabel,
        SKOS.altLabel,
        SKOS.definition,
        SKOS.exactMatch,
        SKOS.closeMatch,
    ):
        assert (skos_prop, RDF.type, OWL.AnnotationProperty) in g


def test_export_ontology_unsupported_format(sample_ontology: GraphAOntology) -> None:
    """Verify export_ontology raises ValueError on invalid formats."""
    with pytest.raises(ValueError, match="UNSUPPORTED_FORMAT"):
        export_ontology(sample_ontology, export_format="unsupported_fmt")
