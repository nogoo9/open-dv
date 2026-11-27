"""Direct JSON FIBO Pack v2 (2.0.0) loader, derived indexes, and FiboPackQueryAdapter."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from decimal import ROUND_HALF_EVEN, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

from opendv.grounding.kernel.lexical import LexicalIndex
from opendv.grounding.providers.fibo.acronyms import FINANCIAL_ACRONYMS
from opendv.grounding.providers.fibo.closure import (
    PackDerivedIndexes,
    build_pack_v2_derived_indexes,
)
from opendv.grounding.providers.fibo.closure_rules import (
    PACK_V2_SCHEMA_VERSION,
    find_baseline_digests_path,
    validate_pack_v2_data,
    verify_expected_sha,
    verify_pack_against_baselines,
)
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.grounding_spi import KnowledgeItem
from opendv.models.wire_dtos_discovery import FiboMatchSpec, FiboQueryReq, FiboQueryResp

MAX_DISK_SIZE_BYTES: Final[int] = 4 * 1024 * 1024
MAX_MEMORY_BYTES: Final[int] = 18 * 1024 * 1024
MAX_LOAD_LATENCY_MS: Final[float] = 70.0

FIBO_V2_MODULES: Final[tuple[str, ...]] = (
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

_PACK_FILENAME_DIGEST_RE = re.compile(r"^fibo-.+-([0-9a-f]{8})\.json$")
_FOUR_DECIMALS: Final[Decimal] = Decimal("0.0001")


def _copy_entity(
    data: dict[str, Any],
    *,
    entity_type: str | None = None,
    replaces_uris: list[str] | None = None,
) -> dict[str, Any]:
    res = copy.deepcopy(data)
    if entity_type is not None:
        res["entity_type"] = entity_type
    if replaces_uris is not None:
        res["replaces_uris"] = list(replaces_uris)
    return res


def _bundled_pack_path() -> Path:
    return Path(__file__).resolve().parent / "fibo-core-pack.json"


def _find_default_pack_path() -> Path:
    env_override = os.environ.get("OPENDV_FIBO_PACK_PATH")
    if env_override:
        p = Path(env_override).resolve()
        if not p.is_file():
            raise OpenDVError(
                f"Configured OPENDV_FIBO_PACK_PATH not found: {env_override}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return p

    canonical_pack = _bundled_pack_path()
    if canonical_pack.is_file():
        return canonical_pack

    raise OpenDVError(
        f"Default FIBO Pack v2 'fibo-core-pack.json' not found in {canonical_pack.parent}",
        error_code=ErrorCode.RESOURCE_NOT_FOUND,
    )


@lru_cache(maxsize=4)
def _cached_sha256_file(path_str: str, mtime: float, size: int) -> str:
    _ = (mtime, size)
    return hashlib.sha256(Path(path_str).read_bytes()).hexdigest()


def get_bundled_fibo_pack_sha256() -> str:
    """Return SHA-256 of the bundled Pack v2 JSON, with baseline-digests fallback in M1.1."""
    bundled = _bundled_pack_path()
    if bundled.is_file():
        stat = bundled.stat()
        return _cached_sha256_file(str(bundled), stat.st_mtime, stat.st_size)

    baseline_path = find_baseline_digests_path()
    if baseline_path is not None:
        try:
            data = json.loads(baseline_path.read_text(encoding="utf-8"))
            fibo_spec = data.get("fibo_core_pack") if isinstance(data, dict) else None
            sha = fibo_spec.get("sha256") if isinstance(fibo_spec, dict) else None
            if isinstance(sha, str) and len(sha.strip()) == 64:
                return sha.strip().lower()
        except (OSError, json.JSONDecodeError):
            pass

    raise OpenDVError(
        f"Bundled FIBO Pack v2 not found at {bundled}",
        error_code=ErrorCode.RESOURCE_NOT_FOUND,
    )


class FiboReferenceOntologyLoader:
    """Direct JSON FIBO Pack v2 (2.0.0) loader with zero runtime rdflib."""

    def __init__(
        self,
        pack_path: str | Path | None = None,
        *,
        pack_bytes: bytes | None = None,
        expected_sha256: str | None = None,
        verify_integrity: bool = True,
    ) -> None:
        if pack_path is not None and pack_bytes is not None:
            raise OpenDVError(
                "Specify at most one of pack_path or pack_bytes",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        resolved_path: Path | None = None
        if pack_bytes is not None:
            raw_bytes = pack_bytes
        else:
            resolved_path = (
                Path(pack_path).resolve() if pack_path is not None else _find_default_pack_path()
            )
            if not resolved_path.is_file():
                raise OpenDVError(
                    f"Reference ontology pack file not found: {resolved_path}",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
            try:
                with resolved_path.open("rb") as fh:
                    raw_bytes = fh.read(MAX_DISK_SIZE_BYTES + 1)
            except FileNotFoundError as err:
                raise OpenDVError(
                    f"Reference ontology pack file not found: {resolved_path}",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                ) from err
            except OSError as err:
                raise ReferenceOntologyCorruptError(
                    f"Failed to read pack from {resolved_path}: {err}"
                ) from err

        if len(raw_bytes) > MAX_DISK_SIZE_BYTES:
            raise ReferenceOntologyCorruptError(
                f"Pack size {len(raw_bytes)} exceeds budget {MAX_DISK_SIZE_BYTES} bytes"
            )

        computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        computed_prefix = computed_sha256[:8]

        if verify_integrity and resolved_path is not None:
            m = _PACK_FILENAME_DIGEST_RE.match(resolved_path.name)
            if m and resolved_path.name != "fibo-core-pack.json" and m.group(1) != computed_prefix:
                raise ReferenceOntologyCorruptError(
                    f"Filename digest '{m.group(1)}' does not match '{computed_prefix}'"
                )

        if expected_sha256 is not None:
            verify_expected_sha(expected_sha256, computed_sha256)

        try:
            data = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise ReferenceOntologyCorruptError(f"Failed to parse pack JSON: {err}") from err
        finally:
            if pack_bytes is None:
                del raw_bytes

        metadata, classes, properties, individuals = validate_pack_v2_data(data)

        if verify_integrity and resolved_path is not None:
            try:
                is_bundled = resolved_path == _bundled_pack_path().resolve()
            except OSError:
                is_bundled = False
            if is_bundled:
                verify_pack_against_baselines(
                    resolved_path,
                    computed_sha256,
                    len(classes),
                    len(properties),
                    len(individuals),
                )

        self._pack_path: Path | None = resolved_path
        self._pack_sha256: str = computed_sha256
        self._integrity_verified: bool = verify_integrity
        self._classes = classes
        self._properties = properties
        self._individuals = individuals
        self._metadata = metadata

        subclass_map: dict[str, list[str]] = {}
        for c_uri, c_val in classes.items():
            for p_uri in c_val.get("sub_class_of", []):
                subclass_map.setdefault(p_uri, []).append(c_uri)
        self._subclass_index = subclass_map
        self._derived = build_pack_v2_derived_indexes(classes, properties, individuals)

    @classmethod
    def from_bytes(
        cls,
        raw_bytes: bytes,
        *,
        expected_sha256: str | None = None,
        verify_integrity: bool = True,
    ) -> FiboReferenceOntologyLoader:
        return cls(
            pack_bytes=raw_bytes,
            expected_sha256=expected_sha256,
            verify_integrity=verify_integrity,
        )

    @classmethod
    def load(
        cls,
        pack_path: str | Path | None = None,
        *,
        expected_sha256: str | None = None,
        verify_integrity: bool = True,
    ) -> FiboReferenceOntologyLoader:
        return cls(
            pack_path=pack_path,
            expected_sha256=expected_sha256,
            verify_integrity=verify_integrity,
        )

    def lookup_by_uri(self, uri: str) -> dict[str, Any] | None:
        replaces = self._derived.replaces_uris.get(uri, [])
        if uri in self._classes:
            return _copy_entity(self._classes[uri], entity_type="class", replaces_uris=replaces)
        if uri in self._properties:
            return _copy_entity(
                self._properties[uri], entity_type="property", replaces_uris=replaces
            )
        if uri in self._individuals:
            return _copy_entity(
                self._individuals[uri], entity_type="individual", replaces_uris=replaces
            )
        return None

    def search_candidates(self, term: str, limit: int = 10) -> list[dict[str, Any]]:
        if limit <= 0 or not term.strip():
            return []
        clean = term.strip().lower()
        tokens = clean.split()
        buckets: tuple[list[tuple[dict[str, Any], str]], ...] = ([], [], [], [])
        seen: set[str] = set()

        def _eval(entries: dict[str, dict[str, Any]], etype: str) -> None:
            for uri, data in entries.items():
                if uri in seen or data.get("deprecated"):
                    continue
                lbl = str(data.get("label", "")).lower()
                loc = uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
                syns = [str(s).lower() for s in data.get("synonyms", [])]
                if clean in (lbl, loc) or clean in syns:
                    b = 0
                elif (
                    lbl.startswith(clean)
                    or loc.startswith(clean)
                    or any(s.startswith(clean) for s in syns)
                ):
                    b = 1
                elif len(tokens) > 1 and all(
                    w in lbl or w in loc or any(w in s for s in syns) for w in tokens
                ):
                    b = 2
                elif clean in lbl or clean in loc or any(clean in s for s in syns):
                    b = 3
                else:
                    continue
                buckets[b].append((data, etype))
                seen.add(uri)

        _eval(self._classes, "class")
        if len(buckets[0]) < limit:
            _eval(self._properties, "property")

        combined: list[tuple[float, dict[str, Any], str]] = []
        for score, bucket in zip((1.00, 0.85, 0.70, 0.50), buckets, strict=True):
            bucket.sort(
                key=lambda x: (
                    0 if x[1] == "class" else 1,
                    len(str(x[0].get("label", ""))),
                    str(x[0].get("label", "")),
                )
            )
            combined.extend((score, d, et) for d, et in bucket)

        results: list[dict[str, Any]] = []
        for score, data, etype in combined[:limit]:
            uri = str(data.get("uri", ""))
            entry = _copy_entity(
                data,
                entity_type=etype,
                replaces_uris=self._derived.replaces_uris.get(uri, []),
            )
            entry["match_score"] = score
            results.append(entry)
        return results

    def get_classes(self) -> dict[str, dict[str, Any]]:
        return {k: _copy_entity(v, entity_type="class") for k, v in self._classes.items()}

    def get_properties(self) -> dict[str, dict[str, Any]]:
        return {k: _copy_entity(v, entity_type="property") for k, v in self._properties.items()}

    def get_individuals(self) -> dict[str, dict[str, Any]]:
        return {k: _copy_entity(v, entity_type="individual") for k, v in self._individuals.items()}

    def get_pack_metadata(self) -> dict[str, Any]:
        return _copy_entity(self._metadata)

    def get_direct_superclasses(self, uri: str) -> list[str]:
        entry = self._classes.get(uri)
        return list(entry.get("sub_class_of", [])) if entry else []

    def get_direct_subclasses(self, uri: str) -> list[str]:
        return list(self._subclass_index.get(uri, []))

    def get_direct_superproperties(self, uri: str) -> list[str]:
        entry = self._properties.get(uri)
        return list(entry.get("sub_property_of", [])) if entry else []

    def get_restriction_domains(self, prop_uri: str) -> set[str]:
        return set(self._derived.restriction_domains.get(prop_uri, ()))

    def get_restriction_ranges(self, prop_uri: str) -> set[str]:
        return set(self._derived.restriction_ranges.get(prop_uri, ()))

    def get_inverse_properties(self, prop_uri: str) -> set[str]:
        return set(self._derived.inverse_of_map.get(prop_uri, ()))

    def are_disjoint(self, uri_a: str, uri_b: str) -> bool:
        return self._derived.are_disjoint(uri_a, uri_b)

    def get_disjoint_classes(self, uri: str) -> set[str]:
        return set(self._derived.ancestor_disjoint_roots.get(uri, ()))

    def get_replaces_uris(self, uri: str) -> list[str]:
        return list(self._derived.replaces_uris.get(uri, ()))

    def get_replaced_by(self, uri: str) -> str | None:
        return self._derived.replaced_by_map.get(uri)

    @property
    def derived_indexes(self) -> PackDerivedIndexes:
        return self._derived

    @property
    def integrity_verified(self) -> bool:
        return self._integrity_verified

    @property
    def pack_sha256(self) -> str:
        return self._pack_sha256

    @property
    def pack_sha256_prefix(self) -> str:
        return self._pack_sha256[:8]

    @property
    def pack_id(self) -> str:
        return str(self._metadata["pack_id"])

    @property
    def version(self) -> str:
        return str(self._metadata["version"])

    @property
    def pack_schema_version(self) -> str:
        return str(self._metadata["pack_schema_version"])

    @property
    def modules(self) -> list[str]:
        return [str(m) for m in self._metadata.get("modules", [])]

    @property
    def class_count(self) -> int:
        return len(self._classes)

    @property
    def property_count(self) -> int:
        return len(self._properties)

    @property
    def individual_count(self) -> int:
        return len(self._individuals)

    @property
    def pack_path(self) -> Path | None:
        return self._pack_path


class FiboPackQueryAdapter:
    """Ask-FIBO Q&A and concept-lookup adapter atop FiboReferenceOntologyLoader + LexicalIndex."""

    def __init__(
        self,
        loader: FiboReferenceOntologyLoader | None = None,
        *,
        pack_path: Path | str | None = None,
        pack_bytes: bytes | None = None,
        expected_sha256: str | None = None,
        verify_integrity: bool = True,
        config_path: Path | str | None = None,
    ) -> None:
        if loader is not None:
            if verify_integrity and not loader.integrity_verified:
                raise ReferenceOntologyCorruptError(
                    "Unverified FiboReferenceOntologyLoader rejected when verify_integrity=True"
                )
            if expected_sha256 is not None:
                verify_expected_sha(expected_sha256, loader.pack_sha256)
            active_loader = loader
        elif pack_bytes is not None:
            active_loader = FiboReferenceOntologyLoader.from_bytes(
                pack_bytes,
                expected_sha256=expected_sha256,
                verify_integrity=verify_integrity,
            )
        else:
            active_loader = get_default_fibo_loader(
                pack_path=pack_path,
                expected_sha256=expected_sha256,
                verify_integrity=verify_integrity,
            )

        self._loader = active_loader
        self._lexical = LexicalIndex(config_path=config_path, acronyms=FINANCIAL_ACRONYMS)
        self._grounding_cutoff = Decimal(self._lexical.config.grounding_threshold)

        items: list[KnowledgeItem] = []
        deprecated_uris: set[str] = set()
        parent_map: dict[str, str | None] = {}
        classes = active_loader.get_classes()
        for uri in sorted(classes):
            entry = classes[uri]
            if entry.get("deprecated"):
                deprecated_uris.add(uri)
            sub_of = entry.get("sub_class_of", [])
            parent_map[uri] = str(sub_of[0]) if sub_of else None
            items.append(
                KnowledgeItem(
                    entry_id=uri,
                    label=str(entry.get("label", "")),
                    entry_kind="class",
                    definition=str(entry.get("definition", "")),
                    aliases=[str(s) for s in entry.get("synonyms", [])],
                    parent_ids=[str(s) for s in sub_of],
                    replaces_uris=active_loader.get_replaces_uris(uri),
                )
            )
        self._deprecated_uris = frozenset(deprecated_uris)
        self._parent_map = parent_map
        self._indexed_uris = frozenset(parent_map.keys())
        self._lexical.index_items(
            items,
            origin_source_id=active_loader.pack_id,
            origin_kind="reference_standard",
        )

    @property
    def loader(self) -> FiboReferenceOntologyLoader:
        return self._loader

    def lookup_concept(self, uri: str) -> dict[str, Any] | None:
        return self._loader.lookup_by_uri(uri)

    def score(self, query: str, concept_uri: str) -> Decimal | None:
        if concept_uri not in self._indexed_uris:
            return None
        return self._lexical.score(query, concept_uri)

    def query(self, req: FiboQueryReq) -> FiboQueryResp:
        detailed = self._lexical.match_detailed(
            req.query,
            top_k=req.top_k,
            entry_kinds={"class"},
            exclude_entry_ids=self._deprecated_uris,
        )
        specs: list[FiboMatchSpec] = []
        for hit, raw_bm25, exact_label_or_syn in detailed:
            entry = self._loader.lookup_by_uri(hit.entry_id) or {}
            suggested: Literal["PATH_A1", "PATH_A2"] = (
                "PATH_A1"
                if hit.raw_score >= self._grounding_cutoff and exact_label_or_syn
                else "PATH_A2"
            )
            raw_fmt = str(raw_bm25.quantize(_FOUR_DECIMALS, rounding=ROUND_HALF_EVEN))
            specs.append(
                FiboMatchSpec(
                    concept_uri=hit.entry_id,
                    label=hit.label,
                    parent_uri=self._parent_map.get(hit.entry_id),
                    normalized_score=f"{hit.raw_score:.4f}",
                    raw_bm25=raw_fmt,
                    suggested_path=suggested,
                    definition=str(entry.get("definition", "")),
                )
            )
        return FiboQueryResp(matches=specs)


@lru_cache(maxsize=8)
def _get_cached_fibo_loader(
    path_str: str,
    norm_expected_sha: str | None,
    verify: bool,
    mtime: float,
    size: int,
    baseline_path_str: str | None = None,
    baseline_mtime: float = 0.0,
) -> FiboReferenceOntologyLoader:
    _ = (mtime, size, baseline_path_str, baseline_mtime)
    return FiboReferenceOntologyLoader(
        pack_path=Path(path_str),
        expected_sha256=norm_expected_sha,
        verify_integrity=verify,
    )


def clear_fibo_loader_cache() -> None:
    _get_cached_fibo_loader.cache_clear()


def get_default_fibo_loader(
    pack_path: str | Path | None = None,
    *,
    expected_sha256: str | None = None,
    verify_integrity: bool = True,
) -> FiboReferenceOntologyLoader:
    resolved_path = (
        Path(pack_path).resolve() if pack_path is not None else _find_default_pack_path()
    )
    if not resolved_path.is_file():
        raise OpenDVError(
            f"Reference ontology pack file not found: {resolved_path}",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    stat = resolved_path.stat()
    baseline_path = find_baseline_digests_path()
    baseline_mtime = baseline_path.stat().st_mtime if baseline_path else 0.0
    norm_sha = expected_sha256.strip().lower() if expected_sha256 else None
    return _get_cached_fibo_loader(
        str(resolved_path),
        norm_sha,
        verify_integrity,
        stat.st_mtime,
        stat.st_size,
        str(baseline_path) if baseline_path else None,
        baseline_mtime,
    )


__all__ = [
    "FIBO_V2_MODULES",
    "MAX_DISK_SIZE_BYTES",
    "MAX_LOAD_LATENCY_MS",
    "MAX_MEMORY_BYTES",
    "PACK_V2_SCHEMA_VERSION",
    "FiboPackQueryAdapter",
    "FiboReferenceOntologyLoader",
    "ReferenceOntologyCorruptError",
    "clear_fibo_loader_cache",
    "get_bundled_fibo_pack_sha256",
    "get_default_fibo_loader",
]
