# Master Plan: Open-DV Ontology Construction Studio
Governs design, spec, planning, and delivery of the Ontology Studio across Phases 0–6.

## 1. Goal
An Open-DV app analyzing documents/data, grounding in FIBO, asking clarifying questions, and producing Data Vault 2.0 ontologies with visual editing, diffing, and export.

## 2. Decision Log
Autonomous delivery; 6 FIBO modules via rdflib; additive deltas & BM25; CAS & React Flow; redaction & RBAC; Opus critique gate; workspace isolation & promotion.

## 3. Delivery Strategy & Tiering
Phases 0–6: 0 Setup -> 1 Research -> 2 Spec -> 3 Plan -> 4 Wave 1 -> 5 Wave 2 -> 6 Release. Requires 0 CRITICAL/MAJOR Opus findings. Tiers: T1 Arch, T2 Core, T3 Mechanical, T4 Housekeeping.

## 4. Documentation Deliverables
`docs/design/ontology-studio/` (`research.md`, `ADR-001..016`, `HANDOFF.md`), `docs/19-ontology-studio-architecture.md`..`docs/23-extension-guide.md`.

## 5. Solution Outline
1. Ingestion (`opendv-sources`): `LiteParseDocumentAdapter` (page map, OCR), profiler.
2. References (`opendv-sources`): `ReferenceOntologyAdapter` SPI, FIBO pack.
3. Models (`opendv-core`, `opendv-contracts`): Graph A extensions, additive deltas, SemVer.
4. Engine (`opendv-service`): Ingestor, BM25 matcher, dual-mode engine, audit.
5. Storage: CAS in `.opendv/ontology_studio/`, `.odvstudio.zip`, async SSE.
6. Surfaces: REST/SSE API, dual CLI parity (`opendv`/`opendvctl`), React Flow.

## 6. Phases
- 0 Setup: Branch `dev/ontology-studio`, labels.
- 1 Research & Design: Survey (`research.md`), UX (`HANDOFF.md`), ADRs & Gate.
- 2 Spec: Arch (`docs/19`), Spec (`docs/20`), Golden set, Critique.
- 3 Implementation: Plan (`docs/21`), Issues.
- 4 Wave 1: 4A Models, 4B Ingest, 4C FIBO, 4D CAS/SSE.
- 5 Wave 2: 5A Engine/BM25, 5B API/CLI parity, 5C UI, 5D Graph.
- 6 Release: 6a Wiring, 6b Eval, 6c Guides, 6d Docs, 6e PR.

## 7. Definition of Done (v1)
- Issues closed via merged PRs to `dev/ontology-studio` with 0 CRITICAL/MAJOR Opus findings.
- Certified baseline digests unchanged: `pe` (`1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81`), `s2`, `s3`, `s9`, `issue_40`, `uf2` per ADR-005.
- End-to-end verified across Web, `opendv`, and `opendvctl`.
