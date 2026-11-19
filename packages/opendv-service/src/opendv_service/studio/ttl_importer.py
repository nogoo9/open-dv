"""Ontology importer supporting YAML, JSON, and lossless RDF Turtle (.ttl) via rdflib."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

import yaml
from pydantic import ValidationError
from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256

DV2 = Namespace("https://open-dv.dev/ontology/core#")

_XSD_TO_GRAPH_A_TYPE: dict[str, str] = {
    str(XSD.string): "string",
    str(XSD.integer): "integer",
    str(XSD.int): "integer",
    str(XSD.long): "integer",
    str(XSD.decimal): "decimal",
    str(XSD.float): "float",
    str(XSD.double): "double",
    str(XSD.boolean): "boolean",
    str(XSD.date): "date",
    str(XSD.dateTime): "timestamp",
    str(XSD.time): "time",
}

_PRED_TUPLES = (
    (SKOS.exactMatch, "skos:exactMatch"),
    (SKOS.closeMatch, "skos:closeMatch"),
    (SKOS.broadMatch, "skos:broadMatch"),
    (SKOS.narrowMatch, "skos:narrowMatch"),
    (SKOS.relatedMatch, "skos:relatedMatch"),
    (OWL.equivalentClass, "owl:equivalentClass"),
    (RDFS.subClassOf, "rdfs:subClassOf"),
    (OWL.sameAs, "owl:sameAs"),
)

_CONCEPT_PREFIXES = ("urn:ontology:", "urn:opendv:")


def _local_slug(uri: str) -> str:
    if ":" in uri and "#" not in uri:
        return uri.rsplit(":", 1)[-1]
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rsplit("/", 1)[-1]


def _parse_turtle_ontology(content: str, domain_namespace: str | None) -> GraphAOntology:
    g = Graph()
    try:
        g.parse(data=content, format="turtle")
    except Exception as exc:
        raise OpenDVError(
            f"Malformed Turtle (.ttl) ontology payload: {exc}",
            error_code=ErrorCode.INPUT_VALIDATION_ERROR,
        ) from exc

    ont_version = "1.0.0"
    inferred_ns = domain_namespace
    ont_desc: str | None = None
    enterprise_owner: str | None = None
    parent_sha256: str | None = None
    standards_refs: list[str] = []
    decision_refs: list[str] = []

    for ont_sub in sorted(g.subjects(RDF.type, OWL.Ontology), key=str):
        v_lit = g.value(ont_sub, OWL.versionInfo)
        if v_lit is not None and str(v_lit).strip():
            ont_version = str(v_lit).strip()
        ns_lit = g.value(ont_sub, DV2.domainNamespace)
        if not inferred_ns and ns_lit is not None and str(ns_lit).strip():
            inferred_ns = str(ns_lit).strip()
        elif not inferred_ns and "/ontology/" in str(ont_sub):
            tail = str(ont_sub).split("/ontology/", 1)[1].strip("/")
            if tail:
                inferred_ns = tail.split("/", 1)[0]
        desc_lit = g.value(ont_sub, RDFS.comment)
        if desc_lit is not None:
            ont_desc = str(desc_lit)
        owner_lit = g.value(ont_sub, DV2.enterpriseOwner)
        if owner_lit is not None:
            enterprise_owner = str(owner_lit)
        psha_lit = g.value(ont_sub, DV2.parentSha256)
        if psha_lit is not None and str(psha_lit).strip():
            parent_sha256 = str(psha_lit).strip()
        standards_refs = sorted({str(s) for s in g.objects(ont_sub, DV2.standardsReference)})
        decision_refs = sorted({str(d) for d in g.objects(ont_sub, DV2.decisionRef)})
        break

    def _order_key(node: Any) -> tuple[int, str]:
        o_lit = g.value(node, DV2.orderIndex)
        if o_lit is not None:
            return (int(o_lit.toPython()), str(node))
        tail = str(node).rsplit("_", 1)[-1]
        return (int(tail) if tail.isdigit() else 999999, str(node))

    concepts: list[CanonicalBusinessConcept] = []
    class_subjects = sorted(
        {s for s in g.subjects(RDF.type, OWL.Class) if str(s).startswith(_CONCEPT_PREFIXES)},
        key=str,
    )
    for c_sub in class_subjects:
        c_uri = str(c_sub)
        label_lit = g.value(c_sub, SKOS.prefLabel) or g.value(c_sub, RDFS.label)
        c_name = (
            str(label_lit).strip() if label_lit else _local_slug(c_uri).replace("_", " ").title()
        )
        def_lit = g.value(c_sub, SKOS.definition) or g.value(c_sub, RDFS.comment)
        c_def = str(def_lit) if def_lit is not None else f"{c_name} canonical business concept."
        hub_lit = g.value(c_sub, DV2.targetHub)
        target_hub = str(hub_lit).strip() if hub_lit else f"hub_{_local_slug(c_uri)}"
        arch_lit = g.value(c_sub, DV2.elmArchetype)
        elm_archetype = str(arch_lit).strip() if arch_lit else None
        origin_lit = g.value(c_sub, DV2.groundingOrigin)
        grounding_origin = str(origin_lit).strip() if origin_lit else None
        parent_uri: str | None = None
        for p_obj in g.objects(c_sub, RDFS.subClassOf):
            if str(p_obj).startswith(_CONCEPT_PREFIXES):
                parent_uri = str(p_obj)
                break
        syn_details = sorted(g.objects(c_sub, DV2.hasSynonymDetail), key=_order_key)
        if syn_details:
            synonyms = [
                str(val)
                for s_node in syn_details
                if (val := g.value(s_node, SKOS.altLabel)) is not None
            ]
        else:
            synonyms = sorted({str(s) for s in g.objects(c_sub, SKOS.altLabel)})

        # Attributes linked via RDFS.domain, sorted by DV2.orderIndex when present
        attrs: list[ConceptAttribute] = []
        attr_subjects = sorted(g.subjects(RDFS.domain, c_sub), key=_order_key)
        for a_sub in attr_subjects:
            if (a_sub, RDF.type, OWL.DatatypeProperty) not in g:
                continue
            a_lbl = g.value(a_sub, RDFS.label)
            if a_lbl is not None:
                a_name = str(a_lbl).strip()
            else:
                raw_tail = str(a_sub).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                a_name = urllib.parse.unquote(raw_tail)
            raw_dt_lit = g.value(a_sub, DV2.rawDataType)
            rng = g.value(a_sub, RDFS.range)
            if raw_dt_lit is not None and str(raw_dt_lit).strip():
                dt_str = str(raw_dt_lit).strip()
            else:
                dt_str = (
                    _XSD_TO_GRAPH_A_TYPE.get(str(rng), "string") if rng is not None else "string"
                )
            opt_lit = g.value(a_sub, DV2.isOptional)
            is_opt = bool(opt_lit.toPython()) if opt_lit is not None else True
            a_desc_lit = g.value(a_sub, RDFS.comment)
            attrs.append(
                ConceptAttribute(
                    attribute_name=a_name,
                    data_type=dt_str,
                    is_optional=is_opt,
                    description=str(a_desc_lit) if a_desc_lit is not None else None,
                )
            )

        # External mappings: prefer lossless DV2.hasMappingDetail nodes, fallback to SKOS/OWL triples
        mappings: list[ExternalMapping] = []
        detail_nodes = sorted(g.objects(c_sub, DV2.hasMappingDetail), key=_order_key)
        if detail_nodes:
            for d_node in detail_nodes:
                scheme_l = g.value(d_node, DV2.mappingScheme)
                target_l = g.value(d_node, DV2.mappingTarget)
                pred_l = g.value(d_node, DV2.mappingPredicate)
                conf_l = g.value(d_node, DV2.mappingConfidence)
                pin_l = g.value(d_node, DV2.mappingPinned)
                prov_l = g.value(d_node, DV2.mappingProvenance)
                if target_l is None:
                    continue
                mappings.append(
                    ExternalMapping(
                        scheme=str(scheme_l or "fibo"),
                        target_uri=str(target_l),
                        predicate=str(pred_l) if pred_l is not None else None,
                        confidence=float(conf_l.toPython()) if conf_l is not None else None,
                        pinned=bool(pin_l.toPython()) if pin_l is not None else None,
                        provenance=str(prov_l) if prov_l is not None else None,
                    )
                )
        else:
            for pred_ref, short_pred in _PRED_TUPLES:
                for t_obj in g.objects(c_sub, pred_ref):
                    if str(t_obj).startswith(_CONCEPT_PREFIXES):
                        continue
                    scheme = "fibo" if "edmcouncil.org/fibo" in str(t_obj) else "external"
                    mappings.append(
                        ExternalMapping(
                            scheme=scheme,
                            target_uri=str(t_obj),
                            predicate=short_pred,
                            confidence=0.85,
                        )
                    )

        concept_kwargs: dict[str, Any] = {
            "concept_uri": c_uri,
            "concept_name": c_name,
            "target_hub": target_hub,
            "definition": c_def,
            "attributes": attrs,
            "external_mappings": mappings,
        }
        if elm_archetype is not None:
            concept_kwargs["elm_archetype"] = elm_archetype
        if grounding_origin is not None:
            concept_kwargs["grounding_origin"] = grounding_origin
        if parent_uri is not None:
            concept_kwargs["parent_concept_uri"] = parent_uri
        if synonyms:
            concept_kwargs["synonyms"] = synonyms
        concepts.append(CanonicalBusinessConcept(**concept_kwargs))

    relationships: list[CanonicalRelationship] = []
    rel_subjects = sorted(
        {
            s
            for s in g.subjects(RDF.type, OWL.ObjectProperty)
            if str(s).startswith(_CONCEPT_PREFIXES)
        },
        key=str,
    )
    for r_sub in rel_subjects:
        r_uri = str(r_sub)
        r_lbl = g.value(r_sub, RDFS.label)
        r_name = str(r_lbl).strip() if r_lbl else _local_slug(r_uri)
        ep_details = sorted(g.objects(r_sub, DV2.hasEndpointDetail), key=_order_key)
        if ep_details:
            endpoints = [
                str(ref)
                for ep_node in ep_details
                if (ref := g.value(ep_node, DV2.endpointRef)) is not None
            ]
        else:
            endpoints = sorted({str(e) for e in g.objects(r_sub, DV2.hasEndpoint)})
        if not endpoints:
            dom = g.value(r_sub, RDFS.domain)
            rng = g.value(r_sub, RDFS.range)
            if dom and rng:
                endpoints = [str(dom), str(rng)]
        tlink_lit = g.value(r_sub, DV2.targetLink)
        tlink = str(tlink_lit).strip() if tlink_lit else f"lnk_{_local_slug(r_uri)}"
        grain_lit = g.value(r_sub, RDFS.comment)
        card_lit = g.value(r_sub, DV2.cardinality)
        rel_desc_lit = g.value(r_sub, DV2.relationshipDescription)
        rel_kwargs: dict[str, Any] = {
            "relationship_uri": r_uri,
            "relationship_name": r_name,
            "target_link": tlink,
            "endpoints": endpoints,
        }
        if grain_lit is not None:
            rel_kwargs["grain_semantics"] = str(grain_lit)
        if card_lit is not None:
            rel_kwargs["cardinality"] = str(card_lit)
        if rel_desc_lit is not None:
            rel_kwargs["description"] = str(rel_desc_lit)
        relationships.append(CanonicalRelationship(**rel_kwargs))

    enums: list[ReferenceEnumeration] = []
    enum_subjects = sorted(
        {
            s
            for s in g.subjects(RDF.type, RDFS.Datatype)
            if g.value(s, DV2.targetReferenceTable) is not None
        },
        key=str,
    )
    for e_sub in enum_subjects:
        e_uri = str(e_sub)
        ref_tbl_lit = g.value(e_sub, DV2.targetReferenceTable)
        ref_tbl = str(ref_tbl_lit).strip() if ref_tbl_lit is not None else f"ref_{_local_slug(e_uri)}"
        e_name_lit = g.value(e_sub, RDFS.label)
        e_desc_lit = g.value(e_sub, RDFS.comment)
        code_details = sorted(g.objects(e_sub, DV2.hasCodeDetail), key=_order_key)
        allowed_codes: list[str] = []
        code_descriptions: dict[str, str] = {}
        if code_details:
            for c_node in code_details:
                c_val_lit = g.value(c_node, DV2.codeValue)
                if c_val_lit is None:
                    continue
                c_val = str(c_val_lit)
                allowed_codes.append(c_val)
                cd_lit = g.value(c_node, RDFS.comment)
                if cd_lit is not None:
                    code_descriptions[c_val] = str(cd_lit)
        else:
            allowed_codes = sorted({str(c) for c in g.objects(e_sub, DV2.allowedCode)})
        enum_kwargs: dict[str, Any] = {
            "enum_uri": e_uri,
            "target_reference_table": ref_tbl,
            "allowed_codes": allowed_codes,
        }
        if e_name_lit is not None:
            enum_kwargs["enumeration_name"] = str(e_name_lit)
        if e_desc_lit is not None:
            enum_kwargs["description"] = str(e_desc_lit)
        if code_descriptions:
            enum_kwargs["code_descriptions"] = code_descriptions
        enums.append(ReferenceEnumeration(**enum_kwargs))

    return GraphAOntology(
        ontology_version=ont_version,
        domain_namespace=inferred_ns or "imported_domain",
        description=ont_desc,
        enterprise_owner=enterprise_owner or "Enterprise Architecture",
        parent_sha256=parent_sha256,
        standards_references=standards_refs,
        decision_refs=decision_refs,
        canonical_business_concepts=concepts,
        canonical_relationships=relationships,
        reference_enumerations=enums,
    )


def parse_ontology_import_payload(
    content: str,
    *,
    import_format: str = "ttl",
    domain_namespace: str | None = None,
    expected_sha256: str | None = None,
) -> GraphAOntology:
    """Parse YAML, JSON, or Turtle ontology content into `GraphAOntology` and verify SHA-256."""
    if not isinstance(content, str) or not content.strip():
        raise OpenDVError(
            "Import ontology content must be a non-empty string.",
            error_code=ErrorCode.INPUT_VALIDATION_ERROR,
        )
    fmt = (import_format or "ttl").strip().lower()
    if fmt not in {"yaml", "yml", "json", "ttl", "turtle"}:
        raise OpenDVError(
            f"Unsupported ontology import format '{import_format}'. Expected yaml, json, or ttl.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    try:
        if fmt == "json":
            raw = json.loads(content)
            if domain_namespace and isinstance(raw, dict) and not raw.get("domain_namespace"):
                raw["domain_namespace"] = domain_namespace
            ont = GraphAOntology.model_validate(raw)
        elif fmt in {"yaml", "yml"}:
            raw = yaml.safe_load(content)
            if not isinstance(raw, dict):
                raise ValueError("YAML ontology payload must decode to an object.")
            if domain_namespace and not raw.get("domain_namespace"):
                raw["domain_namespace"] = domain_namespace
            ont = GraphAOntology.model_validate(raw)
        else:
            ont = _parse_turtle_ontology(content, domain_namespace)
    except OpenDVError:
        raise
    except (json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError, TypeError) as exc:
        raise OpenDVError(
            f"Failed to parse {fmt} ontology payload: {exc}",
            error_code=ErrorCode.INPUT_VALIDATION_ERROR,
        ) from exc

    if expected_sha256:
        actual_sha = compute_ontology_sha256(ont)
        if actual_sha != expected_sha256.strip().lower():
            raise OpenDVError(
                f"Imported ontology JCS SHA-256 mismatch: expected '{expected_sha256}', "
                f"got '{actual_sha}'.",
                error_code=ErrorCode.INPUT_VALIDATION_ERROR,
            )
    return ont


__all__ = ["parse_ontology_import_payload"]
