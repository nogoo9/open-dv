"""Milestone 5 (FR-DOC-01) TDD verification for Phase 1-4 documentation sync.

Validates that active repository guides, architecture/spec documents, and
historical supersession banners reflect the Phase 1-4 unified 6-step Wizard,
4-view DV Schema Explorer, SessionLogsInspector, loopback open-access guards,
and D13 CLI subcommands without stale auth/UI instructions or broken links.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
CLI_SPEC_PATH = REPO_ROOT / "schemas" / "v1" / "cli-spec.json"

ACTIVE_SURFACE_DOCS = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/12-unified-platform-web-and-cli-setup.md",
    "docs/16-human-and-agent-usage-guide.md",
    "docs/19-ontology-studio-architecture.md",
    "docs/20-ontology-studio-spec.md",
    "docs/22-ontology-studio-user-guide.md",
]

CORE_WIZARD_AND_EXPLORER_DOCS = [
    "README.md",
    "docs/12-unified-platform-web-and-cli-setup.md",
    "docs/20-ontology-studio-spec.md",
    "docs/22-ontology-studio-user-guide.md",
]

D13_CLI_DOCS = [
    "README.md",
    "docs/16-human-and-agent-usage-guide.md",
    "docs/17-testing-scenarios-and-datasets.md",
    "docs/20-ontology-studio-spec.md",
    "docs/22-ontology-studio-user-guide.md",
]

HISTORICAL_SUPERSEDED_DOCS = [
    "docs/21-ontology-studio-implementation-plan.md",
    "docs/24-ontology-studio-engineering-learnings.md",
    "docs/25-data-driven-single-run-pipeline-design.md",
    "docs/26-data-driven-pipeline-team-implementation-plan.md",
]

_SUPERSESSION_CONTEXT_RE = re.compile(
    r"(?:supersed|removed|replac|retir|delet|no longer|without\b|zero\b|"
    r"eliminat|historical|legacy\b|obsolete|deprecat|pre-phase-1)",
    re.IGNORECASE,
)

_ACTIVE_STALE_TOKEN_PATTERNS = [
    ("local_token", re.compile(r"\.local_token\b")),
    ("#token=", re.compile(r"#token=")),
    ("X-OpenDV-Workbench", re.compile(r"X-OpenDV-Workbench")),
    ("K_waiver", re.compile(r"\bK_waiver\b")),
    ("PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN", re.compile(r"\bPROPOSAL_SELF_ACCEPTANCE_FORBIDDEN\b")),
    ("OntologyStudioApp", re.compile(r"\bOntologyStudioApp(?:\.tsx)?\b")),
    ("PipelineBuilderView", re.compile(r"\bPipelineBuilderView(?:\.tsx)?\b")),
    ("WizardShell", re.compile(r"\bWizardShell(?:\.tsx)?\b")),
    ("STUDIO_ALREADY_INITIALIZED", re.compile(r"\bSTUDIO_ALREADY_INITIALIZED\b")),
    ("OPENDV_WAIVER_KEY", re.compile(r"\bOPENDV_WAIVER_KEY\b")),
    ("PipelineRunProgressBar", re.compile(r"\bPipelineRunProgressBar\b")),
    ("3-Level Explorer Overview", re.compile(r"3-Level Explorer Overview")),
    (
        "Initialize local studio permissions & loopback token",
        re.compile(r"Initialize local studio permissions\s*&\s*loopback token", re.IGNORECASE),
    ),
]

_UNCONDITIONAL_STALE_INSTRUCTION_PATTERNS = [
    ("mints .local_token", re.compile(r"mints\s+`?\.local_token")),
    ("issues .local_token", re.compile(r"issues\s+`?\.local_token")),
    ("paste .local_token", re.compile(r"paste\s+the\s+token\s+stored\s+in[^\n]*\.local_token")),
    ("/ontology#token=", re.compile(r"/ontology#token=")),
    (
        "PipelineBuilderView mounts",
        re.compile(r"When\s+`?PipelineBuilderView(?:\.tsx)?`?\s+mounts"),
    ),
]

_FLAG_ALIASES: dict[str, list[str]] = {
    "--cutoff": ["--assertion-cutoff-ts"],
    "--query-type": ["--query-id"],
    "--candidate-id": ["--candidate"],
    "--input": ["--input-spec"],
    "--json": ["--json-output"],
    "--grounding-rationale": ["--waiver-rationale"],
    "--project-id": ["--project"],
}


def _read_doc(rel_path: str) -> str:
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"Missing documentation file: {rel_path}"
    return path.read_text(encoding="utf-8")


def _extract_fenced_blocks(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text, flags=re.DOTALL)


def _strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def _load_cli_command_tree() -> tuple[
    dict[tuple[str, ...], set[str]], dict[tuple[str, ...], dict[str, list[str]]]
]:
    spec = json.loads(CLI_SPEC_PATH.read_text(encoding="utf-8"))
    tree: dict[tuple[str, ...], set[str]] = {}
    opt_choices: dict[tuple[str, ...], dict[str, list[str]]] = {}

    def _traverse(node: dict[str, Any], path: tuple[str, ...]) -> None:
        opts = {"--help", "-h", "--json", "--api-url", "--verbose", "-v", "--quiet", "-q"}
        choices_map: dict[str, list[str]] = {}
        for opt in node.get("options", []):
            flag = opt.get("flag")
            short = opt.get("short")
            choices = [str(c) for c in opt["choices"]] if opt.get("choices") is not None else None
            flags = []
            if flag:
                flags.append(flag)
                flags.extend(_FLAG_ALIASES.get(flag, []))
            if short:
                flags.append(short)
            for item in flags:
                opts.add(item)
                if choices is not None:
                    choices_map[item] = choices
        tree[path] = opts
        opt_choices[path] = choices_map
        for sub in node.get("subcommands", []):
            _traverse(sub, (*path, sub["name"]))

    for cmd in spec.get("commands", []):
        _traverse(cmd, (cmd["name"],))
    return tree, opt_choices


@pytest.mark.parametrize(
    "rel_path", [*ACTIVE_SURFACE_DOCS, "docs/17-testing-scenarios-and-datasets.md"]
)
def test_active_docs_no_localhost_8000(rel_path: str) -> None:
    """Active guides must use 127.0.0.1:8420 rather than legacy localhost:8000."""
    text = _read_doc(rel_path)
    assert "localhost:8000" not in text.lower(), (
        f"{rel_path} still cites legacy 'localhost:8000' instead of 127.0.0.1:8420"
    )


@pytest.mark.parametrize("rel_path", ACTIVE_SURFACE_DOCS)
def test_active_docs_no_stale_auth_or_legacy_ui_instructions(rel_path: str) -> None:
    """Active user/setup/spec docs must not instruct stale token/CSRF/waiver or deleted UI flows."""
    text = _read_doc(rel_path)
    violations: list[str] = []

    for label, pat in _UNCONDITIONAL_STALE_INSTRUCTION_PATTERNS:
        if pat.search(text):
            violations.append(f"unconditional stale instruction ({label})")

    for lineno, line in enumerate(text.splitlines(), 1):
        if _SUPERSESSION_CONTEXT_RE.search(line):
            continue
        for label, pat in _ACTIVE_STALE_TOKEN_PATTERNS:
            if pat.search(line):
                violations.append(f"L{lineno} stale reference ({label}): {line.strip()[:100]}")

    assert not violations, f"{rel_path} contains stale auth/UI references:\n" + "\n".join(
        violations[:10]
    )


def test_docs23_supported_extensions_pointer_updated() -> None:
    """docs/23 must not claim SUPPORTED_EXTENSIONS lives in routers/ontology_studio.py."""
    text = _read_doc("docs/23-ontology-studio-extension-guide.md")
    for lineno, line in enumerate(text.splitlines(), 1):
        if "SUPPORTED_EXTENSIONS" in line and "ontology_studio.py" in line:
            pytest.fail(
                f"docs/23 L{lineno} still claims SUPPORTED_EXTENSIONS lives in ontology_studio.py"
            )
    assert "SUPPORTED_EXTENSIONS" not in text, (
        "docs/23 still references removed SUPPORTED_EXTENSIONS symbol"
    )


@pytest.mark.parametrize("rel_path", HISTORICAL_SUPERSEDED_DOCS)
def test_superseded_docs_carry_evolution_notices(rel_path: str) -> None:
    """Historical docs 21/24/25/26 must point explicitly to docs/27 and docs/28."""
    text = _read_doc(rel_path)
    assert "27-unified-ontology-pipeline-requirements-and-uat.md" in text, (
        f"{rel_path} missing link/reference to docs/27"
    )
    assert "28-unified-ontology-pipeline-design-and-plan.md" in text, (
        f"{rel_path} missing link/reference to docs/28"
    )
    assert re.search(r"supersed|historical|evolution\s+addendum|archived", text, re.IGNORECASE), (
        f"{rel_path} missing explicit supersession/historical/addendum notice"
    )
    if rel_path.endswith("24-ontology-studio-engineering-learnings.md"):
        assert "D18" in text and "D16" in text, "docs/24 addendum must cite D18 and D16"
    else:
        assert "D19" in text, f"{rel_path} supersession notice must cite D19"
        assert re.search(r"5-zone", text, re.IGNORECASE), (
            f"{rel_path} supersession notice must note 5-Zone standard"
        )


def test_agents_and_claude_md_phase1_to_4_sync() -> None:
    """AGENTS.md and CLAUDE.md must reflect DESIGN.md, 4-tab Workbench, D16/D18, and docs/27-28."""
    for rel_path in ("AGENTS.md", "CLAUDE.md"):
        text = _read_doc(rel_path)
        for token in (
            "DESIGN.md",
            "27-unified-ontology-pipeline-requirements-and-uat.md",
            "28-unified-ontology-pipeline-design-and-plan.md",
        ):
            assert token in text, f"{rel_path} missing {token}"
        assert re.search(r"5-zone", text, re.IGNORECASE), f"{rel_path} missing 5-Zone reference"
        assert re.search(r"Z0`?\s*(?:\.\.|\u2013|-|to)\s*`?Z4", text), (
            f"{rel_path} missing Z0-Z4 range"
        )
        assert "5,000" in text or "5000" in text, f"{rel_path} missing <5,000-token ceiling note"
        assert "--allow-remote-unauthenticated" in text or "D18" in text, (
            f"{rel_path} missing D18 loopback/remote guard reference"
        )

    claude_text = _read_doc("CLAUDE.md")
    for route in ("/dv-schema", "/explorer", "/settings"):
        assert route in claude_text, f"CLAUDE.md missing unified Workbench route {route}"


@pytest.mark.parametrize("rel_path", CORE_WIZARD_AND_EXPLORER_DOCS)
def test_core_docs_cover_design_md_4tab_shell_and_6step_wizard(rel_path: str) -> None:
    """Core user/setup/spec docs must document the 4-tab Claude DESIGN.md shell and 6-step Wizard."""
    text = _read_doc(rel_path)
    required_literals = [
        "DESIGN.md",
        "Ontology & Pipeline",
        "DV Schema Explorer",
        "Run Explorer",
        "Settings",
        "CitationInspectorDrawer",
        "VisualEditorDrawer",
        "StewardHoldModal",
    ]
    for lit in required_literals:
        assert lit in text, f"{rel_path} missing '{lit}'"

    for step_num in range(1, 7):
        assert re.search(rf"\bStep\s+{step_num}\b", text), f"{rel_path} missing 'Step {step_num}'"

    required_patterns = [
        ("Fast-Forward", r"fast-forward"),
        ("Export Ontology Only", r"export\s+ontology\s+only"),
        ("Ask FIBO", r"ask\s+fibo"),
        ("00..06 console pills", r"00`?\s*(?:\.\.|\u2013|-|to)\s*`?06"),
        ("Sync Drift", r"sync\s+drift"),
    ]
    for label, pat in required_patterns:
        assert re.search(pat, text, re.IGNORECASE), f"{rel_path} missing {label}"


@pytest.mark.parametrize("rel_path", CORE_WIZARD_AND_EXPLORER_DOCS)
def test_core_docs_cover_dv_schema_explorer_logs_and_guards(rel_path: str) -> None:
    """Core docs must document DV Schema Explorer, SessionLogsInspector, and D11/D18/D21/D24-D28 guards."""
    text = _read_doc(rel_path)
    required_literals = [
        "/dv-schema",
        "information_mart",
        "SessionLogsInspector",
        "/logs/stream",
        "EventSource",
        ".jsonl",
        "--allow-remote-unauthenticated",
        "422",
        "AGENT_NOT_CONFIGURED",
        "409",
        "SESSION_JOB_ACTIVE",
        "wizard_schema_version",
    ]
    for lit in required_literals:
        assert lit in text, f"{rel_path} missing '{lit}'"

    required_patterns = [
        ("validate_safe_sql / read-only SQL runner", r"validate_safe_sql|read-only\s+SQL"),
        ("5-Zone", r"5-zone"),
        ("Z0-Z4", r"Z0`?\s*(?:\.\.|\u2013|-|to)\s*`?Z4"),
        ("50 MB retention", r"50\s*MB"),
        ("exit code 40", r"exit(?:\s+code)?\s+`?40`?"),
        ("BudgetHold / Budget Hold", r"budget\s*hold"),
        ("cost meter", r"cost\s+meter"),
        ("768px breakpoint", r"768\s*px"),
        ("desktop-first / view-only", r"desktop-first|view-only"),
        ("Agent-only UI", r"agent-only"),
    ]
    for label, pat in required_patterns:
        assert re.search(pat, text, re.IGNORECASE), f"{rel_path} missing {label}"


@pytest.mark.parametrize("rel_path", D13_CLI_DOCS)
def test_d13_cli_subcommands_documented_across_guides(rel_path: str) -> None:
    """All 4 D13 CLI subcommands must be documented across README, docs/16-human, 17, 20, and 22."""
    text = _read_doc(rel_path)
    for token in ("run dv-schema", "ontology studio", "attach-source", "fibo-qa", "logs"):
        assert token in text, f"{rel_path} missing D13 CLI command token '{token}'"


def test_all_docs_python_blocks_compile() -> None:
    """Every fenced ```python block across docs/**/*.md must compile cleanly."""
    total_blocks = 0
    for md_path in sorted(DOCS_DIR.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        for idx, block in enumerate(_extract_fenced_blocks(text, "python"), 1):
            total_blocks += 1
            try:
                compile(block, f"{md_path.relative_to(REPO_ROOT)}:block_{idx}", "exec")
            except SyntaxError as err:
                pytest.fail(f"Python syntax error in {md_path} block #{idx}: {err}")
    assert total_blocks >= 11, f"Expected >= 11 python blocks in docs/, found {total_blocks}"


def test_updated_docs_relative_markdown_links_resolve() -> None:
    """All relative markdown links in root and target docs/ files must resolve on disk."""
    target_paths = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CLAUDE.md",
        *sorted(
            p
            for p in DOCS_DIR.glob("*.md")
            if p.name.startswith(
                ("12", "16-human", "17", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28")
            )
        ),
    ]
    broken: list[str] = []
    for doc_path in target_paths:
        assert doc_path.is_file(), f"Missing doc path: {doc_path}"
        stripped = _strip_fenced_blocks(doc_path.read_text(encoding="utf-8"))
        for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", stripped):
            clean = raw_target.split("#", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (doc_path.parent / clean).resolve()
            if not resolved.exists():
                broken.append(f"{doc_path.relative_to(REPO_ROOT)}: {raw_target} -> {resolved}")

    assert not broken, "Broken relative markdown links found:\n" + "\n".join(broken)


def test_docs22_d13_cli_snippets_match_cli_spec() -> None:
    """docs/22 bash/sh examples for run dv-schema and ontology studio must validate against cli-spec.json."""
    cmd_tree, opt_choices = _load_cli_command_tree()
    text = _read_doc("docs/22-ontology-studio-user-guide.md")
    blocks = _extract_fenced_blocks(text, "bash") + _extract_fenced_blocks(text, "sh")
    assert blocks, "Expected bash/sh CLI blocks in docs/22"

    seen_commands: set[tuple[str, ...]] = set()
    for block in blocks:
        normalized = block.replace("\\\n", " ")
        for raw_line in normalized.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("export "):
                continue
            if line.startswith("$ "):
                line = line[2:].strip()
            if not (line.startswith("opendv ") or line.startswith("opendvctl ")):
                continue

            tokens = shlex.split(line)
            if len(tokens) < 3:
                continue
            is_d13_scope = (tokens[1] == "run" and tokens[2] == "dv-schema") or (
                tokens[1] == "ontology" and tokens[2] == "studio"
            )
            if not is_d13_scope:
                continue

            path_tokens: list[str] = [tokens[1]]
            flag_tokens: list[str] = []
            for tok in tokens[2:]:
                if tok.startswith("-"):
                    flag_tokens.append(tok)
                elif tuple((*path_tokens, tok)) in cmd_tree or any(
                    k[: len(path_tokens) + 1] == tuple((*path_tokens, tok)) for k in cmd_tree
                ):
                    path_tokens.append(tok)
                else:
                    flag_tokens.append(tok)

            path_tuple = tuple(path_tokens)
            assert path_tuple in cmd_tree, (
                f"Command '{' '.join(path_tokens)}' in docs/22 line '{line}' missing from cli-spec.json"
            )
            seen_commands.add(path_tuple)

            allowed_flags = cmd_tree[path_tuple]
            cmd_choices = opt_choices.get(path_tuple, {})
            idx = 0
            while idx < len(flag_tokens):
                tok = flag_tokens[idx]
                if tok.startswith("-"):
                    if "=" in tok:
                        flag_clean, val = tok.split("=", 1)
                    else:
                        flag_clean = tok
                        if (
                            flag_clean in cmd_choices
                            and idx + 1 < len(flag_tokens)
                            and not flag_tokens[idx + 1].startswith("-")
                        ):
                            val = flag_tokens[idx + 1]
                            idx += 1
                        else:
                            val = None
                    assert flag_clean in allowed_flags, (
                        f"Flag '{flag_clean}' invalid for '{' '.join(path_tokens)}' in docs/22: {line}"
                    )
                    if flag_clean in cmd_choices:
                        assert val in cmd_choices[flag_clean], (
                            f"Invalid choice '{val}' for '{flag_clean}' in docs/22: {line}"
                        )
                idx += 1

    required_d13_paths = {
        ("run", "dv-schema"),
        ("ontology", "studio", "attach-source"),
        ("ontology", "studio", "fibo-qa"),
        ("ontology", "studio", "logs"),
    }
    missing = sorted(" ".join(p) for p in required_d13_paths - seen_commands)
    assert not missing, f"docs/22 bash/sh blocks missing validated examples for: {missing}"
