# Open-DV Modeling Component (`src/opendv/modeling/`)

The **Modeling Component** (`src/opendv/modeling/`, Layer 3 / Step 6) turns profiled source tables (`SourceProfile[]`, PII-tokenized sample rows, and PK/FK containment overlaps) plus business ontology & governance rules into a mathematically verified 5-Zone (`Z0`–`Z4`) Data Vault 2.0 schema (`CertifiedVaultIR`) and immutable wave lock (`EvolutionLockSpec`)—without ever trusting an LLM to write raw SQL DDL or DML.

- **Mode 1 (`opendv model spec | profile | verify`):** Zero-internal-LLM deterministic verification for external coding agents and CI/CD pipelines.
- **Mode 2 (`opendv model run` / `opendv vault build`):** Autonomous CEGAR synthesis (`@ship-vault` + `L1` codemods + `V1–V6b` DuckDB proving + `V7` Semantic Critic + `V8` Horizon Critic with bounded `S6-06` `STEER` / `revert`).

## Component Documentation Suite (`REQ-DOC-01`)

| Document | Audience | Contents |
| :--- | :--- | :--- |
| **Component README** ([`docs/components/modeling/README.md`](README.md)) | All | Overview, execution modes, and navigation index (this file). |
| **User Guide** ([`user-guide.md`](user-guide.md)) | Operators & External Agents | 7-step platform placement, Zones `Z0`–`Z4`, agents, CLI flags, `CegarPolicy` / `ModelingPreferences`, concrete JSON examples, SQL templates, & troubleshooting. |
| **Architecture & Governance Design** ([`dv2-modeling-engine-and-governance.md`](../../designs/dv2-modeling-engine-and-governance.md)) | Architects & Reviewers | End-to-end Stages `0`–`8`, agent role contracts & budgets, DV 2.0 hashing/SQL fixes, alternatives considered, and risk mitigations. |
| **Contributor Guide** ([`contributor-guide.md`](contributor-guide.md)) | Maintainers & Developers | Layer-3 import contract, module-by-module map (`12` modules), `< 5,000`-token `D16` rules, extension recipes, & PR verification gates. |
| **External Agent Skill** ([`SKILL.md`](../../../.agents/skills/opendv-modeling/SKILL.md)) | External Coding Agents | Step-by-step protocol for profiling, interviewing (`P1–P4`), authoring `DraftVaultIR`, and iterating on `remediation_hints`. |
