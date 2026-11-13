#!/usr/bin/env python3
"""Audit script for Open-DV Ontology Construction Studio GitHub issue tracker.

Verifies that all 43 task issues (#197-#239), 3 phase trackers (#194-#196),
and the epic tracker (#193) on nogoo9/open-dv exist, have proper states,
and contain verified completion and Claude Opus critique review summaries.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Trusted GitHub author associations for official verification comments (MAJ-02)
TRUSTED_AUTHOR_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

# Anchored regexes for status/completion verification (MAJ-01, MIN-05)
NEGATION_PATTERN: re.Pattern[str] = re.compile(
    r"(?:\bStatus\b\*{0,2}\s*:\s*\*{0,2}(?:NOT\s+COMPLETED|NOT\s+PASSED|NOT\s+VERIFIED|INCOMPLETE|FAILED|FAIL\b|PENDING)"
    r"|\bVerdict\b\*{0,2}\s*:\s*\*{0,2}FAIL\b"
    r"|\b(?:NOT\s+COMPLETED|NOT\s+PASSED|NOT\s+VERIFIED|INCOMPLETE\s+STATUS)\b)",
    re.IGNORECASE,
)

COMPLETION_PATTERN: re.Pattern[str] = re.compile(
    r"(?:(?:\*{0,2}(?:\bStatus\b|\bVerdict\b)\*{0,2})\s*:.*?\b(?:COMPLETED|VERIFIED|PASS(?:ED)?)\b"
    r"|###?\s+(?:Milestone\s+[A-Z0-9]+\s+)?(?:Execution\s+Summary.*?Task|Task.*?Summary)\b"
    r"|###?\s+\[Phase\s+6\]\s+Wave\s+3\s+Integration\s+&\s+Release\s+Complete\b"
    r"|\bTask\s+[0-9A-Za-z.]+\s+(?:completed|verified|closed|approved)\b"
    r"|\bGATE\s+PASSED\b)",
    re.IGNORECASE,
)

# Critique verification: Must find explicit zero counts without non-zero counts (MAJ-02, MAJ-03)
ZERO_CRITIQUE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:(?<!\d)0\s*CRITICAL\s*(?:and|/|,|\&)\s*(?<!\d)0\s*MAJOR\b"
    r"|(?<!\d)0\s*CRIT\b.*?(?<!\d)0\s*MAJ\b"
    r"|\bCritical\s*:\s*0\b.*?\bMajor\s*:\s*0\b"
    r"|(?<!\d)0\s*CRITICAL\s+and\s+(?<!\d)0\s*MAJOR\s+findings\b"
    r"|\bVerified\s*\((?<!\d)0\s*CRIT(?:ICAL)?\s*/\s*(?<!\d)0\s*MAJ(?:OR)?\)\b"
    r"|\|\s*\*{0,2}CRITICAL\*{0,2}\s*\|\s*`?0`?\s*\|.*?\|\s*\*{0,2}MAJOR\*{0,2}\s*\|\s*`?0`?\s*\|)",
    re.IGNORECASE | re.DOTALL,
)

# Failure format detection matching canonical critique output (MAJ-02)
NON_ZERO_CRITIQUE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:\bGATE\s+BLOCKED\b"
    r"|\bVERDICT\s*:\s*❌?\s*FAIL\b"
    r"|(?<!\d)[1-9]\d*\s*(?:CRITICAL|CRIT)\b"
    r"|(?<!\d)[1-9]\d*\s*(?:MAJOR|MAJ)\s*(?:findings?|defects?|violations?|issues?|problems?)\b"
    r"|(?<!\d)\d+\s*CRITICAL\s*/\s*[1-9]\d*\s*MAJOR\b"
    r"|(?<!\d)[1-9]\d*\s*CRITICAL\s*/\s*\d+\s*MAJOR\b"
    r"|\bCrit(?:ical)?\s*[:=]\s*[1-9]\d*\b"
    r"|\bMaj(?:or)?\s*[:=]\s*[1-9]\d*\b"
    r"|\|\s*\*{0,2}CRITICAL\*{0,2}\s*\|\s*`?[1-9]\d*`?\s*\|"
    r"|\|\s*\*{0,2}MAJOR\*{0,2}\s*\|\s*`?[1-9]\d*`?\s*\|)",
    re.IGNORECASE,
)

# Machine-readable verdict marker (SUG-01)
STRUCTURED_VERDICT_PATTERN: re.Pattern[str] = re.compile(
    r"<!--\s*opendv-verdict:\s*(\{.*?\})\s*-->",
    re.DOTALL,
)

# Legacy Milestone Reviewer/Quality Gate approval pattern
LEGACY_GATE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:\bTask\s+[0-9A-Za-z.]+\s+(?:completed|verified|closed|approved)\s+.*?\b(?:Reviewer\s+Gate|Quality\s+Gate)\b"
    r"|\bMilestone\s+M[0-9]+\s+(?:Reviewer|Quality)\s+Gate\b)",
    re.IGNORECASE,
)


def verify_review_summary(
    comments: list[dict[str, Any]], expected_issue_number: int | None = None
) -> tuple[bool, str]:
    """Check if any single comment contains verified completion and zero-finding critique.

    Enforces author trust, per-comment isolation, issue-number binding, and rejects
    negations or non-zero findings (MAJ-01, MAJ-02).
    """
    if not comments:
        return False, "No comments found"

    has_passing_comment = False
    has_unresolved_failure = False

    for c in comments:
        # Check author association security guard against forged comments (MAJ-02, MIN-02)
        assoc = c.get("authorAssociation")
        if (assoc or "").upper() not in TRUSTED_AUTHOR_ASSOCIATIONS:
            # Untrusted external comment; cannot issue official closeout or verdict
            continue

        body = c.get("body", "")
        if not body:
            continue

        body_clean = body.replace("`", "")

        # 1. ALWAYS check for non-zero critique failures or gate blocks first (MAJ-02)
        if NON_ZERO_CRITIQUE_PATTERN.search(body_clean):
            has_unresolved_failure = True
            continue

        # 2. Skip comments that express negation of completion
        if NEGATION_PATTERN.search(body_clean):
            continue

        # 3. Check machine-readable structured verdict block (bound to expected_issue_number)
        has_bound_verdict = False
        m_struct = STRUCTURED_VERDICT_PATTERN.search(body)
        if m_struct:
            try:
                meta = json.loads(m_struct.group(1))
                issue_match = (
                    expected_issue_number is None or meta.get("issue") == expected_issue_number
                )
                if (
                    issue_match
                    and meta.get("critical") == 0
                    and meta.get("major") == 0
                    and meta.get("status") in ("COMPLETED", "PASSED")
                ):
                    has_bound_verdict = True
            except json.JSONDecodeError:
                pass

        # 4. Check prose completion and critique patterns
        has_completion = bool(COMPLETION_PATTERN.search(body_clean))
        has_zero_critique = bool(ZERO_CRITIQUE_PATTERN.search(body_clean))
        has_legacy_gate = bool(LEGACY_GATE_PATTERN.search(body_clean))

        # A valid passing comment must have NO non-zero critique, NO negation,
        # AND either bound structured verdict OR (prose completion AND prose zero-critique)
        # OR trusted milestone reviewer/quality gate approval
        if (
            (has_bound_verdict and (has_completion or "status" in body.lower()))
            or (has_completion and has_zero_critique)
            or has_legacy_gate
        ):
            has_passing_comment = True
            # A valid passing closeout comment overrides earlier critique iteration failures
            has_unresolved_failure = False

    if has_passing_comment and not has_unresolved_failure:
        return True, "Verified (0 CRIT / 0 MAJ)"
    if has_unresolved_failure:
        return False, "Contains unresolved CRITICAL/MAJOR findings or GATE BLOCKED"
    return False, "Missing verified completion summary with 0 CRIT / 0 MAJ critique"


def fetch_single_issue(repo: str, issue_number: int) -> dict[str, Any] | None:
    """Fallback fetch for a single issue if batch list drops it (MIN-01)."""
    cmd = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        "--repo",
        repo,
        "--json",
        "number,title,state,comments,updatedAt",
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return None


def query_github_issues(
    repo: str, required_numbers: set[int] | None = None
) -> dict[int, dict[str, Any]]:
    """Fetch all issues for the repository using gh CLI with fallback recovery (MIN-01)."""
    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        "number,title,state,comments,updatedAt",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        raise RuntimeError(f"gh issue list failed (code {result.returncode}): {err}")

    try:
        issues_list = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse gh issue list output as JSON: {e}") from e

    issues_by_number: dict[int, dict[str, Any]] = {
        item["number"]: item for item in issues_list if "number" in item
    }

    # If any required issue is missing from batch query, perform individual fetch (MIN-01)
    if required_numbers:
        missing = required_numbers - set(issues_by_number.keys())
        for num in missing:
            single = fetch_single_issue(repo, num)
            if single:
                issues_by_number[num] = single

    return issues_by_number


def format_markdown_table(results: list[dict[str, Any]]) -> str:
    """Render summary table with proper pipe character escaping (MIN-06)."""
    lines = [
        "| Task ID | Issue # | Title | State | Review Summary Status | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        title = r["title"].replace("|", "\\|")
        row = (
            f"| {r['taskId']} | #{r['issueNumber']} | {title} | {r['state']} | "
            f"{r['reviewStatus']} | **{r['verdict']}** |"
        )
        lines.append(row)
    return "\n".join(lines)


def run_audit(
    repo: str,
    issue_map_path: Path,
    strict: bool = False,
    allow_open: set[int] | None = None,
    output_json: Path | None = None,
) -> int:
    """Run comprehensive issue tracker audit."""
    if allow_open is None:
        allow_open = set()

    try:
        with open(issue_map_path, encoding="utf-8") as f:
            issue_map = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error loading issue map from {issue_map_path}: {e}", file=sys.stderr)
        return 1

    tasks = issue_map.get("tasks", [])
    phases = issue_map.get("phases", [])
    epic = issue_map.get("epic", {})

    # In strict mode, assert all entries have valid IDs and verify map completeness (MIN-05)
    if strict:
        for t in tasks:
            if "id" not in t or not isinstance(t["id"], int):
                print(f"[!] STRICT AUDIT FAIL: Task entry missing integer id: {t}", file=sys.stderr)
                return 1
        for p in phases:
            if "id" not in p or not isinstance(p["id"], int):
                print(
                    f"[!] STRICT AUDIT FAIL: Phase entry missing integer id: {p}",
                    file=sys.stderr,
                )
                return 1
        if "id" not in epic or not isinstance(epic["id"], int):
            print(
                f"[!] STRICT AUDIT FAIL: Epic entry missing integer id: {epic}",
                file=sys.stderr,
            )
            return 1
        expected_total = len(tasks) + len(phases) + 1
        if expected_total < 47:
            print(
                f"[!] STRICT AUDIT FAIL: Issue map incomplete (found {expected_total} < 47)",
                file=sys.stderr,
            )
            return 1

    required_numbers: set[int] = set()
    for t in tasks:
        if "id" in t:
            required_numbers.add(t["id"])
    for p in phases:
        if "id" in p:
            required_numbers.add(p["id"])
    if "id" in epic:
        required_numbers.add(epic["id"])

    print(f"[*] Querying live issues from GitHub repository: {repo}...")
    try:
        repo_issues = query_github_issues(repo, required_numbers=required_numbers)
    except RuntimeError as e:
        print(f"Error querying GitHub issues: {e}", file=sys.stderr)
        return 1

    print(f"[+] Retrieved {len(repo_issues)} issues from {repo}.\n")

    results: list[dict[str, Any]] = []
    task_verdicts: dict[int, str] = {}
    phase_verdicts: dict[str, str] = {}

    # 1. Audit Task Issues
    for task in tasks:
        task_id = task.get("taskId", "UNKNOWN")
        issue_num = task.get("id")
        expected_title = task.get("title", "")

        if not issue_num:
            continue

        gh_issue = repo_issues.get(issue_num)
        if not gh_issue:
            results.append(
                {
                    "taskId": task_id,
                    "issueNumber": issue_num,
                    "title": expected_title,
                    "state": "MISSING",
                    "reviewStatus": "NOT_FOUND",
                    "verdict": "FAIL",
                    "reason": "Issue does not exist in repo",
                }
            )
            task_verdicts[issue_num] = "FAIL"
            continue

        state = gh_issue.get("state", "UNKNOWN")
        comments = gh_issue.get("comments", [])
        verified_review, review_detail = verify_review_summary(
            comments, expected_issue_number=issue_num
        )

        if issue_num in allow_open:
            verdict = "PASS" if (state in ("OPEN", "CLOSED")) else "FAIL"
            status_desc = f"ALLOWED_OPEN ({state})"
        else:
            if state != "CLOSED":
                verdict = "FAIL"
                status_desc = f"STILL_OPEN ({review_detail})"
            elif not verified_review:
                verdict = "FAIL"
                status_desc = f"CLOSED ({review_detail})"
            else:
                verdict = "PASS"
                status_desc = f"CLOSED ({review_detail})"

        task_verdicts[issue_num] = verdict
        results.append(
            {
                "taskId": task_id,
                "issueNumber": issue_num,
                "title": gh_issue.get("title", expected_title),
                "state": state,
                "reviewStatus": status_desc,
                "verdict": verdict,
            }
        )

    # Group tasks dynamically by phaseKey from issue-map.json (MIN-03)
    tasks_by_phase: dict[str, list[int]] = {}
    for task in tasks:
        pk = task.get("phaseKey", "").lower()
        if pk and "id" in task:
            tasks_by_phase.setdefault(pk, []).append(task["id"])

    # 2. Audit Phase Trackers
    for phase in phases:
        phase_key = phase.get("key", "").lower()
        issue_num = phase.get("id")
        if not issue_num:
            continue

        gh_issue = repo_issues.get(issue_num)
        if not gh_issue:
            results.append(
                {
                    "taskId": phase_key.upper(),
                    "issueNumber": issue_num,
                    "title": phase.get("title", ""),
                    "state": "MISSING",
                    "reviewStatus": "NOT_FOUND",
                    "verdict": "FAIL",
                    "reason": "Phase tracker issue missing",
                }
            )
            phase_verdicts[phase_key] = "FAIL"
            continue

        state = gh_issue.get("state", "UNKNOWN")
        child_task_nums = tasks_by_phase.get(phase_key, [])
        failed_children = [
            num
            for num in child_task_nums
            if task_verdicts.get(num) != "PASS" and num not in allow_open
        ]

        if not child_task_nums:
            verdict = "FAIL"
            status_desc = "FAIL (No child tasks registered in issue map)"
        elif issue_num in allow_open:
            verdict = "PASS" if not failed_children else "FAIL"
            status_desc = (
                f"ALLOWED_OPEN ({state})"
                if not failed_children
                else f"ALLOWED_OPEN (Blocked by children: {failed_children})"
            )
        elif state != "CLOSED":
            verdict = "FAIL"
            status_desc = "STILL_OPEN"
        elif failed_children:
            verdict = "FAIL"
            status_desc = f"CLOSED (Blocked by unverified child tasks: {failed_children})"
        else:
            verdict = "PASS"
            status_desc = f"CLOSED (All {len(child_task_nums)} child tasks verified)"

        phase_verdicts[phase_key] = verdict
        results.append(
            {
                "taskId": phase_key.upper(),
                "issueNumber": issue_num,
                "title": gh_issue.get("title", phase.get("title", "")),
                "state": state,
                "reviewStatus": status_desc,
                "verdict": verdict,
            }
        )

    # 3. Audit Epic Tracker
    epic_num = epic.get("id")
    if epic_num:
        gh_issue = repo_issues.get(epic_num)
        if not gh_issue:
            results.append(
                {
                    "taskId": "EPIC",
                    "issueNumber": epic_num,
                    "title": epic.get("title", ""),
                    "state": "MISSING",
                    "reviewStatus": "NOT_FOUND",
                    "verdict": "FAIL",
                }
            )
        else:
            comments = gh_issue.get("comments", [])
            verified_epic, epic_review_detail = verify_review_summary(
                comments, expected_issue_number=epic_num
            )
            has_wave3_marker = any(
                (c.get("authorAssociation") or "").upper() in TRUSTED_AUTHOR_ASSOCIATIONS
                and "<!-- opendv-closeout:193 -->" in c.get("body", "")
                for c in comments
            )
            failed_phases = [k for k, v in phase_verdicts.items() if v != "PASS"]

            if not phases:
                status_desc = "FAIL (No phases registered in issue map)"
                verdict = "FAIL"
            elif failed_phases:
                status_desc = f"Blocked by unverified phases: {failed_phases}"
                verdict = "FAIL"
            elif not (verified_epic or has_wave3_marker):
                status_desc = f"Missing Wave 3 Update ({epic_review_detail})"
                verdict = "PASS" if epic_num in allow_open else "FAIL"
            else:
                status_desc = "Updated with Wave 3 & Phase Trackers Verified"
                verdict = "PASS"

            results.append(
                {
                    "taskId": "EPIC",
                    "issueNumber": epic_num,
                    "title": gh_issue.get("title", epic.get("title", "")),
                    "state": gh_issue.get("state", "UNKNOWN"),
                    "reviewStatus": status_desc,
                    "verdict": verdict,
                }
            )

    # Total violations computed directly from FAIL verdicts (MIN-01)
    total_violations = sum(1 for r in results if r["verdict"] == "FAIL")

    # Print Summary Markdown Table
    print(format_markdown_table(results))

    print("\n" + "=" * 80)
    print(f"Total Issues Audited: {len(results)}")
    print(f"Total Violations / Deficiencies: {total_violations}")
    print(f"Audit Mode: {'STRICT' if strict else 'STANDARD'}")
    print("=" * 80 + "\n")

    if output_json:
        report_data = {
            "repo": repo,
            "totalAudited": len(results),
            "totalViolations": total_violations,
            "passed": total_violations == 0,
            "results": results,
        }
        try:
            output_json.parent.mkdir(parents=True, exist_ok=True)
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2)
            print(f"[+] Audit JSON report saved to: {output_json}")
        except OSError as e:
            print(f"Error writing audit JSON to {output_json}: {e}", file=sys.stderr)
            return 1

    return 1 if total_violations > 0 else 0


def main() -> None:
    desc = "Audit GitHub issue tracker for Open-DV Ontology Studio"
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("--repo", default="nogoo9/open-dv", help="GitHub repository (owner/name)")
    parser.add_argument(
        "--issue-map",
        default="docs/design/ontology-studio/issue-map.json",
        help="Path to issue-map.json catalog",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enforce strict zero-violation gate compliance (prohibits --allow-open)",
    )
    parser.add_argument(
        "--allow-open",
        default="",
        help="Comma-separated issue numbers allowed to remain open during standard audit",
    )
    parser.add_argument("--output-json", default=None, help="Path to write output JSON report")

    args = parser.parse_args()

    allow_open_set: set[int] = set()
    if args.allow_open:
        for s in args.allow_open.split(","):
            s = s.strip()
            if s:
                try:
                    allow_open_set.add(int(s))
                except ValueError:
                    parser.error(f"Invalid integer issue number in --allow-open: '{s}'")

    if args.strict and allow_open_set:
        parser.error(
            "--strict mode prohibits --allow-open; all task and phase issues must be "
            "fully completed and closed, and master epic updated"
        )

    issue_map_file = Path(args.issue_map)
    if not issue_map_file.is_file():
        # Try relative to repo root
        issue_map_file = Path(__file__).resolve().parent.parent / args.issue_map

    if not issue_map_file.is_file():
        parser.error(f"Issue map file not found: {args.issue_map}")

    output_json_path = Path(args.output_json) if args.output_json else None

    exit_code = run_audit(
        repo=args.repo,
        issue_map_path=issue_map_file,
        strict=args.strict,
        allow_open=allow_open_set,
        output_json=output_json_path,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
