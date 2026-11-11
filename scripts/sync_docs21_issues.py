#!/usr/bin/env python3
"""Synchronize docs/21-ontology-studio-implementation-plan.md with live GitHub issue numbers."""

from __future__ import annotations

import json
import re
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parent.parent
    map_path = repo_root / "docs" / "design" / "ontology-studio" / "issue-map.json"
    doc_path = repo_root / "docs" / "21-ontology-studio-implementation-plan.md"

    with open(map_path, encoding="utf-8") as f:
        issue_map = json.load(f)

    task_map = {t["taskId"]: t for t in issue_map["tasks"]}
    epic = issue_map["epic"]
    phases = {p["key"]: p for p in issue_map["phases"]}

    with open(doc_path, encoding="utf-8-sig") as f:
        text = f.read()

    # 1. Section 0: update row 4
    epic_num = epic["id"]
    epic_url = epic["url"]
    p4_id, p4_url = phases["phase_4"]["id"], phases["phase_4"]["url"]
    p5_id, p5_url = phases["phase_5"]["id"], phases["phase_5"]["url"]
    p6_id, p6_url = phases["phase_6"]["id"], phases["phase_6"]["url"]

    old_sec0 = (
        "Authored `docs/21` (this plan) and `create_issues.ps1` with `-DryRun`. "
        "Decomposed Waves 1\u20133 into 13 tracks, 43 granular tasks, and parent-child "
        "issue tracker hierarchy."
    )
    new_sec0 = (
        "Authored `docs/21` (this plan) and `create_issues.ps1`. Established live GitHub "
        f"issue tracker on `nogoo9/open-dv`: Epic [#{epic_num}]({epic_url}), Phase trackers "
        f"([#{p4_id}]({p4_url}), [#{p5_id}]({p5_url}), [#{p6_id}]({p6_url})), and "
        "43 granular task issues (#197\u2013#239)."
    )
    text = text.replace(old_sec0, new_sec0)

    # 2. Section 1.1 item 5
    old_sec1_1 = (
        "5. **Issue Tracker Continuous Update**: As each task is picked up, executed, and merged, "
        "its corresponding GitHub issue is updated with PR references, test logs, and the Claude "
        "Opus critique summary."
    )
    new_sec1_1 = (
        "5. **Issue Tracker Continuous Update**: Live GitHub issue tracker established at Epic "
        f"[#{epic_num}]({epic_url}), Phase trackers ([#{p4_id}]({p4_url}), [#{p5_id}]({p5_url}), "
        f"[#{p6_id}]({p6_url})), and 43 task issues ([#197]({task_map['4A.1']['url']})\u2013"
        f"[#239]({task_map['6E.3']['url']})). As each task is picked up, executed, and merged, "
        "its corresponding GitHub issue is updated with PR references, test logs, and the Claude "
        "Opus critique summary."
    )
    text = text.replace(old_sec1_1, new_sec1_1)

    # 3. Section 1.3: update table headers & rows
    old_1_3_header = (
        "| Phase | Wave | Track ID | Track Name | Depends On | Task Count | "
        "Primary Target Package |\n|---|---|---|---|---|:---:|---|"
    )
    new_1_3_header = (
        "| Phase | Tracker Issue | Wave | Track ID | Track Name | Depends On | "
        "Task Count | Primary Target Package |\n|---|:---:|---|---|---|---|:---:|---|"
    )
    text = text.replace(old_1_3_header, new_1_3_header)

    text = re.sub(
        r"\|\s*\*\*Phase 4\*\*\s*\|\s*\*\*Wave 1\*\*\s*\|",
        f"| **Phase 4** | [#{p4_id}]({p4_url}) | **Wave 1** |",
        text,
    )
    text = re.sub(
        r"\|\s*\*\*Phase 5\*\*\s*\|\s*\*\*Wave 2\*\*\s*\|",
        f"| **Phase 5** | [#{p5_id}]({p5_url}) | **Wave 2** |",
        text,
    )
    text = re.sub(
        r"\|\s*\*\*Phase 6\*\*\s*\|\s*\*\*Wave 3\*\*\s*\|",
        f"| **Phase 6** | [#{p6_id}]({p6_url}) | **Wave 3** |",
        text,
    )

    # 4. Section 1.4: update table headers & rows
    old_1_4_header = (
        "| Task ID | Phase | Track | Title & Scope Summary | Depends On | Parallelizable | "
        "Target Implementation Files | Target Test Files |\n"
        "|---|---|---|---|---|:---:|---|---|"
    )
    new_1_4_header = (
        "| Task ID | GitHub Issue | Phase | Track | Title & Scope Summary | Depends On | "
        "Parallelizable | Target Implementation Files | Target Test Files |\n"
        "|---|:---:|:---:|:---:|---|---|:---:|---|---|"
    )
    text = text.replace(old_1_4_header, new_1_4_header)

    for t_id, t_info in task_map.items():
        issue_num = t_info["id"]
        issue_url = t_info["url"]
        link = f"[#{issue_num}]({issue_url})"
        pattern = rf"(\|\s*\*\*{re.escape(t_id)}\*\*\s*\|)(\s*P[456]\s*\|)"
        replacement = rf"\1 {link} |\2"
        text, n = re.subn(pattern, replacement, text)
        if n != 1:
            print(f"Warning: Section 1.4 replace for {t_id} had count {n}")

    # 5. Section 6: add GitHub Issue under each task heading
    for t_id, t_info in task_map.items():
        issue_num = t_info["id"]
        issue_url = t_info["url"]
        link = f"[#{issue_num}]({issue_url})"
        pattern = rf"(#####\s*\[Task {re.escape(t_id)}\][^\n]*\n-\s*\*\*Track\*\*:[^\n]*)"
        replacement = rf"\1\n- **GitHub Issue**: {link}"
        text, n = re.subn(pattern, replacement, text)
        if n != 1:
            print(f"Warning: Section 6 replace for {t_id} had count {n}")

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(text)

    print("Successfully synchronized docs/21 with all live GitHub issue references!")


if __name__ == "__main__":
    main()
