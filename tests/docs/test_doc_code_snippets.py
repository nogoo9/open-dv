"""Documentation code snippet verification test harness (Track 6C / Milestone M6).

Validates all fenced Python, Bash/CLI, JSON, and YAML code blocks across:
- docs/22-ontology-studio-user-guide.md
- docs/23-ontology-studio-extension-guide.md

Conforms to ADR-001..016, docs/19, docs/20, docs/21 (Tasks 6C.1 & 6C.2),
and schemas/v1/cli-spec.json.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_contracts.spi.reference_ontology import ReferenceOntologyAdapter
from opendv_service.ontology.matching.bm25 import ConceptMatcher
from opendv_sources.ingestion.adapter import DocumentAdapter

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_repo_root() -> Path:
    curr = Path(__file__).resolve().parent
    for p in [curr, curr.parent, curr.parent.parent]:
        if (p / "schemas" / "v1" / "cli-spec.json").is_file():
            return p
    return REPO_ROOT


ROOT_DIR = _find_repo_root()
DOCS_22_PATH = ROOT_DIR / "docs" / "22-ontology-studio-user-guide.md"
DOCS_23_PATH = ROOT_DIR / "docs" / "23-ontology-studio-extension-guide.md"
CLI_SPEC_PATH = ROOT_DIR / "schemas" / "v1" / "cli-spec.json"


def _extract_fenced_blocks(filepath: Path, lang: str) -> list[str]:
    """Extract code blocks matching ```lang ... ``` from a markdown file."""
    if not filepath.is_file():
        return []
    text = filepath.read_text(encoding="utf-8")
    return re.findall(rf"```{lang}\n(.*?)```", text, flags=re.DOTALL)


def _load_cli_spec() -> dict[str, Any]:
    """Load canonical CLI specification tree from schemas/v1/cli-spec.json."""
    assert CLI_SPEC_PATH.is_file(), f"Missing CLI spec at {CLI_SPEC_PATH}"
    return json.loads(CLI_SPEC_PATH.read_text(encoding="utf-8"))


def _extract_command_tree(cli_spec: dict[str, Any]) -> dict[tuple[str, ...], set[str]]:
    """Flatten cli-spec command hierarchy into a map of command paths to allowed flags."""
    tree: dict[tuple[str, ...], set[str]] = {}

    def _traverse(node: dict[str, Any], path: tuple[str, ...]) -> None:
        opts: set[str] = set()
        for opt in node.get("options", []):
            if opt.get("flag"):
                opts.add(opt["flag"])
            if opt.get("short"):
                opts.add(opt["short"])
        # Standard global/client options
        opts.update({
            "--help", "-h", "--json", "--api-url", "--token",
            "--verbose", "-v", "--quiet", "-q",
        })

        subs = node.get("subcommands", [])
        if subs:
            tree[path] = opts
            for sub in subs:
                sub_path = (*path, sub["name"])
                _traverse(sub, sub_path)
        else:
            tree[path] = opts

    for cmd in cli_spec.get("commands", []):
        _traverse(cmd, (cmd["name"],))

    return tree


# -----------------------------------------------------------------------------
# Track 6C.1: User Guide (docs/22) Tests
# -----------------------------------------------------------------------------


def test_docs_22_file_exists() -> None:
    """Verify docs/22-ontology-studio-user-guide.md exists and is non-empty."""
    assert DOCS_22_PATH.is_file(), f"Expected {DOCS_22_PATH} to exist."
    text = DOCS_22_PATH.read_text(encoding="utf-8")
    line_count = len(text.strip().splitlines())
    assert line_count >= 400, (
        f"User guide should be comprehensive (>= 400 lines, got {line_count})."
    )


def test_docs_22_python_snippets() -> None:
    """Extract and syntax-validate all Python code blocks in docs/22."""
    blocks = _extract_fenced_blocks(DOCS_22_PATH, "python")
    for idx, block in enumerate(blocks, 1):
        try:
            ast.parse(block)
        except SyntaxError as err:
            pytest.fail(f"Syntax error in docs/22 python block #{idx}: {err}\nBlock:\n{block}")


def test_docs_22_cli_snippets() -> None:
    """Validate all CLI invocations in docs/22 against schemas/v1/cli-spec.json."""
    spec = _load_cli_spec()
    cmd_tree = _extract_command_tree(spec)

    blocks = _extract_fenced_blocks(DOCS_22_PATH, "bash") + _extract_fenced_blocks(
        DOCS_22_PATH, "sh"
    )
    assert blocks, "Expected bash/sh CLI snippets in docs/22."

    tested_commands_count = 0
    for block in blocks:
        # Normalize line continuations
        normalized = block.replace("\\\n", " ")
        for raw_line in normalized.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("export "):
                continue

            # Strip leading prompt if present
            if line.startswith("$ "):
                line = line[2:].strip()

            if not (line.startswith("opendv ") or line.startswith("opendvctl ")):
                continue

            try:
                tokens = shlex.split(line)
            except ValueError as err:
                pytest.fail(f"Invalid shell syntax in docs/22: {line} ({err})")

            if len(tokens) < 2:
                continue

            # Resolve command path (e.g. ('ontology', 'studio', 'create'))
            cmd_root = tokens[1]
            if cmd_root not in ("ontology", "serve", "config", "pipeline", "auth"):
                continue

            path_tokens: list[str] = [cmd_root]
            flag_tokens: list[str] = []
            i = 2
            while i < len(tokens):
                tok = tokens[i]
                if tok.startswith("-"):
                    flag_tokens.append(tok)
                elif tuple((*path_tokens, tok)) in cmd_tree or any(
                    k[: len(path_tokens) + 1] == tuple((*path_tokens, tok)) for k in cmd_tree
                ):
                    path_tokens.append(tok)
                else:
                    flag_tokens.append(tok)
                i += 1

            path_tuple = tuple(path_tokens)
            cmd_str = " ".join(path_tokens)
            assert (
                path_tuple in cmd_tree
            ), f"Command '{cmd_str}' in line '{line}' not recognized in cli-spec.json."

            allowed_flags = cmd_tree[path_tuple]
            for flag in flag_tokens:
                if flag.startswith("-"):
                    flag_clean = flag.split("=")[0]
                    assert (
                        flag_clean in allowed_flags
                    ), (
                        f"Flag '{flag_clean}' in line '{line}' not recognized for command "
                        f"{cmd_str}. Allowed: {sorted(allowed_flags)}"
                    )

            tested_commands_count += 1

    assert tested_commands_count >= 15, (
        f"Expected at least 15 verified CLI commands, found {tested_commands_count}."
    )


def test_docs_22_json_yaml_snippets() -> None:
    """Validate all JSON and YAML code blocks in docs/22 parse cleanly and validate operations."""
    from pydantic import ValidationError

    from opendv_cli.commands.ontology_studio import _normalize_delta_operation

    json_blocks = _extract_fenced_blocks(DOCS_22_PATH, "json")
    for idx, block in enumerate(json_blocks, 1):
        try:
            parsed = json.loads(block)
            assert isinstance(parsed, (dict, list)), f"Block #{idx} must parse as dict or list."
            if isinstance(parsed, dict) and "operations" in parsed:
                for op_idx, op in enumerate(parsed["operations"]):
                    try:
                        _normalize_delta_operation(op)
                    except (ValueError, TypeError, KeyError, ValidationError) as err:
                        pytest.fail(
                            f"Invalid operation #{op_idx} in docs/22 JSON block #{idx}: {err}\n"
                            f"Operation: {op}"
                        )
        except json.JSONDecodeError as err:
            pytest.fail(f"Invalid JSON syntax in docs/22 block #{idx}: {err}\nBlock:\n{block}")

    yaml_blocks = _extract_fenced_blocks(DOCS_22_PATH, "yaml") + _extract_fenced_blocks(
        DOCS_22_PATH, "yml"
    )
    for idx, block in enumerate(yaml_blocks, 1):
        try:
            parsed = yaml.safe_load(block)
            assert isinstance(parsed, (dict, list)), f"Block #{idx} must parse as dict or list."
            if isinstance(parsed, dict) and "operations" in parsed:
                for op_idx, op in enumerate(parsed["operations"]):
                    try:
                        _normalize_delta_operation(op)
                    except (ValueError, TypeError, KeyError, ValidationError) as err:
                        pytest.fail(
                            f"Invalid operation #{op_idx} in docs/22 YAML block #{idx}: {err}\n"
                            f"Operation: {op}"
                        )
        except yaml.YAMLError as err:
            pytest.fail(f"Invalid YAML syntax in docs/22 block #{idx}: {err}\nBlock:\n{block}")


def test_docs_22_content_coverage() -> None:
    """Verify docs/22 covers all required architectural concepts, journeys, and error precedence."""
    text = DOCS_22_PATH.read_text(encoding="utf-8")

    # Architecture & Core Concepts
    assert "Dual-Mode Engine" in text or "dual-mode" in text
    assert "Content-Addressable Storage" in text or "CAS" in text
    assert "99d4aa051a49f40596c53210592853fde09ee6995db5b1ff0ca8e57170dffa8e" in text
    assert "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81" in text

    # 4 User Journeys
    assert "Journey 1" in text
    assert "Journey 2" in text
    assert "Journey 3" in text
    assert "Journey 4" in text

    # Web Workbench Walkthrough
    assert "/ontology" in text
    assert "Sources" in text
    assert "Clarification" in text
    assert "React Flow" in text or "elkjs" in text
    assert "Level-of-Detail" in text or "LOD" in text
    assert "Diff Overlay" in text or "diff" in text

    # 11-step Error Precedence & RFC 9457
    assert "11-step" in text or "Error Precedence" in text
    assert "RFC 9457" in text
    assert "problem_type_uri" in text or "urn:opendv:error:" in text


# -----------------------------------------------------------------------------
# Track 6C.2: Extension Guide (docs/23) Tests
# -----------------------------------------------------------------------------


def test_docs_23_file_exists() -> None:
    """Verify docs/23-ontology-studio-extension-guide.md exists and is non-empty."""
    assert DOCS_23_PATH.is_file(), f"Expected {DOCS_23_PATH} to exist."
    text = DOCS_23_PATH.read_text(encoding="utf-8")
    line_count = len(text.strip().splitlines())
    assert line_count >= 350, (
        f"Extension guide should be comprehensive (>= 350 lines, got {line_count})."
    )


def test_docs_23_python_spi_implementations() -> None:
    """Extract and execute all Python SPI implementations in docs/23, asserting conformance."""
    blocks = _extract_fenced_blocks(DOCS_23_PATH, "python")
    assert blocks, "Expected Python SPI code blocks in docs/23."

    # Verify AST parses cleanly on every block
    for idx, block in enumerate(blocks, 1):
        try:
            ast.parse(block)
        except SyntaxError as err:
            pytest.fail(f"Syntax error in docs/23 python block #{idx}: {err}\nBlock:\n{block}")

    # Execute all blocks sequentially in an isolated module
    mod_name = "doc23_extension_snippets_runtime"
    mod = types.ModuleType(mod_name)
    sys.modules[mod_name] = mod

    for idx, block in enumerate(blocks, 1):
        try:
            exec(compile(block, f"docs_23_block_{idx}.py", "exec"), mod.__dict__)
        except (RuntimeError, TypeError, ValueError, AttributeError, NameError, ImportError) as err:
            pytest.fail(f"Execution failed for docs/23 python block #{idx}: {err}\nBlock:\n{block}")

    # Extension Point 1: Custom DocumentAdapter subclass
    assert hasattr(mod, "XmlSchemaDocumentAdapter"), "Expected XmlSchemaDocumentAdapter in docs/23."
    xml_adapter_cls = mod.XmlSchemaDocumentAdapter
    assert issubclass(
        xml_adapter_cls, DocumentAdapter
    ), "XmlSchemaDocumentAdapter must subclass DocumentAdapter."
    xml_adapter = xml_adapter_cls()
    assert xml_adapter.supports_extension(".xsd")
    assert xml_adapter.supports_mime_type("application/xml")

    # Extension Point 2: Custom ReferenceOntologyAdapter implementation
    assert hasattr(
        mod, "HealthcareReferenceOntologyLoader"
    ), "Expected HealthcareReferenceOntologyLoader in docs/23."
    hc_loader_cls = mod.HealthcareReferenceOntologyLoader
    hc_loader = hc_loader_cls()
    assert isinstance(
        hc_loader, ReferenceOntologyAdapter
    ), "HealthcareReferenceOntologyLoader must implement ReferenceOntologyAdapter SPI."
    assert hasattr(hc_loader, "lookup_by_uri")
    assert hasattr(hc_loader, "search_candidates")

    # Extension Point 3: Custom ConceptMatcher subclass
    assert hasattr(
        mod, "DomainAwareConceptMatcher"
    ), "Expected DomainAwareConceptMatcher in docs/23."
    matcher_cls = mod.DomainAwareConceptMatcher
    assert issubclass(
        matcher_cls, ConceptMatcher
    ), "DomainAwareConceptMatcher must subclass ConceptMatcher SPI."
    matcher = matcher_cls()
    assert hasattr(matcher, "index_concepts")
    assert hasattr(matcher, "lookup_concept")
    assert hasattr(matcher, "match")
    assert hasattr(matcher, "score")

    # Extension Point 4: Custom Persona & Agent Harness Spec Builder
    assert hasattr(
        mod, "build_compliance_scout_task_spec"
    ), "Expected build_compliance_scout_task_spec in docs/23."
    build_persona_fn = mod.build_compliance_scout_task_spec
    task_spec = build_persona_fn(focus_domains=["GDPR", "HIPAA"])
    assert task_spec.task_id.startswith("task-compliance-")
    assert task_spec.persona == "term-scout" or "scout" in task_spec.persona

    # Extension Point 5: Custom Export Serializer
    assert hasattr(mod, "CypherDDLExporter"), "Expected CypherDDLExporter in docs/23."
    exporter_cls = mod.CypherDDLExporter
    exporter = exporter_cls()
    assert hasattr(exporter, "export_cypher"), "CypherDDLExporter must provide export_cypher."


def test_docs_23_json_yaml_snippets() -> None:
    """Validate all JSON and YAML code blocks in docs/23 parse cleanly."""
    json_blocks = _extract_fenced_blocks(DOCS_23_PATH, "json")
    for idx, block in enumerate(json_blocks, 1):
        try:
            parsed = json.loads(block)
            assert isinstance(parsed, (dict, list)), f"Block #{idx} must parse as dict or list."
        except json.JSONDecodeError as err:
            pytest.fail(f"Invalid JSON syntax in docs/23 block #{idx}: {err}\nBlock:\n{block}")

    yaml_blocks = _extract_fenced_blocks(DOCS_23_PATH, "yaml") + _extract_fenced_blocks(
        DOCS_23_PATH, "yml"
    )
    for idx, block in enumerate(yaml_blocks, 1):
        try:
            parsed = yaml.safe_load(block)
            assert isinstance(parsed, (dict, list)), f"Block #{idx} must parse as dict or list."
        except yaml.YAMLError as err:
            pytest.fail(f"Invalid YAML syntax in docs/23 block #{idx}: {err}\nBlock:\n{block}")


def test_docs_23_structural_coverage() -> None:
    """Verify docs/23 covers all 5 extension SPIs and architectural guidelines."""
    text = DOCS_23_PATH.read_text(encoding="utf-8")

    assert "DocumentAdapter" in text
    assert "ReferenceOntologyAdapter" in text
    assert "ConceptMatcher" in text
    assert "BasePersona" in text or "AgentPersona" in text or "persona" in text
    assert "RDFExporter" in text or "export" in text
