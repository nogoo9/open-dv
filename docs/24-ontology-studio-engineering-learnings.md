# 24 — Ontology Studio Engineering Learnings & Audit Post-Mortem

**Status:** Normative Engineering Reference  
**Scope:** Cross-package engineering invariants distilled from building and dual-model auditing (**Gemini 4 Carbon** + **Claude CLI Opus 5.5**) the Ontology Construction Studio (`dev/ontology-studio`) across `opendv-core`, `opendv-contracts`, `opendv-sources`, `opendv-service`, `opendv-agent-harness`, `opendv-platform-api`, `opendv-cli`, `opendvctl`, and `opendv-web-workbench`.

---

## Overview

During multi-wave implementation and adversarial dual-model pre-merge verification of the Ontology Construction Studio, recurring failure classes emerged at cryptographic serialization boundaries, OS-level lock/ACL primitives, async-to-thread event bridges, cross-transport DTO contracts, governance/privacy enforcement, and multi-agent workspace hygiene.

Every contributor and autonomous agent working in `open-dv` must treat the six guardrails below as non-negotiable engineering invariants. For quick-reference enforcement rules loaded automatically by agent harnesses, see [`.agents/rules/ontology-studio-learnings.md`](../.agents/rules/ontology-studio-learnings.md) and [`AGENTS.md`](../AGENTS.md).

---

## 1. Cryptographic Hash & RFC 8785 JCS Invariance

Content-Addressable Storage (CAS), Optimistic Concurrency Control (`If-Match` / `base_sha256` / `parent_sha256`), and golden parity suites depend on bit-exact SHA-256 agreement across Python (`opendv-core`), TypeScript (`opendvctl`, `opendv-web-workbench`), and persisted disk artifacts.

### Root Causes Observed
- **Placeholder Hex Digests & Custom Namespace Confusion:** Early design drafts referenced an unverified placeholder genesis digest instead of computing the true SHA-256 over the RFC 8785 JSON Canonicalization Scheme (JCS) bytes of the default `0.0.0` empty Graph A preimage (`1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9` for `general` / `"Enterprise Architecture"`), while user journey examples reused that default-namespace digest against custom-namespace greenfield sessions (`wealth_mgmt` / `"Global Wealth Advisory"` -> `9897c2a95f2987821979eeec92a96a14eb64af37b06222b993adbc717aff1142`).
- **Disk Byte vs. Logical Digest Divergence:** Writing `model_dump(mode="json")` instead of `canonicalize_graph_a_dict(...)` JCS bytes into CAS caused `sha256(file_bytes) != target_sha256`, breaking tamper-evident CAS verification on reload and bundle export.
- **Retroactive Normalization Breaking Golden Digests:** Applying empty-collection pruning (`_norm_empty`) or lexicographic sorting indiscriminately to legacy Graph A root lists or ordered `attributes` arrays silently altered certified baseline digests (`pe_graph_a_ontology.yaml` -> `1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81`).
- **Lossy Client-Side Canonicalization:** Reconstructing a Graph A dictionary in the browser from a lossy UI node/edge projection (inventing default `enterprise_owner`/`domain_namespace` values, dropping `reference_enumerations`/`decision_refs`, or emitting `source_concept_uri`/`target_concept_uri` instead of `endpoints`) guaranteed client/server SHA-256 mismatches.

### Mandatory Guardrails
1. **Never invent placeholder digests** in specifications, ADRs, fixtures, or tests. Always compute digests directly from `compute_ontology_sha256` / `dumps_jcs_bytes` over the exact canonical preimage and assert parity in automated tests.
2. **Always persist exact canonical bytes to CAS** (`dumps_jcs_bytes(canonicalize_graph_a_dict(ont))`) so `hashlib.sha256(raw_disk_bytes).hexdigest() == target_sha256` holds unconditionally.
3. **Scope `_norm_empty` strictly to post-baseline extension fields** (`_CBC_EXT`, `_ATTR_OPT`, `_REL_EXT`, `_ENUM_EXT`, `_ONT_EXT`) so omitted optional extensions hash identically to empty defaults without perturbing legacy root lists (`canonical_relationships`, `reference_enumerations`, `standards_references`, `decision_refs`) or declaration-ordered concept `attributes`.
4. **Never substitute lossy client-side Graph A reconstructions for server exports** when an active Studio session exists; fetch canonical RFC 8785 JCS bytes from the backend service.

---

## 2. Windows / POSIX Concurrency, File Locking & Filesystem Security

`open-dv` runs first-class on both Windows (`nt`) and POSIX (`linux`/`darwin`). Naive cross-platform filesystem code fails under concurrent contention, non-English Windows locales, and open file handles.

### Root Causes Observed
- **Overlapping Mandatory Byte-Range Locks (`WinError 33`):** Mixing two lock implementations or locking byte `0` via `msvcrt.locking(fd, LK_NBLCK, 1)` and subsequently seeking to byte `0` to write lock-holder JSON metadata triggered Windows mandatory lock violations or corrupted lock offsets on unlock.
- **Leaked File Descriptors on Post-Lock Exceptions:** Raising an exception while writing lock diagnostic metadata after acquiring the OS lock leaked the open file handle and left the lock permanently wedged in the process.
- **Localized `icacls.exe` Output Scraping:** Verifying owner-only (`0600`/`0700`) directory permissions on Windows by parsing English strings (`"BUILTIN\\Administrators"`, `"NT AUTHORITY\\SYSTEM"`) from `icacls.exe` stdout failed on German, Japanese, and French Windows installations and suffered from subprocess overhead.
- **Non-Atomic Directory Overwrites (`force=True`):** Deleting an existing session/bundle target directory before validating and extracting replacement content risked permanent data loss on mid-flight failure or `PermissionError` on Windows when readers held handles.
- **Orphaned Subprocess Trees:** Terminating only the immediate child PID on CLI/harness subprocess timeout left grandchild worker processes alive holding Studio file locks and stdout pipes on Windows.

### Mandatory Guardrails
1. **Maintain a single canonical lock implementation (`StudioLock`, hierarchy `L1 -> L5`).** On Windows, lock a dedicated byte range disjoint from any metadata write offset (or keep lock files zero-byte/fixed-offset) and **always release the OS lock and close `fd` in `try/except` cleanup** if post-acquire setup fails.
2. **Use Win32 `advapi32` / `kernel32` binary SID DACLs via `ctypes`** on Windows (`S-1-5-18` LocalSystem, `S-1-5-32-544` Builtin Administrators, and the current process token owner SID)—**never scrape localized `icacls.exe` text output**.
3. **Perform `force=True` directory replacements via sibling staging + tombstone swap:** stage and validate into `<target>.staging.<nonce>`, rename existing `<target>` to `<target>.tombstone.<nonce>`, rename staging into `<target>`, and roll back the tombstone if the final swap fails before unlinking the tombstone.
4. **Kill entire process trees on subprocess timeouts** (`taskkill /F /T /PID` or Job Objects on Windows; process groups `os.killpg` on POSIX).
5. **Quote batch variables safely** (`set "VAR=value"`) in `.cmd` scripts and bind local development servers explicitly to `--host 127.0.0.1`.
6. **Scope `ContextVar` lock reentrancy to `(thread_id, task_id, is_locked)` and roll back on `BaseException`:** Python copies `ContextVar` mappings into child `asyncio.create_task` coroutines and `asyncio.to_thread` workers; key reentrancy ownership on `(thread_id, task_id, is_locked)` so spawned tasks never inherit false lock reentrancy, and wrap ordered multi-lock acquisition (`L1 -> L5`) in `except BaseException` so `asyncio.CancelledError` unwinds all partially acquired locks cleanly.
7. **Coordinate parent-directory `.lifecycle_<id>.lock` (`0_lifecycle < 1_session`) and re-verify POSIX `flock` inodes:** Never hold a lock inside a directory slated for atomic rename (`os.replace`) or deletion (`shutil.rmtree`); acquire `<sessions_parent>/.lifecycle_<id>.lock` (`resource_id="0_lifecycle"`) ahead of `<session_dir>/.session.lock` (`resource_id="1_session"`) inside `reentrant_session_lock` so directory swaps exclude all session writers without Windows open-handle conflicts. On POSIX, always verify `(st_dev, st_ino)` of `os.fstat(fd)` matches `os.stat(path)` after `flock` before returning.
8. **Enforce magic-byte/extension agreement, `--infilter`, and linear `O(N)` ingestion passes:** Verify PKZIP (`PK\x03\x04`) vs OLE2 CFB (`D0 CF 11 E0 A1 B1 1A E1`) magic headers, scan legacy OLE streams for DDE/external-link fields, and pass explicit `--infilter` flags before `soffice` conversion so LibreOffice cannot content-sniff HTML/RTF/FODT uploads; keep Markdown fence/heading extraction strictly `O(lines)` in a single pass.

---

## 3. Async-to-Thread Boundaries & Gapless W3C SSE Streaming

FastAPI routes execute on an `asyncio` event loop while CPU/IO-bound document ingestion, clarification synthesis, and deterministic construction execute in worker threads (`run_in_threadpool` / `ThreadPoolExecutor`).

### Root Causes Observed
- **Thread-Unsafe `asyncio.Queue` Mutation:** Calling `queue.put_nowait(event)` directly from a background OS worker thread corrupted `asyncio.Queue` internal state and failed to wake awaiting SSE coroutines reliably across Python event loop implementations.
- **Replay-to-Live Race Window:** Reading historical events from `events.jsonl` on disk *before* registering the live subscriber queue dropped events emitted during the disk read window, or duplicated boundary events when subscribing first without sequence deduplication.
- **Fire-and-Forget Client Job Starvation:** Returning `202 Accepted` (`JobHandleDTO`) to the Web Workbench without wiring `EventSource` (`/jobs/{job_id}/events` with `withCredentials: true` and polling fallback) left UI progress indicators frozen indefinitely.

### Mandatory Guardrails
1. **Never call `asyncio.Queue` methods directly from worker threads.** Capture `(loop, queue)` on the event-loop thread and publish exclusively via `loop.call_soon_threadsafe(...)`.
2. **Subscribe BEFORE replaying disk journals:** register the live `(loop, queue)` subscriber first, replay persisted journal entries from disk, track `last_emitted_seq`, and discard any queued live event with `seq <= last_emitted_seq` to guarantee **gapless, duplicate-free** SSE delivery.
3. **Close SSE lifecycles cleanly and fence post-commit job cancellation:** Cancel open `EventSource` streams and poll timers on terminal events (`job_completed`, `job_failed`) and session switches (`activeJobGeneration++`); on the server (`JobSupervisor`), latch a sticky `commit_started` flag upon entering `"committing"` so `cancel_job` refuses late cancellations and never overwrites a completed auto-commit.

---

## 4. Cross-Surface Single Source of Truth (API, `opendv`, `opendvctl`, Web Workbench & Docs)

`open-dv` exposes Ontology Studio across four user-facing surfaces: Python standalone CLI (`opendv`), compiled TypeScript client CLI (`opendvctl`), FastAPI REST/SSE (`opendv-platform-api`), and React 19 SPA (`opendv-web-workbench`), documented in normative guides (`docs/20`–`docs/23`).

### Root Causes Observed
- **Ad-Hoc CLI Parser Monkey-Patching:** Patching flags directly in `apps/opendvctl/src/parser.ts` or `apps/opendv-cli` without registering them in `schemas/v1/cli-spec.json` caused immediate parity failures and broken `--help` / snippet validation.
- **Divergent Governance Thresholds:** Hardcoding `0.70` in one helper while `ONT-EVO-002` specifies `0.6500` (`skos:exactMatch` / `skos:closeMatch`, excluding `skos:broadMatch`), or misnumbering `ONT-EVO-001..003` in user guides, created contradictory pass/fail verdicts between UI scorecards, CLIs, and server commits.
- **`extra="forbid"` Wire Contract Drift:** Pydantic v2 DTOs enforce `extra="forbid"`. Subtle field-name mismatches in Web Workbench requests and documentation snippets caused 100% `HTTP 400`/`403`/`422` failures against the real backend:
  - Multipart upload sending `confidential="true"` instead of `do_not_send_to_agent="true"` (silently dropping confidential taint!).
  - Delta operations omitting `op_id`, sending forbidden `provenance` on `AddExternalMappingOp`, or sending `confidence` instead of `mapping_confidence`.
  - Greenfield `0.0.0` commits defaulting missing `active_version` to `"1.0.0"` or computing `"0.1.0"` instead of `"1.0.0"` when `add_concept` is present.
  - Clarification answers sending `selected_option_ids` / `custom_answer_text` instead of `selected_options` / `user_text`, and omitting `expected_answers_sha256` / `If-Match`.
  - Cookie-authenticated write requests omitting `credentials: "include"` and `X-OpenDV-Workbench: 1` CSRF protection headers, or transmitting raw `OPENDV_WAIVER_KEY` secrets inside JSON bodies rather than `X-OpenDV-Waiver-Key` headers.
- **Executable Doc Drift & Unescaped Untrusted Text:** Extension guide examples (`docs/23`) drifted from real Protocol method signatures (`DocumentAdapter`, `ReferenceOntologyAdapter`) and interpolated untrusted LLM/PDF ontology descriptions and URIs directly into Cypher DDL strings/comments without newline or backtick sanitization.

### Mandatory Guardrails
1. **Treat `schemas/v1/cli-spec.json`, `schemas/v1/error-codes.json`, and `opendv-contracts` DTOs as the single source of truth.** Disable `argparse` prefix abbreviation (`allow_abbrev=False`) so Python `opendv` matches TypeScript `opendvctl` on rejected flag prefixes (e.g. `--waiver-key` vs `--waiver-key-file`), and always rebuild compiled `opendvctl` binaries (`dist/opendvctl.exe` and `dist/release/opendvctl-windows-x64.exe`, which `ClientCliTransport` prefers when present) after `cli-spec.json` or `apps/opendvctl/src` updates. Enforce `set(ErrorCode) == set(error-codes.json)` and CLI tree parity in automated CI tests.
2. **Route all Web Workbench HTTP traffic through a single `studioFetch` boundary** (even when injecting mock `fetchImpl` in unit tests) so `credentials: "include"`, `X-OpenDV-Workbench: 1` on mutating verbs (`POST`/`PUT`/`PATCH`/`DELETE`), and `Authorization: Bearer` headers are tested and guaranteed uniformly.
3. **Execute documentation snippets in CI (`tests/docs/test_doc_code_snippets.py`):** validate CLI invocations against `cli-spec.json`, validate JSON/YAML blocks against `extra="forbid"` Pydantic DTOs, execute Python SPI examples against live package classes, and treat all ontology text/identifiers extracted from PDFs or LLMs as **untrusted input** requiring strict identifier validation and delimiter sanitization in every custom exporter.
4. **Never auto-fetch server `head_sha256` to populate `If-Match` over stale client state:** CLI commands and SPA stores must pin `If-Match` / `base_sha256` to the exact baseline revision from which the local delta or working canvas was derived. Auto-querying the latest server `head_sha256` over uncommitted local edits silently defeats `ONT-EVO-003` optimistic concurrency control.

---

## 5. Fail-Closed Governance, Privacy Tainting & Portable Audit Chains

Enterprise ontology construction handles regulated financial/healthcare schemas and confidential legal agreements. Security, grounding, and audit controls must always fail closed.

### Root Causes Observed
- **Path A Lexical Grounding Bypass:** Scoring opaque concept URI slugs or allowing `0`-mapping concepts, `skos:broadMatch` predicates, unverified client-typed confidences, or averaged mapping scores to pass `ONT-EVO-002`, or failing to revoke `grandfathered_concept_uris` / per-concept waivers once a concept is mutated by any of the 17 existing-concept-mutating delta operations (`18` total in `CONCEPT_MUTATING_OP_TYPES`, including `update_relationship_endpoint`).
- **Identifier Boundary Leaks in Pseudonymizers:** Using regex `\b` word boundaries failed to redact confidential terms embedded inside `snake_case` SQL/DV 2.0 column and hub names (because `_` is a regex word character in `\w`!). Non-sticky confidential flags also risked clearing taint on re-upload or CAS garbage collection.
- **Broken Cross-Workspace Audit HMAC Chains:** Appending raw unsigned JSON lines to `audit.jsonl` broke tamper-evident hash chaining, while importing `.odvstudio.zip` bundles exported under a foreign workspace's `K_audit` secret either rejected valid bundles or left unverifiable foreign HMACs in the local session ledger without replaying delta hashes and locally re-anchoring the chain.

### Mandatory Guardrails
1. **Enforce strict Path A `ONT-EVO-002` grounding everywhere:** a concept is Path A grounded **if and only if** at least one server-verified mapping has `scheme == "FIBO"` (case-insensitive), `predicate` in `{"skos:exactMatch", "skos:closeMatch"}` (and their full SKOS IRIs; `skos:broadMatch` is strictly excluded), and numeric `confidence >= 0.6500`. Never invent default confidence scores. Revoke grandfather and per-concept waiver exemptions immediately when a concept is mutated.
2. **Use alphanumeric lookarounds `(?<![A-Za-z0-9])...(?![A-Za-z0-9])` (never `\b`), union `sensitive_terms` with `sensitive_entities`, and enforce max-rank sticky taint:** Always pseudonymize over the union of caller `sensitive_terms` and chunk/table `metadata["sensitive_entities"]`, and compute effective taint as the lattice maximum (`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED`) across persisted items, manifests, and `taint_index.json` while purging tombstoned source SHAs on re-parse.
3. **Maintain WAL roll-forward & `PENDING_COMMIT`-gated recovery on signed `audit.jsonl` ledgers:** Never write unsigned entries to `audit.jsonl`, roll forward a single `K_audit`-verified write-ahead tail record if `session.json` persistence was interrupted, require an un-aborted `PENDING_COMMIT` audit intent before recovering forward `versions/v*` directories (quarantining unauthenticated orphans), verify imported `v0.0.0` roots against the genuine genesis digest, and bind waivers to both submitted and normalized `proposal_delta_sha256` with fail-closed `rebase` lineage checks.
4. **Keep administrative and bootstrap context overrides strictly server-controlled:** Internal execution flags such as `allow_bootstrap` or `allow_legacy_ip1_bootstrap` must be passed only from trusted server-side call sites and never derived or auto-enabled from caller-controlled request payloads, headers, or bundle metadata.
5. **Separate machine proposer identity from human job trigger (`ADR-006`) and re-sign acknowledged foreign waivers under local `K_waiver`:** Machine-synthesized construction proposals (`POST /construct`) must stamp `proposer = f"{resolved_mode}:{job_id}"` and `triggered_by = principal` so human stewards can accept engine proposals without colliding with `PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN` (reserved for raw human-authored proposals). Likewise, when an administrator explicitly acknowledges foreign waivers during cross-workspace bundle import, re-sign validated non-expired `StewardWaiverRecord`s under the destination workspace's `K_waiver` so subsequent `promote_to_workspace` checks verify local HMAC signatures cleanly.

---

## 6. Multi-Agent Wave Hygiene & Ownership Discipline

Parallel multi-agent execution accelerates delivery across decoupled monorepo packages, but requires strict workspace and git hygiene to prevent cross-wave interference and repository pollution.

### Root Causes Observed
- **Tracked Scratch & Audit Dumps:** Subagents running parallel tracks wrote temporary context summaries (`scripts/context_track_*.md`), critique logs (`scripts/critique_*.json`), tracker dumps (`reports/*.json`), and one-off GitHub issue mutation scripts directly into tracked directories instead of ephemeral scratch directories (`<appDataDir>/brain/<id>/scratch/` or gitignored `.critique/`).
- **Shared Build Output Contention:** Running `bun run build:static` inside a frontend wave while a backend wave simultaneously ran `ruff`/`pytest` over `apps/opendv-platform-api/src/opendv_platform_api/static` risked cross-agent workspace race conditions.

### Mandatory Guardrails
1. **Never create or stage agent context notes, critique dumps, or one-off scratch scripts inside tracked repository paths (`scripts/`, `reports/`, repo root).** Store ephemeral agent artifacts in the harness scratch directory or gitignored `.critique/` directories, and verify `git status --short` before completing any wave.
2. **Enforce disjoint file-ownership boundaries across parallel waves**, deferring cross-boundary static asset copies (`build:static`) or OpenAPI/schema regeneration to dedicated sequential integration gates.
