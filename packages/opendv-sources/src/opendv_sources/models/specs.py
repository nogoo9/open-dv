"""Declarative specifications for data sources and format options."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from opendv_contracts.dtos.common import BaseContractModel

_CREDENTIAL_REF_REGEX = re.compile(r"^(env|secret):[A-Za-z0-9_]+$")
_DISALLOWED_OPTION_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "auth",
    "access_key",
    "private_key",
}


class SourceSpec(BaseContractModel):
    """Declarative specification for an input data source."""

    adapter: str = Field(
        ...,
        description="Adapter plugin identifier (e.g. 'file', 'inline', 'duckdb_attach')",
    )
    uri: str | None = Field(
        default=None,
        description="Workspace-relative URI, file path, or connection string",
    )
    format: str | None = Field(
        default=None,
        description="Explicit format override (e.g. 'csv', 'parquet', 'jsonl')",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter- and format-specific configuration options",
    )
    credentials_ref: str | None = Field(
        default=None,
        description="Reference to credentials in 'env:VAR' or 'secret:NAME' form",
    )
    inline_content: str | bytes | None = Field(
        default=None,
        description="Inline content for API uploads (Decision D1)",
    )
    client_path: str | None = Field(
        default=None,
        description="Client display path for remote inline uploads",
    )

    @field_validator("credentials_ref")
    @classmethod
    def _validate_credentials_ref(cls, v: str | None) -> str | None:
        if v is not None and not _CREDENTIAL_REF_REGEX.match(v.strip()):
            raise ValueError(
                f"credentials_ref must use 'env:NAME' or 'secret:NAME' format, got '{v}'. "
                "Literal passwords/secrets are strictly forbidden (REQ-CFG-01)."
            )
        return v.strip() if v else None

    @field_validator("options")
    @classmethod
    def _validate_options_no_secrets(cls, v: dict[str, Any]) -> dict[str, Any]:
        for k in v:
            lower_k = k.lower()
            if any(banned in lower_k for banned in _DISALLOWED_OPTION_KEYS):
                raise ValueError(
                    f"Option '{k}' rejected: literal credentials in options are forbidden. "
                    "Use credentials_ref instead."
                )
            if lower_k == "allowed_roots":
                raise ValueError(
                    f"Option '{k}' rejected: client override of allowed_roots is forbidden. "
                    "Configure allowed roots on the server adapter instead."
                )
        return v


class FormatSpec(BaseContractModel):
    """Specification for data format parsing options."""

    format: str = Field(
        ...,
        description="Format identifier (e.g. 'csv', 'parquet', 'json', 'pdf')",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Format parser options (e.g. delimiter, header)",
    )
    schema_hint: dict[str, Any] | None = Field(
        default=None,
        description="Optional expected schema column hint",
    )


__all__ = ["FormatSpec", "SourceSpec"]
