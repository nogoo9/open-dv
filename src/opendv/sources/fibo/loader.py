"""Direct JSON Reference Ontology Loader for EDM Council FIBO packs (V2).

Pure stdlib JSON loading with zero runtime rdflib dependency, <70 ms cold load,
<18 MB peak memory, O(1) URI lookup, and cryptographic SHA-256 integrity
verification against schemas/v2/baseline-digests.json.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from opendv.models.errors import (
    ErrorCode,
    OpenDVError,
    ReferenceOntologyCorruptError,
)

MAX_DISK_SIZE_BYTES: int = 4 * 1024 * 1024
MAX_MEMORY_BYTES: int = 18 * 1024 * 1024
MAX_LOAD_LATENCY_MS: float = 70.0

CANONICAL_FIBO_CORE_SHA256: str = (
    "bebd6ae87dfa4eaee523fdeac730f0fe652761004f24e5d15ab36d5225a0acb5"
)
CANONICAL_FIBO_CORE_PREFIX: str = CANONICAL_FIBO_CORE_SHA256[:8]
CANONICAL_FIBO_CORE_UPSTREAM_COMMIT: str = "69dbbe7a6a4e32049ba3f3988b48877bc954f061"
CANONICAL_FIBO_CORE_UPSTREAM_TAG: str = "master_2024Q4"


def _copy_entity(data: dict[str, Any], entity_type: str | None = None) -> dict[str, Any]:
    res = copy.deepcopy(data)
    if entity_type is not None:
        res["entity_type"] = entity_type
    return res


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

    pkg_dir = Path(__file__).resolve().parent
    canonical_pack = pkg_dir / "fibo-core-pack.json"
    if canonical_pack.is_file():
        return canonical_pack

    prefix_pack = pkg_dir / f"fibo-core-{CANONICAL_FIBO_CORE_PREFIX}.json"
    if prefix_pack.is_file():
        return prefix_pack

    raise OpenDVError(
        f"Default FIBO reference pack 'fibo-core-pack.json' not found in {pkg_dir}",
        error_code=ErrorCode.RESOURCE_NOT_FOUND,
    )


def _find_baseline_digests_path() -> Path | None:
    env_override = os.environ.get("OPENDV_BASELINE_DIGESTS_PATH")
    if env_override:
        p = Path(env_override).resolve()
        if not p.is_file():
            raise OpenDVError(
                f"Configured OPENDV_BASELINE_DIGESTS_PATH not found: {env_override}",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return p

    current: Path | None = Path(__file__).resolve().parent
    for _ in range(8):
        if current is None:
            break
        candidate = current / "schemas" / "v2" / "baseline-digests.json"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _validate_entries(
    entries: dict[str, Any],
    kind: str,
    *,
    require_subclass_list: bool,
) -> None:
    for uri, val in entries.items():
        if not isinstance(val, dict) or val.get("uri") != uri:
            raise ReferenceOntologyCorruptError(
                f"Malformed {kind} entry for URI '{uri}': must be dict with matching uri"
            )
        if "label" in val and not isinstance(val["label"], str):
            raise ReferenceOntologyCorruptError(f"{kind} entry '{uri}' label must be a string")
        synonyms = val.get("synonyms", [])
        if not isinstance(synonyms, list) or not all(isinstance(x, str) for x in synonyms):
            raise ReferenceOntologyCorruptError(
                f"{kind} entry '{uri}' synonyms must be a list of strings"
            )
        if require_subclass_list:
            sub_classes = val.get("sub_class_of", [])
            if not isinstance(sub_classes, list) or not all(
                isinstance(x, str) for x in sub_classes
            ):
                raise ReferenceOntologyCorruptError(
                    f"{kind} entry '{uri}' sub_class_of must be a list of strings"
                )


def _verify_against_baselines(
    resolved_path: Path,
    computed_sha256: str,
    class_count: int,
) -> None:
    if computed_sha256 != CANONICAL_FIBO_CORE_SHA256:
        raise ReferenceOntologyCorruptError(
            f"Pack SHA-256 '{computed_sha256}' does not match canonical digest "
            f"'{CANONICAL_FIBO_CORE_SHA256}' ({resolved_path})"
        )

    baseline_path = _find_baseline_digests_path()
    if baseline_path is None:
        return

    try:
        baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err:
        raise ReferenceOntologyCorruptError(
            f"Baseline digests file at {baseline_path} is corrupted: {err}"
        ) from err

    if not isinstance(baseline_data, dict):
        raise ReferenceOntologyCorruptError(
            f"Baseline digests root must be a JSON object in {baseline_path}"
        )

    fibo_spec = baseline_data.get("fibo_core_pack")
    if not isinstance(fibo_spec, dict):
        raise ReferenceOntologyCorruptError(
            f"Baseline digests missing 'fibo_core_pack' object in {baseline_path}"
        )

    expected_sha = fibo_spec.get("sha256")
    if not isinstance(expected_sha, str) or expected_sha.lower() != computed_sha256:
        raise ReferenceOntologyCorruptError(
            f"Pack SHA-256 '{computed_sha256}' mismatches baseline-digests.json '{expected_sha}'"
        )

    expected_count = fibo_spec.get("concept_count")
    if not isinstance(expected_count, int) or expected_count != class_count:
        raise ReferenceOntologyCorruptError(
            f"Pack concept_count {class_count} mismatches baseline-digests.json '{expected_count}'"
        )


class FiboReferenceOntologyLoader:
    """Direct JSON Reference Ontology Loader with zero runtime rdflib."""

    def __init__(
        self,
        pack_path: str | Path | None = None,
        *,
        expected_sha256: str | None = None,
        verify_integrity: bool = True,
    ) -> None:
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
                f"Failed to read reference ontology pack from {resolved_path}: {err}"
            ) from err

        if len(raw_bytes) > MAX_DISK_SIZE_BYTES:
            raise ReferenceOntologyCorruptError(
                f"Pack size {len(raw_bytes)} exceeds budget {MAX_DISK_SIZE_BYTES} bytes"
            )

        computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        computed_prefix = computed_sha256[:8]

        if verify_integrity:
            filename = resolved_path.name
            if (
                filename.startswith("fibo-core-")
                and filename.endswith(".json")
                and filename != "fibo-core-pack.json"
            ):
                prefix = filename.removeprefix("fibo-core-").removesuffix(".json")
                if len(prefix) == 8 and prefix != computed_prefix:
                    raise ReferenceOntologyCorruptError(
                        f"Filename prefix '{prefix}' does not match digest '{computed_prefix}'"
                    )

        if expected_sha256 is not None:
            norm = expected_sha256.strip().lower()
            if len(norm) == 64 and norm != computed_sha256:
                raise ReferenceOntologyCorruptError(
                    f"Pack digest '{computed_sha256}' does not match expected '{norm}'"
                )
            if len(norm) == 8 and norm != computed_prefix:
                raise ReferenceOntologyCorruptError(
                    f"Pack prefix '{computed_prefix}' does not match expected '{norm}'"
                )
            if len(norm) not in (8, 64):
                raise ReferenceOntologyCorruptError(
                    f"Invalid expected_sha256 length ({len(norm)}): must be 8 or 64"
                )

        try:
            data = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise ReferenceOntologyCorruptError(f"Failed to parse pack JSON: {err}") from err
        finally:
            del raw_bytes

        if not isinstance(data, dict):
            raise ReferenceOntologyCorruptError(
                f"Pack root must be a JSON object, got {type(data).__name__}"
            )

        classes = data.get("classes")
        properties = data.get("properties")
        metadata = data.get("metadata")
        if (
            not isinstance(classes, dict)
            or not isinstance(properties, dict)
            or not isinstance(metadata, dict)
            or not classes
            or not properties
        ):
            raise ReferenceOntologyCorruptError(
                "Pack must contain non-empty 'classes', 'properties', and 'metadata' objects"
            )
        if not isinstance(metadata.get("version"), str) or not isinstance(
            metadata.get("pack_id"), str
        ):
            raise ReferenceOntologyCorruptError(
                "Pack metadata must include string 'version' and 'pack_id'"
            )

        _validate_entries(classes, "Class", require_subclass_list=True)
        _validate_entries(properties, "Property", require_subclass_list=False)

        if verify_integrity:
            _verify_against_baselines(resolved_path, computed_sha256, len(classes))

        self._pack_path: Path = resolved_path
        self._pack_sha256: str = computed_sha256
        self._classes: dict[str, dict[str, Any]] = classes
        self._properties: dict[str, dict[str, Any]] = properties
        self._metadata: dict[str, Any] = metadata

        subclass_map: dict[str, list[str]] = {}
        for c_uri, c_val in classes.items():
            for p_uri in c_val.get("sub_class_of", []):
                subclass_map.setdefault(p_uri, []).append(c_uri)
        self._subclass_index: dict[str, list[str]] = subclass_map

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
        match = self._classes.get(uri)
        entity_type = "class"
        if match is None:
            match = self._properties.get(uri)
            entity_type = "property"
        if match is None:
            return None
        return _copy_entity(match, entity_type=entity_type)

    def search_candidates(self, term: str, limit: int = 10) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        clean_term = term.strip().lower()
        if not clean_term:
            return []

        tokens = clean_term.split()
        buckets: tuple[
            list[tuple[dict[str, Any], str]],
            list[tuple[dict[str, Any], str]],
            list[tuple[dict[str, Any], str]],
            list[tuple[dict[str, Any], str]],
        ] = ([], [], [], [])
        seen_uris: set[str] = set()

        def _evaluate(entries: dict[str, dict[str, Any]], entity_type: str) -> None:
            for uri, data in entries.items():
                if uri in seen_uris:
                    continue
                label = str(data.get("label", "")).lower()
                local = uri.rsplit("/", 1)[-1].rsplit("#", 1)[-1].lower()
                syns = [str(s).lower() for s in data.get("synonyms", [])]

                if clean_term in (label, local) or clean_term in syns:
                    buckets[0].append((data, entity_type))
                elif (
                    label.startswith(clean_term)
                    or local.startswith(clean_term)
                    or any(s.startswith(clean_term) for s in syns)
                ):
                    buckets[1].append((data, entity_type))
                elif len(tokens) > 1 and all(
                    w in label or w in local or any(w in s for s in syns) for w in tokens
                ):
                    buckets[2].append((data, entity_type))
                elif (
                    clean_term in label
                    or clean_term in local
                    or any(clean_term in s for s in syns)
                ):
                    buckets[3].append((data, entity_type))
                else:
                    continue
                seen_uris.add(uri)

        _evaluate(self._classes, "class")
        if len(buckets[0]) < limit:
            _evaluate(self._properties, "property")

        def _sort_key(item: tuple[dict[str, Any], str]) -> tuple[int, int, str]:
            label = str(item[0].get("label", ""))
            return (0 if item[1] == "class" else 1, len(label), label)

        scores = (1.00, 0.85, 0.70, 0.50)
        combined: list[tuple[float, tuple[dict[str, Any], str]]] = []
        for score, bucket in zip(scores, buckets, strict=True):
            bucket.sort(key=_sort_key)
            combined.extend((score, item) for item in bucket)

        results: list[dict[str, Any]] = []
        for score, (data, ent_type) in combined[:limit]:
            entry = _copy_entity(data, entity_type=ent_type)
            entry["match_score"] = score
            results.append(entry)
        return results

    def get_classes(self) -> dict[str, dict[str, Any]]:
        return {k: _copy_entity(v, entity_type="class") for k, v in self._classes.items()}

    def get_properties(self) -> dict[str, dict[str, Any]]:
        return {k: _copy_entity(v, entity_type="property") for k, v in self._properties.items()}

    def get_pack_metadata(self) -> dict[str, Any]:
        return _copy_entity(self._metadata)

    def get_direct_superclasses(self, uri: str) -> list[str]:
        entry = self._classes.get(uri)
        return list(entry.get("sub_class_of", [])) if entry else []

    def get_direct_subclasses(self, uri: str) -> list[str]:
        return list(self._subclass_index.get(uri, []))

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
    def modules(self) -> list[str]:
        return [str(m) for m in self._metadata.get("modules", [])]

    @property
    def class_count(self) -> int:
        return len(self._classes)

    @property
    def property_count(self) -> int:
        return len(self._properties)

    @property
    def pack_path(self) -> Path:
        return self._pack_path


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
    baseline_path = _find_baseline_digests_path()
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
    "CANONICAL_FIBO_CORE_PREFIX",
    "CANONICAL_FIBO_CORE_SHA256",
    "CANONICAL_FIBO_CORE_UPSTREAM_COMMIT",
    "CANONICAL_FIBO_CORE_UPSTREAM_TAG",
    "MAX_DISK_SIZE_BYTES",
    "MAX_LOAD_LATENCY_MS",
    "MAX_MEMORY_BYTES",
    "FiboReferenceOntologyLoader",
    "ReferenceOntologyCorruptError",
    "clear_fibo_loader_cache",
    "get_default_fibo_loader",
]
