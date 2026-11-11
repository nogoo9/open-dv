# Context Notes for Claude Opus 5.5 Critique - Track 5D Partition 2: UI Panels, Canvas & Editors

## Architecture & Partitioning Plan
Track 5D implements the Web Workbench Visual Studio SPA (`/ontology`, Tasks 5D.1–5D.6, Issues #222–#227) in `apps/opendv-web-workbench`.
This is Partition 2 of 4 (113,127 characters diff, strictly within `MAX_DIFF_CHARS = 120_000` limit), covering:
- **Sources & Parsing Management Panel** (`components/SourcesPanel.tsx`)
- **Clarification Q&A Panel & Answer Submissions** (`components/ClarificationPanel.tsx`)
- **Visual Editor Drawer** (`components/VisualEditorDrawer.tsx`)
- **Quality Scorecard & Steward Feedback Panel** (`components/QualityFeedbackPanel.tsx`)
- **React Flow Graph Canvas & Multi-Tier LOD** (`components/GraphCanvas.tsx`)
- **Panel & Component Behavioral Tests** (`tests/ontology-panels.test.tsx`)

All tests pass cleanly:
- `bun test --cwd apps/opendv-web-workbench`: **197 pass, 0 fail (1262 expect assertions)**
- `bun x tsc -b apps/opendv-web-workbench`: Clean 0 errors
- `bun run build:static`: Clean static bundle emitted (2.56 MB JS, 2.77 KB CSS)

## Key Invariant Implementations & Reviewer Findings Addressed in Partition 2
1. **Raw Draft Construction & Committed Field Removal Gating (MAJ-01 Resolution)**:
   - In `VisualEditorDrawer.tsx`, `draft` is constructed directly from the user's actual input before validation:
     `definition: trimmedDef.length > 0 ? trimmedDef : null`, `target_hub: trimmedHub.length > 0 ? trimmedHub : null`, `parent_concept_uri: trimmedParent.length > 0 ? trimmedParent : null`.
   - `validateConceptEdit` runs on the raw draft and properly catches non-additive field clearing:
     - `CANNOT_REMOVE_COMMITTED_DEFINITION` fires when a committed definition is cleared by the user.
     - `CANNOT_MODIFY_COMMITTED_TARGET_HUB` fires when a committed target hub is cleared by the user.
     - `CANNOT_REPARENT_COMMITTED_CONCEPT` fires when a committed concept parent is modified or added to a root concept.
   - For valid edits and uncommitted draft field removals, `updated` cleanly sets empty fields to `undefined`, allowing draft values to be cleared.
   - On an unchanged save, `currentStoreConcept.definition === updated.definition` (`undefined === undefined` or `"Def" === "Def"`) evaluates to true; the no-op check succeeds and skips `updateConcept`, preserving CAS hash stability.

2. **Dedicated Behavioral & Contract Test Coverage (MAJ-02 Resolution)**:
   - `tests/ontology-panels.test.tsx` includes dedicated tests for:
     - `validateConceptEdit blocks removing definition of committed concept (MAJ-01)`
     - `validateConceptEdit blocks adding a parent to a committed root concept (MIN-10)`
     - `validateConceptEdit allows removing uncommitted draft values on committed concept`
     - `VisualEditorDrawer renders concept details and handles validation`
     - `SourcesPanel renders inventory with TXT support and privacy pills`
     - `ClarificationPanel renders question list and pending count badge`
   - In `tests/ontology-graph-canvas.test.tsx`:
     - `computeFallbackLayeredLayout with cycle and isolated node resilience`
     - `computeFallbackLayeredLayout drops dangling edges without creating phantom slots`
     - `layoutGraph respects timeoutMs and falls back to deterministic layout`

2. **Drawer Form State Preservation, Cancel/Discard Reset & OCC Conflict Detection (MAJ-01 & MIN-01 Resolution)**:
   - In `VisualEditorDrawer.tsx`, base snapshot captured via `initialSnapshotRef = useRef<string>(selectedConcept ? JSON.stringify(selectedConcept) : "")` updated on concept selection change.
   - On `handleClose` (Cancel), resets `prevSelectedUriRef.current = undefined`, `initialSnapshotRef.current = ""`, and clears form/validation state, completely discarding pending edits so reopening the same concept re-initializes cleanly from the store.
   - On `handleSave`, checks `currentStoreConcept && initialSnapshotRef.current && JSON.stringify(currentStoreConcept) !== initialSnapshotRef.current` and raises `CONCURRENT_MODIFICATION_DETECTED: Concept was modified in store. Discard or re-open to merge changes (ONT-EVO-003)`.
   - On `handleSave`, no-op equality check skips mutation when no edits occurred.

3. **Session Switch State Management & Disabling Canvas Desync (MIN-03 & MIN-05 Resolution)**:
   - In `GraphCanvas.tsx`, set `deleteKeyCode={null}` on ReactFlow so Backspace/Delete cannot remove canvas nodes/edges without store mutation.
   - In `runAutoLayout`, only clears `isLayoutRunning` if `layoutGenRef.current === currentGen`.
   - On session switch, clears canvas node positions (`setNodes([])`).
   - In `QualityFeedbackPanel.tsx`, tracked `activeSessionId` resets form state only when session ID changes; scorecard polls do not wipe user inputs. Grounding gauge fill color aligned with `Math.round(groundingPct) >= 65`.

4. **Sources Batch Errors & Confirmation Guard (MIN-11 Resolution)**:
   - In `SourcesPanel.tsx`, format hint aligned with TXT (`Supported: PDF, DOCX, XLSX, HTML, JSON, CSV, Markdown, TXT`).
   - Batch upload collects all file errors into an array.
   - `handleDeleteSource` includes confirmation prompt before deletion.
   - `source_sha256` slice guarded against undefined.

5. **Clarification Feedback on Submission Rejection (MIN-12 Resolution)**:
   - In `ClarificationPanel.tsx`, records and displays an error banner per question when `submitAnswer` returns false or throws.

3. **Committed Attribute Type Immutability & Case-Insensitive Duplicate Check (MIN-02 & MIN-08 Resolution)**:
   - In `validateConceptEdit`, added strict check for committed attribute data type immutability (`matching.data_type === ca.data_type`), preventing breaking schema changes under ONT-EVO-001.
   - Duplicate attribute detection normalizes attribute names with `.toLowerCase()` before checking set size.

4. **Parsing Gating on Active Session (MIN-06 Resolution)**:
   - In `SourcesPanel.tsx`, `handleStartParse` requires `activeSession` and sets validation error if missing, matching `handleFiles`.

5. **Waiver State Synchronization & Credential Hygiene (MIN-07 Resolution)**:
   - In `QualityFeedbackPanel.tsx`, `waiverRequested`, `waiverRationale`, `rating`, and `comment` sync with `qualityScorecard` in an effect.
   - `waiverKey` is cleared immediately after submission.
   - The active waiver badge (`waiver-applied-badge`) is rendered visibly outside the toggle block.

6. **Incremental Node Staggering (MIN-09 Resolution)**:
   - In `GraphCanvas.tsx`, unplaced nodes without pre-existing positions are staggered in a grid (`100 + (idx % 5) * 260, 100 + Math.floor(idx / 5) * 160`) rather than stacking at (100, 100).

7. **Source Document CAS Hash Stability & Raw Byte Upload**:
   - In `SourcesPanel.tsx`, ALL source files are read directly as raw bytes via `await file.arrayBuffer()` without `file.text()`, guaranteeing 100% byte stability and content SHA-256 stability in CAS.
   - Client-side size guard (`maxFileSizeBytes`, default 50 MB) emits RFC 7807 413 `PAYLOAD_TOO_LARGE` toast before memory allocation.
   - Source deletion uses `handleDeleteSource` with try/catch and error reporting to avoid unhandled rejections.

8. **Behavioral Test Coverage for ONT-EVO-001 Committed Concept Guards**:
   - `VisualEditorDrawer.tsx` exports pure `validateConceptEdit`.
   - `tests/ontology-panels.test.tsx` includes comprehensive behavioral tests for:
     - `CANNOT_RENAME_COMMITTED_CONCEPT`
     - `CANNOT_CHANGE_ARCHETYPE`
     - `CANNOT_REPARENT_COMMITTED_CONCEPT`
     - `CANNOT_MODIFY_COMMITTED_TARGET_HUB`
     - `CANNOT_REMOVE_COMMITTED_SYNONYM`
     - `CANNOT_REMOVE_COMMITTED_ATTRIBUTE`
     - `CANNOT_MODIFY_COMMITTED_ATTRIBUTE_TYPE`
     - `CANNOT_REMOVE_COMMITTED_MAPPING`
     - Direct self-parenting and multi-hop cycle detection under ONT-DAG-001
     - Valid purely additive changes passing with 0 errors
   - Tests for deterministic layered layout with cycles, isolated nodes, and dangling edges.
