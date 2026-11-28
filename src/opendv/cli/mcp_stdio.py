"""Unified Open-DV MCP stdio server supporting project (17), parsing (6), and all (23) profiles."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from opendv.cli._client import resolve_cli_workspace_root
from opendv.models import (
    CegarPolicy,
    ConceptMemorySpec,
    ConceptMemoryUpdate,
    ConceptSummary,
    DbtSqlDialect,
    DiscoveryMode,
    ErrorCode,
    HoldAnswersReq,
    HorizonActionReq,
    MigrationApplyResp,
    OntologyCommitReq,
    OntologyConstructReq,
    OntologyRevisionResp,
    OntologyValidateReq,
    OntologyValidationResp,
    OpenDVError,
    ProjectState,
    RunControlReq,
    RunStateResp,
    SourceSummary,
    SqlQueryReq,
    SqlQueryResult,
    StorageCompileReq,
    StorageTargetReq,
    TableIndexEntry,
    TargetBundleResp,
    VaultBuildReq,
    VaultVerifyResp,
    WaiverSubmitReq,
)
from opendv.service import OpenDVService
from opendv.service._mcp_parse_tools import (
    PARSING_MCP_TOOL_NAMES,
    register_parsing_mcp_tools,
)
from opendv.service._mcp_parse_tools import (
    run_mcp_async as _run_async,
)
from opendv.service._mcp_parse_tools import (
    run_mcp_sync as _run_sync,
)
from opendv.service.mcp_broker import JobMcpBroker
from opendv.service.os_platform import resolve_user_state_dir
from opendv.service.parsing_svc import ParsingService
from opendv.service.secrets import scoped_env_first
from opendv.service.workflows._discovery_run_ctx import build_workspace_seed_concepts

MCP_TOOL_NAMES: tuple[str, ...] = (
    "project_get",
    "source_list",
    "evidence_tables_index",
    "concept_memory_update",
    "ontology_search",
    "ontology_construct",
    "ontology_status",
    "ontology_answer",
    "ontology_validate",
    "ontology_commit",
    "vault_build",
    "vault_status",
    "vault_verify",
    "vault_horizon",
    "storage_compile",
    "storage_migrate",
    "storage_query",
)


def _slug_name(name: str) -> str:
    return "_".join(name.strip().lower().split())


def _search_concepts(svc: OpenDVService, project_id: str, query: str) -> list[ConceptSummary]:
    needle = query.strip().lower()
    bundle = svc.get_ontology_bundle(project_id)
    locked_names = (
        {_slug_name(k) for k in bundle.evolution_locks.locked_hub_bks}
        if bundle.evolution_locks is not None
        else set()
    )
    results: list[ConceptSummary] = []

    if bundle.ontology is not None:
        for concept in bundle.ontology.concepts:
            if (
                not needle
                or needle in concept.concept_name.lower()
                or any(needle in syn.lower() for syn in concept.synonyms)
            ):
                results.append(
                    ConceptSummary(
                        concept_uri=concept.concept_uri,
                        concept_name=concept.concept_name,
                        source_layer="committed_graph_a",
                        locked_in_vault=_slug_name(concept.concept_name) in locked_names,
                    )
                )

    for staged in bundle.staged_candidates:
        if not needle or needle in staged.concept_name.lower():
            results.append(
                ConceptSummary(
                    concept_uri=staged.concept_uri,
                    concept_name=staged.concept_name,
                    source_layer="in_run",
                    locked_in_vault=False,
                )
            )

    for item in build_workspace_seed_concepts(svc.state_manager, project_id, svc.workspace_root):
        if not needle or needle in item.concept_name.lower() or needle in item.concept_uri.lower():
            results.append(item)

    deduped: list[ConceptSummary] = []
    seen: set[tuple[str, str, str]] = set()
    for hit in results:
        key = (hit.concept_uri, hit.concept_name, hit.source_layer)
        if key not in seen:
            seen.add(key)
            deduped.append(hit)
    return deduped


def _register_project_mcp_tools(
    mcp: MCPServer,
    *,
    svc: OpenDVService,
    project_id: str,
) -> None:
    # Keep Step 4 JobMcpBroker type linked and available for in-run broker introspection.
    _broker_cls = JobMcpBroker
    _ = _broker_cls

    @mcp.tool(
        name="project_get",
        description="Get active project state and active ontology revision.",
    )
    def project_get() -> ProjectState:
        return _run_sync("project_get", lambda: svc.get_project(project_id))

    @mcp.tool(
        name="source_list",
        description="List data and document sources attached to project.",
    )
    def source_list() -> list[SourceSummary]:
        return _run_sync("source_list", lambda: svc.list_sources(project_id))

    @mcp.tool(
        name="evidence_tables_index",
        description="List indexed source tables across project.",
    )
    def evidence_tables_index() -> list[TableIndexEntry]:
        return _run_sync("evidence_tables_index", lambda: svc.list_tables_index(project_id))

    @mcp.tool(
        name="concept_memory_update",
        description="Pin, mute, or unset a term in concept memory.",
    )
    def concept_memory_update(
        term: str,
        state: Literal["PINNED", "MUTED", "UNSET"],
        concept_uri: str | None = None,
    ) -> ConceptMemorySpec:
        return _run_sync(
            "concept_memory_update",
            lambda: svc.upsert_concept_memory(
                project_id,
                ConceptMemoryUpdate(term=term, state=state, concept_uri=concept_uri),
            ),
        )

    @mcp.tool(
        name="ontology_search",
        description="Search committed Graph A, staged candidates, concept memory, and seeds.",
    )
    def ontology_search(query: str) -> list[ConceptSummary]:
        return _run_sync("ontology_search", lambda: _search_concepts(svc, project_id, query))

    @mcp.tool(name="ontology_construct", description="Run Step 4 ontology construction.")
    async def ontology_construct(
        discovery_mode: DiscoveryMode | None = None,
        hint: str | None = None,
    ) -> RunStateResp:
        req = OntologyConstructReq(discovery_mode=discovery_mode, hint=hint)
        return await _run_async(
            "ontology_construct",
            svc.start_ontology_construction(project_id, req),
        )

    @mcp.tool(
        name="ontology_status",
        description="Fetch Step 4 discovery run status and hold prompts.",
    )
    def ontology_status(run_id: str) -> RunStateResp:
        return _run_sync("ontology_status", lambda: svc.get_ontology_run(project_id, run_id))

    @mcp.tool(
        name="ontology_answer",
        description="Submit clarification answers to a paused Step 4 run.",
    )
    async def ontology_answer(run_id: str, answers: dict[str, str]) -> RunStateResp:
        req = HoldAnswersReq(answers=answers)
        return await _run_async(
            "ontology_answer",
            svc.answer_ontology_hold(project_id, run_id, req),
        )

    @mcp.tool(
        name="ontology_validate",
        description="Dry-run validate Graph A YAML or delta ops.",
    )
    def ontology_validate(
        yaml_text: str | None = None,
        base_revision_id: str | None = None,
        operations: list[dict[str, Any]] | None = None,
    ) -> OntologyValidationResp:
        def _call() -> OntologyValidationResp:
            req = OntologyValidateReq.model_validate(
                {
                    "yaml_text": yaml_text,
                    "base_revision_id": base_revision_id,
                    "operations": operations,
                }
            )
            return svc.validate_ontology(project_id, req)

        return _run_sync("ontology_validate", _call)

    @mcp.tool(name="ontology_commit", description="Commit validated Graph A YAML or delta ops.")
    def ontology_commit(
        base_revision_id: str,
        rationale: str,
        yaml_text: str | None = None,
        waive_quarantine: bool = False,
        operations: list[dict[str, Any]] | None = None,
    ) -> OntologyRevisionResp:
        def _call() -> OntologyRevisionResp:
            req = OntologyCommitReq.model_validate(
                {
                    "base_revision_id": base_revision_id,
                    "rationale": rationale,
                    "yaml_text": yaml_text,
                    "waive_quarantine": waive_quarantine,
                    "operations": operations,
                }
            )
            return svc.commit_ontology(project_id, req)

        return _run_sync("ontology_commit", _call)

    @mcp.tool(name="vault_build", description="Run Step 6 CEGAR Data Vault 2.0 synthesis.")
    async def vault_build(policy: CegarPolicy | None = None) -> RunStateResp:
        return await _run_async(
            "vault_build",
            svc.build_vault(project_id, VaultBuildReq(policy=policy)),
        )

    @mcp.tool(name="vault_status", description="Fetch Step 6 vault build run status.")
    def vault_status(run_id: str) -> RunStateResp:
        return _run_sync("vault_status", lambda: svc.get_vault_run(project_id, run_id))

    @mcp.tool(name="vault_verify", description="Verify Draft/Certified Vault IR deterministically.")
    def vault_verify() -> VaultVerifyResp:
        return _run_sync("vault_verify", lambda: svc.verify_vault(project_id))

    @mcp.tool(
        name="vault_horizon",
        description="Accept/steer horizon, answer holds, submit waivers, or control Step 6 runs.",
    )
    async def vault_horizon(
        run_id: str,
        action: Literal["accept", "steer", "answer_hold", "submit_waiver", "control"],
        answers: dict[str, str] | None = None,
        notes: str = "",
        added_fields: list[str] | None = None,
        waiver: dict[str, Any] | None = None,
        control: dict[str, Any] | None = None,
    ) -> RunStateResp:
        async def _dispatch() -> RunStateResp:
            if action in ("accept", "steer"):
                req = HorizonActionReq(
                    action=action,
                    answers=answers or {},
                    notes=notes,
                    added_fields=added_fields or [],
                )
                return await svc.submit_horizon_steering(project_id, run_id, req)
            if action == "answer_hold":
                return await svc.submit_vault_decisions(
                    project_id, run_id, HoldAnswersReq(answers=answers or {})
                )
            if action == "submit_waiver":
                waiver_req = WaiverSubmitReq.model_validate(waiver or {})
                svc.submit_vault_waiver(project_id, waiver_req)
                return svc.get_vault_run(project_id, run_id)
            control_req = RunControlReq.model_validate(control or {})
            return await svc.send_vault_control(project_id, run_id, control_req)

        return await _run_async("vault_horizon", _dispatch())

    @mcp.tool(
        name="storage_compile",
        description="Compile Step 7 target DDL/DML/dbt/Iceberg artifacts.",
    )
    def storage_compile(
        target_id: str, dbt_sql_dialect: DbtSqlDialect = "duckdb"
    ) -> TargetBundleResp | list[TargetBundleResp]:
        return _run_sync(
            "storage_compile",
            lambda: svc.compile_target(
                project_id,
                StorageCompileReq(target_id=target_id, dbt_sql_dialect=dbt_sql_dialect),
            ),
        )

    @mcp.tool(
        name="storage_migrate",
        description="Apply local DuckDB or Parquet storage migration.",
    )
    def storage_migrate(target_id: Literal["duckdb_local", "parquet"]) -> MigrationApplyResp:
        return _run_sync(
            "storage_migrate",
            lambda: svc.migrate_target(project_id, StorageTargetReq(target_id=target_id)),
        )

    @mcp.tool(
        name="storage_query",
        description="Execute bounded read-only SQL query against local storage.",
    )
    def storage_query(sql: str, limit: int = 1000) -> SqlQueryResult:
        return _run_sync(
            "storage_query",
            lambda: svc.query_target(project_id, SqlQueryReq(sql=sql, limit=limit)),
        )


def create_mcp_stdio_server(
    *,
    profile: Literal["project", "parsing", "all"] = "project",
    project_id: str | None = None,
    workspace_root: Path | str | None = None,
    state_dir: Path | None = None,
    service: OpenDVService | None = None,
    parsing_service: ParsingService | None = None,
    allow_roots: Sequence[Path] | None = None,
    allow_unredacted: bool = False,
    max_inline_bytes: int = 65_536,
    spill_dir: Path | None = None,
) -> MCPServer:
    """Register Open-DV lifecycle (17), parsing (6), or combined (23) MCP tools."""
    if profile not in ("project", "parsing", "all"):
        raise OpenDVError(
            f"Unsupported MCP profile '{profile}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if max_inline_bytes < 1:
        raise OpenDVError(
            "--max-inline-bytes must be >= 1.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if profile == "parsing" and project_id is not None:
        raise OpenDVError(
            "--project cannot be passed with --profile parsing.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if profile in ("project", "all") and (project_id is None or not project_id.strip()):
        raise OpenDVError(
            f"--project is required for --profile {profile}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    resolved_root = resolve_cli_workspace_root(workspace_root)
    instructions = (
        "Open-DV V2 stateless parsing MCP server. Errors carry structured ProblemDetails JSON."
        if profile == "parsing"
        else (
            f"Open-DV V2 lifecycle MCP server pinned to project '{project_id}'. "
            "Errors carry structured ProblemDetails JSON."
        )
    )
    mcp = MCPServer(name="opendv", instructions=instructions)

    if profile in ("project", "all"):
        assert project_id is not None
        svc = service if service is not None else OpenDVService(resolved_root, state_dir=state_dir)
        _register_project_mcp_tools(mcp, svc=svc, project_id=project_id)

    if profile in ("parsing", "all"):
        eff_roots = list(allow_roots) if allow_roots is not None else [resolved_root]
        eff_spill = (
            spill_dir
            if spill_dir is not None
            else (resolve_user_state_dir(state_dir) / "parse_spill")
        )
        p_svc = (
            parsing_service
            if parsing_service is not None
            else ParsingService(workspace_root=resolved_root, allowed_roots=eff_roots)
        )
        register_parsing_mcp_tools(
            mcp,
            parsing_service=p_svc,
            allow_roots=eff_roots,
            allow_unredacted=allow_unredacted,
            max_inline_bytes=max_inline_bytes,
            spill_dir=eff_spill,
            workspace_root=resolved_root,
        )

    return mcp


def run_mcp_stdio_server(
    *,
    profile: Literal["project", "parsing", "all"] = "project",
    project_id: str | None = None,
    workspace_root: Path | str | None = None,
    state_dir: Path | None = None,
    allow_roots: Sequence[Path] | None = None,
    allow_unredacted: bool = False,
    max_inline_bytes: int = 65_536,
    spill_dir: Path | None = None,
    cli_env_first: bool = False,
) -> None:
    """Start the blocking JSON-RPC stdio loop for the Open-DV MCP server."""
    with scoped_env_first(cli_env_first):
        server = create_mcp_stdio_server(
            profile=profile,
            project_id=project_id,
            workspace_root=workspace_root,
            state_dir=state_dir,
            allow_roots=allow_roots,
            allow_unredacted=allow_unredacted,
            max_inline_bytes=max_inline_bytes,
            spill_dir=spill_dir,
        )
        server.run(transport="stdio")


__all__ = [
    "MCP_TOOL_NAMES",
    "PARSING_MCP_TOOL_NAMES",
    "create_mcp_stdio_server",
    "run_mcp_stdio_server",
]
