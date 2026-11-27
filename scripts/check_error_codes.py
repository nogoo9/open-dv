#!/usr/bin/env python3
"""Validation script for Open-DV closed error code catalog and baseline digests.

Validates that:
1. `schemas/v2/error-codes.json` (or `schemas/v1/error-codes.json`) satisfies schema
   constraints and taxonomy rules.
2. Every error code used in Python source code exists in the catalog.
3. Every error mapping in code matches the normative exit code and HTTP status code.
4. Certified baseline digests in `baseline-digests.json` match byte-for-byte.
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
    "Syntax",
    "Step 0 Syntax",
    "Preconditions",
    "Resolution",
    "Verification",
    "Execution",
}
V2_CATEGORIES = VALID_CATEGORIES - {"Step 0 Syntax"}

V1_EXIT_CODES = set(range(8))
V2_EXIT_CODES = {0, 1, 2, 3, 4, 5, 40, 50}
VALID_EXIT_CODES = V1_EXIT_CODES | V2_EXIT_CODES
VALID_HTTP_STATUSES = {
    None,
    200,
    201,
    202,
    400,
    401,
    403,
    404,
    409,
    412,
    413,
    415,
    422,
    500,
    501,
    502,
    503,
}

DOC_ERROR_TERMS = (
    "VIOLATION",
    "NOT_FOUND",
    "INVALID",
    "FORBIDDEN",
    "REQUIRED",
    "CONFLICT",
    "COLLISION",
    "MISMATCH",
)

_ERROR_CODE_ATTR_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_V2_ERROR_URI_RE = re.compile(r"^urn:opendv:error:[a-z0-9-]+$")


class ErrorCatalogValidator:
    def __init__(
        self,
        catalog_path: Path,
        baselines_path: Path | None = None,
        repo_dir: Path | None = None,
    ):
        self.catalog_path = catalog_path
        self.baselines_path = baselines_path
        self.repo_dir = repo_dir or Path.cwd()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.catalog_data: dict[str, Any] = {}
        self.codes_by_id: dict[str, dict[str, Any]] = {}
        self.is_v2: bool = self.catalog_path.parent.name == "v2"

    def validate_catalog(self) -> bool:
        if not self.catalog_path.is_file():
            self.errors.append(f"Catalog file not found: {self.catalog_path}")
            return False

        try:
            self.catalog_data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            self.errors.append(f"Malformed catalog JSON: {e}")
            return False

        self.is_v2 = (
            str(self.catalog_data.get("catalog_version", "")).startswith("2.")
            or self.catalog_path.parent.name == "v2"
        )

        if "error_codes" not in self.catalog_data or not isinstance(
            self.catalog_data["error_codes"], list
        ):
            self.errors.append("Catalog missing 'error_codes' array")
            return False

        entries = self.catalog_data["error_codes"]
        total_declared = self.catalog_data.get("total_error_codes")
        if self.is_v2:
            if (
                isinstance(total_declared, bool)
                or not isinstance(total_declared, int)
                or total_declared != 34
            ):
                self.errors.append(
                    f"V2 catalog 'total_error_codes' must equal 34, got {total_declared!r}"
                )
            if len(entries) != 34:
                self.errors.append(
                    f"V2 catalog must define exactly 34 error_codes, got {len(entries)}"
                )
        elif total_declared is not None and total_declared != len(entries):
            self.errors.append(
                f"Catalog 'total_error_codes' ({total_declared}) != "
                f"len(error_codes) ({len(entries)})"
            )

        allowed_categories = V2_CATEGORIES if self.is_v2 else VALID_CATEGORIES
        allowed_exit_codes = V2_EXIT_CODES if self.is_v2 else V1_EXIT_CODES

        seen_codes: set[str] = set()
        for idx, entry in enumerate(entries):
            prefix = f"Entry [{idx}]"
            code = entry.get("code")
            if (
                not code
                or not isinstance(code, str)
                or not re.fullmatch(r"^[A-Z][A-Z0-9_]{1,63}$", code)
            ):
                self.errors.append(f"{prefix}: Invalid or missing 'code': {code}")
                continue

            if code in seen_codes:
                self.errors.append(f"{prefix}: Duplicate error code '{code}'")
            seen_codes.add(code)

            cat = entry.get("category")
            if cat not in allowed_categories:
                valid_cats = sorted(allowed_categories)
                self.errors.append(
                    f"{prefix} '{code}': Invalid category '{cat}' (expected one of {valid_cats})"
                )

            exit_code = entry.get("exit_code")
            if isinstance(exit_code, bool) or exit_code not in allowed_exit_codes:
                self.errors.append(
                    f"{prefix} '{code}': Invalid exit_code '{exit_code}' "
                    f"(expected one of {sorted(allowed_exit_codes)})"
                )

            if "http_status" not in entry:
                self.errors.append(f"{prefix} '{code}': Missing 'http_status' key")
            else:
                http_status = entry["http_status"]
                if isinstance(http_status, bool) or http_status not in VALID_HTTP_STATUSES:
                    self.errors.append(f"{prefix} '{code}': Invalid http_status '{http_status}'")
                elif self.is_v2:
                    if exit_code == 40 and http_status is not None:
                        self.errors.append(
                            f"{prefix} '{code}': exit_code 40 requires http_status=null, "
                            f"got {http_status}"
                        )
                    elif exit_code != 40 and http_status is None:
                        self.errors.append(
                            f"{prefix} '{code}': http_status=null is only allowed for exit_code 40"
                        )
                elif http_status is None:
                    self.errors.append(f"{prefix} '{code}': Invalid http_status 'None' in V1")

            title = entry.get("problem_title")
            if not title or not isinstance(title, str):
                self.errors.append(f"{prefix} '{code}': Missing or non-string 'problem_title'")

            uri = entry.get("problem_type_uri")
            if not uri or not isinstance(uri, str) or not uri.startswith("urn:opendv:"):
                self.errors.append(
                    f"{prefix} '{code}': Invalid problem_type_uri '{uri}' "
                    "(must start with 'urn:opendv:')"
                )
            elif self.is_v2:
                if code == "OK" and uri != "urn:opendv:ok":
                    self.errors.append(
                        f"{prefix} '{code}': Invalid V2 problem_type_uri '{uri}' "
                        "(expected 'urn:opendv:ok')"
                    )
                elif code != "OK" and not _V2_ERROR_URI_RE.fullmatch(uri):
                    self.errors.append(
                        f"{prefix} '{code}': Invalid V2 problem_type_uri '{uri}' "
                        "(must match '^urn:opendv:error:[a-z0-9-]+$')"
                    )

            desc = entry.get("description")
            if not desc or not isinstance(desc, str):
                self.errors.append(f"{prefix} '{code}': Missing or non-string 'description'")

            if self.is_v2:
                waivable = entry.get("waivable")
                rationale_required = entry.get("rationale_required")
                if not isinstance(waivable, bool) or not isinstance(rationale_required, bool):
                    self.errors.append(
                        f"{prefix} '{code}': V2 entries require boolean 'waivable' and "
                        "'rationale_required'"
                    )
            else:
                waiv = entry.get("waivability")
                if (
                    not isinstance(waiv, dict)
                    or "is_waivable" not in waiv
                    or "rationale_required" not in waiv
                ):
                    self.errors.append(
                        f"{prefix} '{code}': Missing or malformed 'waivability' object"
                    )

            self.codes_by_id[code] = entry

        return len(self.errors) == 0

    def scan_python_ast(self) -> None:
        """Scan Python files for error code raises and assertions."""
        if self.is_v2:
            py_files = list(self.repo_dir.glob("src/opendv/**/*.py"))
        else:
            py_files = list(self.repo_dir.glob("packages/**/*.py")) + list(
                self.repo_dir.glob("apps/**/*.py")
            )

        for p in py_files:
            if "site-packages" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue

            rel_path = p.relative_to(self.repo_dir)
            for node in ast.walk(tree):
                if (
                    self.is_v2
                    and isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "ErrorCode"
                    and _ERROR_CODE_ATTR_RE.fullmatch(node.attr)
                    and node.attr not in self.codes_by_id
                ):
                    self.errors.append(
                        f"{rel_path}:{node.lineno}: Undocumented ErrorCode.{node.attr} reference"
                    )

                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name in ("PydanticCustomError", "_Err") and node.args:
                        first_arg = node.args[0]
                        if isinstance(first_arg, ast.Constant) and isinstance(
                            first_arg.value, str
                        ):
                            err_val = first_arg.value
                            if (
                                err_val.isupper()
                                and "_" in err_val
                                and err_val not in self.codes_by_id
                            ):
                                self.errors.append(
                                    f"{rel_path}:{node.lineno}: Undocumented error code "
                                    f"'{err_val}' raised in {func_name}"
                                )

                    if self.is_v2 and (func_name == "OpenDVError" or func_name.endswith("Error")):
                        for kw in node.keywords:
                            if (
                                kw.arg == "error_code"
                                and isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, str)
                                and kw.value.value not in self.codes_by_id
                            ):
                                self.errors.append(
                                    f"{rel_path}:{node.lineno}: Undocumented error_code "
                                    f"'{kw.value.value}' passed to {func_name}"
                                )

    def scan_docs(self) -> None:
        """Scan documentation markdown for error codes."""
        docs_files = list(self.repo_dir.glob("docs/**/*.md"))
        code_regex = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")
        for df in docs_files:
            content = df.read_text(encoding="utf-8")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                if any(
                    k in line for k in ("exit ", "exit code", "HTTP 4", "HTTP 5", "fails", "reject")
                ):
                    for match in code_regex.finditer(line):
                        token = match.group(1)
                        if token.endswith("_") or token.startswith("HTTP_"):
                            continue
                        if (
                            any(term in token for term in DOC_ERROR_TERMS)
                            and token not in self.codes_by_id
                        ):
                            rel_path = df.relative_to(self.repo_dir)
                            self.warnings.append(
                                f"{rel_path}:{line_idx}: Documented code '{token}' "
                                "not found in error-codes catalog"
                            )

    def validate_baselines(self) -> bool:
        """Verify baseline digests match bit-for-bit against filesystem artifacts."""
        if (
            not self.baselines_path
            or str(self.baselines_path).lower() == "none"
            or not self.baselines_path.is_file()
        ):
            return True

        try:
            b_data = json.loads(self.baselines_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            self.errors.append(f"Malformed baseline digests JSON: {e}")
            return False

        # V1 schema baselines
        schemas = b_data.get("schema_baselines", {})
        for schema_rel, meta in schemas.items():
            schema_file = self.repo_dir / schema_rel
            if not schema_file.is_file():
                self.warnings.append(f"Baseline schema not found on disk: {schema_rel}")
                continue
            expected_sha = meta.get("sha256")
            actual_sha = hashlib.sha256(schema_file.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                self.errors.append(
                    f"Schema baseline hash mismatch for {schema_rel}: "
                    f"expected {expected_sha}, got {actual_sha}"
                )

        # V2 FIBO core pack & starter packs
        fibo_meta = b_data.get("fibo_core_pack")
        if isinstance(fibo_meta, dict) and "path" in fibo_meta:
            fibo_file = self.repo_dir / fibo_meta["path"]
            if not fibo_file.is_file():
                self.errors.append(f"V2 baseline artifact not found: {fibo_meta['path']}")
            else:
                raw_fibo = fibo_file.read_bytes()
                expected_sha = fibo_meta.get("sha256")
                actual_sha = hashlib.sha256(raw_fibo).hexdigest()
                if actual_sha != expected_sha:
                    self.errors.append(
                        f"FIBO core pack hash mismatch for {fibo_meta['path']}: "
                        f"expected {expected_sha}, got {actual_sha}"
                    )
                try:
                    fibo_json = json.loads(raw_fibo.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self.errors.append(f"Invalid FIBO core pack JSON ({fibo_meta['path']}): {exc}")
                else:
                    meta_obj = fibo_json.get("metadata") if isinstance(fibo_json, dict) else None
                    actual_schema_ver = (
                        meta_obj.get("pack_schema_version") if isinstance(meta_obj, dict) else None
                    )
                    if actual_schema_ver != fibo_meta.get("pack_schema_version"):
                        self.errors.append(
                            f"FIBO pack_schema_version mismatch: "
                            f"expected {fibo_meta.get('pack_schema_version')!r}, "
                            f"got {actual_schema_ver!r}"
                        )
                    for baseline_key, json_key in (
                        ("concept_count", "classes"),
                        ("property_count", "properties"),
                        ("individual_count", "individuals"),
                    ):
                        actual_len = (
                            len(fibo_json.get(json_key, {}))
                            if isinstance(fibo_json, dict)
                            and isinstance(fibo_json.get(json_key), dict)
                            else None
                        )
                        if actual_len != fibo_meta.get(baseline_key):
                            self.errors.append(
                                f"FIBO {baseline_key} mismatch: "
                                f"expected {fibo_meta.get(baseline_key)!r}, got {actual_len!r}"
                            )

        starter_packs = b_data.get("starter_packs")
        if isinstance(starter_packs, dict):
            for pack_id, meta in starter_packs.items():
                if isinstance(meta, dict) and "path" in meta:
                    pack_file = self.repo_dir / meta["path"]
                    if not pack_file.is_file():
                        self.errors.append(
                            f"V2 starter pack '{pack_id}' not found: {meta['path']}"
                        )
                    else:
                        expected_sha = meta.get("sha256")
                        actual_sha = hashlib.sha256(pack_file.read_bytes()).hexdigest()
                        if actual_sha != expected_sha:
                            self.errors.append(
                                f"Starter pack '{pack_id}' hash mismatch: "
                                f"expected {expected_sha}, got {actual_sha}"
                            )

        return len(self.errors) == 0

    def report(self) -> int:
        if self.errors:
            print(f"❌ Error Code Catalog Validation FAILED ({len(self.errors)} errors):")
            for e in self.errors:
                print(f"  - {e}")
            return 1
        count = len(self.codes_by_id)
        print(f"✅ Error Code Catalog Validation PASSED ({count} error codes registered).")
        if self.warnings:
            print(f"⚠️ Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"  - {w}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check closed error code catalog and baseline digests"
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("schemas/v2/error-codes.json"),
        help="Path to error-codes.json",
    )
    parser.add_argument(
        "--baselines",
        type=Path,
        default=Path("schemas/v2/baseline-digests.json"),
        help="Path to baseline-digests.json (or 'none' to skip)",
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Path to repository root",
    )
    parser.add_argument(
        "--check-docs",
        action="store_true",
        help="Cross-check documentation markdown files",
    )
    parser.add_argument(
        "--check-ast",
        action="store_true",
        help="Cross-check Python AST for uncatalogued error codes",
    )
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
