---
name: opendv-modeling
description: >-
  Data-informed Data Vault 2.0 modeling workflow for external coding agents:
  inspect schemas and constraints via opendv model spec, profile sources and
  paginate P1-P4 survey questions via opendv model profile, conduct a targeted
  user interview, verify DraftVaultIR deterministically with zero internal LLM
  keys (Mode 1 opendv model verify) or run managed CEGAR (Mode 2 opendv model
  run), and iterate on remediation_hints until CERTIFIED_FAST or CERTIFIED.
---

# Open-DV Modeling Skill (`opendv model`)

Use this 5-step workflow to author, verify, and certify a Data Vault 2.0 schema (`VaultSchemaIR v2.0`) from physical sources without creating `.opendv/` project state or writing raw SQL DDL/DML.

> **CLI Flag Contract:** Use only flags declared in `schemas/v2/cli-spec.json` (`--json` is a global flag placed before `model`). Mode 1 (`opendv model verify`) requires zero internal LLM API keys; Mode 2 (`opendv model run`) executes Open-DV's managed CEGAR loop.

---

## Step 1: Fetch Annotated Schemas, Constraints & Guide (`model spec`)

Always retrieve the live JSON Schemas, minimal examples, invariants, `dv2-modeling-guide.md`, and `ModelingPreferences` defaults before authoring `req.json` or `draft.json`—never hardcode volatile schema fields from memory.

```bash
uv run opendv --json model spec --part all
```

Fetch individual sections when minimizing context size:

```bash
uv run opendv --json model spec --part schema
uv run opendv --json model spec --part guide
uv run opendv --json model spec --part preferences
```

Key fields in `ModelSpecResp`:
- `schemas`: JSON Schemas for `DraftVaultIR`, `DraftHubSpec`, `DraftLinkSpec`, `DraftSatelliteSpec`, `DraftBridgeSpec`, `ColumnTriageEntry`, `GraphAOntology`, `SourceProfile`, `ProfileBundle`, `ModelingPreferences`, `VerifyDraftResult`, and `DVModelingResult`.
- `constraints`: Non-negotiable rules (`inv_05`, `column_triage`, `pii_isolation`, `valid_v6b_rule_ids`, `policies`, token caps).
- `guide_markdown` & `guide_sha256`: Concise DV 2.0 modeling rules (`<= 2,500` tokens).
- `preferences` & `preferences_schema`: Active Tier-1 `ModelingPreferences` defaults and schema.

---

## Step 2: Profile Sources & Paginate Survey Questions (`model profile`)

Profile one or more structured sources (`sqlite`, `duckdb`, `parquet`, `csv`, `xlsx`, `databricks_uc`) using `--source <kind>:<uri>` (split on the first `:` so Windows paths like `duckdb:C:/data/vault.db` parse cleanly) or `--sources-file`:

```bash
uv run opendv --json model profile \
  --source csv:tests/fixtures/business_vault_hints/erp_customers.csv \
  --source csv:tests/fixtures/business_vault_hints/crm_customers.csv \
  --sample-rows 200 \
  --max-questions 5 \
  --questions-offset 0 \
  --out profile_bundle.json
```

Use `--sources-file` (JSON/YAML list or `{"sources": [...]}`) and paginate through remaining `P1–P4` questions with `--questions-offset N`, or pass `--max-questions 0` to retrieve all remaining questions at once:

```bash
uv run opendv --json model profile \
  --sources-file tests/fixtures/business_vault_hints/manifest.json \
  --max-questions 3 \
  --questions-offset 0 \
  --out profile_bundle.json

uv run opendv --json model profile \
  --sources-file tests/fixtures/business_vault_hints/manifest.json \
  --max-questions 0 \
  --questions-offset 3 \
  --out profile_bundle_remaining.json
```

To skip capturing local HMAC-tokenized sample rows (deferring row-level checks to Step 7), pass `--no-real-row-proving`:

```bash
uv run opendv --json model profile --source csv:data.csv --no-real-row-proving --out profile_bundle.json
```

`ProfileBundle` returns:
- `profiles`: `SourceProfile[]` with `format_shape`, `surrogate_score`, `pk_uniqueness_ratio`, `best_non_surrogate_bk_candidate_col`, `best_non_surrogate_bk_uniqueness_ratio`, and PII-redacted `top_value_frequencies`.
- `pk_containment_overlaps`: Asymmetric FK-to-PK containment overlaps (`overlap_ratio`, `compared_hashes`).
- `source_rows_by_table` & `table_sample_flags`: HMAC-SHA256 `<PII:16hex>`-tokenized rows and `is_complete_table` booleans.
- `questions`, `total_candidates`, `returned_count`, `offset`, `remaining_count`, `by_priority`: Deterministic zero-LLM `P1–P4` survey items.

---

## Step 3: Conduct User Interview on Surfaced `P1–P3` Questions

Review `questions` and `remaining_count` with the user before modeling:
- **P1 (Survivorship Conflicts):** Multi-source tables with overlapping business attributes; displays masked PII summaries (`<MASKED_PII>`, `<NULL>`, `<NON_NULL>`) without leaking raw PII. Ask which source takes precedence (`survivorship_priority`) or whether separate companion Hubs (`companion_hub_with_sal`) are required.
- **P2 (Multi-Hop Analytical Grains):** Directed `child_fk -> parent_pk` 2–4 hop chains (`brg_*` `DraftBridgeSpec` candidates). Confirm whether the path is an analytical reporting grain.
- **P3 (Link Mutability):** `M:1`/`1:N` Links showing mutability signals (`updated_at`, `effective_from`, `status`, `fk_turnover_by_table > 0`, or sample key reassignments). Confirm whether to attach an Effectivity Satellite (`esat_*` + `driving_hub`) or model an immutable `t_lnk_*`.
- **P4 (Structural Ambiguity):** Low-cardinality columns (`auto_ref_table` vs `keep_in_satellite`) and surrogate `id` tables with natural key candidates (`surrogate_key_strategy`).

If `remaining_count > 0`, fetch the next page (`--questions-offset`) or `--max-questions 0` if the user wants to review every candidate. Also confirm any Wave-2 `prior_locks` and `ModelingPreferences`.

---

## Step 4: Author `req.json` + `draft.json` & Verify (`model verify`) or Run (`model run`)

Encode the user's interview answers directly into `GraphAOntology`, `ModelingPreferences`, and `DraftVaultIR` (Mode 1), or into `DVModelingRequest` + `business_context_hints` (Mode 2).

### Non-Negotiable Modeling Rules (`V1–V6` & `V6b`)

1. **`COLUMN_OMISSION_JUSTIFICATION_REQUIRED`:** Every `(table_id, col_name)` across all profiled tables in `ProfileBundle.profiles` MUST appear in `DraftVaultIR.column_triage` with disposition `MODELED`, `SYSTEM_METADATA`, `SYSTEM_SURROGATE_DROPPED`, or `OMITTED_INTENTIONALLY`. Any `OMITTED_INTENTIONALLY` entry MUST include a `rationale` with **at least 15 non-whitespace characters** (shorter rationales or `UNRESOLVED` entries fail `DV-COL-001` / Pydantic validation).
2. **`ANTI_SILO_VIOLATION`:** All active physical tables and Hubs MUST form a **single connected component** across shared canonical Hubs, Links (`lnk_*` / `t_lnk_*` / `hal_*`), or Same-As Links (`sal_*`). Splitting a concept (`concept_uri`) into multiple Hubs without `sal_*` connectivity or leaving an active source isolated fails `V1`.
3. **`INV-05` (Zero Hub Attributes):** `DraftHubSpec.attributes` MUST always be `[]`. Place all descriptive columns in Satellites (`sat_*`, `mas_*`, `sat_*_pii`). `opendv model verify` intercepts non-empty Hub `attributes` and returns structured `REPAIR_DRAFT_IR` diagnostics (`exit 5`).
4. **`DV-AP-009` (Dedicated PII Isolation & Keyspace Discipline):** Every `is_pii=True` column MUST live in a dedicated `sat_kind="PII"` (`sat_*_pii`) Satellite (excluded from `Z4` `information_mart.vw_<hub>_current`), and compatible same-keyspace Hubs should not be arbitrarily split without `sal_*`.

### Mode 1 (Zero Internal LLM Keys): `req.json` + `draft.json` -> `opendv model verify`

Minimal `req.json` (`VerifyDraftRequest`; `profiles`, `source_rows_by_table`, and `table_sample_flags` hydrate automatically from `--profile profile_bundle.json`):

```json
{
  "policy": "balanced",
  "modeling_preferences": {
    "multi_source_hub_strategy": "unified_canonical_when_same_arity",
    "low_cardinality_strategy": "auto_ref_table",
    "link_cardinality_strategy": "prefer_standard_or_mas",
    "esat_on_mutable_links": "auto_when_turnover_known",
    "surrogate_key_strategy": "reject_when_natural_exists",
    "disabled_v6b_blocks": [],
    "clarify_mode": "auto",
    "max_survey_questions": 5
  },
  "ontology": {
    "ontology_version": "1.0.0",
    "concepts": [
      {
        "concept_uri": "urn:opendv:concept:customer",
        "concept_name": "Customer",
        "definition": "Legal customer entity across ERP and CRM.",
        "table_bindings": [
          {
            "source_id": "src_1_erp_customers",
            "table_id": "src_1_erp_customers:erp_customers",
            "col_name": "cust_id"
          }
        ]
      }
    ],
    "relationships": [],
    "enumerations": []
  }
}
```

Minimal `draft.json` (`DraftVaultIR`):

```json
{
  "ir_version": "2.0",
  "project_id": "stateless",
  "ontology_version": "1.0.0",
  "wave_number": 1,
  "hubs": [
    {
      "hub_name": "hub_customer",
      "key_tier": "TIER_2_ENTERPRISE_SOR",
      "business_keys": [
        {
          "table_id": "src_1_erp_customers:erp_customers",
          "col_name": "cust_id",
          "source_id": "src_1_erp_customers",
          "key_tier": "TIER_2_ENTERPRISE_SOR",
          "bkcc_value": "ENTERPRISE",
          "canonical_col_name": "cust_id"
        }
      ],
      "source_bindings": [
        {
          "source_id": "src_1_erp_customers",
          "table_id": "src_1_erp_customers:erp_customers",
          "col_mappings": {
            "cust_id": "cust_id"
          }
        }
      ],
      "attributes": [],
      "survivorship_priority": ["src_1_erp_customers"]
    }
  ],
  "links": [],
  "satellites": [
    {
      "sat_name": "sat_customer_erp",
      "sat_kind": "STANDARD",
      "parent_name": "hub_customer",
      "source_bindings": [
        {
          "source_id": "src_1_erp_customers",
          "table_id": "src_1_erp_customers:erp_customers",
          "col_mappings": {
            "legal_name": "legal_name"
          }
        }
      ],
      "attributes": [
        {
          "name": "legal_name",
          "logical_type": "UTF8_STRING",
          "nullable": false,
          "is_pii": false
        }
      ]
    },
    {
      "sat_name": "sat_customer_erp_pii",
      "sat_kind": "PII",
      "parent_name": "hub_customer",
      "source_bindings": [
        {
          "source_id": "src_1_erp_customers",
          "table_id": "src_1_erp_customers:erp_customers",
          "col_mappings": {
            "email": "email"
          }
        }
      ],
      "attributes": [
        {
          "name": "email",
          "logical_type": "UTF8_STRING",
          "nullable": true,
          "is_pii": true,
          "classification_tag": "EMAIL"
        }
      ]
    }
  ],
  "bridges": [],
  "column_triage": [
    {
      "table_id": "src_1_erp_customers:erp_customers",
      "col_name": "cust_id",
      "disposition": "MODELED",
      "target_ref": "hub_customer"
    },
    {
      "table_id": "src_1_erp_customers:erp_customers",
      "col_name": "legal_name",
      "disposition": "MODELED",
      "target_ref": "sat_customer_erp"
    },
    {
      "table_id": "src_1_erp_customers:erp_customers",
      "col_name": "email",
      "disposition": "MODELED",
      "target_ref": "sat_customer_erp_pii"
    },
    {
      "table_id": "src_1_erp_customers:erp_customers",
      "col_name": "legacy_flag",
      "disposition": "OMITTED_INTENTIONALLY",
      "rationale": "Deprecated legacy migration flag excluded from downstream analytical reporting."
    }
  ],
  "justifications": []
}
```

Validate schemas/PII first (`--dry-run`), then verify with DuckDB runtime proving:

```bash
uv run opendv --json model verify \
  --input req.json \
  --draft-ir draft.json \
  --profile profile_bundle.json \
  --dry-run

uv run opendv --json model verify \
  --input req.json \
  --draft-ir draft.json \
  --profile profile_bundle.json \
  --out verify_result.json
```

### Mode 2 (Managed CEGAR Engine): `req.json` -> `opendv model run`

When running Open-DV's internal `@ship-vault`, `V7`, and `V8` critics, pass a `DVModelingRequest` JSON (`business_context_hints <= 1,500` tokens allowed in Mode 2; forbidden in Mode 1) and optional `--guide`:

```bash
uv run opendv --json model run \
  --input req.json \
  --profile profile_bundle.json \
  --guide .opendv/agents/dv2-modeling-guide.md \
  --out run_result.json
```

---

## Step 5: Inspect `next_action`, Apply `remediation_hints`, & Certify

Check the exit code, `status`, `next_action`, and `remediation_hints` in `verify_result.json` / `run_result.json`:

| Exit Code | `status` | `next_action` | Agent Action |
| :---: | :--- | :--- | :--- |
| **`0`** | `CERTIFIED_FAST` / `CERTIFIED` | `READY_FOR_DOWNSTREAM` | Done (`final_energy_e == 0`). Return `certified_ir` (`CertifiedVaultIR`) and `evolution_lock` (`EvolutionLockSpec`). |
| **`5`** | `UNCERTIFIED_DRAFT` | `REPAIR_DRAFT_IR` | Read `remediation_hints` (`gate`, `rule_id`, `entity_name`, `suggested_fix`) and `verifier_report`, fix `draft.json` (e.g., move Hub `attributes` to a Satellite, complete `column_triage`, isolate PII in `sat_*_pii`, connect silos via Links/`sal_*`, add CDK to `mas_*` on `DV-DYN-004`), and re-run `opendv model verify`. |
| **`5`** | `UNCERTIFIED_DRAFT` | `WAIVE_OR_ADJUST_PREFERENCES` | Either remediate the `V6b` finding, add an audited `AntiPatternJustification` into `justifications` (`draft.json`) or `persisted_waivers` (`req.json`), or adjust non-strict `modeling_preferences` (`disabled_v6b_blocks`) and re-run. |
| **`5`** | `HOLD_CHECKPOINTED` | `ASK_HUMAN_HOLD` | (Mode 2) Present `pending_questions` (`ClarificationQuestion` / `HorizonQuestion`) to the user, populate `resume` (`hold_answers`, `answered_question_ids`, or `revert_to_pre_steer: true`) in `req.json`, and re-run `opendv model run`. |
| **`2`** / **`1`** | `FAILED` | `FAILED` | Fix malformed JSON/flags, guide trust errors (`UNTRUSTED_WORKSPACE_OVERRIDE`), or missing runtime credentials. |
