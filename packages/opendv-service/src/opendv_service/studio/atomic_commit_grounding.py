"""Waiver verification, ETag/SemVer parsing, fsync, and ONT-EVO-002 grounding helpers."""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ontology import (
    ALLOWED_PATH_A_PREDICATES,
    CONCEPT_MUTATING_OP_TYPES,
    PATH_A_GROUNDING_THRESHOLD,
    RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES,
    OntologyDelta,
    StewardWaiverRecord,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.ontology.validator import (
    InvalidGroundingWaiverError,
    UngroundedConceptError,
)
from opendv_service.studio.atomic_commit_errors import (
    _SEMVER_REGEX,
    _STRONG_ETAG_PATTERN,
    InvalidPreconditionHeaderError,
)
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

logger = logging.getLogger(__name__)


class _LocalWaiverVerifier:
    """Adapter binding WorkspaceSecurityManager K_waiver verification to GovernanceValidator."""

    def __init__(
        self,
        security_manager: WorkspaceSecurityManager,
        *,
        accepted_delta_shas: Sequence[str] | set[str] | frozenset[str] | None = None,
    ) -> None:
        self._sec = security_manager
        self._accepted_delta_shas: tuple[str, ...] = tuple(
            s.strip().lower()
            for s in (accepted_delta_shas or ())
            if isinstance(s, str) and s.strip()
        )

    def verify_waiver(
        self,
        waiver: StewardWaiverRecord,
        delta_sha256: str,
        base_sha256: str,
    ) -> bool:
        candidates: list[str] = []
        if delta_sha256:
            candidates.append(delta_sha256.strip().lower())
        for extra_sha in self._accepted_delta_shas:
            if extra_sha not in candidates:
                candidates.append(extra_sha)

        prop_sha = (waiver.proposal_delta_sha256 or "").strip().lower()
        delta_matched = False
        for cand in candidates:
            if hmac.compare_digest(prop_sha, cand):
                delta_matched = True
        if not delta_matched:
            return False

        if base_sha256 and not hmac.compare_digest(
            (waiver.base_sha256 or "").strip().lower(), base_sha256.strip().lower()
        ):
            return False
        if waiver.expires_at is not None and waiver.expires_at <= datetime.now(UTC):
            return False
        payload_bytes = waiver.signing_payload().encode("utf-8")
        return self._sec.verify_waiver_signature(payload_bytes, waiver.signature_or_token)


class _AcceptedDeltaWaiverVerifierProxy:
    """Wraps a custom WaiverVerifier so both normalized and submitted delta digests are accepted."""

    def __init__(self, inner: Any, accepted_delta_shas: Sequence[str]) -> None:
        self._inner = inner
        self._accepted_delta_shas = tuple(dict.fromkeys(s for s in accepted_delta_shas if s))

    def verify_waiver(
        self,
        waiver: StewardWaiverRecord,
        delta_sha256: str,
        base_sha256: str,
    ) -> bool:
        cands = list(dict.fromkeys([delta_sha256, *self._accepted_delta_shas]))
        matched = False
        for cand in cands:
            if cand and self._inner.verify_waiver(
                waiver, delta_sha256=cand, base_sha256=base_sha256
            ):
                matched = True
        return matched


def parse_strong_etag(if_match: str) -> str:
    """Validate and extract the 64-char lowercase hex digest from an `If-Match` value.

    Accepts strong HTTP ETag `'"<64-hex>"'` or raw Python `<64-hex>`, while strictly
    rejecting weak ETags (`W/"..."`), wildcards (`*`), comma-separated lists, and
    malformed strings with `InvalidPreconditionHeaderError` (HTTP 400 / exit 2).
    """
    if not isinstance(if_match, str):
        raise InvalidPreconditionHeaderError("If-Match precondition must be a string.")
    trimmed = if_match.strip()
    match = _STRONG_ETAG_PATTERN.fullmatch(trimmed)
    if not match:
        raise InvalidPreconditionHeaderError(
            f"Invalid If-Match precondition '{if_match}': must be a strong 64-character "
            "lowercase hex SHA-256 ETag (weak W/ ETags, '*', and lists are forbidden)."
        )
    return match.group(1) or match.group(2)


def parse_semver_tuple(version_str: str) -> tuple[int, int, int]:
    """Parse a semantic version string into a (major, minor, patch) integer tuple."""
    match = _SEMVER_REGEX.match(version_str.strip())
    if not match:
        raise ValueError(f"Invalid semantic version string: '{version_str}'")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def fsync_directory_best_effort(dir_path: Path) -> None:
    """Flush directory metadata buffers to physical storage."""
    if not dir_path.exists():
        return

    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateFileW(
                str(dir_path),
                0xC0000000,
                7,
                None,
                3,
                0x02000000,
                None,
            )
            if handle not in (-1, 0):
                kernel32.FlushFileBuffers(handle)
                kernel32.CloseHandle(handle)
        except OSError as exc:
            logger.warning("Windows directory FlushFileBuffers on %s failed: %s", dir_path, exc)
    else:
        try:
            fd = os.open(str(dir_path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("POSIX directory fsync on %s failed: %s", dir_path, exc)


def collect_touched_concept_uris(
    base_ontology: GraphAOntology,
    delta: OntologyDelta,
) -> set[str]:
    """Collect all post-delta concept URIs touched by the 18+2 concept-mutating operations."""
    touched: set[str] = set()
    rel_list = list(
        getattr(base_ontology, "canonical_relationships", None)
        or getattr(base_ontology, "relationships", ())
    )
    base_rels_by_uri = {
        r.relationship_uri: r for r in rel_list if getattr(r, "relationship_uri", None)
    }
    base_rels_by_name: dict[str, list[Any]] = {}
    for r in rel_list:
        r_name = getattr(r, "relationship_name", None)
        if r_name:
            base_rels_by_name.setdefault(str(r_name), []).append(r)
        r_uri = getattr(r, "relationship_uri", "") or ""
        if ":" in r_uri:
            slug = r_uri.rsplit(":", 1)[-1]
            if slug:
                base_rels_by_name.setdefault(slug, []).append(r)

    for op in delta.operations:
        op_type = getattr(op, "op_type", "")
        if op_type == "update_concept_urn":
            new_u = getattr(op, "new_concept_uri", None)
            old_u = getattr(op, "old_concept_uri", None)
            if new_u:
                touched.add(str(new_u))
            if old_u:
                touched.add(str(old_u))
        elif op_type in CONCEPT_MUTATING_OP_TYPES:
            c_uri = getattr(op, "concept_uri", None) or getattr(op, "target_uri", None)
            if c_uri:
                touched.add(str(c_uri))
        elif op_type in RELATIONSHIP_ENDPOINT_MUTATING_OP_TYPES:
            rel_uri = getattr(op, "relationship_uri", None)
            rel_name = getattr(op, "relationship_name", None)
            matched_rels: list[Any] = []
            if rel_uri and rel_uri in base_rels_by_uri:
                matched_rels.append(base_rels_by_uri[rel_uri])
            elif rel_name and str(rel_name) in base_rels_by_name:
                matched_rels.extend(base_rels_by_name[str(rel_name)])
            for rel in matched_rels:
                for ep in getattr(rel, "endpoints", ()) or ():
                    if ep:
                        touched.add(str(ep))
                if getattr(rel, "source_concept_uri", None):
                    touched.add(str(rel.source_concept_uri))
                if getattr(rel, "target_concept_uri", None):
                    touched.add(str(rel.target_concept_uri))

    return touched


def verify_ont_evo_002_post_state_grounding(
    base_ontology: GraphAOntology,
    delta: OntologyDelta,
    target_ontology: GraphAOntology,
    *,
    waivers: list[StewardWaiverRecord] | None = None,
    security_manager: WorkspaceSecurityManager | None = None,
    grounding_verifier: Any | None = None,
    explicit_touched_uris: set[str] | None = None,
    submitted_delta_sha256: str | None = None,
) -> None:
    """Enforce ONT-EVO-002 across all 18+2 concept-mutating ops at threshold 0.65.

    - Requires at least one mapping with predicate in `ALLOWED_PATH_A_PREDICATES`
      (`skos:exactMatch` or `skos:closeMatch`; `skos:broadMatch` is strictly excluded)
      and `confidence >= PATH_A_GROUNDING_THRESHOLD` (`0.65`), OR an authorized,
      cryptographically verified `StewardWaiverRecord` (Path B).
    - Grandfathered concepts lose their exemption immediately when touched by any of
      the 18+2 concept-mutating operations.
    """
    touched_uris = (
        set(explicit_touched_uris)
        if explicit_touched_uris is not None
        else collect_touched_concept_uris(base_ontology, delta)
    )
    if not touched_uris:
        return

    target_concepts = {c.concept_uri: c for c in target_ontology.canonical_business_concepts}
    waiver_list = waivers or []
    delta_sha = delta.canonical_sha256()
    accepted_shas = [delta_sha, submitted_delta_sha256] if submitted_delta_sha256 else [delta_sha]
    waiver_verifier = (
        _LocalWaiverVerifier(security_manager, accepted_delta_shas=accepted_shas)
        if security_manager
        else None
    )

    for concept_uri in sorted(touched_uris):
        if concept_uri not in target_concepts:
            continue
        concept = target_concepts[concept_uri]
        mappings = list(concept.external_mappings or [])

        path_a_ok = False
        if grounding_verifier is not None:
            filtered = [
                m
                for m in mappings
                if (getattr(m, "predicate", None) or "") in ALLOWED_PATH_A_PREDICATES
            ]
            if filtered:
                res = grounding_verifier.verify_grounding(
                    concept_uri,
                    candidates=filtered,
                    concept_name=concept.concept_name,
                    synonyms=tuple(concept.synonyms or ()),
                )
                if res.is_grounded and res.score >= PATH_A_GROUNDING_THRESHOLD:
                    path_a_ok = True
        else:
            for m in mappings:
                pred = getattr(m, "predicate", None) or ""
                conf = getattr(m, "confidence", None)
                if (
                    pred in ALLOWED_PATH_A_PREDICATES
                    and conf is not None
                    and float(conf) >= PATH_A_GROUNDING_THRESHOLD
                ):
                    path_a_ok = True
                    break

        if path_a_ok:
            continue

        # Path B: Administrative steward waiver (admin-only per master-plan.md)
        path_b_ok = False
        for w in waiver_list:
            if concept_uri in w.target_concept_uris:
                if not w.rationale or len(w.rationale.strip()) < 10:
                    raise InvalidGroundingWaiverError(
                        "INVALID_GROUNDING_WAIVER: Waiver rationale must be at least 10 characters."
                    )
                if w.authorized_role != "admin":
                    raise InvalidGroundingWaiverError(
                        "INVALID_GROUNDING_WAIVER: Waiver authorized_role must be 'admin'."
                    )
                if waiver_verifier is not None and not waiver_verifier.verify_waiver(
                    w, delta_sha256=delta_sha, base_sha256=delta.base_sha256
                ):
                    raise InvalidGroundingWaiverError(
                        "INVALID_GROUNDING_WAIVER: Cryptographic waiver signature check failed."
                    )
                path_b_ok = True
                break

        if not path_b_ok:
            raise UngroundedConceptError(
                f"ONT_EVO_UNGROUNDED_CONCEPT: Touched concept '{concept_uri}' lacks verified "
                f"Path A reference grounding (skos:exactMatch / skos:closeMatch >= "
                f"{PATH_A_GROUNDING_THRESHOLD:.4f}) and lacks an authorized administrative waiver."
            )


def _compute_required_target_semver(
    base_version: str,
    delta: OntologyDelta,
    base_ontology: GraphAOntology,
) -> str:
    """Compute the exact required next SemVer without caller target_version over-bump overrides."""
    probe = delta.model_copy(update={"target_ontology_version": None})
    probe.__pydantic_fields_set__.discard("target_ontology_version")
    probe.__pydantic_fields_set__.discard("target_version")
    return compute_next_semver(base_version, probe, base_ontology)


def _load_grandfathered_concept_uris(session_dir: Path) -> set[str]:
    """Load grandfathered seed concept URIs from initial version meta.json / metadata.json."""
    versions_dir = session_dir / "versions"
    if not versions_dir.is_dir():
        return set()
    out: set[str] = set()
    for vdir in sorted(versions_dir.iterdir()):
        if not vdir.is_dir():
            continue
        for mname in ("meta.json", "metadata.json"):
            mpath = vdir / mname
            if mpath.is_file():
                try:
                    mdata = json.loads(mpath.read_text(encoding="utf-8"))
                    if isinstance(mdata, dict) and mdata.get("mode") == "seed":
                        uris = mdata.get("grandfathered_concept_uris", [])
                        if isinstance(uris, list):
                            out.update(str(u) for u in uris if u)
                except (OSError, json.JSONDecodeError):
                    continue
    return out
