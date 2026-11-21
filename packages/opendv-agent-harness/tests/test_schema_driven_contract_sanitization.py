"""TDD tests for generic schema-driven contract sanitization against DV-G1-001."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness import (
    HarnessSetupConfig,
    PipelineInputSpec,
    run_agentic_vault_pipeline,
)
from opendv_agent_harness.pipeline import (
    Stage03ColumnTriageOutput,
    Stage04ContractSynthesisOutput,
)
from opendv_agent_harness.pipeline.axi_repair_loop import (
    _clean_contract_dict,
    execute_axi_repair_loop,
)
from opendv_agent_harness.pipeline.stage_outputs import sanitize_value_against_schema
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    GraphAOntology,
)
from opendv_testkit import DeterministicHarnessAdapter

REPO_ROOT = Path(__file__).resolve().parents[3]


def _sample_dir() -> Path:
    return REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def _minimal_ontology_and_contract_with_extra_annotations() -> tuple[
    GraphAOntology, dict[str, Any], dict[str, Any]
]:
    ontology = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance_test",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:account",
                concept_name="Account",
                elm_archetype="CORE_CONCEPT",
                definition="Canonical financial account entity for schema sanitization tests.",
                target_hub="hub_account",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                concept_name="Customer",
                elm_archetype="CORE_CONCEPT",
                definition="Canonical customer entity for schema sanitization tests.",
                target_hub="hub_customer",
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:account_customer",
                relationship_name="Account Customer",
                target_link="lnk_account_customer",
                endpoints=["urn:ontology:entity:account", "urn:ontology:entity:customer"],
                grain_semantics=(
                    "One ownership relationship linking an account and customer in finance_test."
                ),
            )
        ],
    )
    bronze_profiles = {
        "core_bank.accounts": {
            "account_code": {
                "physical_type": "VARCHAR(32)",
                "non_null_ratio": 1.0,
                "uniqueness_ratio": 1.0,
            },
            "customer_lei": {
                "physical_type": "VARCHAR(32)",
                "non_null_ratio": 1.0,
                "uniqueness_ratio": 0.5,
            },
            "account_status": {
                "physical_type": "VARCHAR(32)",
                "non_null_ratio": 1.0,
                "uniqueness_ratio": 0.1,
            },
        }
    }
    contract = {
        "contract_version": "5.0",
        "domain_namespace": "finance_test",
        "lifecycle_stage": "COLD_START_N1",
        "llm_root_comment": "extra root annotation not allowed by schema",
        "column_triage_ledger": [
            {
                "bronze_table": "core_bank.accounts",
                "column_name": "account_code",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_account",
                "source_sql_type": "VARCHAR(32)",
                "rationale": "Natural account identifier.",
                "confidence": 0.99,
            },
            {
                "bronze_table": "core_bank.accounts",
                "column_name": "customer_lei",
                "elm_classification": "FOREIGN_KEY_REF",
                "target_dv_entity": "lnk_account_customer",
                "source_sql_type": None,
                "explanation": "Links account to owning customer.",
            },
            {
                "bronze_table": "core_bank.accounts",
                "column_name": "account_status",
                "elm_classification": "DESCRIPTIVE_PAYLOAD",
                "target_dv_entity": "sat_account",
                "source_sql_type": "VARCHAR(32)",
                "notes": "Descriptive status attribute.",
            },
        ],
        "hubs": [
            {
                "name": "hub_account",
                "ontology_concept_uri": "urn:ontology:entity:account",
                "rationale": "Core account hub",
                "business_key_sources": [
                    {
                        "bronze_table": "core_bank.accounts",
                        "key_columns": ["account_code"],
                        "record_source_code": "CORE_BANK",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                        "bkcc_column_expr": None,
                        "reasoning": "Global standard account code",
                    }
                ],
            },
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
                "business_key_sources": [
                    {
                        "bronze_table": "core_bank.accounts",
                        "key_columns": ["customer_lei"],
                        "record_source_code": "CORE_BANK",
                        "key_authority_tier": "TIER_1_GLOBAL_STANDARD",
                        "require_src_qualifier": False,
                        "bkcc_prefix": "GLOBAL",
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_account_customer",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:account_customer",
                "source_bronze_table": "core_bank.accounts",
                "driving_key_hub": None,
                "llm_annotation": "Associates account and customer",
                "hub_references": [
                    {
                        "target_hub": "hub_account",
                        "role_alias": "account",
                        "source_fk_columns": ["account_code"],
                        "extra_ref_note": "Primary hub ref",
                    },
                    {
                        "target_hub": "hub_customer",
                        "role_alias": "customer",
                        "source_fk_columns": ["customer_lei"],
                    },
                ],
            }
        ],
        "satellites": [
            {
                "name": "sat_account",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_account",
                "source_bronze_table": "core_bank.accounts",
                "security_classification": "INTERNAL",
                "volatility_tier": "STANDARD",
                "payload_columns": ["account_status"],
                "soft_deprecated_columns": [],
                "extra_sat_comment": "Standard satellite",
            }
        ],
        "reference_tables": [],
        "unmapped_escalations": [],
    }
    return ontology, bronze_profiles, contract


def test_clean_contract_dict_strips_extra_keys_across_nested_objects_and_unblocks_gate1() -> None:
    """Extra LLM annotation keys on any additionalProperties=false object must be stripped
    generically so DV-G1-001 passes without hardcoding key names.
    """
    ontology, bronze_profiles, dirty_contract = (
        _minimal_ontology_and_contract_with_extra_annotations()
    )

    cleaned = _clean_contract_dict(dirty_contract)
    assert cleaned is not None
    assert "llm_root_comment" not in cleaned
    assert "rationale" not in cleaned["column_triage_ledger"][0]
    assert "confidence" not in cleaned["column_triage_ledger"][0]
    assert "explanation" not in cleaned["column_triage_ledger"][1]
    assert "notes" not in cleaned["column_triage_ledger"][2]
    assert "rationale" not in cleaned["hubs"][0]
    assert "reasoning" not in cleaned["hubs"][0]["business_key_sources"][0]
    assert cleaned["hubs"][0]["business_key_sources"][0]["bkcc_column_expr"] is None
    assert "llm_annotation" not in cleaned["links"][0]
    assert "driving_key_hub" not in cleaned["links"][0]
    assert cleaned["links"][0]["degenerate_columns"] == []
    assert "extra_ref_note" not in cleaned["links"][0]["hub_references"][0]
    assert "extra_sat_comment" not in cleaned["satellites"][0]

    _verified_contract, axi_res, is_verified = execute_axi_repair_loop(
        candidate_contract=dirty_contract,
        ontology=ontology,
        bronze_profiles=bronze_profiles,
        run_id="run-tdd-schema-strip",
    )
    assert not any("DV-G1-001" in d or "SCHEMA_ERROR" in d for d in axi_res.diagnostics), (
        f"Unexpected Gate 1 schema errors: {axi_res.diagnostics}"
    )
    assert is_verified is True


def test_stage03_and_stage04_llm_annotations_do_not_trigger_dv_g1_001_end_to_end(
    tmp_path: Path,
) -> None:
    """Real LLM Stage 03/04 outputs with extra keys like 'rationale' and 'explanation'
    must survive merge and pass STAGE_05_AXI_DV_STATIC_GATES without DV-G1-001.
    """
    sample_dir = _sample_dir()
    input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
    wave0_spec = input_spec.model_copy(update={"waves": [input_spec.waves[0]]})

    def _annotated_structured_output_factory(spec: Any) -> Any:
        stage_val = spec.stage_id.value
        if stage_val == "STAGE_03_SHIP_VAULT_PASS1":
            return Stage03ColumnTriageOutput.model_validate(
                {
                    "column_triage_ledger": [
                        {
                            "bronze_table": "crm_salesforce.crm_customers",
                            "column_name": "customer_tax_id",
                            "elm_classification": "BUSINESS_KEY",
                            "target_dv_entity": "hub_customer",
                            "source_sql_type": "VARCHAR(64)",
                            "rationale": (
                                "Tax ID is the canonical natural business key for hub_customer."
                            ),
                            "confidence": 0.98,
                        }
                    ]
                }
            )
        if stage_val == "STAGE_04_SHIP_VAULT_PASS2":
            from opendv_agent_harness.pipeline.stage_prompt_builder import (
                synthesize_stage_output_from_prompt,
            )

            base_out = synthesize_stage_output_from_prompt(
                Stage04ContractSynthesisOutput, spec.prompt
            )
            contract = dict(base_out.contract)
            contract["llm_synthesis_notes"] = "Synthesized from pass1 ledger."
            for entry in contract.get("column_triage_ledger", []):
                entry["rationale"] = "Verified column triage rationale."
            for hub in contract.get("hubs", []):
                hub["explanation"] = "Canonical hub."
                for bks in hub.get("business_key_sources", []):
                    bks["rationale"] = "BK authority rationale."
            for lnk in contract.get("links", []):
                lnk["rationale"] = "Relationship link."
                for ref in lnk.get("hub_references", []):
                    ref["explanation"] = "Hub reference."
            for sat in contract.get("satellites", []):
                sat["rationale"] = "Descriptive attributes."
            return Stage04ContractSynthesisOutput(contract=contract)
        return None

    setup = HarnessSetupConfig(
        provider="agy",
        steward_hold_policy="auto_approve_top_candidate",
        stage_output_policy="symbolic_completion",
    )
    out_dir = tmp_path / "out_annotated_llm"
    injected_harness = DeterministicHarnessAdapter(
        setup.to_harness_config(
            workspace_dir=tmp_path,
            state_dir=out_dir / "state",
            app_data_dir=out_dir / "app_data",
            save_dir=out_dir / "save",
        ),
        structured_output_factory=_annotated_structured_output_factory,
    )
    rep = run_agentic_vault_pipeline(
        wave0_spec,
        setup,
        output_dir=out_dir,
        base_dir=sample_dir,
        harness=injected_harness,
    )
    assert rep.status == "COMPLETED", f"Pipeline failed: {rep.step_reports}"
    assert rep.overall_passed is True
    attempt1 = yaml.safe_load(
        (out_dir / "waves" / "wave_0" / "wave_0_contract_attempt_1.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert all("rationale" not in e for e in attempt1.get("column_triage_ledger", []))
    final_contract = yaml.safe_load((out_dir / "final_contract.yaml").read_text(encoding="utf-8"))
    assert "llm_synthesis_notes" not in final_contract
    assert all("rationale" not in e for e in final_contract.get("column_triage_ledger", []))


def test_generic_schema_sanitizer_supports_refs_unions_and_preserves_object_maps() -> None:
    """Verify recursive sanitization handles $ref/$defs, oneOf/anyOf/allOf, and object maps."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {
            "BaseMeta": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "StrictVariantA": {
                "allOf": [
                    {"$ref": "#/$defs/BaseMeta"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"const": "A"},
                            "value_a": {"type": "string"},
                        },
                        "required": ["kind", "value_a"],
                    },
                ]
            },
            "StrictVariantB": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "B"},
                    "value_b": {"type": "integer"},
                },
                "required": ["kind", "value_b"],
            },
        },
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"$ref": "#/$defs/StrictVariantA"},
                        {"$ref": "#/$defs/StrictVariantB"},
                    ]
                },
            },
            "open_map": {
                "type": "object",
                "additionalProperties": True,
            },
            "typed_map": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"label": {"type": "string"}},
                },
            },
            "nullable_ref": {
                "anyOf": [
                    {"$ref": "#/$defs/StrictVariantB"},
                    {"type": "null"},
                ]
            },
        },
    }
    payload = {
        "entries": [
            {"kind": "A", "id": "1", "value_a": "hello", "rationale": "drop me"},
            {"kind": "B", "value_b": 42, "explanation": "drop me too"},
        ],
        "open_map": {"arbitrary_key": "keep me", "nested": {"k": 1}},
        "typed_map": {
            "tag1": {"label": "ok", "extra": "drop"},
        },
        "nullable_ref": {"kind": "B", "value_b": 7, "notes": "drop"},
        "unexpected_root": "drop",
    }
    cleaned = sanitize_value_against_schema(payload, schema, schema)
    assert cleaned == {
        "entries": [
            {"kind": "A", "id": "1", "value_a": "hello"},
            {"kind": "B", "value_b": 42},
        ],
        "open_map": {"arbitrary_key": "keep me", "nested": {"k": 1}},
        "typed_map": {"tag1": {"label": "ok"}},
        "nullable_ref": {"kind": "B", "value_b": 7},
    }
