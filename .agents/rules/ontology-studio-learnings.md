# Open-DV V2 Platform & Ontology Hardening Guardrails

> [!NOTE] **OPEN-DV V2 CLEAN-SHEET ALIGNMENT (2026-11-25):** All concurrency, lock-ordering, Win32 binary-SID ACL, RFC 8785 JCS serialization, gapless `Last-Event-ID` SSE, `extra="forbid"` DTO validation, `< 5,000`-token module caps, WCAG 2.1 AA contrast, and `<MASKED_PII>` redaction rules are active across `src/opendv/`, `web/`, `schemas/v2/`, and unified `opendv` CLI (see [`docs/designs/v2-clean-sheet-architecture.md`](../../docs/designs/v2-clean-sheet-architecture.md)).

Mandatory engineering checklist for modifying CAS/hashing, SQLite WAL state, locks, FastAPI/SSE, CLI, Web Workbench, or documentation.

## 1. Cryptographic Digests & RFC 8785 JCS
- **No Placeholder Digests:** Never invent hex digests in specs, ADRs, fixtures, or tests. Compute digests from real RFC 8785 JCS preimages (`sha256_jcs` / `dumps_jcs_bytes`; V2 `0.0.0` genesis `GraphAOntology` = `0ae856bf9fdba74e83e793ea9f74106a0c14d21d73756e1e3a12abd426d0083a`; FIBO core & 8 starter pack pins live in [`schemas/v2/baseline-digests.json`](../../schemas/v2/baseline-digests.json)).
- **Disk Byte == Digest Preimage:** Persist canonical JCS bytes cleanly inside `project.sqlite` / bundles so `sha256(jcs_bytes) == revision_sha256`.
- **No Lossy Client Exports:** Fetch canonical exports via `/api/v1/projects/{project_id}/ontology/export` (or `opendv ontology export`) and surface typed `ProblemDetails` errors loudly rather than substituting lossy client-side Graph A reconstructions.

## 2. Windows / POSIX Locks, SQLite WAL, ACLs & Portable Archives
- **Per-Project Single-Writer Concurrency (`409 PROJECT_JOB_ACTIVE`):** Serialize mutating project runs cleanly under SQLite WAL (`project.sqlite`, 5 tables) + project lock semantics; never run conflicting background ontology/vault mutations concurrently on the same project.
- **Symlink-Free & Tamper-Proof `.opendv.zip` Bundles (`zip_guard.py`):** Reject symlinks/reparse points, path traversal (`../`), Windows ADS/reserved names (`CON..LPT9`), zip bombs (`MAX_RATIO = 100.0`, `ZIP_STORED` for `.duckdb`/`.parquet`), and SHA-256 manifest mismatches (`ARCHIVE_SECURITY_VIOLATION` / `BUNDLE_HASH_MISMATCH`); restrict API bundle exports to `OPENDV_WORKSPACE_ROOTS`.
- **Locale-Safe Windows ACLs & `KeyringSecretStore`:** Enforce owner-only permissions on Windows via Win32 `advapi32` binary SIDs (`S-1-5-18`, `S-1-5-32-544`, process token owner SID) — never parse localized `icacls.exe` output; keep secrets exclusively in `KeyringSecretStore` / process env (never persisted in `project.sqlite` or `.opendv.zip`).
- **Process-Tree Cleanup & Loopback Binding:** Kill full process trees on subprocess timeouts, quote `.cmd` variables (`set "VAR=val"`), and bind local dev servers to `127.0.0.1` (`opendv serve` exits `40` on non-loopback `--host` unless `--allow-remote-unauthenticated` is explicitly passed).

## 3. Thread-Safe Async & Gapless W3C SSE (`RunStreamEvent`)
- **No Cross-Thread `asyncio.Queue` Calls:** From OS worker threads, dispatch events exclusively via `loop.call_soon_threadsafe(...)`.
- **Subscribe Before Replay:** Register the live SSE queue subscriber *before* reading persisted SQLite/event history, deduplicating by monotonic sequence ID (`since_seq`) for gapless streams.
- **Client Job Tracking & Budget Meter:** Connect `POST .../ontology/construct` and `POST .../runs` to `.../events` SSE streams, close cleanly on terminal state or project switch, and expose live `Budget Meter` (`Turns · Tokens · Active Seconds`) telemetry (`429 BUDGET_EXCEEDED`).

## 4. Cross-Surface Contract Parity (`StrictModel` `extra="forbid"`)
- **Single Source of Truth (`schemas/v2/`):** Regenerate [`schemas/v2/cli-spec.json`](../../schemas/v2/cli-spec.json), [`schemas/v2/error-codes.json`](../../schemas/v2/error-codes.json), [`schemas/v2/openapi.json`](../../schemas/v2/openapi.json), and [`docs/16-cli-reference.md`](../../docs/16-cli-reference.md) via `uv run python -m opendv.cli.export_contracts` and `scripts/generate_cli_reference.py`. Keep all `34` `ErrorCode`s and `8` `CliExitCode`s in lockstep (`scripts/check_error_codes.py --check-ast`).
- **No Auto-Fetched `base_revision_id` OCC Overwrites:** CLI and Web Workbench clients must never auto-fetch current server revisions to overwrite stale local ontology edits on `409 REVISION_CONFLICT`.
- **Untrusted Text Sanitization:** Treat LLM/PDF labels, URIs, and descriptions as untrusted input — validate identifiers and sanitize quotes/comment delimiters in all Step 7 emitters (`src/opendv/storage/emitters/`) and docs.

## 5. Fail-Closed Governance, PII Masking & Security
- **Path A FIBO Grounding (`>= 0.6500`):** Ground Path A via `skos:exactMatch` or `skos:closeMatch >= 0.6500` (`skos:broadMatch` excluded) against pinned `fibo-core-pack.json` (`2,206` concepts); allow Path B1 empirical table/page-span provenance; quarantine ungrounded concepts under Path B2 unless signed via Admin waiver.
- **`(?<![A-Za-z0-9])`-Safe Redaction & PII Taint:** Mask PII (`<MASKED_PII>`) cleanly in `src/opendv/sources/pii.py` before outbound prompts/logs, isolate PII attributes into `sat_*_pii`, guard SSRF & workspace root path escapes in `src/opendv/sources/adapters.py`, and fail closed on corrupt project archives or malformed YAML/JSON (`extra="forbid"`).
- **Zero-SQL `VaultSchemaIR v2.0`:** Agents never author SQL; all physical DDL/DML compiles deterministically in `src/opendv/storage/` across `11` Step-7 targets (`5-Zone Z0–Z4` + `information_mart`).

## 6. Multi-Agent Hygiene, Token Budget (`D16`) & WCAG 2.1 AA Web Workbench
- **Never commit scratch notes or critique dumps** into `scripts/`, `reports/`, or root (`EGM artifact directory: .dev/docs`). Keep every module in `src/opendv/` and `web/src/` strictly `< 5,000` `cl100k_base` tokens (`tests/architecture/test_module_token_budget.py`).
- **Cross-Platform CI Parity:** Avoid calling `Path.resolve()`/`exists()` inside monkeypatched `os.stat`; verify `bun run --cwd web typecheck && bun run --cwd web lint && bun run --cwd web build && bun run --cwd web test`.
- **WCAG 2.1 AA Tokens (`DESIGN.md` & `web/src/styles/tokens.css`):** Maintain `>= 4.5:1` text contrast (`#57554f` muted, `#8f442d` primary ink, `--sal-ink`/`--sat-ink`/`--pii-ink`/`--pass-ink`/`--danger-ink`), `2px` `:focus-visible` outlines, and `>= 3:1` `@xyflow/react` graph edge/control contrast.
