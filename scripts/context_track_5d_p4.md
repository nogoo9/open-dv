# Context Notes for Claude Opus 5.5 Critique - Track 5D Partition 4: Studio State Store Test Suite

## Architecture & Partitioning Plan
Track 5D implements the Web Workbench Visual Studio SPA (`/ontology`, Tasks 5D.1–5D.6, Issues #222–#227) in `apps/opendv-web-workbench`.
This is Partition 4 of 4 (diff ~112,000 characters, strictly within `MAX_DIFF_CHARS = 120_000` limit), covering:
- **Comprehensive Studio Store Test Suite** (`tests/ontology-studio-store.test.ts`):
  - Invariant testing for Task 5D.1: Zustand store lifecycle, session switching, optimistic updates, and offline fallback mode.
  - Invariant testing for Task 5D.2: Source file upload handling, binary `ArrayBuffer` CAS hash stability, and parse trigger workflows.
  - Invariant testing for Task 5D.3: Clarification question response tracking, answer draft resolution, and batch submissions.
  - Invariant testing for Task 5D.4: Graph node and edge management, ELK layout state synchronization, and search filtering via `selectFilteredNodes`.
  - Invariant testing for Task 5D.5: Strictly additive evolution validation (ONT-EVO-001), DAG inheritance acyclicity checks (ONT-DAG-001), and visual diff record calculation.
  - Invariant testing for Task 5D.6: Quality scorecard computation, administrative waiver persistence (rationale, credential protection, and base SHA freshness), 5-star steward feedback recording to `feedback.jsonl` with integer rating validation, and multi-format export serialization (YAML, RFC 8785 JSON, Turtle, JSON-LD, RDF/XML).
- **Cross-Language RFC 8785 Graph A Golden Fixture** (`packages/opendv-contracts/schemas/v1/fixtures/graph_a_golden.json`).

## Resolution of Prior Findings (Iteration 3)
1. **MAJ-01 (Cross-Language Golden Digest Parity & Genuine Round-Trip Verification)**:
   - Committed canonical cross-language fixture `packages/opendv-contracts/schemas/v1/fixtures/graph_a_golden.json`, computed by Python `opendv_contracts.jcs.dumps_jcs` and `opendv_core.ontology_hashing` with concepts `Portfolio` and `Asset`. Expected SHA-256: `"dec92838dddee6d823b866e2b66d66c91cc7cd57c9b355e27901ca863916c8fe"`.
   - In `tests/ontology-studio-store.test.ts`, the golden test constructs genuine `ConceptNode[]` from `goldenFixture.raw_model.canonical_business_concepts` (with client-only `grounding_status` mapped appropriately), passes them through the production `generateLocalExport("rfc8785", ...)`, and asserts both bit-for-bit JCS string equality and exact SHA-256 digest parity.
   - Replaced the circular round-trip test with a genuine reconstruction test: exports Graph A via `generateLocalExport("rfc8785", ...)`, deserializes with `JSON.parse`, maps back to `ConceptNode[]` and `RelationshipEdge[]`, re-exports with `generateLocalExport("rfc8785", ...)`, and asserts byte-for-byte identity and SHA-256 digest match.
   - Aligned `canonicalizeGraphADict` in `studio-store.ts` to prune empty optional collections (`attributes: []`, `synonyms: []`, `external_mappings: []`) matching Python `_norm_empty` in ADR-005.

2. **MAJ-02 (ONT-EVO-002 Concept-Level Waiver Scoping Isolation)**:
   - In `causes ONT-EVO-002 to fail when a new ungrounded concept is added after waiver authorization`, populated `sessionWaivers` with a fresh authorized waiver (`waived_base_sha256 === session.head_sha256`, `waived_concept_uris: ["urn:ontology:entity:custom_alpha"]`).
   - Verified that with `custom_alpha` alone, `waiver_applied === true` and ONT-EVO-002 is `PASS`.
   - Called `store.getState().addConcept(custom_beta)` (where `custom_beta` is ungrounded and not in `waived_concept_uris`), asserting that `waiver_applied` remains `true` (fresh and active for the session) while ONT-EVO-002 strictly becomes `FAIL`.
   - Added direct pure assertion calling `computeQualityScorecard([alpha, beta], [], { waiverApplied: true, waivedUris: [alpha] })` expecting `FAIL`.

3. **MIN-01 (Binary ArrayBuffer CAS Exact Digest & Stored Document Schema)**:
   - Verified exact SHA-256 digest `"38523c087796e5d5dd1cf9bad1fb026781a838dd9dd2cf8af58b9f6502a46778"` for the 5-byte `%PDF-` buffer (`[0x25, 0x50, 0x44, 0x46, 0x2d]`).
   - Aligned mock upload response to OpenAPI schema fields (`source_sha256`, `size_bytes`, `file_name`, `mime_type`, `source_id`, `uploaded_at`, `status: "UPLOADED"`).
   - Asserted that `uploaded.source_sha256`, `uploaded.size_bytes`, `store.getState().sources[0].source_sha256`, and `store.getState().sources[0].size_bytes` match the expected values.

4. **MIN-02 (Authoritative Server Waiver Record & Denial on 200)**:
   - In `enforces waiver rationale >= 10 characters`, the mocked server returns an authoritative waiver record (`status: "authorized"`, `waiver_applied: true`, `waived_base_sha256`, `waived_concept_uris`), which is persisted to `sessionWaivers`.
   - In `studio-store.ts`, updated `submitWaiver` to inspect server `waiverData`: if `waiverData.waiver_applied === false`, the store creates a toast with code `WAIVER_DENIED`, does not record a waiver in `sessionWaivers`, and returns `false`.
   - Added explicit test verifying that a 200 OK response with `waiver_applied: false` is rejected and does not apply the waiver.

5. **MIN-03 (Export Format Separation & Determinism on Shuffled Input)**:
   - Separated and documented JSON schema envelope format (`json`) vs Graph A RFC 8785 format (`rfc8785`).
   - In `generateLocalExport("json", ...)`, added deterministic sorting of concepts and relationships by URI before constructing the export object.
   - Added tests verifying that both `generateLocalExport("json", ...)` and `generateLocalExport("rfc8785", ...)` produce bit-for-bit identical strings when input concept/relationship arrays are shuffled.

6. **MIN-04 (Clean Imports & Code Hygiene)**:
   - Removed unused import `createHash` from `node:crypto`.
   - Removed unused type imports (`ClarificationAnswer`, `ClarificationQuestion`, `QualityCheck`, `QualityScorecard`, `SourceDocument`).

7. **MIN-05 (Feedback Request URL/Method/Payload & Session Switch State Invariants)**:
   - In `submitFeedback` test, captured the fetch call and asserted endpoint path (`/api/v1/ontology/studio/sessions/sess-feedback/feedback`), HTTP method `POST`, and payload containing `session_id`, `rating: 5`, `comment`, and `submitted_at`.
   - In session switch race test during commit, assigned distinct `nodes` and `committedNodes` (`nodeB`) to Session B and asserted they remain completely untouched when Session A's commit resolves.
