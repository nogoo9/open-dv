"""Unit tests for SourceSpec and FormatSpec data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_sources.models.specs import FormatSpec, SourceSpec


def test_source_spec_minimal() -> None:
    """Verify minimal valid SourceSpec instantiates with defaults."""
    spec = SourceSpec(adapter="file")
    assert spec.adapter == "file"
    assert spec.uri is None
    assert spec.format is None
    assert spec.options == {}
    assert spec.credentials_ref is None
    assert spec.inline_content is None
    assert spec.client_path is None


def test_source_spec_with_valid_credentials_ref() -> None:
    """Verify valid credentials_ref in env or secret format is accepted."""
    spec_env = SourceSpec(adapter="s3", credentials_ref="env:AWS_SECRET_KEY")
    assert spec_env.credentials_ref == "env:AWS_SECRET_KEY"

    spec_secret = SourceSpec(adapter="vault", credentials_ref="secret:database_creds")
    assert spec_secret.credentials_ref == "secret:database_creds"


@pytest.mark.parametrize(
    "invalid_ref",
    [
        "plain_password",
        "env:",
        "secret:",
        "password123",
        "env:VAR; rm -rf /",
        "http://secret",
    ],
)
def test_source_spec_rejects_invalid_credentials_ref(invalid_ref: str) -> None:
    """Verify invalid credentials_ref syntax raises validation error."""
    with pytest.raises(ValidationError) as exc_info:
        SourceSpec(adapter="db", credentials_ref=invalid_ref)
    assert "credentials_ref must use 'env:NAME' or 'secret:NAME'" in str(exc_info.value)


@pytest.mark.parametrize(
    "banned_key",
    [
        "password",
        "db_password",
        "secret",
        "api_key",
        "token",
        "auth_token",
        "access_key",
        "private_key",
    ],
)
def test_source_spec_rejects_literal_secret_options(banned_key: str) -> None:
    """Verify literal credential keys in options are strictly rejected (REQ-CFG-01)."""
    with pytest.raises(ValidationError) as exc_info:
        SourceSpec(adapter="db", options={banned_key: "plain_secret_value"})
    assert "literal credentials in options are forbidden" in str(exc_info.value)


def test_source_spec_inline_content_and_client_path() -> None:
    """Verify inline content support for Decision D1 zero-disk operation."""
    spec = SourceSpec(
        adapter="inline",
        format="csv",
        inline_content="id,name\n1,Alpha\n2,Beta",
        client_path="client_upload.csv",
    )
    assert spec.inline_content == "id,name\n1,Alpha\n2,Beta"
    assert spec.client_path == "client_upload.csv"


def test_format_spec_valid() -> None:
    """Verify FormatSpec fields and options handling."""
    fmt = FormatSpec(
        format="csv",
        options={"delimiter": ",", "header": True},
        schema_hint={"id": "int64", "name": "string"},
    )
    assert fmt.format == "csv"
    assert fmt.options["delimiter"] == ","
    assert fmt.options["header"] is True
    assert fmt.schema_hint == {"id": "int64", "name": "string"}
