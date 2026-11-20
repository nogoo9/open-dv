"""Ontology Studio CLI clarification, answers, construction, and FIBO Q&A handlers."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_common import (
    _VERSION_PATTERN,
    _atomic_write_json,
    _err,
    _err_from_opendv_error,
    _err_from_workflow_error,
    _require_non_viewer_principal,
    _require_session_context,
    _resolve_studio_principal,
)
from opendv_contracts.dtos.clarification import ClarificationAnswerItem
from opendv_contracts.errors import OpenDVError
from opendv_contracts.jcs import dumps_jcs
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_service.studio.atomic_commit import (
    ConcurrentModificationError,
    InvalidPreconditionHeaderError,
)
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.job_runners import run_clarify_session, run_construct_session
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import append_signed_audit_record
from opendv_service.studio.wizard_workflows import ask_fibo_qa_workflow

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def handle_clarify(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """8. `opendv ontology studio clarify -s <session>` via `StudioJobRunners`."""
    auth_res = _require_non_viewer_principal(
        args, "Read-only viewer role is not permitted to run clarification."
    )
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agent role is forbidden from dispatching clarification jobs.",
        )
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]
    if str(getattr(args, "mode", None) or "auto").lower() == "agent":
        return _err(
            1,
            "AGENT_UNAVAILABLE",
            "Agent-assisted clarification harness is unavailable in local standalone engine; "
            "use --mode deterministic or --mode auto.",
        )
    max_q_raw = getattr(args, "max_questions", None)
    focus = getattr(args, "focus_concept", None)
    try:
        res = run_clarify_session(
            session_mgr,
            sid,
            candidate_terms=[str(focus)] if focus else None,
            max_questions=int(max_q_raw) if max_q_raw is not None else 10,
            actor_role=role_tier,
        )
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    q_dicts: list[dict[str, Any]] = []
    q_file = sdir / "clarifications" / "questions.json"
    if q_file.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            loaded_q = json.loads(q_file.read_text(encoding="utf-8"))
            if isinstance(loaded_q, list):
                q_dicts = loaded_q
    res["questions"] = q_dicts
    return 0, res


def _normalize_answer_item_dict(raw_item: dict[str, Any]) -> ClarificationAnswerItem:
    qid = str(raw_item.get("question_id") or "").strip()
    raw_opts = raw_item.get("selected_options")
    if raw_opts is None:
        raw_opts = raw_item.get("selected_option_ids")
    if raw_opts is None and raw_item.get("selected_option_id") is not None:
        raw_opts = [str(raw_item["selected_option_id"])]
    selected_opts = (
        [s.strip() for s in raw_opts.split(",") if s.strip()]
        if isinstance(raw_opts, str)
        else (
            [str(s).strip() for s in raw_opts if str(s).strip()]
            if isinstance(raw_opts, list)
            else []
        )
    )
    raw_text = (
        raw_item.get("user_text")
        if raw_item.get("user_text") is not None
        else (
            raw_item.get("free_text")
            if raw_item.get("free_text") is not None
            else raw_item.get("text")
        )
    )
    user_text = str(raw_text).strip() if raw_text is not None and str(raw_text).strip() else None
    return ClarificationAnswerItem(
        question_id=qid, selected_options=selected_opts, user_text=user_text
    )


def handle_answer(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """9. `opendv ontology studio answer -s <session>` (canonical list + JCS OCC)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated actors are forbidden from submitting human clarification answers.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to submit clarification answers.",
        )
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]
    ans_file = getattr(args, "file", None)
    qid_flag = getattr(args, "question_id", None) or getattr(args, "question", None)
    opt_flag = getattr(args, "option", None) or getattr(args, "option_id", None)
    text_flag = getattr(args, "text", None) or getattr(args, "free_text", None)
    exp_sha_flag = getattr(args, "expected_answers_sha256", None)
    incoming_raw: list[dict[str, Any]] = []
    file_exp_sha: str | None = None
    if ans_file:
        fpath = Path(ans_file)
        if not fpath.is_absolute() and executor:
            fpath = (executor.workspace_root / fpath).resolve()
        if not fpath.is_file():
            return _err(3, "FILE_NOT_FOUND", f"Answers file not found: {ans_file}")
        try:
            loaded_answers = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed answers JSON: {exc}")
        if isinstance(loaded_answers, dict):
            if isinstance(loaded_answers.get("expected_answers_sha256"), str):
                file_exp_sha = str(loaded_answers["expected_answers_sha256"])
            if isinstance(loaded_answers.get("answers"), list):
                incoming_raw.extend(
                    item for item in loaded_answers["answers"] if isinstance(item, dict)
                )
            elif loaded_answers.get("question_id"):
                incoming_raw.append(loaded_answers)
            else:
                for k, v in loaded_answers.items():
                    if k != "expected_answers_sha256" and isinstance(v, dict):
                        incoming_raw.append({**v, "question_id": v.get("question_id") or str(k)})
        elif isinstance(loaded_answers, list):
            incoming_raw.extend(item for item in loaded_answers if isinstance(item, dict))
        else:
            return _err(
                2, "SYNTAX_INVALID_ARGUMENT", "Answers file must contain a JSON list or object"
            )
    if qid_flag:
        if opt_flag is None and text_flag is None:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answer must include at least one --option or non-empty --text.",
            )
        flag_item: dict[str, Any] = {"question_id": str(qid_flag)}
        if opt_flag is not None:
            flag_item["selected_options"] = (
                [opt_flag] if isinstance(opt_flag, str) else list(opt_flag)
            )
        if text_flag is not None:
            flag_item["user_text"] = str(text_flag)
        incoming_raw.append(flag_item)
    if not incoming_raw:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Provide either --file or --question-id with --option/--text for studio answer.",
        )
    try:
        incoming_items = [_normalize_answer_item_dict(item) for item in incoming_raw]
    except (ValidationError, ValueError, TypeError) as exc:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid clarification answer item: {exc}")
    seen_qids: set[str] = set()
    for item in incoming_items:
        if not item.question_id:
            return _err(
                2, "SYNTAX_INVALID_ARGUMENT", "Clarification answer item is missing question_id."
            )
        if item.question_id in seen_qids:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Duplicate question_id '{item.question_id}' in clarification answers.",
            )
        seen_qids.add(item.question_id)
        if not item.selected_options and (
            item.user_text is None or not str(item.user_text).strip()
        ):
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answer must include at least one --option or non-empty --text.",
            )
    expected_sha = (exp_sha_flag or file_exp_sha or "").strip().strip('"').lower() or None
    target_ans = sdir / "clarifications" / "answers.json"
    with reentrant_session_lock(sdir / ".session.lock", session_id=sid):
        target_ans.parent.mkdir(parents=True, exist_ok=True)
        q_file = sdir / "clarifications" / "questions.json"
        if q_file.is_file():
            try:
                raw_q = json.loads(q_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                return _err(
                    1, "CORRUPT_SESSION_RECORD", f"Corrupt questions.json in session '{sid}': {exc}"
                )
            if isinstance(raw_q, dict) and isinstance(raw_q.get("questions"), list):
                q_list = raw_q["questions"]
            elif isinstance(raw_q, list):
                q_list = raw_q
            else:
                return _err(
                    1,
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt questions.json in session '{sid}': "
                    "expected list or questions wrapper.",
                )
            if q_list:
                q_map: dict[str, dict[str, Any]] = {}
                for q_obj in q_list:
                    if not isinstance(q_obj, dict) or not q_obj.get("question_id"):
                        return _err(
                            1,
                            "CORRUPT_SESSION_RECORD",
                            f"Corrupt question entry in questions.json for session '{sid}'.",
                        )
                    q_map[str(q_obj["question_id"])] = q_obj
                for item in incoming_items:
                    if item.question_id not in q_map:
                        return _err(
                            3,
                            "RESOURCE_NOT_FOUND",
                            f"Clarification question '{item.question_id}' "
                            f"does not exist in session '{sid}'.",
                        )
                    q_def = q_map[item.question_id]
                    if item.user_text and q_def.get("allow_custom_text") is False:
                        return _err(
                            2,
                            "SYNTAX_INVALID_ARGUMENT",
                            f"Question '{item.question_id}' does not permit "
                            "custom free-text answers.",
                        )
                    valid_opts: set[str] = set()
                    raw_q_opts = q_def.get("options")
                    if isinstance(raw_q_opts, list):
                        for opt_entry in raw_q_opts:
                            if isinstance(opt_entry, str) and opt_entry.strip():
                                valid_opts.add(opt_entry.strip())
                            elif isinstance(opt_entry, dict):
                                valid_opts.update(
                                    str(opt_entry[k]).strip()
                                    for k in ("option_id", "id", "value", "label")
                                    if isinstance(opt_entry.get(k), str)
                                    and str(opt_entry[k]).strip()
                                )
                    if valid_opts:
                        for sel in item.selected_options:
                            if sel not in valid_opts:
                                return _err(
                                    2,
                                    "SYNTAX_INVALID_ARGUMENT",
                                    f"Invalid option '{sel}' for question '{item.question_id}'.",
                                )
        existing_map: dict[str, ClarificationAnswerItem] = {}
        if target_ans.is_file():
            try:
                raw_existing = json.loads(target_ans.read_text(encoding="utf-8"))
                if isinstance(raw_existing, list):
                    for item in raw_existing:
                        if isinstance(item, dict):
                            norm_e = _normalize_answer_item_dict(item)
                            existing_map[norm_e.question_id] = norm_e
                elif isinstance(raw_existing, dict):
                    for k, item in raw_existing.items():
                        if isinstance(item, dict):
                            norm_e = _normalize_answer_item_dict(
                                {**item, "question_id": item.get("question_id") or str(k)}
                            )
                            existing_map[norm_e.question_id] = norm_e
            except (json.JSONDecodeError, OSError, ValidationError, ValueError) as exc:
                return _err(
                    1, "CORRUPT_SESSION_RECORD", f"Corrupt answers record in session '{sid}': {exc}"
                )
        sorted_existing = sorted(existing_map.values(), key=lambda x: x.question_id)
        current_sha = (
            hashlib.sha256(
                dumps_jcs(
                    [a.model_dump(mode="json", exclude_none=True) for a in sorted_existing]
                ).encode("utf-8")
            ).hexdigest()
            if sorted_existing
            else EMPTY_ANSWERS_SHA256
        )
        if expected_sha is not None and expected_sha != current_sha:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Clarification answers OCC mismatch: expected '{expected_sha}', "
                f"current is '{current_sha}'.",
            )
        for item in incoming_items:
            existing_map[item.question_id] = item
        answers_payload = [
            a.model_dump(mode="json", exclude_none=True)
            for a in sorted(existing_map.values(), key=lambda x: x.question_id)
        ]
        answers_sha = session_mgr.get_cas(sid).put(dumps_jcs(answers_payload).encode("utf-8"))
        _atomic_write_json(target_ans, answers_payload)
        append_signed_audit_record(
            session_dir=sdir,
            action="CLARIFICATION_ANSWERS_SAVED",
            actor=principal,
            details={"answered_count": len(answers_payload), "answers_sha256": answers_sha},
            security_manager=session_mgr.security_manager,
        )
    return 0, {
        "session_id": sid,
        "status": "ACCEPTED",
        "saved_count": len(incoming_items),
        "answers_recorded": len(answers_payload),
        "answers_sha256": answers_sha,
    }


def handle_construct(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """10. `opendv ontology studio construct -s <session>` via `StudioJobRunners`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to construct ontology proposals.",
        )
    auto_commit = bool(getattr(args, "auto_commit", False))
    if auto_commit and role_tier == "automated":
        return _err(
            7,
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
            "Automated agent role is forbidden from committing ontology deltas directly.",
        )
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]
    mode = str(getattr(args, "mode", "auto") or "auto").lower()
    base_ver, base_sha = getattr(args, "base_version", None), getattr(args, "base_sha256", None)
    if base_ver and not _VERSION_PATTERN.match(str(base_ver)):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid --base-version format: '{base_ver}'")
    if auto_commit and (mode != "deterministic" or not base_ver or not base_sha):
        return _err(
            2,
            "MISSING_AUTO_COMMIT_PRECONDITION",
            "--auto-commit requires --mode deterministic, --base-version, and --base-sha256",
        )
    if base_ver:
        clean_ver = str(base_ver).lstrip("v")
        vdir = (sdir / "versions" / f"v{clean_ver}").resolve()
        if not vdir.is_relative_to(sdir.resolve()):
            return _err(
                2, "INVALID_WORKSPACE_PATH", f"Base version path traversal detected: '{base_ver}'"
            )
        if clean_ver != "0.0.0" and not (
            (vdir / "ontology.json").is_file() or (vdir / "ontology.yaml").is_file()
        ):
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Specified --base-version does not exist: '{base_ver}'",
            )
    try:
        res = run_construct_session(
            session_mgr,
            sid,
            actor=principal if auto_commit else "system:deterministic",
            actor_role=role_tier,
            mode=mode,
            auto_commit=auto_commit,
            base_version=base_ver,
            base_sha256=base_sha,
            if_match=base_sha if auto_commit else None,
            security_manager=session_mgr.security_manager,
            steering_prompt=getattr(args, "steering_prompt", None),
        )
    except ConcurrentModificationError as err_occ:
        return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
    except InvalidPreconditionHeaderError as err_hdr:
        return _err(2, "INVALID_PRECONDITION_HEADER", str(err_hdr))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    return 0, res


def handle_fibo_qa(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """`opendv ontology studio fibo-qa` read-only FIBO & evidence question handler."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, _, sid = ctx  # type: ignore[misc]
    question = getattr(args, "question", None)
    if not question or not str(question).strip():
        return _err(40, "SYNTAX_INVALID_ARGUMENT", "Missing required --question flag.")
    src_id = getattr(args, "source_id", None)
    state = getattr(getattr(executor, "service", None), "state", None)
    runner = StudioAgentRunner(state_container=state) if state is not None else None
    try:
        return 0, ask_fibo_qa_workflow(
            session_mgr=session_mgr,
            session_id=sid,
            question=str(question),
            source_ids=[str(src_id)] if src_id else None,
            concept_uri=getattr(args, "concept_uri", None),
            agent_runner=runner,
        )
    except StudioWorkflowError as exc:
        return _err_from_workflow_error(exc, wizard_mode=True)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc, wizard_mode=True)
