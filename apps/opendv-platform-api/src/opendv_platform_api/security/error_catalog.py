"""Canonical closed error catalog and RFC 9457 ProblemDetail helpers (Task 5B.4 / Issue #218).

Maps directly to schemas/v1/error-codes.json containing 119 closed error codes
across exit codes 0..7.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

_ERROR_CATALOG: dict[str, dict[str, Any]] = {}


def _find_catalog_path() -> Path | None:
    curr = Path.cwd().resolve()
    for p in [curr, curr.parent, curr.parent.parent]:
        candidate = p / "schemas" / "v1" / "error-codes.json"
        if candidate.is_file():
            return candidate
    return None


def get_error_catalog() -> dict[str, dict[str, Any]]:
    """Return dictionary of code -> error code entry from schemas/v1/error-codes.json."""
    global _ERROR_CATALOG
    if not _ERROR_CATALOG:
        catalog_path = _find_catalog_path()
        if catalog_path and catalog_path.is_file():
            try:
                data = json.loads(catalog_path.read_text(encoding="utf-8"))
                for entry in data.get("error_codes", []):
                    code = entry.get("code")
                    if code:
                        _ERROR_CATALOG[code] = entry
            except (OSError, json.JSONDecodeError):
                pass
    return _ERROR_CATALOG


def get_error_entry(code: str) -> dict[str, Any]:
    """Look up metadata for a canonical error code."""
    catalog = get_error_catalog()
    if code in catalog:
        return catalog[code]

    # Fallback heuristics if catalog file is unavailable
    slug = code.lower().replace("_", "-")
    return {
        "code": code,
        "exit_code": 1,
        "http_status": 400 if "INVALID" in code or "SYNTAX" in code else 500,
        "problem_title": code.replace("_", " ").title(),
        "problem_type_uri": f"urn:opendv:error:{slug}",
    }


class StudioAPIError(Exception):
    """Studio exception holding an authoritative error code and RFC 9457 details."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int | None = None,
        instance: str | None = None,
        invalid_params: list[dict[str, Any]] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        entry = get_error_entry(code)
        self.status_code = status_code or entry.get("http_status", 400)
        self.title = entry.get("problem_title", code.replace("_", " ").title())
        slug = code.lower().replace("_", "-")
        self.type_uri = entry.get("problem_type_uri", f"urn:opendv:error:{slug}")
        self.exit_code = entry.get("exit_code", 1)
        self.instance = instance
        self.invalid_params = invalid_params or []
        self.extensions = extensions or {}


def build_studio_problem_response(
    code: str,
    detail: str,
    *,
    status_code: int | None = None,
    instance: str | None = None,
    invalid_params: list[dict[str, Any]] | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build an RFC 9457 compliant JSONResponse for Studio errors."""
    entry = get_error_entry(code)
    resolved_status = status_code or entry.get("http_status", 400)
    resolved_title = entry.get("problem_title", code.replace("_", " ").title())
    slug = code.lower().replace("_", "-")
    resolved_type = entry.get("problem_type_uri", f"urn:opendv:error:{slug}")

    body: dict[str, Any] = {
        "type": resolved_type,
        "title": resolved_title,
        "status": resolved_status,
        "detail": detail,
        "error_code": code,
    }
    if instance:
        body["instance"] = instance
    if invalid_params:
        body["invalid_params"] = invalid_params
    if extensions:
        body["extensions"] = extensions

    return JSONResponse(
        status_code=resolved_status,
        content=body,
        media_type="application/problem+json",
    )


__all__ = [
    "StudioAPIError",
    "build_studio_problem_response",
    "get_error_catalog",
    "get_error_entry",
]
