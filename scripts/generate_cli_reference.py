"""Script to generate docs/16-cli-reference.md from schemas/v2/cli-spec.json."""

from __future__ import annotations

from opendv.cli.export_contracts import find_repo_root, generate_cli_reference_markdown


def main() -> int:
    repo_root = find_repo_root()
    doc_path = repo_root / "docs" / "16-cli-reference.md"

    content = generate_cli_reference_markdown()
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content, encoding="utf-8", newline="\n")

    print(f"Successfully generated {doc_path} ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
