"""Architecture Gate: V2 Module Token Budget Enforcement (< 5,000 tokens per module)."""

from __future__ import annotations

from pathlib import Path

import pytest
import tiktoken

from tests.architecture.conftest import REPO_ROOT

TOKEN_LIMIT = 5000  # Strictly < 5,000 cl100k_base tokens per module
ENCODING_NAME = "cl100k_base"


def _count_tokens(path: Path, enc: tiktoken.Encoding) -> int:
    return len(enc.encode(path.read_text(encoding="utf-8")))


@pytest.mark.architecture
@pytest.mark.parametrize(
    "surface",
    ["src_opendv", "tests_conftest", "tests_helpers", "web_src"],
)
def test_v2_module_token_budget(surface: str) -> None:
    """Enforce strictly < 5,000 cl100k_base tokens across V2 Python and TS modules (D16)."""
    enc = tiktoken.get_encoding(ENCODING_NAME)
    targets: list[Path] = []

    if surface == "src_opendv":
        src_dir = REPO_ROOT / "src" / "opendv"
        assert src_dir.is_dir(), f"V2 source directory missing: {src_dir}"
        targets = [
            p
            for p in sorted(src_dir.rglob("*.py"))
            if not p.relative_to(REPO_ROOT).as_posix().startswith("src/opendv/api/static/")
        ]
        assert targets, f"No Python modules found under {src_dir}"
    elif surface == "tests_conftest":
        conftest = REPO_ROOT / "tests" / "conftest.py"
        assert conftest.is_file(), f"Missing {conftest}"
        targets = [conftest]
    elif surface == "tests_helpers":
        helpers_dir = REPO_ROOT / "tests" / "helpers"
        if not helpers_dir.is_dir():
            return
        targets = sorted(helpers_dir.rglob("*.py"))
    elif surface == "web_src":
        web_src_dir = REPO_ROOT / "web" / "src"
        assert web_src_dir.is_dir(), f"V2 web/src directory missing: {web_src_dir}"
        targets = [
            p
            for p in sorted(web_src_dir.rglob("*"))
            if p.is_file()
            and p.suffix in (".ts", ".tsx")
            and "e2e" not in p.parts
            and "tests" not in p.parts
            and p.name != "playwright.config.ts"
            and p.relative_to(REPO_ROOT).as_posix() != "web/src/api/types.generated.ts"
            and not p.relative_to(REPO_ROOT).as_posix().startswith("src/opendv/api/static/")
        ]
        assert targets, f"No TypeScript modules found under {web_src_dir}"

    violations: list[str] = []
    for mod in targets:
        tokens = _count_tokens(mod, enc)
        if tokens >= TOKEN_LIMIT:
            violations.append(
                f"{mod.relative_to(REPO_ROOT).as_posix()}: {tokens} tokens (exceeds {TOKEN_LIMIT})"
            )

    assert not violations, (
        f"V2 module token budget violations in '{surface}' (>= {TOKEN_LIMIT} tokens):\n"
        + "\n".join(violations)
    )
