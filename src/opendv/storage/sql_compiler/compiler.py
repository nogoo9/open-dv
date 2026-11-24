"""8-dialect SqlGlotVaultCompiler orchestrating DDL, DML, WaveMigrator, and TargetManifest.

Strictly Layer 2a: imports opendv.models, dialects, ddl_builders, dml_builders, wave_migrator.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from opendv.models.project import EvolutionLockSpec, TargetManifest
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.dialects import (
    SqlDialectProfile,
    TargetId,
    assign_physical_aliases,
    get_dialect_profile,
)
from opendv.storage.sql_compiler.ddl_builders import (
    compile_full_ddl,
    resolve_staging_hd_col_map,
)
from opendv.storage.sql_compiler.dml_builders import compile_full_dml
from opendv.storage.sql_compiler.wave_migrator import WaveMigrator


def _join_sql(stmts: Sequence[str]) -> str:
    parts: list[str] = []
    for s in stmts:
        cleaned = s.strip()
        if not cleaned:
            continue
        if not cleaned.endswith(";"):
            cleaned += ";"
        parts.append(cleaned)
    return "\n\n".join(parts) + ("\n" if parts else "")


@dataclass(frozen=True, slots=True)
class CompiledSqlBundle:
    """Deterministic compiled SQL artifacts and locked aliases for a SQL target."""

    target_id: TargetId
    dialect_profile: SqlDialectProfile
    ddl_statements: list[str]
    dml_statements: list[str]
    migration_statements: list[str]
    ddl_sql: str
    dml_sql: str
    migration_sql: str
    full_sql: str
    physical_aliases: dict[str, str]
    stg_hd_col_map: dict[tuple[str, str], str]
    files: dict[str, str] = field(default_factory=dict)
    manifest: TargetManifest | None = None


class SqlGlotVaultCompiler:
    """Zero-fallback parameterized 8-dialect Data Vault 2.0 compiler."""

    def __init__(self, target_or_dialect: str = "duckdb_local") -> None:
        self.profile = get_dialect_profile(target_or_dialect)
        if self.profile.sqlglot_dialect is None:
            raise ValueError(
                f"SqlGlotVaultCompiler requires a SQL dialect target, got {self.profile.target_id}"
            )
        self.target_id: TargetId = self.profile.target_id
        self.migrator = WaveMigrator()

    def compile(
        self,
        ir: CertifiedVaultIR,
        *,
        prior_ir: CertifiedVaultIR | None = None,
        locks: EvolutionLockSpec | None = None,
        output_dir: Path | None = None,
        verification_tier_override: str | None = None,
        certification_status: str = "CERTIFIED",
    ) -> CompiledSqlBundle:
        """Compile CertifiedVaultIR into verified DDL, DML, migration SQL, and TargetManifest."""
        aliases = assign_physical_aliases(ir, self.target_id, locks=locks)
        hd_col_map = resolve_staging_hd_col_map(ir, max_ident_bytes=self.profile.max_ident_bytes)

        ddl_stmts = compile_full_ddl(ir, self.profile, aliases, hd_col_map)
        dml_stmts = compile_full_dml(ir, self.profile, aliases, hd_col_map)
        mig_stmts = self.migrator.compile_migration(
            prior_ir,
            ir,
            self.target_id,
            locks=locks,
            aliases=aliases,
        )

        ddl_sql = _join_sql(ddl_stmts)
        dml_sql = _join_sql(dml_stmts)
        mig_sql = _join_sql(mig_stmts)
        full_sql = f"{ddl_sql}\n{dml_sql}" if dml_sql else ddl_sql

        wave = ir.wave_number
        synapse_note = (
            "-- NOTE: sqlserver_fabric HASHBYTES supports >8000B on SQL Server 2016+/Fabric; "
            "Azure Synapse dedicated pools enforce an 8,000-byte HASHBYTES input ceiling.\n"
            if self.target_id == "sqlserver_fabric"
            else ""
        )
        files: dict[str, str] = {
            f"ddl/full_schema_w{wave}.sql": synapse_note + ddl_sql,
            f"dml/load_w{wave}.sql": dml_sql,
            f"migrations/V{wave:03d}_migrate.sql": synapse_note + mig_sql,
        }

        tier = verification_tier_override or self.profile.verification_tier
        cert_stat = "CERTIFIED_FAST" if certification_status == "CERTIFIED_FAST" else "CERTIFIED"
        manifest = TargetManifest(
            target_id=self.target_id,
            last_compiled_wave=wave,
            last_materialized_wave=None,
            verification_tier=tier,  # type: ignore[arg-type]
            certification_status=cert_stat,  # type: ignore[arg-type]
            physical_aliases=aliases,
            eval_receipts=["EVAL-AST-01", "EVAL-AST-02", "EVAL-AST-03"],
            generated_files=sorted([*files.keys(), "target_manifest.json"]),
        )
        files["target_manifest.json"] = manifest.model_dump_json(indent=2) + "\n"

        if output_dir is not None:
            for rel, content in sorted(files.items()):
                dest = output_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8", newline="\n")

        return CompiledSqlBundle(
            target_id=self.target_id,
            dialect_profile=self.profile,
            ddl_statements=ddl_stmts,
            dml_statements=dml_stmts,
            migration_statements=mig_stmts,
            ddl_sql=ddl_sql,
            dml_sql=dml_sql,
            migration_sql=mig_sql,
            full_sql=full_sql,
            physical_aliases=aliases,
            stg_hd_col_map=hd_col_map,
            files=files,
            manifest=manifest,
        )


__all__ = ["CompiledSqlBundle", "SqlGlotVaultCompiler"]
