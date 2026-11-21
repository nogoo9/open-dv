---
name: open-dv-elephant
description: >-
  Loads curated open-dv architectural context, the 14 non-negotiable Data Vault
  2.0 invariants, monorepo package boundaries, and the EGM design sitemap
  (docs/designs/SITEMAP.md) for Elephant-Goldfish Model (EGM) sessions. Use
  before /egm-brainstorm, /egm-draft, /egm-review, /egm-bootstrap, or
  /egm-drift-report on open-dv, or when onboarding an architecture session.
---

# `open-dv` Elephant Context Loader

## 1. Session Orientation & Artifact Routing

When working with Elephant-Goldfish Model (`/egm-*`) workflows on `open-dv`, establish full architectural context before drafting or reviewing:

1. Read [`AGENTS.md`](../../../AGENTS.md) and [`CLAUDE.md`](../../../CLAUDE.md) for the 14 non-negotiable DV 2.0 & platform invariants, and inspect `.agents/rules/ontology-studio-learnings.md` when touching CAS/locks/SSE/DTOs.
2. Read the EGM design index [`docs/designs/SITEMAP.md`](../../../docs/designs/SITEMAP.md) (and [`DESIGN.md`](../../../DESIGN.md) for UI specs).
3. Consult active V2 specs in `open-dv/docs/` (`29` through `33`) before proposing schema, modeling loop, workbench, or export changes.
4. Keep EGM scratch outputs out of tracked directories:
   - Inside `open-dv/`: `EGM artifact directory: .dev/docs` (gitignored).
   - From outer `focused-darwin/` workspace root: `EGM artifact directory: open-dv/.dev/docs`.
   - Promote approved specs into `open-dv/docs/designs/` and register them in `docs/designs/SITEMAP.md`.
   - When running `/egm-bootstrap` on directories that already have curated human `README.md` files, pass `target_filename="EGM.md"` unless explicitly updating `README.md`.

## 2. Explicit Review Criteria for Fresh-Context Goldfish Agents

Per EGM guidance (`docs/harnesses.md`), Goldfish reviewers (`egm-review` / `egm-brainstorm`) start with zero prior turn history. Any design brief or `/egm-review` prompt on `open-dv` must state applicable constraints explicitly as review criteria:

- **Zero LLM SQL (`OBJ-01`):** Agents emit/repair declarative contracts/IR only (`NoMistakeDataVault2ContractV5`, `CanonicalIR`); symbolic compilers generate DDL/DML.
- **Deterministic Hashing & DV 2.0 History (`FR-VER-01`, `INV-01..06`, `EVO-01..08`):** Length-prefixed collision-free hashing (`Algorithm A.5`), `BKCC`, insert-only Raw Vault, and additive-only evolution.
- **Shared Core & Layer DAG (`REQ-CORE-01`):** Application logic lives in `packages/opendv-service`; `opendv-cli` never imports API modules; no prod mocks or silent fallbacks (`REQ-MOCK-01`, `REQ-FAIL-01`).
- **3-Transport & Schema Parity:** `opendv` (Python CLI), `opendvctl` (Bun CLI), FastAPI REST/SSE (`:8420`), and React 19 Workbench align on `schemas/v1/cli-spec.json`, `schemas/v1/error-codes.json`, and `extra="forbid"` Pydantic DTOs.
- **Workbench & Module Ceilings:** Loopback open access (`D18`), agent-only UI (`D28`, no deterministic UI toggle), 5-Zone (`Z0–Z4` + `information_mart`) nomenclature (`DESIGN.md`), and `< 5,000` `cl100k_base` tokens per active module (`D16`).

## 3. Monorepo Package & App Map

- **Contracts & Core:** `packages/opendv-contracts`, `packages/opendv-core`, `packages/opendv-telemetry`
- **Ingestion & Verification:** `packages/opendv-sources`, `packages/opendv-axi-verifier`, `packages/opendv-duckdb-sandbox`
- **Agent Orchestration & Service:** `packages/opendv-agent-harness`, `packages/opendv-service`, `packages/opendv-testkit`
- **Clients, API & UI:** `packages/opendv-protocol-ts`, `apps/opendv-cli`, `apps/opendvctl`, `apps/opendv-platform-api`, `apps/opendv-web-workbench`

## 4. Verification Gates

Run from `open-dv/` before finalizing specs or implementations:

- Docs & architecture guards: `uv run pytest tests/docs/ tests/architecture/ -q`
- Full Python suite: `uv run pytest -q`
- TypeScript SDK, `opendvctl`, and Web Workbench: `bun test` and `bun x tsc -b --pretty`
