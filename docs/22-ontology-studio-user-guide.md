# 22 — Open-DV Ontology Construction Studio: Comprehensive End-User Guide

**Document Status:** Normative User & Operational Guide  
**Milestone:** Phase 6 Wave 3 Integration & Release (Task 6C.1 / Issue #233)  
**Governing Documents:** `docs/19-ontology-studio-architecture.md`, `docs/20-ontology-studio-spec.md`, `docs/21-ontology-studio-implementation-plan.md`  
**Interface Dialects:** CLI Specification v1 (`schemas/v1/cli-spec.json`), OpenAPI 3.1.0 (`schemas/v1/openapi.json`), RFC 8785 JCS, RFC 9457 Problem Details  

---

## 1. Introduction & Architecture Overview

The **Open-DV Ontology Construction Studio** is an interactive, neuro-symbolic enterprise modeling environment designed to bridge raw unstructured organizational evidence (regulatory filings, contracts, schemata, policy documents) and formal Data Vault 2.0 conceptual ontologies (`GraphAOntology`).

Whether deployed as an embedded local developer tool or a multi-tenant platform service, the Studio enforces strict mathematical governance, cryptographic immutability, and deterministic reproducibility across the entire conceptual modeling lifecycle.

```
+----------------------------------------------------------------------------------------------------+
|                                 Open-DV Ontology Construction Studio                               |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|  [ Sources & Evidence ]                                      [ Dual-Mode Engine ]                  |
|  - PDF / MD / CSV / XSD       CAS Storage                    - Agent-First (3 Personas)            |
|  - LiteParse layout engine    .opendv/ontology_studio/       - Deterministic Fallback (< 2.5s)     |
|  - Character offset tracking   └── <session_id>/             - Curated FIBO Core Alignment         |
|                                                                                                    |
|                                       │                                      │                     |
|                                       ▼                                      ▼                     |
|  [ Clarification & Feedback ] ─────────────────────────────► [ Interactive Web Workbench ]         |
|  - Ambiguity Q&A cards                                       - Route: /ontology                    |
|  - Business rationale                                        - React Flow + elkjs (<300ms SLA)     |
|  - CAS immutable responses                                   - 3-tier Level-of-Detail (LOD)        |
|                                                              - Color-coded Diff Overlay            |
|                                                                                                    |
|                                       │                                      │                     |
|                                       ▼                                      ▼                     |
|  [ Additive Evolution ] ───────────────────────────────────► [ Multi-Format Export & Promotion ]   |
|  - 24-Op Delta Calculus                                      - YAML, JCS JSON, Turtle, JSON-LD, NT |
|  - ONT-EVO Governance                                        - Atomic Workspace Promotion          |
|  - Concurrency Control (OCC)                                 - Two-Phase Lock (L4/L5)              |
+----------------------------------------------------------------------------------------------------+
```

### 1.1 Dual-Mode Engine

The Studio features a dual-mode construction architecture that gracefully balances generative AI reasoning with zero-credential deterministic guarantees:

1. **Agent-First Mode (3-Persona LLM via `StudioAgentRunner`)**:
   - Orchestrates three specialized cognitive personas wired via the `StudioAgentRunner` runtime (`packages/opendv-service/src/opendv_service/studio/agent_runner.py`) using the platform harness adapter (`AgyCliHarnessAdapter`, default `agy`):
     - `@term-scout`: Fast extraction of candidate business concepts, attributes, and domain terminology from parsed evidence chunks.
     - `@domain-clarifier`: Detects ambiguous conceptual alignments against the normative Polysemy Catalog (`packages/opendv-contracts/src/opendv_contracts/catalogs/polysemy.py`) and synthesizes targeted clarification questions. The catalog disambiguates 18 financial terms across multiple industry meanings, including 8 core Asset Management terms (`portfolio`, `security`, `order`, `trade`, `fund`, `benchmark`, `mandate`, `instrument`) alongside cross-domain terms (`account`, `position`, `facility`, `party`, `yield`, `settlement`, `coupon`, `spread`, `collateral`, `nav`).
     - `@ontology-architect`: Reasoning-intensive synthesis of Canonical Business Concepts (CBCs), Links, Satellites, and formal OWL 2 axioms, guided by natural-language steering prompts.
   - Operates with strict data isolation: PII and sensitive fields are masked using keyed HMAC-SHA256 pseudonymization (`K_pseudo`).

2. **Deterministic Fallback Mode (Zero-Credential CI)**:
   - Runs in fully air-gapped environments without external LLM API keys or network dependencies.
   - Utilizes Okapi BM25 candidate retrieval (`bm25_fibo_v1`) against bundled FIBO reference packs (`fibo-core-pack.json`) paired with strict Path A `ONT-EVO-002` verification (`skos:exactMatch` or `skos:closeMatch` with confidence $\ge 0.6500$; `skos:broadMatch` closure excluded).
   - Guarantees complete execution in $< 2.5$ seconds with deterministic, byte-for-byte reproducible outputs.
   - Candidates lacking a verified Path A FIBO `skos:exactMatch` / `skos:closeMatch` mapping at confidence $\ge 0.6500$ (and touched grandfathered concepts whose exemption was revoked by mutation) are automatically routed to `evidence/staged_candidates.json`, preventing ungrounded pollution.

### 1.2 Content-Addressable Storage (CAS) Lifecycle

Every Studio session resides in an isolated directory structure under `.opendv/ontology_studio/<session_id>/`:

```
.opendv/ontology_studio/<session_id>/
├── session.json                      # Authoritative session state & active version pointer
├── evidence/
│   ├── manifest.json                 # Ingested sources index (binary PDFs, Markdown, free-form CAS text notes) and SHA-256 digests
│   ├── chunks.jsonl                  # Normalized text chunks and character offset spans
│   └── staged_candidates.json        # Low-confidence concepts (< 0.65 BM25 match) held back for Path A/B promotion
├── clarifications/
│   ├── questions.json                # Generated ambiguity questions
│   └── answers.json                  # Steward responses and business rationales
├── proposals/
│   └── <proposal_id>.json            # Pending candidate proposals with delta operations and steering_history
└── versions/
    ├── v0.0.0/                       # Genesis baseline draft
    │   └── ontology.json             # Default general namespace hash: 1916a5c6d501015984b8...
    └── v1.0.0/                       # First committed release
        ├── ontology.json             # Certified PE baseline hash: 1c100409cbbe12051f7211d2...
        └── delta.json                # Additive operations applied from genesis
```

> **Genesis `0.0.0` SHA-256 & Custom Namespaces:** `1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9` is the canonical RFC 8785 JCS SHA-256 digest of the default `0.0.0` `GenesisDraft` (`domain_namespace: "general"`, `enterprise_owner: "Enterprise Architecture"`). Custom greenfield namespaces (such as `--domain-namespace wealth_mgmt --enterprise-owner "Global Wealth Advisory"`, which deterministically evaluates to `9897c2a95f2987821979eeec92a96a14eb64af37b06222b993adbc717aff1142`) serialize their own namespace headers into `versions/v0.0.0/ontology.json`—always capture the authoritative `head_sha256` returned by `opendv ontology studio create` or `opendv ontology studio status` rather than hardcoding the default namespace digest.

### 1.3 Additive Deltas & ONT-EVO Contract Protection

Ontology versioning is governed by the closed 24-operation delta calculus and the three normative `ONT-EVO` invariants (`docs/20-ontology-studio-spec.md` §7.3):
- **ONT-EVO-001 (Immutability & Additive Delta Evolution / Deployed Table Guard)**: Committed ontology snapshots (`versions/vX.Y.Z/`) and their canonical RFC 8785 JCS SHA-256 digests are cryptographically immutable. Standard modeling evolves strictly via additive `OntologyDelta` operations (`MINOR`/`PATCH` SemVer bumps); destructive structural revisions (`remove_concept`, `remove_relationship`, `remove_attribute`, `update_concept_urn`, or overwriting an existing `elm_archetype` / `parent_concept_uri`) require `admin` privileges, trigger a `MAJOR` SemVer bump, and are unconditionally blocked (`ONT_EVO_CONTRACT_VIOLATION`, exit code `4`) whenever targeting deployed Data Vault physical tables.
- **ONT-EVO-002 (Path A FIBO Grounding & Waiver Integrity)**: Every newly added or mutated concept must either (a) carry at least one server-verified `FIBO` external mapping with predicate `skos:exactMatch` or `skos:closeMatch` (`skos:broadMatch` closure is strictly excluded) and numeric confidence $\ge 0.6500$, or (b) be covered by an explicit per-concept administrative waiver (`OPENDV_WAIVER_KEY`) with a $\ge 10$-character business rationale. Workspace-seeded concepts recorded in `grandfathered_concept_uris` are exempt while untouched, and immediately lose their grandfather exemption if touched by any of the 17 existing-concept-mutating operations (`18` total in `CONCEPT_MUTATING_OP_TYPES`, comprising `add_concept` plus `17` existing-concept-mutating ops including `update_relationship_endpoint`).
- **ONT-EVO-003 (Optimistic Concurrency Control, 24-Op Rebase & Lock Hierarchy)**: Every state-mutating request enforces cryptographic OCC preconditions (`If-Match` / `base_sha256`), supports deterministic proposal rebasing across the 24-operation conflict matrix, and acquires filesystem locks strictly in canonical order (`L1 .local_token.lock -> L2 .job_<id>.lock -> L3 .journal_<id>.lock -> L4 graph_a.lock -> L5 .session.lock`).

### 1.4 Asset Management Core Spine & Vertical Packs

The Asset Management ontology architecture provides a modular, composable taxonomy structured around a **Unified Core Enterprise Spine** and **6 Vertical Packs**, selectable individually or collectively via a 1-click **"All Domains"** composite preset:

1. **Unified Core Enterprise Spine (`core_spine`)**:
   - Establishes the 6 foundational Core Business Concepts (CBCs): `Investor`, `Fund`, `Portfolio`, `Security`, `Transaction`, and `LegalEntity`.
   - Defines primary ownership, custody, and holding relationship links common across all asset management lines of business.

2. **6 Composable Vertical Packs**:
   - **Public Equities & OMS (`public_equities_oms`)**: Models trade orders, market executions, broker routing, allocations, and settlement instructions.
   - **Fixed Income & Rates (`fixed_income_rates`)**: Encompasses yield curves, Macaulay duration, coupon schedules, benchmark rate spreads, and debt collateral.
   - **Multi-Asset IBOR (`multi_asset_ibor`)**: Covers Investment Book of Record positions, multi-asset valuations, cash flows, corporate actions, and accruals.
   - **Private Equity & Buyout (`private_equity_buyout`)**: Spans Limited Partner (LP) commitments, General Partner (GP) carried interest, capital call notices, distributions, and portfolio company buyout structures.
   - **Private Credit & Direct Lending (`private_credit_direct_lending`)**: Captures bespoke bilateral loan facilities, debt covenants, borrower compliance certificates, and variable interest margins.
   - **Custody & Clearing (`custody_clearing`)**: Tracks safekeeping custody accounts, depository clearinghouse netting, omnibus accounts, and segregation controls.

3. **1-Click "All Domains" Composite Preset (`all_domains`)**:
   - Combines the Core Spine and all 6 vertical packs into an integrated, enterprise-scale ontology with:
     - **31 Canonical Business Concepts (CBCs)**
     - **29 Canonical Relationships**
     - **7 Reference Enumerations**
   - Evaluates to canonical RFC 8785 JCS SHA-256 digest: `6cdd5c7c084cecfc0de6b7b82599b3bc50a1767aa4e9910866116d276638ead2`.

```python
from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_sources.asset_management.loader import AssetManagementStarterPackRegistry
from opendv_sources.asset_management.presets import get_all_domains_pack

# Inspect registered metadata
registry_packs = AssetManagementStarterPackRegistry.list_packs()
all_domains_meta = AssetManagementStarterPackRegistry.get_metadata(
    AssetManagementPackId.ALL_DOMAINS
)
assert all_domains_meta.concept_count == 31
assert all_domains_meta.relationship_count == 29

# Instantiate the full composite ontology directly
ontology = get_all_domains_pack()
assert len(ontology.canonical_business_concepts) == 31
assert len(ontology.canonical_relationships) == 29
assert len(ontology.reference_enumerations) == 7

# Canonical JCS SHA-256 digest verification
digest = compute_ontology_sha256(ontology)
assert len(digest) == 64
```

### 1.5 Wave 0 Unstructured-Only Architecture

Enterprise data architectures frequently ingest unstructured market research, analyst commentary, and SEC regulatory filings in an initial exploratory or cold-start phase prior to connecting relational trading platforms or enterprise data warehouses.

In standard Data Vault 2.0 pipelines, specifications lacking relational source tables trigger validation error `UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE`. To enable pure document ingestion without artificial dummy relational tables, Open-DV provides **Wave 0 Unstructured-Only Mode**:

- Enabled by setting `unstructured_only: true` in the pipeline YAML specification or passing `--unstructured-only` to `opendv pipeline run` and `opendv pipeline validate`.
- Relaxes the relational prerequisite (`seen_relational_sources == 0` allowed).
- Automatically synthesizes the central document anchor:
  - `hub_document`: Captures document business keys (SHA-256 digest, URI, filepath).
  - `sat_document_metadata`: Stores filename, byte size, MIME type, author, and ingestion timestamp.
  - `sat_document_extraction`: Houses full-text extracted paragraphs, token offsets, and AI extraction provenance.
  - `lnk_document_extraction`: Connects document chunks to downstream conceptual entity hubs.
- Ensures immediate compatibility with subsequent Wave 1 (OMS / IBOR) and Wave 2 (Private Credit / Custody) schema evolutions with zero breaking changes to existing tables.

---

## 2. Getting Started

### 2.1 Deployment Modes

The Studio operates in two primary modes configured via `OPENDV_SERVER_MODE`:

| Mode (`OPENDV_SERVER_MODE`) | Target User | Authentication | Network Binding | Storage |
|---|---|---|---|---|
| **Local Standalone** (`local_standalone`) | Individual modeler / data architect | `OPENDV_TOKEN` or auto-generated `.local_token` (DACL 0600) | Loopback only (`127.0.0.1`) | Local filesystem |
| **Remote Platform** (`remote_platform`) | Enterprise teams / centralized CI/CD | RS256 JWT / Bearer Token (`OPENDV_TOKEN`) / mTLS | External interface (`0.0.0.0`) | Managed server volume |

### 2.2 Launching the Web Workbench

To launch the integrated local Web Workbench:

```bash
# Start local Open-DV API and Web Workbench server
opendv serve --port 8420
```

Once running, open your modern browser (Chrome, Edge, Firefox, Safari) and navigate to:
```
http://127.0.0.1:8420/ontology
```

### 2.3 Dual CLI Configuration

Open-DV provides operational parity between the standalone Python CLI (`opendv`) and the Bun-compiled remote platform client (`opendvctl`):

```bash
# In-process Python CLI execution
opendv ontology studio list

# Loopback client execution (resolves OPENDV_STUDIO_TOKEN -> OPENDV_TOKEN -> .local_token)
export OPENDV_TOKEN="<your-loopback-or-platform-token>"
opendvctl ontology studio list --api-url http://127.0.0.1:8420

# Remote platform execution (non-loopback URLs ONLY read OPENDV_TOKEN to prevent leaking local tokens)
export OPENDV_TOKEN="<your-platform-jwt-or-bearer-token>"
opendvctl ontology studio list --api-url https://studio.enterprise.example

# Inspect available Asset Management starter packs via opendv
opendv ontology studio starter-pack list --session am-walkthrough

# Inspect available Asset Management starter packs via opendvctl
opendvctl ontology studio starter-pack list --session am-walkthrough --api-url http://127.0.0.1:8420
```

### 2.4 Representative Regulatory Sample PDFs & OCR Modes (`examples/sample_pdfs/`)

The repository ships a representative multi-document financial & regulatory corpus in [`examples/sample_pdfs/`](../examples/sample_pdfs/README.md) so you can immediately exercise PDF parsing, layout complexity detection, OCR mode routing (`auto`, `off`, `force`), and deterministic ontology construction without hunting for external filings:

| Document | Source | Pages | Size (Bytes) | Extracted Chunks | Detected Tables | Layout & OCR Profile |
|---|---|---:|---:|---:|---:|---|
| `nyfed_sr506_repo_infrastructure.pdf` | Federal Reserve Bank of New York — Staff Report No. 506 (*Tri-Party Repo Infrastructure*) | 55 | 449,963 | 67 | 0 | Clean multi-page vector prose (`Repo`, `Dealer`, `Collateral`, `ClearingBank`) |
| `fdic_section3_2_loans_credit_risk.pdf` | FDIC Risk Management Manual of Examination Policies — Section 3.2 (*Loans*) | 88 | 1,115,319 | 153 | 0 | Examination policies, underwriting standards, commercial loans & credit controls |
| `occ_comptrollers_handbook_rating_credit_risk.pdf` | OCC Comptroller's Handbook — *Rating Credit Risk* | 69 | 1,467,311 | 539 | 0 | Supervisory credit-rating guidance, facility collateral & allowance governance |
| `institutional_scanned_schedule_ocr_test.pdf` | Hybrid custody & repo schedule fixture | 2 | 6,352 | 2 | 1 | **Page 1:** clean vector + 3-row Markdown pipe collateral table (`is_complex=False`). **Page 2:** simulated scanned exhibit (`raster_ratio=0.6189 > 0.40`, `char_count=36 < 50`, `unmapped_glyphs=1` -> `is_complex=True`) |

All four files together yield **761 evidence chunks** and **1 structured Markdown table**, verified via `LiteParseDocumentAdapter` (`pypdf` fallback when optional `liteparse` CLI is absent) and `TesseractOCRRouter` (`packages/opendv-sources/src/opendv_sources/ingestion/layout_evaluator.py`):

- **`auto` (Recommended Default):** Scores every PDF page using character density (`char_count < 50`), embedded image area ratio (`raster_ratio > 0.40`), and unmapped glyph count (`unmapped_glyphs > 0`). Vector pages parse natively; complex pages (such as Page 2 of `institutional_scanned_schedule_ocr_test.pdf`) route to Tesseract OCR if installed on `PATH`, or gracefully fall back (`ocr_skipped=True`) when Tesseract is absent.
- **`off`:** Skips OCR invocation deterministically (`ocr_skipped=True` on complex pages) and extracts embedded PDF text and Markdown tables only.
- **`force`:** Fails closed with `OCRUnavailableError("OCR_UNAVAILABLE")` (`HTTP 501` in the Web Workbench / exit code `1` in the CLI) whenever the `tesseract` binary is unavailable on `PATH`, guaranteeing no silent partial extraction in strict OCR pipelines.

You can validate the entire four-PDF corpus from the CLI before or alongside a Web Workbench session:

```bash
# 1. Create a capital-markets session
opendv ontology studio create \
  --session regulatory-pdf-e2e-walkthrough \
  --domain-namespace capital_markets \
  --enterprise-owner "Institutional Risk Architecture"

# 2. Upload each regulatory PDF and hybrid scanned fixture
opendv ontology studio upload --session regulatory-pdf-e2e-walkthrough --file examples/sample_pdfs/nyfed_sr506_repo_infrastructure.pdf
opendv ontology studio upload --session regulatory-pdf-e2e-walkthrough --file examples/sample_pdfs/fdic_section3_2_loans_credit_risk.pdf
opendv ontology studio upload --session regulatory-pdf-e2e-walkthrough --file examples/sample_pdfs/occ_comptrollers_handbook_rating_credit_risk.pdf
opendv ontology studio upload --session regulatory-pdf-e2e-walkthrough --file examples/sample_pdfs/institutional_scanned_schedule_ocr_test.pdf

# 3. Parse with smart OCR auto-detection (761 chunks, 1 table)
opendv ontology studio parse --session regulatory-pdf-e2e-walkthrough --ocr-mode auto --wait

# 4. Optionally verify fail-closed OCR enforcement (requires tesseract on PATH)
opendv ontology studio parse --session regulatory-pdf-e2e-walkthrough --ocr-mode force --wait
```

See [`examples/sample_pdfs/README.md`](../examples/sample_pdfs/README.md) for SHA-256 digests and original source URLs.

---

## 3. End-to-End User Journeys

### 3.1 Journey 1: Greenfield Enterprise Modeling from Regulatory Filings

In this journey, a Financial Data Steward creates a brand-new Wealth Management Data Vault model directly from an SEC Form 10-K regulatory filing.

```
+───────────────────────────────────────────────────────────────────────────────────────────+
| Journey 1 Workflow: Regulatory Ingestion -> Clarification -> v1.0.0 Commit                 |
+───────────────────────────────────────────────────────────────────────────────────────────+
| 1. Create Session (wealth_mgmt v0.0.0 -> capture HEAD_SHA = 9897c2a9...)                  |
| 2. Upload Document (annual_report_2024.pdf) -> CAS Evidence Store                         |
| 3. Parse Layout -> Codepoint offset extraction & table detection                          |
| 4. Ambiguity Detection -> "Investment Advisor" vs "Fund Manager"                          |
| 5. Human Clarification -> Steward selects FIBO InvestmentAdvisor                          |
| 6. Neuro-Symbolic Construction -> 6 CBCs, 8 Links, 24 Satellites                          |
| 7. Proposal Acceptance -> Atomic Genesis bump v0.0.0 -> v1.0.0                            |
+───────────────────────────────────────────────────────────────────────────────────────────+
```

#### Step 1: Initialize Session & Capture Genesis `head_sha256`
Create a greenfield studio session initialized at `v0.0.0` and capture its deterministic RFC 8785 JCS `head_sha256` (for `wealth_mgmt` / `"Global Wealth Advisory"`, `head_sha256` evaluates to `9897c2a95f2987821979eeec92a96a14eb64af37b06222b993adbc717aff1142`; for default `general` / `"Enterprise Architecture"`, it evaluates to `1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9`):

```bash
opendv ontology studio create \
  --session wealth-sec-2024 \
  --domain-namespace wealth_mgmt \
  --enterprise-owner "Global Wealth Advisory" \
  --description "SEC 10-K wealth management baseline model"

HEAD_SHA=$(opendv ontology studio status --session wealth-sec-2024 --json | jq -r .head_sha256)
```

#### Step 2: Upload Source Document
Upload the annual report PDF into the session's Content-Addressable Storage (CAS):

```bash
opendv ontology studio upload \
  --session wealth-sec-2024 \
  --file annual_report_2024.pdf \
  --description "FY2024 SEC Form 10-K Filing"
```

#### Step 3: Trigger Ingestion & Parsing
Execute asynchronous layout analysis and text normalization:

```bash
opendv ontology studio parse \
  --session wealth-sec-2024 \
  --ocr-mode auto \
  --wait
```

#### Step 4: Resolve Clarifications
The `@domain-clarifier` detects that *"Fund Advisor"* could align to either `fibo-fbc-fct-fse:InvestmentAdvisor` or `fibo-be-oac-cctl:FundManager`. In the Web Workbench or CLI, answer the question using `ClarificationAnswerRequest` (`selected_options`, `user_text`, and `expected_answers_sha256` OCC precondition):

```json
{
  "answers": [
    {
      "question_id": "q-advisor-align-01",
      "selected_options": ["opt-investment-advisor"],
      "user_text": "Acts as registered investment advisor under SEC regulations."
    }
  ],
  "expected_answers_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
}
```

Submit the answer file:

```bash
opendv ontology studio answer \
  --session wealth-sec-2024 \
  --file advisor_answer.json
```

#### Step 5: Construct Ontology Proposal
Invoke the construction engine against the session's `HEAD_SHA` (`9897c2a95f2987821979eeec92a96a14eb64af37b06222b993adbc717aff1142`, transmitted as `If-Match: "$HEAD_SHA"` and `"base_sha256": "$HEAD_SHA"` over REST):

```bash
opendv ontology studio construct \
  --session wealth-sec-2024 \
  --base-version 0.0.0 \
  --base-sha256 "$HEAD_SHA" \
  --mode agent \
  --wait
```

#### Step 6: Accept Proposal & Commit v1.0.0
Review the generated proposal (`prop-8821`) and accept it against `HEAD_SHA` (`If-Match: "$HEAD_SHA"` / `"parent_sha256": "$HEAD_SHA"`) to perform the genesis commit:

```bash
opendv ontology studio proposal accept \
  --session wealth-sec-2024 \
  --proposal-id prop-8821 \
  --base-sha256 "$HEAD_SHA"
```

The session transitions from `v0.0.0` to `v1.0.0`.

---

### 3.2 Journey 2: Evolutionary Workspace Modeling & Promotion

In this journey, an Enterprise Architect seeds a session from an existing Private Equity Data Vault project (`examples/private_equity`), adds Limited Partner commitment semantics, and promotes the updated model back to the workspace pipeline.

#### Step 1: Seed Session from Workspace
Seed a new studio session from the existing workspace project:

```bash
opendv ontology studio create \
  --session pe-evolution \
  --domain-namespace private_equity \
  --from-workspace examples/private_equity \
  --session-label "PE Limited Partner Additions"
```

The session automatically loads `v1.0.0` with baseline canonical hash `1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81`. Existing URIs are registered in `grandfathered_concept_uris` and remain exempt from `ONT-EVO-002` re-grounding unless mutated by one of the 17 existing-concept-mutating delta operations (`18` total in `CONCEPT_MUTATING_OP_TYPES`).

#### Step 2: Upload Contract Addendum & Apply Delta
Upload the partnership agreement addendum and apply an additive `lp_delta.json` linking the new concept to the existing `urn:ontology:entity:fund` concept:

```json
{
  "operations": [
    {
      "op_id": "op-lp-add-concept-01",
      "op_type": "add_concept",
      "concept_uri": "urn:ontology:entity:limited_partner_commitment",
      "concept_name": "Limited Partner Commitment",
      "definition": "Capital commitment pledged by a limited partner to a private equity fund.",
      "target_hub": "hub_lp_commitment",
      "elm_archetype": "CORE_BUSINESS_CONCEPT"
    },
    {
      "op_id": "op-lp-add-attr-02",
      "op_type": "add_attribute",
      "concept_uri": "urn:ontology:entity:limited_partner_commitment",
      "attribute_name": "commitment_amount",
      "data_type": "decimal"
    },
    {
      "op_id": "op-lp-add-rel-03",
      "op_type": "add_relationship",
      "relationship_uri": "urn:ontology:relationship:fund_has_lp_commitment",
      "relationship_name": "Fund Has LP Commitment",
      "target_link": "lnk_fund_lp_commitment",
      "endpoints": [
        "urn:ontology:entity:fund",
        "urn:ontology:entity:limited_partner_commitment"
      ],
      "grain_semantics": "One commitment record per Limited Partner subscription in a Fund."
    },
    {
      "op_id": "op-lp-add-map-04",
      "op_type": "add_external_mapping",
      "concept_uri": "urn:ontology:entity:limited_partner_commitment",
      "scheme": "FIBO",
      "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractualCommitment",
      "predicate": "skos:exactMatch",
      "mapping_confidence": 0.8200
    }
  ]
}
```

Apply the delta operations to the session:

```bash
opendv ontology studio delta apply \
  --session pe-evolution \
  --file lp_delta.json \
  --base-version 1.0.0 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --rationale "Add Limited Partner capital commitment conceptual modeling"
```

This atomically creates committed snapshot `v1.1.0`.

#### Step 3: Promote to Workspace Pipeline
Promote the updated ontology directly into the workspace Data Vault pipeline:

```bash
opendv ontology studio promote \
  --session pe-evolution \
  --workspace examples/private_equity \
  --workspace-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --head-sha256 5e89a12c4b3f890123456789abcdef0123456789abcdef0123456789abcdef01 \
  --rationale "Promote v1.1.0 LP commitment concepts to production pipeline"
```

The two-phase promotion protocol acquires workspace lock `graph_a.lock`, validates containment, updates the workspace YAML, and records the audit trail.

---

### 3.3 Journey 3: Air-Gapped Zero-Credential CI Pipeline

In automated continuous integration environments where no internet access or LLM credentials exist, the Studio executes deterministically in $< 2.5$ seconds:

```bash
# Seed session from workspace in CI runner
opendv ontology studio create \
  --session ci-verify-run \
  --domain-namespace private_equity \
  --from-workspace examples/private_equity

# Run deterministic construction with auto-commit
opendv ontology studio construct \
  --session ci-verify-run \
  --base-version 1.0.0 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --mode deterministic \
  --auto-commit
```

- If no new inputs exist, the job emits `NOOP_CONSTRUCT` in the audit log and exits cleanly with code `0`.
- All grounding rules and OCC invariants are mathematically verified.

---

### 3.4 Journey 4: Human-in-the-Loop Clarification & Administrative Waivers

When a domain concept cannot satisfy `ONT-EVO-002` Path A FIBO grounding (`skos:exactMatch` or `skos:closeMatch` with confidence $\ge 0.6500$; `skos:broadMatch` is excluded—for example, proprietary internal instruments), an Enterprise Architect can sign a per-concept administrative grounding waiver using `OPENDV_WAIVER_KEY`:

```bash
export OPENDV_WAIVER_KEY="<admin-waiver-secret>"
HEAD_SHA=$(opendv ontology studio status --session wealth-sec-2024 --json | jq -r .head_sha256)
opendv ontology studio proposal accept \
  --session wealth-sec-2024 \
  --proposal-id prop-9902 \
  --base-sha256 "$HEAD_SHA" \
  --low-confidence-waiver \
  --grounding-rationale "Proprietary bespoke investment vehicle authorized under Board Resolution 2026-B" \
  --ungrounded-concept "urn:ontology:entity:bespoke_mezzanine_fund"
```

Administrative waivers require:
1. `low_confidence_waiver=True`
2. `grounding_rationale` of at least 10 characters detailing the governance reason.
3. Explicit per-concept list of `ungrounded-concept` URIs covered by the waiver (subsequent mutations to any waived or grandfathered concept revoke its exemption).
4. Authenticated `OPENDV_WAIVER_KEY` credential and `admin` role authorization (missing/invalid credentials or non-admin callers are denied with exit code `7` `FORBIDDEN_ADMIN_REQUIRED`).

---

### 3.5 Journey 5: End-to-End Asset Management Lifecycle (From Starter Pack to 4-Zone Pipeline)

In this journey, an Asset Management Data Architect establishes an institutional Data Vault 2.0 architecture spanning public equities, fixed income rates, private credit lending covenants, and unstructured SEC filings:

```
+───────────────────────────────────────────────────────────────────────────────────────────+
| Journey 5: Starter Pack -> Text Ingestion -> Steering -> Promotion -> DV 2.0 Pipeline      |
+───────────────────────────────────────────────────────────────────────────────────────────+
| 1. Apply Starter Pack: 1-Click 'all_domains' Preset (31 CBCs, 29 Rels, 7 Enums)           |
| 2. Ingest Free-Form CAS Notes: Analyst Commentary & Covenant Summaries (SHA-256 CAS)      |
| 3. Polysemy Disambiguation: 3-Persona Agent Runner resolves 'portfolio', 'security', etc.  |
| 4. Iterative Steering Refinement: Refine proposal with natural language steering prompt    |
| 5. Staged Candidate Promotion: Path A (synonyms >= 0.65) & Path B (admin signed waiver)    |
| 6. DV 2.0 Blueprint Inspection: Preview & export Zone 2 Raw Vault DDL and VaultBlueprint v1.0 |
| 7. 1-Click Hand-Off: Deep-link to 4-Step Pipeline Builder Wizard                              |
| 8. Execute Single-Run Pipeline: Multi-connector data-driven execution (SQLite & Databricks)  |
| 9. Parity Verification: Verify identical results across opendv and opendvctl                  |
+───────────────────────────────────────────────────────────────────────────────────────────+
```

#### Step 1: Initialize Session & Apply 1-Click "All Domains" Starter Pack
Create an asset management session and immediately apply the composite `all_domains` preset, bootstrapping 31 CBCs, 29 relationships, and 7 enums in a single operation:

```bash
# Initialize Asset Management studio session
opendv ontology studio create \
  --session am-fund \
  --domain-namespace asset_management \
  --enterprise-owner "Global Asset Management Architecture" \
  --description "Comprehensive Asset Management enterprise model"

# Apply 1-Click "All Domains" starter pack via opendv
opendv ontology studio starter-pack apply --session am-fund --pack all_domains

# Equivalent execution via opendvctl
opendvctl ontology studio starter-pack apply --session am-fund --pack all_domains --api-url http://127.0.0.1:8420
```

The session instantly stages an initial delta resulting in canonical SHA-256 digest `6cdd5c7c084cecfc0de6b7b82599b3bc50a1767aa4e9910866116d276638ead2`.

Over REST, this corresponds to:
```bash
curl -s -X POST "http://127.0.0.1:8420/api/v1/ontology/studio/sessions/am-fund/starter-packs/apply" \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "pack_id": "all_domains"
  }'
```

#### Step 2: Ingest Free-Form Analyst Notes into CAS
Ingest unstructured analyst commentary and debt covenant notes directly into Content-Addressable Storage (CAS):

```bash
# Ingest inline analyst note (opendv)
opendv ontology studio text add --session am-fund --text "Private Credit Direct Lending facility agreement with borrower covenant monitoring and variable SOFR spread." --source-name direct_lending_notes.txt

# Ingest inline analyst note (opendvctl)
opendvctl ontology studio text add --session am-fund --text "Private Credit Direct Lending facility agreement with borrower covenant monitoring and variable SOFR spread." --source-name direct_lending_notes.txt --api-url http://127.0.0.1:8420

# Ingest multi-page research memo from file (opendv)
opendv ontology studio text add --session am-fund --file examples/asset_management/docs/sec_filing_sample.md --source-name sec_filing.md

# Ingest multi-page research memo from file (opendvctl)
opendvctl ontology studio text add --session am-fund --file examples/asset_management/docs/sec_filing_sample.md --source-name sec_filing.md --api-url http://127.0.0.1:8420
```

CAS hashes the ingested payloads, generates cryptographic SHA-256 provenance identifiers (`cas:text`), and records source metadata in `.opendv/ontology_studio/am-fund/evidence/manifest.json`.

#### Step 3: 3-Persona Agent Runtime & Polysemy Disambiguation
Invoke `StudioAgentRunner` to orchestrate `@term-scout`, `@domain-clarifier`, and `@ontology-architect`:

```bash
# Execute 3-persona construction with steering guidance (opendv)
opendv ontology studio construct --session am-fund --mode agent --steering-prompt "Ground all securities against FIBO SEC and link to CustodianAccount"

# Equivalent execution via opendvctl
opendvctl ontology studio construct --session am-fund --mode agent --steering-prompt "Ground all securities against FIBO SEC and link to CustodianAccount" --api-url http://127.0.0.1:8420
```

During execution, `@domain-clarifier` consults `KNOWN_POLYSEMIC_DEFINITIONS` to disambiguate overloaded industry terms:
- `portfolio`: Disambiguates between *Securities Portfolio* (`fibo-fbc-pas-fpas:Portfolio`), *Fund Portfolio Account*, and *Structured Credit Collateral Pool*.
- `security`: Disambiguates *Negotiable Financial Security* (`fibo-fnd-oac-cctl:Security`) from *Collateral Pledge* and *Information Security*.
- `benchmark`: Disambiguates *Performance Index* from *Interest Rate Benchmark*.

Clarification answers are submitted via `opendv ontology studio answer`, enabling `@ontology-architect` to synthesize proposal `prop-am-01`.

#### Step 4: Iterative Natural-Language Steering Prompt Refinement
Refine the pending proposal iteratively using natural language instructions without crafting raw delta JSON by hand:

```bash
# Refine proposal with steering prompt (opendv)
opendv ontology studio proposal refine --session am-fund --proposal-id prop-am-01 --steering-prompt "Include ISIN and CUSIP attributes on Security and add link to CustodianAccount" --mode deterministic

# Refine proposal with steering prompt (opendvctl)
opendvctl ontology studio proposal refine --session am-fund --proposal-id prop-am-01 --steering-prompt "Include ISIN and CUSIP attributes on Security and add link to CustodianAccount" --mode deterministic --api-url http://127.0.0.1:8420
```

The refinement endpoint marks `prop-am-01` as `SUPERSEDED`, generates a fresh proposal `prop-refine-02`, and preserves the instruction in `steering_history`.

#### Step 5: Held-Back Candidate Promotion (Path A & Path B)
Candidate concepts whose initial FIBO BM25 similarity score is $< 0.6500$ are automatically segregated into `evidence/staged_candidates.json`. Stewards promote them via two governance paths:

```bash
# List staged candidate concepts held back due to low confidence (opendv)
opendv ontology studio staged list --session am-fund

# List staged candidate concepts (opendvctl)
opendvctl ontology studio staged list --session am-fund --api-url http://127.0.0.1:8420

# Path A: Promote via Synonym Re-Scoring (score must reach >= 0.6500)
opendv ontology studio staged promote --session am-fund --candidate-id cand-holding-debt-001 --synonyms "NegotiableDebtSecurity, CorporateDebtBond"

# Path A via opendvctl
opendvctl ontology studio staged promote --session am-fund --candidate-id cand-holding-debt-001 --synonyms "NegotiableDebtSecurity, CorporateDebtBond" --api-url http://127.0.0.1:8420

# Path B: Promote via Admin Signed Waiver (requires admin role & >=10 char rationale)
export OPENDV_WAIVER_KEY="secret-admin-hmac-key"
opendv ontology studio staged promote --session am-fund --candidate-id cand-bespoke-002 --waiver --rationale "Authorized proprietary credit facility per board charter"

# Path B via opendvctl
opendvctl ontology studio staged promote --session am-fund --candidate-id cand-bespoke-002 --waiver --rationale "Authorized proprietary credit facility per board charter" --api-url http://127.0.0.1:8420
```

#### Step 6: DV 2.0 Blueprint Inspection & VaultBlueprint Export
In the Web Workbench at `/ontology`, open the right drawer and select the **DV 2.0 Blueprint** tab (`data-testid="right-tab-blueprint"`):
1. Review the generated Target Raw Vault tables:
   - Hubs: `hub_security`, `hub_investor`, `hub_rate_index`, `hub_document`.
   - Links: `lnk_portfolio_holding`, `lnk_document_extraction`.
   - Satellites: `sat_security_details`, `sat_investor_details`, `sat_document_metadata`.
2. Click **Copy DDL** (`btn-copy-blueprint-ddl`) or **Download DDL** (`btn-download-blueprint-ddl`) to obtain `final_four_zone_am.sql`, containing 16 validated tables spanning `staging`, `raw_vault`, `business_vault`, and `marts`.
3. Export the unified `VaultBlueprint` v1.0 YAML envelope via REST endpoint:
   ```bash
   curl -s -X GET "http://127.0.0.1:8420/api/v1/ontology/studio/sessions/am-fund/export-blueprint" \
     -H "Authorization: Bearer dev-token" \
     -o artifacts/vault_blueprint.yaml
   ```

#### Step 7: 1-Click Hand-Off to 4-Step Pipeline Builder Wizard
Click **Open in Pipeline Builder ->** (`data-testid="btn-open-in-pipeline-builder"`). The Web Workbench automatically routes to:

```
/builder?session_id=am-fund&source=ontology_studio&domain_namespace=asset_management&pipeline_id=pl_am_fund
```

`WizardShell.tsx` and `specBuilderUtils.ts` parse the URL parameters, load target Hubs and Business Keys from the session ontology, and auto-populate Step 1 (Foundation) without manual re-entry.

#### Step 8: Single-Run Data-Driven Pipeline Execution
Execute the end-to-end data-driven pipeline using the exported `VaultBlueprint` envelope and multi-connector tabular sources:

```bash
# Validate single-run pipeline with VaultBlueprint envelope (opendv)
opendv pipeline validate --blueprint artifacts/vault_blueprint.yaml --source "data/public_am/am_sec_treasury_ibor.sqlite" --dialect duckdb

# Validate single-run pipeline with VaultBlueprint envelope (opendvctl)
opendvctl pipeline validate --blueprint artifacts/vault_blueprint.yaml --source "data/public_am/am_sec_treasury_ibor.sqlite" --dialect duckdb --api-url http://127.0.0.1:8420

# Execute single-run data-driven pipeline targeting DuckDB (opendv)
opendv pipeline run --blueprint artifacts/vault_blueprint.yaml --source "data/public_am/am_sec_treasury_ibor.sqlite" --dialect duckdb

# Execute single-run data-driven pipeline targeting Databricks Delta (opendv)
opendv pipeline run --blueprint artifacts/vault_blueprint.yaml --source "uc://am_prod/ibor" --dialect databricks

# Execute single-run data-driven pipeline (opendvctl)
opendvctl pipeline run --blueprint artifacts/vault_blueprint.yaml --source "data/public_am/am_sec_treasury_ibor.sqlite" --dialect duckdb --api-url http://127.0.0.1:8420
```

#### Step 9: Verifying Dual-CLI Parity
Verify identical execution and output parity across both CLI environments:
- Identical JSON schemas and exit codes (`0` for success).
- Shared canonical validation against `schemas/v1/cli-spec.json`.
- Complete interoperability for local scripting (`opendv`) and containerized remote automation (`opendvctl`).

### 3.6 Multi-Connector Catalog Sources (SQLite CAS Upload & Databricks Unity Catalog)

Open-DV provides pluggable connector support via the `CatalogSourceAdapter` SPI (`opendv-sources`), enabling single-run data-driven execution across diverse tabular backends:

#### Multi-Table SQLite with Client CAS Upload
Local SQLite database files (`*.sqlite`, `*.db`) support automatic schema discovery via `PRAGMA table_info` and foreign key analysis via `PRAGMA foreign_key_list`. When executing against a remote platform instance, SQLite sources declare `client_uploadable=True`, uploading the binary database directly into CAS:

```bash
# Inspect SQLite catalog structure (opendv)
opendv source inspect --uri "data/public_am/am_sec_treasury_ibor.sqlite" --adapter sqlite

# Inspect SQLite catalog structure (opendvctl)
opendvctl source inspect --uri "data/public_am/am_sec_treasury_ibor.sqlite" --adapter sqlite --api-url http://127.0.0.1:8420
```

#### Databricks Unity Catalog
Databricks Unity Catalog connects via `databricks://` or `uc://` 3-level naming (`catalog.schema.table`):
1. **Introspection**: Automatically queries `information_schema.tables`, `columns`, `table_constraints`, `key_column_usage`, and `column_tags`.
2. **Pushdown Profiler**: Executes remote aggregate profiling inside Databricks SQL warehouse to extract min/max/null counts without moving raw data.
3. **Air-Gapped Local Testing**: `DuckDBUnityCatalogEmulator` in `opendv-testkit` simulates the entire Unity Catalog hierarchy, Delta table metadata, and PyArrow record batch streaming locally without requiring live Databricks credentials.

```bash
# Inspect Databricks Unity Catalog table (opendv)
opendv source inspect --uri "uc://am_prod/ibor" --adapter databricks --table security_master

# Inspect Databricks Unity Catalog table (opendvctl)
opendvctl source inspect --uri "uc://am_prod/ibor" --adapter databricks --table security_master --api-url http://127.0.0.1:8420
```

---

---

## 4. Interactive Web Workbench Walkthrough (`/ontology`)

The Open-DV Web Workbench provides a visual, reactive single-page application at `/ontology`. The screenshots below walk step-by-step through a real live `1440x900` session (`regulatory-pdf-e2e-walkthrough`, domain `capital_markets`) built from all four PDFs in [`examples/sample_pdfs/`](../examples/sample_pdfs/README.md):

```
+───────────────────────────────────────────────────────────────────────────────────────────────────+
| Open-DV Workbench  [Session: pe-evolution ▼]  [State: COMMITTED]  [Active: v1.1.0] [Token: Valid] |
+───────────────────────┬─────────────────────────────────────────────────┬─────────────────────────+
| SOURCES & PARSE       | REACT FLOW + ELKJS GRAPH EXPLORER               | VISUAL EDITOR & DIFF    |
|                       |                                                 |                         |
| [Upload Files (+)]    |  ┌──────────────────┐     ┌──────────────────┐  | Selected: Hub LP Commit |
| 📄 10k_sec_2024.pdf   |  |   hub_fund       |────►| hub_lp_commitment|  |                         |
|   12 chunks | CONFID  |  └──────────────────┘     └──────────────────┘  | Archetype: State        |
|                       |            │                 ▲ (Green: Added)   | Target Hub: hub_lp_com  |
| 📄 lp_addendum.md     |            ▼                 │                  |                         |
|   4 chunks | INTERNAL |  ┌──────────────────┐        │                  | Attributes:             |
|                       |  | hub_investor     |────────┘                  | - commitment_amount     |
| [Parse Status: OK]    |  └──────────────────┘                           | - pledge_date           |
|                       |                                                 |                         |
+───────────────────────┼─────────────────────────────────────────────────┼─────────────────────────+
| CLARIFICATION Q&A     | LEVEL OF DETAIL (LOD): [ LOD-1 | LOD-2 | LOD-3 ] | QUALITY & EXPORT        |
|                       |                                                 |                         |
| Q: Align "Advisor"?   | Layout SLA: 142ms (< 300ms)                     | Score: 100% Validated   |
| (•) InvestmentAdvisor | Zoom: 85% | Nodes: 6 | Edges: 8                 | Grounding: 0.8200 (OK)  |
| ( ) FundManager       |                                                 | [Export OWL/TTL]        |
| [Submit Response]     |                                                 | [Promote to Workspace]  |
+───────────────────────┴─────────────────────────────────────────────────┴─────────────────────────+
```

### 4.1 Local Launch, Authentication & RBAC Hydration
- **Recommended Launch (`opendv serve --open`)**: Starts the local loopback server (`127.0.0.1`) and opens `/ontology#token=<nonce>`. The Web Workbench automatically exchanges the launch nonce via `POST /api/v1/ontology/studio/auth/token`, sets an `HttpOnly` cookie, scrubs the URL fragment from browser history, and hydrates your exact RBAC role (`admin`, `editor`, `viewer`, or `automated`) from `TokenExchangeResponse.role` and `X-OpenDV-Role` response headers.
- **Manual Launch & HTTP `409` (`STUDIO_ALREADY_INITIALIZED`) Guidance**: If an instance previously initialized `.opendv/ontology_studio/.local_token`, clicking automatic loopback bootstrap returns HTTP `409`. The Workbench focuses the token input and displays inline instructions telling you either to paste the token stored in `.opendv/ontology_studio/.local_token` or relaunch with `opendv serve --open`.
- **RBAC Role Badge**: Displays the active authenticated role (`admin`, `editor`, `viewer`, or `automated`; unauthenticated local store state initializes to `editor` until `/init`, `/auth/token`, or `X-OpenDV-Role` hydrates your server role). Read-only `viewer` and `automated` roles disable manual edit controls, while waivers, destructive deletions, and promotion require `admin`.

### 4.2 Session Switcher & Recency Ordering
- **Session Switcher**: Switch instantly between active sessions or create a new session with a workspace path (`--workspace`) or greenfield namespace.
- **Most-Recent-First Ordering**: Backend `list_sessions()` sorts sessions by `(updated_at or created_at, session_id)` descending, ensuring newly created and recently edited sessions always appear at the top of the drawer even when more than 50 historical sessions exist.
- **State Badges**: Color-coded lifecycle indicators (`EMPTY`, `INGESTED`, `CLARIFYING`, `CONSTRUCTED`, `COMMITTED`, `PROMOTED`):
  - `EMPTY`: Initialized without committed versions (`v0.0.0`).
  - `INGESTED` / `CLARIFYING` / `CONSTRUCTED`: Active ingestion, ambiguity Q&A, or candidate proposal synthesis in progress.
  - `COMMITTED`: Version committed in session CAS (including sessions initialized from an existing workspace baseline).
  - `PROMOTED`: Head version promoted to workspace Data Vault.

### 4.3 Sources Panel, OCR Modes & Chunk Inspector (Stages 1–2)
- **Drag-and-Drop Uploader**: Supports `.pdf`, `.docx`, `.xlsx`, `.html`/`.htm`, `.json`, `.csv`, `.md`/`.markdown` (including browsers that send `application/octet-stream` for Markdown), and `.txt` up to the `100 MiB` (`STUDIO_MAX_UPLOAD_BYTES`) per-file upload cap.
- **OCR Mode Toggle**: `Auto`, `Force OCR`, and `Off`. Selecting `Force OCR` without Tesseract installed fails closed with `HTTP 501 OCR_UNAVAILABLE`, whereas `Auto` and `Off` extract vector pages and flag scanned/complex pages (`COMPLEX`, `ocr_skipped=True`) cleanly.
- **Confidentiality Taint (`do_not_send_to_agent`)**: Mark sensitive documents via per-document **Privacy** classification (`public`, `internal`, `confidential`, `restricted`) to enforce redaction and taint tracking across CAS chunks.
- **Chunk Inspector Drawer**: After parsing completes, inspect extracted evidence snippets with page/source headers, layout complexity badges (`SIMPLE`, `TABULAR`, `COMPLEX`), UTF-8 character offsets, Markdown tables, and token counts. For multi-document corpora, the workbench reports the full CAS total (for example, `761 chunks extracted`) while displaying a balanced preview sample (`Extracted Evidence Chunks (20)`) across all uploaded files so long multi-hundred-page filings stay responsive.

![Stage 1 — Sources Panel, Privacy Classifications & OCR Mode Toggle](assets/ontology-studio/01-sources-and-ocr-mode.png)
*Stage 1: All four sample regulatory PDFs uploaded into `regulatory-pdf-e2e-walkthrough` inside the `380px` Sources drawer alongside the `Privacy` taint selector and `OCR Mode` (`Auto` / `Force OCR` / `Off`) control prior to parsing.*

![Stage 2 — Parsed Regulatory PDF Evidence & Chunk Inspector](assets/ontology-studio/02-chunk-inspector-parsed-pdfs.png)
*Stage 2: Completion status (`Parsing complete (761 chunks extracted)`) and the balanced `Extracted Evidence Chunks (20)` preview featuring layout complexity badges (`COMPLEX` on Page 2 of the scanned exhibit fixture), token counts, and preserved Markdown table syntax.*

### 4.4 Clarification Q&A & Proposal Ratification (Stage 3)
- **Ambiguity Questions**: Presents domain questions generated by the clarification step with certainty badges (e.g., `~79%`), candidate options, rationale notes, and scrollable source citations (`Citations (N)` inside a compact overflow-safe container), persisting answers via CAS (`clarifications/answers.json` + `If-Match`).
- **Construct & Proposal List**: Triggers construction over extracted tables and regulatory prose chunks, showing each candidate proposal's operation count (`102 ops` badge for the 4-PDF corpus) and status (`PENDING`, `ACCEPTED`, `REJECTED`). Click **`Manage in Right Panel`** in the Q&A tab, **`Proposals (N)`** in the top toolbar, or **`View Details`** on any proposal card to open the full right-panel stewardship workspace.
- **Two-Tab Right Workspace (`Ontology Viewer` | `Proposal Details`)**: The right panel pairs **`Ontology Viewer (N)`** (interactive React Flow graph + Visual Concept Editor) and **`Proposal Details (N)`** as persistent tabs (`✕ Return to Ontology Viewer` also returns you to the canvas). Switching tabs preserves your active `selectedProposalId`, operation search filter, and any unsaved proposal draft edits.
- **Full Proposal Lifecycle in `Proposal Details`**:
  - **What is a Candidate Proposal (`OntologyProposal`)?** An inline guide explains how Open-DV stages additive ontology deltas in CAS prior to human stewardship ratification, including `ONT-EVO-002` Path A grounding (`skos:exactMatch` / `skos:closeMatch`, whereas `skos:broadMatch` does not satisfy Path A alone).
  - **Inline Q&A, Authoring & Operation Editing**: Expand or collapse (**Show Q&A** / **Hide Q&A**) to answer Domain Clarifications inline, click **`+ New Draft Proposal`** to draft a proposal from scratch, or click **`Edit`** / **`+ Add Concept Op`** / **`+ Add Mapping Op`** to modify rationale and delta operations (`add_concept`, `add_external_mapping`, `add_attribute`, `add_synonym`, `refine_definition`, `revise_description`), using **`Edit Node`** to inspect target concepts directly in the Concept Editor.
  - **Unsaved-Edit Ratification Lock & Ratification**: While local proposal edits are unsaved, **`Accept Proposal`** and **`Rebase`** remain disabled with an inline reminder until you click **`Save Edited Proposal`** (persisting a new `PENDING` CAS revision) or **`Reset Edits`**. Once clean, ratify `PENDING` revisions via **`Accept Proposal`** (with optional Admin `self_acceptance_override` and `low_confidence_waiver`), **`Rebase`**, or **`Reject`**.
- **Zero-Op Proposal Guard (`0 ops`)**: When a proposal contains `0` operations (for example, constructed before source documents were uploaded and parsed), the **Accept** button is automatically disabled with an inline reminder (`"0 operations — upload and parse source documents first"`), while **Reject** remains enabled so you can clean up empty proposals.

![Stage 3 — Clarification Q&A & Domain Ontology Proposal](assets/ontology-studio/03-clarifications-and-proposals.png)
*Stage 3: Resolved Domain Ambiguity Questions with compact scrollable citations and pending candidate proposal `prop-bd4a8c90b20e62e5` (`102 ops`, `PENDING`) ready for ratification via `Accept` or `Reject`.*

![Stage 3b — Right-Panel Proposal Details Workspace & Two-Tab Switcher](assets/ontology-studio/08-proposal-details-right-panel.png)
*Stage 3b: Persistent two-tab Right Panel switched to `Proposal Details (3)`, showing the `Proposals (3)` entry button, Candidate Proposal explanation card, collapsible Domain Clarifications summary (`3 Pending · 1 Answered`), and Lineage & Ratification controls alongside `Ontology Viewer (24)`.*

### 4.5 React Flow + elkjs Graph Canvas & Visual Editor Drawer (Stage 4)
- **Dark-Mode Graph Canvas**: Powered by `@xyflow/react` and `elkjs` (`< 300 ms` layout SLA — observed `76.7 ms` for 24 regulatory concepts) featuring high-contrast dark zoom/fit/lock controls and minimap.
- **3-Tier Level-of-Detail (LOD)**:
  - **LOD-1 (`T1 Macro`)**: Compact concept chips and primary Link edges.
  - **LOD-2 (`T2 Meso`)**: Archetype badge, target hub, and attribute/mapping counts.
  - **LOD-3 (`T3 Micro`)**: Full definitions, attribute types, and clipped external mapping pills (`scheme:TerminalFragment`, e.g., `fibo:Collateral`, `fibo:CreditRatingModel`, `fibo:Asset`, `fibo:RealEstate`, `fibo:CreditAgreement`) with full-IRI hover tooltips (`title`).
- **Visual Editor Drawer (`CONCEPT EDITOR`) & `ONT-EVO-002` Grounding Paths**: Click any node (e.g., `Collateral` / `urn:ontology:entity:collateral`) to edit concept metadata, ELM archetype, target hub (`hub_collateral`), attributes, synonyms, parent taxonomy (`ONT-DAG-001`), and external mappings. Under **FIBO / External Mappings**, inline guidance explains the two ways to pass `ONT-EVO-002`:
  - **Path A (Auto-Verified)**: Supply a server-verified `FIBO` mapping using `skos:exactMatch` or `skos:closeMatch` (`skos:broadMatch` is excluded) with confidence $\ge 0.65$.
  - **Path B (Admin Steward Waiver)**: For bespoke domain concepts or manually asserted mappings, authorize an Admin Steward Waiver in the **Quality** tab (`>= 10`-character rationale + `OPENDV_WAIVER_KEY`).

![Stage 4 — Graph Canvas (T3 Micro LOD) & Concept Editor Drawer](assets/ontology-studio/04-graph-canvas-and-visual-editor.png)
*Stage 4: Accepted `v1.0.0` graph displaying 24 grounded regulatory concepts (`T3 Micro` LOD, `Layout: 76.7 ms`, Minimap enabled) with the slide-over `CONCEPT EDITOR` open on `Collateral` (`hub_collateral`).*

### 4.6 Visual Diff Overlay (Committed Versions & Uncommitted Working Draft) (Stage 5)
- Compare any two committed versions (`v0.0.0`, `v1.0.0`, `v1.1.0`, ...) **or** inspect your uncommitted canvas edits live against the active committed baseline (`vX.Y.Z` vs `Working Draft (Uncommitted)`), toggling between **`Unified`** and **`Side-by-Side`** columns.
- Displays the real-time contract verification banner (`STRICTLY ADDITIVE GUARANTEE (ONT-EVO-001)`) and delta summary (`+24 Added`, `Δ 0 Modified`, `-0 Deleted`).
- When unsaved canvas modifications exist, clicking **Visual Diff** defaults immediately to `active_version -> draft` using in-memory graph state without making a failing server export request for `"draft"`:
  - **Green (+)**: Newly added Conceptual Hubs, Links, or Attributes.
  - **Amber (~)**: Modified descriptions, synonyms, or external mappings.
  - **Red (-)**: Deprecated or removed elements (Admin Steward only under `ONT-EVO-001`).

![Stage 5 — Side-by-Side Visual Diff (v0.0.0 to v1.0.0)](assets/ontology-studio/05-visual-diff-side-by-side.png)
*Stage 5: Visual Diff comparing greenfield baseline `v0.0.0` against committed `v1.0.0` in `Side-by-Side` mode, verifying `+24 Added / Δ 0 Modified / -0 Deleted` and `ONT-EVO-001` compliance.*

### 4.7 Quality Scorecard, Multi-Format Exports & Atomic Promotion (Stages 6–7)
- **Quality & Governance Scorecard**: Validates 100% of contract rules in real time, displaying **Grounding Ratio** (`100% (24/24)` vs `Target: >= 65% (BM25)`, or `N/A (No nodes)` when empty), **Acyclicity** (`PASS — Zero DAG inheritance cycles`), **Table Contracts** (`COMPLIANT — ONT-EVO-001 preserved`), the itemized **Contract Rules Checklist** (`ONT-EVO-001`, `ONT-EVO-002`, `ONT-DAG-001`, informational `YAGNI-001` attribute check), **Administrative Steward Waiver** controls, and **Steward Feedback** persistence.
- **Multi-Format Export Modal**: Click **Export** in the top toolbar to preview, copy (`Copy`), and download (`Download File`) any committed snapshot (`v1.0.0 (Current Head)`) across five formats: **Graph A YAML (`.yaml`)**, **Canonical JSON / RFC 8785 JCS (`.json`)**, **W3C Turtle (`.ttl`)**, **JSON-LD (`.jsonld`)**, and **N-Triples (`.nt`)**.
- **Atomic Workspace Promotion**: Switch to the **Workspace Promotion** tab (or click **Promote** in the top toolbar). Enforces dual OCC preconditions—session `head_sha256` (`If-Match`) and `Expected Workspace SHA-256` (`X-OpenDV-Workspace-If-Match`, with a one-click `Greenfield (64 zeros)` helper for new workspaces)—plus a minimum `10`-character `Promotion Rationale` and contract acknowledgment checkbox before enabling `Promote to Workspace`.

![Stage 6 — Quality & Governance Scorecard](assets/ontology-studio/06-quality-and-governance.png)
*Stage 6: Quality & Governance tab confirming `100% (24/24)` FIBO grounding, `PASS` DAG acyclicity, `COMPLIANT` `ONT-EVO-001` status, and Contract Rules Checklist.*

![Stage 7 — Multi-Format Export Modal (W3C Turtle) & Workspace Promotion](assets/ontology-studio/07-export-and-promote.png)
*Stage 7: Tabbed Export & Promotion dialog displaying `W3C Turtle (.ttl)` serialization for `v1.0.0 (Current Head)` with `Copy` and `Download File` actions alongside the `Workspace Promotion` tab.*

### 4.8 DV 2.0 Blueprint Drawer Tab & DDL Generation (Stage 8)
- **3rd Persistent Right Drawer Tab (`data-testid="right-tab-blueprint"`)**: Complementing `Ontology Viewer` and `Proposal Details`, the right workspace includes the dedicated **DV 2.0 Blueprint** view (`apps/opendv-web-workbench/src/ontology/components/BlueprintView.tsx`).
- **Target Raw Vault Summary Badges**: Instantly aggregates physical Data Vault 2.0 tables derived from the active conceptual ontology:
  - `pill-hub-*`: Conceptual CBCs mapped to Raw Vault Hubs (`hub_security`, `hub_investor`, `hub_rate_index`, `hub_document`).
  - `pill-link-*`: Conceptual relationships mapped to Raw Vault Links (`lnk_portfolio_holding`, `lnk_document_extraction`).
  - `pill-sat-*`: Attributes and metadata mapped to Satellites (`sat_security_details`, `sat_investor_details`, `sat_document_metadata`).
- **Syntax-Highlighted SQL DDL Preview (`SqlSyntaxHighlighter`)**: Renders clean, copyable ANSI SQL DDL generated directly from conceptual models, with automatic data type mapping, hash key generation (`MD5`/`SHA256`), load date timestamps (`ldts`), and record source tracking (`rsrc`).
- **Action Buttons**:
  - **Copy DDL** (`data-testid="btn-copy-blueprint-ddl"`): Copies the entire DDL script to the system clipboard.
  - **Download DDL** (`data-testid="btn-download-blueprint-ddl"`): Downloads the 4-zone SQL schema file as `final_four_zone_am.sql`.
  - **Open in Pipeline Builder** (`data-testid="btn-open-in-pipeline-builder"`): Triggers direct deep-link navigation into the Pipeline Builder.
- **4-Zone Reference DDL**: Evaluates 16 tables across the standard Data Vault 2.0 architecture layers:
  - `staging`: Raw intake views (`stg_sec_security_master`, `stg_sec_13f_holdings`, `stg_fred_treasury_rates`, `stg_document_intake`).
  - `raw_vault`: Auditable immutable hubs, links, and satellites (`hub_security`, `hub_investor`, `hub_fund`, `hub_document`, `lnk_portfolio_holding`, `sat_security_details`, `sat_investor_details`, `sat_document_metadata`).
  - `business_vault`: Point-in-Time (PIT) and Bridge constructs (`bv_pit_security`, `bv_bridge_holdings`).
  - `marts`: Analytics-ready Dimensional and Fact views (`dim_security`, `fct_portfolio_positions`).

### 4.9 1-Click Hand-Off to Pipeline Builder & Spec Auto-Staging (Stage 9)
- **Deep-Link Parameter Synchronization**: Clicking **`Open in Pipeline Builder ->`** (`btn-open-in-pipeline-builder`) navigates to:
  ```
  /builder?session_id=<session_id>&source=ontology_studio&domain_namespace=<domain>&pipeline_id=pl_<session_id>
  ```
- **State Pre-Population**:
  `apps/opendv-web-workbench/src/explorer/builder/specBuilderUtils.ts` parses the URL parameters and instantiates an `InteractivePipelineDraft`:
  - Pre-populates target Hubs (`hub_security`, `hub_investor`, `hub_rate_index`, `hub_document`) from active ontology CBCs.
  - Automatically maps business keys (`ticker`, `cik`, `series_id`, `doc_id`).
- **Zero-Click Automatic Staging (`onStageSpec`)**:
  When `PipelineBuilderView.tsx` mounts, it detects incoming `ontology_studio` parameters and triggers `onStageSpec` automatically, staging a valid pipeline specification without requiring any manual clicks or schema re-entry. Stewards can immediately review source mappings, compile the schema, or run the pipeline.

### 4.10 The 4-Step Pipeline Builder Wizard (`/builder`)

The interactive Pipeline Builder (`apps/opendv-web-workbench/src/explorer/builder/WizardShell.tsx`) provides a guided 4-step workflow for configuring, inspecting, and executing single-run Data Vault 2.0 pipelines:

1. **Step 1: Foundation (`Step1Foundation.tsx`)**:
   - **Contract & Lineage Source**: Choose between an active Studio Session (e.g. `am-fund`), an existing run's `VaultBlueprint` (`vault_blueprint.yaml`), or direct file/spec upload.
   - **Target Hub Extraction**: Automatically maps CBCs, target Hub tables, and business keys with zero manual typing.
2. **Step 2: Connector Sources (`Step2ConnectorSources.tsx` & `CatalogTablePickerModal.tsx`)**:
   - **Multi-Connector Catalog**: Configure Files (CSV, Parquet, JSONL), Multi-Table SQLite (`.sqlite`), or Databricks Unity Catalog (`uc://`).
   - **Interactive Table & Key Picker**: Select database tables, designate primary keys and business keys, and configure sampling policies (e.g., maximum rows sampled into DuckDB sandbox).
3. **Step 3: Governance & Dialect (`Step3GovernanceAndDialect.tsx`)**:
   - **Target SQL Dialect**: Choose between `duckdb` (for local shadow validation) and `databricks` (for Databricks Delta Lake 4-zone DDL).
   - **Data Governance Controls**: Configure PII/MNPI column tagging, confidentiality masking rules, and zero-drift strictness.
4. **Step 4: Validate & Run (`Step4ValidateAndRun.tsx`)**:
   - **Single-Run Execution**: Compiles and verifies the 4-zone schema (`staging`, `raw_vault`, `business_vault`, `marts`).
   - **Diff Inspector (`DiffInspector.tsx`)**: Displays an interactive schema diff highlighting added tables, companion satellites, and column renames.
   - **Action Buttons**:
     - **Download Artifacts**: Export `final_four_zone.sql`, `vault_blueprint.yaml`, `final_contract.yaml`, and `pipeline_report.json`.
     - **Evolve Schema with New Data ->**: 1-click transition that feeds the output `VaultBlueprint` into a new run for progressive schema evolution.
     - **Sync Ontology Delta to Studio Session**: Explicitly propagates newly discovered schema drift and table structures back into the Studio Session as an additive delta.

---

## 5. Dual-CLI Command Recipes

Open-DV provides 15 studio subcommands (21 shared dual-CLI leaf commands + standalone-only `init`, 22 total in `schemas/v1/cli-spec.json`) across `opendv` and `opendvctl` under the `ontology studio` namespace. Below are working invocations for every command:

### 5.1 Session Lifecycle Commands

#### `init` (standalone-only)
Initialize the local studio loopback token and permissions:

```bash
opendv ontology studio init
```

Force rotation of existing local token:

```bash
opendv ontology studio init --force
```

#### `create`
Create a greenfield session:

```bash
opendv ontology studio create \
  --session session-alpha \
  --domain-namespace risk_analytics \
  --enterprise-owner "Enterprise Risk Office" \
  --description "Risk analytics baseline"
```

Create a session seeded from an existing workspace:

```bash
opendv ontology studio create \
  --session session-pe \
  --from-workspace examples/private_equity \
  --session-label "PE Evolution"
```

#### `delete`
Delete a studio session and sweep unreferenced CAS blobs:

```bash
opendv ontology studio delete --session session-alpha --force
```

#### `list`
List active studio sessions:

```bash
opendv ontology studio list --limit 25 --offset 0
```

Filter sessions by status:

```bash
opendv ontology studio list --status COMMITTED
```

#### `status`
Display detailed session status, active version, sources, and job history:

```bash
opendv ontology studio status --session session-pe
```

---

### 5.2 Ingestion & Construction Commands

#### `upload`
Upload a document into session CAS:

```bash
opendv ontology studio upload \
  --session session-pe \
  --file partnership_agreement.md \
  --description "Limited Partnership Agreement 2024"
```

Upload a document marked confidential (not sent to third-party LLMs):

```bash
opendv ontology studio upload \
  --session session-pe \
  --file proprietary_formula.pdf \
  --do-not-send-to-agent \
  --description "Proprietary internal pricing formula"
```

#### `parse`
Trigger asynchronous layout and table parsing:

```bash
opendv ontology studio parse \
  --session session-pe \
  --ocr-mode auto \
  --wait
```

#### `clarify`
Generate clarification questions for ambiguous concepts:

```bash
opendv ontology studio clarify \
  --session session-pe \
  --focus-concept "urn:ontology:entity:advisor" \
  --max-questions 5 \
  --wait
```

#### `answer`
Submit answers to clarification questions:

```bash
opendv ontology studio answer \
  --session session-pe \
  --file answers.json
```

#### `construct`
Synthesize ontology proposals using the generative agent:

```bash
opendv ontology studio construct \
  --session session-pe \
  --base-version 1.0.0 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --mode agent \
  --wait
```

Synthesize ontology deterministically in headless CI:

```bash
opendv ontology studio construct \
  --session session-pe \
  --base-version 1.0.0 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --mode deterministic \
  --auto-commit
```

---

### 5.3 Deltas & Proposals Commands

#### `delta validate`
Dry-run validate delta operations and `ONT-EVO` governance rules without mutating session state:

```bash
opendv ontology studio delta validate \
  --session session-pe \
  --file additions.json \
  --base-version 1.0.0 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --verify-grounding
```

#### `delta apply`
Apply explicit additive delta operations from file:

```bash
opendv ontology studio delta apply \
  --session session-pe \
  --file additions.json \
  --base-version 1.0.0 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --rationale "Add capital commitment concept"
```

#### `proposal list`
List all pending proposals for a session:

```bash
opendv ontology studio proposal list --session session-pe
```

#### `proposal show`
Display proposal diff and generated delta operations:

```bash
opendv ontology studio proposal show \
  --session session-pe \
  --proposal-id prop-8821
```

#### `proposal accept`
Accept and commit a proposal to create a new version:

```bash
opendv ontology studio proposal accept \
  --session session-pe \
  --proposal-id prop-8821 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81
```

#### `proposal reject`
Reject a candidate proposal with rationale:

```bash
opendv ontology studio proposal reject \
  --session session-pe \
  --proposal-id prop-8821 \
  --reason "Concepts duplicate existing core enterprise terminology"
```

#### `proposal rebase`
Rebase a proposal onto a newer head commit:

```bash
opendv ontology studio proposal rebase \
  --session session-pe \
  --proposal-id prop-8821 \
  --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81
```

---

### 5.4 Export, Promotion & Bundles Commands

#### `export`
Export the committed ontology to W3C Turtle:

```bash
opendv ontology studio export \
  --session session-pe \
  --export-format turtle \
  --out pe_ontology.ttl
```

Export to canonical RFC 8785 JSON:

```bash
opendv ontology studio export \
  --session session-pe \
  --export-format json \
  --out pe_ontology.json
```

Export to JSON-LD (`.jsonld`):

```bash
opendv ontology studio export \
  --session session-pe \
  --export-format jsonld \
  --out pe_ontology.jsonld
```

#### `promote`
Promote session head ontology to workspace Data Vault project:

```bash
opendv ontology studio promote \
  --session session-pe \
  --workspace examples/private_equity \
  --workspace-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --head-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 \
  --rationale "Promote verified private equity model to workspace pipeline"
```

#### `bundle export`
Export entire session CAS to portable `.odvstudio.zip` bundle:

```bash
opendv ontology studio bundle export \
  --session session-pe \
  --out session_pe_backup.zip
```

#### `bundle import`
Import session bundle into local studio CAS:

```bash
opendv ontology studio bundle import \
  --file session_pe_backup.zip
```

#### `bundle clone`
Clone an existing session into a new isolated working branch:

```bash
opendv ontology studio bundle clone \
  --session session-pe \
  --new-session-id session-pe-experiment \
  --session-label "Experimental LP Modeling"
```

---

### 5.5 Starter Pack Commands

#### `starter-pack list`
List available starter packs for a session:

```bash
# List starter packs via opendv
opendv ontology studio starter-pack list --session am-session-01

# List starter packs via opendvctl
opendvctl ontology studio starter-pack list --session am-session-01 --api-url http://127.0.0.1:8420
```

#### `starter-pack apply`
Apply an individual or composite starter pack to an active session:

```bash
# Apply "all_domains" composite preset (opendv)
opendv ontology studio starter-pack apply --session am-session-01 --pack all_domains

# Apply "all_domains" composite preset (opendvctl)
opendvctl ontology studio starter-pack apply --session am-session-01 --pack all_domains --api-url http://127.0.0.1:8420

# Apply vertical pack: Public Equities OMS (opendv)
opendv ontology studio starter-pack apply --session am-session-01 --pack public_equities_oms

# Apply vertical pack: Fixed Income Rates (opendv)
opendv ontology studio starter-pack apply --session am-session-01 --pack fixed_income_rates
```

---

### 5.6 Free-Form Text Ingestion Commands

#### `text add`
Ingest raw text or markdown commentary into session Content-Addressable Storage (CAS):

```bash
# Ingest raw inline text string (opendv)
opendv ontology studio text add --session am-session-01 --text "Private Credit Direct Lending facility agreement with borrower covenant monitoring." --source-name direct_lending_notes.txt

# Ingest raw inline text string (opendvctl)
opendvctl ontology studio text add --session am-session-01 --text "Private Credit Direct Lending facility agreement with borrower covenant monitoring." --source-name direct_lending_notes.txt --api-url http://127.0.0.1:8420

# Ingest text file (opendv)
opendv ontology studio text add --session am-session-01 --file examples/asset_management/docs/sec_filing_sample.md --source-name sec_filing.md

# Ingest text file (opendvctl)
opendvctl ontology studio text add --session am-session-01 --file examples/asset_management/docs/sec_filing_sample.md --source-name sec_filing.md --api-url http://127.0.0.1:8420
```

---

### 5.7 Steering Refinement Commands

#### `construct` with `--steering-prompt`
Synthesize ontology proposals with natural-language steering instructions:

```bash
# Synthesize proposal with steering guidance (opendv)
opendv ontology studio construct --session am-session-01 --mode agent --steering-prompt "Ground all securities against FIBO SEC and link to CustodianAccount"

# Synthesize proposal with steering guidance (opendvctl)
opendvctl ontology studio construct --session am-session-01 --mode agent --steering-prompt "Ground all securities against FIBO SEC and link to CustodianAccount" --api-url http://127.0.0.1:8420
```

#### `proposal refine`
Iteratively refine an existing PENDING candidate proposal using natural-language feedback:

```bash
# Refine candidate proposal (opendv)
opendv ontology studio proposal refine --session am-session-01 --proposal-id prop-genesis-01 --steering-prompt "Include ISIN and CUSIP attributes on Security and add link to CustodianAccount" --mode deterministic

# Refine candidate proposal (opendvctl)
opendvctl ontology studio proposal refine --session am-session-01 --proposal-id prop-genesis-01 --steering-prompt "Include ISIN and CUSIP attributes on Security and add link to CustodianAccount" --mode deterministic --api-url http://127.0.0.1:8420
```

---

### 5.8 Staged Candidate Commands

#### `staged list`
List held-back candidate concepts scoring below the $0.6500$ FIBO BM25 grounding threshold:

```bash
# List staged low-confidence candidates (opendv)
opendv ontology studio staged list --session am-session-01

# List staged low-confidence candidates (opendvctl)
opendvctl ontology studio staged list --session am-session-01 --api-url http://127.0.0.1:8420
```

#### `staged promote`
Promote a staged candidate concept to an active ontology Hub:

```bash
# Path A: Promote via Synonym Re-Scoring (score must reach >= 0.6500)
opendv ontology studio staged promote --session am-session-01 --candidate-id cand-holding-debt-001 --synonyms "NegotiableDebtSecurity, CorporateDebtBond"

# Path A via opendvctl
opendvctl ontology studio staged promote --session am-session-01 --candidate-id cand-holding-debt-001 --synonyms "NegotiableDebtSecurity, CorporateDebtBond" --api-url http://127.0.0.1:8420

# Path B: Promote via Admin Signed Waiver (requires admin role & >=10 char rationale)
export OPENDV_WAIVER_KEY="secret-admin-hmac-key"
opendv ontology studio staged promote --session am-session-01 --candidate-id cand-bespoke-002 --waiver --rationale "Authorized proprietary credit facility per board charter"

# Path B via opendvctl
opendvctl ontology studio staged promote --session am-session-01 --candidate-id cand-bespoke-002 --waiver --rationale "Authorized proprietary credit facility per board charter" --api-url http://127.0.0.1:8420
```

---

### 5.9 Pipeline Execution Commands

#### `pipeline validate`
Validate Data Vault pipeline specifications and `VaultBlueprint` envelopes:

```bash
# Validate pipeline with VaultBlueprint envelope (opendv)
opendv pipeline validate --blueprint artifacts/vault_blueprint.yaml --dialect duckdb

# Validate pipeline with VaultBlueprint envelope (opendvctl)
opendvctl pipeline validate --blueprint artifacts/vault_blueprint.yaml --dialect duckdb --api-url http://127.0.0.1:8420

# Validate pipeline with input spec file (opendv)
opendv pipeline validate --input examples/asset_management/pipeline_spec.yaml

# Validate pipeline with input spec file (opendvctl)
opendvctl pipeline validate --input examples/asset_management/pipeline_spec.yaml --api-url http://127.0.0.1:8420
```

#### `pipeline run`
Execute single-run data-driven Data Vault pipeline runs:

```bash
# Execute single-run data-driven pipeline targeting DuckDB (opendv)
opendv pipeline run --blueprint artifacts/vault_blueprint.yaml --source "data/public_am/am_sec_treasury_ibor.sqlite" --dialect duckdb

# Execute single-run data-driven pipeline targeting Databricks Delta (opendv)
opendv pipeline run --blueprint artifacts/vault_blueprint.yaml --source "uc://am_prod/ibor" --dialect databricks

# Execute single-run data-driven pipeline (opendvctl)
opendvctl pipeline run --blueprint artifacts/vault_blueprint.yaml --source "data/public_am/am_sec_treasury_ibor.sqlite" --dialect duckdb --api-url http://127.0.0.1:8420
```

---

## 6. Troubleshooting & Error Recovery Catalog

### 6.1 Canonical 11-Step Error Precedence Order

When validating any incoming operation or request, the Studio engine evaluates errors through a strict, authoritative **11-step precedence order** defined in `schemas/v1/error-codes.json`. Lower-numbered steps take absolute precedence over higher-numbered steps:

```
[ Step 0: CLI-Local Syntax / Flags ] ────────► Exit 2 (SYNTAX_INVALID_ARGUMENT)
                   │
                   ▼
[ Step 1: AuthN Token Resolution ] ──────────► Exit 7 (UNAUTHENTICATED / HTTP 401)
                   │
                   ▼
[ Step 2: Precondition Syntax ] ─────────────► Exit 2 (SYNTAX_INVALID_ARGUMENT / HTTP 400)
                   │
                   ▼
[ Step 3: RBAC & Actor Privileges ] ─────────► Exit 7 (FORBIDDEN_ADMIN_REQUIRED / HTTP 403)
                   │
                   ▼
[ Step 4: Body Parse & Field Uniqueness ] ───► Exit 2 (Syntax) / Exit 4 (Duplicate Uniqueness)
                   │
                   ▼
[ Step 5: Envelope Segregation ] ────────────► Exit 4 (ONT_EVO_ENVELOPE_VIOLATION)
                   │
                   ▼
[ Step 6: Lifecycle & Lock State ] ──────────► Exit 6 (LOCK_TIMEOUT / HTTP 409)
                   │
                   ▼
[ Step 7: Field Authorization ] ─────────────► Exit 7 (Admin required before waiver check)
                   │
                   ▼
[ Step 8: Semantic Waiver Validation ] ──────► Exit 4 (INVALID_GROUNDING_WAIVER / HTTP 422)
                   │
                   ▼
[ Step 9: Concurrency Control (OCC) ] ───────► Exit 5 (OCC_CONFLICT / HTTP 412)
                   │
                   ▼
[ Step 10: Referential & ONT-EVO Rules ] ────► Exit 4 (ONT_EVO_UNGROUNDED_CONCEPT / HTTP 422)
```

### 6.2 Exit Codes Reference Table

| Exit Code | Classification | HTTP Equivalent | Description & Typical Causes |
|---|---|---|---|
| **0** | Success | `200`, `201`, `202` | Operation completed successfully. |
| **1** | Operational Failure | `400`, `500`, `503` | OCR binary unavailable, external LLM failure, or missing required rationale. |
| **2** | Syntax / Precondition | `400` | Unrecognized CLI flag, missing mandatory path, or invalid JSON syntax. |
| **3** | Not Found | `404` | Session ID, source document ID, proposal ID, or question ID not found. |
| **4** | Semantic / Contract | `422` | ONT-EVO grounding violation, envelope violation, corrupt source blob, or waiver error. |
| **5** | OCC Concurrency Conflict | `412` | Head SHA-256 mismatch (`If-Match` conflict); concurrent edit committed. |
| **6** | Lock State Conflict | `409` | Active background job lock timeout or concurrent promotion lock in progress. |
| **7** | Authorization Denied | `401`, `403` | Missing token, non-admin attempting waiver or promotion, or CSRF origin denied. |

### 6.3 RFC 9457 Problem Details Format

All Studio REST API errors return standard RFC 9457 `application/problem+json` payloads:

```json
{
  "type": "urn:opendv:error:ont-evo-ungrounded-concept",
  "title": "Ont Evo Ungrounded Concept",
  "status": 422,
  "detail": "Concept 'urn:ontology:entity:bespoke_asset' lacks a verified FIBO skos:exactMatch or skos:closeMatch mapping with confidence >= 0.6500 (observed 0.4800; skos:broadMatch excluded).",
  "error_code": "ONT_EVO_UNGROUNDED_CONCEPT",
  "instance": "/api/v1/ontology/studio/sessions/wealth-sec-2024/proposals/prop-8821/accept",
  "extensions": {
    "concept_uri": "urn:ontology:entity:bespoke_asset",
    "confidence": 0.4800,
    "grounding_threshold": 0.6500
  }
}
```

### 6.4 Common Issues & Solutions

1. **`PRECONDITION_BASE_SHA_MISMATCH` (Exit Code 2 / 5)**:
   - *Cause*: The session head has advanced since you retrieved the base SHA-256 hash (`ONT-EVO-003` OCC guard).
   - *Fix*: Run `opendv ontology studio status --session <session_id>` to view the latest `head_sha256`, or rebase your proposal via `proposal rebase`.

2. **`ONT_EVO_UNGROUNDED_CONCEPT` (Exit Code 4)**:
   - *Cause*: A newly added concept (or a workspace-seeded grandfathered concept whose exemption was revoked by one of the 17 existing-concept-mutating operations—`18` total in `CONCEPT_MUTATING_OP_TYPES`) lacks a server-verified `FIBO` `skos:exactMatch` or `skos:closeMatch` mapping with confidence $\ge 0.6500$ (`skos:broadMatch` does not satisfy Path A).
   - *Fix*: Select a verified `skos:exactMatch` / `skos:closeMatch` FIBO alignment via clarification Q&A, or authorize a per-concept administrative waiver (`OPENDV_WAIVER_KEY`) with `--low-confidence-waiver`, `--ungrounded-concept <uri>`, and `--grounding-rationale`.

3. **`LOCK_TIMEOUT` (Exit Code 6)**:
   - *Cause*: A background parsing or construction job is currently running on the session.
   - *Fix*: Check job status via `status --session <session_id>` or wait for the active worker to release the lock.

4. **`FORBIDDEN_ADMIN_REQUIRED` (Exit Code 7)**:
   - *Cause*: Operation requires Enterprise Architect / Admin privilege (e.g. waiver signing, promotion, concept deletion).
   - *Fix*: Supply valid admin credentials or execute with an authorized local admin token.

5. **`STUDIO_ALREADY_INITIALIZED` (`HTTP 409`) / `UNAUTHENTICATED` (`HTTP 401`) in Web Workbench**:
   - *Cause*: Clicking automatic loopback auth when `.opendv/ontology_studio/.local_token` was already created by an earlier session or server run, or opening `/ontology` without an exchanged cookie/bearer token.
   - *Fix*: Paste the token stored in `.opendv/ontology_studio/.local_token` into the focused token prompt, or relaunch via `uv run opendv serve --open` so the launch nonce exchanges automatically and hydrates your RBAC role (`X-OpenDV-Role`).

6. **Zero-Operation Candidate Proposal (`0 ops`) in Web Workbench**:
   - *Cause*: Running ontology construction before source documents are uploaded and parsed (or after resolving no extractable entities) emits an empty candidate proposal; the **Accept** button stays disabled.
   - *Fix*: Upload and parse your domain sources in the **Sources** tab first, re-run construction, and click **Reject** on the `0 ops` proposal to archive/clean it up.

7. **`PROPOSAL_ALREADY_RESOLVED` (`HTTP 409` / Exit Code 6)**:
   - *Cause*: Attempting to refine or accept a candidate proposal that has already reached a terminal state (`ACCEPTED`, `REJECTED`) or was `SUPERSEDED` by an iterative steering prompt refinement.
   - *Fix*: Run `opendv ontology studio proposal list --session <session_id>` to find the active `PENDING` proposal (such as `prop-refine-...`), or run `construct` to generate a fresh proposal.

8. **`UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE` (Exit Code 4)**:
   - *Cause*: Running or validating a Data Vault pipeline containing document sources but zero relational tables without enabling Wave 0 cold-start mode.
   - *Fix*: Supply `--unstructured-only` on the CLI (`opendv pipeline run --input <spec.yaml> --unstructured-only`) or set `unstructured_only: true` in your pipeline specification YAML.

9. **`INVALID_GROUNDING_WAIVER` (Exit Code 4 / `HTTP 422`)**:
   - *Cause*: Submitting an administrative grounding waiver with a rationale shorter than 10 characters or without verified admin privileges.
   - *Fix*: Ensure the caller holds the `admin` role (via `OPENDV_TOKEN` or `OPENDV_WAIVER_KEY`) and provide a substantive `rationale` string of at least 10 characters explaining why the bespoke concept lacks a standard FIBO equivalent.

10. **`ONT_EVO_CONTRACT_VIOLATION` in Path A Promotion (Exit Code 4 / `HTTP 422`)**:
    - *Cause*: Attempting Path A candidate promotion with synonyms that fail to raise the FIBO BM25 similarity score to $\ge 0.6500$.
    - *Fix*: Supply recognized financial synonyms (e.g. `"NegotiableDebtSecurity"`) to boost match confidence, or promote via Path B using an administrative signed waiver.
