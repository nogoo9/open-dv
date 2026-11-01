"""Comprehensive unit and async test suite for opendv-telemetry.

Tests:
1. Semantic Hash Pills formatting (HK, LHK, HD, GHOST, ZERO-KEY, ERR-KEY).
2. ContentAddressableStore read/write/verify and tamper detection.
3. 5-Tier RBAC redaction across all roles (PUBLIC, INTERNAL_MODELER,
   CONFIDENTIAL_ANALYST, PII_STEWARD, PRIVILEGED_CUSTODIAN).
4. PII, MNPI, raw 64-hex, and Shannon entropy leak detection.
5. W3C distributed trace context generation and contextvars propagation.
6. VaultLedgerEmitter JSON Schema validation, JSONL persistence, and SSE cursor replay.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from opendv_core.constants import (
    ZERO_KEY_ERROR_UPPER,
    ZERO_KEY_UNKNOWN,
)
from opendv_core.models.observability import VaultObservabilityEvent
from opendv_core.schema_registry import validate_observability_event
from opendv_telemetry.cas_store import (
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ContentAddressableStore,
)
from opendv_telemetry.hash_pills import (
    format_compact_hash_pill,
    format_error_key_pill,
    format_ghost_pill,
    format_hash_diff_pill,
    format_hub_pill,
    format_link_pill,
    format_semantic_hash_pill,
    format_zero_key_pill,
)
from opendv_telemetry.ledger_emitter import (
    VaultLedgerEmitter,
    format_sse_event_stream,
)
from opendv_telemetry.pii_detector import (
    PIILeakViolationError,
    compute_shannon_entropy_text,
    detect_unmasked_pii_leaks,
    scan_event_for_leaks,
    validate_no_pii_leaks,
)
from opendv_telemetry.rbac_redactor import (
    RbacRole,
    redact_event_for_role,
    redact_telemetry_event,
    resolve_cleared_tiers,
)
from opendv_telemetry.trace_context import (
    TraceContext,
    generate_span_id,
    generate_trace_id,
    get_current_trace_context,
    is_valid_span_id,
    is_valid_trace_id,
    trace_scope,
)

SAMPLE_SHA256_A = "5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D5D15EB10C8D7621D"
SAMPLE_SHA256_B = "EB156109357A7C4EEB156109357A7C4EEB156109357A7C4EEB156109357A7C4E"
SAMPLE_SHA256_C = "1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF"


@pytest.fixture
def base_valid_event() -> dict:
    """Fixture providing a minimal schema-valid VaultObservabilityEvent dictionary."""
    return {
        "event_id": "evt-0192ded0-7b40-7000-8000-000000000101",
        "run_id": "run-pe-apex-horizon-20261030",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": "b9c7c989f97918e1",
        "timestamp_utc": "2026-10-30T14:25:12Z",
        "stage_id": "STAGE_05_AXI_DV_STATIC_GATES",
        "workbench_view_id": "WB-07",
        "viz_id": "VIZ-GATE-05",
        "agent_persona": "axi-dv",
        "status": "PASS",
        "data_classification": "INTERNAL",
        "layer1_story_banner": {
            "headline": "Static verification gate evaluated with zero violations.",
            "business_impact": "Maintains 100% compliance with Data Vault 2.0 architecture.",
            "verdict_pill": "PASS",
        },
        "layer2_visual_payload": {
            "semantic_hash_pills": [
                format_semantic_hash_pill(
                    "HK",
                    SAMPLE_SHA256_A,
                    "GLOBAL",
                    "LP-101",
                    "CalPERS",
                )
            ],
            "summary_metrics": {
                "rules_evaluated": 12,
                "passed": 12,
                "failed": 0,
            },
        },
    }


# =============================================================================
# 1. SEMANTIC HASH PILLS
# =============================================================================


class TestSemanticHashPills:
    """Verify semantic hash pill rendering for HK, LHK, HD, GHOST, ZERO-KEY, and ERR-KEY."""

    def test_hub_hash_key_pill(self) -> None:
        pill = format_hub_pill(
            full_sha256_hex=SAMPLE_SHA256_A,
            bkcc_prefix="GLOBAL",
            business_key="LP-101",
            entity_label="CalPERS",
        )
        assert pill["pill_label"] == "[HK: 5D15..621D | GLOBAL:LP-101 CalPERS]"
        assert pill["full_sha256_hex"] == SAMPLE_SHA256_A
        assert pill["bkcc_prefix"] == "GLOBAL"
        assert pill["business_key"] == "LP-101"
        assert pill["entity_label"] == "CalPERS"

    def test_link_hash_key_pill(self) -> None:
        pill = format_link_pill(
            full_sha256_hex=SAMPLE_SHA256_B,
            link_label="Fund IV <-> CalPERS",
            bkcc_prefix="GLOBAL",
        )
        assert pill["pill_label"] == "[LHK: EB15..7C4E | Fund IV <-> CalPERS]"
        assert pill["full_sha256_hex"] == SAMPLE_SHA256_B

    def test_hash_diff_pill(self) -> None:
        pill = format_hash_diff_pill(
            full_sha256_hex=SAMPLE_SHA256_C,
            satellite_name="sat_lp_commitment_fx",
        )
        assert pill["pill_label"] == "[HD: 1234..CDEF | sat_lp_commitment_fx]"
        assert pill["full_sha256_hex"] == SAMPLE_SHA256_C

    def test_system_ghost_pill(self) -> None:
        pill = format_ghost_pill()
        assert pill["pill_label"] == "[GHOST: 0000..0000 | SYSTEM_GHOST (1900-01-01)]"
        assert pill["full_sha256_hex"] == ZERO_KEY_UNKNOWN
        assert pill["bkcc_prefix"] == "SYSTEM_GHOST"

    def test_unknown_zero_key_pill(self) -> None:
        pill = format_zero_key_pill()
        assert pill["pill_label"] == "[ZERO-KEY: 0000..0000 | UNKNOWN (-1)]"
        assert pill["full_sha256_hex"] == ZERO_KEY_UNKNOWN
        assert pill["bkcc_prefix"] == "SYSTEM"

    def test_error_zero_key_pill(self) -> None:
        pill = format_error_key_pill()
        assert pill["pill_label"] == "[ERR-KEY: FFFF..FFFF | ERROR (-2)]"
        assert pill["full_sha256_hex"] == ZERO_KEY_ERROR_UPPER
        assert pill["bkcc_prefix"] == "SYSTEM"

    def test_invalid_sha256_raises_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid SHA-256"):
            format_semantic_hash_pill("HK", "INVALID_HEX_DIGEST")

    def test_compact_hash_pill(self) -> None:
        compact = format_compact_hash_pill("CalPERS", ["GLOBAL", "LP-101"], SAMPLE_SHA256_A)
        assert compact == "CalPERS(GLOBAL, LP-101) · #5d15eb"


# =============================================================================
# 2. CONTENT-ADDRESSABLE STORE (CAS)
# =============================================================================


class TestContentAddressableStore:
    """Verify CAS artifact storage, verification, and tamper detection."""

    def test_put_and_get_str(self, tmp_path: Path) -> None:
        store = ContentAddressableStore(base_dir=tmp_path / "artifacts")
        text = "Hello Open-DV Content-Addressable Storage"
        addr = store.put(text)
        assert len(addr) == 64
        assert store.has(addr)
        assert store.get_str(addr) == text
        assert store.verify(addr)

    def test_put_and_get_bytes(self, tmp_path: Path) -> None:
        store = ContentAddressableStore(base_dir=tmp_path / "artifacts")
        data = b"\x00\x01\x02\x03\xfe\xff"
        addr = store.put(data)
        assert store.get_bytes(addr) == data
        assert store.verify(addr)

    def test_put_file(self, tmp_path: Path) -> None:
        store = ContentAddressableStore(base_dir=tmp_path / "artifacts")
        src_file = tmp_path / "test_doc.yaml"
        src_file.write_text("contract_version: '5.0'\nhubs: []", encoding="utf-8")

        addr = store.put_file(src_file)
        assert store.has(addr)
        assert "contract_version: '5.0'" in store.get_str(addr)

    def test_tamper_detection(self, tmp_path: Path) -> None:
        store = ContentAddressableStore(base_dir=tmp_path / "artifacts")
        addr = store.put("Original Untampered Content")

        # Corrupt file directly on disk
        artifact_path = store.base_dir / addr
        artifact_path.write_bytes(b"Tampered Corrupted Payload")

        assert not store.verify(addr)
        with pytest.raises(ArtifactCorruptedError, match="integrity violation"):
            store.get_bytes(addr)

    def test_nonexistent_artifact_raises(self, tmp_path: Path) -> None:
        store = ContentAddressableStore(base_dir=tmp_path / "artifacts")
        fake_addr = "A" * 64
        assert not store.has(fake_addr)
        assert not store.verify(fake_addr)
        with pytest.raises(ArtifactNotFoundError):
            store.get_bytes(fake_addr)

    def test_verify_all(self, tmp_path: Path) -> None:
        store = ContentAddressableStore(base_dir=tmp_path / "artifacts")
        addr1 = store.put("Artifact 1")
        addr2 = store.put("Artifact 2")

        status = store.verify_all()
        assert status[addr1] is True
        assert status[addr2] is True


# =============================================================================
# 3. W3C DISTRIBUTED TRACE CONTEXT
# =============================================================================


class TestW3CTraceContext:
    """Verify W3C trace_id and span_id generation and contextvars propagation."""

    def test_generator_validity(self) -> None:
        tid = generate_trace_id()
        sid = generate_span_id()
        assert len(tid) == 32
        assert len(sid) == 16
        assert is_valid_trace_id(tid)
        assert is_valid_span_id(sid)

    def test_trace_context_child_span(self) -> None:
        parent = TraceContext(
            trace_id=generate_trace_id(),
            span_id=generate_span_id(),
        )
        child = parent.new_child_span()
        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        assert child.span_id != parent.span_id

    def test_w3c_traceparent_roundtrip(self) -> None:
        ctx = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
        )
        header = ctx.to_w3c_traceparent()
        assert header == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        parsed = TraceContext.from_w3c_traceparent(header)
        assert parsed.trace_id == ctx.trace_id
        assert parsed.span_id == ctx.span_id

    def test_trace_scope_propagation(self) -> None:
        assert get_current_trace_context() is None

        with trace_scope() as root_ctx:
            assert get_current_trace_context() == root_ctx
            with trace_scope() as child_ctx:
                assert get_current_trace_context() == child_ctx
                assert child_ctx.trace_id == root_ctx.trace_id
                assert child_ctx.parent_span_id == root_ctx.span_id

            assert get_current_trace_context() == root_ctx

        assert get_current_trace_context() is None


# =============================================================================
# 4. 5-TIER RBAC REDACTION MATRIX
# =============================================================================


class TestFiveTierRbacRedaction:
    """Verify 5-tier classification redaction across PUBLIC, INTERNAL_MODELER,

    CONFIDENTIAL_ANALYST, PII_STEWARD, and PRIVILEGED_CUSTODIAN roles.
    """

    def test_role_clearance_hierarchy(self) -> None:
        assert resolve_cleared_tiers(RbacRole.PUBLIC) == frozenset({"PUBLIC"})
        assert resolve_cleared_tiers(RbacRole.INTERNAL_MODELER) == frozenset({"PUBLIC", "INTERNAL"})
        assert resolve_cleared_tiers(RbacRole.CONFIDENTIAL_ANALYST) == frozenset(
            {"PUBLIC", "INTERNAL", "CONFIDENTIAL"}
        )
        assert resolve_cleared_tiers(RbacRole.PII_STEWARD) == frozenset(
            {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED_PII"}
        )
        assert resolve_cleared_tiers(RbacRole.PRIVILEGED_CUSTODIAN) == frozenset(
            {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "MNPI", "RESTRICTED_PII"}
        )

    def test_public_role_masks_internal_events(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "INTERNAL"

        redacted = redact_event_for_role(ev, role=RbacRole.PUBLIC)
        assert "[REDACTED INTERNAL]" in redacted["layer1_story_banner"]["headline"]

    def test_internal_modeler_sees_internal_unmasked(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "INTERNAL"

        redacted = redact_event_for_role(ev, role=RbacRole.INTERNAL_MODELER)
        pill = redacted["layer2_visual_payload"]["semantic_hash_pills"][0]
        assert pill["business_key"] == "LP-101"
        assert pill["entity_label"] == "CalPERS"

    def test_confidential_tier_masks_individual_lps(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "CONFIDENTIAL"
        ev["layer2_visual_payload"]["summary_metrics"]["lp_count"] = 7

        # INTERNAL_MODELER lacks CONFIDENTIAL clearance
        redacted = redact_event_for_role(ev, role=RbacRole.INTERNAL_MODELER)
        pill = redacted["layer2_visual_payload"]["semantic_hash_pills"][0]
        assert pill["entity_label"] == "[REDACTED:CONFIDENTIAL]"
        assert pill["business_key"] == "[REDACTED:CONFIDENTIAL]"
        assert "[REDACTED:CONFIDENTIAL]" in pill["pill_label"]
        # Aggregates preserved
        assert redacted["layer2_visual_payload"]["summary_metrics"]["lp_count"] == 7

        # CONFIDENTIAL_ANALYST clears CONFIDENTIAL
        cleared = redact_event_for_role(ev, role=RbacRole.CONFIDENTIAL_ANALYST)
        cleared_pill = cleared["layer2_visual_payload"]["semantic_hash_pills"][0]
        assert cleared_pill["entity_label"] == "CalPERS"
        assert cleared_pill["business_key"] == "LP-101"

    def test_mnpi_tier_masks_valuation_figures(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "MNPI"
        ev["layer1_story_banner"]["headline"] = (
            "Audited Q3 Nordic Cloud valuation restated to 14.5x EBITDA ($245,000,000.00)"
        )
        ev["layer2_visual_payload"]["summary_metrics"] = {
            "entry_ev_multiple": "14.5x",
            "fair_value_usd": 245000000.00,
            "table_count": 1,
        }

        # CONFIDENTIAL_ANALYST lacks MNPI clearance
        redacted = redact_event_for_role(ev, role=RbacRole.CONFIDENTIAL_ANALYST)
        assert "14.5x" not in redacted["layer1_story_banner"]["headline"]
        assert "$245,000,000.00" not in redacted["layer1_story_banner"]["headline"]
        assert "[REDACTED:MNPI]" in redacted["layer1_story_banner"]["headline"]
        assert (
            redacted["layer2_visual_payload"]["summary_metrics"]["entry_ev_multiple"]
            == "[REDACTED:MNPI]"
        )
        assert redacted["layer2_visual_payload"]["summary_metrics"]["table_count"] == 1

        # PRIVILEGED_CUSTODIAN clears MNPI
        cleared = redact_event_for_role(ev, role=RbacRole.PRIVILEGED_CUSTODIAN)
        assert "14.5x" in cleared["layer1_story_banner"]["headline"]
        assert "$245,000,000.00" in cleared["layer1_story_banner"]["headline"]
        assert cleared["layer2_visual_payload"]["summary_metrics"]["entry_ev_multiple"] == "14.5x"

    def test_pii_steward_sees_pii_but_masks_mnpi(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "RESTRICTED_PII"
        ev["layer1_story_banner"]["headline"] = (
            "Loaded tax_id_ein 12-3456789, SSN 123-45-6789, ABA 021000021 at 14.5x ($245M)"
        )

        redacted = redact_event_for_role(ev, role=RbacRole.PII_STEWARD)
        # PII Steward can view PII
        assert "12-3456789" in redacted["layer1_story_banner"]["headline"]
        assert "123-45-6789" in redacted["layer1_story_banner"]["headline"]
        # But lacks MNPI clearance: valuation figure masked
        assert "[REDACTED:MNPI]" in redacted["layer1_story_banner"]["headline"]

    def test_restricted_pii_masked_for_standard_roles(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "RESTRICTED_PII"
        ev["layer1_story_banner"]["headline"] = (
            "Loaded tax_id_ein 12-3456789, SSN 123-45-6789, ABA 021000021"
        )
        ev["layer2_visual_payload"]["summary_metrics"] = {
            "wire_aba_routing_raw": "021000021",
        }

        redacted = redact_event_for_role(ev, role=RbacRole.INTERNAL_MODELER)
        assert "12-3456789" not in redacted["layer1_story_banner"]["headline"]
        assert "123-45-6789" not in redacted["layer1_story_banner"]["headline"]
        assert "021000021" not in redacted["layer1_story_banner"]["headline"]
        assert "[REDACTED:RESTRICTED_PII]" in redacted["layer1_story_banner"]["headline"]
        assert (
            redacted["layer2_visual_payload"]["summary_metrics"]["wire_aba_routing_raw"]
            == "[REDACTED:RESTRICTED_PII]"
        )

    def test_redacted_event_remains_schema_valid(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "CONFIDENTIAL"
        redacted = redact_event_for_role(ev, role=RbacRole.PUBLIC)
        # Must validate cleanly against schemas/vault-observability-event.schema.json
        validate_observability_event(redacted)

    def test_mnpi_decimal_magnitude_and_pii_banner_length_clamping(
        self, base_valid_event: dict
    ) -> None:
        ev = dict(base_valid_event)
        ev["data_classification"] = "RESTRICTED_PII"
        ev["layer1_story_banner"]["headline"] = (
            "Loaded tax_id_ein 12-3456789, SSN 123-45-6789, ABA 021000021 "
            "at 14.5x ($216.28M vs $245M)"
        )
        ev["layer2_visual_payload"]["summary_metrics"] = {
            "wire_aba_routing_raw": "021000021",
            "entry_ev_multiple": "14.5x",
        }

        redacted = redact_telemetry_event(ev, viewer_roles=["COMPLIANCE_AUDITOR"])
        hl = redacted["layer1_story_banner"]["headline"]
        assert "$216" not in hl
        assert ".28M" not in hl
        assert "$245M" not in hl
        assert "$[REDACTED:MNPI]" not in hl
        assert "[REDACTED:RESTRICTED_PII]" in hl
        assert "[REDACTED:MNPI]" in hl
        assert len(hl) <= 140
        assert (
            redacted["layer2_visual_payload"]["semantic_hash_pills"][0]["entity_label"]
            == "[REDACTED:CONFIDENTIAL]"
        )
        validate_observability_event(redacted)

        internal_ev = dict(base_valid_event)
        internal_ev["data_classification"] = "INTERNAL"
        unredacted = redact_telemetry_event(internal_ev, viewer_roles=[])
        assert (
            unredacted["layer2_visual_payload"]["semantic_hash_pills"][0]["entity_label"]
            == "CalPERS"
        )


# =============================================================================
# 5. PII & ENTROPY LEAK DETECTOR
# =============================================================================


class TestPiiAndEntropyDetector:
    """Verify regex patterns, raw 64-hex blocking in Layer 1, and Shannon entropy scanning."""

    def test_detects_raw_pii_tokens(self) -> None:
        payload = {
            "ssn_val": "123-45-6789",
            "ein_val": "12-3456789",
            "aba_val": "021000021",
        }
        leaks = detect_unmasked_pii_leaks(payload)
        types = {leak[1] for leak in leaks}
        assert types == {"SSN", "EIN", "ABA_ROUTING"}

    def test_layer1_blocks_raw_64_hex_string(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        ev["layer1_story_banner"]["headline"] = f"Processed entity with hash {SAMPLE_SHA256_A}"

        findings = scan_event_for_leaks(ev)
        assert any(f.leak_type == "RAW_64_HEX_IN_LAYER1" for f in findings)
        with pytest.raises(PIILeakViolationError, match="RAW_64_HEX_IN_LAYER1"):
            validate_no_pii_leaks(ev)

    def test_layer1_blocks_high_entropy_token(self, base_valid_event: dict) -> None:
        ev = dict(base_valid_event)
        # Random high-entropy token in story banner
        ev["layer1_story_banner"]["headline"] = (
            "Verified secret token A9F8B7C6D5E432109876543210ABCDEF12"
        )
        findings = scan_event_for_leaks(ev)
        assert any(f.leak_type == "HIGH_ENTROPY_TOKEN_IN_LAYER1" for f in findings)

    def test_layer1_allows_snake_case_and_dotted_identifiers(
        self, base_valid_event: dict
    ) -> None:
        ev = dict(base_valid_event)
        ev["layer1_story_banner"]["headline"] = (
            "Merged sat_lp_commitment_investran from investran.src_lp_investors"
        )
        ev["layer1_story_banner"]["business_impact"] = (
            "Extracted calpers_side_letter_fund_iv_2026.pdf in STAGE_07_DETERMINISTIC_COMPILER."
        )
        assert scan_event_for_leaks(ev) == []
        validate_no_pii_leaks(ev)

    def test_shannon_entropy_calculation(self) -> None:
        # Uniform hex string has high entropy
        high_entropy = "0123456789abcdef0123456789abcdef"
        low_entropy = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert compute_shannon_entropy_text(high_entropy) > 3.8
        assert compute_shannon_entropy_text(low_entropy) == 0.0


# =============================================================================
# 6. VAULT LEDGER EMITTER & ASYNC SSE STREAMING
# =============================================================================


class TestVaultLedgerEmitterAndSSE:
    """Verify JSONL ledger emission, schema validation, and async SSE cursor replay."""

    def test_emit_valid_event(self, tmp_path: Path, base_valid_event: dict) -> None:
        ledger_file = tmp_path / "state" / "vault-ledger.jsonl"
        emitter = VaultLedgerEmitter(ledger_path=ledger_file)

        emitted = emitter.emit(base_valid_event)
        assert emitted["event_id"] == base_valid_event["event_id"]
        assert ledger_file.is_file()

        lines = ledger_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_id"] == base_valid_event["event_id"]

    def test_emit_attaches_active_trace_context(
        self, tmp_path: Path, base_valid_event: dict
    ) -> None:
        ledger_file = tmp_path / "state" / "vault-ledger.jsonl"
        emitter = VaultLedgerEmitter(ledger_path=ledger_file)

        ev = dict(base_valid_event)
        del ev["trace_id"]
        del ev["span_id"]

        with trace_scope() as ctx:
            emitted = emitter.emit(ev)
            assert emitted["trace_id"] == ctx.trace_id
            assert emitted["span_id"] == ctx.span_id

    def test_emit_rejects_schema_invalid_event(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "state" / "vault-ledger.jsonl"
        emitter = VaultLedgerEmitter(ledger_path=ledger_file)

        invalid_event = {
            "event_id": "missing_required_fields",
        }
        with pytest.raises((ValueError, ValidationError)):
            emitter.emit(invalid_event)

    def test_replay_events_monotonic(self, tmp_path: Path, base_valid_event: dict) -> None:
        ledger_file = tmp_path / "state" / "vault-ledger.jsonl"
        emitter = VaultLedgerEmitter(ledger_path=ledger_file)

        ev1 = dict(base_valid_event)
        ev1["event_id"] = "evt-001"
        ev2 = dict(base_valid_event)
        ev2["event_id"] = "evt-002"
        ev3 = dict(base_valid_event)
        ev3["event_id"] = "evt-003"

        emitter.emit(ev1)
        emitter.emit(ev2)
        emitter.emit(ev3)

        replayed_all = emitter.replay_events()
        assert [e["event_id"] for e in replayed_all] == ["evt-001", "evt-002", "evt-003"]

        replayed_after_1 = emitter.replay_events(after_event_id="evt-001")
        assert [e["event_id"] for e in replayed_after_1] == ["evt-002", "evt-003"]

        replayed_after_2 = emitter.replay_events(after_event_id="evt-002")
        assert [e["event_id"] for e in replayed_after_2] == ["evt-003"]

    @pytest.mark.asyncio
    async def test_format_sse_event_stream_last_event_id(self, base_valid_event: dict) -> None:
        ev1 = dict(base_valid_event)
        ev1["event_id"] = "evt-001"
        ev2 = dict(base_valid_event)
        ev2["event_id"] = "evt-002"
        ev3 = dict(base_valid_event)
        ev3["event_id"] = "evt-003"

        events = [ev1, ev2, ev3]
        frames: list[str] = []

        async for frame in format_sse_event_stream(
            events, last_event_id="evt-001", role=RbacRole.INTERNAL_MODELER
        ):
            frames.append(frame)

        # Expect evt-002, evt-003, and run_complete
        assert any("id: evt-002" in f for f in frames)
        assert any("id: evt-003" in f for f in frames)
        assert not any("id: evt-001" in f for f in frames)
        assert any("event: run_complete" in f for f in frames)

    def test_emit_and_redact_pydantic_model_instance(
        self, tmp_path: Path, base_valid_event: dict
    ) -> None:
        ledger_file = tmp_path / "state" / "vault-ledger.jsonl"
        emitter = VaultLedgerEmitter(ledger_path=ledger_file)

        ev_dict = dict(base_valid_event)
        ev_dict.pop("data_classification", None)
        model_instance = VaultObservabilityEvent.model_validate(ev_dict)

        assert scan_event_for_leaks(model_instance) == []
        redacted = redact_event_for_role(model_instance, role=RbacRole.PUBLIC)
        assert "[REDACTED" not in redacted["layer1_story_banner"]["headline"]
        validate_observability_event(redacted)

        emitted = emitter.emit(model_instance)
        assert emitted["event_id"] == model_instance.event_id
        validate_observability_event(emitted)

    @pytest.mark.asyncio
    async def test_subscribe_sse_terminates_when_stage07_already_in_ledger(
        self, tmp_path: Path, base_valid_event: dict
    ) -> None:
        ledger_file = tmp_path / "state" / "vault-ledger.jsonl"
        emitter = VaultLedgerEmitter(ledger_path=ledger_file)

        ev1 = dict(base_valid_event)
        ev1["event_id"] = "evt-stage-05"
        ev2 = dict(base_valid_event)
        ev2["event_id"] = "evt-stage-07"
        ev2["stage_id"] = "STAGE_07_DETERMINISTIC_COMPILER"
        ev2["agent_persona"] = "deterministic-compiler"

        emitter.emit(ev1)
        emitter.emit(ev2)

        frames: list[str] = []
        async for frame in emitter.subscribe_sse(last_event_id="evt-stage-05"):
            frames.append(frame)

        assert len(frames) == 2
        assert "id: evt-stage-07" in frames[0]
        assert "event: run_complete" in frames[1]
