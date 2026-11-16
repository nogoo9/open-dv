"""Ontology Studio VaultBlueprint export helper (WS4.1).

Exports a Studio session and version as a standardized VaultBlueprintDTO
bundling semantic ontology and physical Data Vault 2.0 contract with
cryptographic RFC 8785 JCS SHA-256 lineage tracking.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import yaml

from opendv_contracts.dtos.blueprint import BlueprintExportResponseDTO, VaultBlueprintDTO
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import VaultBlueprint, create_vault_blueprint
from opendv_service.studio.session_manager import (
    SessionNotFoundError,
    StudioSessionManager,
    normalize_version_tag,
    validate_session_id,
)

_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^v?\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def export_vault_blueprint(
    session_id: str,
    version_tag: str | None = None,
    session_manager: Any | None = None,
    contract: Any | None = None,
) -> VaultBlueprintDTO:
    """Export the active or specified version of a Studio session as a VaultBlueprintDTO."""
    sid = validate_session_id(session_id)
    mgr = session_manager or StudioSessionManager()
    session_meta = mgr.get_session(sid)

    raw_version = (version_tag or session_meta.head_version or "v1.0.0").strip()
    if not _VERSION_PATTERN.match(raw_version):
        raise SessionNotFoundError(
            f"Invalid version format '{raw_version}' for Studio session '{sid}'."
        )
    norm_tag = normalize_version_tag(raw_version)

    session_dir = mgr.get_session_dir(sid)
    versions_root = session_dir / "versions"
    versions_root_res = versions_root.resolve()

    vdir = (versions_root / norm_tag).resolve()
    if not vdir.is_relative_to(versions_root_res):
        raise SessionNotFoundError(
            f"Path traversal detected in version tag '{raw_version}' for Studio session '{sid}'."
        )

    if not vdir.is_dir():
        cand_alt = (versions_root / raw_version).resolve()
        if cand_alt.is_relative_to(versions_root_res) and cand_alt.is_dir():
            vdir = cand_alt
        else:
            cand_stripped = (versions_root / f"v{raw_version.lstrip('v')}").resolve()
            if cand_stripped.is_relative_to(versions_root_res) and cand_stripped.is_dir():
                vdir = cand_stripped
            else:
                raise SessionNotFoundError(
                    f"Version '{norm_tag}' not found for Studio session '{sid}'."
                )

    # 1. Load ontology
    ont_json = vdir / "ontology.json"
    ont_yaml = vdir / "ontology.yaml"
    if ont_json.is_file():
        ont_raw = json.loads(ont_json.read_text(encoding="utf-8"))
    elif ont_yaml.is_file():
        ont_raw = yaml.safe_load(ont_yaml.read_text(encoding="utf-8"))
    else:
        raise FileNotFoundError(
            f"No ontology.json or ontology.yaml found in version directory '{vdir}'."
        )

    ontology_model = (
        ont_raw if isinstance(ont_raw, GraphAOntology) else GraphAOntology.model_validate(ont_raw)
    )

    # 2. Check for existing contract
    contract_model: NoMistakeDataVault2ContractV5 | None = None
    if contract is not None:
        contract_model = (
            contract
            if isinstance(contract, NoMistakeDataVault2ContractV5)
            else NoMistakeDataVault2ContractV5.model_validate(contract)
        )
    else:
        contract_candidates = [
            vdir / "contract.json",
            vdir / "contract.yaml",
            session_dir / "contract.json",
            session_dir / "contract.yaml",
            session_dir / "inputs" / "contract.json",
            session_dir / "inputs" / "contract.yaml",
        ]
        for cand in contract_candidates:
            if cand.is_file():
                try:
                    c_text = cand.read_text(encoding="utf-8")
                    c_dict = (
                        json.loads(c_text) if cand.suffix == ".json" else yaml.safe_load(c_text)
                    )
                    if isinstance(c_dict, dict) and (
                        "tables" in c_dict or "model_name" in c_dict or "schema_version" in c_dict
                    ):
                        contract_model = NoMistakeDataVault2ContractV5.model_validate(c_dict)
                        break
                except (ValueError, TypeError, KeyError, OSError, yaml.YAMLError):
                    continue

    # 3. Create VaultBlueprint with automatic RFC 8785 JCS lineage tracking
    domain_ns = ontology_model.domain_namespace or session_meta.domain_namespace or "general"
    blueprint: VaultBlueprint = create_vault_blueprint(
        domain_namespace=domain_ns,
        ontology=ontology_model,
        contract=contract_model,
        lineage_notes=f"Exported from Studio session '{sid}' version '{norm_tag}'",
    )

    # 4. Convert to transport DTO
    blueprint_dict = blueprint.model_dump(mode="json")
    clean_d = {k: v for k, v in blueprint_dict.items() if k != "blueprint_version"}
    clean_d["blueprint_version"] = "1.0.0"
    clean_d.pop("schema_version", None)
    return VaultBlueprintDTO.model_validate(clean_d)


def export_vault_blueprint_response(
    session_id: str,
    version_tag: str | None = None,
    session_manager: Any | None = None,
    contract: Any | None = None,
) -> BlueprintExportResponseDTO:
    """Export Studio session blueprint wrapped in BlueprintExportResponseDTO with DDL preview."""
    blueprint_dto = export_vault_blueprint(
        session_id=session_id,
        version_tag=version_tag,
        session_manager=session_manager,
        contract=contract,
    )

    target_tables: list[dict[str, Any]] = []
    if blueprint_dto.contract and isinstance(blueprint_dto.contract, dict):
        c = blueprint_dto.contract
        for h in c.get("hubs", []):
            h_dict = h if isinstance(h, dict) else h.model_dump(mode="json")
            target_tables.append({
                "table_name": h_dict.get("hub_name", ""),
                "table_type": "HUB",
                **h_dict,
            })
        for lnk in c.get("links", []):
            lnk_dict = lnk if isinstance(lnk, dict) else lnk.model_dump(mode="json")
            target_tables.append({
                "table_name": lnk_dict.get("link_name", ""),
                "table_type": "LINK",
                **lnk_dict,
            })
        for s in c.get("satellites", []):
            s_dict = s if isinstance(s, dict) else s.model_dump(mode="json")
            target_tables.append({
                "table_name": s_dict.get("satellite_name", ""),
                "table_type": "SATELLITE",
                **s_dict,
            })
        for r in c.get("reference_tables", []):
            r_dict = r if isinstance(r, dict) else r.model_dump(mode="json")
            target_tables.append({
                "table_name": r_dict.get("reference_name", ""),
                "table_type": "REFERENCE",
                **r_dict,
            })

        raw_tables = c.get("tables", {})
        if isinstance(raw_tables, dict):
            for t_name, t_meta in raw_tables.items():
                if isinstance(t_meta, dict):
                    target_tables.append({"table_name": t_name, **t_meta})
                else:
                    target_tables.append({"table_name": t_name})

    now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_vault_ddl = generate_raw_vault_ddl_preview(blueprint_dto, target_tables)
    return BlueprintExportResponseDTO(
        blueprint=blueprint_dto,
        raw_vault_ddl=raw_vault_ddl,
        target_tables=target_tables,
        exported_at_utc=now_utc,
    )


def generate_raw_vault_ddl_preview(
    blueprint_dto: VaultBlueprintDTO,
    target_tables: list[dict[str, Any]],
) -> str:
    """Generate real Zone 2 Raw Vault DDL preview statements."""
    ddl_statements: list[str] = [
        "-- =============================================================================",
        f"-- ZONE 2 RAW VAULT DDL PREVIEW: {blueprint_dto.domain_namespace.upper()}",
        "-- =============================================================================",
        "CREATE SCHEMA IF NOT EXISTS raw_vault;",
    ]

    # 1. Lower contract to Canonical IR for full canonical DDL if valid contract exists
    if blueprint_dto.contract and isinstance(blueprint_dto.contract, dict):
        try:
            from opendv_duckdb_sandbox.compiler_4zone.ddl import (
                _compile_generic_hub_ddl_and_zero_keys,
                _compile_generic_link_ddl_and_zero_keys,
                _compile_generic_reference_ddl_and_seed,
                _compile_generic_satellite_ddl_and_ghost,
            )
            from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir

            ir_obj = lower_contract_to_canonical_ir(blueprint_dto.contract, validate_schema=False)
            for h in ir_obj.hubs:
                ddl_statements.append(_compile_generic_hub_ddl_and_zero_keys(h))
            for lnk in ir_obj.links:
                ddl_statements.append(_compile_generic_link_ddl_and_zero_keys(lnk))
            for s in ir_obj.satellites:
                ddl_statements.append(_compile_generic_satellite_ddl_and_ghost(s))
            for r in ir_obj.reference_tables or []:
                ddl_statements.append(_compile_generic_reference_ddl_and_seed(r))
            return "\n\n".join(ddl_statements)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass

    # 2. If canonical IR lowerer wasn't applicable, generate from target_tables
    if target_tables:
        for tbl in target_tables:
            t_name = tbl.get("table_name", "")
            t_type = tbl.get("table_type", "")
            if not t_name:
                continue
            clean_name = t_name.strip()
            if t_type == "HUB" or clean_name.startswith("hub_"):
                slug = clean_name.removeprefix("hub_")
                bks = tbl.get("business_keys", []) or tbl.get("business_key_sources", [])
                bk_cols = []
                if isinstance(bks, list):
                    for b in bks:
                        if isinstance(b, dict):
                            cols = b.get("columns", [])
                            bk_cols.extend(cols if isinstance(cols, list) else [cols])
                        elif isinstance(b, str):
                            bk_cols.append(b)
                if not bk_cols:
                    bk_cols = [f"{slug}_id"]
                bk_defs = "\n".join(f"    {col} VARCHAR NOT NULL," for col in bk_cols)
                ddl_statements.append(
                    f"CREATE TABLE IF NOT EXISTS raw_vault.{clean_name} (\n"
                    f"    hk_{slug} CHAR(64) NOT NULL PRIMARY KEY,\n"
                    f"{bk_defs}\n"
                    f"    load_dts TIMESTAMP NOT NULL,\n"
                    f"    rec_src VARCHAR NOT NULL\n"
                    f");"
                )
            elif (
                t_type == "LINK"
                or clean_name.startswith("lnk_")
                or clean_name.startswith("link_")
            ):
                slug = clean_name.removeprefix("lnk_").removeprefix("link_")
                refs = tbl.get("hub_references", []) or []
                ref_cols = []
                if isinstance(refs, list):
                    for r in refs:
                        if isinstance(r, dict):
                            ref_cols.append(
                                r.get("fk_column_name", f"hk_{r.get('hub_name', 'ref')}")
                            )
                        elif isinstance(r, str):
                            ref_cols.append(f"hk_{r}")
                if not ref_cols:
                    ref_cols = [f"hk_{slug}_a", f"hk_{slug}_b"]
                fk_defs = "\n".join(f"    {col} CHAR(64) NOT NULL," for col in ref_cols)
                ddl_statements.append(
                    f"CREATE TABLE IF NOT EXISTS raw_vault.{clean_name} (\n"
                    f"    hk_{slug} CHAR(64) NOT NULL PRIMARY KEY,\n"
                    f"{fk_defs}\n"
                    f"    load_dts TIMESTAMP NOT NULL,\n"
                    f"    rec_src VARCHAR NOT NULL\n"
                    f");"
                )
            elif t_type == "SATELLITE" or clean_name.startswith("sat_"):
                slug = clean_name.removeprefix("sat_")
                parent = tbl.get("parent_entity", f"hub_{slug}")
                parent_slug = parent.removeprefix("hub_")
                parent_hk = f"hk_{parent_slug}"
                payload_cols = tbl.get("payload_columns", []) or []
                p_defs = ""
                if payload_cols:
                    p_defs = "\n".join(
                        f"    {c} VARCHAR," for c in payload_cols if isinstance(c, str)
                    ) + "\n"
                ddl_statements.append(
                    f"CREATE TABLE IF NOT EXISTS raw_vault.{clean_name} (\n"
                    f"    {parent_hk} CHAR(64) NOT NULL,\n"
                    f"    load_dts TIMESTAMP NOT NULL,\n"
                    f"    rec_src VARCHAR NOT NULL,\n"
                    f"    hash_diff CHAR(64) NOT NULL,\n"
                    f"{p_defs}"
                    f"    PRIMARY KEY ({parent_hk}, load_dts)\n"
                    f");"
                )
            else:
                ddl_statements.append(
                    f"CREATE TABLE IF NOT EXISTS raw_vault.{clean_name} (\n"
                    f"    code VARCHAR NOT NULL PRIMARY KEY,\n"
                    f"    load_dts TIMESTAMP NOT NULL,\n"
                    f"    rec_src VARCHAR NOT NULL\n"
                    f");"
                )
        return "\n\n".join(ddl_statements)

    # 3. Fallback to ontology concepts if target_tables is empty
    ont = blueprint_dto.ontology or {}
    concepts = ont.get("canonical_business_concepts") or ont.get("concepts") or []
    if isinstance(concepts, list) and concepts:
        for c in concepts:
            c_name = (
                c.get("concept_name") or c.get("name")
                if isinstance(c, dict)
                else getattr(c, "concept_name", getattr(c, "name", str(c)))
            )
            slug = re.sub(r"[^a-z0-9_]+", "_", str(c_name).lower()).strip("_")
            if not slug:
                continue
            ddl_statements.append(
                f"CREATE TABLE IF NOT EXISTS raw_vault.hub_{slug} (\n"
                f"    hk_{slug} CHAR(64) NOT NULL PRIMARY KEY,\n"
                f"    {slug}_id VARCHAR NOT NULL,\n"
                f"    load_dts TIMESTAMP NOT NULL,\n"
                f"    rec_src VARCHAR NOT NULL\n"
                f");\n\n"
                f"CREATE TABLE IF NOT EXISTS raw_vault.sat_{slug} (\n"
                f"    hk_{slug} CHAR(64) NOT NULL,\n"
                f"    load_dts TIMESTAMP NOT NULL,\n"
                f"    rec_src VARCHAR NOT NULL,\n"
                f"    hash_diff CHAR(64) NOT NULL,\n"
                f"    {slug}_name VARCHAR,\n"
                f"    PRIMARY KEY (hk_{slug}, load_dts)\n"
                f");"
            )

    return "\n\n".join(ddl_statements)


__all__ = [
    "export_vault_blueprint",
    "export_vault_blueprint_response",
]
