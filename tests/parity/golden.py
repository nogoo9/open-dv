"""tests/parity/golden.py — Golden Test Suite Infrastructure & GoldenManager.

Implements:
- `GoldenManager`: Core orchestrator for golden file assertions and updates.
- Four determinism pre-checks:
  1. Compiler byte-stability (consecutive identical compilations).
  2. LF line-ending normalization (cross-platform CRLF/LF invariance).
  3. Float precision serialization.
  4. Deterministic LLM boundary verification.
- Three-Way Agreement verification (`committed files ≡ MANIFEST.json ≡ docs/17 §G table`).
- Strict `--update-golden` workflow requiring non-empty `--golden-justification` (>= 10 chars).
- Automated parsing of GT-04 Normative Hash Vectors and Golden Manifest tables from `docs/17`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_agent_harness.adapters.deterministic_adapter import build_deterministic_agy_sdk_shim
from opendv_core import validate_contract_v5
from opendv_duckdb_sandbox import (
    compile_canonical_ir_to_4zone_sql,
    lower_contract_to_canonical_ir,
)


class NonDeterministicCompilerError(Exception):
    """Raised when consecutive compilations of the same contract produce distinct outputs."""


class GoldenManager:
    """Manages baseline golden files, cryptographic manifests, and docs/17 §G synchronization."""

    def __init__(
        self,
        golden_root: Path,
        docs17_path: Path,
        request: pytest.FixtureRequest | None = None,
        *,
        update_mode: bool = False,
        justification: str = "",
    ) -> None:
        self.golden_root = golden_root
        self.docs17_path = docs17_path
        self.request = request

        if request is not None:
            self.update_mode = bool(request.config.getoption("--update-golden", default=False))
            self.justification = str(
                request.config.getoption("--golden-justification", default="")
            ).strip()
        else:
            self.update_mode = update_mode
            self.justification = justification.strip()

    @staticmethod
    def normalize_line_endings_lf(data: bytes | str) -> bytes:
        """Convert arbitrary string or bytes into strict Unix LF (\\n) UTF-8 bytes."""
        raw = data.encode("utf-8") if isinstance(data, str) else data
        return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    @staticmethod
    def format_float(val: float, precision: int = 4) -> str:
        """Serialize a floating-point number with fixed decimal places to avoid IEEE 754 jitter."""
        return f"{val:.{precision}f}"

    def run_determinism_prechecks(self, reference_contract_path: Path | None = None) -> None:
        """Execute the 4 deterministic pre-checks before running golden assertions.

        1. Compiler Byte-Stability: Compiles a reference contract twice consecutively within
           the same process and asserts IR and SQL equality.
        2. Line Ending Invariance: Asserts LF normalization invariance and .gitattributes rule.
        3. Float Precision Serialization: Asserts fixed float formatting stability.
        4. Deterministic LLM Boundary: Asserts deterministic harness shim is operational.
        """
        # Pre-check 1: Compiler Byte-Stability
        contract_path = (
            reference_contract_path
            or self.golden_root.parents[1]
            / "examples"
            / "private_equity"
            / "dv_pe_model_contract.yaml"
        )
        if not contract_path.exists():
            raise FileNotFoundError(f"Reference contract not found: {contract_path}")

        contract_doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        validate_contract_v5(contract_doc)

        ir1 = lower_contract_to_canonical_ir(contract_doc)
        ir2 = lower_contract_to_canonical_ir(contract_doc)
        ir1_json = json.dumps(ir1.model_dump(mode="json", exclude_none=True), sort_keys=True)
        ir2_json = json.dumps(ir2.model_dump(mode="json", exclude_none=True), sort_keys=True)

        if ir1_json != ir2_json:
            raise NonDeterministicCompilerError(
                "Compiler non-determinism detected: Canonical IR differs between consecutive runs."
            )

        sql1 = compile_canonical_ir_to_4zone_sql(
            ir1, include_batch4_restatement_in_seed=False
        ).full_sql_bundle
        sql2 = compile_canonical_ir_to_4zone_sql(
            ir2, include_batch4_restatement_in_seed=False
        ).full_sql_bundle

        if sql1 != sql2:
            raise NonDeterministicCompilerError(
                "Compiler non-determinism detected: 4-Zone SQL differs between consecutive runs."
            )

        # Pre-check 2: Line Ending Invariance (LF Normalization)
        test_mixed = b"line1\r\nline2\rline3\n"
        norm = self.normalize_line_endings_lf(test_mixed)
        assert norm == b"line1\nline2\nline3\n", "LF normalization failed on mixed line endings"

        gitattrs_path = self.golden_root / ".gitattributes"
        assert gitattrs_path.exists(), "tests/golden/.gitattributes must exist on disk"
        gitattrs_text = gitattrs_path.read_text(encoding="utf-8")
        assert "eol=lf" in gitattrs_text, "tests/golden/.gitattributes must specify eol=lf"

        # Pre-check 3: Float Precision Serialization
        f_val = 12.800000000000001
        assert self.format_float(f_val, precision=2) == "12.80"
        assert self.format_float(f_val, precision=4) == "12.8000"

        # Pre-check 4: Deterministic LLM Boundary
        shim = build_deterministic_agy_sdk_shim()
        assert shim is not None, "Deterministic AGY SDK shim failed to instantiate"

    def scan_committed_golden_files(self) -> list[str]:
        """Return relative paths of all committed golden files.

        Excludes MANIFEST.json and dotfiles.
        """
        if not self.golden_root.exists():
            return []
        files: list[str] = []
        for path in self.golden_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.golden_root).as_posix()
            if rel == "MANIFEST.json" or rel.startswith(".") or "/." in rel:
                continue
            files.append(rel)
        return sorted(files)

    def load_manifest(self) -> dict[str, Any]:
        """Load and return the raw MANIFEST.json dictionary."""
        manifest_path = self.golden_root / "MANIFEST.json"
        if not manifest_path.exists():
            return {
                "manifest_version": "1.0.0",
                "generated_at": datetime.now(UTC).isoformat(),
                "generator_transport": "STANDALONE_CLI",
                "entries": [],
            }
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def load_manifest_json(self) -> list[dict[str, Any]]:
        """Load and return the entries list from MANIFEST.json."""
        manifest = self.load_manifest()
        return manifest.get("entries", [])

    def save_manifest(
        self,
        entries: list[dict[str, Any]],
        generator_transport: str = "STANDALONE_CLI",
    ) -> None:
        """Write formatted entries to MANIFEST.json adhering to the normative schema."""
        self.golden_root.mkdir(parents=True, exist_ok=True)
        manifest_path = self.golden_root / "MANIFEST.json"

        # Sort entries deterministically by golden_id then file
        sorted_entries = sorted(entries, key=lambda e: (e["golden_id"], e["file"]))
        doc = {
            "manifest_version": "1.0.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "generator_transport": generator_transport,
            "entries": sorted_entries,
        }
        manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    def parse_docs17_manifest_table(self) -> list[dict[str, str]]:
        """Parse Section G.3 Golden Manifest Table from docs/17 into structured entry dicts."""
        if not self.docs17_path.exists():
            raise FileNotFoundError(f"docs/17 file not found: {self.docs17_path}")

        text = self.docs17_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        in_table = False
        entries: list[dict[str, str]] = []

        for line in lines:
            line_str = line.strip()
            if "### G.3 Golden Manifest Table" in line_str:
                in_table = True
                continue
            if in_table and line_str.startswith("### "):
                break
            if in_table and line_str.startswith("|"):
                # Header or separator or data row
                if ":---" in line_str or "Golden ID" in line_str:
                    continue
                parts = [p.strip().strip("`") for p in line_str.split("|")[1:-1]]
                if len(parts) >= 5:
                    entries.append(
                        {
                            "golden_id": parts[0],
                            "file": parts[1],
                            "sha256": parts[2].lower(),
                            "generated_from": parts[3],
                            "last_justified_change": parts[4],
                        }
                    )
        return entries

    def parse_docs17_gt04_vectors(self) -> list[dict[str, Any]]:
        """Parse Section G.2 Consolidated GT-04 Normative Hash Vectors Table from docs/17."""
        if not self.docs17_path.exists():
            raise FileNotFoundError(f"docs/17 file not found: {self.docs17_path}")

        text = self.docs17_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        in_table = False
        vectors: list[dict[str, Any]] = []

        for line in lines:
            line_str = line.strip()
            if "### G.2 Consolidated GT-04 Normative Hash Vectors Table" in line_str:
                in_table = True
                continue
            if in_table and line_str.startswith("### "):
                break
            if in_table and line_str.startswith("|"):
                if ":---" in line_str or "Vector ID" in line_str:
                    continue
                raw_parts = re.split(r"(?<!\\)\|", line_str)
                parts = [p.strip().replace(r"\|", "|") for p in raw_parts[1:-1]]
                if len(parts) >= 8:
                    vector_id = parts[0].strip("`")
                    source_issue = parts[1]
                    category = parts[2]
                    tokens_raw = parts[3].strip("`")

                    # Parse python literal list
                    try:
                        tokens = ast.literal_eval(tokens_raw)
                    except Exception:
                        tokens = [tokens_raw]

                    is_bk_raw = parts[4].strip("`").lower()
                    is_business_key = is_bk_raw in ("true", "1", "yes")

                    bkcc_raw = parts[5].strip("`").strip("'")
                    bkcc_prefix = None if bkcc_raw in ("None", "") else bkcc_raw

                    prehash_str = parts[6].strip("`").strip("'")
                    expected_sha = parts[7].strip("`").lower()

                    vectors.append(
                        {
                            "vector_id": vector_id,
                            "source_issue": source_issue,
                            "category": category,
                            "tokens": tokens,
                            "is_business_key": is_business_key,
                            "bkcc_prefix": bkcc_prefix,
                            "canonical_prehash": prehash_str,
                            "expected_sha256": expected_sha,
                        }
                    )
        return vectors

    def _update_docs17_manifest_table(self, entries: list[dict[str, Any]]) -> None:
        """Update or rewrite the Section G.3 Golden Manifest Table in docs/17."""
        if not self.docs17_path.exists():
            return

        text = self.docs17_path.read_text(encoding="utf-8")
        table_pattern = re.compile(
            r"(### G\.3 Golden Manifest Table\n\n)"
            r"\| Golden ID \| File \| SHA-256 \| Generated From \| Last Justified Change \|\n"
            r"\| :--- \| :--- \| :--- \| :--- \| :--- \|\n"
            r"(?:\|[^\n]+\n)*",
            re.MULTILINE,
        )

        rows = [
            "### G.3 Golden Manifest Table\n\n",
            "| Golden ID | File | SHA-256 | Generated From | Last Justified Change |\n",
            "| :--- | :--- | :--- | :--- | :--- |\n",
        ]
        sorted_entries = sorted(entries, key=lambda e: (e["golden_id"], e["file"]))
        for e in sorted_entries:
            rows.append(
                f"| `{e['golden_id']}` | `{e['file']}` | `{e['sha256']}` | "
                f"`{e['generated_from']}` | `{e['last_justified_change']}` |\n"
            )
        new_table_str = "".join(rows)

        if table_pattern.search(text):
            updated_text = table_pattern.sub(new_table_str, text, count=1)
        else:
            updated_text = text.rstrip() + "\n\n" + new_table_str

        self.docs17_path.write_text(updated_text, encoding="utf-8")

    def _update_manifest_and_docs17(
        self,
        golden_id: str,
        file_rel: str,
        sha256: str,
        byte_size: int,
        generated_from: str,
        justification: str,
    ) -> None:
        """Internal helper to atomically update MANIFEST.json and docs/17 table."""
        entries = self.load_manifest_json()
        new_entry = {
            "golden_id": golden_id,
            "file": file_rel,
            "sha256": sha256,
            "byte_size": byte_size,
            "generated_from": generated_from,
            "last_justified_change": justification,
        }

        # Replace existing or append
        updated = False
        for idx, entry in enumerate(entries):
            if entry["file"] == file_rel:
                entries[idx] = new_entry
                updated = True
                break
        if not updated:
            entries.append(new_entry)

        self.save_manifest(entries)
        self._update_docs17_manifest_table(entries)

    def assert_golden_byte_equality(
        self,
        golden_id: str,
        relative_file: str,
        actual_bytes: bytes | str,
        generated_from: str,
    ) -> None:
        """Assert actual bytes match the committed golden file, or update under --update-golden."""
        target_path = self.golden_root / relative_file
        normalized_actual = self.normalize_line_endings_lf(actual_bytes)
        actual_sha256 = hashlib.sha256(normalized_actual).hexdigest().lower()

        if self.update_mode:
            if not self.justification or len(self.justification) < 10:
                raise ValueError(
                    f"Refusing to update golden '{relative_file}': --update-golden requires a "
                    f"non-empty --golden-justification citing issue/PR number (at least 10 chars, "
                    f"e.g. '[#68] P1 baseline golden capture')."
                )
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(normalized_actual)
            self._update_manifest_and_docs17(
                golden_id=golden_id,
                file_rel=relative_file,
                sha256=actual_sha256,
                byte_size=len(normalized_actual),
                generated_from=generated_from,
                justification=self.justification,
            )
            return

        if not target_path.exists():
            raise FileNotFoundError(
                f"Golden file '{target_path}' does not exist. Run with --update-golden "
                f"--golden-justification=\"[#ISSUE] reason\" to generate."
            )

        committed_bytes = self.normalize_line_endings_lf(target_path.read_bytes())
        committed_sha256 = hashlib.sha256(committed_bytes).hexdigest().lower()

        if normalized_actual != committed_bytes:
            raise AssertionError(
                f"Golden divergence detected for '{relative_file}':\n"
                f"  Actual SHA-256:    {actual_sha256}\n"
                f"  Committed SHA-256: {committed_sha256}\n"
                f"If this behavioral change is intended and approved, regenerate with:\n"
                f"  uv run pytest -m golden --update-golden --golden-justification=\"[#ISSUE] ...\""
            )

    def verify_three_way_agreement(self) -> None:
        """Verify committed files == MANIFEST.json == docs/17 §G table bit-for-bit."""
        manifest_entries = self.load_manifest_json()
        docs17_entries = self.parse_docs17_manifest_table()
        committed_files = self.scan_committed_golden_files()

        # Check 1: File set equality
        manifest_files = {e["file"] for e in manifest_entries}
        docs17_files = {e["file"] for e in docs17_entries}
        committed_set = set(committed_files)

        assert manifest_files == committed_set, (
            f"MANIFEST.json file set mismatch:\n"
            f"  In MANIFEST but not committed: {manifest_files - committed_set}\n"
            f"  Committed but not in MANIFEST: {committed_set - manifest_files}"
        )
        assert docs17_files == committed_set, (
            f"docs/17 §G table file set mismatch:\n"
            f"  In docs/17 but not committed: {docs17_files - committed_set}\n"
            f"  Committed but not in docs/17: {committed_set - docs17_files}"
        )

        # Check 2: Hash bit-for-bit equality and justification consistency
        for entry in manifest_entries:
            file_rel = entry["file"]
            file_path = self.golden_root / file_rel
            computed_sha = hashlib.sha256(
                self.normalize_line_endings_lf(file_path.read_bytes())
            ).hexdigest().lower()
            assert computed_sha == entry["sha256"], (
                f"MANIFEST sha256 mismatch for '{file_rel}': "
                f"computed {computed_sha} != manifest {entry['sha256']}"
            )
            assert len(file_path.read_bytes()) == entry["byte_size"], (
                f"MANIFEST byte_size mismatch for '{file_rel}'"
            )

        for entry in docs17_entries:
            file_rel = entry["file"]
            matching_manifest = next(m for m in manifest_entries if m["file"] == file_rel)
            assert entry["sha256"] == matching_manifest["sha256"], (
                f"docs/17 sha256 mismatch for '{file_rel}': "
                f"docs/17 {entry['sha256']} != manifest {matching_manifest['sha256']}"
            )
            assert entry["last_justified_change"] == matching_manifest["last_justified_change"], (
                f"docs/17 justification mismatch for '{file_rel}'"
            )
