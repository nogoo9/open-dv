### Claude CLI Critique (claude-opus-5-5) — Phase M5 / Task 5D-Workbench-Studio-P3

- **Verdict**: ✅ **PASS** (`0 CRITICAL`, `0 MAJOR` enforced)
- **Model**: `claude-opus-5-5`
- **Timestamp**: `2026-11-11T08:36:12Z`
- **Summary Metrics**: `Critical: 0` | `Major: 0` | `Minor: 7` | `Suggestions: 3`

> **Reviewer Summary**: I reviewed Partition 3 of Track 5D: the app shell, the export/promotion modal, the diff overlay, the ELK layout adapter with its fallback, the static build pipeline, the types, and the tests. I found no CRITICAL or MAJOR issues. Nothing in this partition touches the Python core models or how Graph A is serialized, so the SHA-256 / RFC 8785 invariant is unaffected. There are no production mocks or stubs in `src/`. `elkInstance` is an optional dependency-injection seam, not a stub. Layering stays inside the workbench app, and no CLI surface changes, so dual-CLI parity doesn't apply.

The MAJ-01 duplicate-modal fix is correct: `ExportPromoteModal` is mounted once, conditionally. The ELK timeout race is now safe: the timer is cleared in `finally` and the late ELK rejection is swallowed. The fallback layout's timeout test is deterministic, and the path-traversal and SHA regex guards are in place. The two OCC fields (`workspace_sha256` and `head_sha256`) are kept separate, with no fallback from one to the other.

The remaining findings are MINOR:
- When the session switches, the export can briefly request the wrong version.
- The modal lets you export historical versions, but I couldn't confirm from this partition that the store actually exports that version's content.
- Two tests claim to check behaviour that only runs in effects, and `renderToStaticMarkup` never runs effects.
- The promotion UI doesn't check grounding or waivers on the client before submitting.
- A few small hygiene items.

I did not run the test suites; this is a static review of the diff only.

#### Architectural Strengths & Highlights
- The MAJ-01 fix is correct: `ExportPromoteModal` is mounted once and only conditionally, and a test checks for exactly 1 instance when open and 0 when closed.
- The ELK timeout race is handled safely: `clearTimeout` runs in `finally`, and a `.catch` on the ELK promise swallows late rejections.
- The timeout test is deterministic: an injected ELK that never resolves forces the fallback, and the test checks exact coordinates (x=50 and x=390).
- The fallback layout tracks next-layer membership with a Set, drops dangling edges, and handles cycles and isolated nodes.
- The two OCC hashes, `workspace_sha256` and `head_sha256`, are checked separately with a 64-hex regex, with no fallback from one to the other.
- The static build's CLI fallback uses `mkdtempSync(os.tmpdir())` with a guaranteed `rmSync` cleanup, and its error messages include both the programmatic and the CLI failure.
- The export version selector builds its options from session data with no hardcoded version, and export is blocked when no version is available.
- Nothing in this partition touches the Python core models, so the Graph A SHA-256 stability invariant is unaffected.
- The explorer handoff test now covers SPA deep links and trailing-slash paths for the /ontology, /builder, /settings and /stages routes.

#### Findings Ledger
- **[MINOR] MIN-01**: Export preview can fetch the previous session's version after a session switch (`apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx`)
  - *Category*: Logic / State Sync
  - *Detail*: The effect that copies the session's version into `selectedVersion` and the export-preview effect run in the same commit. The preview effect therefore calls `exportOntology(selectedFormat, selectedVersion)` once with the old session's version before the re-render cancels it. Worse, if the new session has no `active_version`, the sync effect skips the update and keeps the stale version. The modal then keeps requesting and offering to download an export labelled with a version from a different session.
  - *Recommendation*: Work out the effective version from what is currently available, for example `versions.includes(selectedVersion) ? selectedVersion : activeVersion ?? ''`. Alternatively, reset `selectedVersion` to `''` unconditionally whenever `sessionId` changes, and skip the export while the selected version isn't in that session's version set.
- **[MINOR] MIN-02**: Historical version selector depends on the store actually exporting that snapshot (`apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx`)
  - *Category*: Data Integrity (verify cross-partition)
  - *Detail*: The Version Snapshot selector lists every committed version, and the downloaded file is named `...-v${selectedVersion}`. But `generateLocalExport(format, version, nodes, edges, session)` receives the current head `nodes`/`edges`. If the store's `exportOntology` doesn't load the historical snapshot (in local_standalone mode especially), users would download head content labelled as an older version. The store is outside this partition, so I couldn't confirm either way.
  - *Recommendation*: Confirm that `exportOntology` resolves each version to its own snapshot, and add a test that exports a non-head version and checks the content differs. If the store can't do that, limit the selector to the head version.
- **[MINOR] MIN-03**: The session-switch reset test never runs the reset effect (`apps/opendv-web-workbench/tests/export-promotion-modal.test.tsx`)
  - *Category*: Test Validity
  - *Detail*: The test 'resets workspace target path and expected SHA when active session switches (MAJ-01)' renders two separate component trees with `renderToStaticMarkup`, which never runs `useEffect`. It passes because the `useState` initializer runs fresh on each mount. The reset effect keyed on `[isOpen, sessionId, ...]` is never exercised, so a regression in that effect would go unnoticed.
  - *Recommendation*: Use a client renderer that runs effects (react-dom/client with happy-dom, or `act()`). Switch the session on the same mounted instance and check that the input value and SHA field have been reset.
- **[MINOR] MIN-04**: The MIN-03 test title says it checks an error banner, but it can't, and its string match is brittle (`apps/opendv-web-workbench/tests/export-promotion-modal.test.tsx`)
  - *Category*: Test Validity
  - *Detail*: The test title says the error banner is displayed when export fails, but the error is only set inside an effect, which SSR doesn't run, and the test never checks for `export-error-banner`. It also matches the literal `'disabled="" data-testid="copy-export-btn"'`, so reordering the JSX attributes would break it.
  - *Recommendation*: Rename the test to describe what it actually checks, or add an effect-capable render that checks for the banner. Locate the button by testid first, then check for the `disabled` attribute separately so attribute order doesn't matter.
- **[MINOR] MIN-05**: The promotion form doesn't check grounding or waivers on the client (`apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx`)
  - *Category*: ONT-EVO-002 (defense in depth)
  - *Detail*: The modal checks the rationale, unsaved changes, the path, and both SHAs, but never looks at `qualityScorecard` or UNGROUNDED/LOW_CONFIDENCE nodes. Server-side enforcement should stay the authority, but in `local_standalone` mode the UI lets you promote ungrounded concepts without any warning or waiver hint.
  - *Recommendation*: Show a blocking or warning banner when ungrounded concepts are present and no waiver has been applied (`qualityScorecard.waiver_applied`). Make sure the store or service path enforces ONT-EVO-002 in standalone mode too.
- **[MINOR] MIN-06**: The traversal check rejects valid names and is only enforced on the client (`apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx`)
  - *Category*: Input Validation
  - *Detail*: `trimmedPath.includes("..")` also rejects legitimate segments such as `ws..v2`. Nothing in this diff shows the matching server-side check, and the client check alone is not a security boundary.
  - *Recommendation*: Split on `/` and `\` and reject only segments that are exactly `..`. Make sure the platform API checks that the resolved workspace path stays inside the allowed workspace root.
- **[MINOR] MIN-07**: Unused store selectors, and the shell writes to the store directly (`apps/opendv-web-workbench/src/ontology/OntologyStudioApp.tsx`)
  - *Category*: Code Hygiene
  - *Detail*: `isPromoting` is selected from the store but never used. The modal keeps its own local `isPromoting`, so there are two sources of truth. The Re-layout button calls `studioStore.setState` directly instead of going through a store action, which skips the store's action layer. The session `<select>` can also have a `value` that matches no option when sessions exist but none is active.
  - *Recommendation*: Remove the unused selector, or use the store's `isPromoting` in the modal. Add a `triggerRelayout()` store action. When no session is active, render a placeholder option for the empty value.
- **[SUGGESTION] SUG-01**: The ELK timeout can't stop the bundled synchronous computation (`apps/opendv-web-workbench/src/ontology/components/graph-layout.ts`)
  - *Category*: Layout Performance
  - *Detail*: `elk.bundled.js` without a worker runs on the main thread. When the timeout wins, the layout keeps using CPU, and a long synchronous layout can stop the timer from firing on time. The docstring already says the budget is best-effort.
  - *Recommendation*: For large graphs, consider `elk-worker` with a `workerUrl` so slow layouts can be terminated and the UI stays responsive.
- **[SUGGESTION] SUG-02**: Revoking the download's object URL right after `click()` (`apps/opendv-web-workbench/src/ontology/components/ExportPromoteModal.tsx`)
  - *Category*: Robustness
  - *Detail*: Some browsers can cancel the download if `URL.revokeObjectURL` runs synchronously right after `a.click()`.
  - *Recommendation*: Defer the revoke, e.g. `setTimeout(() => URL.revokeObjectURL(url), 0)`.
- **[SUGGESTION] SUG-03**: Only the entry-point JS artifact is embedded (`apps/opendv-web-workbench/src/build-static.ts`)
  - *Category*: Build Pipeline
  - *Detail*: If the workbench entry ever imports CSS or other assets, both `Bun.build` and the CLI fallback produce extra outputs. Those are silently dropped (and deleted with the temp dir). Separately, the cast `(o as unknown as { kind?: string })` isn't needed, because `BuildArtifact.kind` is already typed.
  - *Recommendation*: Fail the build when non-entry artifacts appear, or collect and embed them. Remove the unneeded cast.
