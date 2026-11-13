"""Unit tests for Claude CLI Opus critique loop script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_opus_critique import (  # noqa: E402
    MAX_DIFF_CHARS,
    REVIEW_RUBRIC,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    CritiqueResult,
    CritiqueSchemaModel,
    format_issue_comment_markdown,
    format_pr_markdown,
    get_git_diff,
    invoke_claude_critique,
    parse_claude_json_output,
    reconcile_verdict_and_counts,
    resolve_claude_binary,
    run_self_tests,
    safe_which,
    sanitize_text,
    validate_model_identifier,
)


def test_json_parsing_fallbacks() -> None:
    sample = {"verdict": "PASS", "summary": "OK"}
    assert parse_claude_json_output(json.dumps(sample)) == sample
    assert parse_claude_json_output(f"```json\n{json.dumps(sample)}\n```") == sample
    assert parse_claude_json_output(f"Prefix text {json.dumps(sample)} Suffix") == sample


def test_is_clean_rejects_fail_with_zero_counts() -> None:
    res = CritiqueResult(
        verdict="FAIL",
        summary="Model declared FAIL",
        critical_count=0,
        major_count=0,
        minor_count=0,
        suggestion_count=0,
    )
    assert res.is_clean is False


def test_is_clean_rejects_execution_error() -> None:
    res = CritiqueResult(
        verdict="PASS",
        summary="Execution error",
        critical_count=0,
        major_count=0,
        minor_count=0,
        suggestion_count=0,
        is_execution_error=True,
    )
    assert res.is_clean is False


def test_schema_counts_cross_check_overrides_underreported() -> None:
    mismatched = {
        "verdict": "PASS",
        "summary": "Attempted bypass with hidden finding",
        "counts": {"critical": 0, "major": 0, "minor": 0, "suggestion": 0},
        "findings": [
            {
                "id": "CRIT-TEST",
                "severity": "CRITICAL",
                "category": "Security",
                "title": "Hidden Critical Finding",
                "description": "Critical in findings array",
                "recommendation": "Fix it",
            }
        ],
    }
    schema = CritiqueSchemaModel.model_validate(mismatched)
    verdict, final_counts = reconcile_verdict_and_counts(schema)
    assert final_counts.critical == 1
    assert verdict == "FAIL"


def test_diff_size_limit_fail_loud() -> None:
    oversized = "X" * (MAX_DIFF_CHARS + 10)
    res = invoke_claude_critique(diff_text=oversized, diff_stat="oversized", dry_run=False)
    assert res.verdict == "FAIL"
    assert res.major_count == 1
    assert any(f.id == "MAJ-DIFF-LIMIT" for f in res.findings)


def test_secret_redaction() -> None:
    leaked = (
        "Token: ghp_1234567890abcdefghijklmnopqrstuvwxyz and "
        "Key: sk-ant-1234567890abcdefghijklmnopqrstuvwxyz"
    )
    sanitized = sanitize_text(leaked)
    assert "ghp_" not in sanitized
    assert "sk-ant-" not in sanitized
    assert "[REDACTED_SECRET]" in sanitized


def test_reject_unsafe_model_metacharacters() -> None:
    for bad in ["opus&calc", "opus|whoami", "model<file", "model%VAR%"]:
        with pytest.raises(ValueError, match="Invalid or unsafe model identifier"):
            invoke_claude_critique(diff_text="diff", diff_stat="stat", model=bad, dry_run=False)


def test_validate_model_identifier() -> None:
    # Valid model names
    for valid in ["opus", "claude-3-opus-20240229", "claude-opus-5-5", "sonnet-3.5"]:
        validate_model_identifier(valid)

    # Invalid model names
    for bad in ["opus&dir", "model|ls", "bad;rm", "test%PATH%"]:
        with pytest.raises(ValueError, match="Invalid or unsafe model identifier"):
            validate_model_identifier(bad)


def test_resolve_claude_binary_rejects_metacharacters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDV_CLAUDE_CLI_BIN", "claude.cmd&calc.exe")
    with pytest.raises(ValueError, match="unsafe shell metacharacters"):
        resolve_claude_binary()


def test_get_git_diff_rejects_option_injection(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid git base_ref starting with '-'"):
        get_git_diff(repo_dir=tmp_path, base_ref="--upload-pack=evil")


def test_format_markdown_renderers() -> None:
    res_pass = CritiqueResult(
        verdict="PASS",
        summary="All checks pass",
        critical_count=0,
        major_count=0,
        minor_count=0,
        suggestion_count=0,
    )
    pr_out = format_pr_markdown(res_pass, phase="M0", task="T0")
    assert "✅ **PASS**" in pr_out
    assert "0 CRITICAL" in pr_out

    issue_out = format_issue_comment_markdown(res_pass, phase="M0", task="T0")
    assert "🟢 `PASS`" in issue_out


def test_self_test_suite_passes() -> None:
    assert run_self_tests() == 0


def test_invoke_claude_critique_success_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    canned_payload = {
        "structured_output": {
            "verdict": "PASS",
            "summary": "Forensic critique passed cleanly.",
            "counts": {"critical": 0, "major": 0, "minor": 0, "suggestion": 0},
            "findings": [],
            "positive_highlights": ["Clean architecture."],
        },
        "modelUsage": {
            "claude-opus-5-5": {"outputTokens": 450},
            "claude-haiku": {"outputTokens": 50},
        },
    }
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps(canned_payload),
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "PASS"
    assert result.is_clean is True
    assert result.critical_count == 0
    assert result.major_count == 0
    assert result.model == "claude-opus-5-5"


def test_invoke_claude_critique_result_string_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    inner_schema = {
        "verdict": "PASS",
        "summary": "Parsed via result string.",
        "counts": {"critical": 0, "major": 0, "minor": 1, "suggestion": 0},
        "findings": [
            {
                "id": "MIN-01",
                "severity": "MINOR",
                "category": "Style",
                "title": "Minor docstring issue",
                "description": "Docstring could be clearer",
                "recommendation": "Expand docstring",
            }
        ],
    }
    canned_payload = {"result": json.dumps(inner_schema)}
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps(canned_payload),
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "PASS"
    assert result.is_clean is True
    assert result.minor_count == 1


def test_invoke_claude_critique_nonzero_returncode(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=1,
        stdout="",
        stderr="API Error: Authentication failure",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "FAIL"
    assert result.is_execution_error is True
    assert result.critical_count == 1
    assert any(f.id == "CRIT-EXEC" for f in result.findings)


def test_invoke_claude_critique_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=10.0)

    monkeypatch.setattr(subprocess, "run", mock_timeout)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "FAIL"
    assert result.is_execution_error is True
    assert any(f.id == "CRIT-TIMEOUT" for f in result.findings)


def test_invoke_claude_critique_subprocess_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_oserror(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(subprocess, "run", mock_oserror)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "FAIL"
    assert result.is_execution_error is True
    assert any(f.id == "CRIT-SUBPROCESS-ERROR" for f in result.findings)


def test_invoke_claude_critique_unparseable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout="Random plain text with no json",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "FAIL"
    assert result.is_execution_error is True
    assert any(f.id == "CRIT-PARSE" for f in result.findings)


def test_invoke_claude_critique_schema_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_schema = {
        "verdict": "PASS",
        "counts": {"critical": 0, "major": 0, "minor": 0, "suggestion": 0},
    }
    completed = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps({"structured_output": bad_schema}),
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr("scripts.run_opus_critique.resolve_claude_binary", lambda: ["mock_claude"])

    result = invoke_claude_critique(diff_text="diff", diff_stat="stat")
    assert result.verdict == "FAIL"
    assert result.is_execution_error is True
    assert any(f.id == "CRIT-SCHEMA-VALIDATION" for f in result.findings)


def test_main_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.run_opus_critique import main

    # Case 1: Empty diff -> 0
    monkeypatch.setattr("scripts.run_opus_critique.get_git_diff", lambda *args, **kwargs: ("", ""))
    monkeypatch.setattr(sys, "argv", ["run_opus_critique.py"])
    assert main() == 0

    # Case 2: Clean pass -> 0
    clean_res = CritiqueResult(
        verdict="PASS",
        summary="Clean pass",
        critical_count=0,
        major_count=0,
        minor_count=0,
        suggestion_count=0,
    )
    monkeypatch.setattr(
        "scripts.run_opus_critique.get_git_diff", lambda *args, **kwargs: ("diff", "stat")
    )
    monkeypatch.setattr(
        "scripts.run_opus_critique.invoke_claude_critique", lambda *args, **kwargs: clean_res
    )
    assert main() == 0

    # Case 3: Gate blocked -> 1
    blocked_res = CritiqueResult(
        verdict="FAIL",
        summary="Gate blocked",
        critical_count=1,
        major_count=0,
        minor_count=0,
        suggestion_count=0,
    )
    monkeypatch.setattr(
        "scripts.run_opus_critique.invoke_claude_critique", lambda *args, **kwargs: blocked_res
    )
    assert main() == 1

    # Case 4: Gate blocked with --no-fail -> 0
    monkeypatch.setattr(sys, "argv", ["run_opus_critique.py", "--no-fail"])
    assert main() == 0

    # Case 5: Execution error -> 2
    err_res = CritiqueResult(
        verdict="FAIL",
        summary="Exec error",
        critical_count=1,
        major_count=0,
        minor_count=0,
        suggestion_count=0,
        is_execution_error=True,
    )
    monkeypatch.setattr(sys, "argv", ["run_opus_critique.py"])
    monkeypatch.setattr(
        "scripts.run_opus_critique.invoke_claude_critique", lambda *args, **kwargs: err_res
    )
    assert main() == 2


def test_git_diff_real_repo(tmp_path: Path) -> None:
    import shutil

    if not shutil.which("git"):
        pytest.skip("git not found")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    test_file = tmp_path / "hello.txt"
    test_file.write_text("initial content\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    test_file.write_text("modified content\n", encoding="utf-8")
    diff_text, diff_stat = get_git_diff(repo_dir=tmp_path, base_ref="HEAD")
    assert "modified content" in diff_text
    assert "hello.txt" in diff_stat


def test_safe_which_rejects_planted_cwd_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_claude = tmp_path / "claude.exe"
    fake_claude.write_text("fake binary", encoding="utf-8")

    monkeypatch.setenv("PATH", str(tmp_path))
    assert safe_which("claude.exe", reject_dirs=(tmp_path,)) is None


def test_system_prompt_and_user_template_user_directive_alignment() -> None:
    assert SYSTEM_PROMPT == REVIEW_RUBRIC
    assert "PRIMARY REVIEW FOCUS (USER DIRECTIVE)" in SYSTEM_PROMPT
    assert "Real Bugs" in SYSTEM_PROMPT
    assert "Genuine Design Issues wrt the Master Design Doc" in SYSTEM_PROMPT
    assert "STOP PEDANTIC DESIGN-DOC NITPICKING" in SYSTEM_PROMPT
    assert "{system_prompt}" in USER_PROMPT_TEMPLATE
    assert "{fence_nonce}" in USER_PROMPT_TEMPLATE
    assert "{diff_text}" in USER_PROMPT_TEMPLATE
