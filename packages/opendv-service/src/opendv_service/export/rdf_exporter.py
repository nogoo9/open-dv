"""Semantic RDF and multi-format ontology serialization (Task 5B.3 / Issue #217).

Provides formal OWL 2 / RDF exports using rdflib 7.x, conforming to
docs/20-ontology-studio-spec.md §7.1 and ADR-004:
- W3C Turtle (.ttl)
- W3C JSON-LD (.jsonld)
- W3C N-Triples (.nt)
- Canonical JSON (RFC 8785)
- Graph A YAML
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import yaml
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from opendv_contracts.jcs import dumps_jcs
from opendv_core.models.graph_a import GraphAOntology

DV2 = Namespace("https://open-dv.dev/ontology/core#")
FIBO = Namespace("https://spec.edmcouncil.org/fibo/ontology/")

SUPPORTED_EXPORT_FORMATS = {
    "turtle": "text/turtle",
    "ttl": "text/turtle",
    "jsonld": "application/ld+json",
    "json-ld": "application/ld+json",
    "ntriples": "application/n-triples",
    "nt": "application/n-triples",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "json": "application/json",
}

XSD_TYPE_MAP: dict[str, URIRef] = {
    "string": XSD.string,
    "str": XSD.string,
    "text": XSD.string,
    "integer": XSD.integer,
    "int": XSD.integer,
    "bigint": XSD.integer,
    "decimal": XSD.decimal,
    "numeric": XSD.decimal,
    "float": XSD.float,
    "double": XSD.double,
    "boolean": XSD.boolean,
    "bool": XSD.boolean,
    "date": XSD.date,
    "datetime": XSD.dateTime,
    "timestamp": XSD.dateTime,
}

SKOS_MAP: dict[str, URIRef] = {
    "exactmatch": SKOS.exactMatch,
    "exact": SKOS.exactMatch,
    "closematch": SKOS.closeMatch,
    "close": SKOS.closeMatch,
    "broadmatch": SKOS.broadMatch,
    "broad": SKOS.broadMatch,
    "narrowmatch": SKOS.narrowMatch,
    "narrow": SKOS.narrowMatch,
    "relatedmatch": SKOS.relatedMatch,
    "related": SKOS.relatedMatch,
}


def build_rdf_graph(ontology: GraphAOntology | dict[str, Any]) -> Graph:
    """Build an rdflib Graph expressing the GraphAOntology in OWL 2 DL semantics."""
    ont_obj = GraphAOntology.model_validate(ontology) if isinstance(ontology, dict) else ontology

    graph = Graph()
    graph.bind("dv2", DV2)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("skos", SKOS)
    graph.bind("xsd", XSD)

    # Declare DV2 annotation properties for OWL 2 DL conformance
    for ann in (
        DV2.enterpriseOwner,
        DV2.targetHub,
        DV2.elmArchetype,
        DV2.targetLink,
        DV2.targetReferenceTable,
        DV2.allowedCode,
    ):
        graph.add((ann, RDF.type, OWL.AnnotationProperty))

    # Ontology declaration
    ont_uri = URIRef(f"https://open-dv.dev/ontology/{ont_obj.domain_namespace}")
    version_uri = URIRef(
        f"https://open-dv.dev/ontology/{ont_obj.domain_namespace}/v{ont_obj.ontology_version}"
    )
    graph.add((ont_uri, RDF.type, OWL.Ontology))
    graph.add((ont_uri, OWL.versionInfo, Literal(ont_obj.ontology_version)))
    graph.add((ont_uri, OWL.versionIRI, version_uri))
    if ont_obj.description:
        graph.add((ont_uri, RDFS.comment, Literal(ont_obj.description)))
    if ont_obj.enterprise_owner:
        graph.add((ont_uri, DV2.enterpriseOwner, Literal(ont_obj.enterprise_owner)))

    # Canonical Business Concepts
    for concept in ont_obj.canonical_business_concepts:
        concept_uri = URIRef(concept.concept_uri)
        graph.add((concept_uri, RDF.type, OWL.Class))
        if concept.concept_name:
            graph.add((concept_uri, RDFS.label, Literal(concept.concept_name)))
        if concept.definition:
            graph.add((concept_uri, RDFS.comment, Literal(concept.definition)))
        if concept.target_hub:
            graph.add((concept_uri, DV2.targetHub, Literal(concept.target_hub)))
        if concept.elm_archetype:
            graph.add((concept_uri, DV2.elmArchetype, Literal(concept.elm_archetype)))
        if concept.parent_concept_uri:
            graph.add((concept_uri, RDFS.subClassOf, URIRef(concept.parent_concept_uri)))
        if concept.synonyms:
            for syn in concept.synonyms:
                graph.add((concept_uri, SKOS.altLabel, Literal(syn)))
        if concept.external_mappings:
            for mapping in concept.external_mappings:
                pred_norm = (
                    (mapping.predicate or "exactMatch").lower().replace("_", "").replace("-", "")
                )
                if ":" in pred_norm:
                    pred_norm = pred_norm.split(":", 1)[1]
                if pred_norm.startswith("skos"):
                    pred_norm = pred_norm[4:]
                skos_pred = SKOS_MAP.get(pred_norm, SKOS.relatedMatch)
                graph.add((concept_uri, skos_pred, URIRef(mapping.target_uri)))
        if concept.attributes:
            for attr in concept.attributes:
                clean_attr = urllib.parse.quote(attr.attribute_name.strip(), safe="")
                sep = "/" if "#" in str(concept_uri) else "#"
                attr_uri = URIRef(f"{concept.concept_uri}{sep}{clean_attr}")
                graph.add((attr_uri, RDF.type, OWL.DatatypeProperty))
                graph.add((attr_uri, RDFS.domain, concept_uri))
                graph.add((attr_uri, RDFS.label, Literal(attr.attribute_name)))
                if getattr(attr, "data_type", None):
                    dt_key = attr.data_type.lower().strip()
                    xsd_type = XSD_TYPE_MAP.get(dt_key, XSD.string)
                    graph.add((attr_uri, RDFS.range, xsd_type))

    # Canonical Relationships
    for rel in ont_obj.canonical_relationships:
        rel_uri = URIRef(rel.relationship_uri)
        graph.add((rel_uri, RDF.type, OWL.ObjectProperty))
        source_uri = getattr(rel, "source_concept_uri", None) or (
            rel.endpoints[0] if getattr(rel, "endpoints", None) else None
        )
        target_uri = getattr(rel, "target_concept_uri", None) or (
            rel.endpoints[1] if getattr(rel, "endpoints", None) and len(rel.endpoints) > 1 else None
        )
        if source_uri:
            graph.add((rel_uri, RDFS.domain, URIRef(source_uri)))
        if target_uri:
            graph.add((rel_uri, RDFS.range, URIRef(target_uri)))
        if rel.relationship_name:
            graph.add((rel_uri, RDFS.label, Literal(rel.relationship_name)))
        if rel.target_link:
            graph.add((rel_uri, DV2.targetLink, Literal(rel.target_link)))
        if rel.grain_semantics:
            graph.add((rel_uri, RDFS.comment, Literal(rel.grain_semantics)))

    # Reference Enumerations
    for enum in ont_obj.reference_enumerations:
        enum_uri = URIRef(enum.enum_uri)
        graph.add((enum_uri, RDF.type, RDFS.Datatype))
        graph.add((enum_uri, DV2.targetReferenceTable, Literal(enum.target_reference_table)))
        for code in enum.allowed_codes:
            graph.add((enum_uri, DV2.allowedCode, Literal(code)))

    return graph


def export_ontology(
    ontology: GraphAOntology | dict[str, Any],
    export_format: str = "turtle",
) -> tuple[str, str]:
    """Serialize GraphAOntology to the specified export format.

    Returns:
        tuple of (serialized_content_str, media_type_str)
    """
    normalized_format = export_format.lower().strip()
    if normalized_format not in SUPPORTED_EXPORT_FORMATS:
        raise ValueError(
            f"UNSUPPORTED_FORMAT: '{export_format}' is not supported. "
            f"Supported formats: {sorted(SUPPORTED_EXPORT_FORMATS.keys())}"
        )

    media_type = SUPPORTED_EXPORT_FORMATS[normalized_format]

    # Always normalize through GraphAOntology and mode="json", exclude_none=True
    ont_obj = GraphAOntology.model_validate(ontology) if isinstance(ontology, dict) else ontology
    raw_dict = ont_obj.model_dump(mode="json", exclude_none=True)

    if normalized_format in ("yaml", "yml"):
        return yaml.safe_dump(raw_dict, sort_keys=False), media_type

    if normalized_format == "json":
        res = dumps_jcs(raw_dict)
        return (res.decode("utf-8") if isinstance(res, bytes) else str(res)), media_type

    graph = build_rdf_graph(ont_obj)

    if normalized_format in ("turtle", "ttl"):
        return graph.serialize(format="turtle"), media_type
    if normalized_format in ("jsonld", "json-ld"):
        return graph.serialize(format="json-ld"), media_type
    if normalized_format in ("ntriples", "nt"):
        nt_raw = graph.serialize(format="nt")
        sorted_lines = (
            "\n".join(sorted(line for line in nt_raw.splitlines() if line.strip())) + "\n"
        )
        return sorted_lines, media_type

    raise ValueError(f"UNSUPPORTED_FORMAT: '{export_format}'")


__all__ = [
    "DV2",
    "FIBO",
    "SUPPORTED_EXPORT_FORMATS",
    "build_rdf_graph",
    "export_ontology",
]
