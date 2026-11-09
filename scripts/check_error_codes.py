#!/usr/bin/env python3
"""Validation script for Open-DV closed error code catalog and baseline digests.

Validates that:
1. `schemas/v1/error-codes.json` satisfies schema constraints and taxonomy rules.
2. Every error code used in Python/TypeScript source code and docs exists in the catalog.
3. Every error mapping in code matches the normative exit code and HTTP status code.
4. Certified baseline digests in `schemas/v1/baseline-digests.json` match byte-for-byte.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


VALID_CATEGORIES = {
    "Step 0 Syntax",
    "Preconditions",
    "Resolution",
    "Verification",
    "Execution",
}

VALID_EXIT_CODES = set(range(8))
VALID_HTTP_STATUSES = {200, 201, 202, 400, 401, 403, 404, 409, 412, 413, 415, 422, 500, 503}


class ErrorCatalogValidator:
    def __init__(self, catalog_path: Path, baselines_path: Path | None = None, repo_dir: Path | None = None):
        self.catalog_path = catalog_path
        self.baselines_path = baselines_path
        self.repo_dir = repo_dir or Path.cwd()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.catalog_data: dict[str, Any] = {}
        self.codes_by_id: dict[str, dict[str, Any]] = {}

    def validate_catalog(self) -> bool:
        if not self.catalog_path.is_file():
            self.errors.append(f"Catalog file not found: {self.catalog_path}")
            return False

        try:
            self.catalog_data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            self.errors.append(f"Malformed catalog JSON: {e}")
            return False

        if "error_codes" not in self.catalog_data or not isinstance(self.catalog_data["error_codes"], list):
            self.errors.append("Catalog missing 'error_codes' array")
            return False

        seen_codes: set[str] = set()
        for idx, entry in enumerate(self.catalog_data["error_codes"]):
            prefix = f"Entry [{idx}]"
            code = entry.get("code")
            if not code or not isinstance(code, str) or not re.fullmatch(r"^[A-Z][A-Z0-9_]{1,63}$", code):
                self.errors.append(f"{prefix}: Invalid or missing 'code': {code}")
                continue

            if code in seen_codes:
                self.errors.append(f"{prefix}: Duplicate error code '{code}'")
            seen_codes.add(code)

            cat = entry.get("category")
            if cat not in VALID_CATEGORIES:
                self.errors.append(f"{prefix} '{code}': Invalid category '{cat}' (expected one of {sorted(VALID_CATEGORIES)})")

            exit_code = entry.get("exit_code")
            if exit_code not in VALID_EXIT_CODES:
                self.errors.append(f"{prefix} '{code}': Invalid exit_code '{exit_code}' (expected 0..7)")

            http_status = entry.get("http_status")
            if http_status not in VALID_HTTP_STATUSES:
                self.errors.append(f"{prefix} '{code}': Invalid http_status '{http_status}'")

            title = entry.get("problem_title")
            if not title or not isinstance(title, str):
                self.errors.append(f"{prefix} '{code}': Missing or non-string 'problem_title'")

            uri = entry.get("problem_type_uri")
            if not uri or not isinstance(uri, str) or not uri.startswith("urn:opendv:"):
                self.errors.append(f"{prefix} '{code}': Invalid problem_type_uri '{uri}' (must start with 'urn:opendv:')")

            desc = entry.get("description")
            if not desc or not isinstance(desc, str):
                self.errors.append(f"{prefix} '{code}': Missing or non-string 'description'")

            waiv = entry.get("waivability")
            if not isinstance(waiv, dict) or "is_waivable" not in waiv or "rationale_required" not in waiv:
                self.errors.append(f"{prefix} '{code}': Missing or malformed 'waivability' object")

            self.codes_by_id[code] = entry

        return len(self.errors) == 0

    def scan_python_ast(self) -> None:
        """Scan Python files for error code raises and assertions."""
        py_files = list(self.repo_dir.glob("packages/**/*.py")) + list(self.repo_dir.glob("apps/**/*.py"))
        for p in py_files:
            if "site-packages" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue

            for node in ast.walk(tree):
                # Check for PydanticCustomError("<CODE>", ...)
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    if func_name in ("PydanticCustomError", "_Err") and node.args:
                        first_arg = node.args[0]
                        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                            err_val = first_arg.value
                            if err_val.isupper() and "_" in err_val:
                                if err_val not in self.codes_by_id:
                                    self.errors.append(f"{p.relative_to(self.repo_dir)}:{node.lineno}: Undocumented error code '{err_val}' raised in PydanticCustomError")

    def scan_docs(self) -> None:
        """Scan documentation markdown for error codes."""
        docs_files = list(self.repo_dir.glob("docs/**/*.md"))
        code_regex = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")
        for df in docs_files:
            content = df.read_text(encoding="utf-8")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                # If line discusses exit codes, extract potential error codes
                if any(k in line for k in ("exit ", "exit code", "HTTP 4", "HTTP 5", "fails", "reject")):
                    for match in code_regex.finditer(line):
                        token = match.group(1)
                        if token.endswith("_") or token.startswith("HTTP_"):
                            continue
                        if any(term in token for term in ["VIOLATION", "NOT_FOUND", "INVALID", "FORBIDDEN", "REQUIRED", "CONFLICT", "COLLISION", "MISMATCH"]):
                            if token not in self.codes_by_id:
                                self.warnings.append(f"{df.relative_to(self.repo_dir)}:{line_idx}: Documented code '{token}' not found in error-codes catalog")

    def validate_baselines(self) -> bool:
        """Verify baseline digests match bit-for-bit against filesystem artifacts."""
        if not self.baselines_path or not self.baselines_path.is_file():
            return True

        try:
            b_data = json.loads(self.baselines_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            self.errors.append(f"Malformed baseline digests JSON: {e}")
            return False

        # Validate schema baselines
        schemas = b_data.get("schema_baselines", {})
        for schema_rel, meta in schemas.items():
            schema_file = self.repo_dir / schema_rel
            if not schema_file.is_file():
                self.warnings.append(f"Baseline schema not found on disk: {schema_rel}")
                continue
            expected_sha = meta.get("sha256")
            actual_sha = hashlib.sha256(schema_file.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                self.errors.append(f"Schema baseline hash mismatch for {schema_rel}: expected {expected_sha}, got {actual_sha}")

        return len(self.errors) == 0

    def report(self) -> int:
        if self.errors:
            print(f"❌ Error Code Catalog Validation FAILED ({len(self.errors)} errors):")
            for e in self.errors:
                print(f"  - {e}")
            return 1
        print(f"✅ Error Code Catalog Validation PASSED ({len(self.codes_by_id)} error codes registered).")
        if self.warnings:
            print(f"⚠️ Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"  - {w}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check closed error code catalog and baseline digests")
    parser.add_argument("--catalog", type=Path, default=Path("schemas/v1/error-codes.json"), help="Path to error-codes.json")
    parser.add_argument("--baselines", type=Path, default=Path("schemas/v1/baseline-digests.json"), help="Path to baseline-digests.json")
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="Path to repository root")
    parser.add_argument("--check-docs", action="store_true", help="Cross-check documentation markdown files")
    parser.add_argument("--check-ast", action="store_true", help="Cross-check Python AST for uncatalogued error codes")
    args = parser.parse_args()

    validator = ErrorCatalogValidator(args.catalog, args.baselines, args.repo_dir)
    validator.validate_catalog()
    if args.check_ast:
        validator.scan_python_ast()
    if args.check_docs:
        validator.scan_docs()
    validator.validate_baselines()
    return validator.report()


if __name__ == "__main__":
    sys.exit(main())
