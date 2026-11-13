# 19 — Open-DV Ontology Construction Studio: System Architecture & Subsystem Specification

**Status:** Normative Architecture Reference (Milestone M2 Deliverable)  
**Target Repository:** `open-dv` (`packages/opendv-core`, `packages/opendv-contracts`, `packages/opendv-sources`, `packages/opendv-agent-harness`, `packages/opendv-service`, `apps/opendv-platform-api`, `apps/opendv-cli`, `apps/opendvctl`, `apps/opendv-web-workbench`)  
**Specification Dialect:** OpenDV Architecture v1 / Data Vault 2.0 Modeling Standard  
**Normative Cross-References:** `docs/design/ontology-studio/master-plan.md`, `research.md`, `ADR-001` through `ADR-016`, `HANDOFF.md`, `docs/20-ontology-studio-spec.md`

---

## 1. System Overview & Platform Architectural Topology

### 1.1 Executive Summary & Mission Statement
The Open-DV Ontology Construction Studio is an enterprise ontology engineering environment designed to automate and govern the creation of Data Vault 2.0 Graph A semantic models from regulatory filings, schemas, and documentation.

The Studio ingests multi-format documents, grounds concepts in FIBO, resolves ambiguities via structured clarifications, and evolves ontologies through additive deltas governed by the ONT-EVO protocol. Persisted artifacts, evidence spans, and versions are stored in Content-Addressable Storage (CAS), guaranteeing cryptographic immutability and complete reproducibility.

### 1.2 Decoupled System Topology & Foundation Layering
The Studio is structured into architectural tiers governed by the unidirectional package dependency DAG detailed in §1.3 (with `opendv-core` and `opendv-contracts` forming the immutable foundation layer):

| Architectural Tier | Component Packages | Key Responsibilities & Capabilities | Dependency Flow |
|---|---|---|---|
| **Foundation** | `opendv-core`, `opendv-contracts` | Immutable domain models, RFC 8785 JCS hashing, closed error codes, DTOs | Zero upward dependencies |
| **1. Presentation** | `opendv-web-workbench`, `opendv-cli`, `opendvctl` | React 19 SPA (`/ontology`), in-process CLI (`LocalExecutor`), remote Bun CLI (`RemoteExecutor`) | Calls Tier 2 REST/SSE or imports Tier 3 service |
| **2. Platform Service** | `opendv-platform-api` | FastAPI application (`/api/v1/ontology/studio/*`), SSE engine, auth guard, job supervisors | Calls Tier 3 service modules |
| **3. Core Engine & Service** | `opendv-service` | Dual-mode construction, Okapi BM25 matcher, CAS engine, 2-phase validation, `OntologyDelta` engine | Injects Tier 4a via SPI; calls Tier 4b |
| **4a. Agent Harness** | `opendv-agent-harness` | 3-Persona SPI (`@term-scout`, `@domain-clarifier`, `@ontology-architect`), prompt rendering | Depends on Foundation (core & contracts) |
| **4b. Ingestion & Sources** | `opendv-sources` | LiteParse document adapter, OCR complexity routing, LibreOffice conversion, curated FIBO core pack | Ingestion adapters; depends on Foundation |


### 1.3 Architectural Invariants & Monorepo Layer Constraints
To preserve long-term maintainability, zero-dependency deployment capabilities, and cryptographic stability, the system enforces the following inviolable monorepo architectural invariants:

1. **Strict Core Layer Isolation (`opendv-core`)**:
   `opendv-core` owns core domain models (`GraphAOntology`, `CanonicalBusinessConcept`, `CanonicalRelationship`, `ReferenceEnumeration`), the RFC 8785 JSON Canonicalization Scheme (JCS) serializer, canonical SHA-256 digest computation (`compute_ontology_sha256`), and structural validation (`structural_validator`). It **must never** import from `opendv-contracts`, `opendv-sources`, `opendv-agent-harness`, `opendv-service`, or any platform application. It has zero external dependencies beyond the Python standard library and Pydantic.
2. **Contract Boundary Decoupling (`opendv-contracts`)**:
   `opendv-contracts` owns all wire DTOs (`StudioSessionDTO`, `ConstructRequest`, `DeltaCommitResponse`), event schemas, and the closed error code catalog (`ErrorCode`, `ProblemDetail`). It depends only on `opendv-core` for primitive domain types.
3. **Agent Harness Decoupling (`opendv-agent-harness`)**:
   `opendv-agent-harness` sits alongside `opendv-sources` in the dependency DAG. It depends strictly on `opendv-contracts` and `opendv-core`. The persona SPI, `ConstructionEngine`, and `ConceptMatcher` Protocols are defined in `opendv-contracts`; `opendv-service` consumes them and `opendv-agent-harness` implements them, eliminating circular dependencies.
4. **Pure State Transition Semantics (`OntologyDelta`)**:
   Applying an `OntologyDelta` to a base `GraphAOntology` is a pure, deterministic function: `apply(base: GraphAOntology, delta: OntologyDelta) -> GraphAOntology`. It produces no side effects and performs zero network IO.
5. **RFC 8785 Canonical Digest Invariance**:
   All entity hashes and ontology SHA-256 digests are computed exclusively over RFC 8785 canonical JSON bytes. Optional extension fields across core models default strictly to `None`. Mapping confidence scores are rounded to 4 decimals strictly at the matcher boundary before assignment; core models do not round or coerce loaded floats. Serialization uses existing `compute_ontology_sha256` behavior: `model_dump(mode="json", exclude_none=True)` with recursive empty collection pruning (`_norm_empty`), guaranteeing that the Private Equity reference benchmark (`pe_graph_a_ontology.yaml`) preserves its certified SHA-256 digest bit-for-bit:
   $$\text{compute\_ontology\_sha256}(\text{load}(\text{pe\_graph\_a\_ontology.yaml})) = \mathtt{1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81}$$
6. **Zero UI Platform Coupling**:
   The Web Workbench SPA at `/ontology` interacts with the platform strictly via public REST and SSE API contracts defined in `schemas/v1/openapi.json`. It possesses no backchannel access to the server filesystem or Python runtimes.

---

## 2. End-to-End System Data Flow & State Transitions

### 2.1 Decoupled Lifecycle State Architecture
To support concurrent proposals, proposal rebasing (ONT-EVO-003), and continuous evolution without state machine deadlocks, the Studio models lifecycle state across three orthogonal dimensions:

1. **Session Version State (`session.version_state`)**:
   - `EMPTY (0.0.0)`: Greenfield genesis session initialized without baseline model.
   - `SEEDED (vX.Y.Z)`: Initialized from an existing workspace pipeline contract (`--from-workspace`), anchoring baseline head SHA and grandfathered concept URIs.
   - `COMMITTED (vA.B.C)`: Head version snapshot committed via accepted proposal, auto-commit, or direct delta.
   - `PROMOTED (vA.B.C)`: Promoted to workspace pipeline (`graph_a.lock`). `PROMOTED` is an active evolutionary state (not a terminal dead end); sessions can ingest further sources, apply direct deltas, or run new construction cycles, transitioning to `COMMITTED` on next version commit.

2. **Proposal Lifecycle (`proposal.status`)**:
   Each proposal `proposals/<pid>.json` stores its lifecycle state (`PENDING`, `ACCEPTED`, `REJECTED`):
   - `PENDING`: Candidate delta synthesized against `base_sha256`. Ready for review or acceptance.
   - `STALE`: Dynamically evaluated at query time: whenever `proposal.base_sha256 != session.head_sha256` and status is not terminal (`ACCEPTED`/`REJECTED`). Requires `POST .../rebase` before acceptance.
   - `ACCEPTED`: Terminal for this proposal. Delta validated and committed to snapshot (`versions/vA.B.C`).
   - `REJECTED`: Terminal for this proposal. Steward rejects candidate (`POST .../reject`). Does not affect session version state.

3. **Pipeline Ingestion & Analysis Progress**:
   Tracked independently via CAS manifests (`inputs/manifest.json`, `evidence/chunks.jsonl`, `clarifications/`):
   - Ingestion, parsing, and clarification question generation run orthogonally and can be executed across any version state (`EMPTY`, `SEEDED`, `COMMITTED`, `PROMOTED`).

| Endpoint / Operation | Allowed Session State | Proposal State Transition | Target Session State | Preconditions & Persistence Actions |
|---|---|---|---|---|
| `POST /init` | System `UNINITIALIZED` | N/A | `INITIALIZED` | Loopback only; mints `.local_token` (0600) under L1 |
| `POST /sessions` | Platform `INITIALIZED` | N/A | `EMPTY` / `SEEDED` | Creates CAS directory layout; seeds genesis `0.0.0` or workspace contract |
| `POST .../sources` | Active | N/A | Unchanged | Ingests blobs into CAS (`inputs/cas/<sha256>`), updates `manifest.json` |
| `POST .../parse` | Active | N/A | Unchanged | Spawns async layout parser; streams SSE events; writes `chunks.jsonl` |
| `POST .../clarify` | Active | N/A | Unchanged | Dispatches `@domain-clarifier`; generates questions in `clarifications/` |
| `POST .../answers` | Active | N/A | Unchanged | Persists steward responses into `clarifications/answers.json` |
| `POST .../construct` | Active | New proposal: `PENDING` (or direct `COMMITTED` if `--auto-commit`) | Current (or `COMMITTED` if auto-commit) | In Agent mode, creates `ProposalRecord` (`PENDING`). In deterministic `--auto-commit`, commits directly to `COMMITTED` |
| `POST .../proposals/{id}/accept` | Active | `PENDING` $\to$ `ACCEPTED` (intervening pending $\to$ `STALE`) | `COMMITTED` (`vX.Y.Z`) | Requires `If-Match: base_sha256`. 2-phase validation; atomic snapshot commit. Concurrent proposals become `STALE` |
| `POST .../proposals/{id}/reject` | Active | `PENDING` / `STALE` $\to$ `REJECTED` | Unchanged | Marks proposal `REJECTED`; session remains completely active for deltas, sources, or promotion |
| `POST .../proposals/{id}/rebase` | Active | `STALE` $\to$ `PENDING` | Unchanged | Re-evaluates conflict keys against new head SHA; validates AST; resets to `PENDING` |
| `POST .../delta` | `EMPTY`, `SEEDED`, `COMMITTED`, `PROMOTED` | Intervening pending $\to$ `STALE` | `COMMITTED` (`vX.Y.Z`) | Direct delta or admin steward revision: validates, commits immutable snapshot. Pending proposals become `STALE` |
| `POST .../promote` | `COMMITTED`, `PROMOTED` | N/A | `PROMOTED` | Verifies containment, acquires `graph_a.lock` [L4], verifies OCC, writes workspace contract |


### 2.2 End-to-End Data Pipeline Trace
The execution of an end-to-end ontology construction cycle follows a rigorous sequence:

1. **Document Ingestion**:
   The user or client uploads documents via `POST /api/v1/ontology/studio/sessions/{id}/sources`. Each file is hashed using SHA-256, written into `<session_dir>/inputs/cas/<sha256>`, and registered in `<session_dir>/inputs/manifest.json`. If `do_not_send_to_agent=True` is asserted, the file is tagged as tainted in `<session_dir>/taint_index.json`.
2. **Text Normalization & Offset Extraction**:
   The ingestion engine invokes `LiteParseDocumentAdapter` (or `pypdf` fallback). Text is normalized to Unicode NFC and Unix Line Feeds (`\n`). A list of `PageMapEntry` records is emitted, recording byte lengths, code point offsets, table boundaries, and heading hierarchies into `<session_dir>/evidence/chunks.jsonl`.
3. **Semantic Grounding (FIBO Alignment)**:
   Discovered terms and table column candidates are queried against the in-memory `ConceptMatcher` loaded with the curated FIBO core pack (`fibo-core-<sha256_8>.json`). The multi-field Okapi BM25 engine scores candidate matches across concept names, synonyms, and definitions. Candidates scoring $\ge 0.6500$ with name/synonym hit confirmation are staged for grounding.
4. **Clarification Generation & Q&A**:
   If semantic ambiguities or competing reference alignments exist, `@domain-clarifier` (or deterministic heuristic rules) generates structured questions stored in `<session_dir>/clarifications/questions.json`. The user submits answers via `POST .../answers`, which are written into `<session_dir>/clarifications/answers.json`.
5. **Construction & Delta Synthesis**:
   `POST .../construct` triggers the construction pipeline. In Agent-First mode, `@ontology-architect` synthesizes Canonical Business Concepts (CBCs), attributes, relationships, and external mappings. In Deterministic mode, schema heuristics (`_id`, `_pk`, `_fk`) and top-1 BM25 hits assemble the model (holding back ungrounded candidates). The output is framed as a `ProposalRecord` containing an additive `OntologyDelta` stored in `<session_dir>/proposals/<pid>.json`.
6. **Governance Gates & Version Commit**:
   Upon human approval (`POST .../proposals/{pid}/accept`), the system acquires `.session.lock` [L5], verifies `If-Match: "<head_sha256>"`, runs `structural_validator` and `governance_validator`, stages artifacts in `.staging-<u>/`, renames staging to `<session_dir>/versions/vX.Y.Z/` via `os.replace`, commits `session.json` via `.session.json.<u>.tmp` $\to$ `os.replace` (the commit point), marks proposal `ACCEPTED` in `proposals/<pid>.json`, appends the commit event to `audit.jsonl` under L5, and flushes directory sync.
7. **Workspace Promotion (Two-Phase Intent Protocol)**:
   Promoting head (`POST .../promote`) verifies workspace containment, acquires `graph_a.lock` [L4] then `.session.lock` [L5] (sorted order on multi-lock), verifies OCC and lineage continuity, writes `.promotion_intent.json`, writes canonical YAML via secure atomic temp file to `target_contract_path`, updates `session.json`, unlinks intent, and releases locks in reverse order per Doc 20 §7.2.

---

## 3. Document Ingestion Subsystem (`opendv-sources`)

### 3.1 Subsystem Architecture & Adapters
The document ingestion subsystem is housed entirely in `packages/opendv-sources` under `opendv_sources.ingestion`. The primary entrypoint is `DocumentIngestionService`, which coordinates format-specific adapters implementing the `DocumentAdapter` SPI:

```python
class DocumentAdapter(Protocol):
    def can_handle(self, mime_type: str, file_path: Path) -> bool: ...
    def extract_text_and_layout(self, file_path: Path, source_sha256: str, options: IngestionOptions) -> IngestionResult: ...
```

### 3.2 LiteParse 0.4.x Integration & Zero-Dependency Execution
The primary ingestion engine is `LiteParseDocumentAdapter` (`liteparse` 0.4.x), embedding Google PDFium C++ in native wheels for self-contained PDF rendering, text extraction, font metrics, and table boundary detection without external runtimes (Java, Poppler, Ghostscript). Detects grid lines/gutters for pipe tables; falls back to `PyPDFDocumentAdapter` (`pypdf`) if `liteparse` is unavailable.

### 3.3 Text Normalization & Unicode Offset Anchoring
To eliminate text offset drift across operating systems, Python strings, and browser DOM rendering, ingestion enforces strict normalization:
1. **Normalization Form C (NFC)**: All incoming text normalized via `unicodedata.normalize("NFC", text)`.
2. **Line Ending Canonicalization**: All CRLF (`\r\n`) and CR (`\r`) converted to standard Unix Line Feeds (`\n`).
3. **Unicode Code Point Counting**: Offsets in `PageMapEntry`, chunks, and `EvidenceRef` are strictly counted in **Unicode code points** ($0 \le \text{start} < \text{end} \le \text{total\_codepoints}$). For `PageMapEntry` and sequential partitions, spans obey monotonic non-overlapping tiling: $0 \le \text{start}_i < \text{end}_i \le \text{start}_{i+1} \le \text{total\_codepoints}$. Citations from distinct concepts may cite overlapping spans.
4. **Browser UTF-16 Mapping**: JavaScript in the Web Workbench operates on UTF-16 code units. For characters outside the BMP ($> \mathtt{0xFFFF}$, surrogate pairs), `DocumentIngestionService` pre-computes an offset translation index:
   $$\text{offset}_{\text{utf16}} = \text{offset}_{\text{codepoint}} + \sum_{k=0}^{\text{offset}-1} \mathbf{1}_{\{c_k > \mathtt{0xFFFF}\}}$$

### 3.4 Layout Complexity Evaluation & OCR Routing
To prevent silent parsing failures on scanned or image-heavy PDFs, each page undergoes layout complexity evaluation via `evaluate_page_complexity(metrics)`: flagged complex if `character_count < 50`, `raster_image_area_ratio > 0.40`, or `has_unmapped_glyph_cid`. When complex:
- If `ocr_mode="auto"|"force"` and Tesseract OCR binary is present on `PATH`, execution routes to OCR extraction.
- If OCR is required but missing, the adapter raises `OCR_UNAVAILABLE` (HTTP 501, exit code 1).

### 3.5 Headless LibreOffice Conversion Adapter
For office formats (`.docx`, `.pptx`, `.odt`), `LibreOfficeDocumentAdapter` converts documents to PDF via `soffice --headless --convert-to pdf --outdir <tmp> <input_file>` with isolated `-env:UserInstallation` profile. If `soffice` is not found, raises `OFFICE_CONVERSION_UNAVAILABLE` (HTTP 501, exit 1).

### 3.6 Multi-Format Ingestion Matrix
| Format & MIME | Primary Adapter | Fallback Adapter | Capabilities Extracted |
|---|---|---|---|
| PDF (`application/pdf`) | `LiteParseDocumentAdapter` | `PyPDFDocumentAdapter` | Text, layout, tables, page bounds, OCR |
| Markdown (`text/markdown`) | `MarkdownDocumentAdapter` | Plain text reader | Headings, code blocks, pipe tables |
| Plain Text (`text/plain`) | `PlainTextDocumentAdapter` | None | NFC/LF text, paragraph chunks |
| CSV / TSV (`text/csv`) | `DelimitedTabularAdapter` | Python `csv` | Column headers, data types, sample rows |
| Parquet (`vnd.apache.parquet`) | `PyArrowTabularAdapter` | DuckDB reader | Schema metadata, field nullability, stats |
| JSON / YAML | `StructuredDataDocumentAdapter` | None | Hierarchical keys, value types |
| HTML / XML | `MarkupDocumentAdapter` | Plain text | DOM text extraction, table structures |
| DOCX / PPTX | `LibreOfficeDocumentAdapter` | None | Headless PDF intermediate $\to$ LiteParse |

---

## 4. Semantic Knowledge Grounding & Reference Ontologies

### 4.1 Pinned FIBO Reference Ontology Architecture
To avoid uncontrolled upstream schema drift and provide deterministic semantic anchoring, the Studio vendors an authoritative subset of the EDM Council Financial Industry Business Ontology (FIBO).

- **Vendoring Script & Pinned Tag**: `open-dv/scripts/vendor_fibo.py` targets EDM Council repo (`edmcouncil/fibo`), pinned tag `master_2024Q4` (commit `69dbbe7a6a4e32049ba3f3988b48877bc954f061`).
- **Core Modules**: Six modules: `FND` (Foundations), `BE` (Business Entities), `FBC` (Financial Business and Commerce), `SEC` (Securities), `LOAN` (Loans), `IND` (Indices and Indicators).
- **Compilation**: Serialized to RFC 8785 canonical JSON as `packages/opendv-sources/src/opendv_sources/fibo/fibo-core-<sha256_8>.json` with digest in `baseline-digests.json`. Budgets: disk $<4.0\text{ MB}$, memory $<18.0\text{ MB}$, cold load $<70\text{ ms}$.

### 4.2 OWL 2 DL Axiom to Data Vault 2.0 Mapping Matrix
The grounding engine maps formal Web Ontology Language (OWL 2 DL) axioms directly to Data Vault 2.0 physical and conceptual constructs:

| OWL 2 DL Construct | Target Construct | Physical Table Pattern | Key Properties |
|---|---|---|---|
| `owl:Class` | Canonical Business Concept (CBC) | `hub_<slug>` | Hash Key (`hk_<slug>`), Business Key (`bk_<slug>`), Load Date (`ldts`), Record Source (`rsrc`) |
| `rdfs:subClassOf` | Hierarchical Link (HAL) or Parent Attribute | `hal_<slug>` | Parent Hash Key, Child Hash Key, Load Date |
| `owl:ObjectProperty` | Canonical Relationship | `lnk_<slug>` | Hash Key (`hk_lnk_<slug>`), Hub Hash Keys (`hk_hub1`, `hk_hub2`), `ldts`, `rsrc` |
| `owl:ObjectProperty` (temporal / unit) | Transactional Link | `t_lnk_<slug>` | Hash Key, Dependent Hub Hash Keys, Transaction Timestamp |
| `owl:DatatypeProperty` | Satellite Attribute | `sat_<slug>` | Parent Hash Key (`hk_<slug>`), Load Date (`ldts`), Hash Diff (`hd_<slug>`), Payload columns |
| `owl:oneOf` / Controlled Class | Reference Enumeration | `ref_<slug>` | Code column (`<slug>_code`), Description column, Effective dates |
| `skos:exactMatch` / `closeMatch` | External Mapping Annotation | Metadata Annotation | Target Ontology (`FIBO`), Standard URI, Confidence score |

### 4.3 Closure `subClassOf` Derivation & Grounding Guard
When a candidate concept establishes a verified direct mapping to a specialized FIBO class (e.g. `fibo-sec-eq:CommonShare`), its taxonomic superclasses (e.g. `fibo-sec-eq:Share`, `fibo-fbc-fi:FinancialInstrument`, `fibo-fnd-oac:Asset`) may be resolved via transitive closure over `rdfs:subClassOf`:

1. **Grounded Seed Precondition**:
   Transitive closure is computed **strictly if and only if** the direct seed mapping is itself fully grounded (direct BM25 score $\ge 0.6500$ or approved administrative waiver Path B). If the direct mapping fails the grounding threshold ($< 0.6500$), closure computation is suppressed.
2. **SKOS Broad Match Classification**:
   Inferred taxonomic superclasses are categorized strictly as `skos:broadMatch` (never `skos:exactMatch` or `skos:closeMatch`). They inherit the direct seed mapping's confidence score rather than a synthetic fixed `1.0000`.
3. **Exclusion from Path A Grounding**:
   Inferred `skos:broadMatch` mappings are structural annotations and are **excluded** from satisfying Path A grounding. Only direct `skos:exactMatch` or `skos:closeMatch` mappings $\ge 0.6500$ satisfy Path A. A concept whose direct mapping scores below $0.6500$ cannot bypass ONT-EVO-002 via superclass inheritance and raises `ONT_EVO_UNGROUNDED_CONCEPT` (exit 4).

### 4.4 `ConceptMatcher` SPI & Okapi BM25 Formulation
The semantic matching engine is implemented via `ConceptMatcher` SPI in `opendv_service.ontology.matching`. The default normative implementation is `bm25_fibo_v1` v1.0.0.

#### 1. Pinned 7-Step Unicode 14.0 Tokenization Pipeline
To prevent token divergence across Python environments, tokenization follows an invariant 7-step sequence:
1. **Unicode NFC Normalization**: `unicodedata.normalize("NFC", s)`.
2. **CamelCase Decomposition**: Splits `CommonShare` $\to$ `Common Share`, `NAVCalculation` $\to$ `NAV Calculation`.
3. **Delimiter Replacement**: Replaces hyphens, underscores, slashes, and periods with single spaces.
4. **Alphanumeric Word Extraction**: Strips punctuation and symbols; retains alphanumeric code points.
5. **Lowercasing**: Unicode lowercase conversion (`s.lower()`).
6. **Domain Acronym Expansion**: Canonicalizes 18 financial acronyms (e.g. `nav` $\to$ `net asset value`, `lei` $\to$ `legal entity identifier`, `pe` $\to$ `private equity`); expanded tokens are protected from subsequent stopword filtration.
7. **Stopword Filtration**: Prunes a deterministic list of 33 English and modeling stopwords (`the`, `is`, `at`, `which`, `on`, `of`, `class`, `concept`, `entity`, etc.).

#### 2. Robertson BM25 Formulation & Field Weights
BM25 scoring uses fixed-precision `Decimal(28)` arithmetic with parameters $k_1 = 1.5$ and $b = 0.75$:

$$\text{IDF}(t) = \ln\left(1 + \frac{N - n(t) + 0.5}{n(t) + 0.5}\right)$$

$$\text{Score}_{\text{raw}}(C, Q) = \sum_{t \in Q} \text{IDF}(t) \sum_{\text{fld} \in \{\text{name}, \text{syn}, \text{def}\}} w_{\text{fld}} \cdot \frac{f(t, C_{\text{fld}}) \cdot (k_1 + 1)}{f(t, C_{\text{fld}}) + k_1 \cdot \left(1 - b + b \cdot \frac{|C_{\text{fld}}|}{\max(\text{avgdl}_{\text{fld}}, 1.0)}\right)}$$

Where field weights are $w_{\text{name}} = 2.5$, $w_{\text{syn}} = 2.0$, $w_{\text{def}} = 1.0$. If $\text{avgdl}_{\text{fld}} == 0$ across the corpus, that field's contribution is defined as 0, preventing division by zero.

#### 3. Symmetric Normalization & Division-by-Zero Protection
To ensure scores map strictly into $[0.0, 1.0]$, raw scores are normalized symmetrically. When evaluating $\text{Score}_{\text{raw}}(Q, Q)$, the query $Q$ is treated as a pseudo-document with its token multiset assigned to the `name` field ($|Q_{\text{name}}| = |Q|$, $Q_{\text{syn}} = \emptyset$, $Q_{\text{def}} = \emptyset$) against corpus $\text{avgdl}_{\text{name}}$:

$$\text{denom} = \max\left(\text{Score}_{\text{raw}}(C, C_{\text{name}}), \text{Score}_{\text{raw}}(Q, Q)\right)$$
Where $\text{Score}_{\text{raw}}(C, C_{\text{name}})$ treats $C_{\text{name}}$ tokens as query $Q_C$ evaluated against all weighted fields of concept $C$.

$$\text{Score}_{\text{norm}}(C, Q) = \begin{cases} 0.0000 & \text{if } \text{denom} \le 0 \\ \min\left(1.0, \frac{\text{Score}_{\text{raw}}(C, Q)}{\text{denom}}\right) & \text{if } \text{denom} > 0 \end{cases}$$

- **Quantization Precedence**: Normalized scores are rounded to 4 decimal places via `ROUND_HALF_EVEN` and coerced to positive zero (`+ 0.0`) **prior to** threshold evaluation.
- **Field Typing & Serialization**: The core ontology model types `ExternalMapping.confidence` strictly as `float | None`. In-memory Okapi BM25 matcher computes with `Decimal(28)` and quantizes via `ROUND_HALF_EVEN`, then converts to `float` at the matcher boundary before assignment to the core model, guaranteeing standard JSON number serialization and canonical JCS hash compatibility.
- **Normative Grounding Threshold**: A concept candidate is accepted as grounded if:
  $$\text{Score}_{\text{quantized}}(C, Q) \ge 0.6500$$
  Boundary test vectors assert that raw `Decimal("0.64995")` quantizes to `Decimal("0.6500")` (grounded, float `0.65`), whereas raw `Decimal("0.64994")` quantizes to `Decimal("0.6499")` (ungrounded, float `0.6499`).
- **Name/Synonym Hit Gate**: If a candidate achieves $\text{Score}_{\text{quantized}} \ge 0.6500$ based purely on definition tokens without matching at least one token from the concept's `concept_name` or `synonyms`, the normalized score is capped at `0.6400`, preventing spurious definition-only false positive matches.

---

## 5. Dual-Mode Construction Engine & Clarification Harness

### 5.1 Construction Strategy Pattern
The construction subsystem (`packages/opendv-service/src/opendv_service/construction`) defines a unified strategy pattern dispatched by the session configuration:

```python
class ConstructionEngine(Protocol):
    async def construct_proposal(
        self,
        session_id: str,
        base_ontology: GraphAOntology,
        evidence_chunks: list[EvidenceChunk],
        clarification_answers: dict[str, Any],
        matcher: ConceptMatcher,
        options: ConstructOptions,
    ) -> ProposalRecord: ...
```

### 5.2 Agent-First Construction Mode (3-Persona LLM Agent Harness)
In Agent-First mode, the engine orchestrates a coordinated team of three specialized agent personas, governed by the OpenDV Agent Harness (`packages/opendv-agent-harness`):

| Persona | Model Tier | Core Responsibilities & Artifact Flow |
|---|---|---|
| **1. `@term-scout`** | `FAST_TIER` | High-throughput entity, attribute, abbreviation, and data type extraction from chunk evidence stream. |
| **2. `@domain-clarifier`** | `HIGH_REASONING` | Polysemy and ambiguity evaluation; generates targeted multiple-choice questions for human stewards. |
| **3. `@ontology-architect`** | `HIGH_REASONING` | Synthesizes `CanonicalBusinessConcept`, verifies FIBO alignment, constructs Links/Sats, emits `OntologyDelta`. |

### 5.3 Deterministic Fallback Pipeline & CI Grounding Guard
When running in air-gapped CI environments, local workstations without API credentials, or during regression benchmarks, the engine executes the **Deterministic Fallback Pipeline**:
1. **Schema Heuristics Engine**:
   - Low-cardinality discrete text columns (including those ending in `_code`) route to Reference Enumerations (`ref_<slug>`), taking precedence over the Hub heuristic.
   - Remaining primary/business key columns ending in `_id`, `_pk`, or `_key` generate Hub CBC candidates (`hub_<entity>`).
   - Foreign key pairs (`<entity1>_id` and `<entity2>_id` co-occurring in the same table) generate Link candidates (`lnk_<entity1>_<entity2>`).
   - Descriptive non-key columns (`name`, `description`, `amount`, `status`) generate Satellite attributes on the primary Hub.
2. **Local BM25 Index Lookup**:
   Each candidate concept is queried against the local in-memory FIBO index.
3. **Automated CI Grounding Enforcement (Zero-Waiver Policy)**:
   In unattended CI or deterministic mode (where human stewards or admins cannot provide interactive waivers), the engine applies an automated grounding filter:
   - Candidates achieving verified FIBO grounding $\ge 0.6500$ are included in the generated `OntologyDelta`.
   - Candidates failing the threshold are **held back** in `evidence/staged_candidates.json` as ungrounded recommendations and are **NOT** committed.
   - **Dependency Closure Guard**: Any dependent candidates (such as `AddRelationshipOp` Link or Satellite candidates) referencing a held-back Hub candidate are transitively held back to prevent dangling relationship endpoints.
   - **Grandfathered Seed Concept Guard**: The hold-back filter calls `is_concept_mutated(base, delta, concept_uri)`: if a touched grandfathered concept lacks verified FIBO grounding $\ge 0.6500$, proposed mutations from the 16-operation set (§7.3.3) targeting it are held back in `evidence/staged_candidates.json` rather than committed.
   - **Physical Identifier Budget Guard**: If deterministic table slugs (`hub_*`, `sat_*`, `lnk_*`, `t_lnk_*`, `ref_*`) exceed 55 characters, candidates route to `evidence/staged_candidates.json` as held-back candidates rather than triggering `INVALID_PHYSICAL_TABLE_NAME` in CI.
   This guarantees that automated runs never emit ungrounded CBCs or dangling endpoints, satisfying ONT-EVO-002 with 100% compliance.
4. **Pure Additive Delta Assembly**:
   The engine packages verified entities into an `OntologyDeltaRequest` with author `"deterministic:pipeline"`.

### 5.4 Clarification Question Harness
The clarification harness bridges machine ambiguity and human domain expertise:
- Questions are persisted in `<session_dir>/clarifications/questions.json`.
- Each question defines a unique `question_id`, `focus_concept`, `question_text`, a list of structured `options` with semantic rationales, and an `allow_custom_text` flag.
- Human responses submitted via `POST .../answers` are written to `<session_dir>/clarifications/answers.json`.
- On subsequent construction runs, the engine injects accepted answers as binding semantic constraints, overriding heuristic guesses.

---

## 6. Content-Addressable Storage (CAS) & Session Architecture

### 6.1 Root Directory Layout
Studio sessions are isolated on disk under `.opendv/ontology_studio/<session_id>/` with restricted permissions (directory mode `0700`, files `0600`):

- **Root Metadata & Security**: `.session.lock` (exclusive L5 lock), `.session_secret` (0600 DACL HMAC secret), `session.json` (authoritative state, active version, committed versions lineage), `taint_index.json` (confidential taint index), `audit.jsonl` (hash-chained append-only log), `feedback.jsonl` (steward quality ratings).
- **Inputs & CAS**: `inputs/manifest.json` (ingested file registry), `inputs/cas/<sha256>` (immutable source blobs).
- **Evidence**: `evidence/chunks.jsonl` (normalized text chunks, UTF-16/codepoint offsets), `evidence/tables.jsonl` (cell structures), `evidence/staged_candidates.json` (held-back ungrounded terms).
- **Clarification & Proposals**: `clarifications/` (`questions.json`, `answers.json`), `proposals/<proposal_id>.json` (pending additive deltas).
- **Committed Versions**: `versions/vX.Y.Z/` (`ontology.yaml`, `ontology.json`, `delta.json`, `meta.json`, `evaluation.json`).
- **Background Jobs**: `jobs/<job_id>.json` (status), `jobs/<job_id>.events.jsonl` (W3C SSE journal), `jobs/.job_<job_id>.lock` (L2 job lease lock).

### 6.2 Multi-File Atomic Commit Protocol & Startup Recovery
To prevent torn reads, lost updates, or corrupt session states during process crashes or concurrent access, all version commits adhere to an atomic staging and recovery protocol:

1. **Canonical Digest Computation**: Compute RFC 8785 Canonical JCS JSON and SHA-256 digest over target ontology model.
2. **Precondition Verification Under Lock**: Acquire exclusive session lock `<session_dir>/.session.lock` [L5] (30s timeout). Run startup/crash reconciliation (§6.2.1) immediately upon acquiring L5. Read `session.json`; verify `head_sha256 == parent_sha256` and `target_version > active_version`. Read current `audit_chain_head_sha256` from `session.json`.
3. **Staged Artifact Serialization**: Write version artifacts (`ontology.yaml`, `ontology.json`, `delta.json`, `meta.json`, `evaluation.json`) into isolated staging directory `<session_dir>/.staging-<u>/`. `meta.json` records `target_sha256`, `parent_sha256`, `proposal_id`, and anchors the known pre-commit `audit_chain_head_sha256`. Flush and sync all file descriptors.
4. **Atomic Directory Rename & Directory Sync**: Atomic directory move `os.replace("<session_dir>/.staging-<u>", "<session_dir>/versions/vX.Y.Z")`. Prior to Step 5, explicitly call `fsync` on parent directory descriptor (`FlushFileBuffers` / `MOVEFILE_WRITE_THROUGH` on Windows), guaranteeing the directory entry is durable on physical media before `session.json` is replaced.
5. **Atomic Metadata Commit (The Commit Point)**: Write updated `session.json` (`active_version = "vX.Y.Z"`, `head_sha256 = target_sha256`, `committed_versions`) to `.session.json.<u>.tmp` and `os.replace` to `session.json` with `fsync` (exponential backoff with jitter on Windows WinError 32). If Step 5 fails while alive, supervisor reverts `versions/vX.Y.Z` to `.staging-<u>`; on crash, startup reconciliation (§6.2.1.3) auto-completes. `session.json` is the sole authoritative commit point.
6. **Durable Proposal State & Audit Append**: Under L5: if `proposal_id` is set, atomically update `proposals/<proposal_id>.json` to status `"ACCEPTED"`. Append the commit record to `<session_dir>/audit.jsonl` (chained after current tail `audit_chain_head_sha256`), and advance `session.audit_chain_head_sha256`. Both actions are idempotent and re-applied during recovery if interrupted.
7. **Lock Release**: Release `.session.lock` [L5].


#### 6.2.1 Deterministic Startup & Per-Operation Crash Recovery Rule
Under `.session.lock` [L5], crash recovery is executed at server startup and at the beginning of **every** operation that acquires L5 prior to any read or mutation:
1. **Missing Active Version Directory Recovery**:
   If `versions/<session.active_version>` is missing on disk: inspect `.staging-*` directories for one whose `meta.json.target_sha256 == session.head_sha256`. If found, execute atomic rename to `versions/<session.active_version>` and sync `versions/` directory fd, repairing interrupted Step 4/Step 5 power loss.
2. **Staging Cleanup Safety Invariant**:
   Abandoned `.staging-*` directories older than 1 hour are pruned **strictly with the exception** that any staging directory whose `meta.json.target_sha256` matches `session.head_sha256` or any entry in `session.committed_versions` is NEVER deleted.
3. **Orphan Version Directory Reconciliation**:
   An orphan version directory is defined strictly as a directory `versions/vX.Y.Z` where numeric SemVer tuple comparison `(int(major), int(minor), int(patch))` exceeds that of `session.active_version` and the version is absent from `committed_versions` (membership in `session.committed_versions` always takes precedence). Historical committed versions in the lineage are immutable and NEVER modified or quarantined. For any uncommitted forward directory `versions/vX.Y.Z`:
   - If left by an abnormal process termination where `meta.json.parent_sha256 == session.head_sha256` and `compute_ontology_sha256(load(ontology.json))` matches `meta.json.target_sha256`, the supervisor completes the interrupted commit: updates `session.json` (`active_version = "vX.Y.Z"`, `head_sha256 = meta.json.target_sha256`), marks `proposals/<pid>.json` as `"ACCEPTED"`, and appends an audit recovery event.
   - If the computed digest does not match, or if `parent_sha256 != session.head_sha256`, only then is that uncommitted forward directory moved to `.quarantine/<dirname>-<timestamp>`.
4. **Active Version & Forward-Only Audit Chain Reconciliation**:
   Under L5, the supervisor inspects `audit.jsonl`. The physical tail record (`tail_sha256`) is accepted as the new session anchor if and only if `session.audit_chain_head_sha256` appears in the chain as an ancestor of `tail_sha256` (verified by tracing `prev_audit_sha256` links backward from the tail). If verified, the supervisor advances `session.audit_chain_head_sha256 = tail_sha256` in `session.json`. If `tail_sha256 != session.audit_chain_head_sha256` and the session anchor is missing from the chain (indicating truncation, data loss, or tampering), the supervisor raises `TAMPER_EVIDENT_CHAIN_CORRUPT` (HTTP 422, exit code 4), quarantines the session, blocks mutating operations, and rejects synthetic recovery appends. Next, for `versions/<session.active_version>/meta.json`: if `meta.proposal_id` exists and `proposals/<meta.proposal_id>.json` status is not `"ACCEPTED"`, update its status to `"ACCEPTED"`. If no commit record exists in `audit.jsonl` (or historical `audit.checkpoint.*.json` archives) for `(session.active_version, meta.target_sha256)`, construct and append the missing commit event chained directly to `tail_sha256`, advancing `session.audit_chain_head_sha256`.

### 6.3 Total Lock Hierarchy & Concurrency Protection
To guarantee deadlock-free operation across asynchronous workers, background job runners, and CLI invocations, locks must be acquired in a strict, total global order:

$$\mathbf{L}_1: \mathtt{.local\_token.lock} \;\longrightarrow\; \mathbf{L}_2: \mathtt{.job\_<jid>.lock} \;\longrightarrow\; \mathbf{L}_3: \mathtt{.journal\_<jid>.lock} \;\longrightarrow\; \mathbf{L}_4: \mathtt{graph\_a.lock} \;\longrightarrow\; \mathbf{L}_5: \mathtt{.session.lock}$$

- All lock acquisitions enforce a default 30-second timeout.
- Failure to acquire a lock within the timeout window raises `LockTimeoutError("LOCK_TIMEOUT")` (HTTP 409, exit code 6).
- **Multi-Session Lock Ordering**: When an operation requires acquiring locks on multiple sessions simultaneously (such as workspace promotion intent recovery involving another session), session locks are acquired strictly in lexicographical order of their session ID strings (`min(s1, s2) -> max(s1, s2)`), preventing cross-session deadlocks.
- **Session-Scoped State Mutation Under $\mathbf{L}_5$**: All mutations of session state files (`inputs/manifest.json`, `taint_index.json`, `evidence/chunks.jsonl` tombstones, `clarifications/*`, `proposals/*`, `staged_candidates.json`, `feedback.jsonl`) are serialized exclusively under `.session.lock` [$\mathbf{L}_5$]. Each write uses atomic temporary file plus `os.replace` and executes §6.2.1 recovery. Source registration writes taint directly into manifest entries and `taint_index.json` atomically in the same $\mathbf{L}_5$ write. Proposal state transitions (`PENDING \to ACCEPTED`, `PENDING \to REJECTED`, `STALE \to PENDING`) execute as atomic compare-and-set operations under $\mathbf{L}_5$.
- **Subsystem Concurrency & Replay Starvation Prevention**: Jobs holding active job lease $\mathbf{L}_2$ must release journal lock $\mathbf{L}_3$ before acquiring $\mathbf{L}_4$ or $\mathbf{L}_5$. SSE telemetry events are buffered in memory and flushed to `jobs/<job_id>.events.jsonl` under $\mathbf{L}_3$ only after releasing $\mathbf{L}_5$, strictly preventing starvation of SSE replay readers and lock-order inversion.
- **Reverse Release Order**: Locks are released in reverse order of acquisition ($\mathbf{L}_5 \to \dots \to \mathbf{L}_1$). As an explicit exception, early release of a temporary foreign session lock ($\mathbf{L}_5(s_{\text{intent}})$) during promotion recovery is permitted while retaining local $\mathbf{L}_5(s_{\text{curr}})$ to minimize foreign lock hold time.

### 6.4 Crash Recovery & Job Leases
Background tasks (parsing, clarification question generation, construction) write active job leases to `jobs/.job_<jid>.lock`:
- On platform API startup, a background supervisor scans `<studio_root>/*/jobs/` for unclosed jobs in `RUNNING` or `PENDING` states.
- If a job's lock can be acquired non-blockingly, the job process has terminated abnormally (process crash or OOM).
- The supervisor marks the job as `FAILED` in `<jid>.json`, appends a terminal error event `JOB_ABANDONED_SERVER_CRASH` (exit code 1) to `<jid>.events.jsonl`, and cleans up orphaned lock files.

### 6.5 CAS Garbage Collection & Source Removal
Sources in CAS are managed via `manifest.json` and mark-and-sweep GC:
- **Removal**: Marked tombstone in `manifest.json` via `DELETE .../sources/{source_id}`. Tombstoning a source also marks or removes all chunks originating from that source in `evidence/chunks.jsonl`, ensuring that tombstoned source chunks no longer serve as reachability roots.
- **Roots of Reachability**: (1) active (non-tombstoned) sources in `inputs/manifest.json`, (2) active citations in `evidence/chunks.jsonl` from non-tombstoned sources, (3) `EvidenceRef` targets in pending `proposals/*.json`, (4) citations in committed `versions/*/ontology.json`.
- **Enforcement**: Deleting sources cited by pending proposals raises `SOURCE_REFERENCED_BY_PENDING_PROPOSAL` (exit 4); deleting sources cited in committed versions raises `SOURCE_REFERENCED_BY_COMMITTED_VERSION` (exit 4). Only completely unreferenced orphan blobs are purged.

---

## 7. ONT-EVO Governance Rules & Model Verification

### 7.1 Two-Phase Validation Layering
Validation is partitioned into two distinct architectural layers:

1. **Phase 1: Structural Validation (`opendv_core.ontology.structural_validator`)**:
   Pure in-memory primitive validation (zero state or filesystem access). Validates URI syntax, `concept_name` grammar, 1:1 Hub bijection, taxonomy DAG acyclicity via Tarjan's strongly connected components algorithm, and scans for reserved provisional URI tokens (`urn:opendv:provisional:`).
2. **Phase 2: Governance Validation (`opendv_service.ontology.governance_validator`)**:
   State-aware business and reference ontology validation. Enforces deployed physical table contract compatibility (ONT-EVO-001), positive semantic grounding verification (ONT-EVO-002: BM25 $\ge 0.6500$ or administrative waivers), grandfathered seed concept exemption rules, and confidential taint/HMAC pseudonym leakage scanning.


### 7.2 ONT-EVO-001: Immutability & Deployed Contract Protection
Committed ontology versions are permanent and immutable. Once a version has been promoted to a physical Data Vault workspace, physical table contracts take effect:

#### Authoritative 24-Operation Governance, SemVer & Deployed Compatibility Matrix
Destructive operations targeting deployed physical constructs are strictly blocked with HTTP 422 `ONT_EVO_CONTRACT_VIOLATION` (exit code 4). Additive and metadata operations are permitted. SemVer bump and RBAC clearance govern all version transitions:

| Operation Name | Classification | SemVer Bump | RBAC Role | Deployed Artifact Compatibility |
|---|---|---|---|---|
| `ReviseDescriptionOp` | Metadata | Patch (+0.0.1) | `editor` | PERMIT (Metadata update) |
| `RescoreExternalMappingOp` | Metadata | Patch (+0.0.1) | Server only | PERMIT (Confidence refresh) |
| `AddConceptOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Creates `hub_<slug>`) |
| `AddRelationshipOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Creates `lnk_<slug>`) |
| `AddAttributeOp` (`optional=True`) | Additive | Minor (+0.1.0) | `editor` | PERMIT (Expands `sat_<slug>`) |
| `AddAttributeOp` (`optional=False`) | Destructive* | Major (+1.0.0)* | `admin`* | Blocked if deployed/existing; Additive (Minor/editor) on new concept |
| `AddSynonymOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Metadata update) |
| `AddExternalMappingOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Metadata update) |
| `AddReferenceEnumOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Creates `ref_<slug>`) |
| `AddReferenceEnumCodeOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Expands reference codes) |
| `SetParentConceptOp` | Additive* | Minor (+0.1.0) | `editor` | PERMIT (Initial parent; reparent requires `ReparentConceptOp`) |
| `SetConceptElmArchetypeOp` | Additive | Minor (+0.1.0) | `editor` | PERMIT (Metadata update) |
| `DeleteConceptOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `hub_<slug>` deployed (422 / exit 4) |
| `RenameConceptOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `hub_<slug>` deployed (422 / exit 4) |
| `DeleteRelationshipOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `lnk_<slug>` deployed (422 / exit 4) |
| `RenameRelationshipOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `lnk_<slug>` deployed (422 / exit 4) |
| `ReparentConceptOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `hub_<slug>` deployed (422 / exit 4) |
| `RemoveAttributeOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `sat_<slug>` deployed (422 / exit 4) |
| `RenameAttributeOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `sat_<slug>` deployed (422 / exit 4) |
| `ChangeAttributeTypeOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `sat_<slug>` deployed (422 / exit 4) |
| `SetAttributeRequiredOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `sat_<slug>` deployed (422 / exit 4) |
| `DeleteReferenceEnumOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `ref_<slug>` deployed (422 / exit 4) |
| `RenameReferenceEnumOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `ref_<slug>` deployed (422 / exit 4) |
| `RemoveReferenceEnumCodeOp` | Destructive | Major (+1.0.0) | `admin` | Blocked if `ref_<slug>` deployed (422 / exit 4) |
| `DeprecateExternalMappingOp` | Destructive | Major (+1.0.0) | `admin` | PERMIT (Metadata deprecation) |
| *Any unclassified operation* | Unknown | Major (+1.0.0) | `admin` | Blocked (Fails closed) |

*\* Note: `AddAttributeOp(optional=False)` is classified as Destructive (Major bump, admin role) strictly when targeting existing or deployed concepts; when attached to a brand-new concept introduced in the same delta, it is Additive (Minor bump, editor role). `SetParentConceptOp` is additive strictly when setting initial parent (`parent_concept_uri is None`). If already set, reparenting requires `ReparentConceptOp` (Destructive, Major bump, admin role).*

#### Physical Table Name Slug Derivation & Identifier Budget
Physical table names are derived deterministically to prevent truncation across Snowflake, BigQuery, Databricks, and DuckDB:
- **Prefix Allocations**: 63-char budget (PostgreSQL/DuckDB limit). Base slug $\le 55$ chars ensures compliance: 4-char prefixes (`hub_`, `lnk_`, `sat_`, `hal_`, `ref_` $\to 59 \le 63$) and 6-char prefix (`t_lnk_` $\to 61 \le 63$).
- **Validation Rule**: `canonical_slug = re.sub(r'[^a-z0-9_]', '_', name.lower()).strip('_')` is checked before truncation. If length $> 55$, validator raises `INVALID_PHYSICAL_TABLE_NAME` (HTTP 422, exit 4). If empty after stripping, fallback `concept_<sha256[:8]>` is used. Distinct concepts producing identical slugs raise `PHYSICAL_TABLE_NAME_COLLISION` (exit 4).

#### Deployment Registry as Authoritative Source of Truth
The authoritative source of truth for deployed physical tables is the **Deployment Registry**: composed of the active contract file in the bound workspace (`target_contract_path`), verified physical metadata from the database catalog, and the session's promotion records (`promoted_at`, `workspace_sha256`) stored in `session.json`. The governance validator queries this registry during delta validation and promotion to enforce AC-7.

### 7.3 ONT-EVO-002: Positive Grounding & Anti-Silo Mandate
To prevent the proliferation of ungrounded, proprietary data silos, every concept in an evolved ontology must satisfy positive grounding:

1. **Grounding Dual-Path Model**:
   Every concept touched or added in a delta must satisfy positive grounding through at least **one** of the following two authoritative paths:
   - **Path A (Reference Pack Grounding)**: At least one direct `skos:exactMatch` or `skos:closeMatch` external mapping to a registered reference ontology (e.g. FIBO). The governance validator recomputes BM25 confidence scores using canonical query `tokens(concept_name) \cup synonyms` via the pinned in-memory `ConceptMatcher` and verifies target URI existence in the registered reference pack (`baseline-digests.json`), requiring verified score $\ge 0.6500$. (Transitive `skos:broadMatch` superclass closures are excluded from satisfying Path A).
   - **Path B (Administrative Grounding Waiver)**: An explicit administrative waiver recorded in `meta.json` with a mandatory rationale $\ge 10$ characters (`low_confidence_grounding_waiver=True`), validated at Error Precedence Step 3 (requiring admin role; non-admin triggers `FORBIDDEN_ADMIN_REQUIRED` exit 7) and Step 8 (`INVALID_GROUNDING_WAIVER` exit 4). Supporting CAS evidence citations (`EvidenceRef` spans) may optionally be attached in metadata to substantiate the waiver rationale, but evidence citations alone without an administrative waiver do not bypass ONT-EVO-002.
2. **Ungrounded Concept Rejection**:
   A concept touched or added in a delta that satisfies neither Path A nor Path B is rejected with `ONT_EVO_UNGROUNDED_CONCEPT` (HTTP 422, exit code 4). Concepts with zero qualifying reference mappings and zero administrative waivers fail grounding and are strictly rejected.
3. **Grandfathered Seed Concept Exemption & Mutation Invalidation Rule**:
   Concepts seeded from an existing workspace ontology root (recorded in `grandfathered_concept_uris` in `meta.json`) are exempt from positive grounding **only while untouched**.
   The governance validator (`is_concept_mutated(base, delta, concept_uri) -> bool`) evaluates whether a delta touches concept $C$ against the authoritative 16 concept-mutating operations:
   - Direct concept mutations: `RenameConceptOp`, `ReparentConceptOp`, `ReviseDescriptionOp`, `SetConceptElmArchetypeOp`, `SetParentConceptOp`.
   - Concept attribute mutations: `AddAttributeOp`, `RenameAttributeOp`, `RemoveAttributeOp`, `ChangeAttributeTypeOp`, `SetAttributeRequiredOp`.
   - Mapping & synonym mutations: `AddSynonymOp`, `AddExternalMappingOp`, `DeprecateExternalMappingOp`, `RescoreExternalMappingOp` (when rescore drops score $< 0.6500$).
   - Direct relationship endpoint mutations (where $C$ is `from_concept_uri` or `to_concept_uri`): `DeleteRelationshipOp`, `RenameRelationshipOp`.
   - New relationships (`AddRelationshipOp`) modify link topology without voiding grandfathered status of existing endpoints unless $C$ itself is mutated.
   Any mutating operation from this 16-operation set targeting a grandfathered concept voids its exemption, requiring independent ONT-EVO-002 positive grounding ($\ge 0.6500$ or administrative waiver $\ge 10$ chars).
4. **Provisional Token Leak Guard**:
   Ontologies are scanned for provisional URIs matching `(?i)urn:opendv:provisional:`. Presence of provisional URIs in a commit or promotion request triggers `ONT_EVO_GROUNDING_VIOLATION` (exit 4).

### 7.4 ONT-EVO-003: Concurrency Control & Proposal Rebase

#### Canonical 11-Step Error Precedence Table
To ensure deterministic error responses across both `opendv` and `opendvctl`, errors are evaluated in a strict 11-step pipeline. `schemas/v1/error-codes.json` is the sole authoritative registry for error code mappings:

| Step | Phase Name | Evaluation Scope | HTTP Status | CLI Exit Code | Canonical Error Examples |
|---|---|---|---|---|---|
| 0 | CLI Syntax | Argument parsing, missing required flags, mutually exclusive flags | 400 | **2** | `SYNTAX_INVALID_ARGUMENT`, `MISSING_AUTO_COMMIT_PRECONDITION` |
| 1 | Authentication | Token presence, validity, loopback header verification | 401 | **7** | `UNAUTHENTICATED`, `TOKEN_EXPIRED` |
| 2 | Lexical Preconditions | URI regex formatting, stateless offset ordering ($0 \le \text{start} < \text{end}$) | 400 | **2** | `SYNTAX_INVALID_ARGUMENT` |
| 3 | RBAC Authorization | Static role clearance (viewer, editor, admin), automated actor restrictions, administrative waiver checks (`--low-confidence-waiver` and `--allow-self-accepted-proposals` require `admin` role; automated actors strictly forbidden from issuing waivers). State-dependent RBAC (e.g. destructive classification requiring admin for `AddAttributeOp(optional=False)` on existing concepts, or proposal author check) is evaluated deterministically after session/proposal resource resolution (Step 7). | 403 | **7** | `FORBIDDEN_ADMIN_REQUIRED`, `AGENT_OPERATION_FORBIDDEN` |
| 4 | Target Containment | Workspace root containment check evaluated strictly after RBAC | 400 | **2** | `INVALID_WORKSPACE_PATH`, `REMOTE_SERVER_PATH_FORBIDDEN` |
| 5 | Body Syntax & Duplicates | Pydantic model validation, duplicate synonyms, duplicate attribute names | 422 / 400 | **4** (syntax: **2**) | `DUPLICATE_SYNONYM`, `DUPLICATE_ATTRIBUTE_NAME`, `AMBIGUOUS_EXTERNAL_MAPPING` |
| 6 | Envelope Verification | Additive envelope vs destructive steward revision envelope (missing session short-circuits with `SESSION_NOT_FOUND` exit 3 before envelope check) | 422 | **4** | `ONT_EVO_ENVELOPE_VIOLATION` |
| 7 | Resource Lifecycle | Session existence, proposal state (PENDING), job status | 409 / 404 | **6** (not found: **3**) | `PROPOSAL_ALREADY_RESOLVED`, `SESSION_NOT_FOUND` |
| 8 | Semantic Waivers & Rationale | Waiver rationale completeness ($\ge 10$ chars), promotion rationale | 422 / 400 | **4** (missing: **1**) | `INVALID_GROUNDING_WAIVER`, `PROMOTION_RATIONALE_REQUIRED` |
| 9 | Optimistic Concurrency | `If-Match` ETag check, session head SHA-256 vs base SHA-256 | 412 | **5** | `ONT_EVO_CONCURRENT_MODIFICATION` |
| 10 | Domain & Referential | Evidence bounds within source codepoints, ONT-EVO-001 deployed table checks, positive grounding, DAG acyclicity | 422 | **4** | `INVALID_EVIDENCE_OFFSETS`, `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `HIERARCHICAL_CYCLE_DETECTED` |

#### Proposal Rebase Algorithm & Mandatory Re-Validation
Proposal states are `PENDING`, `STALE` (derived when concurrent commits advance session head SHA), `ACCEPTED`, and `REJECTED`. A `STALE` proposal is rebased via `POST .../proposals/{pid}/rebase`:
1. **Conflict Key Evaluation**:
   Evaluates read/write conflict keys across intervening committed operations (between `proposal.base_sha256` and current head) and the proposal operations: target concept, relationship, attribute, or enum URIs. If touched URIs intersect, rebase aborts with `ONT_EVO_CONCURRENT_MODIFICATION` (HTTP 412, exit code 5).
2. **Mandatory Two-Phase Re-Validation**:
   After re-anchoring operations onto the new head SHA, the rebase engine **runs full structural and governance validation** on the synthesized target ontology:
   - Verifies that new inheritance relationships do not form taxonomy cycles (`HIERARCHICAL_CYCLE_DETECTED` exit 4).
   - Verifies that newly combined entity names do not collide on 55-character physical slugs (`PHYSICAL_TABLE_NAME_COLLISION` exit 4).
   Only upon clean validation does the proposal transition from `STALE` back to `PENDING` ready for human acceptance.

#### Comprehensive 24-Operation SemVer Calculus & Genesis Transition Rule

##### Greenfield Genesis & 0.x Draft Transitions ($V < \mathtt{1.0.0}$)
1. **Initial CBC Promotion Rule**: From any pre-release draft state `0.y.z` (including `0.0.0`), the first commit that introduces at least one Canonical Business Concept (`AddConceptOp`) transitions the active version directly to `1.0.0` (Initial Release), establishing the first stable major release.
2. **Pre-CBC Draft Increments**: While the ontology remains in draft state without CBCs (e.g. at `0.1.0`), additive reference or metadata operations advance the patch or minor version capped at `0.(y+1).0` or `0.y.(z+1)`. While $V < 1.0.0$, major-class destructive operations bump the draft minor version (`0.(y+1).0`) under `admin` role, ensuring that strictly `AddConceptOp` (initial CBC release) triggers the transition to `1.0.0`.

##### Post-Genesis SemVer Bumping ($V \ge \mathtt{1.0.0}$)
For all subsequent releases, SemVer bumping applies the maximum-precedence rule:
$$\text{Delta\_Bump} = \max_{op \in \text{delta}} \left(\text{Bump}(op)\right)$$
Using the authoritative operation classification, SemVer bump, and RBAC clearance defined in the Authoritative 24-Operation Matrix in §7.2.


---

## 8. Security Model, Isolation & Observability

### 8.1 Pre-LLM Data Protection & Redaction
The Studio operates under zero-trust data protection principles to prevent confidential client data from leaking to third-party LLM inference APIs:

1. **Confidential Source Taint (`do_not_send_to_agent=True`)**: When uploaded with confidential flag, raw text is suppressed from LLM agent prompts.
2. **Taint Propagation**: Any concept, attribute, or chunk derived from a tainted source inherits taint status in `<session_dir>/taint_index.json`.
3. **HMAC Pseudonymization**: When querying LLM agents, tainted entity names and URIs are pseudonymized using HMAC-SHA256 with `<session_dir>/.session_secret`:
   $$\mathtt{Concept\_Pseudonym} = \mathtt{HMAC\_SHA256}(\mathtt{session\_secret}, \mathtt{concept\_uri})[:16]$$
4. **PII & Data Scrubbing**: Prompts emitted to agents are scanned for confidential leakage. Raw tainted spans, unpseudonymized URIs, or unresolved markers trigger `REDACTED_PLACEHOLDER_LEAK` (exit 4), halting execution.

### 8.2 Explicit Server Modes & Authentication Guard
To eliminate authentication bypass risks behind reverse proxies and cleanly support remote CLI modes, the platform defines an explicit server mode configured at startup:

```python
ServerMode = Literal["local_standalone", "remote_platform"]
```

1. **`local_standalone` Mode (Default for Local Developers & Workbench)**:
   - The server binds strictly to loopback interfaces (`127.0.0.1` and `::1`).
   - Reverse proxy forwarding headers (`X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`) are **strictly ignored**.
   - Host header allowlist is locked to `{"localhost", "127.0.0.1", "[::1]"}`, parsed as `host[:port]` where port must match the configured platform bind port.
   - `POST /init` issues `.local_token` (permissions `0600`).
   - `init --force` authorization: For HTTP route `POST /init?force=true`, token rotation requires possession of the existing token (`Authorization: Bearer <local_token>`), preventing unprivileged loopback callers from rotating the token. The OS caller process UID/SID filesystem ownership check applies to the in-process CLI path (`LocalExecutor` in `opendv ontology studio init --force`), where the CLI directly verifies `os.getuid()` / Windows token SID matching `.local_token` owner without network indirection.
2. **`remote_platform` Mode (Shared Enterprise / Hosted Deployment)**:
   - Enabled via startup configuration (`server_mode: remote_platform` or `--remote`).
   - Loopback administrative elevation and `POST /init` are **completely disabled at the routing layer** (`LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE` HTTP 403, exit code 7).
   - Authentication relies strictly on standard HTTP `Authorization: Bearer <token>` headers or mTLS certificates.
   - In M2, the interactive Web Workbench SPA (`/ontology`) is supported in `local_standalone` mode via loopback and launch nonce; in `remote_platform` mode, remote interaction is driven via `opendvctl` with Bearer tokens; browser SSO (OIDC/SAML to secure HttpOnly cookie) is scheduled for Milestone M4.
   - The Host header allowlist is configured dynamically via `OPENDV_ALLOWED_HOSTS`.
3. **Single-Use Browser Launch Nonce & Authenticated Out-of-Band Minting**:
   Launch nonces are NEVER minted as an unauthenticated side effect of HTTP GET requests to `/ontology` (unauthenticated GET requests redirect to auth gate without a nonce). Nonces are minted strictly through authenticated channels: (1) CLI `init` (`opendv ontology studio init`), outputting launch URL with fragment (`http://localhost:8000/ontology#token=<nonce>`) to stdout; (2) platform startup in local mode (stdout); or (3) authenticated loopback `POST /auth/launch-nonce` with `Authorization: Bearer <local_token>`. The nonce is a 256-bit token with 60s TTL in server memory. Landing page sets `Referrer-Policy: no-referrer` and exchanges nonce via `POST .../auth/token` for an `HttpOnly`, `SameSite=Strict` session cookie, invalidating the nonce to reject replays.
4. **CSRF Origin Protection**:
   State-mutating HTTP requests (`POST`, `PUT`, `DELETE`) authenticated via browser session cookie require the custom header `X-OpenDV-Workbench: 1`. Requests authenticated via HTTP `Authorization: Bearer <token>` headers do not require the CSRF header, maintaining full CLI parity for `opendvctl`.

### 8.3 Workspace Containment & Path Traversal Guards
1. **Workspace Boundary Enforcement**:
   Server-side workspace arguments (`--workspace`, `--from-workspace`) are resolved via `Path.resolve()` and checked against `OPENDV_WORKSPACE_ROOTS` using `try: os.path.commonpath([os.path.normcase(root), os.path.normcase(ws)]) == os.path.normcase(root) except ValueError: False` (escaping paths abort with `INVALID_WORKSPACE_PATH` HTTP 400, exit 2). Input payloads (`--file`) represent client-local data and are streamed without workspace containment restrictions.
2. **Session Identifier Sanitation**:
   Session identifiers are validated against regex `^[a-z0-9_-]{1,64}$`. Windows reserved device names (`nul`, `con`, `aux`, `prn`, `com1..9`, `lpt1..9`) and route keywords (`bundle`, `init`, `sessions`, `health`, `auth`, `admin`) are rejected unconditionally across all endpoints, CLI commands, and bundle operations with `INVALID_SESSION_IDENTIFIER` (HTTP 400, exit 2).

### 8.4 Zero-Trust Archive Import Security Limits
Importing portable session bundles (`.odvstudio.zip`) enforces streaming validation: max uncompressed size $\le 250.0\text{ MB}$, max entries $\le 500$, max compression ratio $100:1$ on entries $> 1.0\text{ MB}$. Relative path traversal (`..`, leading slashes, `\`, drive prefixes `C:`), symlink entries (`0o120000`), device files, and NFC/case-folding duplicate collisions trigger immediate rejection with `ARCHIVE_SECURITY_VIOLATION` (HTTP 422, exit code 4). Extraction takes place in an isolated temporary directory before paths are re-validated.

### 8.5 Observability Ledger & Local Audit Hash Chain
All state transitions, grounding decisions, user answers, and administrative waivers are recorded in an append-only ledger `<session_dir>/audit.jsonl`:
- **Lock-Protected Appends**: Appends to `audit.jsonl` are serialized exclusively under `.session.lock` (L5), preventing concurrent forks of the hash chain.
- Each record captures `timestamp`, `session_id`, `actor`, `action`, `target_version`, `head_sha256`, `details`, and `prev_audit_sha256`.
- **Audit Anchoring**: Each audit entry hashes preceding record bytes, forming a tamper-evident hash chain. The latest audit chain head SHA-256 is anchored directly in each committed version's `meta.json` (`audit_chain_head_sha256`), preventing retroactive ledger rewriting.
- **Retention & Checkpoint Chains**: In accordance with the 90-day retention policy, log rotation archives older entries into signed/hashed checkpoint records (`audit.checkpoint.<ts>.json`). Checkpoints cover and record historical commit anchors (and their Merkle root). The active ledger begins from `checkpoint_root_sha256`, allowing bundle import and forensic auditing to verify the cryptographic chain from genesis forward through all checkpoints to the active ledger.
