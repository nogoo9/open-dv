"""Semantic RDF and multi-format ontology serialization (Task 5B.3 / Issue #217).

Provides formal OWL 2 / RDF exports using rdflib 7.x, conforming to
docs/20-ontology-studio-spec.md §7.1 and ADR-004:
- W3C Turtle (.ttl)
- W3C JSON-LD (.jsonld)
- W3C N-Triples (.nt)
- Canonical JSON (RFC 8785 JCS)
- Graph A YAML
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import canonicalize_graph_a_dict

DV2 = Namespace("https://open-dv.dev/ontology/core#")
OPENDV_NS = DV2
FIBO = Namespace("https://spec.edmcouncil.org/fibo/ontology/")

_RFC3987_IRI_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|\\^`\x00-\x1f\x7f]+\Z")

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
    "jcs": "application/json",
    "rfc8785": "application/json",
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

_FULL_PREDICATE_MAP: dict[str, URIRef] = {
    "http://www.w3.org/2004/02/skos/core#exactmatch": SKOS.exactMatch,
    "https://www.w3.org/2004/02/skos/core#exactmatch": SKOS.exactMatch,
    "skos:exactmatch": SKOS.exactMatch,
    "http://www.w3.org/2004/02/skos/core#closematch": SKOS.closeMatch,
    "https://www.w3.org/2004/02/skos/core#closematch": SKOS.closeMatch,
    "skos:closematch": SKOS.closeMatch,
    "http://www.w3.org/2004/02/skos/core#broadmatch": SKOS.broadMatch,
    "https://www.w3.org/2004/02/skos/core#broadmatch": SKOS.broadMatch,
    "skos:broadmatch": SKOS.broadMatch,
    "http://www.w3.org/2004/02/skos/core#narrowmatch": SKOS.narrowMatch,
    "https://www.w3.org/2004/02/skos/core#narrowmatch": SKOS.narrowMatch,
    "skos:narrowmatch": SKOS.narrowMatch,
    "http://www.w3.org/2004/02/skos/core#relatedmatch": SKOS.relatedMatch,
    "https://www.w3.org/2004/02/skos/core#relatedmatch": SKOS.relatedMatch,
    "skos:relatedmatch": SKOS.relatedMatch,
    "http://www.w3.org/2002/07/owl#equivalentclass": OWL.equivalentClass,
    "https://www.w3.org/2002/07/owl#equivalentclass": OWL.equivalentClass,
    "owl:equivalentclass": OWL.equivalentClass,
    "equivalentclass": OWL.equivalentClass,
    "http://www.w3.org/2000/01/rdf-schema#subclassof": RDFS.subClassOf,
    "https://www.w3.org/2000/01/rdf-schema#subclassof": RDFS.subClassOf,
    "rdfs:subclassof": RDFS.subClassOf,
    "subclassof": RDFS.subClassOf,
    "http://www.w3.org/2002/07/owl#sameas": OWL.sameAs,
    "https://www.w3.org/2002/07/owl#sameas": OWL.sameAs,
    "owl:sameas": OWL.sameAs,
    "sameas": OWL.sameAs,
}


class UnsupportedExportFormatError(OpenDVError, ValueError):
    """Raised when an unsupported ontology export format is requested."""

    error_code: ErrorCode = ErrorCode.UNSUPPORTED_FORMAT


def _safe_uriref(iri: str, *, field_name: str = "IRI") -> URIRef:
    """Validate `iri` against RFC 3987 syntax before constructing an rdflib `URIRef`."""
    if not isinstance(iri, str) or not iri.strip():
        raise OpenDVError(
            f"INVALID_WORKSPACE_ONTOLOGY: Empty {field_name} cannot be serialized as RDF IRI.",
            error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
        )
    cleaned = iri.strip()
    if not _RFC3987_IRI_RE.match(cleaned):
        raise OpenDVError(
            f"INVALID_WORKSPACE_ONTOLOGY: Malformed {field_name} '{iri}' violates RFC 3987 "
            "IRI syntax.",
            error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
        )
    parts = urllib.parse.urlsplit(cleaned)
    if parts.scheme.lower() in ("http", "https") and not parts.netloc:
        raise OpenDVError(
            f"INVALID_WORKSPACE_ONTOLOGY: HTTP(S) {field_name} '{iri}' is missing host authority.",
            error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
        )
    return URIRef(cleaned)


def _resolve_mapping_predicate(raw_predicate: str | None) -> URIRef:
    """Resolve SKOS, OWL, RDFS, CURIE, or full IRI predicates without silent corruption."""
    if not raw_predicate or not raw_predicate.strip():
        return SKOS.exactMatch

    cleaned = raw_predicate.strip()
    key = cleaned.lower().replace("_", "").replace("-", "")
    if key in _FULL_PREDICATE_MAP:
        return _FULL_PREDICATE_MAP[key]
    if key in SKOS_MAP:
        return SKOS_MAP[key]

    if cleaned.lower().startswith("skos:"):
        local = key[5:]
        if local in SKOS_MAP:
            return SKOS_MAP[local]

    if cleaned.startswith(("http://", "https://", "urn:")):
        return _safe_uriref(cleaned, field_name="external_mapping.predicate")

    return SKOS.relatedMatch


def _coerce_ontology_model(ontology: GraphAOntology | BaseModel | dict[str, Any]) -> GraphAOntology:
    if isinstance(ontology, GraphAOntology):
        return ontology
    try:
        payload = (
            ontology.model_dump(mode="json", exclude_none=True)
            if isinstance(ontology, BaseModel)
            else ontology
        )
        return GraphAOntology.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise OpenDVError(
            f"INVALID_WORKSPACE_ONTOLOGY: Ontology payload failed schema validation: {exc}",
            error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
        ) from exc


def build_rdf_graph(ontology: GraphAOntology | BaseModel | dict[str, Any]) -> Graph:
    """Build an rdflib Graph expressing the GraphAOntology in OWL 2 DL semantics."""
    ont_obj = _coerce_ontology_model(ontology)

    graph = Graph()
    graph.bind("dv2", DV2)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("skos", SKOS)
    graph.bind("xsd", XSD)

    # Declare DV2 and SKOS annotation properties for strict OWL 2 DL conformance (MIN-04)
    for ann in (
        DV2.enterpriseOwner,
        DV2.targetHub,
        DV2.elmArchetype,
        DV2.domainNamespace,
        DV2.isOptional,
        DV2.hasMappingDetail,
        DV2.mappingScheme,
        DV2.mappingTarget,
        DV2.mappingPredicate,
        DV2.mappingConfidence,
        DV2.targetLink,
        DV2.hasEndpoint,
        DV2.hasEndpointDetail,
        DV2.endpointRef,
        DV2.hasSynonymDetail,
        DV2.orderIndex,
        DV2.groundingOrigin,
        DV2.rawDataType,
        DV2.cardinality,
        DV2.relationshipDescription,
        DV2.mappingPinned,
        DV2.mappingProvenance,
        DV2.standardsReference,
        DV2.decisionRef,
        DV2.parentSha256,
        DV2.targetReferenceTable,
        DV2.allowedCode,
        DV2.hasCodeDetail,
        DV2.codeValue,
        SKOS.prefLabel,
        SKOS.altLabel,
        SKOS.definition,
        SKOS.exactMatch,
        SKOS.closeMatch,
        SKOS.broadMatch,
        SKOS.narrowMatch,
        SKOS.relatedMatch,
    ):
        graph.add((ann, RDF.type, OWL.AnnotationProperty))

    # Ontology declaration
    ont_uri = _safe_uriref(
        f"https://open-dv.dev/ontology/{ont_obj.domain_namespace}",
        field_name="domain_namespace",
    )
    version_uri = _safe_uriref(
        f"https://open-dv.dev/ontology/{ont_obj.domain_namespace}/v{ont_obj.ontology_version}",
        field_name="ontology_version",
    )
    graph.add((ont_uri, RDF.type, OWL.Ontology))
    graph.add((ont_uri, OWL.versionInfo, Literal(ont_obj.ontology_version)))
    graph.add((ont_uri, OWL.versionIRI, version_uri))
    graph.add((ont_uri, DV2.domainNamespace, Literal(ont_obj.domain_namespace)))
    if ont_obj.description:
        graph.add((ont_uri, RDFS.comment, Literal(ont_obj.description)))
    if ont_obj.enterprise_owner:
        graph.add((ont_uri, DV2.enterpriseOwner, Literal(ont_obj.enterprise_owner)))
    if ont_obj.parent_sha256:
        graph.add((ont_uri, DV2.parentSha256, Literal(ont_obj.parent_sha256)))
    for sref in ont_obj.standards_references:
        graph.add((ont_uri, DV2.standardsReference, Literal(sref)))
    for dref in ont_obj.decision_refs:
        graph.add((ont_uri, DV2.decisionRef, Literal(dref)))

    # Canonical Business Concepts
    for concept in ont_obj.canonical_business_concepts:
        concept_uri = _safe_uriref(concept.concept_uri, field_name="concept_uri")
        base_sep = "/" if "#" in str(concept_uri) else "#"
        graph.add((concept_uri, RDF.type, OWL.Class))
        if concept.concept_name:
            graph.add((concept_uri, RDFS.label, Literal(concept.concept_name)))
            graph.add((concept_uri, SKOS.prefLabel, Literal(concept.concept_name)))
        if concept.definition:
            graph.add((concept_uri, RDFS.comment, Literal(concept.definition)))
            graph.add((concept_uri, SKOS.definition, Literal(concept.definition)))
        if concept.target_hub:
            graph.add((concept_uri, DV2.targetHub, Literal(concept.target_hub)))
        if concept.elm_archetype:
            graph.add((concept_uri, DV2.elmArchetype, Literal(concept.elm_archetype)))
        if concept.grounding_origin:
            graph.add((concept_uri, DV2.groundingOrigin, Literal(concept.grounding_origin)))
        if concept.parent_concept_uri:
            parent_uri = _safe_uriref(concept.parent_concept_uri, field_name="parent_concept_uri")
            graph.add((concept_uri, RDFS.subClassOf, parent_uri))
        if concept.synonyms:
            for idx, syn in enumerate(concept.synonyms):
                graph.add((concept_uri, SKOS.altLabel, Literal(syn)))
                syn_uri = _safe_uriref(
                    f"{concept.concept_uri.strip()}{base_sep}synonym_{idx}",
                    field_name="synonym.detail_uri",
                )
                graph.add((concept_uri, DV2.hasSynonymDetail, syn_uri))
                graph.add((syn_uri, DV2.orderIndex, Literal(idx)))
                graph.add((syn_uri, SKOS.altLabel, Literal(syn)))
        if concept.external_mappings:
            for idx, mapping in enumerate(concept.external_mappings):
                target_ref = _safe_uriref(
                    mapping.target_uri, field_name="external_mapping.target_uri"
                )
                pred_ref = _resolve_mapping_predicate(mapping.predicate)
                if pred_ref in (OWL.equivalentClass, RDFS.subClassOf):
                    graph.add((target_ref, RDF.type, OWL.Class))
                elif pred_ref not in (
                    SKOS.exactMatch,
                    SKOS.closeMatch,
                    SKOS.broadMatch,
                    SKOS.narrowMatch,
                    SKOS.relatedMatch,
                    OWL.sameAs,
                ):
                    graph.add((pred_ref, RDF.type, OWL.AnnotationProperty))
                graph.add((concept_uri, pred_ref, target_ref))
                map_uri = _safe_uriref(
                    f"{concept.concept_uri.strip()}{base_sep}mapping_{idx}",
                    field_name="external_mapping.detail_uri",
                )
                graph.add((concept_uri, DV2.hasMappingDetail, map_uri))
                graph.add((map_uri, DV2.mappingScheme, Literal(mapping.scheme)))
                graph.add((map_uri, DV2.mappingTarget, Literal(mapping.target_uri)))
                if mapping.predicate is not None:
                    graph.add((map_uri, DV2.mappingPredicate, Literal(mapping.predicate)))
                if mapping.confidence is not None:
                    graph.add((map_uri, DV2.mappingConfidence, Literal(float(mapping.confidence))))
                if mapping.pinned is not None:
                    graph.add((map_uri, DV2.mappingPinned, Literal(bool(mapping.pinned))))
                if mapping.provenance is not None:
                    graph.add((map_uri, DV2.mappingProvenance, Literal(mapping.provenance)))
        if concept.attributes:
            for idx, attr in enumerate(concept.attributes):
                clean_attr = urllib.parse.quote(attr.attribute_name.strip(), safe="")
                attr_uri = _safe_uriref(
                    f"{concept.concept_uri.strip()}{base_sep}{clean_attr}",
                    field_name="attribute_uri",
                )
                graph.add((attr_uri, RDF.type, OWL.DatatypeProperty))
                graph.add((attr_uri, RDFS.domain, concept_uri))
                graph.add((attr_uri, RDFS.label, Literal(attr.attribute_name)))
                graph.add((attr_uri, DV2.orderIndex, Literal(idx)))
                if getattr(attr, "data_type", None):
                    dt_key = attr.data_type.lower().strip()
                    xsd_type = XSD_TYPE_MAP.get(dt_key, XSD.string)
                    graph.add((attr_uri, RDFS.range, xsd_type))
                    graph.add((attr_uri, DV2.rawDataType, Literal(attr.data_type)))
                if getattr(attr, "is_optional", None) is not None:
                    graph.add((attr_uri, DV2.isOptional, Literal(bool(attr.is_optional))))
                if getattr(attr, "description", None):
                    graph.add((attr_uri, RDFS.comment, Literal(attr.description)))

    # Canonical Relationships (including ordered N-ary endpoints via DV2.hasEndpointDetail)
    for rel in ont_obj.canonical_relationships:
        rel_uri = _safe_uriref(rel.relationship_uri, field_name="relationship_uri")
        rel_sep = "/" if "#" in str(rel_uri) else "#"
        graph.add((rel_uri, RDF.type, OWL.ObjectProperty))
        endpoints = list(getattr(rel, "endpoints", None) or [])
        source_uri = getattr(rel, "source_concept_uri", None) or (
            endpoints[0] if endpoints else None
        )
        target_uri = getattr(rel, "target_concept_uri", None) or (
            endpoints[1] if len(endpoints) > 1 else None
        )
        if source_uri:
            graph.add(
                (rel_uri, RDFS.domain, _safe_uriref(source_uri, field_name="relationship.domain"))
            )
        if target_uri:
            graph.add(
                (rel_uri, RDFS.range, _safe_uriref(target_uri, field_name="relationship.range"))
            )
        for idx, ep in enumerate(endpoints):
            ep_ref = _safe_uriref(ep, field_name="relationship.endpoint")
            graph.add((rel_uri, DV2.hasEndpoint, ep_ref))
            ep_detail = _safe_uriref(
                f"{rel.relationship_uri.strip()}{rel_sep}endpoint_{idx}",
                field_name="relationship.endpoint_detail",
            )
            graph.add((rel_uri, DV2.hasEndpointDetail, ep_detail))
            graph.add((ep_detail, DV2.orderIndex, Literal(idx)))
            graph.add((ep_detail, DV2.endpointRef, ep_ref))
        if rel.relationship_name:
            graph.add((rel_uri, RDFS.label, Literal(rel.relationship_name)))
        if rel.target_link:
            graph.add((rel_uri, DV2.targetLink, Literal(rel.target_link)))
        if rel.grain_semantics:
            graph.add((rel_uri, RDFS.comment, Literal(rel.grain_semantics)))
        if rel.cardinality is not None:
            graph.add((rel_uri, DV2.cardinality, Literal(rel.cardinality)))
        if rel.description is not None:
            graph.add((rel_uri, DV2.relationshipDescription, Literal(rel.description)))

    # Reference Enumerations
    for enum in ont_obj.reference_enumerations:
        enum_uri = _safe_uriref(enum.enum_uri, field_name="enum_uri")
        enum_sep = "/" if "#" in str(enum_uri) else "#"
        graph.add((enum_uri, RDF.type, RDFS.Datatype))
        graph.add((enum_uri, DV2.targetReferenceTable, Literal(enum.target_reference_table)))
        if enum.enumeration_name is not None:
            graph.add((enum_uri, RDFS.label, Literal(enum.enumeration_name)))
        if enum.description is not None:
            graph.add((enum_uri, RDFS.comment, Literal(enum.description)))
        code_descs = enum.code_descriptions or {}
        for idx, code in enumerate(enum.allowed_codes):
            graph.add((enum_uri, DV2.allowedCode, Literal(code)))
            code_uri = _safe_uriref(
                f"{enum.enum_uri.strip()}{enum_sep}code_{idx}",
                field_name="enum.code_detail",
            )
            graph.add((enum_uri, DV2.hasCodeDetail, code_uri))
            graph.add((code_uri, DV2.orderIndex, Literal(idx)))
            graph.add((code_uri, DV2.codeValue, Literal(code)))
            if code in code_descs:
                graph.add((code_uri, RDFS.comment, Literal(code_descs[code])))

    return graph


def export_ontology(
    ontology: GraphAOntology | BaseModel | dict[str, Any],
    export_format: str = "turtle",
) -> tuple[str, str]:
    """Serialize GraphAOntology to the specified export format.

    Returns:
        tuple of (serialized_content_str, media_type_str)
    """
    normalized_format = export_format.lower().strip()
    if normalized_format not in SUPPORTED_EXPORT_FORMATS:
        raise UnsupportedExportFormatError(
            f"UNSUPPORTED_FORMAT: '{export_format}' is not supported. "
            f"Supported formats: {sorted(SUPPORTED_EXPORT_FORMATS.keys())}",
            error_code=ErrorCode.UNSUPPORTED_FORMAT,
        )

    media_type = SUPPORTED_EXPORT_FORMATS[normalized_format]
    ont_obj = _coerce_ontology_model(ontology)

    if normalized_format in ("yaml", "yml"):
        canonical_dict = canonicalize_graph_a_dict(ont_obj)
        return yaml.safe_dump(canonical_dict, sort_keys=False), media_type

    if normalized_format in ("json", "jcs", "rfc8785"):
        canonical_dict = canonicalize_graph_a_dict(ont_obj)
        return dumps_jcs_bytes(canonical_dict).decode("utf-8"), media_type

    graph = build_rdf_graph(ont_obj)

    if normalized_format in ("turtle", "ttl"):
        return graph.serialize(format="turtle"), media_type
    if normalized_format in ("jsonld", "json-ld"):
        return graph.serialize(format="json-ld"), media_type
    if normalized_format in ("ntriples", "nt"):
        nt_raw = graph.serialize(format="nt")
        lines = sorted(line for line in nt_raw.splitlines() if line.strip())
        sorted_lines = ("\n".join(lines) + "\n") if lines else ""
        return sorted_lines, media_type

    raise UnsupportedExportFormatError(
        f"UNSUPPORTED_FORMAT: '{export_format}'",
        error_code=ErrorCode.UNSUPPORTED_FORMAT,
    )


__all__ = [
    "DV2",
    "FIBO",
    "OPENDV_NS",
    "SUPPORTED_EXPORT_FORMATS",
    "UnsupportedExportFormatError",
    "build_rdf_graph",
    "export_ontology",
]
