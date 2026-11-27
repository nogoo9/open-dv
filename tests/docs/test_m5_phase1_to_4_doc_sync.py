"""V2 documentation synchronization & link integrity verification (`docs/`, root docs, `.agents/`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"

ACTIVE_V2_DOCS = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "DESIGN.md",
    "docs/16-cli-reference.md",
    "docs/29-discovery-grounding-and-evolution-scenarios.md",
    "docs/30-enterprise-dv2-modeling-and-loop-engineering.md",
    "docs/31-v2-e2e-requirements-gaps-and-uat-spec.md",
    "docs/32-v2-user-experience-and-scenario-guide.md",
    "docs/components/modeling/README.md",
    "docs/components/modeling/user-guide.md",
    "docs/components/modeling/contributor-guide.md",
    "docs/designs/SITEMAP.md",
    "docs/designs/v2-clean-sheet-architecture.md",
    "docs/designs/v2-workbench-ui-design.md",
    "examples/v2_asset_management/README.md",
]

HISTORICAL_AND_THEORY_DOCS = [
    *[
        p.relative_to(REPO_ROOT).as_posix()
        for p in sorted(DOCS_DIR.glob("[0-2][0-9]*.md"))
        if p.name != "16-cli-reference.md" and int(p.name[:2]) <= 28
    ],
    "docs/33-v2-phased-implementation-and-real-data-uat-plan.md",
    "docs/DV2_AGENT_REFERENCE_GUIDE.md",
]

_SUPERSESSION_CONTEXT_RE = re.compile(
    r"(?:supersed|removed|replac|retir|delet|no longer|without\b|zero\b|"
    r"eliminat|historical|legacy\b|obsolete|deprecat|clean-sheet|alignment)",
    re.IGNORECASE,
)

_ACTIVE_STALE_TOKEN_PATTERNS = [
    ("local_token", re.compile(r"\.local_token\b")),
    ("#token=", re.compile(r"#token=")),
    ("X-OpenDV-Workbench", re.compile(r"X-OpenDV-Workbench")),
    ("OPENDV_WAIVER_KEY", re.compile(r"\bOPENDV_WAIVER_KEY\b")),
    ("STUDIO_ALREADY_INITIALIZED", re.compile(r"\bSTUDIO_ALREADY_INITIALIZED\b")),
    ("SESSION_JOB_ACTIVE", re.compile(r"\bSESSION_JOB_ACTIVE\b")),
    ("OntologyStudioApp", re.compile(r"\bOntologyStudioApp(?:\.tsx)?\b")),
    ("PipelineBuilderView", re.compile(r"\bPipelineBuilderView(?:\.tsx)?\b")),
    ("3-Level Explorer Overview", re.compile(r"3-Level Explorer Overview")),
]


def _read_doc(rel_path: str) -> str:
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"Missing documentation file: {rel_path}"
    return path.read_text(encoding="utf-8")


def _strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def _extract_python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\r?\n(.*?)```", text, flags=re.DOTALL)


@pytest.mark.parametrize("rel_path", ACTIVE_V2_DOCS)
def test_active_v2_docs_no_localhost_8000(rel_path: str) -> None:
    """Active V2 guides must use 127.0.0.1:8420 rather than legacy localhost:8000."""
    text = _read_doc(rel_path)
    assert "localhost:8000" not in text.lower(), (
        f"{rel_path} still cites legacy 'localhost:8000' instead of 127.0.0.1:8420"
    )


@pytest.mark.parametrize("rel_path", ACTIVE_V2_DOCS)
def test_active_v2_docs_no_stale_v1_instructions(rel_path: str) -> None:
    """Active V2 docs must not instruct legacy V1 auth/token/UI flows."""
    text = _read_doc(rel_path)
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _SUPERSESSION_CONTEXT_RE.search(line):
            continue
        for label, pat in _ACTIVE_STALE_TOKEN_PATTERNS:
            if pat.search(line):
                violations.append(f"L{lineno} ({label}): {line.strip()[:100]}")

    assert not violations, f"{rel_path} contains stale V1 references:\n" + "\n".join(violations)


def test_agents_claude_readme_design_v2_sync() -> None:
    """Root docs (AGENTS.md, CLAUDE.md, README.md, DESIGN.md) reflect V2 architecture & AA tokens."""
    for rel_path in ("AGENTS.md", "CLAUDE.md", "README.md"):
        text = _read_doc(rel_path)
        for token in (
            "DESIGN.md",
            "src/opendv/",
            "web/",
            "schemas/v2/",
            "VaultSchemaIR v2.0",
            "Budget Meter",
            "Run Explorer",
            "DV Schema Explorer",
            "5-Zone",
        ):
            assert token in text, f"{rel_path} missing V2 token '{token}'"
        assert re.search(r"\b7-[Ss]tep\b", text), f"{rel_path} missing 7-Step workflow mention"
        for lineno, line in enumerate(text.splitlines(), 1):
            if "opendvctl" in line and not _SUPERSESSION_CONTEXT_RE.search(line):
                pytest.fail(
                    f"{rel_path}:L{lineno} cites removed opendvctl outside supersession note: {line}"
                )

    claude_text = _read_doc("CLAUDE.md")
    for token in ("#57554f", "#8f442d", "5,000", "--allow-remote-unauthenticated", "AGENT_NOT_CONFIGURED"):
        assert token in claude_text, f"CLAUDE.md missing '{token}'"

    design_text = _read_doc("DESIGN.md")
    for token in (
        "#57554f",
        "#8f442d",
        "--sal-ink",
        "--sat-ink",
        "--pii-ink",
        "--pass-ink",
        "--danger-ink",
        ":focus-visible",
        "@xyflow/react",
        "Project Workflow",
        "DV Schema Explorer",
        "Run Explorer",
        "Settings",
        "7-Step",
        "Budget Meter",
        ".opendv.zip",
        "web/src/styles/tokens.css",
        "apps/opendv-website/src/styles/tokens.css",
    ):
        assert token in design_text, f"DESIGN.md missing V2 token/chrome item '{token}'"


@pytest.mark.parametrize("rel_path", HISTORICAL_AND_THEORY_DOCS)
def test_all_historical_and_reference_docs_have_v2_notice(rel_path: str) -> None:
    """Every `docs/00..28`, `docs/33`, and `DV2_AGENT_REFERENCE_GUIDE.md` carries a V2 header notice."""
    text = _read_doc(rel_path)
    header_window = "\n".join(text.splitlines()[:15])
    assert "V2" in header_window, f"{rel_path} missing top V2 alignment/supersession notice"
    assert "SITEMAP.md" in header_window or "v2-clean-sheet-architecture.md" in header_window, (
        f"{rel_path} top notice must link docs/designs/SITEMAP.md or v2-clean-sheet-architecture.md"
    )


def test_all_docs_python_blocks_compile() -> None:
    """Every fenced ```python block across `docs/**/*.md` must compile cleanly."""
    total_blocks = 0
    for md_path in sorted(DOCS_DIR.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        for idx, block in enumerate(_extract_python_blocks(text), 1):
            total_blocks += 1
            try:
                compile(block, f"{md_path.relative_to(REPO_ROOT)}:block_{idx}", "exec")
            except SyntaxError as err:
                pytest.fail(f"Python syntax error in {md_path} block #{idx}: {err}")
    assert total_blocks >= 11, f"Expected >= 11 python blocks in docs/, found {total_blocks}"


def test_active_v2_docs_relative_markdown_links_resolve() -> None:
    """All relative markdown links in active V2 docs must resolve cleanly on disk."""
    broken: list[str] = []
    for rel_path in ACTIVE_V2_DOCS:
        doc_path = REPO_ROOT / rel_path
        stripped = _strip_fenced_blocks(doc_path.read_text(encoding="utf-8"))
        for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", stripped):
            clean = raw_target.split("#", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:", "file:")):
                continue
            resolved = (doc_path.parent / clean).resolve()
            if not resolved.exists():
                broken.append(f"{rel_path}: {raw_target} -> {resolved}")

    assert not broken, "Broken relative markdown links in active V2 docs:\n" + "\n".join(broken)
