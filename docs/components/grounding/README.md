# Open-DV Ontology Grounding Engine (`src/opendv/grounding/`)

The **Ontology Grounding Component** (`src/opendv/grounding/`, Layer 2a / Steps 2–5) resolves operational table columns, value shapes, and source tables into calibrated semantic concepts, properties, relationships, enumerations, and tamper-evident `GroundingReceipt`s before Data Vault 2.0 synthesis (`src/opendv/modeling/`).

- **Standalone & Agentic Column Grounding (`opendv ontology ground` / `POST /api/v1/ontology/ground`):** Combines synchronous deterministic lexical, checksum/format, range, and table-anchor evidence with Logit-Normal confidence intervals and optional Layer-4 LLM disambiguation (`agentic` default, or audited `deterministic_only`).
- **Step-4 Discovery Integration (`opendv ontology construct` & MCP `ground_columns`):** Supplies synchronous `<= 64 KB` candidate choices, server-minted `GroundingReceipt`s, and 4-step `GraphAOntology` `OntologyDelta` projections with multi-wave `OverlayKnowledgeSource` continuity.
- **Pluggable Knowledge Sources (`KnowledgeSource` SPI):** Ships with upgraded **FIBO Pack v2** (`2.0.0`, `master_2026Q3`: `2,240` classes, `767` properties, `942` individuals) and a domain-neutral **YAML Glossary** provider (`GlossaryKnowledgeSource`).
- **Build-Time Sync & Runtime Drift Ops (`scripts/vendor_fibo.py` & `opendv ontology fibo-sync`):** Compiles upstream EDM Council FIBO releases into canonical Pack v2 JSON at build time (zero `rdflib` in `src/opendv/`) and audits/repins committed project URIs in pure JSON at runtime.

## Component Documentation Suite (`REQ-DOC-01`)

| Document | Audience | Contents |
| :--- | :--- | :--- |
| **Component README** ([`docs/components/grounding/README.md`](README.md)) | All | Overview, execution modes, and navigation index (this file). |
| **User Guide** ([`user-guide.md`](user-guide.md)) | Operators, Stewards & External Agents | Steps 2–5 placement, workflows, CLI/REST/MCP parameters, glossary YAML schema, calibrated defaults, worked examples with real outputs, PII/receipt safety, & troubleshooting. |
| **Architecture Design** ([`ontology-grounding-engine-and-fibo-pack.md`](../../designs/ontology-grounding-engine-and-fibo-pack.md)) | Architects & Reviewers | Two-module DAG boundaries, End-to-End Stages `0–6`, `KnowledgeSource` SPI, Logit-Normal calibration equations, held-out metrics, `GraphAOntology` projection rules, & risk mitigations. |
| **Contributor Guide** ([`contributor-guide.md`](contributor-guide.md)) | Maintainers & Developers | Layer-2a `V2_LAYER_BANS` import contract, module-by-module map (`22` code/asset files), `< 5,000`-token `D16` rules, extension recipes, & PR verification gates. |
