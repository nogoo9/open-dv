"""Architecture Gate: Scenario Matrix Synchronization (`UAT-AGT-01`, `GAP-03`)."""

from __future__ import annotations

import ast

import pytest

from tests.architecture.conftest import REPO_ROOT

RULE_PATH = REPO_ROOT / ".agents" / "rules" / "discovery-and-grounding-scenarios.md"
DOCS_29_PATH = REPO_ROOT / "docs" / "29-discovery-grounding-and-evolution-scenarios.md"
DOCS_32_PATH = REPO_ROOT / "docs" / "32-v2-user-experience-and-scenario-guide.md"
REGISTRY_TEST_PATH = REPO_ROOT / "tests" / "parity" / "test_v2_scenarios_registry.py"
WEBSITE_DATA_PATH = REPO_ROOT / "apps" / "opendv-website" / "src" / "data" / "ecosystem-data.ts"
AGENTS_MD_PATH = REPO_ROOT / "AGENTS.md"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
WORKFLOW_SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "open-dv-workflow" / "SKILL.md"

RULE_MAX_BYTES = 24 * 1024  # 24,576 bytes (< 24 KB workspace rule cap)
SCEN_IDS = tuple(f"SCEN-{i:02d}" for i in range(1, 10))
MOD_IDS = tuple(f"MOD-{i:02d}" for i in range(1, 8))
OPS_IDS = tuple(f"OPS-{i:02d}" for i in range(1, 5))
ALL_20_SCENARIO_IDS = (*SCEN_IDS, *MOD_IDS, *OPS_IDS)
TRAP_IDS = tuple(f"TRAP-{i:02d}" for i in range(1, 13))


def _extract_dict_keys_from_ast(source_text: str, target_name: str) -> list[str]:
    tree = ast.parse(source_text, filename=str(REGISTRY_TEST_PATH))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == target_name and isinstance(node.value, ast.Dict):
                return [
                    str(k.value)
                    for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                ]
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id == target_name
                    and isinstance(node.value, ast.Dict)
                ):
                    return [
                        str(k.value)
                        for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
    return []


@pytest.mark.architecture
def test_discovery_and_grounding_rule_size_and_crosslinks() -> None:
    """Assert `.agents/rules/discovery-and-grounding-scenarios.md` < 24 KB & cross-linked."""
    assert RULE_PATH.is_file(), f"Missing rule file: {RULE_PATH}"
    rule_bytes = RULE_PATH.read_bytes()
    assert len(rule_bytes) < RULE_MAX_BYTES, (
        f"{RULE_PATH} is {len(rule_bytes)} bytes (must be < {RULE_MAX_BYTES})"
    )

    rule_text = rule_bytes.decode("utf-8")
    for token in ("SCEN-01..09", "MOD-01..07", "OPS-01..04", "docs/29", "docs/32"):
        assert token in rule_text, f"{RULE_PATH.name} missing '{token}'"

    for ref_path, expected in (
        (AGENTS_MD_PATH, "29-discovery-grounding-and-evolution-scenarios.md"),
        (CLAUDE_MD_PATH, "discovery-and-grounding-scenarios.md"),
        (WORKFLOW_SKILL_PATH, "opendv"),
    ):
        assert ref_path.is_file(), f"Missing {ref_path}"
        assert expected in ref_path.read_text(encoding="utf-8")


@pytest.mark.architecture
def test_scenario_matrix_sync_across_docs_registry_and_website() -> None:
    """Assert `SCEN-01..09`, `MOD-01..07`, `OPS-01..04` sync across docs/29, docs/32, & registry."""
    for p in (DOCS_29_PATH, DOCS_32_PATH, REGISTRY_TEST_PATH, WEBSITE_DATA_PATH):
        assert p.is_file(), f"Missing scenario matrix file: {p}"

    docs29_text = DOCS_29_PATH.read_text(encoding="utf-8")
    docs32_text = DOCS_32_PATH.read_text(encoding="utf-8")
    registry_text = REGISTRY_TEST_PATH.read_text(encoding="utf-8")

    missing_in_29 = [sid for sid in SCEN_IDS if sid not in docs29_text]
    assert not missing_in_29, f"docs/29 missing scenarios: {missing_in_29}"

    missing_in_32 = [sid for sid in ALL_20_SCENARIO_IDS if sid not in docs32_text]
    assert not missing_in_32, f"docs/32 missing scenarios: {missing_in_32}"

    handler_keys = _extract_dict_keys_from_ast(registry_text, "SCENARIO_HANDLERS")
    assert set(handler_keys) == set(ALL_20_SCENARIO_IDS), (
        f"SCENARIO_HANDLERS mismatch: expected {ALL_20_SCENARIO_IDS}, got {handler_keys}"
    )

    for trap_id in TRAP_IDS:
        assert trap_id in registry_text, (
            f"{REGISTRY_TEST_PATH.name} missing trap verification for {trap_id}"
        )

    website_text = WEBSITE_DATA_PATH.read_text(encoding="utf-8")
    for marker in ("SCEN-01..04", "SCEN-05/06/08", "SCEN-07/09"):
        assert marker in website_text, f"{WEBSITE_DATA_PATH.name} missing '{marker}'"
    assert "Local Only" not in website_text
