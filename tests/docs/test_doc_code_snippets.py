"""V2 documentation code snippet verification harness.

Validates fenced CLI (`bash`/`sh`/`powershell`), Python, JSON, and YAML snippets
across active V2 guides (`README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/29..32`,
`examples/v2_asset_management/README.md`, `.agents/skills/open-dv-workflow/`)
against `schemas/v2/cli-spec.json` and V2 Pydantic models.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv.models import GraphAOntology, sha256_jcs
from opendv.sources.adapters import SOURCE_ADAPTERS

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SPEC_PATH = REPO_ROOT / "schemas" / "v2" / "cli-spec.json"
GENESIS_VECTOR_PATH = REPO_ROOT / "vectors" / "genesis.json"
BM25_MATCHER_PATH = REPO_ROOT / "schemas" / "v2" / "matchers" / "bm25_fibo_v1.json"

ACTIVE_SNIPPET_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "examples" / "v2_asset_management" / "README.md",
    REPO_ROOT / "docs" / "29-discovery-grounding-and-evolution-scenarios.md",
    REPO_ROOT / "docs" / "30-enterprise-dv2-modeling-and-loop-engineering.md",
    REPO_ROOT / "docs" / "31-v2-e2e-requirements-gaps-and-uat-spec.md",
    REPO_ROOT / "docs" / "32-v2-user-experience-and-scenario-guide.md",
    REPO_ROOT / ".agents" / "skills" / "open-dv-workflow" / "SKILL.md",
    REPO_ROOT / ".agents" / "skills" / "open-dv-workflow" / "references" / "ui_and_dual_cli_playbook.md",
    REPO_ROOT / ".agents" / "skills" / "opendv-modeling" / "SKILL.md",
    REPO_ROOT / "docs" / "components" / "modeling" / "user-guide.md",
]


def _extract_fenced_blocks(filepath: Path, langs: tuple[str, ...]) -> list[str]:
    if not filepath.is_file():
        return []
    text = filepath.read_text(encoding="utf-8")
    pattern = r"```(?:" + "|".join(re.escape(lang) for lang in langs) + r")\r?\n(.*?)```"
    return re.findall(pattern, text, flags=re.DOTALL)


def _load_v2_cli_spec() -> tuple[
    set[str],
    dict[tuple[str, ...], set[str]],
    dict[tuple[str, ...], dict[str, list[str]]],
]:
    assert CLI_SPEC_PATH.is_file(), f"Missing V2 CLI spec at {CLI_SPEC_PATH}"
    spec = json.loads(CLI_SPEC_PATH.read_text(encoding="utf-8"))
    global_flags = {opt["flag"] for opt in spec.get("global_options", [])} | {"--help", "-h"}

    cmd_flags: dict[tuple[str, ...], set[str]] = {}
    opt_choices: dict[tuple[str, ...], dict[str, list[str]]] = {}
    for cmd in spec.get("subcommands", []):
        path = tuple(cmd["path"])
        flags: set[str] = {"--help", "-h"}
        choices: dict[str, list[str]] = {}
        for opt in cmd.get("options", []):
            flag = opt["flag"]
            flags.add(flag)
            if opt.get("short"):
                flags.add(opt["short"])
            if opt.get("choices"):
                choices[flag] = [str(c) for c in opt["choices"]]
        cmd_flags[path] = flags
        opt_choices[path] = choices
    return global_flags, cmd_flags, opt_choices


def _parse_opendv_line(
    tokens: list[str],
    global_flags: set[str],
    cmd_flags: dict[tuple[str, ...], set[str]],
    opt_choices: dict[tuple[str, ...], dict[str, list[str]]],
    source_label: str,
    line: str,
) -> bool:
    """Validate one `opendv ...` or `uv run opendv ...` command invocation."""
    if tokens[:3] == ["uv", "run", "opendv"]:
        idx = 3
    elif tokens[:1] == ["opendv"]:
        idx = 1
    else:
        return False

    # Consume global flags preceding the command/subcommand path
    while idx < len(tokens) and tokens[idx].startswith("-"):
        flag = tokens[idx].split("=", 1)[0]
        assert flag in global_flags, f"{source_label}: unknown global flag '{flag}' in '{line}'"
        if flag == "--api-url" and "=" not in tokens[idx] and idx + 1 < len(tokens):
            idx += 1
        idx += 1

    if idx >= len(tokens) or tokens[idx] in {"...", "<cmd>"}:
        return False

    path_parts: list[str] = []
    while idx < len(tokens) and not tokens[idx].startswith("-"):
        candidate = (*path_parts, tokens[idx])
        if candidate in cmd_flags or any(
            k[: len(candidate)] == candidate for k in cmd_flags
        ):
            path_parts.append(tokens[idx])
            idx += 1
        else:
            break

    if not path_parts:
        return False

    path_tuple = tuple(path_parts)
    if path_tuple not in cmd_flags:
        # Parent group help invocation (e.g. `opendv source --help`)
        assert any(k[: len(path_tuple)] == path_tuple for k in cmd_flags), (
            f"{source_label}: unknown opendv command '{' '.join(path_tuple)}' in '{line}'"
        )
        return True

    allowed = cmd_flags[path_tuple] | global_flags
    choices = opt_choices.get(path_tuple, {})
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.startswith("-"):
            flag = tok.split("=", 1)[0]
            assert flag in allowed, (
                f"{source_label}: flag '{flag}' not valid for 'opendv {' '.join(path_tuple)}'. "
                f"Allowed: {sorted(allowed)}"
            )
            if flag in choices:
                val = tok.split("=", 1)[1] if "=" in tok else (
                    tokens[idx + 1] if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("-") else None
                )
                if val is not None and not val.startswith("<") and "|" not in val:
                    assert val in choices[flag], (
                        f"{source_label}: invalid choice '{val}' for '{flag}' ({choices[flag]})"
                    )
        idx += 1
    return True


def test_active_v2_docs_exist_and_genesis_vector_validates() -> None:
    """Verify active V2 docs, genesis vector GraphAOntology validation, and matcher config."""
    for path in ACTIVE_SNIPPET_DOCS:
        assert path.is_file(), f"Missing active V2 doc: {path}"

    genesis = json.loads(GENESIS_VECTOR_PATH.read_text(encoding="utf-8"))
    validated = GraphAOntology.model_validate(genesis)
    assert (
        sha256_jcs(validated)
        == "0ae856bf9fdba74e83e793ea9f74106a0c14d21d73756e1e3a12abd426d0083a"
    )

    bm25_cfg = json.loads(BM25_MATCHER_PATH.read_text(encoding="utf-8"))
    assert float(bm25_cfg["grounding_threshold"]) == 0.65
    assert len(SOURCE_ADAPTERS) >= 6


def test_active_v2_cli_snippets_match_v2_cli_spec() -> None:
    """Validate all fenced bash/sh/powershell `opendv` CLI invocations against schemas/v2/cli-spec.json."""
    global_flags, cmd_flags, opt_choices = _load_v2_cli_spec()
    validated_count = 0

    for doc_path in ACTIVE_SNIPPET_DOCS:
        rel = doc_path.relative_to(REPO_ROOT).as_posix()
        for block in _extract_fenced_blocks(doc_path, ("bash", "sh", "powershell")):
            normalized = block.replace("\\\r\n", " ").replace("\\\n", " ")
            for raw_line in normalized.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("$ "):
                    line = line[2:].strip()
                if not (line.startswith("opendv ") or line.startswith("uv run opendv ")):
                    continue
                # Split chained commands on `&&` / `;` cleanly
                for segment in re.split(r"\s*(?:&&|;)\s*", line):
                    seg = segment.strip()
                    if not (seg.startswith("opendv ") or seg.startswith("uv run opendv ")):
                        continue
                    try:
                        tokens = shlex.split(seg)
                    except ValueError as err:
                        pytest.fail(f"{rel}: invalid shell syntax '{seg}': {err}")
                    if _parse_opendv_line(
                        tokens, global_flags, cmd_flags, opt_choices, rel, seg
                    ):
                        validated_count += 1

    assert validated_count >= 18, (
        f"Expected at least 18 validated V2 CLI commands across docs, got {validated_count}"
    )


def test_active_v2_docs_python_json_yaml_snippets_valid() -> None:
    """Verify Python, JSON, and YAML fenced code blocks in active V2 docs parse cleanly."""
    for doc_path in ACTIVE_SNIPPET_DOCS:
        rel = doc_path.relative_to(REPO_ROOT).as_posix()
        for idx, block in enumerate(_extract_fenced_blocks(doc_path, ("python",)), 1):
            try:
                ast.parse(block)
            except SyntaxError as err:
                pytest.fail(f"Python syntax error in {rel} block #{idx}: {err}")
        for idx, block in enumerate(_extract_fenced_blocks(doc_path, ("json",)), 1):
            try:
                parsed: Any = json.loads(block)
                assert isinstance(parsed, (dict, list))
            except json.JSONDecodeError as err:
                pytest.fail(f"JSON syntax error in {rel} block #{idx}: {err}")
        for idx, block in enumerate(_extract_fenced_blocks(doc_path, ("yaml", "yml")), 1):
            try:
                parsed = yaml.safe_load(block)
                assert parsed is None or isinstance(parsed, (dict, list))
            except yaml.YAMLError as err:
                pytest.fail(f"YAML syntax error in {rel} block #{idx}: {err}")
