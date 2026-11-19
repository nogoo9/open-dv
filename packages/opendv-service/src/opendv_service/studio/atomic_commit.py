"""Multi-file atomic commit protocol, two-phase promotion, recovery, and CAS GC facade.

Decomposed into cohesive <5,000-token submodules under `opendv_service.studio.atomic_commit_*`
while preserving the full public/internal API surface and exact `AtomicCommitService` signatures.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ontology import OntologyDelta, StewardWaiverRecord
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.validator import GovernanceValidator
from opendv_service.studio.atomic_commit_audit import (
    append_raw_audit_entry_under_lock,
    collect_active_pending_commit_intents,
    collect_verified_committed_records,
    load_version_ontology,
    mark_proposals_post_commit,
    read_valid_audit_records,
    verify_audit_chain_or_raise,
    write_session_json,
)
from opendv_service.studio.atomic_commit_cas import (
    _extract_sha256_recursively,
    _extract_strings_matching,
    _iter_manifest_entries,
    delete_source,
    mark_and_sweep_cas,
)
from opendv_service.studio.atomic_commit_errors import (
    _IMPORT_ANCHOR_ACTIONS,
    _SEMVER_REGEX,
    _STRONG_ETAG_PATTERN,
    DEFAULT_STAGING_GRACE_SECONDS,
    CommitValidationError,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
    HistoricalContractRationaleRequiredError,
    HistoricalContractWarningUnacknowledgedError,
    InvalidPreconditionHeaderError,
    LineageRootMismatchError,
    NoOpPromotionError,
    PromotionRationaleRequiredError,
    PromotionResult,
    SourceNotFoundError,
    SourceReferencedByCommittedVersionError,
    SourceReferencedByPendingProposalError,
    VersionCommitResult,
)
from opendv_service.studio.atomic_commit_grounding import (
    _AcceptedDeltaWaiverVerifierProxy,
    _compute_required_target_semver,
    _load_grandfathered_concept_uris,
    _LocalWaiverVerifier,
    collect_touched_concept_uris,
    fsync_directory_best_effort,
    parse_semver_tuple,
    parse_strong_etag,
    verify_ont_evo_002_post_state_grounding,
)
from opendv_service.studio.atomic_commit_promo_gov import (
    collect_locally_waived_session_concepts,
    verify_promotion_governance,
)
from opendv_service.studio.atomic_commit_promotion import (
    collect_session_lineage_shas,
    execute_promote_to_workspace,
)
from opendv_service.studio.atomic_commit_protocol import execute_commit_delta
from opendv_service.studio.atomic_commit_recovery import execute_reconcile_and_recover_session
from opendv_service.studio.locks import DEFAULT_LOCK_TIMEOUT_SECONDS
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    TamperEvidentChainCorruptError,
)
from opendv_service.studio.workspace_security import WorkspaceSecurityManager


class AtomicCommitService:
    """Service implementing 6-step atomic commit, two-phase promotion, recovery, and CAS GC."""

    def __init__(
        self,
        session_manager: StudioSessionManager,
        security_manager: WorkspaceSecurityManager | None = None,
        *,
        governance_validator: GovernanceValidator | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.security_manager = security_manager or session_manager.security_manager
        self.governance_validator = governance_validator

    def _load_version_ontology(
        self, session_dir: Path, session: SessionMetadata, version_str: str
    ) -> GraphAOntology:
        return load_version_ontology(session_dir, session, version_str)

    def _mark_proposals_post_commit(
        self,
        session_dir: Path,
        accepted_proposal_id: str | None,
        target_sha256: str,
        now_iso: str,
        evaluation: dict[str, Any] | None = None,
        *,
        accepted_by: str | None = None,
        waivers: Sequence[StewardWaiverRecord] | None = None,
    ) -> None:
        mark_proposals_post_commit(
            session_dir,
            accepted_proposal_id,
            target_sha256,
            now_iso,
            evaluation,
            accepted_by=accepted_by,
            waivers=waivers,
        )

    def commit_delta(
        self,
        session_id: str,
        delta: OntologyDelta,
        proposal_id: str | None = None,
        author: str = "steward",
        if_match: str | None = None,
        evaluation: dict[str, Any] | None = None,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        *,
        actor_role: str = "editor",
        governance_validator: GovernanceValidator | None = None,
        waivers: list[StewardWaiverRecord] | None = None,
        grandfathered_concept_uris: set[str] | frozenset[str] | None = None,
    ) -> VersionCommitResult:
        """Execute the 6-step atomic commit protocol under Total Lock Hierarchy L5 session lock."""
        return execute_commit_delta(
            self,
            session_id,
            delta,
            proposal_id=proposal_id,
            author=author,
            if_match=if_match,
            evaluation=evaluation,
            timeout_sec=timeout_sec,
            actor_role=actor_role,
            governance_validator=governance_validator,
            waivers=waivers,
            grandfathered_concept_uris=grandfathered_concept_uris,
        )

    def _collect_locally_waived_session_concepts(
        self, session_dir: Path, session: SessionMetadata
    ) -> set[str]:
        return collect_locally_waived_session_concepts(self.security_manager, session_dir, session)

    def _verify_promotion_governance(
        self,
        session_dir: Path,
        session: SessionMetadata,
        existing_ws_ont: GraphAOntology | None,
        head_ont: GraphAOntology,
        *,
        target_contract_path: Path | None = None,
        acknowledge_historical_contract_warning: bool = False,
        historical_contract_rationale: str | None = None,
    ) -> None:
        verify_promotion_governance(
            self.session_manager,
            self.security_manager,
            self.governance_validator,
            session_dir,
            session,
            existing_ws_ont,
            head_ont,
            target_contract_path=target_contract_path,
            acknowledge_historical_contract_warning=acknowledge_historical_contract_warning,
            historical_contract_rationale=historical_contract_rationale,
            waived_concepts_loader=self._collect_locally_waived_session_concepts,
        )

    def promote_to_workspace(
        self,
        session_id: str,
        target_workspace: Path | str,
        expected_workspace_sha256: str,
        *,
        expected_head_sha256: str | None = None,
        if_match: str | None = None,
        rationale: str = "",
        actor: str = "admin",
        actor_role: str = "editor",
        acknowledge_historical_contract_warning: bool = False,
        historical_contract_rationale: str | None = None,
        allowed_workspace_roots: Sequence[Path | str] | None = None,
        is_remote_request: bool = False,
        enforce_lineage: bool = True,
        allow_noop: bool = False,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> PromotionResult:
        """Execute two-phase atomic workspace ontology promotion under L4 -> L5 locks."""
        return execute_promote_to_workspace(
            self,
            session_id,
            target_workspace,
            expected_workspace_sha256,
            expected_head_sha256=expected_head_sha256,
            if_match=if_match,
            rationale=rationale,
            actor=actor,
            actor_role=actor_role,
            acknowledge_historical_contract_warning=acknowledge_historical_contract_warning,
            historical_contract_rationale=historical_contract_rationale,
            allowed_workspace_roots=allowed_workspace_roots,
            is_remote_request=is_remote_request,
            enforce_lineage=enforce_lineage,
            allow_noop=allow_noop,
            timeout_sec=timeout_sec,
        )

    def _collect_session_lineage_shas(self, session_dir: Path) -> set[str]:
        return collect_session_lineage_shas(session_dir)

    def reconcile_and_recover_session(
        self,
        session_id: str,
        *,
        staging_grace_seconds: float = DEFAULT_STAGING_GRACE_SECONDS,
        timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        """Execute deterministic crash recovery and reconciliation under L5 session lock."""
        execute_reconcile_and_recover_session(
            self,
            session_id,
            staging_grace_seconds=staging_grace_seconds,
            timeout_sec=timeout_sec,
        )

    def _collect_verified_committed_records(
        self, audit_file: Path, session: SessionMetadata
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        return collect_verified_committed_records(audit_file, session)

    def _collect_active_pending_commit_intents(
        self, audit_file: Path, session: SessionMetadata
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        return collect_active_pending_commit_intents(audit_file, session)

    def _verify_audit_chain_or_raise(
        self, session_id: str, session: SessionMetadata, *, mutate: bool = False
    ) -> str:
        return verify_audit_chain_or_raise(
            self.session_manager, self.security_manager, session_id, session, mutate=mutate
        )

    def _read_valid_audit_records(self, audit_file: Path) -> list[dict[str, Any]]:
        return read_valid_audit_records(audit_file)

    def _append_raw_audit_entry_under_lock(
        self,
        session_dir: Path,
        session_id: str,
        action: str,
        target_version: str,
        head_sha256: str,
        actor: str,
        details: dict[str, Any],
        prev_audit_sha: str | None,
    ) -> dict[str, Any]:
        return append_raw_audit_entry_under_lock(
            self.security_manager,
            session_dir,
            session_id,
            action,
            target_version,
            head_sha256,
            actor,
            details,
            prev_audit_sha,
        )

    def _write_session_json(self, session_dir: Path, metadata: SessionMetadata) -> None:
        write_session_json(session_dir, metadata)


__all__ = [
    "DEFAULT_STAGING_GRACE_SECONDS",
    "_IMPORT_ANCHOR_ACTIONS",
    "_SEMVER_REGEX",
    "_STRONG_ETAG_PATTERN",
    "AtomicCommitService",
    "CommitValidationError",
    "ConcurrentModificationError",
    "ForbiddenAdminRequiredError",
    "HistoricalContractRationaleRequiredError",
    "HistoricalContractWarningUnacknowledgedError",
    "InvalidPreconditionHeaderError",
    "LineageRootMismatchError",
    "NoOpPromotionError",
    "PromotionRationaleRequiredError",
    "PromotionResult",
    "SourceNotFoundError",
    "SourceReferencedByCommittedVersionError",
    "SourceReferencedByPendingProposalError",
    "TamperEvidentChainCorruptError",
    "VersionCommitResult",
    "_AcceptedDeltaWaiverVerifierProxy",
    "_LocalWaiverVerifier",
    "_compute_required_target_semver",
    "_extract_sha256_recursively",
    "_extract_strings_matching",
    "_iter_manifest_entries",
    "_load_grandfathered_concept_uris",
    "collect_touched_concept_uris",
    "delete_source",
    "fsync_directory_best_effort",
    "mark_and_sweep_cas",
    "parse_semver_tuple",
    "parse_strong_etag",
    "verify_ont_evo_002_post_state_grounding",
]
