# Technical Research: Open-DV Ontology Construction Studio
**Status**: Proposed (Under M1 Review) | **Ref**: `master-plan.md`, `PROJECT.md`, ADR-001..016

## 1. Executive Summary
The Studio bridges documents and Data Vault 2.0 via FIBO reference ontology, layout ingestion, interactive graph visualization, and semantic concept matching.

## 2. FIBO Reference Ontology Architecture

### 2.1 Curated Modules
FIBO (`master_2024Q4`): Vendors 6 modules: FND, BE, SEC, FBC, LOAN, IND. `vendor_fibo.py --check` verifies tag resolves to commit `69dbbe7a6a4e32049ba3f3988b48877bc954f061` and matching archive SHA-256.

### 2.2 OWL 2 DL Axiom Mapping to Data Vault 2.0
- `owl:Class` -> CBC -> Hub (`hub_<slug>`): 1:1 Hub bijection (`ONT-EVO-002`).
- `rdfs:subClassOf` -> in-ontology `parent_concept_uri` -> HAL (`hal_<slug>`) (DAG acyclicity; `ONT-EVO-002`); external ground as `ExternalMapping(mapping_type="subClassOf")`; deployed rebind blocked (`ONT-EVO-001`).
- `owl:ObjectProperty` -> `CanonicalRelationship` -> Link (`lnk_<slug>`) / Trans Link (`t_lnk_<slug>`).
- `owl:DatatypeProperty` -> `attributes` -> Satellite (`sat_<slug>`).
- `owl:oneOf` / Controlled Class -> `ReferenceEnumeration` -> Reference Table (`ref_<slug>`) (`ONT-EVO-001`).
- `skos:exactMatch`/`closeMatch` -> `external_mappings` -> Hub/Sat Alignment (`ONT-EVO-002`).

### 2.3 Performance Budgets & Provenance Rules
- `fibo-core` (~3,200 concepts): <18 MB RAM, <70 ms load. BM25: <5 MB RAM, <2 ms query, <50 ms build.
- **Closure subClassOf Exemption**: Reference-pack closure `subClassOf` mappings carry `provenance="deterministic"`, exempt from committing actor match if structural closure verification passes (else `ONT_EVO_FORGED_PROVENANCE` exit 4).

### 2.4 Serialization Pipeline
Export (`GET /sessions/{id}/export?format={fmt}`) builds `rdflib.Graph` serialized to Turtle (`dv2:` -> `https://opendv.org/schema/v2#`) or JSON-LD.

## 3. Layout-Aware Ingestion
- **Adapters**: `LiteParseDocumentAdapter` (`liteparse` 0.4.x, Apache 2.0, self-contained embedded PDFium without system dependencies, native tabular/Markdown); tested `pypdf` fallback path retained.
- **Character Grounding**: Global slice table ($0 \le \text{start}_i \le \text{end}_i \le \text{start}_{i+1}$), $O(\log N)$ search; `EvidenceRef` offsets count Unicode code points. Complex pages route to OCR. Office docs convert via `soffice` (missing -> `OFFICE_CONVERSION_UNAVAILABLE` exit 1).

## 4. Interactive Graph Visualization
- **React Flow + elkjs**: `@xyflow/react` React 19 SPA, `elkjs` Web Worker (500 nodes <300 ms).
- **LOD & Diff View**: `onlyRenderVisibleElements={true}`. Macro (<0.45), Meso (0.45-1.15), Micro ($\ge$1.15). Diffs: Added (`#10b981`), Modified (`#f59e0b`), Removed (`#ef4444`).

## 5. Semantic Concept Matching
- **Lexical BM25**: $k_1=1.5, b=0.75$, 3,200 FIBO concepts. Score ($2.5 \cdot \text{name} + 2.0 \cdot \text{syn} + 1.0 \cdot \text{def}$), normalized symmetrically (ADR-008 §2; confidence $\ge 0.65$). Pluggable SPI (ADR-008).
