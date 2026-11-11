# Context Notes for Claude Opus 5.5 Critique - Track 6E: Wave 3 Integration & Release

## Review Scope & Partitioning
Track 6E executes the final Wave 3 integration, monorepo CI validation, issue tracker closeout, and non-auto-merging release pull request to `main` (Tasks 6E.1–6E.3, Issues #237–#239).

Due to the cumulative monorepo diff (>112,000 lines across Waves 1–3), Track 6E is reviewed via targeted path analysis covering the integration glue, taxonomy contracts, test gates, and audit scripts:
- `packages/opendv-contracts/src/opendv_contracts/errors.py`
- `packages/opendv-contracts/src/opendv_contracts/__init__.py`
- `packages/opendv-contracts/tests/test_cli_spec.py`
- `packages/opendv-service/src/opendv_service/cli_spec/generator.py`
- `tests/test_p2_stress.py`
- `tests/parity/golden.py`
- `tests/parity/test_golden.py`
- `tests/golden/ontology_studio/test_golden_evaluation.py`
- `scripts/audit_issue_tracker.py`

## Verification Status & Quality Gates
All automated test suites and linters pass 100% cleanly across all packages and apps:
1. **Contracts & CLI Spec Tests**:
   - `pytest packages/opendv-contracts/tests/test_cli_spec.py`: **7 passed in 1.05s** (including `test_error_codes_normative_catalog_conformance` and `test_cli_spec_freshness_against_generator`)
   - `pytest tests/test_p2_stress.py`: **23 passed in 19.67s**
2. **Golden Parity & Benchmark Integrity**:
   - `pytest tests/parity/test_golden.py tests/golden/ontology_studio/test_golden_evaluation.py`: **21 passed in 56.93s**
   - `test_ontology_studio_golden_integrity`: Bit-for-bit SHA-256 match for `wm_graph_a_ontology.yaml` (`f2c1dd...`) and `golden_cqs.json` (`89d2d4...`).
   - `compute_ontology_sha256(PE Graph A)`: **Exact bit-for-bit match `1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81`** verified via `tests/test_challenger_m4_empirical.py`.
3. **TypeScript & CLI Suites**:
   - `packages/opendv-protocol-ts`: 26 passed, `tsc -b` 0 errors.
   - `apps/opendvctl`: 90 passed in 204ms (including `studio-cli.test.ts` testing exit codes 1, 2, 3, 4, 5, 6, 7).
   - `apps/opendv-web-workbench`: 274 passed in 5.74s, `tsc -b` 0 errors.
4. **Linter**:
   - `uv run ruff check packages/*/src apps/*/src`: **All checks passed (0 errors)**.
5. **Issue Tracker Audit**:
   - `scripts/audit_issue_tracker.py`: Verified 47/47 issues compliant across #193–#239.

## Key Architectural & Invariant Implementations

### 1. Direct Catalog Verification & Bidirectional Parity (MAJ-01 Remediation)
- In addition to internal consistency checks, `test_error_codes_normative_catalog_conformance` in `packages/opendv-contracts/tests/test_cli_spec.py` directly loads `schemas/v1/error-codes.json` and asserts:
  - Every single one of the 119 codes in `error-codes.json` exists in `ErrorCode` and `ERROR_MAPPINGS` with exact matching `exit_code` and `problem_title`.
  - Non-legacy codes have bit-for-bit identical `problem_type_uri` (`urn:opendv:error:<slug>`) and `http_status`.
  - The only `ErrorCode` members not in `error-codes.json` are the documented 20 Wave 1 legacy codes (e.g. `API_UNREACHABLE`, `PROVIDER_UNAVAILABLE`, `VERIFICATION_FAILED`).
  - Total `ErrorCode` count is 139 (119 catalog + 20 legacy = 139).
  - Every `problem_type_uri` across `ERROR_MAPPINGS` is strictly unique (zero collision).
- `generator.py` maps `ERROR_MAPPINGS.values()` directly as the single source of truth, eliminating ad-hoc fallback logic.
- Both `test_cli_spec.py` and `test_p2_stress.py` import `VALID_EXIT_CODES: frozenset[int] = frozenset(range(8))` from `opendv_contracts`, preventing constant duplication.

### 2. Contract Evolution & Specification Freshness (MAJ-02 Remediation)
- The CLI specification evolves additively from Wave 1 into Wave 3. All commands and error mappings are synchronized across:
  - `packages/opendv-contracts/schemas/cli-spec.json`
  - `schemas/cli-spec.json` (canonical runtime root)
  - `schemas/v1/cli-spec.json` (forward studio schema)
- `test_cli_spec_freshness_against_generator` strictly asserts byte-for-byte identity of all three schema files against `generate_cli_spec()`.
- The `ErrorCode` docstring clarifies the relationship between Wave 1 core codes (which retain normative (2, 503) mappings per original Phase 2 CLI specs) and forward Studio capabilities (which use granular exit codes 1..7).
- Dual-CLI parity: `apps/opendvctl` consumes `cli-spec.json` and all 90 TS tests pass with exit codes 0..7 cleanly exercised.

### 3. Golden Orphan Detection & Configurable Prefix Filtering (MAJ-03 & MIN-04 Remediation)
- `GoldenManager.__init__` adds `include_prefixes: tuple[str, ...] = ("GT-",)`.
- `scan_committed_golden_files()` uses `path.parts` to safely exclude `__pycache__` and filters by `self.include_prefixes`.
- `verify_three_way_agreement()` enforces strict orphan detection: any file under `golden_root` not starting with `include_prefixes` is verified against an explicit registered allowlist (`allowed_non_prefixed`), failing immediately on any unexpected golden orphan file.
- Dedicated unit test `test_golden_manager_include_prefixes_filtering` in `tests/parity/test_golden.py` covers default GT-prefix scanning, `__pycache__` exclusion, and empty-tuple mode.
- Committed ontology studio benchmarks (`wm_graph_a_ontology.yaml` and `golden_cqs.json`) have dedicated cryptographic integrity verification in `test_ontology_studio_golden_integrity()`.

### 4. Non-Auto-Merge Release PR Constraint
- The release PR from `dev/ontology-studio` to `main` will strictly NOT pass `--auto`.
- `gh pr view --repo nogoo9/open-dv --json autoMergeRequest` will be verified to return `null`.
