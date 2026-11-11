# Context Notes for Claude Opus 5.5 Critique - Track 5D Partition 1: Studio Store & State Layer

## Architecture & Partitioning Plan
Track 5D implements the Web Workbench Visual Studio SPA (`/ontology`, Tasks 5D.1–5D.6, Issues #222–#227) in `apps/opendv-web-workbench`.
Due to the `MAX_DIFF_CHARS = 120_000` diff limit, the track is reviewed across 3 partitions:
- **Partition 1 (Current)**: Studio State Store (`store/studio-store.ts`), Route Utilities (`route-utils.ts`), Dev Server route handling (`dev-server.ts`), and package dependencies (`package.json`).
- **Partition 2**: Type definitions (`store/types.ts`), Sources Panel, Clarification Panel, Diff Overlay, Visual Editor Drawer, Graph Canvas & Elkjs Layout, Quality Scorecard, and static build & panel/canvas tests.
- **Partition 3**: Export & Promotion Modal, SPA Shell App, Index entry, and store & modal tests (`tests/ontology-studio-store.test.ts`, `tests/export-promotion-modal.test.tsx`).

All components and files are present in the checkout and verified with Bun:
- `bun test --cwd apps/opendv-web-workbench`: **177 pass, 0 fail (1215 expect calls)**
- `bun x tsc -b apps/opendv-web-workbench`: Clean 0 errors
- `bun run build:static`: Clean static bundle emitted (2.55 MB JS, 2.77 KB CSS)

## Key Architectural & Invariant Implementations in Partition 1
1. **Genuine Delta Operations & Commit Tracking (CRIT-01 Resolution)**:
   - `commitChanges` compares active `nodes` and `edges` against `committedNodes` and `committedEdges` to generate an authentic sequence of delta operations conforming to `opendv_contracts.dtos.ontology.OntologyDelta` and `OntologyDeltaRequest`:
     - `add_concept`: New concepts added during the session, with target hub, parent concept URI, archetype, definition, and attributes.
     - `refine_definition`: Changed definitions for existing concepts.
     - `add_attribute`: New attributes attached to existing concepts.
     - `add_external_mapping`: New external reference mappings attached to existing concepts.
     - `add_relationship`: New relationships connecting concepts with target link and cardinality.
   - Computes target SemVer version dynamically (`MINOR` bump if concepts/relationships added, `PATCH` bump for attribute/definition refinements).
   - Anchors the commit with strict OCC `If-Match: "${activeSession.head_sha256}"` header.
   - On commit success, updates `head_sha256`, `active_version`, clears `hasUnsavedChanges`, and synchronizes `committedNodes` / `committedEdges`.

2. **Race Protection & Session Isolation Across All Mutating Actions (MAJ-02, MIN-01, MIN-05)**:
   - `selectSession` verifies request generation (`reqId === activeSessionRequestId`) before and after `await res.json()` and before firing follow-up graph/clarification loads.
   - Initial state reset in `selectSession` clears `answers_sha256`, `parsingState`, `hasUnsavedChanges`, and graph state to prevent cross-session leaks.
   - Mutating async actions (`commitChanges`, `promoteWorkspace`, `submitAnswer`, `submitWaiver`, `uploadSource`, `deleteSource`) capture `const sid = activeSession.session_id` and enforce `if (get().activeSession?.session_id !== sid) return;` after every `await` to prevent state corruption when switching sessions mid-flight.
   - `loadGraph` and `loadClarifications` track request generation IDs (`activeGraphRequestId`, `activeClarificationsRequestId`) and emit structured RFC 9457 error toasts on non-OK responses.

3. **Structural Diff Engine (MAJ-03)**:
   - `computeDiff` accepts base and target versions with optional `baseGraph`.
   - Compares concepts (added, deleted, modified), concept attributes (detecting breaking attribute removals), external mappings, and relationships (added, deleted, modified).
   - Only marks concepts as 'modified' when actual content differs (definition, attributes, mappings).

4. **Strict ONT-EVO-002 Invariant Enforcement & Ungrounded Concept Normalization (MAJ-01, MIN-01)**:
   - `computeQualityScorecard` strictly enforces ONT-EVO-002: reports `FAIL` whenever ungrounded concepts exist unless an authorized steward administrative waiver covers every ungrounded concept URI (`waived_concept_uris`).
   - `addConcept` automatically normalizes `grounding_status` to `"UNGROUNDED"` when no external mappings are provided.
   - Adding a new ungrounded concept to a graph with an existing waiver invalidates ONT-EVO-002 and immediately reports `FAIL`, preventing ungrounded leaks.
   - `waiver_applied`, `waiver_rationale`, and `waived_concept_uris` are preserved across graph reloads.

5. **Export & Token Hygiene (MIN-02, MIN-04, MIN-08)**:
   - Weak ETags stripped of `W/` prefix and quotes.
   - JSON local export retains `parent_concept_uri`, `target_hub`, `cardinality`, and `target_link`.
   - `submitWaiver` rejects requests when the graph is empty (`EMPTY_GRAPH`) or all concepts are already grounded (`NO_UNGROUNDED_CONCEPTS`).

6. **SSR & Static Test Rendering (MIN-06 Note)**:
   - Zustand v5's default `useStore(api, selector)` sets `getServerSnapshot = () => selector(api.getInitialState())`. In unit test environments using React 19's `renderToStaticMarkup`, React calls `getServerSnapshot` rather than `getSnapshot`. Passing `getInitialState()` causes pre-seeded store state in static component tests to be ignored. Therefore, `useStudioStore` explicitly passes `() => selector(studioStore.getState())` as both client snapshot and server snapshot, ensuring deterministic behavior in both browser SPA and static test contexts.
