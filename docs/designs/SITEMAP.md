# `open-dv` EGM Design Sitemap (`docs/designs/SITEMAP.md`)

> Canonical Elephant-Goldfish Model (EGM) design index for `open-dv`.
>
> - **Scratch artifacts (unapproved briefs, WIP drafts, review & drift reports):** Written to `.dev/docs` (`EGM artifact directory: .dev/docs`, gitignored) and never committed prior to human sign-off.
> - **New approved EGM feature specs:** Committed under `docs/designs/<feature>.md` and indexed in this file.
> - **Core `open-dv` numbered specs & UI blueprints:** Maintained in `docs/` (`00`–`33`) and `docs/design/` and indexed below.
> - **Project Elephant context skill:** [`.agents/skills/open-dv-elephant/SKILL.md`](../../.agents/skills/open-dv-elephant/SKILL.md)

## Active Architecture, Constitution & UI System

| Document | Role |
| :--- | :--- |
| [`AGENTS.md`](../../AGENTS.md) | Root agent & contributor constitution: 14 non-negotiable DV 2.0 invariants, monorepo DAG ownership, and verification gates. |
| [`CLAUDE.md`](../../CLAUDE.md) | CLI/Claude Code entrypoint summarizing immutable invariants, token caps, parity rules, and doc order. |
| [`DESIGN.md`](../../DESIGN.md) | Canonical Claude-aligned UI design system (`#faf9f5` cream, `#cc785c` coral, `#181715` dark chrome, `5-Zone` `Z0–Z4` + `information_mart`). |
| [`docs/00-mission-vision-and-functional-requirements.md`](../00-mission-vision-and-functional-requirements.md) | Normative mission, functional requirements (`FR-DOM-*`, `FR-VER-*`, `FR-INT-*`), and traceability rubric. |

## Platform Parity & Ontology Studio Baseline

| Document | Role |
| :--- | :--- |
| [`docs/18-implementation-plan-platform-parity-and-architecture.md`](../18-implementation-plan-platform-parity-and-architecture.md) | Shared `opendv-service` core, 3-transport parity (`opendv`, `opendvctl`, FastAPI), and architectural requirements. |
| [`docs/19-ontology-studio-architecture.md`](../19-ontology-studio-architecture.md) | Ontology Studio CAS, locking, SSE runtime, governance, and workspace model. |
| [`docs/20-ontology-studio-spec.md`](../20-ontology-studio-spec.md) | Normative API, DTO, CLI, and Studio conformance specification. |
| [`docs/24-ontology-studio-engineering-learnings.md`](../24-ontology-studio-engineering-learnings.md) | Hardening learnings (RFC 8785 JCS, Windows/POSIX locks, async SSE, fail-closed governance). |
| [`docs/27-unified-ontology-pipeline-requirements-and-uat.md`](../27-unified-ontology-pipeline-requirements-and-uat.md) | Unified 6-step Wizard + 4-View DV Schema Explorer V1 requirements (`D1`–`D28`). |
| [`docs/28-unified-ontology-pipeline-design-and-plan.md`](../28-unified-ontology-pipeline-design-and-plan.md) | Unified Wizard V1 phased architecture (`< 5,000`-token decomposition `D16`). |

## Active V2 Specifications (Enterprise Vault, Guided Studio & Real-Data UAT)

| Document | Role |
| :--- | :--- |
| [`docs/29-discovery-grounding-and-evolution-scenarios.md`](../29-discovery-grounding-and-evolution-scenarios.md) | Canonical 9-scenario discovery, grounding & multi-wave evolution matrix (`SCEN-01..09`), `Local Only` & parity rules. |
| [`docs/30-enterprise-dv2-modeling-and-loop-engineering.md`](../30-enterprise-dv2-modeling-and-loop-engineering.md) | V2 blueprint: `BKCC` + `sal_*`, 3-Loop CEGAR, zero-SQL `UniversalVaultSchemaIR v2.0`, decoupled `v7`, `V8` Horizon Critic, `.opendv.zip`. |
| [`docs/31-v2-e2e-requirements-gaps-and-uat-spec.md`](../31-v2-e2e-requirements-gaps-and-uat-spec.md) | Consolidated E2E V2 architecture (`Phases 0–9`), `OBS-01..48`, `REQ-01..68`, and `89`-item UAT expected-output checklist. |
| [`docs/32-v2-user-experience-and-scenario-guide.md`](../32-v2-user-experience-and-scenario-guide.md) | Complete Steps 1–7 UX guide (`Core Project State` default `.opendv.zip`, `Guided` vs `Architect`) and `20`-scenario playbook. |
| [`docs/33-v2-phased-implementation-and-real-data-uat-plan.md`](../33-v2-phased-implementation-and-real-data-uat-plan.md) | Master V2 phased TDD implementation & real-data browser UAT plan (`Phases V2-1..V2-9`). |
| [`docs/design/ontology-studio/uat-v2-feedback-and-triage-log.md`](../design/ontology-studio/uat-v2-feedback-and-triage-log.md) | Manual UAT feedback triage ledger driving V2 scope and requirements. |

## EGM Feature Designs (`docs/designs/`)

> New EGM `/egm-draft` specifications promoted from `.dev/docs` will be listed here upon approval.
