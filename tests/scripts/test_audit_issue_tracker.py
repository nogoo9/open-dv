"""Unit tests for Open-DV issue tracker audit engine (scripts/audit_issue_tracker.py).

Verifies anchored regex matching, per-comment isolation, canonical failure format
detection, issue-bound structured verdict parsing, pipe escaping, and tracker hierarchy logic
(MAJ-01, MAJ-02, MAJ-03, MIN-06, MIN-07).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from audit_issue_tracker import (  # noqa: E402
    format_markdown_table,
    run_audit,
    verify_review_summary,
)


def test_verify_review_summary_valid_passing() -> None:
    """Verify that a standard passing completion summary with 0 CRIT / 0 MAJ passes."""
    comments = [
        {
            "authorAssociation": "COLLABORATOR",
            "body": (
                "### Milestone M6 Task 6A.1 Completion Summary\n\n"
                "**Status**: COMPLETED & VERIFIED\n\n"
                "- Claude Opus 5.5 Critique Gate: 0 CRITICAL / 0 MAJOR findings verified."
            ),
        }
    ]
    verified, reason = verify_review_summary(comments, expected_issue_number=228)
    assert verified is True
    assert "Verified (0 CRIT / 0 MAJ)" in reason


def test_verify_review_summary_structured_verdict_bound() -> None:
    """Verify that machine-readable opendv-verdict block matching issue number passes (MAJ-02)."""
    verdict_json = (
        '{"issue": 228, "task": "6A.1", "critical": 0, "major": 0, "status": "COMPLETED"}'
    )
    comments = [
        {
            "authorAssociation": "MEMBER",
            "body": f"<!-- opendv-verdict: {verdict_json} -->\nCustom summary text.",
        }
    ]
    verified, reason = verify_review_summary(comments, expected_issue_number=228)
    assert verified is True
    assert "Verified (0 CRIT / 0 MAJ)" in reason


def test_verify_review_summary_rejects_untrusted_author() -> None:
    """Verify that comments from untrusted authors or missing association are rejected (MIN-02)."""
    verdict_json = (
        '{"issue": 228, "task": "6A.1", "critical": 0, "major": 0, "status": "COMPLETED"}'
    )
    # Untrusted association "NONE"
    comments_untrusted = [
        {
            "authorAssociation": "NONE",
            "body": f"<!-- opendv-verdict: {verdict_json} -->\nCustom summary text.",
        }
    ]
    verified, _ = verify_review_summary(comments_untrusted, expected_issue_number=228)
    assert verified is False

    # Missing authorAssociation entirely
    comments_missing = [
        {
            "body": f"<!-- opendv-verdict: {verdict_json} -->\nCustom summary text.",
        }
    ]
    verified, _ = verify_review_summary(comments_missing, expected_issue_number=228)
    assert verified is False


def test_verify_review_summary_structured_verdict_rejects_mismatched_issue() -> None:
    """Verify that a structured verdict block with mismatched issue number is rejected (MAJ-02)."""
    verdict_json = (
        '{"issue": 999, "task": "6A.1", "critical": 0, "major": 0, "status": "COMPLETED"}'
    )
    comments = [
        {
            "authorAssociation": "OWNER",
            "body": f"<!-- opendv-verdict: {verdict_json} -->\nCustom summary text.",
        }
    ]
    verified, _ = verify_review_summary(comments, expected_issue_number=228)
    assert verified is False


def test_verify_review_summary_rejects_structured_verdict_with_failure_text() -> None:
    """Verify that a structured verdict CANNOT bypass non-zero failure detection (MAJ-02)."""
    verdict_json = (
        '{"issue": 228, "task": "6A.1", "critical": 0, "major": 0, "status": "COMPLETED"}'
    )
    comments = [
        {
            "authorAssociation": "COLLABORATOR",
            "body": (
                f"<!-- opendv-verdict: {verdict_json} -->\n"
                "❌ GATE BLOCKED: Critique found 0 CRITICAL and 2 MAJOR findings."
            ),
        }
    ]
    verified, reason = verify_review_summary(comments, expected_issue_number=228)
    assert verified is False
    assert "Contains unresolved CRITICAL/MAJOR findings" in reason


def test_verify_review_summary_rejects_negations() -> None:
    """Verify that comments containing negation phrases fail (MIN-05)."""
    for negation in ["INCOMPLETE", "NOT COMPLETED", "NOT VERIFIED", "PENDING"]:
        comments = [
            {
                "authorAssociation": "COLLABORATOR",
                "body": (
                    f"**Status**: {negation}\n"
                    "Claude Opus 5.5 Critique Gate: 0 CRITICAL / 0 MAJOR findings."
                ),
            }
        ]
        verified, _ = verify_review_summary(comments, expected_issue_number=228)
        assert verified is False, f"Negation '{negation}' was incorrectly accepted"


def test_verify_review_summary_canonical_failure_formats() -> None:
    """Verify canonical failure formats are detected and reject the review (MAJ-02)."""
    failure_cases = [
        "Critique found 2 CRITICAL / 0 MAJOR findings.",
        "Critique found 0 CRITICAL / 3 MAJOR findings.",
        "Critique found 0 CRITICAL and 4 MAJOR findings.",
        "❌ GATE BLOCKED: Critique found 0 CRITICAL and 1 MAJOR findings.",
        "VERDICT: FAIL",
        "Critical: 1",
        "Major: 2",
        "| **MAJOR** | 1 | 0 | ❌ FAIL |",
        "| **CRITICAL** | 2 | 0 | ❌ FAIL |",
    ]

    for fail_text in failure_cases:
        comments = [
            {
                "authorAssociation": "COLLABORATOR",
                "body": (
                    "**Status**: COMPLETED\n"
                    f"{fail_text}"
                ),
            }
        ]
        verified, _ = verify_review_summary(comments, expected_issue_number=228)
        assert verified is False, f"Failure pattern '{fail_text}' was incorrectly accepted"


def test_verify_review_summary_rejects_cross_comment_leakage() -> None:
    """Verify that completion in Comment A and bare unrelated word in Comment B does not verify."""
    comments = [
        {"authorAssociation": "COLLABORATOR", "body": "**Status**: COMPLETED"},
        {
            "authorAssociation": "COLLABORATOR",
            "body": "The build was CLEAN and we APPROVE the design.",
        },
    ]
    verified, reason = verify_review_summary(comments, expected_issue_number=228)
    assert verified is False
    assert "Missing verified completion summary" in reason


def test_format_markdown_table_pipe_escaping() -> None:
    """Ensure titles with Markdown pipe character '|' are escaped in rendered table (MIN-06)."""
    results = [
        {
            "taskId": "6B.1",
            "issueNumber": 230,
            "title": "Task 6B.1: Tier 1 | Tier 2 Boundaries",
            "state": "CLOSED",
            "reviewStatus": "Verified (0 CRIT / 0 MAJ)",
            "verdict": "PASS",
        }
    ]
    rendered = format_markdown_table(results)
    assert "Tier 1 \\| Tier 2 Boundaries" in rendered
    assert "Tier 1 | Tier 2" not in rendered


def test_run_audit_hierarchy_phase_blocked_by_child(tmp_path: Path) -> None:
    """Verify that if child task fails, parent phase verdict is FAIL (MIN-06, MIN-07)."""
    dummy_map = {
        "phases": [{"key": "phase_6", "id": 196, "title": "Phase 6"}],
        "tasks": [
            {
                "taskId": "6A.1",
                "phaseKey": "phase_6",
                "id": 228,
                "title": "Task 6A.1",
            }
        ],
        "epic": {"id": 193, "title": "Epic"},
    }
    map_path = tmp_path / "issue-map.json"
    map_path.write_text(json.dumps(dummy_map), encoding="utf-8")
    report_path = tmp_path / "report.json"

    # Issue 228 is CLOSED but missing critique summary -> FAIL
    fake_repo_issues = {
        228: {"number": 228, "title": "Task 6A.1", "state": "CLOSED", "comments": []},
        196: {"number": 196, "title": "Phase 6", "state": "CLOSED", "comments": []},
        193: {"number": 193, "title": "Epic", "state": "OPEN", "comments": []},
    }

    with patch("audit_issue_tracker.query_github_issues", return_value=fake_repo_issues):
        exit_code = run_audit(
            repo="nogoo9/open-dv",
            issue_map_path=map_path,
            strict=False,
            allow_open=set(),
            output_json=report_path,
        )
        assert exit_code == 1
        data = json.loads(report_path.read_text(encoding="utf-8"))
        phase_res = next(r for r in data["results"] if r["taskId"] == "PHASE_6")
        assert phase_res["verdict"] == "FAIL"
        assert "Blocked by unverified child tasks" in phase_res["reviewStatus"]
