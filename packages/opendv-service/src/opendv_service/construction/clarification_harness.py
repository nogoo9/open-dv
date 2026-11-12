"""Clarification question generation and answer harness with CAS persistence.

Conforms to docs/19-ontology-studio-architecture.md (§5.4),
docs/20-ontology-studio-spec.md, and docs/21-ontology-studio-implementation-plan.md (§4.1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import Field, ValidationError

from opendv_contracts.catalogs.polysemy import KNOWN_POLYSEMIC_DEFINITIONS
from opendv_contracts.dtos.clarification import (
    ClarificationAnswerItem,
    ClarificationAnswerRequest,
    ClarificationAnswerResponse,
    ClarificationOption,
    ClarificationQuestion,
)
from opendv_contracts.dtos.common import BaseContractModel
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import EvidenceRef
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.jcs import dumps_jcs
from opendv_service.construction.utils import atomic_write_text
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.locks import LockLevel, studio_lock

logger = logging.getLogger(__name__)

EMPTY_ANSWERS_SHA256: Final[str] = hashlib.sha256(dumps_jcs([]).encode("utf-8")).hexdigest()


class QuestionNotFoundError(OpenDVError, KeyError):
    """Raised when an answered question ID does not exist in questions.json."""

    error_code: ErrorCode = ErrorCode.QUESTION_NOT_FOUND

    def __init__(self, message: str) -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.QUESTION_NOT_FOUND)

    def __str__(self) -> str:
        return self.message


class InvalidClarificationOptionError(OpenDVError, ValueError):
    """Raised when an answered option ID is not valid for the question."""

    error_code: ErrorCode = ErrorCode.SYNTAX_INVALID_ARGUMENT

    def __init__(self, message: str) -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)


class InvalidClarificationAnswerError(OpenDVError, ValueError):
    """Raised when an answer item in request fails validation constraints."""

    error_code: ErrorCode = ErrorCode.SYNTAX_INVALID_ARGUMENT

    def __init__(self, message: str) -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT)


class ClarificationConflictError(OpenDVError):
    """Raised when an OCC precondition check fails due to concurrent modification (ONT-EVO-003)."""

    error_code: ErrorCode = ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION

    def __init__(self, message: str) -> None:
        OpenDVError.__init__(self, message, error_code=ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION)


def concept_to_slug(term: str) -> str:
    """Normalize concept term to a canonical snake_case slug."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", term)
    return re.sub(r"[^a-z0-9_-]+", "_", s.lower()).strip("_")


def tokenize_concept(term: str) -> list[str]:
    """Tokenize a concept name or column header into lowercase token words."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", term)
    return [t.lower() for t in re.split(r"[^a-zA-Z0-9]+", s) if t]


class ClarificationConstraints(BaseContractModel):
    """Extracted semantic constraints from accepted steward clarification answers."""

    preferred_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="Concept name or slug to preferred FIBO URI mapping",
    )
    custom_guidance: dict[str, str] = Field(
        default_factory=dict,
        description="Concept name or slug to free-form steward guidance",
    )
    selected_option_ids: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Question ID to selected option IDs mapping",
    )


class ClarificationHarness:
    """Manages clarification question lifecycle, answer persistence in CAS,
    and constraint extraction.
    """

    def __init__(
        self,
        session_dir: Path | str,
        cas: ContentAddressableStorage | None = None,
    ) -> None:
        self.session_dir = Path(session_dir).resolve()
        self.clarifications_dir = self.session_dir / "clarifications"
        self.clarifications_dir.mkdir(parents=True, exist_ok=True)
        self.questions_file = self.clarifications_dir / "questions.json"
        self.answers_file = self.clarifications_dir / "answers.json"
        self.cas = cas or ContentAddressableStorage(root_dir=self.session_dir / "inputs")

    def generate_questions(
        self,
        candidate_terms: Sequence[str | dict[str, Any]] | None = None,
        evidence_chunks: Sequence[EvidenceChunk] | None = None,
        tables: Sequence[TableStructure] | None = None,
        max_questions: int = 5,
    ) -> list[ClarificationQuestion]:
        """Generate structured clarification questions and persist to questions.json."""
        if max_questions < 1:
            raise ValueError(f"max_questions must be at least 1, got {max_questions}")

        lock_file = self.session_dir / ".session.lock"
        with studio_lock(lock_file, level=LockLevel.L5, session_id=self.session_dir.name):
            terms_to_evaluate: list[str | dict[str, Any]] = list(candidate_terms or [])
            evidence_citations_by_key: dict[str, list[EvidenceRef]] = {}

            # If evidence chunks provided, map citations to polysemic catalog keys (MIN-04)
            if evidence_chunks:
                for chunk in evidence_chunks:
                    chunk_text_lower = chunk.text.lower()
                    for poly_key in KNOWN_POLYSEMIC_DEFINITIONS:
                        if re.search(rf"\b{re.escape(poly_key.lower())}\b", chunk_text_lower):
                            ref = EvidenceRef(
                                evidence_type="document_span",
                                reference_uri=f"{chunk.document_id}#{chunk.chunk_id}",
                                snippet=chunk.text[:200],
                                confidence=chunk.confidence,
                                char_start=chunk.start_char,
                                char_end=chunk.end_char,
                            )
                            evidence_citations_by_key.setdefault(poly_key, []).append(ref)
                            if not candidate_terms:
                                terms_to_evaluate.append(poly_key.title())

            # If candidate terms not provided, extract from tables
            if not candidate_terms and not terms_to_evaluate and tables:
                for tbl in tables:
                    for h in tbl.headers or []:
                        clean = h.strip()
                        if clean.lower().endswith(("_id", "_pk", "_key")):
                            t = (
                                clean.lower()
                                .removesuffix("_id")
                                .removesuffix("_pk")
                                .removesuffix("_key")
                                .replace("_", " ")
                                .title()
                            )
                            terms_to_evaluate.append(t)
                        else:
                            terms_to_evaluate.append(clean.replace("_", " ").title())

            # De-duplicate terms preserving order
            unique_terms: list[str | dict[str, Any]] = []
            seen_keys: set[str] = set()
            for item in terms_to_evaluate:
                if isinstance(item, dict):
                    term_val = item.get("term")
                    if not term_val or not isinstance(term_val, str) or not term_val.strip():
                        logger.debug(
                            "Skipping dict candidate term without valid 'term' key: %s", item
                        )
                        continue
                    k = term_val.lower().strip()
                else:
                    k = str(item).lower().strip()
                if k not in seen_keys:
                    seen_keys.add(k)
                    unique_terms.append(item)

            # Load existing questions to merge without clobbering
            existing_questions = {q.question_id: q for q in self.get_questions()}
            new_questions_count = 0

            for item in unique_terms:
                if new_questions_count >= max_questions:
                    break
                if isinstance(item, dict):
                    term_str = str(item.get("term", "")).strip()
                else:
                    term_str = str(item).strip()
                if not term_str:
                    continue
                term_lower = term_str.lower().strip()
                term_slug = concept_to_slug(term_str)
                term_word_list = tokenize_concept(term_str)
                term_tokens = set(term_word_list)

                matched_key = None
                # 1. Exact slug match first (supports multi-token keys)
                for poly_key in KNOWN_POLYSEMIC_DEFINITIONS:
                    if concept_to_slug(poly_key) == term_slug:
                        matched_key = poly_key
                        break
                # 2. Multi-token contiguous phrase match, then single-token word match
                if matched_key is None:
                    for poly_key in sorted(
                        KNOWN_POLYSEMIC_DEFINITIONS.keys(),
                        key=lambda k: len(tokenize_concept(k)),
                        reverse=True,
                    ):
                        poly_words = tokenize_concept(poly_key)
                        if not poly_words:
                            continue
                        if len(poly_words) >= 2:
                            n = len(poly_words)
                            if any(
                                term_word_list[i : i + n] == poly_words
                                for i in range(len(term_word_list) - n + 1)
                            ):
                                matched_key = poly_key
                                break
                        elif poly_words[0] in term_tokens:
                            matched_key = poly_key
                            break

                if not matched_key:
                    continue

                options_data = KNOWN_POLYSEMIC_DEFINITIONS[matched_key]
                # Sanitize slug to valid identifier characters capped at 40 chars
                slug = concept_to_slug(term_str)[:40]
                # Deterministic question ID derived from full term and options (MIN-05)
                hash_src = f"{term_lower}:{':'.join(opt['label'] for opt in options_data)}"
                hash_suffix = hashlib.sha256(hash_src.encode("utf-8")).hexdigest()[:8]
                qid = f"q-poly-{slug}-{hash_suffix}"

                if qid in existing_questions:
                    continue

                options = [
                    ClarificationOption(
                        option_id=f"opt-{idx + 1}",
                        label=opt["label"],
                        fibo_uri=opt.get("fibo_uri"),
                        rationale=opt["rationale"],
                    )
                    for idx, opt in enumerate(options_data)
                ]
                q = ClarificationQuestion(
                    question_id=qid,
                    focus_concept=term_str,
                    question_text=(
                        f"The term '{term_str}' appears polysemic in financial "
                        "ontology modeling. Which semantic definition best aligns "
                        "with your domain context?"
                    ),
                    evidence_citations=evidence_citations_by_key.get(matched_key, []),
                    options=options,
                    allow_custom_text=True,
                    matched_key=matched_key,
                )
                existing_questions[qid] = q
                new_questions_count += 1

            # Persist questions.json atomically only when questions changed or file missing (SUG-02)
            if new_questions_count > 0 or not self.questions_file.exists():
                payload = [
                    q.model_dump(mode="json", exclude_none=True)
                    for q in existing_questions.values()
                ]
                atomic_write_text(self.questions_file, json.dumps(payload, indent=2))
            return list(existing_questions.values())

    def get_questions(self) -> list[ClarificationQuestion]:
        """Load currently stored clarification questions."""
        if not self.questions_file.exists():
            return []
        try:
            raw = json.loads(self.questions_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [ClarificationQuestion.model_validate(item) for item in raw]
            if isinstance(raw, dict) and "questions" in raw and isinstance(raw["questions"], list):
                return [ClarificationQuestion.model_validate(item) for item in raw["questions"]]
            raise ValueError(f"Unexpected JSON structure in {self.questions_file}")
        except (
            OSError,
            json.JSONDecodeError,
            ValidationError,
            KeyError,
            TypeError,
            ValueError,
        ) as err:
            raise RuntimeError(
                f"Cannot load clarification questions: questions.json is corrupt at "
                f"{self.questions_file}: {err}"
            ) from err

    def get_answers(self) -> tuple[list[ClarificationAnswerItem], str]:
        """Load stored answers and compute canonical RFC 8785 JCS SHA-256 digest (MAJ-01).

        Returns:
            Tuple of (list of sorted answers, canonical answers_sha256).
            If no answers exist or answers.json is absent, returns ([], EMPTY_ANSWERS_SHA256).
        """
        lock_file = self.session_dir / ".session.lock"
        with studio_lock(lock_file, level=LockLevel.L5, session_id=self.session_dir.name):
            if not self.answers_file.exists():
                return [], EMPTY_ANSWERS_SHA256
            try:
                raw_answers = json.loads(self.answers_file.read_text(encoding="utf-8"))
                item_list = (
                    raw_answers if isinstance(raw_answers, list) else raw_answers.get("answers", [])
                )
                answers = sorted(
                    [ClarificationAnswerItem.model_validate(item) for item in item_list],
                    key=lambda a: a.question_id,
                )
                jcs_payload = [a.model_dump(mode="json", exclude_none=True) for a in answers]
                digest = hashlib.sha256(dumps_jcs(jcs_payload).encode("utf-8")).hexdigest()
                return answers, digest
            except (
                OSError,
                json.JSONDecodeError,
                ValidationError,
                AttributeError,
                TypeError,
            ) as err:
                raise RuntimeError(
                    f"Cannot load answers: answers.json is corrupt at {self.answers_file}: {err}"
                ) from err

    def get_answers_sha256(self) -> str:
        """Return the current canonical JCS SHA-256 digest of stored answers (MAJ-01)."""
        _, digest = self.get_answers()
        return digest

    def save_answers(
        self,
        request: ClarificationAnswerRequest,
    ) -> ClarificationAnswerResponse:
        """Validate, persist answers in answers.json and CAS, returning canonical digest.

        Raises:
            QuestionNotFoundError: If any answered question_id does not exist in questions.json.
            InvalidClarificationOptionError: If selected option IDs are invalid or duplicated.
            InvalidClarificationAnswerError: If answer payload or timestamp fails validation.
            ClarificationConflictError: If OCC precondition check fails (ONT-EVO-003).
            RuntimeError: If existing answers.json exists but is corrupt.
        """
        if not request.answers:
            raise InvalidClarificationAnswerError(
                "ClarificationAnswerRequest.answers must contain at least one answer item."
            )

        lock_file = self.session_dir / ".session.lock"
        with studio_lock(lock_file, level=LockLevel.L5, session_id=self.session_dir.name):
            # Load existing answers to merge and compute current digest for OCC
            merged_answers: dict[str, ClarificationAnswerItem] = {}
            if self.answers_file.exists():
                try:
                    raw_answers = json.loads(self.answers_file.read_text(encoding="utf-8"))
                    if isinstance(raw_answers, list):
                        item_list = raw_answers
                    elif (
                        isinstance(raw_answers, dict)
                        and "answers" in raw_answers
                        and isinstance(raw_answers["answers"], list)
                    ):
                        item_list = raw_answers["answers"]
                    else:
                        raise TypeError("Expected list or dict with 'answers' list in answers.json")
                    for item in item_list:
                        ans = ClarificationAnswerItem.model_validate(item)
                        merged_answers[ans.question_id] = ans
                except (
                    OSError,
                    json.JSONDecodeError,
                    ValidationError,
                    AttributeError,
                    TypeError,
                ) as err:
                    raise RuntimeError(
                        "Cannot save answers: existing answers.json is corrupt at "
                        f"{self.answers_file}: {err}"
                    ) from err

            # Always compute current_sha256 (canonical empty digest when empty/missing) (MAJ-01)
            if merged_answers:
                sorted_existing = sorted(merged_answers.values(), key=lambda a: a.question_id)
                existing_jcs = dumps_jcs(
                    [a.model_dump(mode="json", exclude_none=True) for a in sorted_existing]
                )
                current_sha256 = hashlib.sha256(existing_jcs.encode("utf-8")).hexdigest()
            else:
                current_sha256 = EMPTY_ANSWERS_SHA256

            if (
                request.expected_answers_sha256 is not None
                and request.expected_answers_sha256 != current_sha256
            ):
                raise ClarificationConflictError(
                    f"OCC precondition failed: expected_answers_sha256 was "
                    f"'{request.expected_answers_sha256}', but current stored answers digest is "
                    f"'{current_sha256}' (ONT-EVO-003)."
                )

            existing_questions = {q.question_id: q for q in self.get_questions()}

            # Validate that question IDs in request are not duplicated (MIN-02)
            seen_request_qids: set[str] = set()
            for answer in request.answers:
                if answer.question_id in seen_request_qids:
                    raise InvalidClarificationAnswerError(
                        f"Duplicate answer provided for question_id '{answer.question_id}' "
                        "in a single request."
                    )
                seen_request_qids.add(answer.question_id)

            # Validate that all answered questions exist and selected option IDs are valid
            for answer in request.answers:
                if answer.question_id not in existing_questions:
                    raise QuestionNotFoundError(
                        f"Question with ID '{answer.question_id}' not found "
                        "in active session questions."
                    )
                target_q = existing_questions[answer.question_id]
                has_options = bool(answer.selected_options)
                has_user_text = bool(answer.user_text and answer.user_text.strip())
                if not has_options and not has_user_text:
                    raise InvalidClarificationAnswerError(
                        f"Answer item for question '{answer.question_id}' must provide either "
                        "selected_options or non-empty user_text."
                    )
                if not target_q.allow_custom_text and has_user_text:
                    raise InvalidClarificationAnswerError(
                        f"Question '{answer.question_id}' does not permit custom user text."
                    )
                valid_opt_ids = {opt.option_id for opt in target_q.options}
                if answer.selected_options:
                    # Check duplicate option IDs within answer (MIN-01)
                    if len(answer.selected_options) != len(set(answer.selected_options)):
                        raise InvalidClarificationOptionError(
                            f"Duplicate option IDs in selected_options for question "
                            f"'{answer.question_id}': {answer.selected_options}"
                        )
                    # If question has no options, reject non-empty selected_options (MIN-01)
                    if not valid_opt_ids:
                        raise InvalidClarificationOptionError(
                            f"Question '{answer.question_id}' has no predefined options; "
                            "selected_options must be empty."
                        )
                    for opt_id in answer.selected_options:
                        if opt_id not in valid_opt_ids:
                            allowed_sorted = sorted(valid_opt_ids)
                            raise InvalidClarificationOptionError(
                                f"Invalid option '{opt_id}' selected for question "
                                f"'{answer.question_id}'. Allowed option IDs: {allowed_sorted}"
                            )

            now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            for ans in request.answers:
                # Normalise timestamp to consistent UTC 'Z' format (MAJ-02, MIN-03)
                if ans.answered_at:
                    try:
                        raw_ts = ans.answered_at.strip()
                        parsed_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                        if parsed_dt.tzinfo is None:
                            # Explicitly treat naive timestamps as UTC (MAJ-02)
                            parsed_dt = parsed_dt.replace(tzinfo=UTC)
                        formatted_at = parsed_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (ValueError, TypeError) as err:
                        raise InvalidClarificationAnswerError(
                            f"Invalid ISO-8601 timestamp in answered_at '{ans.answered_at}': {err}"
                        ) from err
                else:
                    formatted_at = now_utc

                cleaned_user_text = ans.user_text.strip() if ans.user_text else None
                if not cleaned_user_text:
                    cleaned_user_text = None

                item_to_store = ans.model_copy(
                    update={
                        "answered_at": formatted_at,
                        "answered_by": ans.answered_by or "human:steward",
                        "user_text": cleaned_user_text,
                    }
                )
                merged_answers[item_to_store.question_id] = item_to_store

            all_answers = sorted(merged_answers.values(), key=lambda a: a.question_id)

            # Compute RFC 8785 Canonical JCS SHA-256
            jcs_payload = [a.model_dump(mode="json", exclude_none=True) for a in all_answers]
            jcs_str = dumps_jcs(jcs_payload)
            answers_bytes = jcs_str.encode("utf-8")
            answers_sha256 = hashlib.sha256(answers_bytes).hexdigest()

            # Store into CAS first and verify digest (SUG-01)
            stored_digest = self.cas.put(answers_bytes)
            if stored_digest != answers_sha256:
                raise RuntimeError(
                    f"CAS storage digest mismatch: computed {answers_sha256}, "
                    f"stored {stored_digest}"
                )

            # Persist to answers.json atomically
            answers_payload = [a.model_dump(mode="json", exclude_none=True) for a in all_answers]
            atomic_write_text(self.answers_file, json.dumps(answers_payload, indent=2))

            return ClarificationAnswerResponse(
                saved_count=len(request.answers),
                answers_sha256=answers_sha256,
            )

    def get_binding_constraints(self) -> ClarificationConstraints:
        """Extract structured binding constraints from persisted answers and questions.

        Raises:
            RuntimeError: If answers.json exists but is corrupt.
        """
        lock_file = self.session_dir / ".session.lock"
        with studio_lock(lock_file, level=LockLevel.L5, session_id=self.session_dir.name):
            if not self.answers_file.exists() or not self.questions_file.exists():
                return ClarificationConstraints()

            questions = {q.question_id: q for q in self.get_questions()}
            preferred_mappings: dict[str, str] = {}
            custom_guidance: dict[str, str] = {}
            selected_option_ids: dict[str, list[str]] = {}

            try:
                raw_answers = json.loads(self.answers_file.read_text(encoding="utf-8"))
                if isinstance(raw_answers, list):
                    item_list = raw_answers
                elif (
                    isinstance(raw_answers, dict)
                    and "answers" in raw_answers
                    and isinstance(raw_answers["answers"], list)
                ):
                    item_list = raw_answers["answers"]
                else:
                    raise TypeError("Expected list or dict with 'answers' list in answers.json")

                # Parse and collect valid answers
                answers_by_qid: dict[str, ClarificationAnswerItem] = {}
                for item in item_list:
                    ans = ClarificationAnswerItem.model_validate(item)
                    answers_by_qid[ans.question_id] = ans

                # Sort questions so exact catalog matches (focus_slug == matched_key)
                # are prioritized over broad/compound matches (MAJ-01)
                sorted_qids = sorted(
                    answers_by_qid.keys(),
                    key=lambda qid: (
                        0
                        if (
                            questions.get(qid)
                            and questions[qid].matched_key
                            and concept_to_slug(questions[qid].focus_concept)
                            == questions[qid].matched_key
                        )
                        else 1,
                        qid,
                    ),
                )

                for qid in sorted_qids:
                    ans = answers_by_qid[qid]
                    q = questions.get(ans.question_id)
                    if not q:
                        continue

                    focus_slug = concept_to_slug(q.focus_concept)
                    selected_option_ids[ans.question_id] = ans.selected_options

                    # Find selected option's fibo_uri (first selected option takes precedence)
                    opts_map = {opt.option_id: opt for opt in q.options}
                    for opt_id in ans.selected_options:
                        opt = opts_map.get(opt_id)
                        if opt and opt.fibo_uri:
                            if focus_slug not in preferred_mappings:
                                preferred_mappings[focus_slug] = opt.fibo_uri
                                preferred_mappings[q.focus_concept] = opt.fibo_uri
                            # Prioritize exact match or first-write (MAJ-01)
                            if q.matched_key and (
                                q.matched_key not in preferred_mappings
                                or focus_slug == q.matched_key
                            ):
                                preferred_mappings[q.matched_key] = opt.fibo_uri
                            break  # First selected option with fibo_uri wins

                    user_text = ans.user_text.strip() if ans.user_text else None
                    if user_text:
                        if focus_slug not in custom_guidance:
                            custom_guidance[focus_slug] = user_text
                            custom_guidance[q.focus_concept] = user_text
                        if q.matched_key and (
                            q.matched_key not in custom_guidance or focus_slug == q.matched_key
                        ):
                            custom_guidance[q.matched_key] = user_text

            except (
                OSError,
                json.JSONDecodeError,
                ValidationError,
                AttributeError,
                TypeError,
            ) as err:
                raise RuntimeError(
                    f"Cannot load binding constraints: answers.json is corrupt at "
                    f"{self.answers_file}: {err}"
                ) from err

            return ClarificationConstraints(
                preferred_mappings=preferred_mappings,
                custom_guidance=custom_guidance,
                selected_option_ids=selected_option_ids,
            )
