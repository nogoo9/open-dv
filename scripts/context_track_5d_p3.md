# Context Notes for Claude Opus 5.5 Critique - Track 5D Partition 3: App Shell, Export/Promotion, Diff Overlay & Canvas Tests

## Architecture & Partitioning Plan
Track 5D implements the Web Workbench Visual Studio SPA (`/ontology`, Tasks 5D.1–5D.6, Issues #222–#227) in `apps/opendv-web-workbench`.
This is Partition 3 of 4 (111,230 characters diff, strictly within `MAX_DIFF_CHARS = 120_000` limit), covering:
- **Studio State Type Definitions** (`src/ontology/store/types.ts`):
  - Canonical type definitions for `ConceptNode`, `RelationshipEdge`, `QualityScorecard`, `DiffState`, `StudioSessionSummary`, and error toasts.
  - Conforms strictly to `schemas/v1/openapi.json` and `schemas/v1/error-codes.json`.
- **Graph Layout Adapter with Deterministic Fallback & Timeout Budget** (`src/ontology/components/graph-layout.ts`):
  - Layered graph layout using elkjs with deterministic fallback (< 300 ms for 100+ nodes).
  - Timeout race enforcement with timeout handle stored and cleaned up via `clearTimeout(timeoutHandle)` in a `finally` block.
  - Attached `.catch(() => {})` to `elk.layout(elkGraph)` promise before `Promise.race` to prevent unhandled late rejection if timeout wins the race (MIN-05).
  - Docstring reflects best-effort timeout budget falling back to deterministic layered layout on slow environments (MIN-05).
  - Accepts optional `elkInstance` dependency injection in `LayoutOptions` for deterministic unit testing (MIN-04).
  - Set-based O(1) next-layer membership tracking in fallback BFS avoiding O(n^2) on wide layers.
  - Dangling edge filtering ensuring missing sources or targets do not produce phantom layout slots.
- **Visual Studio SPA Shell Application** (`src/ontology/OntologyStudioApp.tsx`):
  - Top navigation bar with active session selector, branch status, commit counter, and quick action buttons (Export, Promote, Diff, New Session, Re-layout).
  - Single conditional mount of `<ExportPromoteModal>` only when `layout.activeModal === "export" || layout.activeModal === "promote"`, with duplicate unconditional element deleted (MAJ-01).
  - App-level Escape listener is strictly isolated to `layout.activeModal === "new_session"`, allowing `ExportPromoteModal` to manage its own Escape lifecycle according to its local `isPromoting` state and tab selections (MIN-01).
  - Header Visual Diff dynamically computes base version from `activeSession.commits` history (immediate predecessor `commits[commits.length - 2]?.version`).
  - Re-layout header button wired to trigger canvas auto-layout via store `layoutTriggerCount`.
  - Offline banner displaying `Standalone Mode (Local In-Memory Engine)` when `activeMode === "local_standalone"`.
  - Empty-state prompt with "Compute Visual Diff" action when viewing Diff history tab and `diffState.active` is false (SUG-01).
  - Collapsible side drawers and bottom dock.
- **Multi-Format Export & Workspace Promotion Modal** (`src/ontology/components/ExportPromoteModal.tsx`):
  - Multi-format exporter supporting Graph A YAML, JSON Schema Representation (.json), W3C Turtle (.ttl), JSON-LD, and RDF/XML.
  - Snapshot version selector builds options dynamically from `active_version` and `commits` without hardcoded "1.0.0" fallback, displays "No versions available" if empty, and guards export execution (MIN-06).
  - Dual OCC verification with independent `workspace_sha256` and `head_sha256`, client-side regex guard `/^[a-f0-9]{64}$/i`, and no aliasing or fallback between the two.
  - Precondition checks on `hasUnsavedChanges` and rationale length >= 10 chars.
  - Client-side relative path validation rejecting absolute paths and directory traversal (`..`) (SUG-03).
  - Explicit error message set in modal when promotion returns false or fails concurrency check, and clean error string unwrapping without raw 'Error: ...' prefix (MIN-07).
  - Copy-success timeout ref tracked and cleared in effect cleanup (SUG-02).
- **Visual Diff Overlay** (`src/ontology/components/DiffOverlay.tsx`):
  - Visual diff visualization supporting both unified list and side-by-side comparative views.
  - Strictly-additive evolution banner citing ONT-EVO-001 consistently.
  - Empty state message rendered in side-by-side target column when zero items added/modified.
  - Composite React keys (`${r.item_type}-${r.change_type}-${r.identifier}-${idx}`) preventing key collision in side-by-side and unified views.
  - Conditional rendering returning `null` when `mode === "off" || !diffState.active`.
- **Static Asset Builder & Index Route Entry** (`src/build-static.ts`, `src/index.ts`):
  - Bun-based static bundling pipeline embedding static HTML, JS, and CSS bundles for embedding in `@opendv/platform-api`.
  - Concurrency-safe build using `os.tmpdir()` with `mkdtempSync` for guaranteed cleanup outside workspace tree.
  - Client route registration mounting `/ontology` and `/ontology/*` without regressions to the 3-level pipeline explorer.
- **Canvas & Promotion Test Suites** (`tests/ontology-graph-canvas.test.tsx`, `tests/export-promotion-modal.test.tsx`, `tests/explorer-handoff.test.ts`):
  - Test verifying exactly one `ExportPromoteModal` instance is mounted when `activeModal` is active, and 0 when null (MAJ-01).
  - Assertions on exact `disabled=""` attributes on export Copy and Download buttons when preview is empty (MIN-03).
  - Deterministic timeout test in `ontology-graph-canvas.test.tsx` using injected hanging ELK instance to verify exact fallback coordinates (`x === 50` for root, `x === 390` for child) on layout timeout (MIN-04).
  - Global `studioStore` singleton reset in `beforeEach` to eliminate order-dependent coupling.
  - Strictly typed test fixtures conforming to declared `types.ts` interfaces.
  - Explorer handoff test suite ensuring zero regressions across all 8 canonical pipeline stages and existing workbench routes.
