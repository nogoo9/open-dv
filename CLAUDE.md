# Claude Code & CLI Agent Instructions (`CLAUDE.md`)

Welcome to the `open-dv` monorepo. Before planning, editing, reviewing, or running commands in this repository, read and enforce the following mandatory project documents in order:

1. **[`AGENTS.md`](AGENTS.md)** — Root Agent & Contributor Constitution: mission, target-agnostic DV 2.0 vs. DuckDB verification boundaries, the 14 non-negotiable engineering invariants (`OBJ-01`, `FR-DOM-*`, `FR-VER-*`, `REQ-CORE-01`, `REQ-CLI-01/02`, `REQ-ONT-01/02`), monorepo package DAG boundaries, and verification gates.
2. **[`.agents/rules/ontology-studio-learnings.md`](.agents/rules/ontology-studio-learnings.md)** — High-signal operational checklist covering RFC 8785 JCS hash invariance, Windows/POSIX file locking & binary-SID ACLs, thread-safe `asyncio`/SSE bridges, `extra="forbid"` cross-surface DTO parity, fail-closed `ONT-EVO-001..003` governance & `snake_case`-safe privacy redaction, and multi-agent repo hygiene.
3. **[`docs/24-ontology-studio-engineering-learnings.md`](docs/24-ontology-studio-engineering-learnings.md)** — Comprehensive engineering post-mortem and rationale from building and dual-model auditing Ontology Studio.

## Quick Non-Negotiable Reminders
- **Zero Production Mocks / Silent Fallbacks:** Fail closed and loudly with typed `ErrorCode` diagnostics (`schemas/v1/error-codes.json`). Never silently swallow server errors or substitute lossy client-side fallbacks when an active backend session is configured.
- **Cross-Transport Parity:** Standalone `opendv` (Python), remote `opendvctl` (Bun/TS), FastAPI (`/api/v1/...`), Web Workbench (`/ontology`), and executable documentation (`docs/22`, `docs/23`) must agree on `schemas/v1/cli-spec.json`, `extra="forbid"` Pydantic DTOs, and canonical RFC 8785 JCS SHA-256 digests (`0.0.0` genesis = `1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9`).
- **Workspace Hygiene:** Never write scratch notes, critique dumps, or temporary scripts into tracked folders (`scripts/`, `reports/`, or repo root). Always verify `git status --short` and run the relevant `uv run pytest` and `bun test` / `bun x tsc --noEmit` suites before completing work.
