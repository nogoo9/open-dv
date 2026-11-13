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
from opendv_agent_harness.privacy import Pseudonymizer, validate_task_spec_privacy
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
    ) -> None:
        pseudo = Pseudonymizer(b"r4-secret-hmac-key-0123456789abc")

        # 1. Metadata entity 'John Smith' embedded in alphanumeric token in chunk.text
        chunk_text_leak = _make_chunk(
            "Project TermA assigned to handle xJohn Smith99.",
            metadata={"sensitive_entities": ["John Smith"]},
        )
        with pytest.raises(OpenDVError) as exc_text:
            pseudo.pseudonymize_chunk(chunk_text_leak, sensitive_terms=["TermA"])
        assert exc_text.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

        # 2. Caller term 'TermA' embedded in alphanumeric token in chunk.text
        chunk_caller_leak = _make_chunk(
            "Code superTermA99 reviewed by John Smith.",
            metadata={"sensitive_entities": ["John Smith"]},
        )
        with pytest.raises(OpenDVError) as exc_caller:
            pseudo.pseudonymize_chunk(chunk_caller_leak, sensitive_terms=["TermA"])
        assert exc_caller.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

        # 3. Metadata entity 'John Smith' embedded in alphanumeric token inside chunk.metadata
        chunk_meta_leak = _make_chunk(
            "Project TermA reviewed by John Smith.",
            metadata={
                "sensitive_entities": ["John Smith"],
                "audit_trail": "approved_by_xJohn Smith99",
            },
        )
        with pytest.raises(OpenDVError) as exc_meta:
            pseudo.pseudonymize_chunk(chunk_meta_leak, sensitive_terms=["TermA"])
        assert exc_meta.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN

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
