"""Unit tests for Wave 3 issue closeout engine (scripts/close_wave3_issues.py).

Verifies idempotency markers, subprocess handling, fail-closed semantics,
child-task gating on Phase 6 tracker, and partial failure recovery (MAJ-01, MAJ-02, MIN-02, MIN-06).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from close_wave3_issues import (  # noqa: E402
    ISSUES,
    close_phase_6_tracker,
    is_issue_already_closed_and_verified,
    update_and_close_issue,
    update_epic_tracker,
)


def test_all_issues_contain_idempotency_markers() -> None:
    """Every closeout body MUST contain the exact HTML closeout marker (MAJ-01)."""
    assert len(ISSUES) == 10  # 228 through 237
    for num, data in ISSUES.items():
        marker = f"<!-- opendv-closeout:{num} -->"
        assert marker in data["body"], f"Issue #{num} missing idempotency marker {marker}"
        verdict_marker = f'"issue": {num}'
        assert verdict_marker in data["body"], f"Issue #{num} missing verdict metadata"


def test_is_issue_already_closed_and_verified_positive() -> None:
    """Verify detection when issue is CLOSED and contains marker."""
    fake_payload = {
        "state": "CLOSED",
        "comments": [
            {"body": "Earlier discussion"},
            {"body": "### Milestone M6 Task 6A.1 Completion Summary\n<!-- opendv-closeout:228 -->"},
        ],
    }
    fake_completed = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps(fake_payload),
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=fake_completed):
        assert is_issue_already_closed_and_verified(228, "nogoo9/open-dv") is True


def test_is_issue_already_closed_and_verified_open_issue() -> None:
    """Verify that an OPEN issue is NOT considered closed."""
    fake_payload = {
        "state": "OPEN",
        "comments": [{"body": "<!-- opendv-closeout:228 -->"}],
    }
    fake_completed = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps(fake_payload),
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=fake_completed):
        assert is_issue_already_closed_and_verified(228, "nogoo9/open-dv") is False


def test_is_issue_already_closed_and_verified_missing_marker() -> None:
    """Verify that a CLOSED issue without marker is NOT considered verified."""
    fake_payload = {
        "state": "CLOSED",
        "comments": [{"body": "Closed without review summary"}],
    }
    fake_completed = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps(fake_payload),
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=fake_completed):
        assert is_issue_already_closed_and_verified(228, "nogoo9/open-dv") is False


def test_update_and_close_issue_dry_run() -> None:
    """Dry run should return True without executing mutation commands."""
    with patch("close_wave3_issues.run_gh") as mock_gh:
        ok = update_and_close_issue(
            228,
            ISSUES[228],
            repo="nogoo9/open-dv",
            dry_run=True,
        )
        assert ok is True
        mock_gh.assert_not_called()


def test_update_and_close_issue_partial_failure_recovery() -> None:
    """When marker is on OPEN issue, skip comment and close issue (MIN-02, MIN-06)."""
    view_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps({
            "state": "OPEN",
            "comments": [{"body": "<!-- opendv-closeout:228 -->"}],
        }),
        stderr="",
    )
    close_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "close"],
        returncode=0,
        stdout="Closed issue 228",
        stderr="",
    )

    with (
        patch("close_wave3_issues.run_gh", side_effect=[view_proc, close_proc]) as mock_gh,
        patch("time.sleep"),
    ):
        ok = update_and_close_issue(
            228,
            ISSUES[228],
            repo="nogoo9/open-dv",
            dry_run=False,
            force=False,
        )
        assert ok is True
        # Verify comment command was NOT called, only view and close (MIN-06)
        subcommands = [call.args[0][1] for call in mock_gh.call_args_list if len(call.args[0]) > 1]
        assert "comment" not in subcommands
        assert subcommands == ["view", "close"]


def test_close_phase_6_tracker_blocked_by_open_child() -> None:
    """Phase 6 Tracker #196 MUST NOT be closed if child tasks like #239 are open (MAJ-01)."""
    open_child_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps({"state": "OPEN", "stateReason": None, "comments": []}),
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=open_child_proc) as mock_gh:
        ok = close_phase_6_tracker(repo="nogoo9/open-dv", dry_run=False)
        assert ok is True
        # Ensure 'gh issue close' for 196 was NEVER called
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] == "close" and argv[2] == "196")


def test_close_phase_6_tracker_fail_closed_on_child_error() -> None:
    """If child task lookup fails with non-zero return code, fail closed immediately (MAJ-01)."""
    error_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=1,
        stdout="",
        stderr="network timeout or auth failure",
    )
    with patch("close_wave3_issues.run_gh", return_value=error_proc) as mock_gh:
        ok = close_phase_6_tracker(repo="nogoo9/open-dv", dry_run=False)
        assert ok is False
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] in ("close", "comment") and argv[2] == "196")


def test_close_phase_6_tracker_fail_closed_on_child_malformed_json() -> None:
    """If child task returns malformed JSON, fail closed immediately (MAJ-01)."""
    bad_json_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout="Internal Server Error 500",
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=bad_json_proc) as mock_gh:
        ok = close_phase_6_tracker(repo="nogoo9/open-dv", dry_run=False)
        assert ok is False
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] in ("close", "comment") and argv[2] == "196")


def test_close_phase_6_tracker_dry_run_gating() -> None:
    """Dry-run must check gating and report in-progress tasks without closing (MIN-07)."""
    open_child_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps({"state": "OPEN", "stateReason": None, "comments": []}),
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=open_child_proc) as mock_gh:
        ok = close_phase_6_tracker(repo="nogoo9/open-dv", dry_run=True)
        assert ok is True
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] in ("close", "comment"))


def test_update_epic_tracker_blocked_by_open_phase() -> None:
    """Master Epic #193 MUST NOT be marked complete if Phase 6 tracker is open (MAJ-01)."""
    open_phase_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout=json.dumps({"state": "OPEN", "stateReason": None, "comments": []}),
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=open_phase_proc) as mock_gh:
        ok = update_epic_tracker(repo="nogoo9/open-dv", dry_run=False)
        assert ok is True
        # Ensure comment command for 193 was NEVER called
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] == "comment" and argv[2] == "193")


def test_update_epic_tracker_fail_closed_on_phase_error() -> None:
    """If phase tracker lookup fails with non-zero return code, fail closed immediately (MAJ-01)."""
    error_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=1,
        stdout="",
        stderr="network timeout or auth failure",
    )
    with patch("close_wave3_issues.run_gh", return_value=error_proc) as mock_gh:
        ok = update_epic_tracker(repo="nogoo9/open-dv", dry_run=False)
        assert ok is False
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] == "comment" and argv[2] == "193")


def test_update_epic_tracker_fail_closed_on_phase_malformed_json() -> None:
    """If phase tracker returns malformed JSON, fail closed immediately (MAJ-01)."""
    bad_json_proc = subprocess.CompletedProcess(
        args=["gh", "issue", "view"],
        returncode=0,
        stdout="Gateway Timeout 504",
        stderr="",
    )
    with patch("close_wave3_issues.run_gh", return_value=bad_json_proc) as mock_gh:
        ok = update_epic_tracker(repo="nogoo9/open-dv", dry_run=False)
        assert ok is False
        for call_item in mock_gh.call_args_list:
            argv = call_item.args[0]
            assert not (argv[0] == "issue" and argv[1] == "comment" and argv[2] == "193")
