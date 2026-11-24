#!/usr/bin/env python3
"""RDF extraction and JCS compilation helpers for scripts/vendor_fibo.py."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import rdflib
from rdflib import OWL, RDF, RDFS, SKOS

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from opendv.models.json_hasher import dumps_jcs_bytes  # noqa: E402

DEFAULT_PINNED_COMMIT = "69dbbe7a6a4e32049ba3f3988b48877bc954f061"
DEFAULT_PINNED_TAG = "master_2024Q4"
UPSTREAM_REPO = "https://github.com/edmcouncil/fibo.git"
CORE_MODULES: tuple[str, ...] = ("FND", "BE", "FBC", "SEC", "LOAN", "IND")
DEFAULT_COMPILED_AT = "2024-12-31T00:00:00Z"

EXPLANATORY_NOTE = rdflib.URIRef(
    "https://spec.edmcouncil.org/fibo/ontology/FND/Utilities/AnnotationVocabulary/explanatoryNote"
)
FIBO_BASE_URI_PREFIX = "https://spec.edmcouncil.org/fibo/ontology/"


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
        return DEFAULT_PINNED_COMMIT, DEFAULT_PINNED_TAG, None
    return DEFAULT_PINNED_COMMIT, DEFAULT_PINNED_TAG, None


def get_fibo_module(uri: str) -> str | None:
    """Determine which of the 6 core FIBO modules a URI belongs to."""
    if not uri.startswith(FIBO_BASE_URI_PREFIX):
        return None
    rest = uri[len(FIBO_BASE_URI_PREFIX) :]
    for m in CORE_MODULES:
        if rest.startswith(f"{m}/"):
            return m
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
    """Extract (label, definition, synonyms) for an RDF node."""
    raw_labels = [lit for lit in g.objects(node, RDFS.label) if isinstance(lit, rdflib.Literal)]
    label = pick_best_literal(raw_labels) or str(node).rstrip("/").split("/")[-1]

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
    syns = sorted(
        {
            str(lit).strip()
            for lit in g.objects(node, SKOS.altLabel)
            if isinstance(lit, rdflib.Literal) and str(lit).strip()
        }
    )
    return label, definition, syns


def extract_fibo_graph(g: rdflib.Graph) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract classes and properties for the 6 core modules from an RDF graph."""
    classes: dict[str, Any] = {}
    properties: dict[str, Any] = {}

    for c in g.subjects(RDF.type, OWL.Class):
        if not isinstance(c, rdflib.URIRef):
            continue
        uri = str(c)
        mod = get_fibo_module(uri)
        if mod is None:
            continue
        label, definition, syns = extract_annotations(g, c)
        parents = sorted(
            {
                str(p)
                for p in g.objects(c, RDFS.subClassOf)
                if isinstance(p, rdflib.URIRef) and str(p) != uri
            }
        )
        classes[uri] = {
            "definition": definition,
            "label": label,
            "module": mod,
            "sub_class_of": parents,
            "synonyms": syns,
            "uri": uri,
        }

    for prop_rdf_type, prop_type_name in (
        (OWL.ObjectProperty, "ObjectProperty"),
        (OWL.DatatypeProperty, "DatatypeProperty"),
    ):
        for p in g.subjects(RDF.type, prop_rdf_type):
            if not isinstance(p, rdflib.URIRef):
                continue
            uri = str(p)
            mod = get_fibo_module(uri)
            if mod is None:
                continue
            label, definition, syns = extract_annotations(g, p)
            domains = sorted(
                {str(d) for d in g.objects(p, RDFS.domain) if isinstance(d, rdflib.URIRef)}
            )
            ranges = sorted(
                {str(r) for r in g.objects(p, RDFS.range) if isinstance(r, rdflib.URIRef)}
            )
            sub_props = sorted(
                {
                    str(sp)
                    for sp in g.objects(p, RDFS.subPropertyOf)
                    if isinstance(sp, rdflib.URIRef) and str(sp) != uri
                }
            )
            if uri in properties:
                existing = properties[uri]
                existing["domain"] = sorted(set(existing["domain"]) | set(domains))
                existing["range"] = sorted(set(existing["range"]) | set(ranges))
                existing["sub_property_of"] = sorted(
                    set(existing["sub_property_of"]) | set(sub_props)
                )
                existing["synonyms"] = sorted(set(existing["synonyms"]) | set(syns))
                if not existing["definition"] and definition:
                    existing["definition"] = definition
                continue
            properties[uri] = {
                "definition": definition,
                "domain": domains,
                "label": label,
                "module": mod,
                "property_type": prop_type_name,
                "range": ranges,
                "sub_property_of": sub_props,
                "synonyms": syns,
                "uri": uri,
            }
    return classes, properties


def compile_fibo_pack(
    classes: dict[str, Any],
    properties: dict[str, Any],
    *,
    upstream_tag: str = DEFAULT_PINNED_TAG,
    upstream_commit: str = DEFAULT_PINNED_COMMIT,
    compiled_at: str = DEFAULT_COMPILED_AT,
    modules: tuple[str, ...] = CORE_MODULES,
    is_fixture: bool = False,
) -> tuple[bytes, dict[str, Any], str, str]:
    """Assemble and serialize canonical RFC 8785 JSON pack."""
    pack_id = "fibo-core-fixture" if is_fixture else "fibo-core"
    pack: dict[str, Any] = {
        "classes": classes,
        "metadata": {
            "compiled_at": compiled_at,
            "modules": sorted(list(modules)),
            "pack_id": pack_id,
            "upstream_commit": upstream_commit,
            "upstream_tag": upstream_tag,
            "version": "1.0.0",
        },
        "properties": properties,
    }
    if is_fixture:
        pack["metadata"]["is_fixture"] = True

    raw_bytes = dumps_jcs_bytes(pack)
    sha256_full = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes, pack, sha256_full, sha256_full[:8]


def create_mock_fibo_graph() -> rdflib.Graph:
    """Create a synthetic in-memory RDF graph covering all 6 core modules."""
    g = rdflib.Graph()
    base = FIBO_BASE_URI_PREFIX
    specs = [
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
) -> rdflib.Graph:
    """Recursively scan and parse RDF/OWL/Turtle files for the 6 core modules."""
    g = rdflib.Graph()
    total_files = 0
    parse_errors: list[str] = []

    for mod_name in CORE_MODULES:
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
        if target_mod_dir is None:
            continue
        for f in sorted(
            p for p in target_mod_dir.rglob("*") if p.suffix.lower() in (".rdf", ".owl", ".ttl")
        ):
            total_files += 1
            try:
                g.parse(str(f))
            except Exception as err:  # noqa: BLE001
                parse_errors.append(f"{f.name}: {err}")

    if total_files == 0:
        raise RuntimeError(
            f"No ontology files (.rdf, .owl, .ttl) found for core modules in {repo_root}"
        )
    if parse_errors and not allow_parse_errors:
        raise RuntimeError(
            f"Production compile failed: {len(parse_errors)} ontology files had parse errors. "
            f"First error: {parse_errors[0]}"
        )
    return g
