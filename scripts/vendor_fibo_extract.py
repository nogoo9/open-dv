#!/usr/bin/env python3
"""RDF/OWL/Turtle Pack v2 extraction and canonical JCS compilation for vendor_fibo.py."""

from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import rdflib
from rdflib import OWL, RDF, RDFS, SKOS
from rdflib.namespace import DCTERMS

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.vendor_fibo_archive import (  # noqa: E402
    _win_long_path,
    validate_xml_ontology_bytes,
)

from opendv.grounding.providers.fibo.closure import minimal_taxonomic_leaves  # noqa: E402
from opendv.models.json_hasher import dumps_jcs_bytes  # noqa: E402

DEFAULT_PINNED_COMMIT = "2d73435a4419b66b7c9f44cb9763e35fe8ac7890"
DEFAULT_PINNED_TAG = "master_2026Q3"
DEFAULT_PINNED_ARCHIVE_SHA256 = (
    "6fa27845ca7d7f32a035226d6b06f1e9637a9bdada1c2ed9c086dd21f8273cc9"
)
UPSTREAM_REPO = "https://github.com/edmcouncil/fibo.git"

PROD_MODULES: Final[tuple[str, ...]] = (
    "BE",
    "CAE",
    "DER",
    "FBC",
    "FND",
    "IND",
    "LOAN",
    "SEC",
)
ALL_V2_MODULES: Final[tuple[str, ...]] = (
    "BE",
    "CAE",
    "DER",
    "FBC",
    "FND",
    "IND",
    "LOAN",
    "MD",
    "SEC",
)
CORE_MODULES: tuple[str, ...] = PROD_MODULES
DEFAULT_COMPILED_AT = "2026-09-30T00:00:00Z"

FIBO_BASE_URI_PREFIX = "https://spec.edmcouncil.org/fibo/ontology/"
CMNS_BASE_URI_PREFIX = "https://www.omg.org/spec/Commons/"

EXPLANATORY_NOTE = rdflib.URIRef(
    "https://spec.edmcouncil.org/fibo/ontology/FND/Utilities/AnnotationVocabulary/explanatoryNote"
)
_CMNS_AV = "https://www.omg.org/spec/Commons/AnnotationVocabulary/"
CMNS_AV_SYNONYM = rdflib.URIRef(f"{_CMNS_AV}synonym")
CMNS_AV_ABBREVIATION = rdflib.URIRef(f"{_CMNS_AV}abbreviation")
CMNS_AV_ACRONYM = rdflib.URIRef(f"{_CMNS_AV}acronym")
CMNS_AV_REPLACED_BY = rdflib.URIRef(f"{_CMNS_AV}replacedBy")

_ROLE_PLAYED_BY_URIS: Final[frozenset[str]] = frozenset(
    {
        "https://www.omg.org/spec/Commons/RolesAndCompositions/isPlayedBy",
        "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Roles/isPlayedBy",
    }
)
_IDENTIFIES_URIS: Final[frozenset[str]] = frozenset(
    {
        "https://www.omg.org/spec/Commons/Identifiers/identifies",
        "https://www.omg.org/spec/Commons/Designators/denotes",
        "https://spec.edmcouncil.org/fibo/ontology/FND/Relations/Relations/denotes",
        "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/Agents/identifies",
    }
)

_MAX_TOTAL_INDIVIDUALS: Final[int] = 2500
_MAX_EXCHANGE_INDIVIDUALS: Final[int] = 350

_ALLOWLIST_INDIVIDUAL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "DayCountConvention",
        "BusinessDayConvention",
        "InterestRateBenchmark",
        "ReferenceInterestRate",
        "RegulatoryAgency",
        "CentralBank",
        "StockExchange",
        "RegulatedExchange",
        "Exchange",
    }
)
_EXCLUDED_ISO_INDIVIDUAL_TOKENS: Final[tuple[str, ...]] = (
    "ISO4217",
    "ISO3166",
    "/Currency",
    "/Country",
)

# 9-archetype priority local names (design_draft.md L344)
_ID_PRIORITY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "OrganizationIdentifier",
        "FinancialInstrumentIdentifier",
        "AccountIdentifier",
        "PartyIdentifier",
        "RegistryIdentifier",
        "MarketIdentifier",
        "BankAccountIdentifier",
        "PersonIdentifier",
    }
)
_ID_PRIORITY_EXCLUDED: Final[frozenset[str]] = frozenset(
    {"EntityLegalFormIdentifier", "CurrencyIdentifier"}
)
_CODE_CLASSIFIER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "CodeElement",
        "Classifier",
        "MeasurementUnit",
        "GeopoliticalEntity",
        "Country",
        "Currency",
        "Convention",
        "DayCountConvention",
        "BusinessDayConvention",
        "EntityLegalFormIdentifier",
        "CurrencyIdentifier",
    }
)
_TEMPORAL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "TimeInstant",
        "Date",
        "DateTime",
        "DatePeriod",
        "Duration",
        "ExplicitDate",
        "DateOfBirth",
        "BirthDate",
    }
)
_QUANTITY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "QuantityValue",
        "Percentage",
        "Ratio",
        "MonetaryAmount",
        "MonetaryPrice",
        "InterestRate",
    }
)
_VALUE_OBJECT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Name",
        "ContextualName",
        "OrganizationName",
        "PersonName",
        "Address",
        "StreetAddress",
        "ConventionalStreetAddress",
        "PostalAddress",
        "ElectronicMailAddress",
        "TelephoneNumber",
        "Rating",
        "RatingScore",
        "ContractualDefinition",
    }
)
_VALUE_OBJECT_EXCLUDED: Final[frozenset[str]] = frozenset(
    {"Covenant", "Collateral", "Option"}
)
_PARTY_ROLE_NAMES: Final[frozenset[str]] = frozenset(
    {"PartyRole", "AgentRole", "FunctionalRole", "ThingInRole"}
)
_EVENT_NAMES: Final[frozenset[str]] = frozenset({"Occurrence", "Event"})
_AGREEMENT_NAMES: Final[frozenset[str]] = frozenset({"Agreement", "Contract"})


def _local_name(uri: str) -> str:
    return uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _humanize_local_name(local: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return spaced.strip().lower()


def load_baseline_fibo_config() -> tuple[str, str, str | None]:
    """Load FIBO upstream config validated against schemas/v2/baseline-digests.json."""
    baseline_path = REPO_ROOT / "schemas" / "v2" / "baseline-digests.json"
    if baseline_path.is_file():
        try:
            data = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            raise RuntimeError(
                f"Failed to parse authoritative baseline digests from {baseline_path}: {err}"
            ) from err
        fibo_core = data.get("fibo_core_pack")
        if not isinstance(fibo_core, dict):
            raise RuntimeError(
                f"Malformed baseline digests in {baseline_path}: 'fibo_core_pack' must be dict"
            )
        return DEFAULT_PINNED_COMMIT, DEFAULT_PINNED_TAG, DEFAULT_PINNED_ARCHIVE_SHA256
    return DEFAULT_PINNED_COMMIT, DEFAULT_PINNED_TAG, DEFAULT_PINNED_ARCHIVE_SHA256


def get_fibo_module(uri: str, *, modules: Sequence[str] | None = None) -> str | None:
    """Determine which FIBO module a URI belongs to (rejects non-FIBO URIs)."""
    if not uri.startswith(FIBO_BASE_URI_PREFIX):
        return None
    rest = uri[len(FIBO_BASE_URI_PREFIX) :]
    allowed = tuple(modules) if modules is not None else ALL_V2_MODULES
    for m in allowed:
        if rest.startswith(f"{m}/"):
            return m
    return None


def _get_entry_module(
    uri: str, *, modules: Sequence[str] | None = None, include_cmns: bool = True
) -> str | None:
    mod = get_fibo_module(uri, modules=modules)
    if mod is not None:
        return mod
    if include_cmns and uri.startswith(CMNS_BASE_URI_PREFIX):
        return "CMNS"
    return None


def pick_best_literal(literals: list[rdflib.Literal]) -> str:
    """Select English (@en) or untagged literal, falling back to lexicographical sort."""
    valid = [lit for lit in literals if str(lit).strip()]
    if not valid:
        return ""
    en_lits = [
        str(lit).strip()
        for lit in valid
        if getattr(lit, "language", None)
        and str(getattr(lit, "language", "")).lower().split("-")[0] == "en"
    ]
    if en_lits:
        return sorted(en_lits)[0]
    untagged = [str(lit).strip() for lit in valid if getattr(lit, "language", None) is None]
    if untagged:
        return sorted(untagged)[0]
    return sorted(str(lit).strip() for lit in valid)[0]


def extract_annotations(g: rdflib.Graph, node: rdflib.URIRef) -> tuple[str, str, list[str]]:
    """Extract (label, definition, synonyms) for an RDF node including cmns-av synonyms."""
    raw_labels = [lit for lit in g.objects(node, RDFS.label) if isinstance(lit, rdflib.Literal)]
    uri_str = str(node)
    local = _local_name(uri_str)
    fallback_lbl = (
        _humanize_local_name(local) if uri_str.startswith(CMNS_BASE_URI_PREFIX) else local
    )
    label = pick_best_literal(raw_labels) or fallback_lbl

    raw_defs = [
        lit
        for lit in g.objects(node, SKOS.definition)
        if isinstance(lit, rdflib.Literal) and str(lit).strip()
    ]
    if not raw_defs:
        raw_defs = [
            lit
            for lit in g.objects(node, EXPLANATORY_NOTE)
            if isinstance(lit, rdflib.Literal) and str(lit).strip()
        ]
    if not raw_defs:
        raw_defs = [
            lit
            for lit in g.objects(node, RDFS.comment)
            if isinstance(lit, rdflib.Literal) and str(lit).strip()
        ]
    definition = pick_best_literal(raw_defs)

    syn_preds = (SKOS.altLabel, CMNS_AV_SYNONYM, CMNS_AV_ABBREVIATION, CMNS_AV_ACRONYM)
    syn_vals: set[str] = set()
    for pred in syn_preds:
        for lit in g.objects(node, pred):
            if isinstance(lit, rdflib.Literal):
                val = str(lit).strip()
                if val and val.lower() != label.lower():
                    syn_vals.add(val)
    return label, definition, sorted(syn_vals)


def _unpack_rdf_list(g: rdflib.Graph, list_node: rdflib.Node) -> list[rdflib.Node]:
    items: list[rdflib.Node] = []
    seen: set[rdflib.Node] = set()
    cur: rdflib.Node | None = list_node
    while cur is not None and cur != RDF.nil and cur not in seen:
        seen.add(cur)
        first = g.value(cur, RDF.first)
        if first is not None:
            items.append(first)
        cur = g.value(cur, RDF.rest)
    return items


def _collect_filler_uris(
    g: rdflib.Graph, node: rdflib.Node, visited: set[rdflib.Node]
) -> list[str]:
    if node in visited:
        return []
    visited.add(node)
    if isinstance(node, rdflib.URIRef):
        return [str(node)]
    if not isinstance(node, rdflib.BNode):
        return []
    out: list[str] = []
    for coll_pred in (OWL.unionOf, OWL.intersectionOf):
        for lst in g.objects(node, coll_pred):
            for member in _unpack_rdf_list(g, lst):
                out.extend(_collect_filler_uris(g, member, visited))
    return out


def _collect_class_expr_bnodes(
    g: rdflib.Graph, node: rdflib.Node, visited: set[rdflib.Node]
) -> list[rdflib.BNode]:
    if not isinstance(node, rdflib.BNode) or node in visited:
        return []
    visited.add(node)
    if (node, RDF.type, OWL.Restriction) in g or g.value(node, OWL.onProperty) is not None:
        return [node]
    out: list[rdflib.BNode] = []
    for coll_pred in (OWL.intersectionOf, OWL.unionOf):
        for lst in g.objects(node, coll_pred):
            for member in _unpack_rdf_list(g, lst):
                out.extend(_collect_class_expr_bnodes(g, member, visited))
    return out


def _int_literal(node: rdflib.Node | None) -> int | None:
    if isinstance(node, rdflib.Literal):
        try:
            return int(str(node))
        except ValueError:
            return None
    return None


def _parse_restriction(
    g: rdflib.Graph, bnode: rdflib.BNode
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    on_prop = g.value(bnode, OWL.onProperty)
    if not isinstance(on_prop, rdflib.URIRef):
        return None, [], []
    prop_uri = str(on_prop)

    target_uris_raw: list[str] = []
    for pred in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.onClass, OWL.onDataRange):
        for obj in g.objects(bnode, pred):
            target_uris_raw.extend(_collect_filler_uris(g, obj, set()))
    target_uris = sorted(dict.fromkeys(u for u in target_uris_raw if u))

    card = _int_literal(
        g.value(bnode, OWL.cardinality) or g.value(bnode, OWL.qualifiedCardinality)
    )
    min_c = _int_literal(
        g.value(bnode, OWL.minCardinality) or g.value(bnode, OWL.minQualifiedCardinality)
    )
    max_c = _int_literal(
        g.value(bnode, OWL.maxCardinality) or g.value(bnode, OWL.maxQualifiedCardinality)
    )

    if card is not None:
        r_kind = "card"
        min_card, max_card = card, card
    elif min_c is not None:
        r_kind = "min"
        min_card, max_card = min_c, max_c
    elif max_c is not None:
        r_kind = "max"
        min_card, max_card = None, max_c
    elif g.value(bnode, OWL.allValuesFrom) is not None:
        r_kind = "all"
        min_card, max_card = None, None
    else:
        r_kind = "some"
        min_card, max_card = None, None

    assoc: dict[str, Any] = {
        "property_uri": prop_uri,
        "restriction_kind": r_kind,
        "target_uris": target_uris,
    }
    if min_card is not None:
        assoc["min_card"] = min_card
    if max_card is not None:
        assoc["max_card"] = max_card

    played_by = target_uris if prop_uri in _ROLE_PLAYED_BY_URIS else []
    identifies = target_uris if prop_uri in _IDENTIFIES_URIS else []
    return assoc, played_by, identifies


def _is_deprecated(g: rdflib.Graph, node: rdflib.URIRef) -> bool:
    return any(str(lit).strip().lower() in ("true", "1") for lit in g.objects(node, OWL.deprecated))


def _extract_replaced_by(
    g: rdflib.Graph,
    node: rdflib.URIRef,
    *,
    deprecated: bool,
    equiv_pred: rdflib.URIRef,
) -> str | None:
    uri_str = str(node)
    for pred in (DCTERMS.isReplacedBy, CMNS_AV_REPLACED_BY):
        targets = sorted(
            str(o)
            for o in g.objects(node, pred)
            if isinstance(o, rdflib.URIRef) and str(o) != uri_str
        )
        if targets:
            return targets[0]
    if deprecated:
        eq_targets = sorted(
            str(o)
            for o in g.objects(node, equiv_pred)
            if isinstance(o, rdflib.URIRef) and str(o) != uri_str
        )
        if eq_targets:
            return eq_targets[0]
    return None


def _compute_ancestors_map(classes: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    ancestors: dict[str, set[str]] = {}
    for start in classes:
        visited: set[str] = {start}
        q: collections.deque[str] = collections.deque([start])
        while q:
            cur = q.popleft()
            entry = classes.get(cur)
            if not entry:
                continue
            for p in entry.get("sub_class_of", ()):
                if p not in visited:
                    visited.add(p)
                    q.append(p)
        ancestors[start] = visited
    return ancestors


def _classify_archetype(uri: str, ancestors: set[str]) -> str:
    local_self = _local_name(uri)
    anc_locals = {_local_name(a) for a in ancestors}
    if (anc_locals & _ID_PRIORITY_NAMES) and local_self not in _ID_PRIORITY_EXCLUDED:
        return "identifier"
    if anc_locals & _CODE_CLASSIFIER_NAMES:
        return "code_classifier"
    if "Identifier" in anc_locals:
        return "identifier"
    if anc_locals & _TEMPORAL_NAMES:
        return "temporal"
    if anc_locals & _QUANTITY_NAMES:
        return "quantity_measure"
    if (anc_locals & _VALUE_OBJECT_NAMES) and not (anc_locals & _VALUE_OBJECT_EXCLUDED):
        return "value_object"
    if anc_locals & _PARTY_ROLE_NAMES:
        return "party_role"
    if anc_locals & _EVENT_NAMES:
        return "event_occurrence"
    if anc_locals & _AGREEMENT_NAMES:
        return "agreement"
    return "entity"


def _extract_individuals(
    g: rdflib.Graph,
    classes: dict[str, dict[str, Any]],
    ancestors_map: dict[str, set[str]],
    *,
    modules: Sequence[str],
    include_cmns: bool,
) -> dict[str, dict[str, Any]]:
    # Map allowlisted root class URIs to category bucket
    allow_class_uris: dict[str, str] = {}
    for c_uri, anc_set in ancestors_map.items():
        anc_locals = {_local_name(a) for a in anc_set}
        matched = anc_locals & _ALLOWLIST_INDIVIDUAL_NAMES
        if matched:
            if "RegulatedExchange" in matched:
                allow_class_uris[c_uri] = "RegulatedExchange"
            elif "StockExchange" in matched or "Exchange" in matched:
                allow_class_uris[c_uri] = "Exchange"
            else:
                allow_class_uris[c_uri] = sorted(matched)[0]

    exchange_items: list[tuple[int, str, dict[str, Any]]] = []
    other_items: list[tuple[str, dict[str, Any]]] = []

    for ind in sorted(set(g.subjects(RDF.type, OWL.NamedIndividual)), key=str):
        if not isinstance(ind, rdflib.URIRef):
            continue
        uri = str(ind)
        if any(tok in uri for tok in _EXCLUDED_ISO_INDIVIDUAL_TOKENS):
            continue
        mod = _get_entry_module(uri, modules=modules, include_cmns=include_cmns)
        if mod is None:
            continue
        rdf_types = sorted(
            str(t)
            for t in g.objects(ind, RDF.type)
            if isinstance(t, rdflib.URIRef) and t != OWL.NamedIndividual
        )
        buckets = {allow_class_uris[t] for t in rdf_types if t in allow_class_uris}
        if not buckets:
            continue
        label, _defn, syns = extract_annotations(g, ind)
        record = {
            "aliases": syns,
            "deprecated": _is_deprecated(g, ind),
            "label": label,
            "module": mod,
            "types": rdf_types,
            "uri": uri,
        }
        if "RegulatedExchange" in buckets:
            exchange_items.append((0, uri, record))
        elif "Exchange" in buckets:
            exchange_items.append((1, uri, record))
        else:
            other_items.append((uri, record))

    individuals: dict[str, dict[str, Any]] = {}
    for uri, rec in sorted(other_items, key=lambda x: x[0]):
        if len(individuals) >= _MAX_TOTAL_INDIVIDUALS:
            break
        individuals[uri] = rec

    # Check Exchange count before applying 350 cap
    if len(exchange_items) > _MAX_EXCHANGE_INDIVIDUALS:
        exchange_items.sort(key=lambda x: (x[0], x[1]))
        exchange_items = exchange_items[:_MAX_EXCHANGE_INDIVIDUALS]
    for _prio, uri, rec in exchange_items:
        if len(individuals) >= _MAX_TOTAL_INDIVIDUALS:
            break
        individuals[uri] = rec
    return individuals


def extract_fibo_graph_v2(
    g: rdflib.Graph,
    *,
    modules: Sequence[str] = PROD_MODULES,
    include_cmns: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Extract Pack v2 classes, properties, and allowlisted individuals from an RDF graph."""
    classes: dict[str, Any] = {}
    properties: dict[str, Any] = {}

    # Collect symmetric owl:disjointWith & owl:AllDisjointClasses first
    disjoint_map: dict[str, set[str]] = {}
    for s, o in g.subject_objects(OWL.disjointWith):
        if isinstance(s, rdflib.URIRef):
            s_uri = str(s)
            for t_uri in _collect_filler_uris(g, o, set()):
                if t_uri != s_uri:
                    disjoint_map.setdefault(s_uri, set()).add(t_uri)
                    disjoint_map.setdefault(t_uri, set()).add(s_uri)
    for adc in g.subjects(RDF.type, OWL.AllDisjointClasses):
        members_list = g.value(adc, OWL.members)
        if members_list is not None:
            m_uris = [
                str(m) for m in _unpack_rdf_list(g, members_list) if isinstance(m, rdflib.URIRef)
            ]
            for m1 in m_uris:
                for m2 in m_uris:
                    if m1 != m2:
                        disjoint_map.setdefault(m1, set()).add(m2)

    for c in sorted(set(g.subjects(RDF.type, OWL.Class)), key=str):
        if not isinstance(c, rdflib.URIRef):
            continue
        uri = str(c)
        mod = _get_entry_module(uri, modules=modules, include_cmns=include_cmns)
        if mod is None:
            continue
        label, definition, syns = extract_annotations(g, c)
        parents: set[str] = set()
        assoc_seen: dict[tuple[str, str], dict[str, Any]] = {}
        direct_played_by: list[str] = []
        direct_identifies: list[str] = []

        for p in g.objects(c, RDFS.subClassOf):
            if isinstance(p, rdflib.URIRef) and str(p) != uri:
                parents.add(str(p))
            elif isinstance(p, rdflib.BNode):
                for bnode in _collect_class_expr_bnodes(g, p, set()):
                    assoc, played, ident = _parse_restriction(g, bnode)
                    if assoc is not None:
                        key = (assoc["property_uri"], assoc["restriction_kind"])
                        existing = assoc_seen.get(key)
                        if existing is None:
                            assoc_seen[key] = assoc
                        else:
                            existing["target_uris"] = sorted(
                                set(existing["target_uris"]) | set(assoc["target_uris"])
                            )
                    direct_played_by.extend(played)
                    direct_identifies.extend(ident)

        for eq in g.objects(c, OWL.equivalentClass):
            if isinstance(eq, rdflib.BNode):
                for bnode in _collect_class_expr_bnodes(g, eq, set()):
                    assoc, played, ident = _parse_restriction(g, bnode)
                    if assoc is not None:
                        key = (assoc["property_uri"], assoc["restriction_kind"])
                        existing = assoc_seen.get(key)
                        if existing is None:
                            assoc_seen[key] = assoc
                        else:
                            existing["target_uris"] = sorted(
                                set(existing["target_uris"]) | set(assoc["target_uris"])
                            )
                    direct_played_by.extend(played)
                    direct_identifies.extend(ident)

        dep = _is_deprecated(g, c)
        repl_by = _extract_replaced_by(g, c, deprecated=dep, equiv_pred=OWL.equivalentClass)
        entry: dict[str, Any] = {
            "associated_properties": sorted(
                assoc_seen.values(), key=lambda x: (x["property_uri"], x["restriction_kind"])
            ),
            "definition": definition,
            "deprecated": dep,
            "disjoint_with": sorted(disjoint_map.get(uri, ())),
            "identified_entity_uris": list(dict.fromkeys(direct_identifies)),
            "is_played_by": list(dict.fromkeys(direct_played_by)),
            "label": label,
            "module": mod,
            "sub_class_of": sorted(parents),
            "synonyms": syns,
            "uri": uri,
        }
        if repl_by is not None:
            entry["replaced_by"] = repl_by
        classes[uri] = entry

    ancestors_map = _compute_ancestors_map(classes)

    def _anc_fn(u: str) -> set[str]:
        return ancestors_map.get(u, {u})

    # Inherit is_played_by / identified_entity_uris with minimal_taxonomic_leaves + archetypes
    inherited_played: dict[str, list[str]] = {}
    inherited_ident: dict[str, list[str]] = {}
    for uri, anc_set in ancestors_map.items():
        raw_pb: list[str] = list(classes[uri]["is_played_by"])
        raw_id: list[str] = list(classes[uri]["identified_entity_uris"])
        for anc in sorted(anc_set):
            if anc == uri or anc not in classes:
                continue
            raw_pb.extend(classes[anc]["is_played_by"])
            raw_id.extend(classes[anc]["identified_entity_uris"])
        inherited_played[uri] = minimal_taxonomic_leaves(raw_pb, _anc_fn) if raw_pb else []
        inherited_ident[uri] = minimal_taxonomic_leaves(raw_id, _anc_fn) if raw_id else []

    for uri, c_val in classes.items():
        c_val["is_played_by"] = inherited_played[uri]
        c_val["identified_entity_uris"] = inherited_ident[uri]
        c_val["archetype"] = _classify_archetype(uri, ancestors_map[uri])

    for prop_rdf_type, prop_type_name in (
        (OWL.ObjectProperty, "ObjectProperty"),
        (OWL.DatatypeProperty, "DatatypeProperty"),
    ):
        for p in sorted(set(g.subjects(RDF.type, prop_rdf_type)), key=str):
            if not isinstance(p, rdflib.URIRef):
                continue
            uri = str(p)
            mod = _get_entry_module(uri, modules=modules, include_cmns=include_cmns)
            if mod is None:
                continue
            label, definition, syns = extract_annotations(g, p)
            domains = sorted(
                {
                    u
                    for d in g.objects(p, RDFS.domain)
                    for u in _collect_filler_uris(g, d, set())
                }
            )
            ranges = sorted(
                {
                    u
                    for r in g.objects(p, RDFS.range)
                    for u in _collect_filler_uris(g, r, set())
                }
            )
            sub_props = sorted(
                {
                    str(sp)
                    for sp in g.objects(p, RDFS.subPropertyOf)
                    if isinstance(sp, rdflib.URIRef) and str(sp) != uri
                }
            )
            inv_set: set[str] = {
                str(inv)
                for inv in g.objects(p, OWL.inverseOf)
                if isinstance(inv, rdflib.URIRef) and str(inv) != uri
            }
            for rev_inv in g.subjects(OWL.inverseOf, p):
                if isinstance(rev_inv, rdflib.URIRef) and str(rev_inv) != uri:
                    inv_set.add(str(rev_inv))

            is_func = (p, RDF.type, OWL.FunctionalProperty) in g
            is_inv_func = (p, RDF.type, OWL.InverseFunctionalProperty) in g
            dep = _is_deprecated(g, p)
            repl_by = _extract_replaced_by(g, p, deprecated=dep, equiv_pred=OWL.equivalentProperty)

            if uri in properties:
                existing = properties[uri]
                existing["domain"] = sorted(set(existing["domain"]) | set(domains))
                existing["range"] = sorted(set(existing["range"]) | set(ranges))
                existing["sub_property_of"] = sorted(
                    set(existing["sub_property_of"]) | set(sub_props)
                )
                existing["inverse_of"] = sorted(set(existing["inverse_of"]) | inv_set)
                existing["synonyms"] = sorted(set(existing["synonyms"]) | set(syns))
                existing["is_functional"] = existing["is_functional"] or is_func
                existing["is_inverse_functional"] = (
                    existing["is_inverse_functional"] or is_inv_func
                )
                if not existing["definition"] and definition:
                    existing["definition"] = definition
                continue

            prop_entry: dict[str, Any] = {
                "definition": definition,
                "deprecated": dep,
                "domain": domains,
                "inverse_of": sorted(inv_set),
                "is_functional": is_func,
                "is_inverse_functional": is_inv_func,
                "label": label,
                "module": mod,
                "property_type": prop_type_name,
                "range": ranges,
                "sub_property_of": sub_props,
                "synonyms": syns,
                "uri": uri,
            }
            if repl_by is not None:
                prop_entry["replaced_by"] = repl_by
            properties[uri] = prop_entry

    individuals = _extract_individuals(
        g,
        classes,
        ancestors_map,
        modules=modules,
        include_cmns=include_cmns,
    )
    return classes, properties, individuals


def extract_fibo_graph(
    g: rdflib.Graph,
    *,
    modules: Sequence[str] = CORE_MODULES,
    include_cmns: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract classes and properties (legacy 2-tuple helper)."""
    classes, properties, _ = extract_fibo_graph_v2(
        g, modules=modules, include_cmns=include_cmns
    )
    return classes, properties


def compile_fibo_pack(
    classes: dict[str, Any],
    properties: dict[str, Any],
    individuals: dict[str, Any] | None = None,
    *,
    upstream_tag: str = DEFAULT_PINNED_TAG,
    upstream_commit: str = DEFAULT_PINNED_COMMIT,
    compiled_at: str = DEFAULT_COMPILED_AT,
    modules: Sequence[str] = CORE_MODULES,
    is_fixture: bool = False,
) -> tuple[bytes, dict[str, Any], str, str]:
    """Assemble and serialize canonical RFC 8785 JCS Pack v2 (zero trailing newline)."""
    pack_id = "fibo-core-fixture" if is_fixture else "fibo-core"
    pack: dict[str, Any] = {
        "classes": classes,
        "individuals": individuals or {},
        "metadata": {
            "compiled_at": compiled_at,
            "modules": sorted(list(modules)),
            "pack_id": pack_id,
            "pack_schema_version": "2.0.0",
            "upstream_commit": upstream_commit,
            "upstream_repo": UPSTREAM_REPO,
            "upstream_tag": upstream_tag,
            "version": "2.0.0-fixture" if is_fixture else "2.0.0",
        },
        "properties": properties,
    }
    if is_fixture:
        pack["metadata"]["is_fixture"] = True

    raw_bytes = dumps_jcs_bytes(pack)
    sha256_full = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes, pack, sha256_full, sha256_full[:8]


def create_mock_fibo_graph(*, include_dev: bool = False) -> rdflib.Graph:
    """Create a synthetic in-memory RDF graph covering production (and optional dev) modules."""
    g = rdflib.Graph()
    base = FIBO_BASE_URI_PREFIX
    specs: list[
        tuple[str, str, str, list[str], str | None, str | None, str | None, str | None, str | None]
    ] = [
        (f"{base}FND/Agreements/Agreements/Agreement", "agreement",
         "A negotiated and legally enforceable understanding between two or more parties.",
         ["understanding", "compact"], None, f"{base}FND/Parties/Parties/hasParty",
         "has party", "ObjectProperty", "Identifies an independent party."),
        (f"{base}FND/Agreements/Contracts/Contract", "contract",
         "An agreement creating legally binding obligations.", ["binding agreement"],
         f"{base}FND/Agreements/Agreements/Agreement", None, None, None, None),
        (f"{base}BE/LegalEntities/LegalPersons/LegalEntity", "legal entity",
         "An entity capable of bearing legal rights and obligations.", ["juristic person"],
         None, f"{base}BE/LegalEntities/hasLegalAddress", "has legal address",
         "ObjectProperty", "Specifies legal address."),
        (f"{base}BE/Corporations/Corporations/Corporation", "corporation",
         "A formal business organization.", ["company", "corporate body"],
         f"{base}BE/LegalEntities/LegalPersons/LegalEntity", None, None, None, None),
        (f"{base}CAE/CorporateEvents/CorporateActions/CorporateAction", "corporate action",
         "An event initiated by an issuer affecting security holders.", ["corporate event"],
         None, f"{base}CAE/CorporateEvents/CorporateActions/hasActionType",
         "has action type", "DatatypeProperty", "Specifies corporate action type."),
        (f"{base}DER/DerivativesContracts/DerivativesBasics/DerivativeInstrument",
         "derivative instrument",
         "A financial instrument whose value depends on underlying assets or rates.",
         ["derivative"], None,
         f"{base}DER/DerivativesContracts/DerivativesBasics/hasUnderlier",
         "has underlier", "ObjectProperty", "Identifies underlying asset."),
        (f"{base}FBC/FinancialInstruments/FinancialInstrument", "financial instrument",
         "A contract giving rise to a financial asset and liability.", ["tradable asset"],
         None, f"{base}FBC/DebtAndEquities/Debt/hasNominalValue", "has nominal value",
         "DatatypeProperty", "The stated nominal value."),
        (f"{base}FBC/FinancialInstruments/Security", "security",
         "A fungible negotiable financial instrument.", ["investment security"],
         f"{base}FBC/FinancialInstruments/FinancialInstrument", None, None, None, None),
        (f"{base}SEC/Equities/EquityInstruments/Share", "share",
         "A unit of equity ownership in a corporation.", ["stock unit", "equity share"],
         f"{base}FBC/FinancialInstruments/Security", f"{base}SEC/Debt/Bonds/hasMaturityDate",
         "has maturity date", "DatatypeProperty", "Maturity due date."),
        (f"{base}SEC/Debt/Bonds/Bond", "bond",
         "A debt instrument issued by an entity.", ["fixed income security"],
         f"{base}FBC/FinancialInstruments/Security", None, None, None, None),
        (f"{base}LOAN/LoansGeneral/Loans/Loan", "loan",
         "A financing agreement in which a lender advances funds.", ["credit facility"],
         f"{base}FND/Agreements/Contracts/Contract",
         f"{base}LOAN/LoansGeneral/Loans/hasPrincipal", "has principal",
         "DatatypeProperty", "Original sum lent."),
        (f"{base}LOAN/LoansGeneral/Loans/CollateralizedLoan", "collateralized loan",
         "A loan secured by borrower assets.", ["secured loan"],
         f"{base}LOAN/LoansGeneral/Loans/Loan", None, None, None, None),
        (f"{base}IND/Indicators/Indicators/FinancialIndex", "financial index",
         "A numerical index representing portfolio value.", ["market index", "benchmark index"],
         None, f"{base}IND/InterestRates/hasBenchmarkRate", "has benchmark rate",
         "ObjectProperty", "Associates instrument with benchmark rate."),
        (f"{base}IND/InterestRates/BenchmarkInterestRate", "benchmark interest rate",
         "A standardized reference interest rate.", ["reference rate"],
         f"{base}IND/Indicators/Indicators/FinancialIndex", None, None, None, None),
    ]
    if include_dev:
        specs.append(
            (f"{base}MD/TemporalCore/SecurityPriceFeed", "security price feed",
             "A market data feed publishing security prices.", ["price feed"],
             None, f"{base}MD/TemporalCore/hasQuoteSource", "has quote source",
             "DatatypeProperty", "Quote vendor source."),
        )
    for c_uri, c_lbl, c_def, c_syns, parent, p_uri, p_lbl, p_type, p_def in specs:
        c_ref = rdflib.URIRef(c_uri)
        g.add((c_ref, RDF.type, OWL.Class))
        g.add((c_ref, RDFS.label, rdflib.Literal(c_lbl)))
        g.add((c_ref, SKOS.definition, rdflib.Literal(c_def)))
        for s in c_syns:
            g.add((c_ref, SKOS.altLabel, rdflib.Literal(s)))
        if parent:
            g.add((c_ref, RDFS.subClassOf, rdflib.URIRef(parent)))
        if p_uri and p_lbl and p_type and p_def:
            p_ref = rdflib.URIRef(p_uri)
            rdf_t = OWL.ObjectProperty if p_type == "ObjectProperty" else OWL.DatatypeProperty
            g.add((p_ref, RDF.type, rdf_t))
            g.add((p_ref, RDFS.label, rdflib.Literal(p_lbl)))
            g.add((p_ref, SKOS.definition, rdflib.Literal(p_def)))
            g.add((p_ref, RDFS.domain, c_ref))
    return g


def load_fibo_from_directory(
    repo_root: Path,
    *,
    allow_parse_errors: bool = False,
    modules: Sequence[str] = CORE_MODULES,
) -> rdflib.Graph:
    """Scan and parse .ttl/.rdf/.owl files for selected modules and local cmns-* files."""
    g = rdflib.Graph()
    parsed_files: set[Path] = set()
    parse_errors: list[str] = []

    search_dirs: list[Path] = []
    for mod_name in modules:
        candidates = [
            repo_root / mod_name,
            repo_root / "fibo" / mod_name,
            repo_root / "ontology" / mod_name,
        ]
        target_mod_dir = next((c for c in candidates if c.is_dir()), None)
        if target_mod_dir is None:
            matching = sorted(
                [p for p in repo_root.rglob(mod_name) if p.is_dir()],
                key=lambda p: (len(p.parts), str(p)),
            )
            target_mod_dir = matching[0] if matching else None
        if target_mod_dir is not None:
            search_dirs.append(target_mod_dir)

    # Also include local Commons (cmns-*) directories/files if present
    for extra in sorted(repo_root.rglob("*")):
        if extra.is_dir() and (
            extra.name.lower() == "commons" or extra.name.lower().startswith("cmns-")
        ):
            search_dirs.append(extra)
        elif (
            extra.is_file()
            and extra.name.lower().startswith("cmns-")
            and extra.suffix.lower() in (".ttl", ".rdf", ".owl")
        ):
            parsed_files.add(extra)

    files_to_parse: set[Path] = set(parsed_files)
    for d in search_dirs:
        for f in d.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".rdf", ".owl", ".ttl"):
                files_to_parse.add(f)

    for f in sorted(files_to_parse):
        suffix = f.suffix.lower()
        raw_bytes = _win_long_path(f).read_bytes()
        if suffix in (".rdf", ".owl"):
            validate_xml_ontology_bytes(raw_bytes, f.name)
            fmt = "xml"
        else:
            fmt = "turtle"
        try:
            g.parse(data=raw_bytes, format=fmt)
        except Exception as err:  # noqa: BLE001
            parse_errors.append(f"{f.name}: {err}")

    if not files_to_parse:
        raise RuntimeError(
            f"No ontology files (.rdf, .owl, .ttl) found for modules {list(modules)} in {repo_root}"
        )
    if parse_errors and not allow_parse_errors:
        raise RuntimeError(
            f"Production compile failed: {len(parse_errors)} ontology files had parse errors. "
            f"First error: {parse_errors[0]}"
        )
    return g
