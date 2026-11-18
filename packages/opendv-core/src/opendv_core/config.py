"""Centralized declarative configuration and thresholds for Data Vault 2.0 modeling and drift.

Conforms to ADR-020 and docs/25-data-driven-single-run-pipeline-design.md (§5.3).
Enforces zero magic literals across core modeling algorithms (Alg A.1 - Alg A.9b),
providing typed defaults with transparent environment variable overrides.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opendv_core.constants import (
    DEFAULT_DELIMITER,
    GHOST_DTS,
    GHOST_REC_SRC,
    GLOBAL_BKCC,
    NULL_TOKEN,
    SHA256_HEX_LENGTH,
    SYSTEM_BKCC,
    ZERO_KEY_ERROR,
    ZERO_KEY_ERROR_TOKEN,
    ZERO_KEY_REC_SRC,
    ZERO_KEY_UNKNOWN,
    ZERO_KEY_UNKNOWN_TOKEN,
)

# Standard environment variable prefix for core modeling overrides
ENV_PREFIX = "OPENDV_"


class CoreModelingConfig(BaseModel):
    """Declarative modeling thresholds and sentinels for Open-DV core algorithms."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    # Algorithm A.9a schema drift classification thresholds
    levenshtein_rename_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum normalized Levenshtein similarity to classify column rename "
            "without DDL mutation."
        ),
    )
    jaccard_similarity_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Minimum value sample Jaccard overlap to confirm column rename.",
    )
    companion_satellite_volatility_tier: str = Field(
        default="ADDITIVE_DRIFT_COMPANION",
        description="Volatility tier assigned to additive drift companion satellites.",
    )

    # Core modeling & extensibility thresholds (Alg A.1 - Alg A.6)
    business_key_uniqueness_threshold: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Required empirical uniqueness ratio for candidate business keys.",
    )
    sparse_fk_null_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description=(
            "Non-null threshold (U_nn) below which optional foreign keys are split per Alg A.4."
        ),
    )
    min_string_length_for_bk: int = Field(
        default=1,
        ge=1,
        description="Minimum trimmed string length for valid business key values.",
    )
    max_sandbox_rows: int = Field(
        default=10_000,
        ge=1,
        description="Default maximum rows sampled into DuckDB shadow sandbox for verification.",
    )

    # System table exclusions
    system_table_prefixes: tuple[str, ...] = Field(
        default=("sqlite_stat", "sqlite_sequence", "information_schema"),
        description="Prefixes of internal engine/catalog tables excluded from modeling.",
    )
    sqlite_internal_table_prefixes: tuple[str, ...] = Field(
        default=("sqlite_stat", "sqlite_sequence"),
        description="Internal SQLite catalog table prefixes to skip during inspection.",
    )

    # Domain-specific modeling defaults & aliases (M7)
    core_hub_entities: tuple[str, ...] = Field(
        default=("customer", "order", "invoice"),
        description="Entities adopting _core and _pii satellite naming suffixes.",
    )
    promo_campaign_column: str = Field(
        default="promo_campaign_code",
        description="Column name indicating promotion campaign reference key.",
    )
    promo_hub_name: str = Field(
        default="hub_promotion",
        description="Target hub for synthesized promotion entities.",
    )
    promo_sat_name: str = Field(
        default="sat_promotion_ref",
        description="Target satellite for synthesized promotion entities.",
    )
    promo_link_name: str = Field(
        default="lnk_order_customer_promotion",
        description="Target ternary link for order-customer-promotion relationship.",
    )
    default_customer_tax_id_column: str = Field(
        default="customer_tax_id",
        description="Default customer tax ID foreign key column name.",
    )
    canonical_link_aliases: tuple[tuple[str, str], ...] = Field(
        default=(("lnk_invoice_order_customer", "lnk_invoice_customer_order"),),
        description="Normalized canonical link name aliases.",
    )
    default_unstructured_target_hub: str = Field(
        default="hub_document",
        description=(
            "Fallback target hub when unstructured document extraction targets are unspecified."
        ),
    )
    unstructured_bronze_table: str = Field(
        default="unstructured_documents",
        description="Canonical bronze table name for unstructured document ingestion.",
    )
    unstructured_record_source: str = Field(
        default="LEGAL_DOC_VAULT",
        description="Default record source code for unstructured documents.",
    )
    unstructured_doc_key_column: str = Field(
        default="document_id",
        description="Business key column name for unstructured document hub.",
    )
    unstructured_doc_concept_uri: str = Field(
        default="urn:ontology:entity:document",
        description="Ontology concept URI for unstructured document hub.",
    )
    unstructured_provenance_sat_name: str = Field(
        default="sat_document_provenance",
        description="Target satellite for unstructured document provenance.",
    )
    unstructured_provenance_columns: tuple[str, ...] = Field(
        default=("content_sha256", "doc_mime_type", "char_length"),
        description="Payload columns for unstructured document provenance satellite.",
    )
    default_claude_reasoning_model: str = Field(
        default="claude-opus-4-6",
        description="Default Claude reasoning model ID for heavy personas.",
    )
    default_claude_fast_model: str = Field(
        default="claude-haiku-4-5",
        description="Default Claude fast model ID for utility personas.",
    )
    default_antigravity_reasoning_model: str = Field(
        default="gemini-4-argon-high",
        description="Default Antigravity/Gemini reasoning model ID for heavy personas.",
    )
    default_antigravity_fast_model: str = Field(
        default="gemini-3.8-flash-high",
        description="Default Antigravity/Gemini fast model ID for utility personas.",
    )

    # Algorithm A.5 hashing and Data Vault 2.0 sentinel defaults
    default_delimiter: str = Field(
        default=DEFAULT_DELIMITER,
        description="Canonical Stage 4 hashing delimiter between tokens.",
    )
    null_token: str = Field(
        default=NULL_TOKEN,
        description="Reserved byte-length prefixed token for NULL/empty values (-1:^^).",
    )
    sha256_hex_length: int = Field(
        default=SHA256_HEX_LENGTH,
        description="Length in characters of canonical SHA-256 hex digest (64).",
    )
    zero_key_unknown: str = Field(
        default=ZERO_KEY_UNKNOWN,
        description="Sentinel hash key for Unknown record (-1).",
    )
    zero_key_error: str = Field(
        default=ZERO_KEY_ERROR,
        description="Sentinel hash key for Error record (-2).",
    )
    zero_key_unknown_token: str = Field(
        default=ZERO_KEY_UNKNOWN_TOKEN,
        description="Business key token for Unknown record.",
    )
    zero_key_error_token: str = Field(
        default=ZERO_KEY_ERROR_TOKEN,
        description="Business key token for Error record.",
    )
    zero_key_rec_src: str = Field(
        default=ZERO_KEY_REC_SRC,
        description="Record source sentinel for system zero keys.",
    )
    ghost_dts: str = Field(
        default=GHOST_DTS,
        description="Effective date timestamp for PIT ghost records.",
    )
    ghost_rec_src: str = Field(
        default=GHOST_REC_SRC,
        description="Record source for PIT ghost records.",
    )
    global_bkcc: str = Field(
        default=GLOBAL_BKCC,
        description="Global Business Key Collision Code namespace.",
    )
    system_bkcc: str = Field(
        default=SYSTEM_BKCC,
        description="System Business Key Collision Code namespace.",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_env_overrides(cls, data: Any) -> Any:
        """Apply environment variable overrides for any unset fields."""
        if not isinstance(data, dict):
            data = {}
        merged = dict(data)
        env = os.environ

        for field_name in cls.model_fields:
            if field_name in merged and merged[field_name] is not None:
                continue

            env_key = f"{ENV_PREFIX}{field_name.upper()}"
            if env_key in env:
                raw_val = env[env_key]
                field_info = cls.model_fields[field_name]
                annotation = field_info.annotation

                if annotation is float:
                    merged[field_name] = float(raw_val)
                elif annotation is int:
                    merged[field_name] = int(raw_val)
                elif annotation == tuple[str, ...] or (
                    hasattr(annotation, "__origin__") and annotation.__origin__ is tuple
                ):
                    merged[field_name] = tuple(
                        part.strip() for part in raw_val.split(",") if part.strip()
                    )
                elif annotation is str:
                    merged[field_name] = raw_val
                else:
                    merged[field_name] = raw_val

        return merged

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CoreModelingConfig:
        """Create configuration reading overrides from a specific mapping (or os.environ)."""
        source_env = os.environ if env is None else env
        overrides: dict[str, Any] = {}

        for field_name in cls.model_fields:
            env_key = f"{ENV_PREFIX}{field_name.upper()}"
            if env_key in source_env:
                raw_val = source_env[env_key]
                field_info = cls.model_fields[field_name]
                annotation = field_info.annotation

                if annotation is float:
                    overrides[field_name] = float(raw_val)
                elif annotation is int:
                    overrides[field_name] = int(raw_val)
                elif annotation == tuple[str, ...] or (
                    hasattr(annotation, "__origin__") and annotation.__origin__ is tuple
                ):
                    overrides[field_name] = tuple(
                        part.strip() for part in raw_val.split(",") if part.strip()
                    )
                elif annotation is str:
                    overrides[field_name] = raw_val
                else:
                    overrides[field_name] = raw_val

        return cls(**overrides)


@lru_cache(maxsize=1)
def get_core_modeling_config() -> CoreModelingConfig:
    """Return cached CoreModelingConfig singleton loaded from environment."""
    return CoreModelingConfig()


__all__ = [
    "ENV_PREFIX",
    "CoreModelingConfig",
    "get_core_modeling_config",
]
