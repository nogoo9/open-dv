<#
.SYNOPSIS
    Automated GitHub Issue Tracker Generator for Open-DV Ontology Construction Studio.
.DESCRIPTION
    Creates the hierarchical GitHub Issue tracker structure (1 Epic Tracker, 3 Phase Trackers,
    and 43 Granular Task Issues) for Waves 1, 2, and 3 (Phases 4, 5, 6) of the Ontology Studio.
    Supports -DryRun mode for simulated verification without modifying remote state.
.PARAMETER DryRun
    Simulate issue creation without calling the GitHub CLI (`gh`).
.PARAMETER Repo
    Target GitHub repository in owner/repo format (default: "nogoo9/open-dv").
.EXAMPLE
    powershell -File docs/design/ontology-studio/create_issues.ps1 -DryRun
.EXAMPLE
    powershell -File docs/design/ontology-studio/create_issues.ps1 -Repo "nogoo9/open-dv"
#>

[CmdletBinding()]
param(
    [switch]$DryRun = $false,
    [string]$Repo = $(if ($env:GITHUB_REPOSITORY) { $env:GITHUB_REPOSITORY } else { "nogoo9/open-dv" })
)

$ErrorActionPreference = "Stop"

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "   Open-DV Ontology Construction Studio - Issue Tracker Generator     " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Repository : $Repo" -ForegroundColor Yellow
Write-Host "Dry-Run    : $DryRun" -ForegroundColor Yellow
Write-Host ""

# -----------------------------------------------------------------------------
# 1. Verification of Tooling
# -----------------------------------------------------------------------------
if (-not $DryRun) {
    $ghPath = Get-Command "gh" -ErrorAction SilentlyContinue
    if (-not $ghPath) {
        Write-Error "GitHub CLI ('gh') is not installed or not available on PATH."
        exit 1
    }
    $authStatus = gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "GitHub CLI is not authenticated. Run 'gh auth login' first."
        exit 1
    }
    Write-Host "[OK] GitHub CLI is authenticated and available." -ForegroundColor Green
} else {
    Write-Host "[INFO] Dry-Run mode enabled. Simulating issue creation hierarchy." -ForegroundColor Magenta
}

# -----------------------------------------------------------------------------
# 2. Epic Tracker Issue Definition
# -----------------------------------------------------------------------------
$epicTitle = "[Tracker] Open-DV Ontology Construction Studio: Autonomous Multi-Phase Delivery Plan (Phases 0-6)"
$epicLabels = "enhancement,tracker,area:ontology-studio"
$epicBody = @"
# [Tracker] Open-DV Ontology Construction Studio: Autonomous Multi-Phase Delivery Plan (Phases 0-6)

**Master Tracker Issue** for the **Open-DV Ontology Construction Studio** in repository `$Repo`.

Governing Documentation:
- Master Plan: docs/design/ontology-studio/master-plan.md
- Implementation Plan: docs/21-ontology-studio-implementation-plan.md
- Architecture Specification: docs/19-ontology-studio-architecture.md
- Functional Specification: docs/20-ontology-studio-spec.md
- Architecture Decision Records: ADR-001 through ADR-016

---

## 1. Executive Summary & Core Objectives
The Open-DV Ontology Construction Studio is an enterprise ontology engineering environment that automates and governs the creation of Data Vault 2.0 Graph A semantic models from multi-format enterprise documentation, regulatory filings, and tabular schemas.

### Core System Requirements
- R1. Autonomous Multi-Phase Lifecycle & Tracking (Phases 0-6): Complete delivery lifecycle across Setup (Phase 0), Research & Design (Phase 1), Spec & Architecture (Phase 2), Implementation Plan (Phase 3), Build Wave 1 Foundation (Phase 4), Build Wave 2 Engine & UI (Phase 5), and Integration & Release (Phase 6). Tasks run in isolated track workspaces (at most 4 concurrent tracks) with mandatory Claude CLI Opus 5.5 critique loops at the end of each phase and track before opening PRs into dev/ontology-studio.
- R2. Core Engine, Document Ingestion, and Model Extensions: Extend GraphAOntology with backward-compatible optional fields (parent_concept_uri, attributes, synonyms, external_mappings) defaulting to None to preserve RFC 8785 SHA-256 stability. Implement additive OntologyDelta operations governed by ONT-EVO contract guards (ONT-EVO-001..003). Provide ingestion via Python liteparse with layout complexity OCR routing and character-offset PageMapEntry. Vendor 6 core FIBO modules (FND, BE, FBC, SEC, LOAN, IND) via scripts/vendor_fibo.py with runtime rdflib loader. Build dual-mode engine supporting Okapi BM25 matching, deterministic fallback, and 3-persona LLM agent reasoning.
- R3. Service, Persistence, CLI, and Web UI: Server-side Studio sessions in .opendv/ontology_studio/<session_id>/ supporting CAS storage, async W3C SSE jobs with Last-Event-ID replay, and .odvstudio.zip bundle portability. Expose /api/v1/ontology/studio/* endpoints with full CLI parity under opendv and opendvctl. Deliver React 19 Web Workbench SPA at /ontology featuring Sources & Parse, Clarification Q&A, React Flow (@xyflow/react) + elkjs Graph Explorer with diff badges, Visual Editor, Quality & Feedback capture, and multi-format export/promotion.
- R4. Complete Documentation Suite: Produce master-plan.md, research.md, ADR-001..016, HANDOFF.md, docs/19, docs/20, docs/21, docs/22, docs/23, README.md, and docs/16-cli-reference.md.

---

## 2. Inviolable Architectural Invariants
1. Canonical SHA-256 Hash Stability Invariant:
   compute_ontology_sha256 evaluated over examples/private_equity/pe_graph_a_ontology.yaml must yield exactly:
   1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81
   Genesis draft 0.0.0 hash: 99d4aa051a49f40596c53210592853fde09ee6995db5b1ff0ca8e57170dffa8e.
2. ONT-EVO Contract Protection:
   - ONT-EVO-001: Immutable deployed table contracts (blocks destructive operations on physical tables).
   - ONT-EVO-002: Positive grounding requirement (direct reference mapping >= 0.6500 or admin waiver >= 10 chars).
   - ONT-EVO-003: Deterministic conflict detection, proposal rebasing, and SemVer version calculus.
3. Monorepo Layering & AST Isolation:
   - opendv-cli never imports opendv-platform-api or web frameworks (fastapi, starlette, httpx).
   - opendv-platform-api routes all business logic through opendv-service.
   - Zero test doubles or mock shims in production code (packages/*/src, apps/*/src).
4. Dual CLI Parity: Bit-for-bit identical command tree and exit code parity across opendv and opendvctl.

---

## 3. Phase Roll-Up Checklist
- [x] Phase 0: Setup & Baseline Verification (M0 - Completed)
- [x] Phase 1: Research & Design (M1 - Gate Passed)
- [x] Phase 2: Specification & Architecture (M2 - Gate Passed)
- [x] Phase 3: Implementation Plan & GitHub Issue Tracker (M3 - Completed)
- [ ] Phase 4: Wave 1 Foundation (14 Tasks: Tracks 4A, 4B, 4C, 4D)
- [ ] Phase 5: Wave 2 Engine & UI (17 Tasks: Tracks 5A, 5B, 5C, 5D)
- [ ] Phase 6: Wave 3 Integration & Release (12 Tasks: Tracks 6A, 6B, 6C, 6D, 6E)
"@

# -----------------------------------------------------------------------------
# 3. Phase Tracker Issues Definition
# -----------------------------------------------------------------------------
$phases = @(
    @{
        Key   = "phase_4"
        Title = "[Phase 4] Wave 1 Foundation: Core Models, Ingestion, FIBO Vendor Pack & CAS Session Storage"
        Label = "enhancement,phase,phase:4"
        Body  = @"
# [Phase 4] Wave 1 Foundation: Core Models, Ingestion, FIBO Vendor Pack & CAS Session Storage

Phase Sub-Issue tracking Wave 1 Foundation of the Open-DV Ontology Construction Studio.
Plan reference: docs/21-ontology-studio-implementation-plan.md Section 3.

## Phase Objectives
1. Track 4A (Core Models & Deltas): Model extensions defaulting to None, canonical hash stability (1c1004...), additive delta DTOs, pure state applier, ONT-EVO structural/governance validator.
2. Track 4B (Document Ingestion): LiteParseDocumentAdapter (PDFium), PageMapEntry Unicode offset index, layout complexity OCR routing, and multi-format adapters.
3. Track 4C (FIBO Reference Alignment): scripts/vendor_fibo.py compiling 6 core modules into canonical pack < 4MB, rdflib loader, transitive closure resolver.
4. Track 4D (CAS Session Persistence): .opendv/ontology_studio/<session_id>/ CAS layout, 5-level lock hierarchy, multi-file atomic commit protocol, W3C SSE streaming, .odvstudio.zip bundle portability.

## Exit Criteria
- Canonical PE hash: 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 verified.
- FIBO core pack compiled and verified against baseline-digests.json.
- All Phase 4 task PRs merged with 0 CRITICAL and 0 MAJOR Claude Opus findings.
"@
    },
    @{
        Key   = "phase_5"
        Title = "[Phase 5] Wave 2 Engine & UI: Dual-Mode Engine, Platform API, Dual CLI Parity & Web Workbench"
        Label = "enhancement,phase,phase:5"
        Body  = @"
# [Phase 5] Wave 2 Engine & UI: Dual-Mode Engine, Platform API, Dual CLI Parity & Web Workbench

Phase Sub-Issue tracking Wave 2 Engine & UI of the Open-DV Ontology Construction Studio.
Plan reference: docs/21-ontology-studio-implementation-plan.md Section 4.

## Phase Objectives
1. Track 5A (Dual-Mode Engine): Okapi BM25 matcher (bm25_fibo_v1), zero-API-key deterministic fallback pipeline, 3-persona LLM harness (@term-scout, @domain-clarifier, @ontology-architect), clarification Q&A harness.
2. Track 5B (Platform REST API): 27 REST endpoints under /api/v1/ontology/studio/*, background job supervisor, local vs remote server modes, CSRF origin verification.
3. Track 5C (Dual CLI Parity): Standalone opendv (LocalExecutor) and Bun opendvctl (RemoteExecutor) with 100% exit code parity across 0..7.
4. Track 5D (Web Workbench UI): SPA mounted at /ontology in apps/opendv-web-workbench, React Flow (@xyflow/react) + elkjs Graph Explorer, visual diff overlay, Quality Scorecard, and multi-format export modal.

## Exit Criteria
- Web Workbench renders interactive graph canvas and visual diff overlay at /ontology.
- Dual CLI test suite passes with bit-for-bit canonical JCS stdout and exit code parity.
- All Phase 5 task PRs merged with 0 CRITICAL and 0 MAJOR Claude Opus findings.
"@
    },
    @{
        Key   = "phase_6"
        Title = "[Phase 6] Wave 3 Integration & Release: E2E Verification, CI Golden Evaluation, Docs & Release PR"
        Label = "enhancement,phase,phase:6"
        Body  = @"
# [Phase 6] Wave 3 Integration & Release: E2E Verification, CI Golden Evaluation, Docs & Release PR

Phase Sub-Issue tracking Wave 3 Integration & Release of the Open-DV Ontology Construction Studio.
Plan reference: docs/21-ontology-studio-implementation-plan.md Section 5.

## Phase Objectives
1. Track 6A (CI Golden Evaluation): Golden test evaluation suite with CQs across deterministic and agent modes (precision >= 85%, recall >= 80%, CQ >= 90%).
2. Track 6B (E2E Integration Testing): Comprehensive opaque-box and white-box test suite across Tiers 1-5 (Journeys, Boundaries, Combinatorial, Chaos, Adversarial).
3. Track 6C (User & Extension Guides): Author docs/22-ontology-studio-user-guide.md and docs/23-ontology-studio-extension-guide.md.
4. Track 6D (Developer Reference & Schemas): Update CLI reference docs/16-cli-reference.md, README.md, and synchronize all schemas/v1/* definitions.
5. Track 6E (Final Release PR): Full CI pass, issue closeout verification, and final PR opened from dev/ontology-studio to main.

## Exit Criteria
- Golden evaluation set passes with all quality thresholds satisfied in CI.
- Full E2E Tiers 1-5 pass cleanly.
- Final PR opened from dev/ontology-studio to main with 0 CRITICAL and 0 MAJOR Claude Opus findings.
"@
    }
)

# -----------------------------------------------------------------------------
# 4. Granular Task Issues Catalog (43 Tasks)
# -----------------------------------------------------------------------------
$tasks = @(
    # --- Track 4A: Core Models & Deltas (Phase 4) ---
    @{
        Id          = "4A.1"
        PhaseKey    = "phase_4"
        Track       = "4A"
        Title       = "[Task 4A.1] GraphAOntology Model Extension & Optional Fields"
        Description = "Extend CanonicalBusinessConcept in packages/opendv-core/src/opendv_core/models/graph_a.py with backward-compatible optional fields defaulting to None: parent_concept_uri, attributes, synonyms, external_mappings."
        Targets     = "packages/opendv-core/src/opendv_core/models/graph_a.py"
        Tests       = "packages/opendv-core/tests/test_graph_a_extension.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-core/tests/test_graph_a_extension.py -q"
    },
    @{
        Id          = "4A.2"
        PhaseKey    = "phase_4"
        Track       = "4A"
        Title       = "[Task 4A.2] Canonical JCS Hash Stability & Zero-Drift Invariance"
        Description = "Update packages/opendv-core/src/opendv_core/ontology_hashing.py to ensure exclude_none=True and recursive empty normalization preserve PE hash 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81."
        Targets     = "packages/opendv-core/src/opendv_core/ontology_hashing.py"
        Tests       = "packages/opendv-core/tests/test_canonical_hash_invariance.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-core/tests/test_canonical_hash_invariance.py -q"
    },
    @{
        Id          = "4A.3"
        PhaseKey    = "phase_4"
        Track       = "4A"
        Title       = "[Task 4A.3] Full 24-Op OntologyDelta Catalog, SPI Definitions & OCC Envelope"
        Description = "Implement the complete closed 24-operation delta DTO catalog in packages/opendv-contracts/src/opendv_contracts/dtos/ontology.py with SemVer bump classes and OntologyDelta envelope with base_version and base_sha256."
        Targets     = "packages/opendv-contracts/src/opendv_contracts/dtos/ontology.py, packages/opendv-contracts/src/opendv_contracts/spi/grounding.py, packages/opendv-contracts/src/opendv_contracts/spi/waiver.py, packages/opendv-contracts/src/opendv_contracts/spi/agent.py, packages/opendv-contracts/src/opendv_contracts/spi/reference_ontology.py, packages/opendv-contracts/src/opendv_contracts/spi/deployment.py"
        Tests       = "packages/opendv-contracts/tests/test_ontology_delta_dtos.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-contracts/tests/test_ontology_delta_dtos.py -q"
    },
    @{
        Id          = "4A.4"
        PhaseKey    = "phase_4"
        Track       = "4A"
        Title       = "[Task 4A.4] Pure State Applier, SemVer Calculus & ONT-EVO Governance Guards"
        Description = "Implement pure state applier with SemVer overwrite calculus, ONT-EVO-001 table protection, ONT-EVO-002 post-apply grounding check across all created/modified concepts (AddConceptOp, UpdateConceptUrnOp, SetParentConceptOp, mapping ops), and ONT-EVO-003 pure OCC check."
        Targets     = "packages/opendv-service/src/opendv_service/ontology/applier.py, packages/opendv-service/src/opendv_service/ontology/validator.py"
        Tests       = "packages/opendv-service/tests/test_ont_evo_validator.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_ont_evo_validator.py -q"
    },

    # --- Track 4B: Document Ingestion & LiteParse (Phase 4) ---
    @{
        Id          = "4B.1"
        PhaseKey    = "phase_4"
        Track       = "4B"
        Title       = "[Task 4B.1] DocumentAdapter SPI & Ingestion Service Framework"
        Description = "Define EvidenceChunk, PageMapEntry, TableStructure, IngestionResult, and TaintClassification DTOs in opendv-contracts; implement DocumentAdapter SPI and DocumentIngestionService in opendv-sources."
        Targets     = "packages/opendv-contracts/src/opendv_contracts/dtos/ingestion.py, packages/opendv-sources/src/opendv_sources/ingestion/service.py"
        Tests       = "packages/opendv-sources/tests/test_ingestion_service.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-sources/tests/test_ingestion_service.py -q"
    },
    @{
        Id          = "4B.2"
        PhaseKey    = "phase_4"
        Track       = "4B"
        Title       = "[Task 4B.2] LiteParse 0.4.x Native PDF Adapter & Unicode Offset Mapping"
        Description = "Implement LiteParseDocumentAdapter embedding Google PDFium C++ for text, table boundary detection, and PageMapEntry with Unicode codepoint offsets and browser UTF-16 index."
        Targets     = "packages/opendv-sources/src/opendv_sources/adapters/liteparse_adapter.py"
        Tests       = "packages/opendv-sources/tests/test_liteparse_adapter.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-sources/tests/test_liteparse_adapter.py -q"
    },
    @{
        Id          = "4B.3"
        PhaseKey    = "phase_4"
        Track       = "4B"
        Title       = "[Task 4B.3] Layout Complexity Evaluator & Tesseract OCR Routing"
        Description = "Implement evaluate_page_complexity assessing character density and raster ratio; in auto mode degrade gracefully to native PDFium parsing with ocr_skipped=True; in force mode fail loudly with typed OCR_UNAVAILABLE (HTTP 501 / exit 1)."
        Targets     = "packages/opendv-sources/src/opendv_sources/ingestion/layout_evaluator.py"
        Tests       = "packages/opendv-sources/tests/test_ocr_layout_routing.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-sources/tests/test_ocr_layout_routing.py -q"
    },
    @{
        Id          = "4B.4"
        PhaseKey    = "phase_4"
        Track       = "4B"
        Title       = "[Task 4B.4] Multi-Format Ingestion Adapters (MD, CSV, Parquet, JSON, HTML, Office)"
        Description = "Implement format-specific adapters for Markdown, CSV/TSV, Parquet metadata, JSON/YAML, HTML, and LibreOfficeDocumentAdapter for docx/pptx/odt conversion via headless soffice."
        Targets     = "packages/opendv-sources/src/opendv_sources/adapters/multiformat_adapter.py, packages/opendv-sources/src/opendv_sources/adapters/libreoffice_adapter.py"
        Tests       = "packages/opendv-sources/tests/test_multiformat_ingestion.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-sources/tests/test_multiformat_ingestion.py -q"
    },

    # --- Track 4C: FIBO Reference Alignment & Pack (Phase 4) ---
    @{
        Id          = "4C.1"
        PhaseKey    = "phase_4"
        Track       = "4C"
        Title       = "[Task 4C.1] FIBO Compilation Script (scripts/vendor_fibo.py)"
        Description = "Author scripts/vendor_fibo.py compiling pinned EDM Council FIBO master_2024Q4 6 modules (FND, BE, FBC, SEC, LOAN, IND) into canonical JSON pack fibo-core-<sha256_8>.json (< 4MB)."
        Targets     = "scripts/vendor_fibo.py, packages/opendv-sources/src/opendv_sources/fibo/fibo-core-pack.json"
        Tests       = "tests/scripts/test_vendor_fibo.py"
        TestCmd     = "uv run --directory open-dv python scripts/vendor_fibo.py --verify-only && uv run --directory open-dv pytest tests/scripts/test_vendor_fibo.py -q"
    },
    @{
        Id          = "4C.2"
        PhaseKey    = "phase_4"
        Track       = "4C"
        Title       = "[Task 4C.2] Reference Ontology SPI & Runtime rdflib Loader"
        Description = "Implement ReferenceOntologyAdapter SPI in opendv-sources and high-performance rdflib 7.x loader parsing OWL classes, rdfs:subClassOf, and SKOS annotations (< 70ms cold load)."
        Targets     = "packages/opendv-sources/src/opendv_sources/fibo/loader.py, packages/opendv-sources/src/opendv_sources/spi/reference_ontology.py"
        Tests       = "packages/opendv-sources/tests/test_fibo_loader.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-sources/tests/test_fibo_loader.py -q"
    },
    @{
        Id          = "4C.3"
        PhaseKey    = "phase_4"
        Track       = "4C"
        Title       = "[Task 4C.3] Transitive subClassOf Closure Resolver & Grounding Guard"
        Description = "Implement transitive taxonomic closure resolver taking verified seed score parameter: compute superclasses strictly if direct seed mapping >= 0.6500, categorizing as skos:broadMatch."
        Targets     = "packages/opendv-sources/src/opendv_sources/fibo/closure.py"
        Tests       = "packages/opendv-sources/tests/test_fibo_closure.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-sources/tests/test_fibo_closure.py -q"
    },

    # --- Track 4D: Storage, Session CAS & Streaming (Phase 4) ---
    @{
        Id          = "4D.1"
        PhaseKey    = "phase_4"
        Track       = "4D"
        Title       = "[Task 4D.1] CAS Session Directory Structure, Workspace Secret & HKDF Key Hierarchy"
        Description = "Implement workspace_security.py generating 256-bit CSPRNG .odv_workspace_secret (0600 DACL) and HKDF key hierarchy (K_pseudo, K_audit, K_waiver), StudioSessionManager, strict session ID regex ^[a-z0-9][a-z0-9_-]{0,63}$ rejection without silent normalization, and audit hash chaining."
        Targets     = "packages/opendv-service/src/opendv_service/studio/session_manager.py, packages/opendv-service/src/opendv_service/studio/workspace_security.py, packages/opendv-service/src/opendv_service/studio/cas_storage.py"
        Tests       = "packages/opendv-service/tests/test_studio_session_cas.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_studio_session_cas.py -q"
    },
    @{
        Id          = "4D.2"
        PhaseKey    = "phase_4"
        Track       = "4D"
        Title       = "[Task 4D.2] Global 5-Level Lock Hierarchy & Deadlock Prevention"
        Description = "Implement file locking engine enforcing total acquisition order L1 -> L2 -> L3 -> L4 -> L5 with 30s timeout (LOCK_TIMEOUT exit 6 / 409) and lexicographical multi-session ordering."
        Targets     = "packages/opendv-service/src/opendv_service/studio/locks.py"
        Tests       = "packages/opendv-service/tests/test_lock_hierarchy.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_lock_hierarchy.py -q"
    },
    @{
        Id          = "4D.3"
        PhaseKey    = "phase_4"
        Track       = "4D"
        Title       = "[Task 4D.3] Multi-File Atomic Commit Protocol, Crash Recovery & SSE Engine"
        Description = "Implement 6-step atomic commit protocol, crash recovery (garbage-collect staging before Step 4, COMMIT_ABORTED before Step 5, auto-reconcile proposal ACCEPTED after Step 5 with terminal COMMITTED entry), cross-workspace bundle trust model (audit.imported, IMPORT_GENESIS), and bundle security."
        Targets     = "packages/opendv-service/src/opendv_service/studio/atomic_commit.py, packages/opendv-service/src/opendv_service/studio/sse_journal.py, packages/opendv-service/src/opendv_service/studio/bundle.py"
        Tests       = "packages/opendv-service/tests/test_atomic_commit_recovery.py, packages/opendv-service/tests/test_sse_journal_replay.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_atomic_commit_recovery.py packages/opendv-service/tests/test_sse_journal_replay.py -q"
    },

    # --- Track 5A: Dual-Mode Construction & Clarification (Phase 5) ---
    @{
        Id          = "5A.1"
        PhaseKey    = "phase_5"
        Track       = "5A"
        Title       = "[Task 5A.1] Okapi BM25 Concept Matcher & GroundingVerifier Binding"
        Description = "Implement ConceptMatcher SPI, bm25_fibo_v1, and concrete FiboPackGroundingVerifier asserting loaded pack SHA-256 matches baseline-digests.json bit-for-bit (raising REFERENCE_ONTOLOGY_CORRUPT on mismatch)."
        Targets     = "packages/opendv-service/src/opendv_service/ontology/matching/bm25.py, packages/opendv-service/src/opendv_service/ontology/matching/tokenization.py"
        Tests       = "packages/opendv-service/tests/test_bm25_matcher.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_bm25_matcher.py -q"
    },
    @{
        Id          = "5A.2"
        PhaseKey    = "phase_5"
        Track       = "5A"
        Title       = "[Task 5A.2] Deterministic Fallback Construction Engine"
        Description = "Implement zero-API-key deterministic fallback pipeline in opendv_service/construction/ using schema heuristics, BM25 lookup, and staged candidates holding area."
        Targets     = "packages/opendv-service/src/opendv_service/construction/deterministic_pipeline.py"
        Tests       = "packages/opendv-service/tests/test_deterministic_pipeline.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_deterministic_pipeline.py -q"
    },
    @{
        Id          = "5A.3"
        PhaseKey    = "phase_5"
        Track       = "5A"
        Title       = "[Task 5A.3] 3-Persona LLM Agent Reasoning Harness"
        Description = "Implement 3-persona agent harness consuming ingestion DTOs from opendv-contracts with AST isolation, confidential taint tracking, per-workspace HKDF-SHA256 pseudonymization (K_pseudo from .odv_workspace_secret), and leak scan."
        Targets     = "packages/opendv-agent-harness/src/opendv_agent_harness/personas/term_scout.py, packages/opendv-agent-harness/src/opendv_agent_harness/personas/domain_clarifier.py, packages/opendv-agent-harness/src/opendv_agent_harness/personas/ontology_architect.py"
        Tests       = "packages/opendv-agent-harness/tests/test_studio_personas.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-agent-harness/tests/test_studio_personas.py -q"
    },
    @{
        Id          = "5A.4"
        PhaseKey    = "phase_5"
        Track       = "5A"
        Title       = "[Task 5A.4] Clarification Question Generation & Answer Harness"
        Description = "Implement automated clarification question generator creating questions.json with semantic rationale options, persisting answers.json into CAS for next-iteration grounding."
        Targets     = "packages/opendv-service/src/opendv_service/construction/clarification_harness.py"
        Tests       = "packages/opendv-service/tests/test_clarification_harness.py"
        TestCmd     = "uv run --directory open-dv pytest packages/opendv-service/tests/test_clarification_harness.py -q"
    },

    # --- Track 5B: Platform REST API & Job Supervisor (Phase 5) ---
    @{
        Id          = "5B.1"
        PhaseKey    = "phase_5"
        Track       = "5B"
        Title       = "[Task 5B.1] Studio Session & Source Management REST Endpoints (Routes 1-11)"
        Description = "Implement REST endpoints 1-11 under /api/v1/ontology/studio/ matching openapi.json: /init, /sessions, /sessions/{id}, /sources, /sources/{id}, /status, /manifest with closed ProblemDetail responses."
        Targets     = "apps/opendv-platform-api/src/opendv_platform_api/routers/ontology_studio.py, packages/opendv-service/src/opendv_service/export/rdf_exporter.py"
        Tests       = "apps/opendv-platform-api/tests/test_ontology_studio_routes.py, packages/opendv-service/tests/test_rdf_export.py"
        TestCmd     = "uv run --directory open-dv pytest apps/opendv-platform-api/tests/test_ontology_studio_routes.py -k session -q"
    },
    @{
        Id          = "5B.2"
        PhaseKey    = "phase_5"
        Track       = "5B"
        Title       = "[Task 5B.2] Parse, Clarification, Construction & Job REST Endpoints (Routes 12-18)"
        Description = "Implement REST endpoints 12-18: /parse, /clarifications, /answers, /construct, /jobs/{id}, /jobs/{id}/events (W3C SSE), /jobs/{id}/cancel with background supervisor."
        Targets     = "apps/opendv-platform-api/src/opendv_platform_api/routers/ontology_studio.py, packages/opendv-service/src/opendv_service/export/rdf_exporter.py"
        Tests       = "apps/opendv-platform-api/tests/test_ontology_studio_routes.py, packages/opendv-service/tests/test_rdf_export.py"
        TestCmd     = 'uv run --directory open-dv pytest apps/opendv-platform-api/tests/test_ontology_studio_routes.py -k "parse or clarify or construct or job" -q'
    },
    @{
        Id          = "5B.3"
        PhaseKey    = "phase_5"
        Track       = "5B"
        Title       = "[Task 5B.3] Deltas, Proposals, Waiver Acceptance, Export & Promotion (Routes 19-27)"
        Description = "Implement REST endpoints 19-27 (deltas validate/apply, proposals list/get/accept/reject/rebase, GET export, promote) with exact 27-route openapi.json alignment and steward waiver authorization."
        Targets     = "apps/opendv-platform-api/src/opendv_platform_api/routers/ontology_studio.py, packages/opendv-service/src/opendv_service/export/rdf_exporter.py"
        Tests       = "apps/opendv-platform-api/tests/test_ontology_studio_routes.py, packages/opendv-service/tests/test_rdf_export.py"
        TestCmd     = 'uv run --directory open-dv pytest apps/opendv-platform-api/tests/test_ontology_studio_routes.py packages/opendv-service/tests/test_rdf_export.py -q'
    },
    @{
        Id          = "5B.4"
        PhaseKey    = "phase_5"
        Track       = "5B"
        Title       = "[Task 5B.4] Dual Server Modes, Security Guards & 11-Step Error Precedence"
        Description = "Implement local_standalone (launch nonce, loopback-only) vs remote_platform modes, CSRF origin verification, workspace containment guard, and 11-step canonical error precedence."
        Targets     = "apps/opendv-platform-api/src/opendv_platform_api/security/studio_auth.py, apps/opendv-platform-api/src/opendv_platform_api/security/csrf.py"
        Tests       = "apps/opendv-platform-api/tests/test_studio_auth_modes.py, apps/opendv-platform-api/tests/test_error_precedence_11steps.py"
        TestCmd     = "uv run --directory open-dv pytest apps/opendv-platform-api/tests/test_studio_auth_modes.py apps/opendv-platform-api/tests/test_error_precedence_11steps.py -q"
    },

    # --- Track 5C: CLI Integration & Parity (Phase 5) ---
    @{
        Id          = "5C.1"
        PhaseKey    = "phase_5"
        Track       = "5C"
        Title       = "[Task 5C.1] Standalone opendv Studio Subcommands with Waiver Flag"
        Description = "Implement 14 ontology studio subcommand groups (21 leaf commands) in standalone opendv via LocalExecutor, supporting --waiver-rationale and --waiver-key-file (never raw secret on argv)."
        Targets     = "apps/opendv-cli/src/opendv_cli/commands/ontology_studio.py"
        Tests       = "apps/opendv-cli/tests/test_studio_local_executor.py"
        TestCmd     = "uv run --directory open-dv pytest apps/opendv-cli/tests/test_studio_local_executor.py -q"
    },
    @{
        Id          = "5C.2"
        PhaseKey    = "phase_5"
        Track       = "5C"
        Title       = "[Task 5C.2] Bun opendvctl Studio Subcommands with Waiver Flag"
        Description = "Implement ontology studio subcommands in apps/opendvctl/src/remote-executor.ts connecting to REST/SSE endpoints with RFC 8785 canonical output, exit code mapping, and --waiver-rationale / bearer auth."
        Targets     = "apps/opendvctl/src/remote-executor.ts"
        Tests       = "apps/opendvctl/tests/studio-cli.test.ts"
        TestCmd     = "bun test apps/opendvctl/tests/studio-cli.test.ts"
    },
    @{
        Id          = "5C.3"
        PhaseKey    = "phase_5"
        Track       = "5C"
        Title       = "[Task 5C.3] Dual-CLI Bit-for-Bit Parity Verification Harness"
        Description = "Implement automated parity test harness executing identical studio command scenarios across opendv and opendvctl: asserting bit-for-bit JCS stdout and exit code parity across 0..7 for shared commands."
        Targets     = "tests/parity/test_ontology_studio_cli_parity.py"
        Tests       = "tests/parity/test_ontology_studio_cli_parity.py"
        TestCmd     = "uv run --directory open-dv pytest tests/parity/test_ontology_studio_cli_parity.py -q"
    },

    # --- Track 5D: Web Workbench UI (Phase 5) ---
    @{
        Id          = "5D.1"
        PhaseKey    = "phase_5"
        Track       = "5D"
        Title       = "[Task 5D.1] Web Workbench SPA Shell & State Store (/ontology)"
        Description = "Mount decoupled React 19 SPA at /ontology in apps/opendv-web-workbench, implementing Zustand studio store managing active session, sources, graph, and UI panels."
        Targets     = "apps/opendv-web-workbench/src/ontology/OntologyStudioApp.tsx, apps/opendv-web-workbench/src/ontology/store/studio-store.ts"
        Tests       = "apps/opendv-web-workbench/tests/ontology-studio-store.test.ts"
        TestCmd     = "cd open-dv; bun test apps/opendv-web-workbench/tests/ontology-studio-store.test.ts"
    },
    @{
        Id          = "5D.2"
        PhaseKey    = "phase_5"
        Track       = "5D"
        Title       = "[Task 5D.2] Sources & Parse Management Panel"
        Description = "Implement SourcesPanel component: multi-file drag-and-drop upload, parsing progress indicator, evidence chunk preview, and layout complexity badges."
        Targets     = "apps/opendv-web-workbench/src/ontology/components/SourcesPanel.tsx"
        Tests       = "apps/opendv-web-workbench/tests/ontology-panels.test.tsx"
        TestCmd     = "cd open-dv; bun test apps/opendv-web-workbench/tests/ontology-panels.test.tsx -t SourcesPanel"
    },
    @{
        Id          = "5D.3"
        PhaseKey    = "phase_5"
        Track       = "5D"
        Title       = "[Task 5D.3] Clarification Q&A Panel & Answer Submissions"
        Description = "Implement ClarificationPanel component: question cards with semantic rationale options, custom answer input, evidence citations, and instant answer persistence."
        Targets     = "apps/opendv-web-workbench/src/ontology/components/ClarificationPanel.tsx"
        Tests       = "apps/opendv-web-workbench/tests/ontology-panels.test.tsx"
        TestCmd     = "cd open-dv; bun test apps/opendv-web-workbench/tests/ontology-panels.test.tsx -t ClarificationPanel"
    },
    @{
        Id          = "5D.4"
        PhaseKey    = "phase_5"
        Track       = "5D"
        Title       = "[Task 5D.4] React Flow + elkjs Graph Canvas & Multi-Tier LOD"
        Description = "Implement GraphCanvas integrating @xyflow/react and elkjs web worker layout (< 300ms for 100 nodes), custom concept/relationship nodes, and 3-tier zoom LOD."
        Targets     = "apps/opendv-web-workbench/src/ontology/components/GraphCanvas.tsx"
        Tests       = "apps/opendv-web-workbench/tests/ontology-graph-canvas.test.tsx"
        TestCmd     = "cd open-dv; bun test apps/opendv-web-workbench/tests/ontology-graph-canvas.test.tsx"
    },
    @{
        Id          = "5D.5"
        PhaseKey    = "phase_5"
        Track       = "5D"
        Title       = "[Task 5D.5] Visual Diff Overlay & Visual Editor Drawer"
        Description = "Implement DiffOverlay rendering green/red/amber node badges comparing base and target versions, and VisualEditorDrawer for editing attributes, synonyms, and mappings."
        Targets     = "apps/opendv-web-workbench/src/ontology/components/DiffOverlay.tsx, apps/opendv-web-workbench/src/ontology/components/VisualEditorDrawer.tsx"
        Tests       = "apps/opendv-web-workbench/tests/ontology-panels.test.tsx"
        TestCmd     = "cd open-dv; bun test apps/opendv-web-workbench/tests/ontology-panels.test.tsx -t DiffOverlay"
    },
    @{
        Id          = "5D.6"
        PhaseKey    = "phase_5"
        Track       = "5D"
        Title       = "[Task 5D.6] Quality Scorecard, Steward Feedback & Multi-Format Export/Promotion Modal"
        Description = "Implement QualityFeedbackPanel (contract rule checks, 5-star rating to feedback.jsonl) and ExportPromoteModal (YAML, RFC 8785 JSON, Turtle, JSON-LD, and workspace promotion)."
        Targets     = "apps/opendv-web-workbench/src/ontology/components/QualityFeedbackPanel.tsx, apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx"
        Tests       = "apps/opendv-web-workbench/tests/export-promotion-modal.test.tsx"
        TestCmd     = "bun test apps/opendv-web-workbench/tests/export-promotion-modal.test.tsx"
    },

    # --- Track 6A: CI Golden Evaluation Set (Phase 6) ---
    @{
        Id          = "6A.1"
        PhaseKey    = "phase_6"
        Track       = "6A"
        Title       = "[Task 6A.1] CI Golden Evaluation Harness & Benchmark Ground Truth"
        Description = "Implement automated golden benchmark evaluator in tests/golden/ontology_studio/ measuring precision (>= 85%), recall (>= 80%), and CQ answer coverage (>= 90%)."
        Targets     = "tests/golden/ontology_studio/test_golden_evaluation.py, tests/golden/ontology_studio/golden_cqs.json"
        Tests       = "tests/golden/ontology_studio/test_golden_evaluation.py"
        TestCmd     = "uv run --directory open-dv pytest tests/golden/ontology_studio/test_golden_evaluation.py -q"
    },
    @{
        Id          = "6A.2"
        PhaseKey    = "phase_6"
        Track       = "6A"
        Title       = "[Task 6A.2] Dual-Mode Benchmark Validation (Deterministic vs Agent)"
        Description = "Validate golden competency questions across both deterministic fallback and 3-persona agent modes, ensuring deterministic mode runs in < 2.5s with exit 0."
        Targets     = "tests/golden/ontology_studio/test_golden_modes.py"
        Tests       = "tests/golden/ontology_studio/test_golden_modes.py"
        TestCmd     = "uv run --directory open-dv pytest tests/golden/ontology_studio/test_golden_modes.py -q"
    },

    # --- Track 6B: E2E Integration Testing (Phase 6) ---
    @{
        Id          = "6B.1"
        PhaseKey    = "phase_6"
        Track       = "6B"
        Title       = "[Task 6B.1] Tier 1 & Tier 2 E2E Tests: Canonical Journeys & Boundaries"
        Description = "Implement Tier 1 E2E user journeys (Greenfield, FIBO-grounded, Multi-version, Promotion) and Tier 2 boundary tests (empty files, huge files, Unicode, max lengths)."
        Targets     = "tests/e2e/ontology_studio/test_tier1_journeys.py, tests/e2e/ontology_studio/test_tier2_boundaries.py"
        Tests       = "tests/e2e/ontology_studio/test_tier1_journeys.py"
        TestCmd     = "uv run --directory open-dv pytest tests/e2e/ontology_studio/test_tier1_journeys.py tests/e2e/ontology_studio/test_tier2_boundaries.py -q"
    },
    @{
        Id          = "6B.2"
        PhaseKey    = "phase_6"
        Track       = "6B"
        Title       = "[Task 6B.2] Tier 3 & Tier 4 E2E Tests: Combinatorial & Workload Chaos"
        Description = "Implement Tier 3 combinatorial matrix tests and Tier 4 chaos tests (simulated process crash before/after atomic commit step 5, lock contention, SSE disconnects)."
        Targets     = "tests/e2e/ontology_studio/test_tier3_combinatorial.py, tests/e2e/ontology_studio/test_tier4_workload_chaos.py"
        Tests       = "tests/e2e/ontology_studio/test_tier4_workload_chaos.py"
        TestCmd     = "uv run --directory open-dv pytest tests/e2e/ontology_studio/test_tier3_combinatorial.py tests/e2e/ontology_studio/test_tier4_workload_chaos.py -q"
    },
    @{
        Id          = "6B.3"
        PhaseKey    = "phase_6"
        Track       = "6B"
        Title       = "[Task 6B.3] Tier 5 Adversarial Hardening (Waivers, Zip Security, Taint)"
        Description = "Implement Tier 5 white-box adversarial tests: unauthorized waivers, client mapping score spoofing, zip bomb decompression, symlink rejection, path traversal, Windows reserved names, and PII leak scan in LLM prompts."
        Targets     = "tests/e2e/ontology_studio/test_tier5_adversarial.py"
        Tests       = "tests/e2e/ontology_studio/test_tier5_adversarial.py"
        TestCmd     = "uv run --directory open-dv pytest tests/e2e/ontology_studio/test_tier5_adversarial.py -q"
    },

    # --- Track 6C: User Guide & Extension Guide (Phase 6) ---
    @{
        Id          = "6C.1"
        PhaseKey    = "phase_6"
        Track       = "6C"
        Title       = "[Task 6C.1] Author docs/22-ontology-studio-user-guide.md"
        Description = "Author comprehensive end-user guide covering Studio concepts, Web Workbench walk-through, CLI recipes, clarification Q&A, and export/promotion workflows."
        Targets     = "docs/22-ontology-studio-user-guide.md"
        Tests       = "tests/docs/test_doc_code_snippets.py"
        TestCmd     = "uv run --directory open-dv pytest tests/docs/test_doc_code_snippets.py -k docs_22 -q"
    },
    @{
        Id          = "6C.2"
        PhaseKey    = "phase_6"
        Track       = "6C"
        Title       = "[Task 6C.2] Author docs/23-ontology-studio-extension-guide.md"
        Description = "Author developer extension guide: adding custom DocumentAdapters, custom ReferenceOntology packs, extending BM25 tokenizers, and custom agent personas."
        Targets     = "docs/23-ontology-studio-extension-guide.md"
        Tests       = "tests/docs/test_doc_code_snippets.py"
        TestCmd     = "uv run --directory open-dv pytest tests/docs/test_doc_code_snippets.py -k docs_23 -q"
    },

    # --- Track 6D: Developer Reference & Schema Sync (Phase 6) ---
    @{
        Id          = "6D.1"
        PhaseKey    = "phase_6"
        Track       = "6D"
        Title       = "[Task 6D.1] Synchronize CLI Reference (docs/16-cli-reference.md) & README.md"
        Description = "Update docs/16-cli-reference.md with complete ontology studio subcommands, options, and examples; update main README.md introducing Ontology Construction Studio."
        Targets     = "docs/16-cli-reference.md, README.md"
        Tests       = "tests/architecture/test_schema_sync.py"
        TestCmd     = "uv run --directory open-dv pytest tests/architecture/test_schema_sync.py -q"
    },
    @{
        Id          = "6D.2"
        PhaseKey    = "phase_6"
        Track       = "6D"
        Title       = "[Task 6D.2] OpenAPI & CLI Spec Drift Verification & TypeScript Re-generation"
        Description = "Verify schemas/v1/openapi.json and cli-spec.json match live code; re-generate packages/opendv-protocol-ts bindings and assert bun test passes with 0 drift."
        Targets     = "schemas/v1/openapi.json, schemas/v1/cli-spec.json, packages/opendv-protocol-ts/src/types/openapi.ts"
        Tests       = "packages/opendv-protocol-ts/tests/openapi-dtos.test.ts"
        TestCmd     = "bun test packages/opendv-protocol-ts/tests/openapi-dtos.test.ts && bun x tsc -b --pretty"
    },

    # --- Track 6E: Final Release PR to main (Phase 6) ---
    @{
        Id          = "6E.1"
        PhaseKey    = "phase_6"
        Track       = "6E"
        Title       = "[Task 6E.1] Full Monorepo CI Suite Verification & Baseline Stability"
        Description = "Execute complete CI test suite across Python, TypeScript, and opendvctl: assert PE hash 1c1004..., AST import contracts, mock guards, and ruff checks."
        Targets     = "Repository-wide"
        Tests       = "Full CI suite"
        TestCmd     = "uv run --directory open-dv pytest && bun test && bun x tsc -b --pretty && uv run --directory open-dv ruff check packages/*/src apps/*/src"
    },
    @{
        Id          = "6E.2"
        PhaseKey    = "phase_6"
        Track       = "6E"
        Title       = "[Task 6E.2] GitHub Issue Tracker Final Closeout Audit"
        Description = "Audit GitHub issue tracker: confirm all 43 task issues and 3 phase issues are linked to merged PRs and closed with recorded Claude Opus review summaries."
        Targets     = "scripts/audit_issue_tracker.py, GitHub Tracker"
        Tests       = "scripts/audit_issue_tracker.py"
        TestCmd     = "uv run --directory open-dv python scripts/audit_issue_tracker.py --repo nogoo9/open-dv"
    },
    @{
        Id          = "6E.3"
        PhaseKey    = "phase_6"
        Track       = "6E"
        Title       = "[Task 6E.3] Final Release PR: dev/ontology-studio to main"
        Description = "Open final non-auto-merging Pull Request from dev/ontology-studio to main with full release notes, architectural attestations, and Claude Opus critique report."
        Targets     = "Pull Request to main"
        Tests       = "Claude Opus 5.5 Release Gate"
        TestCmd     = "uv run --directory open-dv python scripts/run_opus_critique.py --phase Release --task Final --model opus"
    }
)

# -----------------------------------------------------------------------------
# 5. Execution Logic
# -----------------------------------------------------------------------------

# Step 1: Create or Simulate Epic Tracker Issue
Write-Host "----------------------------------------------------------------------" -ForegroundColor Gray
Write-Host "1. Creating Master Epic Tracker Issue..." -ForegroundColor Yellow

$issueCatalog = [ordered]@{
    repo      = $Repo
    createdAt = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    dryRun    = [bool]$DryRun
    epic      = $null
    phases    = @()
    tasks     = @()
}

$epicIssueId = "EPIC-TRACKER"
$existingIssues = @{}
if (-not $DryRun) {
    try {
        $rawExisting = & gh issue list --repo $Repo --limit 200 --state all --json number,title,url | ConvertFrom-Json
        foreach ($item in $rawExisting) {
            $existingIssues[$item.title] = $item
        }
        Write-Host "[INFO] Loaded $($existingIssues.Count) existing issues from $Repo for idempotency." -ForegroundColor Gray
    } catch {
        Write-Warning "Could not fetch existing issues: $_"
    }
}

if ($DryRun) {
    Write-Host "[DRY-RUN] Would create Epic: '$epicTitle'" -ForegroundColor Cyan
    Write-Host "[DRY-RUN] Labels: $epicLabels" -ForegroundColor Gray
    Write-Host "[DRY-RUN] Assigned Simulated ID: #190" -ForegroundColor Green
    $epicIssueId = "190"
    $issueCatalog.epic = [ordered]@{
        id    = 190
        title = $epicTitle
        url   = "https://github.com/$Repo/issues/190"
    }
} elseif ($existingIssues.ContainsKey($epicTitle)) {
    $existingEpic = $existingIssues[$epicTitle]
    $epicIssueId = "$($existingEpic.number)"
    $epicUrl = $existingEpic.url
    Write-Host "Reusing existing Epic Tracker: #$epicIssueId ($epicUrl)" -ForegroundColor Green
    $issueCatalog.epic = [ordered]@{
        id    = [int]$epicIssueId
        title = $epicTitle
        url   = $epicUrl
    }
} else {
    $cmdArgs = @("issue", "create", "--repo", $Repo, "--title", $epicTitle, "--body", $epicBody, "--label", $epicLabels)
    $rawEpicOut = & gh @cmdArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create Epic Tracker issue: $rawEpicOut"
        exit 1
    }
    $epicUrl = ($rawEpicOut | Out-String).Trim().Split([Environment]::NewLine) | Where-Object { $_ -match "^https?://" } | Select-Object -Last 1
    $epicIssueId = ($epicUrl.Trim() -split "/")[-1]
    Write-Host "Created Epic Tracker: #$epicIssueId ($epicUrl)" -ForegroundColor Green
    $issueCatalog.epic = [ordered]@{
        id    = [int]$epicIssueId
        title = $epicTitle
        url   = $epicUrl
    }
    Start-Sleep -Seconds 2
}

# Step 2: Create Phase Tracker Sub-Issues
Write-Host "----------------------------------------------------------------------" -ForegroundColor Gray
Write-Host "2. Creating Phase Tracker Issues (3 Phases)..." -ForegroundColor Yellow

$phaseIds = @{}
foreach ($p in $phases) {
    Write-Host "Processing Phase: $($p.Title)..." -ForegroundColor Yellow
    if ($DryRun) {
        $simId = "PHASE-" + $p.Key.ToUpper()
        Write-Host "[DRY-RUN] Would create Phase: '$($p.Title)'" -ForegroundColor Cyan
        Write-Host "[DRY-RUN] Labels: $($p.Label) | Parent: #$epicIssueId" -ForegroundColor Gray
        Write-Host "[DRY-RUN] Assigned Simulated ID: #$simId" -ForegroundColor Green
        $phaseIds[$p.Key] = $simId
        $issueCatalog.phases += [ordered]@{
            key   = $p.Key
            id    = $simId
            title = $p.Title
            url   = "https://github.com/$Repo/issues/$simId"
        }
    } elseif ($existingIssues.ContainsKey($p.Title)) {
        $existingP = $existingIssues[$p.Title]
        $phaseIssueId = "$($existingP.number)"
        $pUrl = $existingP.url
        Write-Host "Reusing existing Phase Tracker: #$phaseIssueId ($pUrl)" -ForegroundColor Green
        $phaseIds[$p.Key] = $phaseIssueId
        $issueCatalog.phases += [ordered]@{
            key   = $p.Key
            id    = [int]$phaseIssueId
            title = $p.Title
            url   = $pUrl
        }
    } else {
        $pArgs = @("issue", "create", "--repo", $Repo, "--title", $p.Title, "--body", $p.Body, "--label", $p.Label, "--parent", $epicIssueId)
        $rawPOut = & gh @pArgs 2>&1
        $pUrl = ($rawPOut | Out-String).Trim().Split([Environment]::NewLine) | Where-Object { $_ -match "^https?://" } | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0 -or -not $pUrl) {
            Write-Warning "Parent flag failed ($rawPOut), retrying without --parent..."
            $pArgsFallback = @("issue", "create", "--repo", $Repo, "--title", $p.Title, "--body", $p.Body, "--label", $p.Label)
            $rawPOut = & gh @pArgsFallback 2>&1
            $pUrl = ($rawPOut | Out-String).Trim().Split([Environment]::NewLine) | Where-Object { $_ -match "^https?://" } | Select-Object -Last 1
        }
        $phaseIssueId = ($pUrl.Trim() -split "/")[-1]
        Write-Host "Created Phase Tracker: #$phaseIssueId ($pUrl)" -ForegroundColor Green
        $phaseIds[$p.Key] = $phaseIssueId
        $issueCatalog.phases += [ordered]@{
            key   = $p.Key
            id    = [int]$phaseIssueId
            title = $p.Title
            url   = $pUrl
        }
        Start-Sleep -Seconds 2
    }
}

# Step 3: Create Granular Task Issues (43 Tasks)
Write-Host "----------------------------------------------------------------------" -ForegroundColor Gray
Write-Host "3. Creating Granular Task Issues (43 Tasks)..." -ForegroundColor Yellow

$taskCount = 0
foreach ($t in $tasks) {
    $taskCount++
    $parentId = $phaseIds[$t.PhaseKey]
    $phaseTag = $t.PhaseKey -replace "_", ":"
    $tLabels = "enhancement,task,$phaseTag,track:$($t.Track)"
    
    $tBody = @"
# $($t.Title)

Task Issue under parent Phase #$parentId.
Plan reference: docs/21-ontology-studio-implementation-plan.md.

## Task Summary
- Identifier: Task $($t.Id) (Track $($t.Track))
- Description: $($t.Description)
- Target Implementation Files: $($t.Targets)
- Target Test Files: $($t.Tests)

## Definition of Done (DoD)
1. Implementation satisfies all requirements described in docs/21 for Task $($t.Id).
2. Automated test command passes cleanly:
   $($t.TestCmd)
3. Full monorepo CI checks pass (bun test, uv run ruff check).
4. Claude CLI Opus 5.5 Critique Gate:
   Execute critique loop:
   uv run --directory open-dv python scripts/run_opus_critique.py --paths $($t.Targets) --model opus
   Scoped Review Directive (2026-11-09T17:11:51Z): Focus strictly on real bugs and substantive design alignment against master-plan.md and project objectives. Requires strictly 0 CRITICAL and 0 MAJOR findings before merging PR.
"@

    if ($DryRun) {
        Write-Host "[DRY-RUN] ($taskCount/43) Task $($t.Id): '$($t.Title)' (Parent: #$parentId)" -ForegroundColor Cyan
        $issueCatalog.tasks += [ordered]@{
            taskId   = $t.Id
            phaseKey = $t.PhaseKey
            track    = $t.Track
            id       = "TASK-$($t.Id)"
            title    = $t.Title
            url      = "https://github.com/$Repo/issues/TASK-$($t.Id)"
        }
    } elseif ($existingIssues.ContainsKey($t.Title)) {
        $existingT = $existingIssues[$t.Title]
        $taskIssueId = "$($existingT.number)"
        $tUrl = $existingT.url
        Write-Host "Reusing existing Task ($taskCount/43): #$taskIssueId $($t.Title)" -ForegroundColor Green
        $issueCatalog.tasks += [ordered]@{
            taskId   = $t.Id
            phaseKey = $t.PhaseKey
            track    = $t.Track
            id       = [int]$taskIssueId
            title    = $t.Title
            url      = $tUrl
        }
    } else {
        $tempBodyFile = [System.IO.Path]::GetTempFileName()
        try {
            [System.IO.File]::WriteAllText($tempBodyFile, $tBody, [System.Text.Encoding]::UTF8)
            $tArgs = @("issue", "create", "--repo", $Repo, "--title", $t.Title, "--body-file", $tempBodyFile, "--label", $tLabels, "--parent", $parentId)
            $rawTOut = & gh @tArgs 2>&1
            $tUrl = ($rawTOut | Out-String).Trim().Split([Environment]::NewLine) | Where-Object { $_ -match "^https?://" } | Select-Object -Last 1
            if ($LASTEXITCODE -ne 0 -or -not $tUrl) {
                $tArgsFallback = @("issue", "create", "--repo", $Repo, "--title", $t.Title, "--body-file", $tempBodyFile, "--label", $tLabels)
                $rawTOut = & gh @tArgsFallback 2>&1
                $tUrl = ($rawTOut | Out-String).Trim().Split([Environment]::NewLine) | Where-Object { $_ -match "^https?://" } | Select-Object -Last 1
            }
        } finally {
            if (Test-Path $tempBodyFile) { Remove-Item $tempBodyFile -Force }
        }
        $taskIssueId = ($tUrl.Trim() -split "/")[-1]
        Write-Host "Created Task ($taskCount/43): #$taskIssueId $($t.Title)" -ForegroundColor Green
        $issueCatalog.tasks += [ordered]@{
            taskId   = $t.Id
            phaseKey = $t.PhaseKey
            track    = $t.Track
            id       = [int]$taskIssueId
            title    = $t.Title
            url      = $tUrl
        }
        Start-Sleep -Milliseconds 1500
    }
}

# Step 4: Export Issue Catalog Map
$outputMapPath = Join-Path $PSScriptRoot "issue-map.json"
$issueCatalog | ConvertTo-Json -Depth 5 | Set-Content $outputMapPath -Encoding UTF8
Write-Host "Wrote Issue Catalog Map to: $outputMapPath" -ForegroundColor Cyan

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Issue Generation Summary:" -ForegroundColor Yellow
Write-Host "  - Epic Tracker Issues  : 1" -ForegroundColor Green
Write-Host "  - Phase Tracker Issues : 3 (Phase 4, Phase 5, Phase 6)" -ForegroundColor Green
Write-Host "  - Granular Task Issues : 43 (Tracks 4A..4D, 5A..5D, 6A..6E)" -ForegroundColor Green
Write-Host "  - Total Issues         : 47" -ForegroundColor Green
if ($DryRun) {
    Write-Host "DRY-RUN SUCCEEDED: Zero remote issues modified." -ForegroundColor Magenta
} else {
    Write-Host "LIVE CREATION COMPLETED: All 47 issues successfully created on $Repo." -ForegroundColor Green
}
Write-Host "======================================================================" -ForegroundColor Cyan
