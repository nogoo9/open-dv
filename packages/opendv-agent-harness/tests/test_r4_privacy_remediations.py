"""Regression tests for R4-A Agent Privacy remediations (P09a::MAJ-01).

Covers:
- `Pseudonymizer.pseudonymize_chunk` computing `effective_terms` as the deduplicated
  non-empty union of caller `sensitive_terms` and `chunk.metadata["sensitive_entities"]`
  (including nested metadata mappings, sets, keys, and values).
- Pre- and post-pop residual leak verification raising `CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN`.
- `_walk_privacy_input` enforcement against unstripped `sensitive_entities` and tainted
  `IngestionResult.page_map` text.
"""

from __future__ import annotations

import json

import pytest

from opendv_agent_harness.personas import build_term_scout_task_spec
from opendv_agent_harness.privacy import (
    Pseudonymizer,
    _build_term_pattern,
    _verify_no_residual_terms_in_value,
    validate_task_spec_privacy,
)
from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    PageMapEntry,
    TableStructure,
    TaintClassification,
)
from opendv_contracts.errors import ErrorCode, OpenDVError


def _make_chunk(
    text: str,
    *,
    metadata: dict | None = None,
    taint: TaintClassification = TaintClassification.CONFIDENTIAL,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="chk-r4-priv",
        document_id="doc-r4-priv",
        text=text,
        taint=taint,
        metadata=metadata or {},
    )


class TestPseudonymizeChunkUnionRemediation:
    """Regression suite for P09a::MAJ-01."""

    def test_caller_sensitive_terms_and_metadata_sensitive_entities_are_unioned(self) -> None:
        pseudo = Pseudonymizer(b"r4-secret-hmac-key-0123456789abc")
        chunk = _make_chunk(
            "Project TermA term sheet reviewed by John Smith at closing.",
            metadata={
                "sensitive_entities": ["John Smith"],
                "reviewer_note": "Approved by John Smith for TermA deal.",
                "tags": {"TermA", "John Smith"},
                "nested": {
                    "approver": "John Smith",
                    "codename": "TermA",
                },
            },
        )

        masked = pseudo.pseudonymize_chunk(chunk, sensitive_terms=["TermA"])

        assert masked.metadata["pseudonymized"] is True
        assert masked.metadata["redacted_entity_count"] == 2
        assert "sensitive_entities" not in masked.metadata

        # Neither TermA nor John Smith may appear anywhere in masked text or metadata
        assert "terma" not in masked.text.lower()
        assert "john smith" not in masked.text.lower()
        assert pseudo.pseudonymize_token("TermA") in masked.text
        assert pseudo.pseudonymize_token("John Smith") in masked.text

        # Set elements and nested dict strings are also masked
        assert all(
            "terma" not in s.lower() and "john smith" not in s.lower()
            for s in masked.metadata["tags"]
        )
        serialized_dump = json.dumps(
            {
                "text": masked.text,
                "reviewer_note": masked.metadata["reviewer_note"],
                "nested": masked.metadata["nested"],
            }
        ).lower()
        assert "terma" not in serialized_dump
        assert "john smith" not in serialized_dump

        # Round-trip de-pseudonymization restores original text
        assert pseudo.de_pseudonymize(masked.text) == chunk.text

        # Resulting chunk passes TaskSpec privacy validation cleanly
        spec = build_term_scout_task_spec(
            input_data={
                "chunks": [
                    masked.model_copy(
                        update={
                            "metadata": {**masked.metadata, "tags": sorted(masked.metadata["tags"])}
                        }
                    )
                ]
            }
        )
        assert spec is not None

    def test_union_deduplicates_case_insensitively_and_collects_nested_sensitive_entities(
        self,
    ) -> None:
        pseudo = Pseudonymizer(b"r4-secret-hmac-key-0123456789abc")
        chunk = _make_chunk(
            "TermA signed by John Smith and Jane Doe.",
            metadata={
                "sensitive_entities": ["John Smith", "terma"],
                "nested": {"sensitive_entities": ("Jane Doe", "JOHN SMITH")},
            },
        )

        masked = pseudo.pseudonymize_chunk(chunk, sensitive_terms=["TermA"])
        assert masked.metadata["redacted_entity_count"] == 3
        assert "sensitive_entities" not in masked.metadata
        assert "sensitive_entities" not in masked.metadata["nested"]
        for leaked in ("terma", "john smith", "jane doe"):
            assert leaked not in masked.text.lower()

    def test_residual_leak_of_metadata_entity_when_caller_passes_terms_raises_forbidden(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pseudo = Pseudonymizer(b"r4-secret-hmac-key-0123456789abc")
        orig_pseudonymize_text = pseudo.pseudonymize_text

        # 1. Simulate faulty text masking that ignores metadata 'John Smith' and only masks 'TermA'
        monkeypatch.setattr(
            pseudo,
            "pseudonymize_text",
            lambda text, _terms: orig_pseudonymize_text(text, ["TermA"]),
        )
        chunk_text_leak = _make_chunk(
            "Project TermA assigned to handle John Smith.",
            metadata={"sensitive_entities": ["John Smith"]},
        )
        with pytest.raises(OpenDVError) as exc_text:
            pseudo.pseudonymize_chunk(chunk_text_leak, sensitive_terms=["TermA"])
        assert exc_text.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
        assert "john smith" not in str(exc_text.value).lower()
        assert "terma" not in str(exc_text.value).lower()

        # 2. Simulate faulty text masking that ignores caller 'TermA' and only masks 'John Smith'
        monkeypatch.setattr(
            pseudo,
            "pseudonymize_text",
            lambda text, _terms: orig_pseudonymize_text(text, ["John Smith"]),
        )
        chunk_caller_leak = _make_chunk(
            "Code TermA reviewed by John Smith.",
            metadata={"sensitive_entities": ["John Smith"]},
        )
        with pytest.raises(OpenDVError) as exc_caller:
            pseudo.pseudonymize_chunk(chunk_caller_leak, sensitive_terms=["TermA"])
        assert exc_caller.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
        assert "terma" not in str(exc_caller.value).lower()
        assert "john smith" not in str(exc_caller.value).lower()

        # 3. Residual metadata entity under confidential key raises forbidden without leaking either
        monkeypatch.setattr(
            pseudo,
            "pseudonymize_text",
            lambda text, _terms: (
                text
                if "audit_by_" in text
                else orig_pseudonymize_text(text, ["TermA", "John Smith"])
            ),
        )
        chunk_meta_leak = _make_chunk(
            "Project TermA reviewed by John Smith.",
            metadata={
                "sensitive_entities": ["John Smith"],
                "secret_audit_trail_key": "audit_by_John Smith",
            },
        )
        with pytest.raises(OpenDVError) as exc_meta:
            pseudo.pseudonymize_chunk(chunk_meta_leak, sensitive_terms=["TermA"])
        assert exc_meta.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
        msg_lower = str(exc_meta.value).lower()
        assert "john smith" not in msg_lower
        assert "terma" not in msg_lower
        assert "secret_audit_trail_key" not in msg_lower

    def test_no_plaintext_or_raw_key_leak_in_residual_or_collision_exceptions(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression P09a::MAJ-01: Exception messages never leak confidential terms or raw keys."""
        effective_terms = ["Project Nightfall", "Counterparty Omega"]
        patterns = [_build_term_pattern(t) for t in effective_terms]

        # 1. Residual leak inside nested metadata key and value scrubs both term and raw key name
        with pytest.raises(OpenDVError) as exc_val:
            _verify_no_residual_terms_in_value(
                {"secret_deal_codename_key": "Signed with Counterparty Omega yesterday."},
                patterns,
                "chunk.metadata",
            )
        assert exc_val.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
        err_str = str(exc_val.value)
        assert all(t.lower() not in err_str.lower() for t in effective_terms)
        assert "secret_deal_codename_key" not in err_str
        assert "term #1" in err_str
        assert "<entry#0>" in err_str

        with pytest.raises(OpenDVError) as exc_key:
            _verify_no_residual_terms_in_value(
                {"Project Nightfall": "redacted"},
                patterns,
                "chunk.metadata",
            )
        err_key_str = str(exc_key.value)
        assert all(t.lower() not in err_key_str.lower() for t in effective_terms)
        assert "<key#0>" in err_key_str

        # 2. HMAC collision ValueError reports ONLY colliding pseudo_<hex> token, never plaintexts
        import hmac as std_hmac

        class _FixedDigest:
            def hexdigest(self) -> str:
                return "deadbeefcafe00a1" * 4

        monkeypatch.setattr(std_hmac, "new", lambda *_a, **_kw: _FixedDigest())
        collision_pseudo = Pseudonymizer(b"collision-test-key-0123456789abc")
        tok = collision_pseudo.pseudonymize_token("Project Nightfall")
        assert tok == "pseudo_deadbeefcafe00a1"

        with pytest.raises(ValueError) as exc_coll:
            collision_pseudo.pseudonymize_token("Counterparty Omega")
        coll_str = str(exc_coll.value)
        assert "pseudo_deadbeefcafe00a1" in coll_str
        assert all(t.lower() not in coll_str.lower() for t in effective_terms)

    def test_boundary_aware_residual_verification_and_pseudonym_scrubbing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression P09a::MAJ-02: Boundary matching and pseudo_<16hex> scrubbing."""
        pseudo = Pseudonymizer(b"r6-boundary-hmac-key-0123456789ab")

        # (a) 'Ann' inside 'Annual' and 'Acme' inside 'AcmeCorp' must NOT false-positive
        ann_chunk = _make_chunk(
            "Ann reviewed the Annual report while Acme and AcmeCorp finalized terms.",
            metadata={
                "sensitive_entities": ["Ann", "Acme"],
                "summary": "Annual filing for AcmeCorp reviewed by Ann at Acme.",
            },
        )
        masked_ann = pseudo.pseudonymize_chunk(ann_chunk)
        assert "Annual" in masked_ann.text
        assert "AcmeCorp" in masked_ann.text
        assert "Annual" in masked_ann.metadata["summary"]
        assert "AcmeCorp" in masked_ann.metadata["summary"]
        assert pseudo.de_pseudonymize(masked_ann.text) == ann_chunk.text

        # (b) Short/hex/prefix terms ('do', 'eu', 'a1', 'cafe', 'pseudo') appearing inside
        # generated pseudo_<16hex> tokens must NEVER self-trigger or corrupt tokens!
        short_terms = ["do", "eu", "a1", "cafe", "pseudo", "Long Secret Counterparty"]
        short_chunk = _make_chunk(
            "Long Secret Counterparty will do eu a1 review at cafe with pseudo tag.",
            metadata={
                "sensitive_entities": short_terms,
                "note": "cafe a1 do eu pseudo check",
            },
        )
        # Force deterministic hex digests containing 'cafe', 'a1', 'd0' to guarantee overlap
        import hashlib as std_hashlib
        import hmac as std_hmac

        real_hmac_new = std_hmac.new

        class _OverlapDigest:
            def __init__(self, term_bytes: bytes) -> None:
                raw_hex = real_hmac_new(b"overlap-seed", term_bytes, std_hashlib.sha256).hexdigest()
                # Embed 'cafea1' prefix into every 16-hex token while keeping distinctness
                self._hex = "cafea1" + raw_hex[:58]

            def hexdigest(self) -> str:
                return self._hex

        monkeypatch.setattr(std_hmac, "new", lambda _k, msg, _d: _OverlapDigest(msg))
        overlap_pseudo = Pseudonymizer(b"overlap-key-0123456789abcdef0123")
        masked_short = overlap_pseudo.pseudonymize_chunk(short_chunk)
        assert masked_short.metadata["pseudonymized"] is True
        assert overlap_pseudo.de_pseudonymize(masked_short.text) == short_chunk.text

        # (c) Genuine unmasked 'Ann' in a chunk marked pseudonymized=True STILL raises forbidden
        # with zero plaintext 'Ann' in the exception message
        unmasked_ann_chunk = _make_chunk(
            "Annual summary reviewed by Ann yesterday.",
            metadata={
                "pseudonymized": True,
                "sensitive_entities": ["Ann"],
            },
        )
        with pytest.raises(OpenDVError) as exc_unmasked:
            validate_task_spec_privacy("Inspect chunk", input_data=unmasked_ann_chunk)
        assert exc_unmasked.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
        assert "ann" not in str(exc_unmasked.value).lower()

    def test_walk_privacy_input_rejects_unstripped_sensitive_entities_and_raw_page_map(
        self,
    ) -> None:
        # 1. Forged pseudonymized=True with lingering sensitive_entities in EvidenceChunk
        forged_chunk = _make_chunk(
            "Clean summary text.",
            metadata={
                "pseudonymized": True,
                "nested": {"sensitive_entities": {"John Smith"}},
            },
        )
        with pytest.raises(OpenDVError) as exc_chk:
            validate_task_spec_privacy("Analyze chunk", input_data={"chunk": forged_chunk})
        assert exc_chk.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

        # 2. Forged pseudonymized=True with lingering sensitive_entities in TableStructure
        forged_tbl = TableStructure(
            table_id="tbl-1",
            headers=["col"],
            rows=[["val"]],
            taint=TaintClassification.CONFIDENTIAL,
            metadata={"pseudonymized": True, "sensitive_entities": ["John Smith"]},
        )
        with pytest.raises(OpenDVError) as exc_tbl:
            validate_task_spec_privacy("Analyze table", input_data={"table": forged_tbl})
        assert exc_tbl.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

        # 3. Tainted IngestionResult with empty normalized_text but raw text in page_map
        raw_page_ing = IngestionResult(
            document_id="doc-page-leak",
            filename="confidential.pdf",
            mime_type="application/pdf",
            taint=TaintClassification.CONFIDENTIAL,
            normalized_text="",
            page_map=[
                PageMapEntry(
                    page_number=1,
                    text="Raw confidential page text for John Smith.",
                    start_offset=0,
                    end_offset=42,
                )
            ],
        )
        with pytest.raises(OpenDVError) as exc_ing:
            validate_task_spec_privacy("Analyze ingestion", input_data=raw_page_ing)
        assert exc_ing.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
