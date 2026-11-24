"""Step 7 multi-target SQL/format compilation, materialization, regenerate, and query."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import DbtSqlDialect, EvolutionLockSpec, TargetManifest
from opendv.models.sources import ConnectorConfigPayload, SourceKind
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import (
    ArtifactFileSpec,
    MigrationApplyResp,
    SqlQueryReq,
    SqlQueryResult,
    StorageCompileReq,
    StorageTargetReq,
    TargetBundleResp,
)
from opendv.service.os_platform import atomic_write_text
from opendv.service.state_store import ProjectStateManager
from opendv.service.workflows._run_adapters import (
    append_audit_log,
    duckdb_write_lease_key,
    list_project_runs,
    load_active_certified_ir,
    load_latest_evolution_lock,
)
from opendv.service.workflows._source_docs import resolve_readable_source_path
from opendv.storage import (
    ALL_TARGET_IDS,
    SQL_TARGET_IDS,
    DbtEmitter,
    IcebergEmitter,
    ParquetEmitter,
    SqlGlotVaultCompiler,
    TargetEvalGate,
    execute_readonly_query,
    get_dialect_profile,
    materialize_duckdb_local,
    materialize_parquet_local,
)
from opendv.storage.local_runner import SourceAttachment

_FORBIDDEN_CONFIG_KEY_RE = re.compile(r"(?:token|secret|password|api[_-]?key)", re.IGNORECASE)
_STRUCTURED_KINDS: frozenset[str] = frozenset({"sqlite", "duckdb", "parquet", "csv", "xlsx"})


def _validate_target_id(target_id: str) -> str:
    if target_id not in ALL_TARGET_IDS:
        raise OpenDVError(
            f"Unsupported target_id '{target_id}'. Valid targets: {', '.join(ALL_TARGET_IDS)}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return target_id


def _artifact_kind(
    target_id: str,
    relpath: str,
) -> Literal["ddl", "dml", "manifest", "metadata", "parquet", "dbt"]:
    lower = relpath.lower()
    if lower.endswith("manifest.json"):
        return "manifest"
    if target_id == "parquet":
        return "parquet" if lower.endswith(".parquet") else "manifest"
    if target_id == "dbt":
        return "dbt"
    if target_id == "iceberg":
        return "metadata"
    if "ddl" in lower:
        return "ddl"
    return "dml"


def _scan_artifacts(target_id: str, target_dir: Path) -> list[ArtifactFileSpec]:
    specs: list[ArtifactFileSpec] = []
    if not target_dir.is_dir():
        return specs
    for p in sorted(target_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(target_dir).as_posix()
        raw = p.read_bytes()
        specs.append(
            ArtifactFileSpec(
                relpath=rel,
                kind=_artifact_kind(target_id, rel),
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    return specs


def _deterministic_receipt_id(
    project_id: str,
    wave_number: int,
    target_id: str,
    artifacts: list[ArtifactFileSpec],
) -> str:
    h = hashlib.sha256(f"{project_id}:w{wave_number}:{target_id}".encode())
    for art in artifacts:
        if art.relpath != "target_manifest.json":
            h.update(f"{art.relpath}:{art.sha256}\n".encode())
    return f"EVAL-{h.hexdigest()[:8]}"


class StorageService:
    """Step 7 compilation, migration, regeneration, evaluation receipts, and read-only SQL."""

    def __init__(
        self,
        *,
        state_manager: ProjectStateManager,
        workspace_root: Path,
    ) -> None:
        self._state = state_manager
        self._workspace_root = workspace_root

    def _config_file(self, project_id: str) -> Path:
        return self._state.targets_dir(project_id) / "target_configs.json"

    def _load_configs(self, project_id: str) -> dict[str, dict[str, Any]]:
        cfg_file = self._config_file(project_id)
        if not cfg_file.is_file():
            return {}
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _load_active_ir_and_prior(
        self,
        project_id: str,
    ) -> tuple[CertifiedVaultIR, CertifiedVaultIR | None, EvolutionLockSpec | None]:
        ir = load_active_certified_ir(self._state, project_id)
        revs = self._state.list_revisions(project_id, rev_kind="wave_ir")

        prior_ir: CertifiedVaultIR | None = None
        for rev in reversed(revs):
            cand = CertifiedVaultIR.model_validate(rev["payload"])
            if cand.wave_number < ir.wave_number:
                prior_ir = cand
                break
        locks = load_latest_evolution_lock(self._state, project_id)
        return ir, prior_ir, locks

    def _certification_status(self, project_id: str) -> Literal["CERTIFIED", "CERTIFIED_FAST"]:
        runs = list_project_runs(self._state, project_id)
        step6 = [r for r in runs if r.step == "step6" and r.status == "COMPLETED"]
        if step6 and step6[0].policy == "fast":
            return "CERTIFIED_FAST"
        return "CERTIFIED"

    def _build_source_attachments(
        self,
        project_id: str,
    ) -> tuple[list[SourceAttachment], bool]:
        attachments: list[SourceAttachment] = []
        meta_only = False
        for src in self._state.list_sources(project_id):
            rec = self._state.get_source_record(project_id, src.source_id)
            kind = str(rec["kind"])
            if kind not in _STRUCTURED_KINDS:
                continue
            if str(rec["status"]) == "METADATA_ONLY_BINARY_ABSENT":
                meta_only = True
                continue
            path = resolve_readable_source_path(
                self._state, self._workspace_root, project_id, rec
            )
            if path is None:
                cfg = ConnectorConfigPayload.model_validate(rec["connector_config"])
                raw_uri = str(cfg.uri_or_path or rec["uri_or_filename"])
                cand = Path(raw_uri)
                path = cand if cand.is_absolute() else (self._workspace_root / cand)
            typed_kind: SourceKind = kind  # type: ignore[assignment]
            attachments.append(
                SourceAttachment(
                    source_id=src.source_id,
                    kind=typed_kind,
                    path=path,
                )
            )
        return attachments, meta_only

    def list_targets(self, project_id: str) -> list[dict[str, Any]]:
        """List status and manifest summaries across all 11 supported targets."""
        self._state.get_project(project_id)
        configs = self._load_configs(project_id)
        targets_root = self._state.targets_dir(project_id)
        summaries: list[dict[str, Any]] = []
        for tid in ALL_TARGET_IDS:
            profile = get_dialect_profile(tid)
            tdir = targets_root / tid
            manifest_path = tdir / "target_manifest.json"
            manifest_obj: dict[str, Any] | None = None
            if manifest_path.is_file():
                manifest_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
            summaries.append(
                {
                    "target_id": tid,
                    "sqlglot_dialect": profile.sqlglot_dialect,
                    "verification_tier": profile.verification_tier,
                    "config": configs.get(tid, {}),
                    "compiled": manifest_obj is not None,
                    "manifest": manifest_obj,
                }
            )
        return summaries

    def list_target_manifests(self, project_id: str) -> list[TargetManifest]:
        """List validated TargetManifest records for all compiled targets of project_id."""
        self._state.get_project(project_id)
        targets_root = self._state.targets_dir(project_id)
        manifests: list[TargetManifest] = []
        for tid in ALL_TARGET_IDS:
            manifest_path = targets_root / tid / "target_manifest.json"
            if manifest_path.is_file():
                manifests.append(
                    TargetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                )
        return manifests

    def upsert_target_config(
        self,
        project_id: str,
        target_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Save non-secret target configuration under project targets/."""
        self._state.get_project(project_id)
        tid = _validate_target_id(target_id)
        for key in config:
            if _FORBIDDEN_CONFIG_KEY_RE.search(key):
                raise OpenDVError(
                    f"Plaintext secret key '{key}' is forbidden in target config; use secret_refs.",
                    error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                )
        configs = self._load_configs(project_id)
        configs[tid] = dict(config)
        cfg_file = self._config_file(project_id)
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cfg_file, json.dumps(configs, indent=2, sort_keys=True))
        return configs[tid]

    def list_receipts(self, project_id: str) -> list[dict[str, Any]]:
        """List recorded Step 7 evaluation and migration audit receipts for project_id."""
        self._state.get_project(project_id)
        rows = self._state.list_events_or_receipts(project_id, record_kind="audit_log")
        receipts: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload") or {}
            if isinstance(payload, dict) and str(payload.get("step") or "") == "step7":
                receipts.append(payload)
        return receipts

    def compile_target(
        self,
        project_id: str,
        req: StorageCompileReq,
    ) -> TargetBundleResp | list[TargetBundleResp]:
        """Compile DDL/DML or format bundle for target_id (or all 11 targets if 'all')."""
        if req.target_id == "all":
            return self.compile_all_targets(
                project_id,
                dbt_sql_dialect=req.dbt_sql_dialect,
                verify_runtime=req.verify_runtime,
            )
        return self._compile_single_target(project_id, req)

    def compile_all_targets(
        self,
        project_id: str,
        *,
        dbt_sql_dialect: DbtSqlDialect = "duckdb",
        verify_runtime: bool = False,
    ) -> list[TargetBundleResp]:
        """Compile and gate all 11 supported targets deterministically."""
        self._state.get_project(project_id)
        return [
            self._compile_single_target(
                project_id,
                StorageCompileReq(
                    target_id=tid,
                    dbt_sql_dialect=dbt_sql_dialect,
                    verify_runtime=verify_runtime,
                ),
            )
            for tid in ALL_TARGET_IDS
        ]

    def _compile_single_target(
        self,
        project_id: str,
        req: StorageCompileReq,
    ) -> TargetBundleResp:
        """Compile DDL/DML or format bundle for a single target_id and gate via TargetEvalGate."""
        tid = _validate_target_id(req.target_id)
        ir, prior_ir, locks = self._load_active_ir_and_prior(project_id)
        cert_status = self._certification_status(project_id)
        target_dir = self._state.targets_dir(project_id) / tid
        target_dir.mkdir(parents=True, exist_ok=True)
        gate = TargetEvalGate()

        manifest: TargetManifest
        if tid in SQL_TARGET_IDS:
            bundle = SqlGlotVaultCompiler(tid).compile(
                ir,
                prior_ir=prior_ir,
                locks=locks,
                output_dir=target_dir,
                certification_status=cert_status,
            )
            gate.verify_sql_ast(tid, bundle.full_sql, ir=ir)
            if tid == "duckdb_local" and req.verify_runtime:
                gate.verify_duckdb_runtime(
                    ir,
                    bundle.ddl_sql,
                    bundle.dml_sql,
                    physical_aliases=bundle.physical_aliases,
                )
            manifest = bundle.manifest or TargetManifest(
                target_id=tid,
                last_compiled_wave=ir.wave_number,
                verification_tier=bundle.dialect_profile.verification_tier,
                certification_status=cert_status,
                physical_aliases=bundle.physical_aliases,
            )
        elif tid == "iceberg":
            ice = IcebergEmitter().emit(ir, prior_ir=prior_ir, locks=locks, output_dir=target_dir)
            gate.verify_format_bundle("iceberg", target_dir, ir)
            manifest = ice.manifest
        elif tid == "parquet":
            pq_bundle = ParquetEmitter().emit(ir, target_dir, locks=locks)
            gate.verify_format_bundle("parquet", target_dir, ir)
            manifest = pq_bundle.manifest
        else:
            dbt_dialect: DbtSqlDialect = req.dbt_sql_dialect
            dbt_bundle = DbtEmitter().emit(
                ir,
                dbt_sql_dialect=dbt_dialect,
                locks=locks,
                output_dir=target_dir,
            )
            gate.verify_format_bundle("dbt", target_dir, ir, dbt_sql_dialect=dbt_dialect)
            manifest = dbt_bundle.manifest

        pre_artifacts = _scan_artifacts(tid, target_dir)
        receipt_id = _deterministic_receipt_id(project_id, ir.wave_number, tid, pre_artifacts)
        receipts = list(manifest.eval_receipts)
        if receipt_id not in receipts:
            receipts.append(receipt_id)
        final_manifest = manifest.model_copy(
            update={
                "eval_receipts": receipts,
                "certification_status": cert_status,
                "generated_files": [a.relpath for a in pre_artifacts],
            }
        )
        atomic_write_text(
            target_dir / "target_manifest.json",
            final_manifest.model_dump_json(indent=2),
        )
        artifacts = _scan_artifacts(tid, target_dir)
        append_audit_log(
            self._state,
            project_id,
            event_type="EVAL_RECEIPT",
            actor="system",
            step="step7",
            summary=f"Compiled and verified target '{tid}' wave {ir.wave_number} ({receipt_id}).",
            details={
                "target_id": tid,
                "wave_number": ir.wave_number,
                "receipt_id": receipt_id,
                "artifact_count": len(artifacts),
            },
        )
        return TargetBundleResp(target_id=tid, manifest=final_manifest, artifacts=artifacts)

    def _apply_materialization(
        self,
        project_id: str,
        target_id: str,
        *,
        regenerate: bool,
    ) -> MigrationApplyResp:
        tid = _validate_target_id(target_id)
        ir, prior_ir, locks = self._load_active_ir_and_prior(project_id)
        target_dir = self._state.targets_dir(project_id) / tid

        if regenerate and target_dir.exists() and tid not in ("duckdb_local", "parquet"):
            shutil.rmtree(target_dir)

        if tid == "duckdb_local":
            attachments, meta_only = self._build_source_attachments(project_id)
            lease_key = duckdb_write_lease_key(project_id, tid)
            holder_id = f"mig-{uuid.uuid4().hex[:10]}"
            self._state.claim_lease(project_id, lease_key=lease_key, holder_run_id=holder_id)
            try:
                resp = materialize_duckdb_local(
                    ir,
                    target_dir,
                    attachments,
                    prior_ir=prior_ir,
                    locks=locks,
                    metadata_only=meta_only,
                    regenerate=regenerate,
                )
            finally:
                self._state.release_lease(project_id, lease_key=lease_key, holder_run_id=holder_id)
        elif tid == "parquet":
            attachments, meta_only = self._build_source_attachments(project_id)
            resp = materialize_parquet_local(
                ir,
                target_dir,
                attachments,
                locks=locks,
                metadata_only=meta_only,
                regenerate=regenerate,
            )
        else:
            proj = self._state.get_project(project_id)
            compiled = self._compile_single_target(
                project_id,
                StorageCompileReq(
                    target_id=tid,
                    dbt_sql_dialect=proj.settings_snapshot.dbt_sql_dialect,
                ),
            )
            resp = MigrationApplyResp(
                target_id=compiled.target_id,
                wave_number=ir.wave_number,
                rows_loaded=0,
                verified=True,
            )

        append_audit_log(
            self._state,
            project_id,
            event_type="MIGRATION_REGENERATED" if regenerate else "MIGRATION_APPLIED",
            actor="system",
            step="step7",
            summary=(
                f"{'Regenerated' if regenerate else 'Migrated'} target '{tid}' "
                f"wave {resp.wave_number} (rows_loaded={resp.rows_loaded})."
            ),
            details={
                "target_id": tid,
                "wave_number": resp.wave_number,
                "rows_loaded": resp.rows_loaded,
                "verified": resp.verified,
            },
        )
        return resp

    def migrate_target(
        self,
        project_id: str,
        req: StorageTargetReq,
    ) -> MigrationApplyResp:
        """Apply migration/materialization onto target_id (acquiring DuckDB write lease)."""
        return self._apply_materialization(project_id, req.target_id, regenerate=False)

    def regenerate_target(
        self,
        project_id: str,
        req: StorageTargetReq,
    ) -> MigrationApplyResp:
        """Cleanly wipe target_dir and deterministically regenerate all artifacts from Wave IR."""
        return self._apply_materialization(project_id, req.target_id, regenerate=True)

    def query_target(
        self,
        project_id: str,
        req: SqlQueryReq,
    ) -> SqlQueryResult:
        """Execute a sandboxed read-only SQL query against the local DuckDB vault."""
        self._state.get_project(project_id)
        duckdb_dir = self._state.targets_dir(project_id) / "duckdb_local"
        primary = duckdb_dir / f"{project_id}.duckdb"
        fallback = duckdb_dir / "demo_vault.duckdb"
        db_path = primary if primary.is_file() else fallback
        if not db_path.is_file():
            raise OpenDVError(
                f"Local DuckDB database not found for project '{project_id}'. Run migrate first.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return execute_readonly_query(db_path, req.sql, limit=req.limit)
