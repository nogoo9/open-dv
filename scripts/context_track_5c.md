# Track 5C Task 5C.1: Standalone `opendv` Studio Subcommands Review Scope

## Review Scope & Partitioning Strategy
Per the critique script's normative fail-loud boundary (`MAX_DIFF_CHARS: 120_000`) and the official recommendation to "Decompose changes into smaller targeted reviews using `--paths`":
Track 5C changes are reviewed in targeted partitions:
1. Partition 1 (Apps/Opendvctl Remote Executor & Tests):
   `apps/opendvctl/src/remote-executor.ts` and `apps/opendvctl/tests/studio-cli.test.ts`
   -> Critique Verdict: PASS (0 CRITICAL, 0 MAJOR).
2. Partition 2 (Dual-CLI Bit-for-Bit Parity Harness):
   `tests/parity/test_ontology_studio_cli_parity.py`
   -> Critique Verdict: PASS (0 CRITICAL, 0 MAJOR).
3. Partition 3 (LocalExecutor Implementation & CLI Wiring - THIS REVIEW):
   Target paths in diff:
   - `apps/opendv-cli/src/opendv_cli/commands/ontology_studio.py` (21 leaf commands, in-process LocalExecutor)
   - `apps/opendv-cli/src/opendv_cli/executor.py` (LocalExecutor dispatch wiring)
   - `apps/opendv-cli/src/opendv_cli/main.py` (CLI tree subparser registration)
   Companion test suite:
   - `apps/opendv-cli/tests/test_studio_local_executor.py` (13 integration tests verifying all 21 commands, OCC conflicts, low-confidence waivers, historical contract downgrade gates, and genesis delta validation). All 13 tests pass with 100% pass rate under `pytest`.

## Architectural Boundaries and Invariants in this Partition:
1. AST Isolation: Zero imports from `opendv_platform_api` or web frameworks (`fastapi`, `starlette`, `httpx`). Direct in-process binding via `LocalExecutor` directly to `opendv-service`.
2. Closed Error Taxonomy: Uses exit codes 0..7 with authoritative error codes from `schemas/v1/error-codes.json`.
3. OCC & Atomicity: All mutations enforce optimistic concurrency control (`--base-sha256`, `--head-sha256`) and L4/L5 locks (`multi_studio_lock`, `studio_lock`).
4. Concurrency & Integrity in `handle_answer`: Full read-modify-write cycle executed under L5 session lock; corrupted answers files return exit 1 (`CORRUPT_SESSION_RECORD`).
5. Delta Validation Exact State Pairing: `handle_delta_validate` strictly pairs `base_ontology`, `base_ver`, and `expected_parent_sha` to the exact same version/file base (fails closed with exit 2 `SYNTAX_INVALID_ARGUMENT` on corrupt base files, and exit 3 `VERSION_NOT_FOUND` if a requested `--base-version` is missing).
6. Monotonic SemVer Bump: Auto-commit construction always evaluates `compute_next_semver` against base ontology artifacts without invalid fallback downgrades.
7. Subparser Spec Alignment: `register_studio_subparsers` reuses the spec-driven studio parser without destructive deletion, registering `--show-token`, `--authorized-by`, and `--authorized-role` flags.
8. Historical Contract Downgrade Protection: `handle_promote` enforces `--acknowledge-historical-contract-warning` and `--historical-contract-rationale` (>= 10 chars) if promoting a version older than existing workspace contract (`HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED` [exit 4], `HISTORICAL_CONTRACT_RATIONALE_REQUIRED` [exit 1]).
9. Waiver Credential Security: Passwords and waiver keys are never accepted on CLI argv; only via `--waiver-key-file` or `OPENDV_WAIVER_KEY`.
