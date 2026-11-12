"""Unit tests for Clarification Question Generation & Answer Harness in CAS.

Verifies Task 5A.4 / Issue #214:
1. Question generation creating structured questions.json with semantic rationale options.
2. Answer validation, persistence in answers.json, and CAS blob storage.
3. Canonical JCS SHA-256 calculation for answers_sha256.
4. Error code QUESTION_NOT_FOUND (exit 3, HTTP 404) on unknown question IDs.
5. Binding constraint extraction (ClarificationConstraints) feeding construction pipelines.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from opendv_contracts.catalogs.polysemy import KNOWN_POLYSEMIC_DEFINITIONS
from opendv_contracts.dtos.clarification import (
    ClarificationAnswerItem,
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
    ClarificationQuestion,
)
from opendv_contracts.dtos.ingestion import EvidenceChunk
from opendv_contracts.dtos.ontology import EvidenceRef
from opendv_service.construction.clarification_harness import (
    EMPTY_ANSWERS_SHA256,
    ClarificationConflictError,
    ClarificationConstraints,
    ClarificationHarness,
    InvalidClarificationAnswerError,
    InvalidClarificationOptionError,
    QuestionNotFoundError,
)


def test_generate_questions_creates_questions_json(tmp_path: Path) -> None:
    """Verify generate_questions creates questions.json with semantic rationale options."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account", "Position"], max_questions=5)

    assert len(questions) == 2
    assert harness.questions_file.exists()

    raw_data = json.loads(harness.questions_file.read_text(encoding="utf-8"))
    assert len(raw_data) == 2

    # Verify options have FIBO URIs and rationales
    for q in questions:
        assert isinstance(q, ClarificationQuestion)
        assert len(q.options) >= 2
        for opt in q.options:
            assert opt.fibo_uri and opt.fibo_uri.startswith("https://spec.edmcouncil.org/fibo/")
            assert opt.rationale and len(opt.rationale) > 10


def test_save_answers_success_and_cas_storage(tmp_path: Path) -> None:
    """Verify answers are persisted to answers.json, CAS blob is created, and JCS digest matches."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    assert len(questions) == 1
    q = questions[0]
    selected_opt = q.options[0].option_id

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[selected_opt],
                user_text="Use customer accounts exclusively",
                answered_by="human:steward@example.com",
            )
        ]
    )

    resp = harness.save_answers(req)
    assert isinstance(resp, ClarificationAnswerResponse)
    assert resp.saved_count == 1
    assert len(resp.answers_sha256) == 64

    # Verify answers.json exists
    assert harness.answers_file.exists()

    # Verify CAS storage has the blob with exact digest
    cas_shard = resp.answers_sha256[:2]
    cas_rest = resp.answers_sha256[2:]
    cas_blob = tmp_path / "inputs" / "cas" / cas_shard / cas_rest
    assert cas_blob.exists(), f"CAS blob {cas_blob} does not exist"

    # Verify blob content equals JCS of answers
    saved_bytes = cas_blob.read_bytes()
    assert hashlib.sha256(saved_bytes).hexdigest() == resp.answers_sha256


def test_save_answers_unknown_question_raises_exit_3(tmp_path: Path) -> None:
    """Verify saving answer for an unknown question raises QuestionNotFoundError
    (exit 3, HTTP 404).
    """
    harness = ClarificationHarness(session_dir=tmp_path)
    harness.generate_questions(candidate_terms=["Account"])

    bad_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id="unknown-question-id-12345",
                selected_options=["opt-1"],
            )
        ]
    )

    with pytest.raises(QuestionNotFoundError) as exc_info:
        harness.save_answers(bad_req)

    err = exc_info.value
    assert err.exit_code == 3
    assert err.http_status == 404
    assert err.error_code == "QUESTION_NOT_FOUND"


def test_extract_binding_constraints(tmp_path: Path) -> None:
    """Verify binding constraints extraction properly binds preferred mappings and guidance."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account", "Position"])
    assert len(questions) == 2

    # Choose General Ledger Account for Account
    account_q = next(q for q in questions if q.focus_concept == "Account")
    gl_opt = next(opt for opt in account_q.options if "General Ledger" in opt.label)

    # Choose Financial Position for Position
    position_q = next(q for q in questions if q.focus_concept == "Position")
    sec_opt = next(opt for opt in position_q.options if "Financial Position" in opt.label)

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=account_q.question_id,
                selected_options=[gl_opt.option_id],
                user_text="Strictly GL accounts",
            ),
            ClarificationAnswerItem(
                question_id=position_q.question_id,
                selected_options=[sec_opt.option_id],
            ),
        ]
    )

    harness.save_answers(req)
    constraints = harness.get_binding_constraints()
    assert isinstance(constraints, ClarificationConstraints)

    # Preferred mappings should map both slug and title
    assert constraints.preferred_mappings.get("account") == gl_opt.fibo_uri
    assert constraints.preferred_mappings.get("position") == sec_opt.fibo_uri
    assert constraints.custom_guidance.get("account") == "Strictly GL accounts"


def test_binding_constraints_extracts_preferred_mappings_and_guidance(tmp_path: Path) -> None:
    """Verify extracted binding constraints contain preferred mappings and custom guidance."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    assert len(questions) == 1
    q = questions[0]
    cust_opt = next(opt for opt in q.options if "Customer" in opt.label)

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[cust_opt.option_id],
                user_text="Use customer account mapping",
            )
        ]
    )
    harness.save_answers(req)
    constraints = harness.get_binding_constraints()

    assert constraints.preferred_mappings["account"] == cust_opt.fibo_uri
    assert constraints.preferred_mappings["Account"] == cust_opt.fibo_uri
    assert constraints.custom_guidance["account"] == "Use customer account mapping"
    assert constraints.selected_option_ids[q.question_id] == [cust_opt.option_id]


def test_save_answers_merges_multiple_submissions_without_data_loss(tmp_path: Path) -> None:
    """Verify saving answers across multiple submissions merges without data loss (CRIT-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account", "Position"])
    assert len(questions) == 2

    account_q = next(q for q in questions if q.focus_concept == "Account")
    position_q = next(q for q in questions if q.focus_concept == "Position")

    # Submission 1: answer Account question only
    req1 = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=account_q.question_id,
                selected_options=[account_q.options[0].option_id],
                user_text="GL account selection",
            )
        ]
    )
    resp1 = harness.save_answers(req1)
    assert resp1.saved_count == 1

    # Verify 1 answer present in file and constraints
    c1 = harness.get_binding_constraints()
    assert "account" in c1.preferred_mappings
    assert "position" not in c1.preferred_mappings

    # Submission 2: answer Position question subsequently
    req2 = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=position_q.question_id,
                selected_options=[position_q.options[0].option_id],
                user_text="Position selection",
            )
        ]
    )
    resp2 = harness.save_answers(req2)
    assert resp2.saved_count == 1

    # Verify BOTH answers are merged in answers.json (no data loss)
    raw_answers = json.loads(harness.answers_file.read_text(encoding="utf-8"))
    assert len(raw_answers) == 2
    answered_ids = {item["question_id"] for item in raw_answers}
    assert account_q.question_id in answered_ids
    assert position_q.question_id in answered_ids

    # Verify constraints contain both preferred mappings
    c2 = harness.get_binding_constraints()
    assert "account" in c2.preferred_mappings
    assert "position" in c2.preferred_mappings
    assert c2.custom_guidance["account"] == "GL account selection"
    assert c2.custom_guidance["position"] == "Position selection"

    # Verify CAS storage has the merged blob matching resp2 digest
    cas_shard = resp2.answers_sha256[:2]
    cas_rest = resp2.answers_sha256[2:]
    cas_blob = tmp_path / "inputs" / "cas" / cas_shard / cas_rest
    assert cas_blob.exists()
    assert hashlib.sha256(cas_blob.read_bytes()).hexdigest() == resp2.answers_sha256


def test_generate_questions_evidence_chunks_word_boundary(tmp_path: Path) -> None:
    """Verify evidence chunk text polysemy matching enforces word boundaries (MIN-02)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    # Text contains 'composition' which should NOT match 'position'
    chunk = EvidenceChunk(
        chunk_id="chk-001",
        document_id="doc-001",
        text="The committee evaluated the compositional makeup of the board.",
    )
    questions = harness.generate_questions(evidence_chunks=[chunk])
    assert len(questions) == 0

    # Text contains 'position' as a discrete word
    chunk_pos = EvidenceChunk(
        chunk_id="chk-002",
        document_id="doc-001",
        text="The fund established a significant position in foreign exchange.",
    )
    q_pos = harness.generate_questions(evidence_chunks=[chunk_pos])
    assert len(q_pos) == 1
    assert q_pos[0].focus_concept == "Position"


def test_generate_questions_skips_dict_terms_without_term_key(tmp_path: Path) -> None:
    """Verify candidate term dictionaries lacking a valid 'term' key are skipped (MIN-08)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    terms: list[str | dict[str, Any]] = [
        {"invalid": "no_term_key"},
        {"term": ""},
        {"term": "Account"},
    ]
    questions = harness.generate_questions(candidate_terms=terms)
    assert len(questions) == 1
    assert questions[0].focus_concept == "Account"


def test_polysemy_catalog_uris_resolve_in_fibo_pack() -> None:
    """Verify all polysemic definitions catalog URIs resolve in the pinned FIBO pack (MIN-05)."""
    from opendv_sources.fibo.loader import get_default_fibo_loader

    loader = get_default_fibo_loader()
    loader.load()
    classes = loader.get_classes()

    for term, options in KNOWN_POLYSEMIC_DEFINITIONS.items():
        assert len(options) >= 2, f"Term '{term}' should have at least 2 polysemic options"
        for opt in options:
            fibo_uri = opt.get("fibo_uri")
            assert fibo_uri is not None, f"Option '{opt['label']}' missing fibo_uri"
            assert fibo_uri in classes, (
                f"Catalog URI '{fibo_uri}' for '{opt['label']}' "
                "does not resolve in pinned FIBO pack"
            )


def test_multi_select_option_priority(tmp_path: Path) -> None:
    """Verify multi-select answers prioritize first selected option mapping (SUG-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]
    assert len(q.options) >= 2

    # Select both option 1 and option 2
    opt1, opt2 = q.options[0], q.options[1]
    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[opt1.option_id, opt2.option_id],
            )
        ]
    )
    harness.save_answers(req)
    constraints = harness.get_binding_constraints()
    assert constraints.preferred_mappings["account"] == opt1.fibo_uri


def test_save_answers_invalid_option_id_raises_invalid_option_error(tmp_path: Path) -> None:
    """Verify selecting an unknown option_id raises InvalidClarificationOptionError
    (exit 2, HTTP 422).
    """
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    bad_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=["nonexistent-option-id-999"],
            )
        ]
    )

    with pytest.raises(InvalidClarificationOptionError) as exc_info:
        harness.save_answers(bad_req)

    err = exc_info.value
    assert err.exit_code == 2
    assert err.http_status == 400
    assert err.error_code == "SYNTAX_INVALID_ARGUMENT"
    assert err.to_problem_detail().status == 400
    assert "Invalid option 'nonexistent-option-id-999'" in str(err)


def test_generate_questions_word_boundary_avoids_substring_false_positive(tmp_path: Path) -> None:
    """Verify 'Composition' does not match 'position', while 'Account Type' matches 'account'."""
    harness = ClarificationHarness(session_dir=tmp_path)
    # Composition should NOT trigger polysemy question for 'position'
    q_comp = harness.generate_questions(candidate_terms=["Composition"])
    assert len(q_comp) == 0

    # Account Type should trigger polysemy question for 'account'
    h2 = ClarificationHarness(session_dir=tmp_path / "h2")
    q_acct = h2.generate_questions(candidate_terms=["Account Type"])
    assert len(q_acct) == 1
    assert q_acct[0].focus_concept == "Account Type"


def test_corrupt_questions_json_raises_runtime_error(tmp_path: Path) -> None:
    """Verify get_questions raises RuntimeError if existing questions.json is corrupt."""
    harness = ClarificationHarness(session_dir=tmp_path)
    harness.questions_file.write_text("{corrupt json!", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"questions\.json is corrupt"):
        harness.get_questions()


def test_save_answers_empty_or_disallowed_custom_text(tmp_path: Path) -> None:
    """Verify answer without selected_options or user_text is rejected, and
    custom text on non-custom questions is rejected.
    """
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    # Empty answer: neither option nor text
    empty_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[],
                user_text=None,
            )
        ]
    )
    with pytest.raises(
        ValueError, match="must provide either selected_options or non-empty user_text"
    ):
        harness.save_answers(empty_req)

    # Disallow custom text on question
    q_no_custom = q.model_copy(update={"allow_custom_text": False})
    harness.questions_file.write_text(
        json.dumps([q_no_custom.model_dump(mode="json")]), encoding="utf-8"
    )

    disallowed_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
                user_text="Custom comment not allowed",
            )
        ]
    )
    with pytest.raises(ValueError, match="does not permit custom user text"):
        harness.save_answers(disallowed_req)


def test_corrupt_answers_json_raises_runtime_error_in_constraints(tmp_path: Path) -> None:
    """Verify get_binding_constraints raises RuntimeError if answers.json is corrupt (MIN-05)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    harness.generate_questions(candidate_terms=["Account"])
    # Write garbage to answers.json
    harness.answers_file.write_text("{bad json!", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"answers\.json is corrupt"):
        harness.get_binding_constraints()


def test_jcs_digest_invariance_under_answer_ordering(tmp_path: Path) -> None:
    """Verify answers_sha256 is canonically invariant regardless of answer insertion order."""
    h1 = ClarificationHarness(session_dir=tmp_path / "s1")
    h2 = ClarificationHarness(session_dir=tmp_path / "s2")

    q1 = h1.generate_questions(candidate_terms=["Account", "Position"])
    q2 = h2.generate_questions(candidate_terms=["Account", "Position"])

    # Order A: Account first, then Position
    req_a = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q1[0].question_id,
                selected_options=[q1[0].options[0].option_id],
                answered_at="2026-11-10T08:00:00Z",
            ),
            ClarificationAnswerItem(
                question_id=q1[1].question_id,
                selected_options=[q1[1].options[0].option_id],
                answered_at="2026-11-10T08:00:00Z",
            ),
        ]
    )
    # Order B: Position first, then Account
    req_b = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q2[1].question_id,
                selected_options=[q2[1].options[0].option_id],
                answered_at="2026-11-10T08:00:00Z",
            ),
            ClarificationAnswerItem(
                question_id=q2[0].question_id,
                selected_options=[q2[0].options[0].option_id],
                answered_at="2026-11-10T08:00:00Z",
            ),
        ]
    )

    resp_a = h1.save_answers(req_a)
    resp_b = h2.save_answers(req_b)

    assert resp_a.answers_sha256 == resp_b.answers_sha256


def test_save_answers_occ_precondition_success_and_conflict(tmp_path: Path) -> None:
    """Verify OCC precondition check (ONT-EVO-003): matching expected_answers_sha256
    succeeds, while stale/mismatched digest raises ClarificationConflictError (exit 1, HTTP 409).
    """
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account", "Position"])
    assert len(questions) == 2
    q_acct, q_pos = questions[0], questions[1]

    # Save initial answer without precondition
    req1 = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q_acct.question_id,
                selected_options=[q_acct.options[0].option_id],
            )
        ]
    )
    resp1 = harness.save_answers(req1)
    base_sha256 = resp1.answers_sha256

    # Successful OCC update specifying matching expected_answers_sha256
    req2_ok = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q_pos.question_id,
                selected_options=[q_pos.options[0].option_id],
            )
        ],
        expected_answers_sha256=base_sha256,
    )
    resp2 = harness.save_answers(req2_ok)
    assert resp2.saved_count == 1
    assert resp2.answers_sha256 != base_sha256

    # Conflicting concurrent update specifying stale expected_answers_sha256
    req3_stale = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q_pos.question_id,
                selected_options=[q_pos.options[1].option_id],
            )
        ],
        expected_answers_sha256=base_sha256,  # Stale!
    )
    with pytest.raises(ClarificationConflictError) as exc_info:
        harness.save_answers(req3_stale)

    err = exc_info.value
    assert err.exit_code == 5
    assert err.http_status == 412
    assert err.error_code == "ONT_EVO_CONCURRENT_MODIFICATION"
    assert err.to_problem_detail().status == 412
    assert "OCC precondition failed" in str(err)


def test_save_answers_duplicate_question_id_in_request_rejected(tmp_path: Path) -> None:
    """Verify duplicate question_id in one request raises InvalidClarificationAnswerError."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    dup_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
            ),
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[1].option_id],
            ),
        ]
    )

    with pytest.raises(InvalidClarificationAnswerError) as exc_info:
        harness.save_answers(dup_req)

    err = exc_info.value
    assert err.exit_code == 2
    assert err.http_status == 400
    assert err.error_code == "SYNTAX_INVALID_ARGUMENT"
    assert err.to_problem_detail().status == 400
    assert "Duplicate answer provided" in str(err)


def test_save_answers_duplicate_option_ids_rejected(tmp_path: Path) -> None:
    """Verify duplicate option IDs raises InvalidClarificationOptionError (MIN-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]
    opt_id = q.options[0].option_id

    dup_opt_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[opt_id, opt_id],
            )
        ]
    )

    with pytest.raises(InvalidClarificationOptionError) as exc_info:
        harness.save_answers(dup_opt_req)

    err = exc_info.value
    assert err.exit_code == 2
    assert err.http_status == 400
    assert err.error_code == "SYNTAX_INVALID_ARGUMENT"
    assert "Duplicate option IDs" in str(err)


def test_save_answers_question_without_options_rejects_selected_options(tmp_path: Path) -> None:
    """Verify selecting options for a question that defines no options is rejected (MIN-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    q_no_opts = ClarificationQuestion(
        question_id="q-freeform-01",
        focus_concept="Freeform Concept",
        question_text="Please describe freeform concept",
        options=[],
        allow_custom_text=True,
    )
    harness.questions_file.write_text(
        json.dumps([q_no_opts.model_dump(mode="json")]), encoding="utf-8"
    )

    bad_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id="q-freeform-01",
                selected_options=["opt-1"],
            )
        ]
    )

    with pytest.raises(InvalidClarificationOptionError) as exc_info:
        harness.save_answers(bad_req)

    err = exc_info.value
    assert err.exit_code == 2
    assert err.http_status == 400
    assert "has no predefined options" in str(err)


def test_generate_questions_populates_evidence_citations(tmp_path: Path) -> None:
    """Verify questions prompted by evidence chunks populate evidence_citations (MIN-04)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    chunk = EvidenceChunk(
        chunk_id="chk-pos-99",
        document_id="doc-trade-01",
        text="The firm opened a large position in equity derivatives.",
        confidence=0.98,
        start_char=10,
        end_char=65,
    )
    questions = harness.generate_questions(evidence_chunks=[chunk])
    assert len(questions) == 1
    q = questions[0]
    assert q.focus_concept == "Position"
    assert len(q.evidence_citations) == 1

    citation = q.evidence_citations[0]
    assert isinstance(citation, EvidenceRef)
    assert citation.evidence_type == "document_span"
    assert citation.reference_uri == "doc-trade-01#chk-pos-99"
    assert citation.confidence == 0.98
    assert citation.char_start == 10
    assert citation.char_end == 65
    assert "position" in citation.snippet.lower()


def test_save_answers_invalid_answered_at_timestamp_raises_422(tmp_path: Path) -> None:
    """Verify invalid ISO-8601 timestamp raises InvalidClarificationAnswerError (MIN-03)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    bad_ts_req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
                answered_at="not-an-iso-8601-timestamp",
            )
        ]
    )

    with pytest.raises(InvalidClarificationAnswerError) as exc_info:
        harness.save_answers(bad_ts_req)

    err = exc_info.value
    assert err.exit_code == 2
    assert err.http_status == 400
    assert "Invalid ISO-8601 timestamp" in str(err)


def test_generate_questions_does_not_rewrite_file_when_unmodified(tmp_path: Path) -> None:
    """Verify generate_questions does not rewrite questions.json if no new questions (SUG-02)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    harness.generate_questions(candidate_terms=["Account"])
    assert harness.questions_file.exists()

    mtime_initial = harness.questions_file.stat().st_mtime_ns

    # Call again with the same term: new_questions_count == 0
    harness.generate_questions(candidate_terms=["Account"])
    mtime_after = harness.questions_file.stat().st_mtime_ns

    assert mtime_initial == mtime_after


def test_cas_retrieval_via_api(tmp_path: Path) -> None:
    """Verify stored answer blob is retrievable via CAS API directly (SUG-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
            )
        ]
    )
    resp = harness.save_answers(req)

    # Fetch directly from CAS instance
    cas_blob = harness.cas.get(resp.answers_sha256)
    assert cas_blob is not None
    assert hashlib.sha256(cas_blob).hexdigest() == resp.answers_sha256


def test_binding_constraints_exact_catalog_match_priority_over_compound_term(
    tmp_path: Path,
) -> None:
    """Verify exact match 'Account' takes priority over 'Account Type' for 'account' (MAJ-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account", "Account Type"])
    assert len(questions) == 2

    q_acct = next(q for q in questions if q.focus_concept == "Account")
    q_acct_type = next(q for q in questions if q.focus_concept == "Account Type")

    gl_opt = next(opt for opt in q_acct.options if "General Ledger" in opt.label)
    cust_opt = next(opt for opt in q_acct_type.options if "Customer" in opt.label)

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q_acct_type.question_id,
                selected_options=[cust_opt.option_id],
                user_text="Customer account for account type",
            ),
            ClarificationAnswerItem(
                question_id=q_acct.question_id,
                selected_options=[gl_opt.option_id],
                user_text="GL account for account",
            ),
        ]
    )
    harness.save_answers(req)
    constraints = harness.get_binding_constraints()

    # Exact match for 'account' catalog key MUST be LedgerAccount
    assert constraints.preferred_mappings["account"] == gl_opt.fibo_uri
    assert constraints.custom_guidance["account"] == "GL account for account"

    # Specific compound term 'account_type' MUST be CustomerAccount
    assert constraints.preferred_mappings["account_type"] == cust_opt.fibo_uri
    assert constraints.custom_guidance["account_type"] == "Customer account for account type"


def test_save_answers_naive_timestamp_treated_as_utc(tmp_path: Path) -> None:
    """Verify naive timestamp is deterministically treated as UTC without host shift (MAJ-02)."""
    h1 = ClarificationHarness(session_dir=tmp_path / "s1")
    h2 = ClarificationHarness(session_dir=tmp_path / "s2")

    q1 = h1.generate_questions(candidate_terms=["Account"])[0]
    q2 = h2.generate_questions(candidate_terms=["Account"])[0]

    # Submit naive timestamp to h1
    resp1 = h1.save_answers(
        ClarificationAnswerRequest(
            answers=[
                ClarificationAnswerItem(
                    question_id=q1.question_id,
                    selected_options=[q1.options[0].option_id],
                    answered_at="2026-11-10T08:00:00",
                )
            ]
        )
    )

    # Submit explicit UTC timestamp to h2
    resp2 = h2.save_answers(
        ClarificationAnswerRequest(
            answers=[
                ClarificationAnswerItem(
                    question_id=q2.question_id,
                    selected_options=[q2.options[0].option_id],
                    answered_at="2026-11-10T08:00:00Z",
                )
            ]
        )
    )

    # Digests and stored answered_at MUST match bit-for-bit
    assert resp1.answers_sha256 == resp2.answers_sha256

    saved_raw = json.loads(h1.answers_file.read_text(encoding="utf-8"))
    assert saved_raw[0]["answered_at"] == "2026-11-10T08:00:00Z"


def test_save_answers_empty_request_rejected(tmp_path: Path) -> None:
    """Verify empty answers list in ClarificationAnswerRequest is rejected (MIN-01)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    with pytest.raises(InvalidClarificationAnswerError) as exc_info:
        harness.save_answers(ClarificationAnswerRequest(answers=[]))
    assert exc_info.value.exit_code == 2
    assert exc_info.value.http_status == 400


def test_generate_questions_max_questions_validation(tmp_path: Path) -> None:
    """Verify max_questions < 1 raises ValueError (MIN-06)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    with pytest.raises(ValueError, match="max_questions must be at least 1"):
        harness.generate_questions(candidate_terms=["Account"], max_questions=0)


def test_generate_questions_preserves_camel_case_in_focus_concept(tmp_path: Path) -> None:
    """Verify camelCase terms preserve casing in focus_concept and map to slug (MIN-02)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["CustomerAccount"])
    assert len(questions) == 1
    q = questions[0]
    assert q.focus_concept == "CustomerAccount"

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
            )
        ]
    )
    harness.save_answers(req)
    constraints = harness.get_binding_constraints()
    assert "customer_account" in constraints.preferred_mappings
    assert "CustomerAccount" in constraints.preferred_mappings


def test_construction_package_star_import_smoke() -> None:
    """Verify from opendv_service.construction import * works cleanly (MIN-01)."""
    import opendv_service.construction as mod

    for name in mod.__all__:
        assert hasattr(mod, name), f"Missing exported attribute {name}"


def test_get_answers_and_sha256_accessor_and_empty_state_occ(tmp_path: Path) -> None:
    """Verify get_answers and get_answers_sha256 return EMPTY_ANSWERS_SHA256 for empty state,
    and submitting expected_answers_sha256=EMPTY_ANSWERS_SHA256 succeeds on first write (MAJ-01).
    """
    harness = ClarificationHarness(session_dir=tmp_path)
    answers, digest = harness.get_answers()
    assert answers == []
    assert digest == EMPTY_ANSWERS_SHA256
    assert harness.get_answers_sha256() == EMPTY_ANSWERS_SHA256

    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    # First write with matching expected_answers_sha256 = EMPTY_ANSWERS_SHA256 succeeds
    req_first = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
            )
        ],
        expected_answers_sha256=EMPTY_ANSWERS_SHA256,
    )
    resp = harness.save_answers(req_first)
    assert resp.saved_count == 1
    assert resp.answers_sha256 != EMPTY_ANSWERS_SHA256

    # Accessor now returns the stored answers and updated digest
    saved_answers, updated_digest = harness.get_answers()
    assert len(saved_answers) == 1
    assert updated_digest == resp.answers_sha256
    assert harness.get_answers_sha256() == resp.answers_sha256


def test_concurrent_first_write_occ_conflict(tmp_path: Path) -> None:
    """Verify concurrent first writes expecting EMPTY_ANSWERS_SHA256 results in 409 (MAJ-01)."""
    h1 = ClarificationHarness(session_dir=tmp_path)
    questions = h1.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    # Client 1 submits first write with expected_answers_sha256=EMPTY_ANSWERS_SHA256
    req1 = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
            )
        ],
        expected_answers_sha256=EMPTY_ANSWERS_SHA256,
    )
    resp1 = h1.save_answers(req1)
    assert resp1.saved_count == 1

    # Client 2 also tries to submit first write expecting EMPTY_ANSWERS_SHA256 (concurrent race)
    req2 = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[1].option_id],
            )
        ],
        expected_answers_sha256=EMPTY_ANSWERS_SHA256,
    )
    with pytest.raises(ClarificationConflictError) as exc_info:
        h1.save_answers(req2)
    assert exc_info.value.exit_code == 5
    assert exc_info.value.http_status == 412
    assert exc_info.value.error_code == "ONT_EVO_CONCURRENT_MODIFICATION"
    assert exc_info.value.to_problem_detail().status == 412


def test_whitespace_only_user_text_normalized_to_none(tmp_path: Path) -> None:
    """Verify whitespace-only user_text is normalized to None and excluded (MIN-02)."""
    harness = ClarificationHarness(session_dir=tmp_path)
    questions = harness.generate_questions(candidate_terms=["Account"])
    q = questions[0]

    req = ClarificationAnswerRequest(
        answers=[
            ClarificationAnswerItem(
                question_id=q.question_id,
                selected_options=[q.options[0].option_id],
                user_text="   \t   \n  ",
            )
        ]
    )
    harness.save_answers(req)
    answers, _ = harness.get_answers()
    assert answers[0].user_text is None

    constraints = harness.get_binding_constraints()
    assert "account" not in constraints.custom_guidance


def test_question_not_found_str_returns_plain_message() -> None:
    """Verify str(QuestionNotFoundError) returns plain message without repr quotes (MIN-06)."""
    err = QuestionNotFoundError("Question q-123 not found")
    assert str(err) == "Question q-123 not found"
