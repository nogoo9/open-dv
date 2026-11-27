"""Unified Steps 2-5 Discovery & Ontology workflow facade."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

from opendv.agents import AgentHarnessPort
from opendv.grounding.kernel.overlay import OverlayKnowledgeSource
from opendv.grounding.providers.fibo.knowledge_source import FiboKnowledgeSource
from opendv.grounding.providers.fibo.loader import FiboPackQueryAdapter
from opendv.grounding.providers.glossary.knowledge_source import GlossaryKnowledgeSource
from opendv.grounding.spi import KnowledgeSource
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import (
    FiboProjectDriftReport,
    FiboSyncReq,
    FiboSyncResp,
    GroundingResponse,
    OntologyGroundReq,
)
from opendv.models.ontology import CanonicalBusinessConcept, GraphAOntology
from opendv.models.policy import STEP4_DEFAULT_BUDGET
from opendv.models.project import ConceptMemorySpec, StagedCandidateSpec
from opendv.models.sources import (
    ConnectorAdapterSpec,
    DocumentMap,
    PageTextView,
    SourceProfile,
    SourceSummary,
    TableIndexEntry,
    TablePreviewPage,
)
from opendv.models.wire_dtos_discovery import (
    CitationResolveReq,
    CitationSpanResp,
    FiboQueryReq,
    FiboQueryResp,
    HoldAnswersReq,
    OntologyBundleResp,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyRevisionResp,
    OntologyValidateReq,
    OntologyValidationResp,
    RunControlReq,
    RunStateResp,
    ScanApprovalReq,
    SourceAttachReq,
    SourceInspectReq,
    SourceReattachReq,
)
from opendv.service.citations import resolve_citation
from opendv.service.fibo_pack_ops import check_project_drift, install_fibo_pack
from opendv.service.readiness import HarnessReadinessService
from opendv.service.secrets import KeyringSecretStore
from opendv.service.settings_store import SettingsStore
from opendv.service.state_store import ProjectStateManager
from opendv.service.turtle_writer import export_ontology_turtle
from opendv.service.workflows._discovery_ontology import DiscoveryOntologyHelper
from opendv.service.workflows._discovery_run import DiscoveryRunHelper
from opendv.service.workflows._discovery_sources import (
    DiscoverySourcesHelper,
    list_adapters,
)
from opendv.service.workflows._grounding_orchestrator import ground_columns_orchestrated
from opendv.service.workflows._run_adapters import load_latest_run_state, resolve_harness
from opendv.service.workflows._source_docs import load_doc_pages_for_record
from opendv.sources.grounding_adapter import source_profiles_to_grounding_request

_RFC3987_IRI_RE = re.compile(
    r"\A[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|\\^`\x00-\x1f\x7f-\x9f]+\Z"
)


def _safe_staged_uri(raw_uri: str, namespace: str, candidate_id: str) -> str:
    cleaned = raw_uri.strip()
    if cleaned and _RFC3987_IRI_RE.match(cleaned):
        parts = urllib.parse.urlsplit(cleaned)
        if parts.scheme.lower() not in ("http", "https") or parts.netloc:
            return cleaned
    return f"urn:{namespace}:{urllib.parse.quote(candidate_id, safe='')}"


def _dedupe_models[M: BaseModel](existing: list[M], extra: list[M]) -> list[M]:
    seen = {x.model_dump_json() for x in existing}
    merged = list(existing)
    for item in extra:
        key = item.model_dump_json()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _merge_staged_candidates(
    state_manager: ProjectStateManager,
    project_id: str,
    ont: GraphAOntology,
) -> GraphAOntology:
    revs = state_manager.list_revisions(project_id, rev_kind="staged_candidate")
    if not revs:
        return ont
    concepts = list(ont.concepts)
    by_uri = {c.concept_uri: idx for idx, c in enumerate(concepts)}
    for row in revs:
        cand = StagedCandidateSpec.model_validate(row["payload"])
        if cand.status == "PROMOTED":
            continue
        uri = _safe_staged_uri(cand.concept_uri, ont.domain_namespace, cand.candidate_id)
        if uri in by_uri:
            idx = by_uri[uri]
            cur = concepts[idx]
            syns = list(cur.synonyms)
            if cand.concept_name != cur.concept_name and cand.concept_name not in syns:
                syns.append(cand.concept_name)
            concepts[idx] = cur.model_copy(
                update={
                    "synonyms": syns,
                    "table_bindings": _dedupe_models(cur.table_bindings, cand.table_bindings),
                    "source_citations": _dedupe_models(cur.source_citations, cand.source_citations),
                    "mcp_evidence": _dedupe_models(cur.mcp_evidence, cand.mcp_evidence),
                }
            )
        else:
            by_uri[uri] = len(concepts)
            concepts.append(
                CanonicalBusinessConcept(
                    concept_uri=uri,
                    concept_name=cand.concept_name,
                    description=cand.rationale or f"Staged ({cand.status}, {cand.grounding_path})",
                    key_authority_tier="TIER_2_ENTERPRISE_SOR",
                    governance_tags=[f"staged:{cand.status.lower()}"],
                    table_bindings=list(cand.table_bindings),
                    source_citations=list(cand.source_citations),
                    mcp_evidence=list(cand.mcp_evidence),
                )
            )
    return ont.model_copy(update={"concepts": concepts})


class DiscoveryService:
    """Facade combining Steps 2-3 Sources/Docs and Steps 4-5 Ontology Discovery/Commit."""

    def __init__(
        self,
        *,
        state_manager: ProjectStateManager,
        settings_store: SettingsStore,
        secret_store: KeyringSecretStore,
        workspace_root: Path,
        matcher: FiboPackQueryAdapter | None = None,
        injected_harness: AgentHarnessPort | None = None,
        readiness_service: HarnessReadinessService | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self._state = state_manager
        self._settings = settings_store
        self._secrets = secret_store
        self._injected_harness = injected_harness
        self._readiness_service = readiness_service
        self._state_dir = state_dir
        self._matcher = matcher or FiboPackQueryAdapter()
        self._workspace_root = workspace_root
        self._sources = DiscoverySourcesHelper(state_manager, secret_store, workspace_root)
        self._ontology = DiscoveryOntologyHelper(state_manager, self._matcher, workspace_root)
        self._runs = DiscoveryRunHelper(
            state_manager=state_manager,
            settings_store=settings_store,
            secret_store=secret_store,
            matcher=self._matcher,
            workspace_root=workspace_root,
            injected_harness=injected_harness,
            readiness_service=readiness_service,
        )

    def list_adapters(self) -> list[ConnectorAdapterSpec]:
        return list_adapters()

    def inspect_source(self, req: SourceInspectReq) -> SourceProfile:
        return self._sources.inspect_source(req)

    def attach_source(self, project_id: str, req: SourceAttachReq) -> SourceSummary:
        return self._sources.attach_source(project_id, req)

    def upload_source(
        self,
        project_id: str,
        *,
        filename: str,
        content: bytes,
        kind: str | None = None,
    ) -> SourceSummary:
        return self._sources.upload_source(
            project_id, filename=filename, content=content, kind=kind
        )

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        return self._sources.list_sources(project_id)

    def reattach_source(
        self, project_id: str, source_id: str, req: SourceReattachReq
    ) -> SourceSummary:
        return self._sources.reattach_source(project_id, source_id, req)

    def detach_source(self, project_id: str, source_id: str) -> None:
        self._sources.detach_source(project_id, source_id)

    def list_tables_index(self, project_id: str) -> list[TableIndexEntry]:
        return self._sources.list_tables_index(project_id)

    def preview_table(
        self,
        project_id: str,
        source_id: str,
        table_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> TablePreviewPage:
        return self._sources.preview_table(
            project_id, source_id, table_id, offset=offset, limit=limit
        )

    def preview_table_by_id(
        self,
        project_id: str,
        table_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        source_id: str | None = None,
    ) -> TablePreviewPage:
        return self._sources.preview_table_by_id(
            project_id, table_id, offset=offset, limit=limit, source_id=source_id
        )

    def list_document_maps(self, project_id: str) -> list[DocumentMap]:
        return self._sources.list_document_maps(project_id)

    def get_document_map(self, project_id: str, source_id: str) -> DocumentMap:
        return self._sources.get_document_map(project_id, source_id)

    def get_document_pages(
        self,
        project_id: str,
        source_id: str,
        *,
        page_start: int = 1,
        page_end: int | None = None,
    ) -> list[PageTextView]:
        return self._sources.get_document_pages(
            project_id, source_id, page_start=page_start, page_end=page_end
        )

    def approve_scan_pages(
        self, project_id: str, source_id: str, req: ScanApprovalReq
    ) -> DocumentMap:
        return self._sources.approve_scan_pages(project_id, source_id, req)

    async def start_ontology_construction(
        self,
        project_id: str,
        req: OntologyConstructReq,
        *,
        run_id: str | None = None,
    ) -> RunStateResp:
        return await self._runs.start_construction(project_id, req, run_id=run_id)

    def get_ontology_run(self, project_id: str, run_id: str) -> RunStateResp:
        """Return latest RunStateResp for a Step 4 ontology discovery run."""
        state = load_latest_run_state(self._state, project_id, run_id)
        if state.step != "step4":
            raise OpenDVError(
                f"Step 4 run '{run_id}' not found in project '{project_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return state

    async def answer_ontology_hold(
        self, project_id: str, run_id: str, req: HoldAnswersReq
    ) -> RunStateResp:
        return await self._runs.answer_hold(project_id, run_id, req)

    def control_ontology_run(
        self, project_id: str, run_id: str, req: RunControlReq
    ) -> RunStateResp:
        return self._runs.control_run(project_id, run_id, req)

    def get_ontology_bundle(self, project_id: str) -> OntologyBundleResp:
        return self._ontology.get_ontology_bundle(project_id)

    def validate_ontology(
        self, project_id: str, req: OntologyValidateReq
    ) -> OntologyValidationResp:
        return self._ontology.validate_ontology(project_id, req)

    def commit_ontology(
        self, project_id: str, req: OntologyCommitReq
    ) -> OntologyRevisionResp:
        return self._ontology.commit_ontology(project_id, req)

    def fibo_qa(
        self,
        req: FiboQueryReq,
        *,
        project_id: str | None = None,
    ) -> FiboQueryResp:
        """Query bundled offline FIBO concepts with BM25, optionally scoped to project_id."""
        effective_req = req
        if project_id is not None:
            proj = self._state.get_project(project_id)
            if req.domain_namespace is None:
                effective_req = req.model_copy(
                    update={"domain_namespace": proj.domain_namespace}
                )
        return self._matcher.query(effective_req)

    def resolve_citation_span(
        self, project_id: str, req: CitationResolveReq
    ) -> CitationSpanResp:
        """Resolve a verbatim citation quote span against an attached project document."""
        for src in self._state.list_sources(project_id):
            rec = self._state.get_source_record(project_id, src.source_id)
            if rec["document_map"] is None:
                continue
            dmap = DocumentMap.model_validate(rec["document_map"])
            if req.doc_id not in (src.source_id, dmap.doc_id):
                continue
            meta_only = str(rec["status"]) == "METADATA_ONLY_BINARY_ABSENT"
            pages = (
                None
                if meta_only
                else load_doc_pages_for_record(
                    self._state, self._workspace_root, project_id, src.source_id, rec, dmap
                )
            )
            page_map = (
                {idx + 1: txt for idx, txt in enumerate(pages)} if pages is not None else None
            )
            return resolve_citation(
                req,
                parsed_pages=page_map,
                metadata_only_binary_absent=meta_only,
                document_map=dmap,
            )
        raise OpenDVError(
            f"Document '{req.doc_id}' not found in project '{project_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    def export_ontology(
        self,
        project_id: str,
        *,
        fmt: Literal["yaml", "json", "ttl"] = "yaml",
        include_staged: bool = False,
    ) -> tuple[str, str]:
        """Serialize the active GraphAOntology as YAML, JSON, or Turtle (RDF)."""
        proj = self._state.get_project(project_id)
        if proj.active_ontology_rev_id is None:
            ont = GraphAOntology(ontology_version="0.0.0", domain_namespace=proj.domain_namespace)
        else:
            rev = self._state.get_revision(project_id, proj.active_ontology_rev_id)
            ont = GraphAOntology.model_validate(rev["payload"])
        if include_staged:
            ont = _merge_staged_candidates(self._state, project_id, ont)
        if fmt == "yaml":
            text = yaml.safe_dump(ont.model_dump(mode="json"), sort_keys=False)
            return text, "application/yaml"
        if fmt == "json":
            return json.dumps(ont.model_dump(mode="json"), indent=2), "application/json"
        if fmt == "ttl":
            return export_ontology_turtle(ont)
        raise OpenDVError(
            f"Unsupported ontology export format '{fmt}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    async def ground_ontology(
        self,
        req: OntologyGroundReq,
        *,
        fibo_pack_bytes: bytes | None = None,
        glossary_bytes: bytes | None = None,
    ) -> GroundingResponse:
        """Run standalone or project-backed column grounding via Layer-4 orchestrator."""
        proj = None
        if req.request is not None:
            ground_req = req.request
        else:
            assert req.project_id is not None
            proj = self._state.get_project(req.project_id)
            profiles = [
                SourceProfile.model_validate(prof_raw)
                for s in self._state.list_sources(req.project_id)
                if (
                    prof_raw := self._state.get_source_record(req.project_id, s.source_id).get(
                        "profile"
                    )
                )
                is not None
            ]
            try:
                ground_req = source_profiles_to_grounding_request(
                    profiles,
                    project_state=proj,
                    policy=req.policy,
                    max_tables=50,
                    max_columns=500,
                )
            except ValidationError as exc:
                raise OpenDVError(
                    str(exc),
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                ) from exc

        if req.knowledge_source == "glossary":
            base_source: KnowledgeSource = (
                GlossaryKnowledgeSource(glossary_bytes=glossary_bytes)
                if glossary_bytes is not None
                else GlossaryKnowledgeSource(glossary_path=req.glossary_path)
            )
        elif fibo_pack_bytes is not None:
            base_source = FiboKnowledgeSource(pack_bytes=fibo_pack_bytes)
        elif req.fibo_pack is not None:
            base_source = FiboKnowledgeSource(pack_path=req.fibo_pack)
        else:
            base_source = FiboKnowledgeSource(loader=self._matcher.loader)

        source: KnowledgeSource = base_source
        if proj is not None and req.project_id is not None:
            existing_ont = (
                GraphAOntology.model_validate(
                    self._state.get_revision(req.project_id, proj.active_ontology_rev_id)["payload"]
                )
                if proj.active_ontology_rev_id is not None
                else None
            )
            concept_memory = [
                ConceptMemorySpec.model_validate(r["payload"])
                for r in self._state.list_revisions(req.project_id, rev_kind="concept_memory")
            ]
            source = OverlayKnowledgeSource(
                base_source,
                ontology=existing_ont,
                concept_memory=concept_memory,
                discovery_mode=proj.settings_snapshot.discovery_mode,
            )

        harness = (
            None
            if ground_req.policy.mode == "deterministic_only"
            else resolve_harness(
                proj.settings_snapshot if proj is not None else self._settings.load_settings(),
                self._secrets,
                injected_harness=self._injected_harness,
                readiness_service=self._readiness_service,
            )
        )
        return await ground_columns_orchestrated(
            ground_req, source=source, harness=harness, budget=STEP4_DEFAULT_BUDGET
        )

    def run_fibo_sync(self, req: FiboSyncReq) -> FiboSyncResp:
        """Install/verify FIBO Pack v2 locally and optionally check/repin a project."""
        pack_file = req.pack_file.strip() if req.pack_file is not None else None
        if req.pack_file is not None and not pack_file:
            raise OpenDVError(
                "pack_file cannot be blank.", error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT
            )
        check_pid = req.check_project_id.strip() if req.check_project_id is not None else None
        if req.check_project_id is not None and not check_pid:
            raise OpenDVError(
                "check_project_id cannot be blank.", error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT
            )
        if not pack_file and not check_pid:
            raise OpenDVError(
                "Specify at least one of pack_file or check_project_id.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if req.apply_repin and not check_pid:
            raise OpenDVError(
                "apply_repin requires check_project_id.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

        installed: FiboSyncResp | None = None
        if pack_file is not None:
            installed = install_fibo_pack(pack_file, tag=req.tag, state_dir=self._state_dir)

        drift: FiboProjectDriftReport | None = None
        if check_pid is not None:
            drift = check_project_drift(
                self._state,
                check_pid,
                pack_file=pack_file,
                tag=req.tag,
                state_dir=self._state_dir,
                apply_repin=req.apply_repin,
            )

        return FiboSyncResp(
            installed_pack_path=installed.installed_pack_path if installed else None,
            pack_sha256=(
                installed.pack_sha256
                if installed
                else (drift.pack_sha256_new if drift else None)
            ),
            upstream_tag=installed.upstream_tag if installed else req.tag,
            drift_report=drift,
        )
