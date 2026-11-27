#!/usr/bin/env python3
"""Pack v2 schema, budget (4MB/18MB/70ms), JCS, and starter_packs Path A verification."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.vendor_fibo_extract import (  # noqa: E402
    ALL_V2_MODULES,
    CMNS_BASE_URI_PREFIX,
    CORE_MODULES,
    DEFAULT_PINNED_COMMIT,
    DEFAULT_PINNED_TAG,
    get_fibo_module,
)

from opendv.grounding.providers.fibo.closure_rules import (  # noqa: E402
    can_satisfy_path_a_grounding,
    validate_pack_v2_data,
)
from opendv.grounding.providers.fibo.loader import (  # noqa: E402
    FiboPackQueryAdapter,
    FiboReferenceOntologyLoader,
)
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError  # noqa: E402
from opendv.models.json_hasher import dumps_jcs_bytes  # noqa: E402

MAX_DISK_SIZE_BYTES = 4 * 1024 * 1024
MAX_MEMORY_BYTES = 18 * 1024 * 1024
MAX_LOAD_LATENCY_MS = 70.0
MIN_PRODUCTION_CLASSES = 2000
MIN_PRODUCTION_PROPERTIES = 700
MAX_PRODUCTION_INDIVIDUALS = 2500

_DIGEST_FILENAME_RE = re.compile(r"^fibo-.+-([0-9a-f]{8})\.json$")
_STARTER_MIN_CONFIDENCE = Decimal("0.6500")


@dataclass(frozen=True)
class BudgetVerificationResult:
    """Outcome of validating a compiled FIBO JSON pack against budgets and Pack v2 schema."""

    disk_size_bytes: int
    memory_peak_bytes: int
    cold_load_ms: float
    sha256_full: str
    sha256_8: str
    class_count: int
    property_count: int
    is_valid: bool
    error_message: str | None = None
    individual_count: int = 0


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
    individual_count: int = 0,
) -> BudgetVerificationResult:
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
        individual_count=individual_count,
    )


def verify_pack(
    pack_path: Path,
    *,
    allow_fixture: bool = False,
    require_production_counts: bool = False,
    expected_commit: str = DEFAULT_PINNED_COMMIT,
    expected_tag: str = DEFAULT_PINNED_TAG,
    required_modules: Sequence[str] | None = None,
) -> BudgetVerificationResult:
    """Verify a compiled FIBO Pack v2 against size, memory, latency, JCS, and schema."""
    if not pack_path.is_file():
        return _fail_result(f"Pack file does not exist: {pack_path}")

    disk_size = pack_path.stat().st_size
    if disk_size >= MAX_DISK_SIZE_BYTES:
        return _fail_result(
            f"Disk size {disk_size} exceeds budget {MAX_DISK_SIZE_BYTES} bytes",
            disk_size=disk_size,
        )

    raw_bytes = pack_path.read_bytes()
    sha256_full = hashlib.sha256(raw_bytes).hexdigest()
    sha256_8 = sha256_full[:8]

    samples: list[float] = []
    try:
        t0 = time.perf_counter()
        data = json.loads(raw_bytes)
        samples.append((time.perf_counter() - t0) * 1000.0)
        for _ in range(2):
            ts = time.perf_counter()
            _ = json.loads(raw_bytes)
            samples.append((time.perf_counter() - ts) * 1000.0)
        cold_load_ms = min(samples)
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

    tracemalloc.start()
    _ = json.loads(raw_bytes)
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if peak_memory >= MAX_MEMORY_BYTES:
        return _fail_result(
            f"Peak memory {peak_memory} exceeds budget {MAX_MEMORY_BYTES} bytes",
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
    if dumps_jcs_bytes(data) != raw_bytes:
        return _fail_result(
            "Pack bytes do not conform bit-for-bit to RFC 8785 JCS canonicalization",
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    filename = pack_path.name
    m_fn = _DIGEST_FILENAME_RE.match(filename)
    if m_fn and filename != "fibo-core-pack.json":
        expected_prefix = m_fn.group(1)
        if expected_prefix != sha256_8:
            return _fail_result(
                f"Filename hash prefix '{expected_prefix}' does not match digest '{sha256_8}'",
                disk_size=disk_size,
                peak_memory=peak_memory,
                cold_load_ms=cold_load_ms,
                sha256_full=sha256_full,
                sha256_8=sha256_8,
            )

    try:
        metadata, classes, properties, individuals = validate_pack_v2_data(data)
    except ReferenceOntologyCorruptError as err:
        return _fail_result(
            str(err),
            disk_size=disk_size,
            peak_memory=peak_memory,
            cold_load_ms=cold_load_ms,
            sha256_full=sha256_full,
            sha256_8=sha256_8,
        )

    if not allow_fixture and (
        metadata.get("is_fixture") is True or metadata.get("pack_id") != "fibo-core"
    ):
        return _fail_result("Pack is marked as synthetic fixture; production pack required")

    req_mods = tuple(required_modules) if required_modules is not None else CORE_MODULES
    modules_in_pack = metadata.get("modules")
    if not isinstance(modules_in_pack, list) or any(m not in modules_in_pack for m in req_mods):
        return _fail_result("Required core modules missing in pack metadata")

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
                f"and tag {expected_tag}, got commit='{up_commit}', tag='{up_tag}'"
            )
        if len(classes) < MIN_PRODUCTION_CLASSES:
            return _fail_result(f"Class count {len(classes)} below {MIN_PRODUCTION_CLASSES}")
        if len(properties) < MIN_PRODUCTION_PROPERTIES:
            return _fail_result(
                f"Property count {len(properties)} below {MIN_PRODUCTION_PROPERTIES}"
            )
        if not individuals or len(individuals) > MAX_PRODUCTION_INDIVIDUALS:
            return _fail_result(
                f"Individual count {len(individuals)} out of bounds "
                f"(1..{MAX_PRODUCTION_INDIVIDUALS})"
            )

    for uri, c_data in classes.items():
        if c_data.get("uri") != uri:
            return _fail_result(f"Invalid class entry for {uri}: key must equal entry URI")
        exp_mod = "CMNS" if uri.startswith(CMNS_BASE_URI_PREFIX) else get_fibo_module(
            uri, modules=ALL_V2_MODULES
        )
        if c_data.get("module") != exp_mod:
            return _fail_result(f"Module mismatch in class {uri}")

    for uri, p_data in properties.items():
        if p_data.get("uri") != uri:
            return _fail_result(f"Invalid property entry for {uri}: key must equal entry URI")
        exp_mod = "CMNS" if uri.startswith(CMNS_BASE_URI_PREFIX) else get_fibo_module(
            uri, modules=ALL_V2_MODULES
        )
        if p_data.get("module") != exp_mod:
            return _fail_result(f"Module mismatch in property {uri}")

    represented = {item.get("module") for item in classes.values() if isinstance(item, dict)}
    for m in req_mods:
        if m not in represented:
            return _fail_result(f"No classes found for core module '{m}' in compiled pack")

    return BudgetVerificationResult(
        disk_size_bytes=disk_size,
        memory_peak_bytes=peak_memory,
        cold_load_ms=cold_load_ms,
        sha256_full=sha256_full,
        sha256_8=sha256_8,
        class_count=len(classes),
        property_count=len(properties),
        is_valid=True,
        individual_count=len(individuals),
    )


def verify_starter_packs_path_a(pack_path: Path) -> None:
    """Verify all 8 starter_packs URIs resolve as Path A >= 0.6500 via FiboPackQueryAdapter."""
    from opendv.sources.starter_packs import STARTER_PACK_IDS, get_starter_pack_ontology

    loader = FiboReferenceOntologyLoader(pack_path=pack_path, verify_integrity=False)
    adapter = FiboPackQueryAdapter(loader=loader, verify_integrity=False)

    failures: list[str] = []
    for pack_id in STARTER_PACK_IDS:
        ont = get_starter_pack_ontology(pack_id)
        for concept in ont.concepts:
            if not concept.external_mappings:
                failures.append(f"{pack_id}.{concept.concept_name}: missing external_mappings")
                continue
            for mapping in concept.external_mappings:
                if not can_satisfy_path_a_grounding(mapping.mapping_type):
                    failures.append(
                        f"{pack_id}.{concept.concept_name}: invalid Path A mapping_type "
                        f"'{mapping.mapping_type}'"
                    )
                    continue
                if mapping.confidence < _STARTER_MIN_CONFIDENCE:
                    failures.append(
                        f"{pack_id}.{concept.concept_name}: mapping.confidence "
                        f"{mapping.confidence} < {_STARTER_MIN_CONFIDENCE}"
                    )
                    continue
                entry = loader.lookup_by_uri(mapping.standard_uri)
                if entry is None:
                    failures.append(
                        f"{pack_id}.{concept.concept_name}: URI not in compiled pack: "
                        f"{mapping.standard_uri}"
                    )
                    continue
                if entry.get("deprecated"):
                    failures.append(
                        f"{pack_id}.{concept.concept_name}: URI is deprecated in compiled pack: "
                        f"{mapping.standard_uri}"
                    )
                    continue
                label = str(entry.get("label", "")).strip()
                score = adapter.score(label, mapping.standard_uri)
                if score is None or score < _STARTER_MIN_CONFIDENCE:
                    failures.append(
                        f"{pack_id}.{concept.concept_name}: FiboPackQueryAdapter.score"
                        f"('{label}', {mapping.standard_uri}) = {score} < {_STARTER_MIN_CONFIDENCE}"
                    )

    if failures:
        raise OpenDVError(
            "Starter-pack Path A verification failed:\n" + "\n".join(failures),
            error_code=ErrorCode.VERIFICATION_GATE_FAILED,
        )
