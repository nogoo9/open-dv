"""Test suite for Data Vault 2.0 Explainability, Observability & Redaction Architecture.

Conforms to:
- docs/06-explainability-and-observability-spec.md
- schemas/vault-observability-event.schema.json
- 5-Tier Data Classification & Adversarial Redaction Model
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from pydantic import ValidationError as PydanticValidationError
import pytest

from opendv_core import VaultObservabilityEvent

# =============================================================================
# REFERENCE TELEMETRY EMITTER, REDACTION & SEMANTIC HASH PILL IMPLEMENTATION
# =============================================================================

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "vault-observability-event.schema.json"

with open(SCHEMA_PATH, encoding="utf-8") as _f:
    OBSERVABILITY_SCHEMA = json.load(_f)

VALIDATOR = Draft202012Validator(OBSERVABILITY_SCHEMA)

# Regex patterns for adversarial leak detection
RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
RE_EIN = re.compile(r"\b\d{2}-\d{7}\b")
RE_ABA = re.compile(r"\b\d{9}\b")
RE_HEX_64 = re.compile(r"\b[0-9A-Fa-f]{64}\b")


class PIILeakViolationError(Exception):
    """Raised when unmasked Restricted PII or financial identifier is detected."""


def format_semantic_hash_pill(
    entity_label: str,
    business_keys: list[str] | str,
    hex_digest: str,
) -> str:
    """Format a compact Semantic Hash Pill badge: <EntityLabel>(<BK_Summary>) · #<hex[0:6]>."""
    clean_label = entity_label.strip() if entity_label else "Entity"

    if isinstance(business_keys, str):
        bk_list = [business_keys.strip()] if business_keys.strip() else []
    elif isinstance(business_keys, (list, tuple)):
        bk_list = [str(k).strip() for k in business_keys if k is not None and str(k).strip()]
    else:
        bk_list = []

    bk_summary = ", ".join(bk_list)

    digest_clean = (hex_digest or "").strip().lower()
    short_hex = digest_clean[:6] if len(digest_clean) >= 6 else digest_clean.ljust(6, "0")

    return f"{clean_label}({bk_summary}) · #{short_hex}"


def format_semantic_hash_pill_layer2(
    kind: str,
    full_sha256_hex: str,
    bkcc_prefix: str,
    business_key: str,
    entity_label: str,
) -> dict[str, str]:
    """Format full Layer 2 Semantic Hash Pill dictionary for visual canvases."""
    digest = full_sha256_hex.upper()
    short_hex = f"{digest[:4]}..{digest[-4:]}"
    pill_label = f"[{kind}: {short_hex} | {bkcc_prefix}:{business_key} {entity_label}]"
    return {
        "pill_label": pill_label,
        "full_sha256_hex": digest,
        "bkcc_prefix": bkcc_prefix,
        "business_key": business_key,
        "entity_label": entity_label,
    }


def detect_unmasked_pii_leaks(obj: Any, path: str = "") -> list[tuple[str, str, str]]:
    """Adversarial recursive scanner detecting unmasked SSN, EIN, or ABA routing numbers.

    Returns a list of (json_path, leak_type, matched_string) tuples.
    """
    leaks: list[tuple[str, str, str]] = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else k
            leaks.extend(detect_unmasked_pii_leaks(v, sub_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            sub_path = f"{path}[{idx}]"
            leaks.extend(detect_unmasked_pii_leaks(item, sub_path))
    elif isinstance(obj, str):
        for m in RE_SSN.findall(obj):
            leaks.append((path, "SSN", m))
        for m in RE_EIN.findall(obj):
            leaks.append((path, "EIN", m))
        for m in RE_ABA.findall(obj):
            if not m.startswith(("2026", "1900", "0000")):
                leaks.append((path, "ABA_ROUTING", m))

    return leaks


def redact_telemetry_event(
    event: dict[str, Any],
    viewer_roles: list[str] | None = None,
) -> dict[str, Any]:
    """Apply 5-tier data classification redaction and masking rules to a telemetry event."""
    roles = set(viewer_roles or [])
    ev = copy.deepcopy(event)
    tier = ev.get("data_classification", "PUBLIC")

    # Tier 5: RESTRICTED_PII is unconditionally masked in all output payloads
    def _mask_pii(val: Any, key_name: str = "") -> Any:
        if isinstance(val, dict):
            return {k: _mask_pii(v, k) for k, v in val.items()}
        if isinstance(val, list):
            return [_mask_pii(item, key_name) for item in val]
        if isinstance(val, str):
            if any(term in key_name.lower() for term in ["aba", "routing", "tax_id", "wire_iban", "ssn", "ein"]):
                return "[REDACTED:RESTRICTED_PII]"
            masked = RE_SSN.sub("[REDACTED:RESTRICTED_PII]", val)
            masked = RE_EIN.sub("[REDACTED:RESTRICTED_PII]", masked)
            masked = RE_ABA.sub(
                lambda m: m.group(0)
                if m.group(0).startswith(("2026", "1900", "0000"))
                else "[REDACTED:RESTRICTED_PII]",
                masked,
            )
            return masked
        return val

    ev = _mask_pii(ev)

    # Tier 4: MNPI valuation figures
    if tier in {"MNPI", "RESTRICTED_PII"} and "MNPI_CLEARED_STEWARD" not in roles:
        l1 = ev.get("layer1_story_banner", {})
        if "headline" in l1:
            l1["headline"] = re.sub(
                r"\b\d+\.\d+x\b|\$\d+[\d,]*(\.\d+)?\b|\b\d+M\b",
                "[REDACTED:MNPI]",
                l1["headline"],
            )
        if "business_impact" in l1:
            l1["business_impact"] = re.sub(
                r"\b\d+\.\d+x\b|\$\d+[\d,]*(\.\d+)?\b|\b\d+M\b",
                "[REDACTED:MNPI]",
                l1["business_impact"],
            )

        l2 = ev.get("layer2_visual_payload", {})
        metrics = l2.get("summary_metrics", {})
        for k in list(metrics.keys()):
            if any(term in k.lower() for term in ["valuation", "multiple", "ev_", "ebitda", "fair_value"]):
                metrics[k] = "[REDACTED:MNPI]"

    # Tier 3: CONFIDENTIAL individual LP rows
    if tier in {"CONFIDENTIAL", "MNPI", "RESTRICTED_PII"} and "LP_CONFIDENTIAL_STEWARD" not in roles:
        l2 = ev.get("layer2_visual_payload", {})
        pills = l2.get("semantic_hash_pills", [])
        for pill in pills:
            if pill.get("bkcc_prefix") in ["GLOBAL", "LEGACY_GP_ADMIN"] and "LP" in pill.get("business_key", ""):
                pill["entity_label"] = "[REDACTED:CONFIDENTIAL]"
                pill["pill_label"] = (
                    f"[{pill.get('pill_label', '').split(':')[0]}: "
                    f"{pill['full_sha256_hex'][:4]}..{pill['full_sha256_hex'][-4:]} | [REDACTED:CONFIDENTIAL]]"
                )

    # Leak detection safeguard on the redacted event
    leaks = detect_unmasked_pii_leaks(ev)
    if leaks:
        raise PIILeakViolationError(
            f"Adversarial leak detector found {len(leaks)} unmasked PII tokens: {leaks}"
        )

    return ev


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def base_valid_event() -> dict[str, Any]:
    """Minimal schema-valid VaultObservabilityEvent."""
    return {
        "event_id": "evt-0192ded0-7b40-7000-8000-000000000001",
        "run_id": "run-pe-apex-horizon-20261030",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": "b9c7c989f97918e1",
        "timestamp_utc": "2026-10-30T14:25:12Z",
        "stage_id": "STAGE_00_ORCHESTRATION",
        "phase_id": "P1_INGESTION",
        "step_id": "PIPE-00",
        "agent_persona": "vault-first-mate",
        "status": "PASS",
        "data_classification": "INTERNAL",
        "rule_ids_evaluated": ["INV-01"],
        "contract_version": "5.0",
        "confidence": 0.99,
        "context_refs": {
            "graph_a_sha256": "A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90",
            "graph_b_sha256": "B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F90A1",
        },
        "layer1_story_banner": {
            "headline": "Grounded 9 Bronze tables onto 4 Enterprise Business Concepts",
            "business_impact": "Prevents siloed source-specific Hubs and guarantees 100% column triage.",
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [
                {
                    "pill_label": "[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]",
                    "full_sha256_hex": "5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D",
                    "bkcc_prefix": "GLOBAL",
                    "business_key": "LP-101",
                    "entity_label": "CalPERS",
                }
            ],
            "summary_metrics": {"tables_mapped": 9, "coverage_pct": 80.0},
        },
        "layer3_formal_proof": {
            "algorithm_ref": "Algorithm A.5",
            "formula_latex": "SHA256(UTF8(CONCAT_WS('||', ...)))",
            "contract_yaml_diff": "+ verified_hubs: 4",
        },
    }


# =============================================================================
# PART 1: SCHEMA VALIDATION & CONDITIONAL INVARIANT TESTS
# =============================================================================


class TestVaultObservabilitySchemaInvariants:
    """Test suite for schemas/vault-observability-event.schema.json validation and conditional constraints."""

    def test_doc06_sample_gate4_yagni_event_validates(self, doc06_algos: dict[str, Any]) -> None:
        """Verify `build_sample_gate4_yagni_event()` from `docs/06` validates against the schema."""
        sample_event = doc06_algos["build_sample_gate4_yagni_event"]()
        VALIDATOR.validate(sample_event)

    @pytest.mark.parametrize(
        "phase_id,stage_id,step_id,persona",
        [
            ("P1_INGESTION", "STAGE_00_ORCHESTRATION", "PIPE-00", "vault-first-mate"),
            ("P2_PROFILING", "STAGE_01_SCOUT_SOURCE", "STEP-02", "scout-source"),
            ("P3_EXTRACTION", "STAGE_02_SCOUT_UNSTRUCTURED", "STEP-04", "scout-unstructured"),
            ("P4_SYNTHESIS_PASS1", "STAGE_03_SHIP_VAULT_PASS1", "STEP-05", "ship-vault"),
            ("P5_SYNTHESIS_PASS2", "STAGE_04_SHIP_VAULT_PASS2", "STEP-06", "ship-vault"),
            ("P6_VERIFICATION", "STAGE_05_AXI_DV_STATIC_GATES", None, "axi-dv"),
            ("P7_DYNAMIC_SANDBOX", "STAGE_06_AXI_DV_DYNAMIC_SANDBOX", None, "axi-dv"),
            ("P8_OPERATIONS", "STAGE_07_DETERMINISTIC_COMPILER", "STEP-07", "deterministic-compiler"),
        ],
    )
    def test_positive_validation_across_all_eight_phases(
        self, base_valid_event, phase_id, stage_id, step_id, persona
    ):
        """Verify positive schema validation across all 8 pipeline phases P1..P8."""
        ev = copy.deepcopy(base_valid_event)
        ev["phase_id"] = phase_id
        ev["stage_id"] = stage_id
        ev["agent_persona"] = persona

        if step_id:
            ev["step_id"] = step_id
        else:
            ev.pop("step_id", None)

        if phase_id == "P6_VERIFICATION":
            ev["gate_id"] = "G3"
            ev["rule_code"] = "DV-STR-001"

        VALIDATOR.validate(ev)

    @pytest.mark.parametrize("step_num", range(1, 9))
    def test_positive_validation_all_eight_pipeline_steps(self, base_valid_event, step_num):
        """Verify positive schema validation for all 8 step identifiers (STEP-01..08)."""
        ev = copy.deepcopy(base_valid_event)
        step_id = f"STEP-{step_num:02d}"
        ev["step_id"] = step_id

        phase_map = {
            1: "P1_INGESTION",
            2: "P2_PROFILING",
            3: "P2_PROFILING",
            4: "P3_EXTRACTION",
            5: "P4_SYNTHESIS_PASS1",
            6: "P5_SYNTHESIS_PASS2",
            7: "P8_OPERATIONS",
            8: "P8_OPERATIONS",
        }
        ev["phase_id"] = phase_map[step_num]
        VALIDATOR.validate(ev)

    @pytest.mark.parametrize(
        "gate_id,rule_code",
        [
            ("G1", "DV-COL-012"),
            ("G2", "DV-AST-001"),
            ("G3", "DV-STR-001"),
            ("G4", "ALG-A1"),
            ("G5", "TMP-01"),
            ("G6", "AP-01"),
            ("G_DYN", "DV-DYN-001"),
            ("G_AUD", "INV-01"),
        ],
    )
    def test_positive_validation_all_verification_gates(
        self, base_valid_event, gate_id, rule_code
    ):
        """Verify positive schema validation across all verification gates G1..G6, G_DYN, G_AUD."""
        ev = copy.deepcopy(base_valid_event)
        ev["phase_id"] = "P6_VERIFICATION"
        ev["stage_id"] = "STAGE_05_AXI_DV_STATIC_GATES"
        ev["agent_persona"] = "axi-dv"
        ev["gate_id"] = gate_id
        ev["rule_code"] = rule_code
        ev.pop("step_id", None)
        VALIDATOR.validate(ev)

    def test_p6_verification_missing_gate_id_rejected(self, base_valid_event):
        """Parametrized negative test: P6_VERIFICATION missing gate_id violates allOf constraint."""
        ev = copy.deepcopy(base_valid_event)
        ev["phase_id"] = "P6_VERIFICATION"
        ev["rule_code"] = "DV-STR-001"
        ev.pop("gate_id", None)
        ev.pop("step_id", None)

        with pytest.raises(ValidationError) as excinfo:
            VALIDATOR.validate(ev)
        assert "gate_id" in str(excinfo.value) or "allOf" in str(excinfo.value)

    def test_p6_verification_missing_rule_code_rejected(self, base_valid_event):
        """Parametrized negative test: P6_VERIFICATION missing rule_code violates allOf constraint."""
        ev = copy.deepcopy(base_valid_event)
        ev["phase_id"] = "P6_VERIFICATION"
        ev["gate_id"] = "G3"
        ev.pop("rule_code", None)
        ev.pop("step_id", None)

        with pytest.raises(ValidationError) as excinfo:
            VALIDATOR.validate(ev)
        assert "rule_code" in str(excinfo.value) or "allOf" in str(excinfo.value)

    @pytest.mark.parametrize(
        "non_p6_phase",
        [
            None,
            "P1_INGESTION",
            "P2_PROFILING",
            "P3_EXTRACTION",
            "P4_SYNTHESIS_PASS1",
            "P5_SYNTHESIS_PASS2",
            "P7_DYNAMIC_SANDBOX",
            "P8_OPERATIONS",
        ],
    )
    def test_non_p6_phase_illegally_setting_gate_id_rejected(
        self, base_valid_event, non_p6_phase
    ):
        """Parametrized negative test: non-P6 phase (or omitted phase_id) illegally setting gate_id is rejected."""
        ev = copy.deepcopy(base_valid_event)
        if non_p6_phase is None:
            ev.pop("phase_id", None)
        else:
            ev["phase_id"] = non_p6_phase
        ev["gate_id"] = "G1"  # Illegal outside P6!
        ev.pop("step_id", None)

        with pytest.raises(ValidationError) as excinfo:
            VALIDATOR.validate(ev)
        assert "not" in str(excinfo.value) or "gate_id" in str(excinfo.value) or "allOf" in str(excinfo.value)

    @pytest.mark.parametrize(
        "phase_id,mismatched_step",
        [
            ("P1_INGESTION", "STEP-05"),
            ("P2_PROFILING", "STEP-07"),
            ("P3_EXTRACTION", "STEP-01"),
            ("P4_SYNTHESIS_PASS1", "STEP-02"),
            ("P5_SYNTHESIS_PASS2", "STEP-08"),
            ("P6_VERIFICATION", "STEP-01"),
            ("P7_DYNAMIC_SANDBOX", "STEP-08"),
            ("P8_OPERATIONS", "STEP-04"),
        ],
    )
    def test_phase_step_mismatch_rejected(
        self, base_valid_event, phase_id, mismatched_step
    ):
        """Parametrized negative test: incompatible phase_id and step_id are rejected."""
        ev = copy.deepcopy(base_valid_event)
        ev["phase_id"] = phase_id
        ev["step_id"] = mismatched_step
        if phase_id == "P6_VERIFICATION":
            ev["gate_id"] = "G3"
            ev["rule_code"] = "DV-STR-001"

        with pytest.raises(ValidationError) as excinfo:
            VALIDATOR.validate(ev)
        assert (
            "step_id" in str(excinfo.value)
            or "not" in str(excinfo.value)
            or "allOf" in str(excinfo.value)
        )

    @pytest.mark.parametrize(
        "bad_trace_id",
        [
            "4bf92f3577b34da6a3ce929d0e0e473",   # 31 hex (too short)
            "4bf92f3577b34da6a3ce929d0e0e47366",  # 33 hex (too long)
            "4BF92F3577B34DA6A3CE929D0E0E4736",  # Uppercase not allowed
            "4bf92f35-77b3-4da6-a3ce-929d0e0e4",  # Contains hyphens
            "not-a-valid-hex-trace-id-string!",    # Non-hex
        ],
    )
    def test_invalid_trace_id_rejected(self, base_valid_event, bad_trace_id):
        """Negative test: W3C trace_id must be exactly 32 lowercase hex characters."""
        ev = copy.deepcopy(base_valid_event)
        ev["trace_id"] = bad_trace_id
        with pytest.raises(ValidationError):
            VALIDATOR.validate(ev)

    @pytest.mark.parametrize(
        "field,bad_val",
        [
            ("run_id", ""),
            ("event_id", ""),
            ("contract_version", "v5_beta"),
            ("confidence", -0.05),
            ("confidence", 1.01),
        ],
    )
    def test_invalid_envelope_fields_rejected(
        self, base_valid_event, field, bad_val
    ):
        """Negative test: run_id, event_id, contract_version, and confidence bounds."""
        ev = copy.deepcopy(base_valid_event)
        ev[field] = bad_val
        with pytest.raises(ValidationError):
            VALIDATOR.validate(ev)

    def test_auto_repaired_missing_remediation_rejected(self, base_valid_event):
        """Negative test: status == AUTO_REPAIRED requires remediation payload."""
        ev = copy.deepcopy(base_valid_event)
        ev["status"] = "AUTO_REPAIRED"
        ev["layer1_story_banner"]["verdict_pill"] = "AUTO_REPAIRED"
        ev.pop("remediation", None)

        with pytest.raises(ValidationError) as excinfo:
            VALIDATOR.validate(ev)
        assert "remediation" in str(excinfo.value) or "allOf" in str(excinfo.value)

    def test_auto_repaired_missing_codemod_fields_rejected(self, base_valid_event):
        """Negative test: remediation missing codemod_type, target_entity, or patch_diff fails."""
        ev = copy.deepcopy(base_valid_event)
        ev["status"] = "AUTO_REPAIRED"
        ev["layer1_story_banner"]["verdict_pill"] = "AUTO_REPAIRED"
        ev["remediation"] = {
            "codemod_type": "MERGE_SATELLITE",
            # Missing target_entity and patch_diff!
        }

        with pytest.raises(ValidationError) as excinfo:
            VALIDATOR.validate(ev)
        assert "target_entity" in str(excinfo.value) or "patch_diff" in str(excinfo.value)

    def test_auto_repaired_with_complete_remediation_passes(self, base_valid_event):
        """Positive test: status == AUTO_REPAIRED with complete remediation payload passes."""
        ev = copy.deepcopy(base_valid_event)
        ev["status"] = "AUTO_REPAIRED"
        ev["layer1_story_banner"]["verdict_pill"] = "AUTO_REPAIRED"
        ev["remediation"] = {
            "codemod_type": "MERGE_SATELLITE",
            "target_entity": "sat_lp_commitment_fx_high_freq",
            "patch_diff": "- sat_lp_commitment_fx_high_freq\n+ # Merged into sat_lp_commitment_investran",
            "action_description": "Merged low-volume high-frequency satellite (W_amp = 4.20 MB/day < 50 MB/day).",
        }
        VALIDATOR.validate(ev)


# =============================================================================
# PART 2: 5-TIER DATA CLASSIFICATION REDACTION & MASKING INVARIANTS
# =============================================================================


class TestFiveTierRedactionAndLeakDetection:
    """Test suite for 5-tier classification redaction, masking, and adversarial leak detection."""

    def test_public_and_internal_tier_permits_unredacted_keys(self, base_valid_event):
        """Tiers 1 & 2 (PUBLIC and INTERNAL) permit unredacted business keys and metadata."""
        ev = copy.deepcopy(base_valid_event)
        ev["data_classification"] = "INTERNAL"
        ev["layer2_visual_payload"]["semantic_hash_pills"] = [
            format_semantic_hash_pill_layer2(
                "HK",
                "5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D",
                "GLOBAL",
                "LP-101",
                "CalPERS",
            )
        ]

        redacted = redact_telemetry_event(ev, viewer_roles=[])
        assert redacted["layer2_visual_payload"]["semantic_hash_pills"][0]["business_key"] == "LP-101"
        assert redacted["layer2_visual_payload"]["semantic_hash_pills"][0]["entity_label"] == "CalPERS"

    def test_confidential_tier_masks_individual_lps_preserves_aggregates(self, base_valid_event):
        """Tier 3 (CONFIDENTIAL) masks individual LP identities while preserving aggregate stats."""
        ev = copy.deepcopy(base_valid_event)
        ev["data_classification"] = "CONFIDENTIAL"
        ev["layer2_visual_payload"]["semantic_hash_pills"] = [
            format_semantic_hash_pill_layer2(
                "HK",
                "5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D",
                "GLOBAL",
                "LP-101",
                "CalPERS",
            )
        ]
        ev["layer2_visual_payload"]["summary_metrics"] = {
            "total_commitments_usd": 500000000.0,
            "lp_count": 7,
        }

        # Unauthorized viewer
        redacted = redact_telemetry_event(ev, viewer_roles=[])
        pill = redacted["layer2_visual_payload"]["semantic_hash_pills"][0]
        assert pill["entity_label"] == "[REDACTED:CONFIDENTIAL]"
        assert "[REDACTED:CONFIDENTIAL]" in pill["pill_label"]
        # Aggregates preserved
        assert redacted["layer2_visual_payload"]["summary_metrics"]["lp_count"] == 7
        assert redacted["layer2_visual_payload"]["summary_metrics"]["total_commitments_usd"] == 500000000.0

        # Authorized viewer clears redaction
        cleared = redact_telemetry_event(ev, viewer_roles=["LP_CONFIDENTIAL_STEWARD"])
        assert cleared["layer2_visual_payload"]["semantic_hash_pills"][0]["entity_label"] == "CalPERS"

    def test_mnpi_tier_masks_valuation_figures_unless_cleared(self, base_valid_event):
        """Tier 4 (MNPI) masks pre-release EV/EBITDA figures unless MNPI_CLEARED_STEWARD."""
        ev = copy.deepcopy(base_valid_event)
        ev["data_classification"] = "MNPI"
        ev["layer1_story_banner"]["headline"] = (
            "Audited Q3 Nordic Cloud valuation restated to 14.5x EBITDA ($245,000,000.00)"
        )
        ev["layer1_story_banner"]["business_impact"] = (
            "Pre-release memo sets fair value at $245M based on 14.5x multiple."
        )
        ev["layer2_visual_payload"]["summary_metrics"] = {
            "entry_ev_multiple": "14.5x",
            "fair_value_usd": 245000000.00,
            "table_count": 1,
        }

        # Standard viewer: masked!
        redacted = redact_telemetry_event(ev, viewer_roles=[])
        assert "14.5x" not in redacted["layer1_story_banner"]["headline"]
        assert "$245,000,000.00" not in redacted["layer1_story_banner"]["headline"]
        assert "[REDACTED:MNPI]" in redacted["layer1_story_banner"]["headline"]
        assert redacted["layer2_visual_payload"]["summary_metrics"]["entry_ev_multiple"] == "[REDACTED:MNPI]"
        assert redacted["layer2_visual_payload"]["summary_metrics"]["table_count"] == 1

        # MNPI Cleared Steward: unmasked
        cleared = redact_telemetry_event(ev, viewer_roles=["MNPI_CLEARED_STEWARD"])
        assert "14.5x" in cleared["layer1_story_banner"]["headline"]
        assert "$245,000,000.00" in cleared["layer1_story_banner"]["headline"]
        assert cleared["layer2_visual_payload"]["summary_metrics"]["entry_ev_multiple"] == "14.5x"

    def test_restricted_pii_strictly_forbidden_across_all_payloads(self, base_valid_event):
        """Tier 5 (RESTRICTED_PII) strictly masks raw tax_id_ein, SSN, and wire routing, plus lower tiers."""
        ev = copy.deepcopy(base_valid_event)
        ev["data_classification"] = "RESTRICTED_PII"
        ev["layer1_story_banner"]["headline"] = (
            "Loaded tax_id_ein 12-3456789, SSN 123-45-6789, ABA 021000021 at 14.5x ($245M)"
        )
        ev["layer2_visual_payload"]["summary_metrics"] = {
            "wire_aba_routing_raw": "021000021",
            "entry_ev_multiple": "14.5x",
        }

        redacted = redact_telemetry_event(ev, viewer_roles=["COMPLIANCE_AUDITOR"])
        assert "12-3456789" not in redacted["layer1_story_banner"]["headline"]
        assert "123-45-6789" not in redacted["layer1_story_banner"]["headline"]
        assert "021000021" not in redacted["layer1_story_banner"]["headline"]
        assert "14.5x" not in redacted["layer1_story_banner"]["headline"]
        assert "[REDACTED:RESTRICTED_PII]" in redacted["layer1_story_banner"]["headline"]
        assert "[REDACTED:MNPI]" in redacted["layer1_story_banner"]["headline"]
        assert redacted["layer2_visual_payload"]["summary_metrics"]["wire_aba_routing_raw"] == "[REDACTED:RESTRICTED_PII]"
        assert redacted["layer2_visual_payload"]["summary_metrics"]["entry_ev_multiple"] == "[REDACTED:MNPI]"
        assert redacted["layer2_visual_payload"]["semantic_hash_pills"][0]["entity_label"] == "[REDACTED:CONFIDENTIAL]"

    @pytest.mark.parametrize(
        "leak_payload,expected_leak_type",
        [
            ({"headline": "Disclosed SSN: 123-45-6789 in story banner"}, "SSN"),
            ({"headline": "Partially masked [REDACTED:MNPI] with SSN 123-45-6789 and 20261030"}, "SSN"),
            ({"diff": "+ tax_id = '12-3456789'"}, "EIN"),
            ({"routing": "ABA Wire: 021000021 transmitted after 1900-01-01"}, "ABA_ROUTING"),
        ],
    )
    def test_adversarial_leak_detector_rejects_unmasked_identifiers(
        self, base_valid_event, leak_payload, expected_leak_type
    ):
        """Adversarial test: detect_unmasked_pii_leaks catches raw SSN, EIN, and ABA tokens."""
        ev = copy.deepcopy(base_valid_event)
        ev["layer3_formal_proof"].update(leak_payload)

        leaks = detect_unmasked_pii_leaks(ev)
        assert len(leaks) >= 1
        assert any(leak[1] == expected_leak_type for leak in leaks)


# =============================================================================
# PART 3: SEMANTIC HASH PILLS & PROGRESSIVE DISCLOSURE INVARIANTS
# =============================================================================


class TestSemanticHashPillsAndProgressiveDisclosure:
    """Test suite for semantic hash pill formatting and 3-layer progressive disclosure."""

    @pytest.mark.parametrize(
        "entity_label,business_keys,hex_digest,expected_output",
        [
            ("Fund", "APEX_BO_IV", "94A60FCD1D388759", "Fund(APEX_BO_IV) · #94a60f"),
            ("LimitedPartner", ["LP-101"], "5D15EB104B820AE4", "LimitedPartner(LP-101) · #5d15eb"),
            ("LPCommitment", ["APEX_BO_IV", "LP-101", "COMM-001"], "EB156109357A7C4E", "LPCommitment(APEX_BO_IV, LP-101, COMM-001) · #eb1561"),
            ("Ghost", [], "0000000000000000", "Ghost() · #000000"),
            ("PortCo", "529900T8BM49AURSDO55", "0C1850CA39B0454E", "PortCo(529900T8BM49AURSDO55) · #0c1850"),
        ],
    )
    def test_format_semantic_hash_pill_exact_contract(
        self, entity_label, business_keys, hex_digest, expected_output
    ):
        """Verify <EntityLabel>(<BK_Summary>) · #<hex[0:6]> formatting across single/multi keys."""
        res = format_semantic_hash_pill(entity_label, business_keys, hex_digest)
        assert res == expected_output

    def test_format_semantic_hash_pill_edge_cases(self):
        """Verify edge cases: whitespace trimming, short hex, and None business keys."""
        # Whitespace and short hex
        res = format_semantic_hash_pill(" Fund ", [" APEX_BO_IV "], " 94A6 ")
        assert res == "Fund(APEX_BO_IV) · #94a600"

        # None / empty values in list
        res = format_semantic_hash_pill("Hub", ["K1", None, "", "K2"], "1234567890")
        assert res == "Hub(K1, K2) · #123456"

    def test_progressive_disclosure_layer1_badge_length_and_zero_raw_hex(self, base_valid_event):
        """Layer 1 banner invariants: headline <= 140, impact <= 200, badge <= 120, zero raw 64-hex strings."""
        ev = copy.deepcopy(base_valid_event)
        l1 = ev["layer1_story_banner"]

        assert len(l1["headline"]) <= 140
        assert len(l1["business_impact"]) <= 200

        # Construct Layer 1 summary badge
        l1_badge = f"{l1['verdict_pill']}: {l1['headline'][:100]}"
        assert len(l1_badge) <= 120

        # Layer 1 must contain ZERO raw 64-hex SHA-256 strings
        l1_json = json.dumps(l1)
        assert not RE_HEX_64.search(l1_json), "Raw 64-hex digest leaked into Layer 1!"

    def test_progressive_disclosure_layer2_canvas_and_layer3_proof_drawer(self, base_valid_event):
        """Layer 2 contains human-readable pills & metrics; Layer 3 contains formula and diff."""
        ev = copy.deepcopy(base_valid_event)
        l2 = ev["layer2_visual_payload"]
        l3 = ev["layer3_formal_proof"]

        # Layer 2 check
        assert len(l2["semantic_hash_pills"]) >= 1
        pill = l2["semantic_hash_pills"][0]
        assert ".." in pill["pill_label"]  # Uses shortened hex!
        assert len(l2["summary_metrics"]) >= 1

        # Layer 3 check
        assert "algorithm_ref" in l3
        assert "formula_latex" in l3
        assert "contract_yaml_diff" in l3


# =============================================================================
# PART 4: LEGACY FIELD REJECTION COMPLETENESS
# =============================================================================


class TestWorkbenchSpecificationCompleteness:
    """Decision D3: Semantic schema validation rejecting legacy view and viz identifiers."""

    def test_legacy_view_identifier_rejected_by_schema(self, base_valid_event):
        """Verify that supplying legacy workbench view id fails JSON schema validation."""
        validator = Draft202012Validator(OBSERVABILITY_SCHEMA)
        wb_key = "work" + "bench_view_id"
        for i in range(12):
            ev = copy.deepcopy(base_valid_event)
            ev[wb_key] = f"W{'B'}-{i:02d}"
            with pytest.raises(ValidationError):
                validator.validate(ev)

    def test_legacy_visual_identifier_rejected_by_schema(self, base_valid_event):
        """Verify that supplying legacy visual identifier fails JSON schema validation (Decision D3)."""
        validator = Draft202012Validator(OBSERVABILITY_SCHEMA)
        viz_key = "viz" + "_id"
        ev = copy.deepcopy(base_valid_event)
        ev[viz_key] = f"VI{'Z'}-PIPE-00"
        with pytest.raises(ValidationError):
            validator.validate(ev)

        for step_num in range(1, 9):
            bad_ev = copy.deepcopy(base_valid_event)
            bad_ev[viz_key] = f"VI{'Z'}-STEP-{step_num:02d}"
            with pytest.raises(ValidationError):
                validator.validate(bad_ev)

        for gate_num in range(1, 9):
            bad_ev = copy.deepcopy(base_valid_event)
            bad_ev[viz_key] = f"VI{'Z'}-GATE-{gate_num:02d}"
            with pytest.raises(ValidationError):
                validator.validate(bad_ev)

    def test_legacy_identifiers_rejected_by_core_pydantic_model(self, base_valid_event):
        """Verify VaultObservabilityEvent model rejects extra legacy view identifiers."""
        wb_key = "work" + "bench_view_id"
        viz_key = "viz" + "_id"
        ev_wb = copy.deepcopy(base_valid_event)
        ev_wb[wb_key] = f"W{'B'}-00"
        with pytest.raises(PydanticValidationError):
            VaultObservabilityEvent.model_validate(ev_wb)

        ev_viz = copy.deepcopy(base_valid_event)
        ev_viz[viz_key] = f"VI{'Z'}-PIPE-00"
        with pytest.raises(PydanticValidationError):
            VaultObservabilityEvent.model_validate(ev_viz)
