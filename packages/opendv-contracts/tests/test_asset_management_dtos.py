"""Unit tests for Asset Management DTO contracts."""

import pytest
from pydantic import ValidationError

from opendv_contracts.dtos import (
    ApplyStarterPackRequest,
    AssetManagementPackId,
    ConstructRequest,
    PromoteCandidateRequest,
    RefineProposalRequest,
    SourceUploadResponse,
    StagedCandidateDTO,
    StagedCandidatesQueryResponse,
    StarterPackMetadata,
    TextSourceUploadRequest,
)


def test_starter_pack_ids_enum() -> None:
    expected_packs = {
        "core_spine",
        "public_equities_oms",
        "fixed_income_rates",
        "multi_asset_ibor",
        "private_equity_buyout",
        "private_credit_direct_lending",
        "custody_clearing",
        "all_domains",
    }
    actual = {p.value for p in AssetManagementPackId}
    assert actual == expected_packs


def test_starter_pack_metadata_roundtrip() -> None:
    meta = StarterPackMetadata(
        pack_id=AssetManagementPackId.CORE_SPINE,
        display_name="Unified Core Enterprise Spine",
        description="Foundational enterprise data vault spine",
        domain_namespace="asset_management",
        standards_references=["FIBO", "ISO 17442"],
        concept_count=6,
        relationship_count=6,
        concepts=["LegalEntity", "Account", "Agreement"],
    )
    dumped = meta.model_dump(mode="json")
    reloaded = StarterPackMetadata.model_validate(dumped)
    assert reloaded == meta

    # extra='forbid' validation
    with pytest.raises(ValidationError):
        StarterPackMetadata(
            pack_id=AssetManagementPackId.CORE_SPINE,
            display_name="test",
            description="test",
            domain_namespace="am",
            concept_count=1,
            relationship_count=1,
            unexpected_field="disallowed",
        )


def test_apply_starter_pack_request_normalization() -> None:
    # Plural list
    req1 = ApplyStarterPackRequest(pack_ids=[AssetManagementPackId.CORE_SPINE])
    assert req1.pack_ids == [AssetManagementPackId.CORE_SPINE]

    # Singular alias normalized
    req2 = ApplyStarterPackRequest(pack_id=AssetManagementPackId.ALL_DOMAINS)
    assert req2.pack_ids == [AssetManagementPackId.ALL_DOMAINS]

    # Singular 'pack' alias normalized (defense-in-depth)
    req3 = ApplyStarterPackRequest(pack=AssetManagementPackId.CORE_SPINE)
    assert req3.pack_ids == [AssetManagementPackId.CORE_SPINE]
    assert req3.pack_id == AssetManagementPackId.CORE_SPINE

    # Model validate from raw dict with 'pack'
    req4 = ApplyStarterPackRequest.model_validate({"pack": "core_spine"})
    assert req4.pack_ids == [AssetManagementPackId.CORE_SPINE]

    # Missing all raises error
    with pytest.raises(ValidationError):
        ApplyStarterPackRequest()


def test_text_source_upload_request_and_response() -> None:
    req = TextSourceUploadRequest(
        text="# Asset Management Note\nDetails about portfolio.",
        source_name="notes.md",
        format="markdown",
    )
    assert req.format == "markdown"
    assert req.do_not_send_to_agent is False

    # Empty text raises error
    with pytest.raises(ValidationError):
        TextSourceUploadRequest(text="")

    # Response model aliases
    resp = SourceUploadResponse(
        source_id="src-12345",
        source_sha256="a" * 64,
        file_name="notes.md",
        filename="notes.md",
        size_bytes=42,
        byte_count=42,
        mime_type="text/markdown",
    )
    assert resp.sha256 == "a" * 64
    assert resp.size_bytes == 42


def test_staged_candidate_and_promotion_models() -> None:
    cand = StagedCandidateDTO(
        candidate_id="staged-covenant",
        concept_name="Covenant",
        suggested_hub="hub_covenant",
        top_match_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract",
        top_match_score=0.62,
    )
    assert cand.status == "held_back"
    assert cand.reason_held_back == "GROUNDING_THRESHOLD_UNMET"

    query_resp = StagedCandidatesQueryResponse(
        session_id="sess-001",
        staged_candidates=[cand],
    )
    assert query_resp.total_count == 1
    assert len(query_resp.candidates) == 1

    # Path A promotion
    req_a = PromoteCandidateRequest(path="path_a", synonyms=["Debt Covenant", "Maintenance Test"])
    assert req_a.path == "path_a"
    assert req_a.synonyms == ["Debt Covenant", "Maintenance Test"]

    # Path B promotion normalization
    req_b = PromoteCandidateRequest(
        waiver=True,
        rationale="Exempted direct lending custom covenant structure",
    )
    assert req_b.path == "path_b"
    assert req_b.waiver_rationale == "Exempted direct lending custom covenant structure"


def test_construct_request_and_refine_request() -> None:
    construct_req = ConstructRequest(
        mode="agent",
        steering_prompt="Focus exclusively on fixed income securities and yield curves.",
    )
    assert (
        construct_req.steering_prompt
        == "Focus exclusively on fixed income securities and yield curves."
    )
    assert construct_req.mode == "agent"

    refine_req = RefineProposalRequest(
        steering_prompt="Remove benchmark and add collateral covenants",
        mode="deterministic",
    )
    assert refine_req.steering_prompt == "Remove benchmark and add collateral covenants"
    assert refine_req.mode == "deterministic"
