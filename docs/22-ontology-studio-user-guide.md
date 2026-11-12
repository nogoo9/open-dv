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

1. **Agent-First Mode (3-Persona LLM)**:
   - Orchestrates three specialized cognitive personas:
     - `@term-scout`: Fast extraction of candidate business concepts, attributes, and domain terminology from parsed evidence chunks.
     - `@domain-clarifier`: Detects ambiguous conceptual alignments against standard taxonomies and synthesizes targeted clarification questions.
     - `@ontology-architect`: Reasoning-intensive synthesis of Canonical Business Concepts (CBCs), Links, Satellites, and formal OWL 2 axioms.
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
│   ├── manifest.json                 # Ingested sources index and SHA-256 digests
│   ├── chunks.jsonl                  # Normalized text chunks and character offset spans
│   └── staged_candidates.json        # Low-confidence concepts pending clarification
├── clarifications/
│   ├── questions.json                # Generated ambiguity questions
│   └── answers.json                  # Steward responses and business rationales
├── proposals/
│   └── <proposal_id>.json            # Pending candidate proposals with delta operations
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
```

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

## 4. Interactive Web Workbench Walkthrough (`/ontology`)

The Open-DV Web Workbench provides a visual, reactive single-page application at `/ontology`:

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

### 4.1 Shell & Navigation Bar
- **Session Switcher**: Switch instantly between active sessions or initialize a new workspace.
- **State Badges**: Color-coded lifecycle indicators:
  - `EMPTY`: Initialized without committed versions (`v0.0.0`).
  - `SEEDED`: Initialized from an existing workspace baseline.
  - `COMMITTED`: Version committed in session CAS.
  - `PROMOTED`: Head version promoted to workspace Data Vault.

### 4.2 Sources Panel
- Drag-and-drop file uploader supporting `.pdf`, `.md`, `.csv`, `.tsv`, `.parquet`, `.json`, `.xsd`.
- OCR Mode toggle: `auto` (default), `force`, `off`.
- Taint classification pill badges: `public` (blue), `internal` (green), `confidential` (amber), `restricted` (red).
- Chunk Inspector drawer: View extracted text spans with UTF-8 character offsets and page coordinates.

### 4.3 Clarification Q&A Panel
- Presents semantic ambiguity questions generated by `@domain-clarifier`.
- Displays candidate reference ontology URIs with definitions.
- Captures human steward choice and mandatory business rationale.
- Saves responses directly to session CAS (`clarifications/answers.json`).

### 4.4 React Flow + elkjs Graph Canvas
- High-performance graph canvas powered by `@xyflow/react` and `elkjs`.
- Automated hierarchical layout executing in $< 300$ ms SLA.
- **3-Tier Level-of-Detail (LOD)**:
  - **LOD-1 (Macro)**: Displays Conceptual Hubs and primary Link relationships.
  - **LOD-2 (Standard)**: Expands core attributes, synonym badges, and external mapping pills.
  - **LOD-3 (Micro)**: Displays complete Satellite attribute specifications, data types, and grain semantics.

### 4.5 Visual Diff Overlay
- Compares active proposals or version deltas against the base snapshot:
  - **Green (+)**: Newly added Conceptual Hubs, Links, or Attributes.
  - **Amber (~)**: Modified descriptions, synonyms, or rescored external mappings.
  - **Red (-)**: Deprecated or tombstoned elements (admin steward revisions only).

### 4.6 Visual Editor Drawer
- Inspect and edit concept attributes, data types (`string`, `integer`, `decimal`, `datetime`), synonyms, and external mappings.
- Re-parent concepts within standard taxonomies (`SetParentConcept`).

### 4.7 Quality Scorecard & Export Modal
- Validates 100% of ONT-EVO rules in real time.
- Displays grounding confidence score meters against the $0.6500$ threshold.
- One-click export modal to Graph A YAML (`.yaml`), Canonical RFC 8785 JCS JSON (`.json`), W3C Turtle (`.ttl`), JSON-LD (`.jsonld`), or N-Triples (`.nt`).
- Two-phase promotion trigger with lock status verification.

---

## 5. Dual-CLI Command Recipes

Open-DV provides **21** shared dual-CLI leaf commands across `opendv` and `opendvctl` (plus the standalone-only local bootstrap command `init`, 22 leaf commands total in `schemas/v1/cli-spec.json`) under the `ontology studio` namespace. Below are working invocations for every command:

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
opendv ontology studio list --status active
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

Export to W3C N-Triples (`.nt`):

```bash
opendv ontology studio export \
  --session session-pe \
  --export-format ntriples \
  --out pe_ontology.nt
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
