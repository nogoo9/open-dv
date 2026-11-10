#!/usr/bin/env python3
"""FIBO Reference Ontology Compilation Script for Open-DV Ontology Construction Studio.

Compiles EDM Council FIBO master_2024Q4 release across 6 core modules:
- FND: Foundations
- BE: Business Entities
- FBC: Financial Business & Commerce
- SEC: Securities
- LOAN: Loans
- IND: Indices & Indicators

Extracts classes (owl:Class), properties (owl:ObjectProperty, owl:DatatypeProperty),
labels (rdfs:label), definitions (skos:definition, fibo-fnd-utl-av:explanatoryNote, rdfs:comment),
synonyms (skos:altLabel), and taxonomic hierarchies (rdfs:subClassOf).

Outputs deterministic RFC 8785 canonical JSON pack `fibo-core-<sha256_8>.json`
conforming strictly to resource budgets:
- Disk size: < 4.0 MB
- Memory overhead: < 18.0 MB
- Cold load time: < 70 ms
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tracemalloc
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import rdflib
from rdflib import OWL, RDF, RDFS, SKOS

GIT_BIN: str = shutil.which("git") or "git"


def _init_windows_platform() -> None:
    """Windows search-order hijack prevention and UTF-8 console setup (MIN-05)."""
    if sys.platform == "win32":
        os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"  # noqa: SIM112
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Repo root anchor (MIN-05)
REPO_ROOT = Path(__file__).resolve().parent.parent

# Import canonical RFC 8785 JSON canonicalizer from opendv_contracts
try:
    from opendv_contracts.jcs import dumps_jcs_bytes
except ImportError:
    contracts_src = REPO_ROOT / "packages" / "opendv-contracts" / "src"
    if contracts_src.is_dir():
        sys.path.insert(0, str(contracts_src))
        from opendv_contracts.jcs import dumps_jcs_bytes
    else:
        raise

def _load_baseline_fibo_config() -> tuple[str, str, str | None]:
    """Load upstream_commit, upstream_tag, and archive_sha256 from baseline (MIN-03, MIN-05)."""
    baseline_path = REPO_ROOT / "schemas" / "v1" / "baseline-digests.json"
    if baseline_path.is_file():
        try:
            data = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            msg = f"Failed to parse authoritative baseline digests from {baseline_path}: {err}"
            raise RuntimeError(msg) from err
        ref_packs = data.get("reference_packs")
        if not isinstance(ref_packs, dict):
            msg = f"Malformed baseline digests in {baseline_path}: 'reference_packs' must be dict"
            raise RuntimeError(msg)
        ref = ref_packs.get("fibo_core", {})
        if not isinstance(ref, dict):
            msg = f"Malformed baseline digests in {baseline_path}: 'fibo_core' must be dict"
            raise RuntimeError(msg)
        commit = ref.get("upstream_commit") or "69dbbe7a6a4e32049ba3f3988b48877bc954f061"
        tag = ref.get("upstream_tag") or "master_2024Q4"
        archive_sha = ref.get("archive_sha256")
        return commit, tag, archive_sha
    return "69dbbe7a6a4e32049ba3f3988b48877bc954f061", "master_2024Q4", None


def _hash_file_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 digest of a file in chunks to limit memory footprint (MIN-06)."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


# Pinned reference parameters per schemas/v1/baseline-digests.json and docs/21 §3.3
_baseline_commit, _baseline_tag, PINNED_ARCHIVE_SHA256 = _load_baseline_fibo_config()
UPSTREAM_REPO = "https://github.com/edmcouncil/fibo.git"
UPSTREAM_TAG = _baseline_tag
UPSTREAM_COMMIT = _baseline_commit
CORE_MODULES: tuple[str, ...] = ("FND", "BE", "FBC", "SEC", "LOAN", "IND")
DEFAULT_COMPILED_AT = "2024-12-31T00:00:00Z"

DEFAULT_OUTPUT_DIR = REPO_ROOT / "packages" / "opendv-sources" / "src" / "opendv_sources" / "fibo"
DEFAULT_CACHE_DIR = REPO_ROOT / ".opendv" / "cache" / "fibo"

# Resource Budget Invariants (ADR-003 & docs/21 §3.3)
MAX_DISK_SIZE_BYTES = 4 * 1024 * 1024  # < 4.0 MB
MAX_MEMORY_BYTES = 18 * 1024 * 1024    # < 18.0 MB
MAX_LOAD_LATENCY_MS = 70.0              # < 70 ms

# Minimum counts for real production vendored pack (MAJ-03)
MIN_PRODUCTION_CLASSES = 2000
MIN_PRODUCTION_PROPERTIES = 800

EXPLANATORY_NOTE = rdflib.URIRef(
    "https://spec.edmcouncil.org/fibo/ontology/FND/Utilities/AnnotationVocabulary/explanatoryNote"
)

FIBO_BASE_URI_PREFIX = "https://spec.edmcouncil.org/fibo/ontology/"


@dataclass(frozen=True)
class BudgetVerificationResult:
    """Outcome of validating a compiled FIBO JSON pack against budgets and schema."""

    disk_size_bytes: int
    memory_peak_bytes: int
    cold_load_ms: float
    sha256_full: str
    sha256_8: str
    class_count: int
    property_count: int
    is_valid: bool
    error_message: str | None = None


def _fail_result(
    msg: str,
    *,
    disk_size: int = 0,
    peak_memory: int = 0,
    cold_load_ms: float = 0.0,
    sha256_full: str = "",
    sha256_8: str = "",
    class_count: int = 0,
    property_count: int = 0,
) -> BudgetVerificationResult:
    """Helper to construct a failed BudgetVerificationResult (SUG-02)."""
    return BudgetVerificationResult(
        disk_size_bytes=disk_size,
        memory_peak_bytes=peak_memory,
        cold_load_ms=cold_load_ms,
        sha256_full=sha256_full,
        sha256_8=sha256_8,
        class_count=class_count,
        property_count=property_count,
        is_valid=False,
        error_message=msg,
    )


def get_fibo_module(uri: str) -> str | None:
    """Determine which of the 6 core FIBO modules a URI belongs to.

    Strictly requires URI to begin with FIBO base prefix:
    https://spec.edmcouncil.org/fibo/ontology/<MODULE>/ (MIN-01).
    """
    if not uri.startswith(FIBO_BASE_URI_PREFIX):
        return None
    rest = uri[len(FIBO_BASE_URI_PREFIX) :]
    for m in CORE_MODULES:
        if rest.startswith(f"{m}/"):
            return m
    return None


def _pick_best_literal(literals: list[rdflib.Literal]) -> str:
    """Select English (@en) or untagged literal, falling back to lexicographical sort (MIN-03)."""
    valid = [lit for lit in literals if str(lit).strip()]
    if not valid:
        return ""
    en_lits = [
        str(lit).strip()
        for lit in valid
        if getattr(lit, "language", None)
        and getattr(lit, "language", "").lower().split("-")[0] == "en"
    ]
    if en_lits:
        return sorted(en_lits)[0]
    untagged = [str(lit).strip() for lit in valid if getattr(lit, "language", None) is None]
    if untagged:
        return sorted(untagged)[0]
    return sorted([str(lit).strip() for lit in valid])[0]


def _extract_annotations(
    g: rdflib.Graph, node: rdflib.URIRef
) -> tuple[str, str, list[str]]:
    """Extract (label, definition, synonyms) for an RDF node with language preference (MIN-04)."""
    raw_labels = [lit for lit in g.objects(node, RDFS.label) if isinstance(lit, rdflib.Literal)]
    label = _pick_best_literal(raw_labels)
    if not label:
        label = str(node).rstrip("/").split("/")[-1]

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
    definition = _pick_best_literal(raw_defs)

    syns = sorted(
        {
            str(lit).strip()
            for lit in g.objects(node, SKOS.altLabel)
            if isinstance(lit, rdflib.Literal) and str(lit).strip()
        }
    )
    return label, definition, syns


def extract_fibo_graph(g: rdflib.Graph) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract classes and properties for the 6 core modules from an RDF graph.

    Produces canonically-sorted, schema-compliant dictionary representations.
    """
    classes: dict[str, Any] = {}
    properties: dict[str, Any] = {}

    # 1. Extract Classes (owl:Class)
    for c in g.subjects(RDF.type, OWL.Class):
        if not isinstance(c, rdflib.URIRef):
            continue
        uri = str(c)
        mod = get_fibo_module(uri)
        if mod is None:
            continue

        label, definition, syns = _extract_annotations(g, c)

        # Extract taxonomic hierarchy (rdfs:subClassOf) - only keep named URIRefs
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

    # 2. Extract Properties (owl:ObjectProperty, owl:DatatypeProperty)
    property_type_specs = (
        (OWL.ObjectProperty, "ObjectProperty"),
        (OWL.DatatypeProperty, "DatatypeProperty"),
    )
    for prop_rdf_type, prop_type_name in property_type_specs:
        for p in g.subjects(RDF.type, prop_rdf_type):
            if not isinstance(p, rdflib.URIRef):
                continue
            uri = str(p)
            mod = get_fibo_module(uri)
            if mod is None:
                continue

            label, definition, syns = _extract_annotations(g, p)

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
                # Retain ObjectProperty priority if already recorded (MIN-05)
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
    upstream_tag: str = UPSTREAM_TAG,
    upstream_commit: str = UPSTREAM_COMMIT,
    compiled_at: str = DEFAULT_COMPILED_AT,
    modules: tuple[str, ...] = CORE_MODULES,
    is_fixture: bool = False,
) -> tuple[bytes, dict[str, Any], str, str]:
    """Assemble and serialize canonical RFC 8785 JSON pack.

    Returns tuple of (bytes, dict, sha256_full, sha256_8).
    Marks fixtures explicitly to prevent shipping mock data in production (MAJ-03).
    """
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
    sha256_8 = sha256_full[:8]
    return raw_bytes, pack, sha256_full, sha256_8


def verify_pack(
    pack_path: Path,
    *,
    allow_fixture: bool = False,
    require_production_counts: bool = False,
    expected_commit: str = UPSTREAM_COMMIT,
    expected_tag: str = UPSTREAM_TAG,
) -> BudgetVerificationResult:
    """Perform rigorous offline verification of a compiled FIBO pack against budgets and schema."""
    if not pack_path.is_file():
        return _fail_result(f"Pack file does not exist: {pack_path}")

    disk_size = pack_path.stat().st_size
    if disk_size >= MAX_DISK_SIZE_BYTES:
        return _fail_result(
            f"Disk size {disk_size} exceeds budget {MAX_DISK_SIZE_BYTES} bytes (<4.0 MB)",
            disk_size=disk_size,
        )

    raw_bytes = pack_path.read_bytes()
    sha256_full = hashlib.sha256(raw_bytes).hexdigest()
    sha256_8 = sha256_full[:8]

    # Pass 1: Cold load latency (best-of-3 runs to guard against host jitter) (MIN-04)
    cold_load_samples: list[float] = []
    try:
        t0 = time.perf_counter()
        data = json.loads(raw_bytes)
        t1 = time.perf_counter()
        cold_load_samples.append((t1 - t0) * 1000.0)
        for _ in range(2):
            t_s = time.perf_counter()
            _ = json.loads(raw_bytes)
            t_e = time.perf_counter()
            cold_load_samples.append((t_e - t_s) * 1000.0)
        cold_load_ms = min(cold_load_samples)
    except json.JSONDecodeError as err:
        return _fail_result(
            f"Invalid JSON in pack: {err}",
            disk_size=disk_size,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    if not isinstance(data, dict):
        return _fail_result(
            "Pack root must be a JSON object (dict)",
            disk_size=disk_size,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    # Pass 2: Peak heap memory in isolated pass (MIN-03)
    tracemalloc.start()
    _ = json.loads(raw_bytes)
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if peak_memory >= MAX_MEMORY_BYTES:
        return _fail_result(
            f"Peak memory {peak_memory} exceeds budget {MAX_MEMORY_BYTES} bytes (<18.0 MB)",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    if cold_load_ms >= MAX_LOAD_LATENCY_MS:
        return _fail_result(
            f"Cold load latency {cold_load_ms:.2f}ms exceeds budget {MAX_LOAD_LATENCY_MS}ms",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    # Verify canonical JCS bit-for-bit roundtrip
    re_canonical_bytes = dumps_jcs_bytes(data)
    if re_canonical_bytes != raw_bytes:
        return _fail_result(
            "Pack bytes do not conform bit-for-bit to RFC 8785 JCS canonicalization",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    # Verify filename prefix matches computed hash if named fibo-core-<sha256_8>.json
    filename = pack_path.name
    is_pattern_named = (
        filename.startswith("fibo-core-")
        and filename.endswith(".json")
        and filename != "fibo-core-pack.json"
    )
    if is_pattern_named:
        expected_prefix = filename[len("fibo-core-") : -len(".json")]
        if expected_prefix != sha256_8:
            return _fail_result(
                f"Filename hash prefix '{expected_prefix}' does not match digest '{sha256_8}'",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )

    # Schema structure verification
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return _fail_result(
            "Missing or invalid 'metadata' object in pack",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    # Reject fake mock packs in production mode (MAJ-03)
    if not allow_fixture and (
        metadata.get("is_fixture") is True or metadata.get("pack_id") != "fibo-core"
    ):
        return _fail_result(
            "Pack is marked as synthetic fixture; production pack required",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    # Check metadata modules type (MIN-01)
    modules_in_pack = metadata.get("modules")
    if not isinstance(modules_in_pack, list) or not all(
        isinstance(m, str) for m in modules_in_pack
    ):
        return _fail_result(
            "Pack metadata.modules must be a list of strings",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )
    for m in CORE_MODULES:
        if m not in modules_in_pack:
            return _fail_result(
                f"Required module '{m}' not present in pack metadata",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )

    classes = data.get("classes")
    properties = data.get("properties")
    if not isinstance(classes, dict) or not isinstance(properties, dict):
        return _fail_result(
            "'classes' and 'properties' must be JSON objects",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    if not classes or not properties:
        return _fail_result(
            "Pack 'classes' or 'properties' collection is unexpectedly empty",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
            class_count=len(classes),
            property_count=len(properties),
        )

    # Enforce production criteria: minimum counts & verified provenance (MAJ-01, MAJ-03)
    if require_production_counts:
        up_commit = metadata.get("upstream_commit")
        up_tag = metadata.get("upstream_tag")
        if (
            up_commit != expected_commit
            or up_tag != expected_tag
            or str(up_commit).startswith("unverified-")
        ):
            return _fail_result(
                f"Production pack metadata must match pinned commit {expected_commit} "
                f"and tag {expected_tag}, got commit='{up_commit}', tag='{up_tag}'",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
                class_count=len(classes),
                property_count=len(properties),
            )

        if len(classes) < MIN_PRODUCTION_CLASSES:
            return _fail_result(
                f"Class count {len(classes)} below production minimum {MIN_PRODUCTION_CLASSES}",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
                class_count=len(classes),
                property_count=len(properties),
            )
        if len(properties) < MIN_PRODUCTION_PROPERTIES:
            err = (
                f"Property count {len(properties)} below production min "
                f"{MIN_PRODUCTION_PROPERTIES}"
            )
            return _fail_result(
                err,
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
                class_count=len(classes),
                property_count=len(properties),
            )

    # Validate per-entry schema and key consistency (MIN-02)
    for uri, c_data in classes.items():
        if not isinstance(c_data, dict) or c_data.get("uri") != uri:
            return _fail_result(
                f"Invalid class entry for {uri}: key must equal entry URI",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )
        if c_data.get("module") != get_fibo_module(uri):
            return _fail_result(
                f"Module mismatch in class {uri}",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )

    for uri, p_data in properties.items():
        if not isinstance(p_data, dict) or p_data.get("uri") != uri:
            return _fail_result(
                f"Invalid property entry for {uri}: key must equal entry URI",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )
        if p_data.get("module") != get_fibo_module(uri):
            return _fail_result(
                f"Module mismatch in property {uri}",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )
        if p_data.get("property_type") not in ("ObjectProperty", "DatatypeProperty"):
            return _fail_result(
                f"Invalid property_type for {uri}: {p_data.get('property_type')}",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )

    # Verify at least one class exists per core module
    represented = {
        item.get("module") for item in classes.values() if isinstance(item, dict)
    }
    for m in CORE_MODULES:
        if m not in represented:
            return _fail_result(
                f"No classes found for core module '{m}' in compiled pack",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
                class_count=len(classes),
                property_count=len(properties),
            )

    return BudgetVerificationResult(
        disk_size_bytes=disk_size,
        memory_peak_bytes=peak_memory,
        cold_load_ms=cold_load_ms,
        sha256_full=sha256_full,
        sha256_8=sha256_8,
        class_count=len(classes),
        property_count=len(properties),
        is_valid=True,
    )


def create_mock_fibo_graph() -> rdflib.Graph:
    """Create a synthetic in-memory RDF graph containing valid concepts across all 6 core modules.

    Used for offline CI testing and network-free compilation verification.
    """
    g = rdflib.Graph()

    specs = [
        # 1. FND (Foundations)
        (
            "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/Agreement",
            "agreement",
            "A negotiated and legally enforceable understanding between two or more parties.",
            ["understanding", "compact"],
            None,
            "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/hasParty",
            "has party",
            "ObjectProperty",
            "Identifies an independent party associated with an agreement or contract.",
        ),
        (
            "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract",
            "contract",
            "An agreement between two or more parties creating legally binding obligations.",
            ["binding agreement"],
            "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/Agreement",
            None,
            None,
            None,
            None,
        ),
        # 2. BE (Business Entities)
        (
            "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
            "legal entity",
            "An entity that is capable of bearing legal rights and obligations.",
            ["juristic person"],
            None,
            "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/hasLegalAddress",
            "has legal address",
            "ObjectProperty",
            "Specifies the legal or registered address of the entity.",
        ),
        (
            "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Corporations/Corporation",
            "corporation",
            "A formal business organization created under the laws of a state.",
            ["company", "corporate body"],
            "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity",
            None,
            None,
            None,
            None,
        ),
        # 3. FBC (Financial Business & Commerce)
        (
            "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstrument",
            "financial instrument",
            "A contract giving rise to a financial asset and financial liability.",
            ["tradable asset"],
            None,
            "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/hasNominalValue",
            "has nominal value",
            "DatatypeProperty",
            "The stated nominal or par value of an issued financial security or loan.",
        ),
        (
            "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Security",
            "security",
            "A fungible, negotiable financial instrument that holds monetary value.",
            ["investment security"],
            "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstrument",
            None,
            None,
            None,
            None,
        ),
        # 4. SEC (Securities)
        (
            "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/Share",
            "share",
            "A unit of equity ownership in a corporation or financial instrument.",
            ["stock unit", "equity share"],
            "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Security",
            "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/hasMaturityDate",
            "has maturity date",
            "DatatypeProperty",
            "The date on which the principal amount of a debt security becomes due.",
        ),
        (
            "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/Bond",
            "bond",
            "A debt instrument issued by an entity for a specified period to raise capital.",
            ["fixed income security"],
            "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Security",
            None,
            None,
            None,
            None,
        ),
        # 5. LOAN (Loans)
        (
            "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/Loans/Loan",
            "loan",
            "A financing agreement in which a lender advances funds to a borrower.",
            ["credit facility"],
            "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract",
            "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/Loans/hasPrincipal",
            "has principal",
            "DatatypeProperty",
            "The original sum of money lent on which interest and repayment are calculated.",
        ),
        (
            "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/Loans/CollateralizedLoan",
            "collateralized loan",
            "A loan secured by borrower assets or pledges.",
            ["secured loan"],
            "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/Loans/Loan",
            None,
            None,
            None,
            None,
        ),
        # 6. IND (Indices & Indicators)
        (
            "https://spec.edmcouncil.org/fibo/ontology/IND/Indicators/Indicators/FinancialIndex",
            "financial index",
            "A numerical index representing the statistical value of a portfolio.",
            ["market index", "benchmark index"],
            None,
            "https://spec.edmcouncil.org/fibo/ontology/IND/InterestRates/hasBenchmarkRate",
            "has benchmark rate",
            "ObjectProperty",
            "Associates a financial instrument with an underlying benchmark interest rate.",
        ),
        (
            "https://spec.edmcouncil.org/fibo/ontology/IND/InterestRates/BenchmarkInterestRate",
            "benchmark interest rate",
            "A reference interest rate used as a standardized baseline for financial contracts.",
            ["reference rate"],
            "https://spec.edmcouncil.org/fibo/ontology/IND/Indicators/Indicators/FinancialIndex",
            None,
            None,
            None,
            None,
        ),
    ]

    for (
        class_uri,
        class_label,
        class_def,
        class_syns,
        parent_uri,
        prop_uri,
        prop_label,
        prop_type,
        prop_def,
    ) in specs:
        c_ref = rdflib.URIRef(class_uri)
        g.add((c_ref, RDF.type, OWL.Class))
        g.add((c_ref, RDFS.label, rdflib.Literal(class_label)))
        g.add((c_ref, SKOS.definition, rdflib.Literal(class_def)))
        for s in class_syns:
            g.add((c_ref, SKOS.altLabel, rdflib.Literal(s)))
        if parent_uri:
            g.add((c_ref, RDFS.subClassOf, rdflib.URIRef(parent_uri)))

        if prop_uri and prop_label and prop_type and prop_def:
            p_ref = rdflib.URIRef(prop_uri)
            p_type_ref = (
                OWL.ObjectProperty if prop_type == "ObjectProperty" else OWL.DatatypeProperty
            )
            g.add((p_ref, RDF.type, p_type_ref))
            g.add((p_ref, RDFS.label, rdflib.Literal(prop_label)))
            g.add((p_ref, SKOS.definition, rdflib.Literal(prop_def)))
            g.add((p_ref, RDFS.domain, c_ref))

    return g


MAX_ARCHIVE_MEMBERS: int = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES: int = 200 * 1024 * 1024  # 200 MB


def safe_extract_archive(archive_path: Path, target_dir: Path) -> Path:
    """Safely extract tar or zip archives, preventing traversal vulnerabilities (MIN-01, SUG-02).

    Enforces path resolution with is_relative_to, rejects symlinks and hardlinks outright,
    enforces uncompressed member count and byte limits, and extracts into temporary staging.
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target_dir.parent) as temp_staging:
        staging_dir = (Path(temp_staging) / "contents").resolve()
        staging_dir.mkdir(parents=True, exist_ok=True)

        if archive_path.name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(archive_path, "r:*") as tar:
                members = tar.getmembers()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    msg = (
                        f"Archive member count ({len(members)}) exceeds limit: "
                        f"{MAX_ARCHIVE_MEMBERS}"
                    )
                    raise ValueError(msg)
                total_bytes = 0
                for member in members:
                    # 1. Path traversal check on member name using is_relative_to
                    dest_path = (staging_dir / member.name).resolve()
                    if not dest_path.is_relative_to(staging_dir):
                        msg = f"Path traversal attempt detected in tar archive: {member.name}"
                        raise ValueError(msg)

                    # 2. Outright rejection of symlinks, hardlinks, devices, and FIFOs (MIN-01)
                    if (
                        member.issym()
                        or member.islnk()
                        or member.isdev()
                        or member.ischr()
                        or member.isblk()
                        or member.isfifo()
                    ):
                        msg = f"Links and special files prohibited in FIBO archive: {member.name}"
                        raise ValueError(msg)

                    total_bytes += member.size
                    if total_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        msg = (
                            f"Archive uncompressed size exceeds limit "
                            f"({MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes)"
                        )
                        raise ValueError(msg)

                if hasattr(tarfile, "data_filter"):
                    tar.extractall(staging_dir, filter="data")
                else:
                    tar.extractall(staging_dir)

        elif archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                infolist = zf.infolist()
                if len(infolist) > MAX_ARCHIVE_MEMBERS:
                    msg = (
                        f"Archive member count ({len(infolist)}) exceeds limit: "
                        f"{MAX_ARCHIVE_MEMBERS}"
                    )
                    raise ValueError(msg)
                total_bytes = 0
                for info in infolist:
                    dest_path = (staging_dir / info.filename).resolve()
                    if not dest_path.is_relative_to(staging_dir):
                        msg = f"Path traversal attempt detected in zip archive: {info.filename}"
                        raise ValueError(msg)
                    total_bytes += info.file_size
                    if total_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        msg = (
                            f"Archive uncompressed size exceeds limit "
                            f"({MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes)"
                        )
                        raise ValueError(msg)
                zf.extractall(staging_dir)
        else:
            msg = f"Unsupported archive format for {archive_path}; expected .tar.gz, .tgz, or .zip"
            raise ValueError(msg)

        # Atomic promotion into target directory (MIN-02)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(staging_dir), str(target_dir))

    return target_dir


def load_fibo_from_directory(
    repo_root: Path,
    *,
    allow_parse_errors: bool = False,
) -> rdflib.Graph:
    """Recursively scan and parse RDF/OWL/Turtle ontology files for the 6 core modules.

    Matches directories deterministically preferring shallowest match (MIN-02).
    Fails on parse errors in production mode to prevent silent graph truncation (MIN-04).
    """
    g = rdflib.Graph()
    total_files_scanned = 0
    found_files = 0
    parse_errors: list[str] = []

    for mod_name in CORE_MODULES:
        candidates = [
            repo_root / mod_name,
            repo_root / "fibo" / mod_name,
            repo_root / "ontology" / mod_name,
        ]
        target_mod_dir = next((c for c in candidates if c.is_dir()), None)
        if target_mod_dir is None:
            # Deterministic shallowest search across directory tree (MIN-02)
            matching_dirs = sorted(
                [p for p in repo_root.rglob(mod_name) if p.is_dir()],
                key=lambda p: (len(p.parts), str(p)),
            )
            target_mod_dir = matching_dirs[0] if matching_dirs else None

        if target_mod_dir is None:
            print(
                f"Warning: Module directory '{mod_name}' not found under {repo_root}",
                file=sys.stderr,
            )
            continue

        mod_files = sorted(
            [p for p in target_mod_dir.rglob("*") if p.suffix.lower() in (".rdf", ".owl", ".ttl")]
        )
        for f in mod_files:
            total_files_scanned += 1
            try:
                g.parse(str(f))
                found_files += 1
            except Exception as err:  # noqa: BLE001
                err_msg = f"{f.name}: {err}"
                parse_errors.append(err_msg)
                print(f"Warning: Failed to parse ontology file {f}: {err}", file=sys.stderr)

    if total_files_scanned == 0:
        msg = f"No ontology files (.rdf, .owl, .ttl) found for core modules in {repo_root}"
        raise RuntimeError(msg)

    if parse_errors and not allow_parse_errors:
        msg = (
            f"Production compile failed: {len(parse_errors)} ontology files had parse errors. "
            f"First error: {parse_errors[0]}"
        )
        raise RuntimeError(msg)

    return g


SCRIPT_CACHE_MARKER: str = ".opendv_cache_manifest.json"


def is_production_target(target_path: Path) -> bool:
    """Check if target path is inside production packages/ directory (MIN-07)."""
    try:
        resolved = target_path.resolve()
        prod_pkg_dir = (REPO_ROOT / "packages").resolve()
        return resolved == prod_pkg_dir or resolved.is_relative_to(prod_pkg_dir)
    except (ValueError, OSError):
        return "packages" in target_path.parts


def ensure_fibo_source(
    *,
    cache_dir: Path,
    offline_archive: Path | None = None,
    archive_sha256: str | None = None,
    repo_url: str = UPSTREAM_REPO,
    tag: str = UPSTREAM_TAG,
    expected_commit: str = UPSTREAM_COMMIT,
    is_production: bool = False,
) -> tuple[Path, str]:
    """Ensure FIBO repository files are locally available and verify git provenance.

    Returns tuple of (source_directory, verified_upstream_commit).
    Protects user directories from deletion (MAJ-03), separates offline and git caches (MAJ-02),
    verifies git commit on checkout/clone (MAJ-01), and checks working-tree cleanliness (MIN-03).
    """
    if not re.match(r"^[0-9a-f]{40}$", expected_commit.lower()):
        msg = f"Invalid expected commit '{expected_commit}'; must be a 40-character hex SHA"
        raise ValueError(msg)
    if tag.startswith("-"):
        msg = f"Invalid tag '{tag}'; cannot start with '-'"
        raise ValueError(msg)

    manifest_path = cache_dir / SCRIPT_CACHE_MARKER

    # Validate cache_dir ownership if non-empty (MAJ-03)
    if cache_dir.exists() and any(cache_dir.iterdir()) and not manifest_path.is_file():
        msg = (
            f"Refusing to mutate or overwrite unmanaged directory '{cache_dir}'. "
            f"Directory exists and does not contain manifest ({SCRIPT_CACHE_MARKER})."
        )
        raise RuntimeError(msg)

    cache_dir.mkdir(parents=True, exist_ok=True)
    if not manifest_path.is_file():
        manifest_path.write_text(
            json.dumps({"created_by": "opendv-vendor-fibo"}),
            encoding="utf-8",
        )

    # 1. Offline Archive Handling with Separate Subtree (MAJ-02)
    if offline_archive is not None:
        if not offline_archive.is_file():
            msg = f"Offline archive not found: {offline_archive}"
            raise FileNotFoundError(msg)
        actual_archive_sha = _hash_file_sha256(offline_archive)

        if archive_sha256 is not None and actual_archive_sha.lower() != archive_sha256.lower():
            msg = (
                f"Offline archive SHA-256 mismatch for {offline_archive}: "
                f"computed {actual_archive_sha}, expected {archive_sha256}"
            )
            raise RuntimeError(msg)

        extract_dir = cache_dir / "offline" / f"archive_{actual_archive_sha[:12]}"
        archive_manifest = extract_dir / ".opendv_archive_manifest.json"

        # Re-extract if directory missing or during production compiles (MIN-02)
        if is_production or not (extract_dir.is_dir() and archive_manifest.is_file()):
            print(f"Extracting offline archive {offline_archive} to {extract_dir}...")
            safe_extract_archive(offline_archive, extract_dir)
            archive_manifest.write_text(
                json.dumps({
                    "archive_sha256": actual_archive_sha,
                    "created_by": "opendv-vendor-fibo",
                    "extracted_at": datetime.now(UTC).isoformat(),
                    "source_archive": offline_archive.name,
                }),
                encoding="utf-8",
            )

        # Check against certified baseline digest if configured (MAJ-01)
        if PINNED_ARCHIVE_SHA256 and actual_archive_sha.lower() == PINNED_ARCHIVE_SHA256.lower():
            return extract_dir, expected_commit

        # In production mode, reject untrusted offline archives (MAJ-01)
        if is_production:
            msg = (
                "Cannot compile to production package from unverified offline archive. "
                "Production package requires verified git clone provenance or certified "
                "baseline archive digest; untrusted offline archives prohibited (MAJ-01)."
            )
            raise RuntimeError(msg)

        verified_commit = f"unverified-{actual_archive_sha[:12]}"
        return extract_dir, verified_commit

    # 2. Git Cache Handling (separate git subtree, manifest outside working tree) (MAJ-02)
    git_cache_dir = cache_dir / "git"
    git_manifest = cache_dir / "git_manifest.json"

    if git_cache_dir.is_dir() and (git_cache_dir / ".git" / "config").is_file():
        # Validate remote origin URL matches requested repo (MIN-03)
        remote_cmd = [GIT_BIN, "-C", str(git_cache_dir), "remote", "get-url", "origin"]
        remote_proc = subprocess.run(remote_cmd, capture_output=True, text=True, check=False)
        if (
            remote_proc.returncode == 0
            and remote_proc.stdout.strip()
            and remote_proc.stdout.strip() != repo_url
        ):
            shutil.rmtree(git_cache_dir)

    if git_cache_dir.is_dir() and (git_cache_dir / ".git").is_dir():
        rev_cmd = [GIT_BIN, "-C", str(git_cache_dir), "rev-parse", "HEAD"]
        rev_proc = subprocess.run(rev_cmd, capture_output=True, text=True, check=False)
        if rev_proc.returncode != 0:
            msg = f"Corrupted git cache in {git_cache_dir}: rev-parse HEAD failed"
            raise RuntimeError(msg)
        actual_head = rev_proc.stdout.strip()

        if actual_head != expected_commit:
            co_cmd = [
                GIT_BIN,
                "-C",
                str(git_cache_dir),
                "checkout",
                "--detach",
                expected_commit,
                "--",
            ]
            co_proc = subprocess.run(co_cmd, capture_output=True, text=True, check=False)
            if co_proc.returncode != 0:
                fetch_cmd = [
                    GIT_BIN,
                    "-C",
                    str(git_cache_dir),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    expected_commit,
                ]
                subprocess.run(fetch_cmd, capture_output=True, text=True, check=False)
                co_proc = subprocess.run(co_cmd, capture_output=True, text=True, check=False)

            rev2_proc = subprocess.run(rev_cmd, capture_output=True, text=True, check=False)
            new_head = rev2_proc.stdout.strip() if rev2_proc.returncode == 0 else ""
            if new_head != expected_commit:
                msg = (
                    f"Failed to check out expected commit {expected_commit} in {git_cache_dir}. "
                    f"Current HEAD is {new_head or 'unknown'}. "
                    "Never writing unverified commit (MAJ-01)."
                )
                raise RuntimeError(msg)
            actual_head = new_head

        # Clean working tree check (MIN-03)
        status_cmd = [
            GIT_BIN,
            "-C",
            str(git_cache_dir),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ]
        status_proc = subprocess.run(status_cmd, capture_output=True, text=True, check=False)
        if status_proc.returncode != 0 or status_proc.stdout.strip():
            msg = (
                f"Cached git repository at {git_cache_dir} has uncommitted or untracked changes. "
                "Refusing to compile from dirty tree (MIN-03)."
            )
            raise RuntimeError(msg)

        git_manifest.write_text(
            json.dumps({
                "cached_at": datetime.now(UTC).isoformat(),
                "commit": actual_head,
                "created_by": "opendv-vendor-fibo",
                "tag": tag,
            }),
            encoding="utf-8",
        )
        return git_cache_dir, actual_head

    # 3. Fresh Shallow Clone into git_cache_dir with Temp Sibling Staging (MAJ-02, MAJ-03)
    if git_cache_dir.exists():
        shutil.rmtree(git_cache_dir)
    git_cache_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=git_cache_dir.parent) as temp_clone_parent:
        staging_clone_dir = Path(temp_clone_parent) / "fibo_repo"
        print(f"Cloning FIBO tag '{tag}' from {repo_url} into {staging_clone_dir}...")
        cmd = [
            GIT_BIN,
            "clone",
            "--depth",
            "1",
            "--branch",
            tag,
            "--",
            repo_url,
            str(staging_clone_dir),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            msg = (
                f"Failed to clone FIBO repository:\n{proc.stderr}\n"
                "If network is unavailable, provide an offline archive via --offline-archive "
                "or use --mock-fixture."
            )
            raise RuntimeError(msg)

        rev_cmd = [GIT_BIN, "-C", str(staging_clone_dir), "rev-parse", "HEAD"]
        rev_proc = subprocess.run(rev_cmd, capture_output=True, text=True, check=False)
        if rev_proc.returncode != 0:
            msg = f"git rev-parse HEAD failed on cloned repository {staging_clone_dir}"
            raise RuntimeError(msg)

        verified_head = rev_proc.stdout.strip()
        if verified_head != expected_commit:
            msg = (
                f"Cloned repository HEAD ({verified_head}) does not match expected "
                f"pinned commit ({expected_commit}) for tag '{tag}' (MAJ-02)."
            )
            raise RuntimeError(msg)

        # Do NOT write untracked manifest files inside cloned repository working tree (MAJ-02)
        staging_clone_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_clone_dir), str(git_cache_dir))

        git_manifest.write_text(
            json.dumps({
                "cached_at": datetime.now(UTC).isoformat(),
                "commit": verified_head,
                "created_by": "opendv-vendor-fibo",
                "head": verified_head,
                "tag": tag,
            }),
            encoding="utf-8",
        )

    return git_cache_dir, verified_head


def main(argv: list[str] | None = None) -> int:
    """CLI Entry point for FIBO compilation and verification."""
    _init_windows_platform()
    parser = argparse.ArgumentParser(
        description="FIBO compilation and verification script for Open-DV Ontology Studio.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Target directory for compiled canonical JSON pack",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Local cache directory for cloned FIBO repository",
    )
    parser.add_argument(
        "--offline-archive",
        type=Path,
        default=None,
        help="Optional path to local .tar.gz/.zip archive of FIBO repository",
    )
    parser.add_argument(
        "--archive-sha256",
        type=str,
        default=None,
        help="Expected SHA-256 hex digest of offline archive for provenance validation",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        default=False,
        help="Enforce production verification criteria regardless of target directory",
    )
    parser.add_argument(
        "--mock-fixture",
        action="store_true",
        help="Compile synthetic mock fixture graph (for offline testing / CI)",
    )
    parser.add_argument(
        "--verify-pack",
        nargs="?",
        const="",
        default=None,
        help="Verify an existing compiled pack against schema and budgets (optional file path)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify the existing pack in output-dir without re-compiling",
    )
    parser.add_argument(
        "--compiled-at",
        type=str,
        default=DEFAULT_COMPILED_AT,
        help="ISO timestamp to record in pack metadata for reproducible hashing",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=UPSTREAM_TAG,
        help="FIBO release tag",
    )
    parser.add_argument(
        "--commit",
        type=str,
        default=UPSTREAM_COMMIT,
        help="FIBO pinned upstream commit",
    )
    parser.add_argument(
        "--repo-url",
        type=str,
        default=UPSTREAM_REPO,
        help="FIBO upstream git repository URL",
    )

    args = parser.parse_args(argv)

    # Security check: Prohibit writing mock fixture into production package directory (MAJ-03)
    is_prod_target = args.production or is_production_target(args.output_dir)
    if args.mock_fixture and is_prod_target:
        msg = (
            "Error: --mock-fixture requires an explicit non-production --output-dir "
            "(refusing to write synthetic test fixtures into production package sources)."
        )
        print(msg, file=sys.stderr)
        return 1

    # 1. Verification Mode (--verify-only or --verify-pack)
    if args.verify_only or args.verify_pack is not None:
        target_pack: Path | None = None
        if args.verify_pack and args.verify_pack != "":
            target_pack = Path(args.verify_pack).resolve()
        else:
            # Exclude alias fibo-core-pack.json when searching candidates (MAJ-02)
            candidate_files = sorted(
                [
                    p
                    for p in args.output_dir.glob("fibo-core-*.json")
                    if p.name != "fibo-core-pack.json"
                ]
            )
            if len(candidate_files) > 1:
                # Ambiguous candidate packs detected (MIN-05)
                print(
                    f"Error: Multiple hash-named packs found in {args.output_dir}: "
                    f"{[p.name for p in candidate_files]}. Please clean up obsolete packs.",
                    file=sys.stderr,
                )
                return 1
            if candidate_files:
                target_pack = candidate_files[0]
            elif (args.output_dir / "fibo-core-pack.json").is_file():
                target_pack = args.output_dir / "fibo-core-pack.json"

        if target_pack is None or not target_pack.is_file():
            print(f"Error: No FIBO pack found to verify in {args.output_dir}", file=sys.stderr)
            return 1

        print(f"Verifying FIBO reference pack: {target_pack}")
        # Enforce production criteria based on resolved target pack location (MIN-05, MIN-07)
        is_prod = args.production or is_production_target(target_pack)
        result = verify_pack(
            target_pack,
            allow_fixture=not is_prod,
            require_production_counts=is_prod,
            expected_commit=args.commit,
            expected_tag=args.tag,
        )
        if not result.is_valid:
            print(f"❌ FIBO Pack Verification FAILED: {result.error_message}", file=sys.stderr)
            return 1

        # Check that alias file, if present, matches hash-named pack bit-for-bit (MAJ-02)
        alias_file = target_pack.parent / "fibo-core-pack.json"
        if (
            alias_file.is_file()
            and target_pack != alias_file
            and alias_file.read_bytes() != target_pack.read_bytes()
        ):
            print(
                "❌ Alias fibo-core-pack.json does not match hash-named pack bit-for-bit",
                file=sys.stderr,
            )
            return 1

        print("✅ FIBO Pack Verification PASSED:")
        print(f"  - File: {target_pack.name}")
        print(f"  - Canonical SHA-256: {result.sha256_full}")
        print(f"  - Hash prefix: {result.sha256_8}")
        print(f"  - Classes: {result.class_count}")
        print(f"  - Properties: {result.property_count}")
        disk_mb = result.disk_size_bytes / 1024 / 1024
        peak_mb = result.memory_peak_bytes / 1024 / 1024
        print(f"  - Disk size: {disk_mb:.2f} MB (< 4.0 MB budget)")
        print(f"  - Peak memory: {peak_mb:.2f} MB (< 18.0 MB budget)")
        print(f"  - Cold load: {result.cold_load_ms:.2f} ms (< 70.0 ms budget)")
        return 0

    # 2. Compilation Mode
    print(f"Initiating FIBO Core Pack compilation for tag '{args.tag}' (commit {args.commit})...")
    verified_commit = args.commit
    if args.mock_fixture:
        print("Using synthetic mock fixture graph...")
        g = create_mock_fibo_graph()
    else:
        try:
            repo_path, verified_commit = ensure_fibo_source(
                cache_dir=args.cache_dir,
                offline_archive=args.offline_archive,
                archive_sha256=args.archive_sha256,
                repo_url=args.repo_url,
                tag=args.tag,
                expected_commit=args.commit,
                is_production=is_prod_target,
            )
            print(f"Parsing ontology files from {repo_path} (commit: {verified_commit})...")
            g = load_fibo_from_directory(repo_path, allow_parse_errors=False)
        except (RuntimeError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as err:
            print(f"Error preparing FIBO source: {err}", file=sys.stderr)
            return 1

    print(f"Extracting classes and properties across 6 core modules: {', '.join(CORE_MODULES)}...")
    classes, properties = extract_fibo_graph(g)
    print(f"Extracted {len(classes)} classes and {len(properties)} properties.")

    # Write verified commit into pack metadata (MAJ-01)
    raw_bytes, _, sha256_full, sha256_8 = compile_fibo_pack(
        classes,
        properties,
        upstream_tag=args.tag,
        upstream_commit=verified_commit,
        compiled_at=args.compiled_at,
        modules=CORE_MODULES,
        is_fixture=args.mock_fixture,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Safe atomic verification before committing files to production (MAJ-02, MIN-02)
    canonical_filename = f"fibo-core-{sha256_8}.json"
    staging_path = args.output_dir / f".staging-{sha256_8}.tmp"
    alias_staging = args.output_dir / ".staging-pack.tmp"
    try:
        staging_path.write_bytes(raw_bytes)

        # Validate staging artifact against budgets before replacing prior pack (MAJ-01)
        verification = verify_pack(
            staging_path,
            allow_fixture=args.mock_fixture or not is_prod_target,
            require_production_counts=is_prod_target and not args.mock_fixture,
            expected_commit=args.commit,
            expected_tag=args.tag,
        )
        if not verification.is_valid:
            msg = f"❌ Budget verification FAILED for compiled pack: {verification.error_message}"
            print(msg, file=sys.stderr)
            return 1

        # Verification succeeded: atomically replace canonical and alias files (MAJ-02)
        canonical_output_path = args.output_dir / canonical_filename
        os.replace(staging_path, canonical_output_path)
        print(f"Committed canonical pack: {canonical_output_path} ({len(raw_bytes)} bytes)")

        alias_staging.write_bytes(raw_bytes)
        pack_alias_path = args.output_dir / "fibo-core-pack.json"
        os.replace(alias_staging, pack_alias_path)

        # Create __init__.py only after verification succeeds for production targets (MIN-05)
        if is_prod_target:
            init_file = args.output_dir / "__init__.py"
            if not init_file.exists():
                init_file.write_text(
                    '"""FIBO reference ontology compiled core pack package."""\n',
                    encoding="utf-8",
                )
    finally:
        staging_path.unlink(missing_ok=True)
        alias_staging.unlink(missing_ok=True)

    # Clean up obsolete hash-named packs safely after atomic promotion (MAJ-02, MIN-05)
    for old_pack in args.output_dir.glob("fibo-core-*.json"):
        if (
            re.match(r"^fibo-core-[0-9a-f]{8}\.json$", old_pack.name)
            and old_pack.name != canonical_filename
        ):
            old_pack.unlink()

    print("✅ Compilation and Budget Verification Successful:")
    print(f"  - Canonical File: {canonical_filename}")
    print(f"  - SHA-256 Digest: {sha256_full}")
    print(f"  - Disk Size: {verification.disk_size_bytes / 1024 / 1024:.2f} MB")
    print(f"  - Peak Memory: {verification.memory_peak_bytes / 1024 / 1024:.2f} MB")
    print(f"  - Cold Load Time: {verification.cold_load_ms:.2f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
