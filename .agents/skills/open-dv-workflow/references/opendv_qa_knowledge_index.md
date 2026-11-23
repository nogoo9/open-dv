# Open-DV Q&A Knowledge Index & Authoritative Grounding Map

When a user asks a question about **Open-DV**, run `opendv_workflow.py search-docs --query "<terms>" --limit 8 --output <out.json>` first and cite the exact files/sections rather than answering from memory.

---

## 1. Quick Topic-to-Source Index (in `open-dv`)

| Question Domain | Authoritative Repo Source(s) |
| :--- | :--- |
| **Visual Design Tokens, Typography, Chrome, 5-Zone Standard** | `DESIGN.md` (`#faf9f5` canvas, `#cc785c` coral, `Instrument Serif`/`DM Sans`/`JetBrains Mono`, `54px` workbench topbar) |
| **Locked Product Decisions (`D1`–`D28`) & Requirements (`FR-*`, `UAT-01..30`)** | `docs/27-unified-ontology-pipeline-requirements-and-uat.md` |
| **System Architecture, 12 Screen Specs & Ponytail Refinements (`P1`–`P5`)** | `docs/28-unified-ontology-pipeline-design-and-plan.md` |
| **End-User Walkthroughs, Asset Mgmt Starter Packs & Regulatory PDFs** | `docs/22-ontology-studio-user-guide.md`, `examples/sample_pdfs/README.md`, `examples/pipeline_samples/global_commerce_vault/` |
| **Human & Agent Dual-CLI Usage (`opendv` + `opendvctl`) & Modes** | `docs/16-human-and-agent-usage-guide.md`, `schemas/v1/cli-spec.json` |
| **REST Endpoints, Request/Response DTOs & Error Catalog** | `schemas/v1/openapi.json`, `docs/20-ontology-studio-spec.md`, `docs/21-ontology-studio-api-and-cli.md` |
| **DV 2.0 Test Scenarios, Golden Vectors (`GT-01..03`) & Datasets** | `docs/17-testing-scenarios-and-datasets.md` |
| **Ontology Studio Architecture, 24-Op Delta Calculus, CAS & Locks (`L2..L5`)** | `docs/19-ontology-studio-architecture.md`, `docs/23-ontology-studio-governance-and-security.md` |
| **Data-Driven Multi-Connector Pipeline Architecture** | `docs/24-data-driven-pipeline-architecture-and-spec.md`, `docs/25-data-driven-pipeline-requirements-and-uat.md` |

---

## 2. Core Open-DV Architectural Facts & Frequently Asked Answers

- **Stage Console (`00..06`) vs. Internal Pipeline Stages (`STAGE_00..07`)**:
  - `STAGE_00_ORCHESTRATION` (Preflight/Readiness) ➔ Pill `00`
  - `STAGE_01_SCOUT_SOURCE` (`GraphBProfiler`) ➔ Pill `01`
  - `STAGE_02_SCOUT_UNSTRUCTURED` (Unstructured `Alg A.9b` + Drift `UF-1..5`) ➔ Pill `02`
  - `STAGE_03_SHIP_VAULT_PASS1` (100% Column Triage `DV-G4-002`) + `STAGE_04_SHIP_VAULT_PASS2` (`@ship-vault` synthesis) ➔ Pill `03 Synth`
  - `STAGE_05_AXI_DV_STATIC_GATES` (Gates `0–4` + `Alg A.8` `@toon_v1` repair) ➔ Pill `04 Repair`
  - `STAGE_07_DETERMINISTIC_COMPILER` (`CanonicalIR` + 5-Zone `DuckDB` & `Databricks Delta` SQL) ➔ Pill `05 Lower`
  - `STAGE_06_AXI_DV_DYNAMIC_SANDBOX` (4-Batch `:memory:` DuckDB Prover `DV-DYN-001..015`) ➔ Pill `06 Prover`
  *(Log filters accept both `STAGE_00..07` and `00..06`.)*
- **5-Zone (`Z0–Z4`) Standard vs. `compiler_4zone`**:
  - UI & docs present 5 Zones (`Z0` Refs/Bronze, `Z1` Staging, `Z2` Raw Vault Hubs/Links/Sats, `Z3` Business Vault SAL/PIT/Bridge, `Z4` Information Mart); internal `compiler_4zone` module stays stable for CLI/parity tests.
- **Zero-Mention Unstructured Rules (`D4`) & Beyond-FIBO Concepts (`D3`)**:
  - Documents have zero occurrence-count pruning (`1x · Semantic` rules qualify); concepts may bind via FIBO Path A (`exactMatch`/`closeMatch >= 0.65`) or empirical Path B (`STRUCTURED_SCHEMA`/`DOCUMENT_OBSERVED` citations) with optional `skos:broadMatch`.
- **Dual Zero Keys, Ghost Record & Hash Standard (`Algorithm A.5`)**:
  - Every Hub includes Unknown (`'-1' -> '0'*64`) and Not-Applicable (`'-2' -> 'f'*64`); every Satellite includes Ghost Record (`'0'*64 @ 1900-01-01T00:00:00Z`).
  - Hash keys normalize via `LOWER(HEX(SHA256(OCTET_LENGTH(t1)||':'||t1 || '||' || ...)))` across Python, DuckDB, and Databricks (`SHA2(..., 256)`).
- **3-Tier Scoped `BKCC` (`KEY-04`)**:
  - Per `(table, column, target_hub)`: `TIER_1_GLOBAL_STANDARD` (`GLOBAL` passive convergence), `TIER_2_ENTERPRISE_SOR` (MDM system namespace), and `TIER_3_LOCAL_SCOPED` (isolated by source system or dynamic `tenant_id` column).
- **Common Error Codes**:
  - Exit **`40`** on `opendv serve --host 0.0.0.0`: Loopback-only guard (`D18`); bind `127.0.0.1` or pass `--allow-remote-unauthenticated`.
  - HTTP **`422 AGENT_NOT_CONFIGURED`**: Configure & verify a real provider (`agy` / `claude-cli`) in Step 4a / `Settings`.
  - HTTP **`409 SESSION_JOB_ACTIVE`** (CLI exit `20`): Active job/run already holds the session lock (`D27`); view live logs or cancel first.
  - HTTP **`412 Precondition Failed`**: Refresh session `head_sha256` (`If-Match`) or `expected_answers_sha256` (`ONT-EVO-003`), or rebase pending proposals.
  - HTTP **`501 OCR_UNAVAILABLE`**: `--ocr-mode force` requested without `tesseract` on `PATH`; install Tesseract or use `--ocr-mode auto|off`.
