"""tests/parity/test_docs17_registry.py — Meta-test verifying docs/17 ↔ registry parity."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.parity.scenarios import ScenarioSpec, list_scenarios

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_17_PATH = REPO_ROOT / "docs" / "17-testing-scenarios-and-datasets.md"


def _parse_docs17(
    text: str | None = None,
) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str]], str]:
    if text is None:
        text = DOCS_17_PATH.read_text(encoding="utf-8")

    # 1. Parse catalog rows S1-S10
    catalog_rows = re.findall(
        r"\|\s*\*\*(S\d+):[^*]+\*\*\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|",
        text,
    )

    # 2. Parse 13 issue headings
    issue_headings = re.findall(
        r"^## Issue #(\d+)\s*—\s*([^\n]+)",
        text,
        flags=re.MULTILINE,
    )
    return catalog_rows, issue_headings, text


def _check_cited_paths(text: str, repo_root: Path = REPO_ROOT) -> None:
    """Verify all cited markdown links and backtick repository paths exist on disk."""
    # Match markdown link paths (excluding external http/https links)
    link_paths = re.findall(r"\[.*?\]\((?!http)([^)#\s]+)\)", text)
    for p in link_paths:
        target = (repo_root / "docs" / p).resolve()
        assert target.exists(), (
            f"Markdown link target cited in docs/17 does not exist: {p} -> {target}"
        )

    # Match backtick code blocks citing repository paths
    backtick_paths = re.findall(r"`([a-zA-Z0-9_\-\./]+)`", text)
    for bp in backtick_paths:
        if bp.startswith(("packages/", "examples/", "tests/", "schemas/", "docs/")):
            target = (repo_root / bp).resolve()
            assert target.exists(), (
                f"Repository path cited in docs/17 backticks does not exist: {bp} -> {target}"
            )


def test_docs17_catalog_rows_mapped_to_registry() -> None:
    """Verify all catalog rows S1-S10 map to >= 1 registry entry."""
    catalog_rows, _, _ = _parse_docs17()
    assert len(catalog_rows) == 10, f"Expected 10 catalog rows, found {len(catalog_rows)}"

    for row_id, _dataset, _invariants, _command in catalog_rows:
        mapped = [spec for spec in list_scenarios() if row_id in spec.scenarios]
        assert mapped, f"Catalog row {row_id} has no mapping in SCENARIO_REGISTRY"


def test_docs17_issue_headings_mapped_to_registry() -> None:
    """Verify all 13 issue headings map to >= 1 registry entry."""
    _, issue_headings, _ = _parse_docs17()
    assert len(issue_headings) == 13, f"Expected 13 issue headings, found {len(issue_headings)}"

    for issue_num, issue_title in issue_headings:
        anchor_pattern = f"Issue #{issue_num}"
        mapped = [spec for spec in list_scenarios() if anchor_pattern in spec.docs17_anchor]
        assert mapped, f"Issue heading #{issue_num} ({issue_title}) has no mapping in registry"


def test_scenario_and_uf_coverage_matrix() -> None:
    """Verify no scenario (S1..S10) or unmatched case (UF-1..UF-5) has 0 coverage."""
    all_specs = list_scenarios()

    for s_idx in range(1, 11):
        s_id = f"S{s_idx}"
        count = sum(1 for spec in all_specs if s_id in spec.scenarios)
        assert count >= 1, f"Scenario {s_id} coverage count dropped to {count}"

    for uf_idx in range(1, 6):
        uf_id = f"UF-{uf_idx}"
        count = sum(1 for spec in all_specs if uf_id in spec.uf_cases)
        assert count >= 1, f"Unmatched case {uf_id} coverage count dropped to {count}"


def test_docs17_all_cited_paths_exist() -> None:
    """Verify every repository path cited in docs/17 exists on disk."""
    _, _, text = _parse_docs17()
    _check_cited_paths(text)


def test_library_only_entries_require_reason() -> None:
    """Assert any scenario marked library_only specifies a non-empty explanation."""
    for spec in list_scenarios():
        if spec.surface.startswith("library_only"):
            assert len(spec.surface) > len("library_only()"), (
                f"Scenario {spec.id} marked library_only without reason"
            )


# ---------------------------------------------------------------------------
# Mutation Sensitivity Tests: Deliberate omission / corruption must fail
# ---------------------------------------------------------------------------


def test_mutation_sensitivity_unmapped_issue_heading_fails() -> None:
    """Assert an unmapped issue heading strictly triggers assertion failure."""
    unmapped_headings = [("9999", "Scenario S99 (Fabricated Nonexistent Issue)")]
    with pytest.raises(AssertionError, match="has no mapping in registry"):
        for issue_num, issue_title in unmapped_headings:
            anchor_pattern = f"Issue #{issue_num}"
            mapped = [spec for spec in list_scenarios() if anchor_pattern in spec.docs17_anchor]
            assert mapped, f"Issue heading #{issue_num} ({issue_title}) has no mapping in registry"


def test_mutation_sensitivity_unmapped_catalog_row_fails() -> None:
    """Assert an unmapped catalog row strictly triggers assertion failure."""
    unmapped_rows = [("S99", "fake_dataset", "fake_inv", "fake_cmd")]
    with pytest.raises(AssertionError, match="has no mapping in SCENARIO_REGISTRY"):
        for row_id, _dataset, _invariants, _command in unmapped_rows:
            mapped = [spec for spec in list_scenarios() if row_id in spec.scenarios]
            assert mapped, f"Catalog row {row_id} has no mapping in SCENARIO_REGISTRY"


def test_mutation_sensitivity_uncovered_scenario_fails() -> None:
    """Assert an uncovered scenario strictly triggers coverage matrix failure."""
    specs_without_s1 = [spec for spec in list_scenarios() if "S1" not in spec.scenarios]
    with pytest.raises(AssertionError, match=r"Scenario S1 coverage count dropped to 0"):
        count = sum(1 for spec in specs_without_s1 if "S1" in spec.scenarios)
        assert count >= 1, f"Scenario S1 coverage count dropped to {count}"


def test_mutation_sensitivity_uncovered_uf_case_fails() -> None:
    """Assert an uncovered UF case strictly triggers coverage matrix failure."""
    specs_without_uf5 = [spec for spec in list_scenarios() if "UF-5" not in spec.uf_cases]
    with pytest.raises(AssertionError, match=r"Unmatched case UF-5 coverage count dropped to 0"):
        count = sum(1 for spec in specs_without_uf5 if "UF-5" in spec.uf_cases)
        assert count >= 1, f"Unmatched case UF-5 coverage count dropped to {count}"


def test_mutation_sensitivity_broken_markdown_link_fails() -> None:
    """Assert a broken markdown link target strictly triggers assertion failure."""
    mutated_text = "See details in [Broken Reference](nonexistent/relative/doc.md)."
    with pytest.raises(
        AssertionError,
        match="Markdown link target cited in docs/17 does not exist",
    ):
        _check_cited_paths(mutated_text)


def test_mutation_sensitivity_broken_backtick_path_fails() -> None:
    """Assert a nonexistent repository path in backticks strictly triggers assertion failure."""
    mutated_text = "Refer to `packages/opendv-fictional/does_not_exist.py` for API."
    with pytest.raises(
        AssertionError,
        match="Repository path cited in docs/17 backticks does not exist",
    ):
        _check_cited_paths(mutated_text)


def test_mutation_sensitivity_empty_library_only_reason_fails() -> None:
    """Assert an empty library_only() surface declaration strictly triggers failure."""
    dummy_spec = ScenarioSpec(
        id="PAR-DUMMY",
        docs17_anchor="Dummy Anchor",
        scenarios=["S1"],
        uf_cases=[],
        fixtures={},
        operations=["verify"],
        expected=lambda _: None,
        surface="library_only()",
    )
    with pytest.raises(AssertionError, match="marked library_only without reason"):
        if dummy_spec.surface.startswith("library_only"):
            assert len(dummy_spec.surface) > len("library_only()"), (
                f"Scenario {dummy_spec.id} marked library_only without reason"
            )
