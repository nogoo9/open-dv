"""Pydantic v2 models for the Open-DV Data Vault 2.0 Canonical Intermediate Representation (IR).

Strictly mirrors `schemas/dv2-canonical-ir.schema.json` and validates
`examples/private_equity/pe_canonical_ir.json`.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SAT_REF_RE = re.compile(r"^sat_ref_[a-z0-9_]+$")
HUB_HK_RE = re.compile(r"^hk_[a-z0-9_]+$")
LINK_HK_RE = re.compile(r"^hk_(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")


class StrictIRModel(BaseModel):
    """Base model forbidding undeclared properties (`additionalProperties: false`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class IRHashConfig(StrictIRModel):
    """Cryptographic hash algorithm and delimiter configuration in the Canonical IR."""

    algorithm: Literal["SHA256", "MD5"] = "SHA256"
    storage_type: Literal["BINARY(32)", "CHAR(64)", "BINARY(16)", "CHAR(32)"] = "CHAR(64)"
    delimiter: str = Field(default="||", min_length=1)
    null_token_bk: str = Field(default="-1", min_length=1)
    null_token_payload: str = Field(default="-1", min_length=1)
    zero_key_unknown_token: Literal["-1"] = "-1"
    zero_key_error_token: Literal["-2"] = "-2"
    encoding_scheme: Literal["DELIMITER_ESCAPED", "LENGTH_PREFIXED"] | None = "LENGTH_PREFIXED"


class IRSystemColumns(StrictIRModel):
    """Standard DV 2.0 system audit and change-tracking column names and SQL types."""

    load_dts_col: Literal["load_dts"] = "load_dts"
    load_dts_type: Literal[
        "TIMESTAMP_NTZ(9)",
        "TIMESTAMP_NTZ(6)",
        "TIMESTAMPTZ",
        "TIMESTAMP",
    ] = "TIMESTAMP"
    rec_src_col: Literal["rec_src"] = "rec_src"
    rec_src_type: Literal["VARCHAR(255)", "VARCHAR(128)", "TEXT"] = "VARCHAR(255)"
    hash_diff_col: Literal["hash_diff"] = "hash_diff"
    applied_dts_col: str | None = None


class IRHashExpression(StrictIRModel):
    """Deterministic hash expression specification for `_hk` or `hash_diff`."""

    algorithm: str
    input_columns: list[str] = Field(min_length=1)
    normalization: Literal["UPPER_TRIM", "EXACT_TRIM"]


class IRHashColumn(StrictIRModel):
    """Hash key (`hk_*`) or `hash_diff` column specification."""

    name: str
    data_type: str
    nullable: bool
    hash_expression: IRHashExpression


class IRColumnDef(StrictIRModel):
    """Physical column definition in a Canonical IR table."""

    name: str
    data_type: str
    nullable: bool


class IRBusinessKeyColumn(StrictIRModel):
    """Business key column specification on a Canonical IR Hub."""

    name: str
    data_type: str
    nullable: bool
    is_bkcc: bool | None = None
    is_tenant_id: bool | None = None


class IRHubSourceMapping(StrictIRModel):
    """Source staging table mapping for a Canonical IR Hub."""

    staging_table: str
    bk_source_columns: list[str]
    rec_src_value: str


class IRHub(StrictIRModel):
    """Hub entity definition in the Canonical IR."""

    name: str = Field(pattern=r"^(b?hub)_[a-z0-9_]+$")
    business_concept: str | None = None
    verified_global_master_key: bool | None = None
    hk_column: IRHashColumn
    business_keys: list[IRBusinessKeyColumn] = Field(min_length=1)
    load_dts_column: IRColumnDef
    rec_src_column: IRColumnDef
    extra_columns: list[dict[str, Any]] | None = None
    source_mappings: list[IRHubSourceMapping] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_hub_hk_name(self) -> IRHub:
        if not HUB_HK_RE.match(self.hk_column.name):
            msg = f"Hub hk_column name '{self.hk_column.name}' must match ^hk_[a-z0-9_]+$"
            raise ValueError(msg)
        return self


class IRLinkHubReference(StrictIRModel):
    """Foreign key reference from a Canonical IR Link to a target Hub."""

    fk_column_name: str
    target_hub: str
    target_hk_column: str
    data_type: str
    nullable: bool
    role_name: str


class IRSourceMapping(StrictIRModel):
    """Source staging table mapping for a Link or Satellite."""

    staging_table: str
    rec_src_value: str


class IRLink(StrictIRModel):
    """Link entity definition in the Canonical IR."""

    name: str = Field(pattern=r"^(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$")
    link_type: Literal[
        "STANDARD",
        "HIERARCHICAL",
        "SAME_AS",
        "NON_HISTORIZED",
        "EXPLORATION",
    ]
    semantic_role: str | None = None
    max_recursion_depth: int | None = Field(default=None, ge=1, le=100)
    cycle_prevention_predicate: bool | None = None
    hk_column: IRHashColumn
    hub_references: list[IRLinkHubReference] = Field(min_length=2)
    degenerate_columns: list[IRColumnDef] | None = None
    immutable_measures: list[IRColumnDef] | None = None
    load_dts_column: IRColumnDef
    rec_src_column: IRColumnDef
    source_mappings: list[dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_link_hk_and_inputs(self) -> IRLink:
        if not LINK_HK_RE.match(self.hk_column.name):
            msg = (
                f"Link hk_column name '{self.hk_column.name}' must match "
                r"^hk_(lnk|t_lnk|nh_lnk|sal|hal|x_lnk)_[a-z0-9_]+$"
            )
            raise ValueError(msg)
        if len(self.hk_column.hash_expression.input_columns) < 2:
            msg = f"Link '{self.name}' hash_expression.input_columns must have at least 2 items"
            raise ValueError(msg)
        return self


class IRParentHkColumn(StrictIRModel):
    """Parent Hub/Link hash key foreign key column on a Satellite."""

    name: str
    target_column: str
    data_type: str
    nullable: bool


class IRSatellite(StrictIRModel):
    """Satellite entity definition in the Canonical IR."""

    name: str = Field(pattern=r"^(sat|bsat|mas|ma_sat|esat|sts|rts)_[a-z0-9_]+$")
    satellite_type: Literal[
        "STANDARD",
        "BUSINESS",
        "MULTI_ACTIVE",
        "EFFECTIVITY",
        "STATUS_TRACKING",
        "RECORD_TRACKING",
    ]
    split_classification: (
        Literal["CORE", "HIGH_VELOCITY", "STATIC", "PII_RESTRICTED", "DERIVED_BV"] | None
    ) = None
    parent_entity: str
    additional_parents: list[str] | None = None
    parent_hk_column: IRParentHkColumn
    child_dependent_keys: list[IRColumnDef] | None = None
    driving_keys: list[str] | None = None
    load_dts_column: IRColumnDef
    applied_dts_column: IRColumnDef | None = None
    rec_src_column: IRColumnDef
    hash_diff_column: IRHashColumn
    hash_diff_migration_policy: (
        Literal["SPLIT_NEW_SATELLITE", "NULL_EQUIVALENT_PADDING", "VERSIONED_CUTOVER"] | None
    ) = None
    payload_columns: list[IRColumnDef] = Field(min_length=1)
    source_mapping: IRSourceMapping


class IRPitTable(StrictIRModel):
    """Point-in-Time (PIT) helper table definition in the Canonical IR."""

    name: str = Field(pattern=r"^pit_[a-z0-9_]+$")
    parent_entity: str
    as_of_dts_column: Literal["as_of_dts"] = "as_of_dts"
    snapshot_window_policy: Literal["DAILY", "LOGARITHMIC", "CHANGE_DRIVEN"] | None = None
    tracked_satellites: list[str] = Field(min_length=1)


class IRBridgeTable(StrictIRModel):
    """Bridge helper table definition in the Canonical IR."""

    name: str = Field(pattern=r"^brg_[a-z0-9_]+$")
    as_of_dts_column: str | None = None
    traversed_hubs: list[str] = Field(min_length=2)
    traversed_links: list[str] = Field(min_length=1)


class IRReferenceTable(StrictIRModel):
    """Reference table (`ref_*`) definition in the Canonical IR."""

    name: str = Field(pattern=r"^ref_[a-z0-9_]+$")
    natural_code_column: str
    satellites: list[str]

    @model_validator(mode="after")
    def _validate_ref_satellites(self) -> IRReferenceTable:
        for sat_name in self.satellites:
            if not SAT_REF_RE.match(sat_name):
                msg = f"Reference table satellite '{sat_name}' must match ^sat_ref_[a-z0-9_]+$"
                raise ValueError(msg)
        return self


class CanonicalIR(StrictIRModel):
    """Root Canonical Intermediate Representation model (`schemas/dv2-canonical-ir.schema.json`)."""

    ir_version: Literal["1.0.0"] = "1.0.0"
    model_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    hash_config: IRHashConfig
    system_columns: IRSystemColumns
    hubs: list[IRHub]
    links: list[IRLink]
    satellites: list[IRSatellite]
    pit_tables: list[IRPitTable] | None = None
    bridge_tables: list[IRBridgeTable] | None = None
    reference_tables: list[IRReferenceTable] | None = None


DV2CanonicalIR = CanonicalIR

__all__ = [
    "CanonicalIR",
    "DV2CanonicalIR",
    "IRBridgeTable",
    "IRBusinessKeyColumn",
    "IRColumnDef",
    "IRHashColumn",
    "IRHashConfig",
    "IRHashExpression",
    "IRHub",
    "IRHubSourceMapping",
    "IRLink",
    "IRLinkHubReference",
    "IRParentHkColumn",
    "IRPitTable",
    "IRReferenceTable",
    "IRSatellite",
    "IRSourceMapping",
    "IRSystemColumns",
]
