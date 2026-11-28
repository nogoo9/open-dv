"""Stateless column grounding helpers (zero `.opendv/` directory creation)."""

from __future__ import annotations

from pathlib import Path

from opendv.agents import AgentHarnessPort
from opendv.grounding.providers.fibo.knowledge_source import FiboKnowledgeSource
from opendv.grounding.providers.fibo.loader import FiboReferenceOntologyLoader
from opendv.grounding.providers.glossary.knowledge_source import GlossaryKnowledgeSource
from opendv.grounding.spi import KnowledgeSource
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import GroundingResponse, OntologyGroundReq
from opendv.models.policy import STEP4_DEFAULT_BUDGET
from opendv.service.os_platform import resolve_user_state_dir
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.workflows._grounding_orchestrator import ground_columns_orchestrated
from opendv.service.workflows._run_adapters import resolve_harness


def build_grounding_knowledge_source(
    req: OntologyGroundReq,
    *,
    fibo_pack_bytes: bytes | None = None,
    glossary_bytes: bytes | None = None,
    default_loader: FiboReferenceOntologyLoader | None = None,
) -> KnowledgeSource:
    """Build base KnowledgeSource (YAML glossary or FIBO Pack v2) for OntologyGroundReq."""
    if req.knowledge_source == "glossary":
        if glossary_bytes is None and req.glossary_path == "<preloaded>":
            raise OpenDVError(
                "Missing preloaded glossary bytes.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        return (
            GlossaryKnowledgeSource(glossary_bytes=glossary_bytes)
            if glossary_bytes is not None
            else GlossaryKnowledgeSource(glossary_path=req.glossary_path)
        )
    if fibo_pack_bytes is not None:
        return FiboKnowledgeSource(pack_bytes=fibo_pack_bytes)
    if req.fibo_pack is not None:
        return FiboKnowledgeSource(pack_path=req.fibo_pack)
    return FiboKnowledgeSource(loader=default_loader)


async def ground_ontology_stateless(
    req: OntologyGroundReq,
    *,
    fibo_pack_bytes: bytes | None = None,
    glossary_bytes: bytes | None = None,
    default_loader: FiboReferenceOntologyLoader | None = None,
    workspace_root: Path | str | None = None,
    state_dir: Path | str | None = None,
    injected_harness: AgentHarnessPort | None = None,
    readiness_service: HarnessReadinessService | None = None,
    settings_store: SettingsStore | None = None,
    secret_store: KeyringSecretStore | None = None,
) -> GroundingResponse:
    """Execute standalone column grounding statelessly without ProjectStateManager."""
    if req.request is None:
        raise OpenDVError(
            "Stateless ontology grounding requires a request payload.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    ground_req = req.request
    source = build_grounding_knowledge_source(
        req,
        fibo_pack_bytes=fibo_pack_bytes,
        glossary_bytes=glossary_bytes,
        default_loader=default_loader,
    )

    harness: AgentHarnessPort | None = None
    if ground_req.policy.mode != "deterministic_only":
        eff_state_dir = resolve_user_state_dir(state_dir)
        eff_settings_store = (
            settings_store
            if settings_store is not None
            else SettingsStore(user_state_dir=eff_state_dir)
        )
        ws_settings = eff_settings_store.load_settings()
        eff_secret_store = (
            secret_store
            if secret_store is not None
            else KeyringSecretStore(
                (
                    Path(workspace_root).resolve()
                    if workspace_root is not None
                    else Path.cwd().resolve()
                ),
                state_dir=eff_state_dir,
                settings_store=eff_settings_store,
            )
        )
        harness = resolve_harness(
            ws_settings,
            eff_secret_store,
            injected_harness=injected_harness,
            readiness_service=readiness_service,
        )

    return await ground_columns_orchestrated(
        ground_req, source=source, harness=harness, budget=STEP4_DEFAULT_BUDGET
    )


__all__ = [
    "build_grounding_knowledge_source",
    "ground_ontology_stateless",
]
