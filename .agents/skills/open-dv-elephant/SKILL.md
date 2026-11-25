---
name: open-dv-elephant
description: >-
  Loads curated open-dv V2 architectural context, the 14 non-negotiable Data Vault
  2.0 invariants, 4-layer src/opendv/ DAG boundaries, and the EGM design sitemap
  (docs/designs/SITEMAP.md) for Elephant-Goldfish Model (EGM) sessions. Use
  before /egm-brainstorm, /egm-draft, /egm-review, /egm-bootstrap, or
  /egm-drift-report on open-dv, or when onboarding an architecture session.
---

# `open-dv` Elephant Context Loader

## 1. Session Orientation & Artifact Routing

When working with Elephant-Goldfish Model (`/egm-*`) workflows on `open-dv`, establish full architectural context before drafting or reviewing:

1. Read [`AGENTS.md`](../../../AGENTS.md) and [`CLAUDE.md`](../../../CLAUDE.md) for the 14 non-negotiable DV 2.0 & platform invariants, and inspect `.agents/rules/ontology-studio-learnings.md` when touching JCS hashing, SQLite WAL persistence, ACLs/keyring, SSE streams, or DTOs.
2. Read the EGM design index [`docs/designs/SITEMAP.md`](../../../docs/designs/SITEMAP.md), [`docs/designs/v2-clean-sheet-architecture.md`](../../../docs/designs/v2-clean-sheet-architecture.md), [`docs/designs/v2-workbench-ui-design.md`](../../../docs/designs/v2-workbench-ui-design.md), and [`DESIGN.md`](../../../DESIGN.md).
3. Consult active V2 specs in `open-dv/docs/` (`29` through `32`, plus `16-cli-reference.md` and `DV2_AGENT_REFERENCE_GUIDE.md`) before proposing schema, modeling loop, workbench, or export changes.
4. Keep EGM scratch outputs out of tracked directories:
   - Inside `open-dv/`: `EGM artifact directory: .dev/docs` (gitignored).
   - From outer `focused-darwin/` workspace root: `EGM artifact directory: open-dv/.dev/docs`.
   - Promote approved specs into `open-dv/docs/designs/` and register them in `docs/designs/SITEMAP.md`.
   - When running `/egm-bootstrap` on directories that already have curated human `README.md` files, pass `target_filename="EGM.md"` unless explicitly updating `README.md`.

## 2. Explicit Review Criteria for Fresh-Context Goldfish Agents

Per EGM guidance, Goldfish reviewers (`egm-review` / `egm-brainstorm`) start with zero prior turn history. Any design brief or `/egm-review` prompt on `open-dv` must state applicable constraints explicitly as review criteria:

- **Zero LLM SQL (`OBJ-01`):** Agents emit/repair declarative `OntologyDelta` (`34` ops) and `VaultSchemaIR v2.0` (`UniversalVaultSchemaIR`) proposals only; deterministic `src/opendv/storage/emitters/` generate physical SQL/orchestrator deliverables across `11` Step-7 targets.
- **Deterministic Hashing & DV 2.0 History (`FR-VER-01`, `INV-01..06`, `EVO-01..08`):** Length-prefixed collision-free hashing (`Algorithm A.5`), `3-Tier BKCC`, insert-only Raw Vault, and additive-only evolution.
- **4-Layer DAG & Fail-Closed Core (`REQ-CORE-01`):** Core logic lives in `src/opendv/` enforcing strict downward imports (`models` -> `sources`/`verifier`/`storage` -> `agents` -> `service` -> `api`/`cli`). `cli` never imports `api`; zero prod mocks or silent fallbacks (`REQ-MOCK-01`, `REQ-FAIL-01`).
- **Contract & Transport Parity:** Unified `opendv` CLI (`46` subcommands), FastAPI REST/SSE (`/api/v1/projects/...`), `opendv mcp serve`, and React 19 Workbench (`web/`) align strictly on `schemas/v2/{cli-spec,error-codes,openapi,baseline-digests}.json` and `StrictModel` (`extra="forbid"`) DTOs (`34` `ErrorCode`s, `8` `CliExitCode`s).
- **Workbench UX & Module Ceilings:** Loopback open access (`D18`, exit `40` on non-loopback without `--allow-remote-unauthenticated`), 4-tab `:8420` Workbench (`Project Workflow` 7-Step `Guided ⇄ Observatory` + `Budget Meter`, `DV Schema Explorer` `5-Zone Z0–Z4` + `information_mart`, `Run Explorer`, `Settings`), WCAG 2.1 AA tokens (`DESIGN.md`), and strictly `< 5,000` `cl100k_base` tokens per active module (`D16`).

## 3. V2 Consolidated Package & Frontend Map

- **Layer 1 (`src/opendv/models/`):** Pydantic v2 `StrictModel` DTOs (`GraphAOntology`, `34`-op `OntologyDelta`, `VaultSchemaIR v2.0`, `ProjectState`), `34` `ErrorCode`s, `8` `CliExitCode`s, RFC 8785 `json_hasher`.
- **Layer 2 (`src/opendv/{sources,verifier,storage}/`):** Connector SPI (`SQLite`, `DuckDB`, `Postgres`, `CSV`, `Parquet`, `PDF`, `Text`, `Inline`), SSRF/PII guards, `fibo-core-pack.json` (`2,206` concepts), 3-Loop CEGAR prover, and `11` Step-7 dialect emitters.
- **Layer 3–4 (`src/opendv/{agents,service}/`):** Agent CLI/MCP harnesses (`agy`, `claude`, `gemini`), `OpenDVService`, SQLite WAL `project.sqlite` (`5` tables), `KeyringSecretStore`, `zip_guard.py` (`.opendv.zip`), and SSE streams.
- **Layer 5 & UI (`src/opendv/{api,cli}/`, `web/`, `apps/opendv-website/`):** FastAPI REST/SSE host (`:8420`), unified `opendv` CLI (`46` subcommands), React 19 Workbench (`web/`), and Astro docs site (`apps/opendv-website/`).

## 4. Verification Gates

Run from `open-dv/` before finalizing specs or implementations:

- Contracts & error codes: `uv run python -m opendv.cli.export_contracts --check` and `uv run python scripts/check_error_codes.py --catalog schemas/v2/error-codes.json --baselines schemas/v2/baseline-digests.json --check-ast`
- Python tests & guards: `uv run pytest -q` (and `uv run pytest tests/architecture/ tests/docs/ -q`)
- Python lint & types: `uv run ruff check src tests scripts` and `uv run mypy src`
- Web Workbench (`web/`): `bun run --cwd web typecheck && bun run --cwd web lint && bun run --cwd web build && bun run --cwd web test`
