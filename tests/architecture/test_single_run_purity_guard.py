"""Architecture Gate: Single-Run Platform Purity Enforcement (C4 Guard).

Per ADR-019 and Doc 26 §1.1, validates:
1. Zero occurrences of `waves`, `wave_type`, or `wave_id` in CLI specifications
   (schemas/cli-spec.json, packages/opendv-contracts/schemas/cli-spec.json).
2. Zero occurrences of `waves`, `wave_type`, or `wave_id` in dual CLI implementations
   (apps/opendv-cli/src, apps/opendvctl/src).
3. Single-run contract purity in DTOs (PipelineInputDTO, SourceFeedDTO, CreateRunRequest)
   with zero `waves`, `wave_type`, or `wave_id` model fields.
4. Production service CLI spec and pipeline use cases contain zero references to
   `waves`, `wave_type`, or `wave_id`.
5. Platform API run router contains zero occurrences of `wave_type` and operates
   strictly on single-run data-driven inputs.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

BANNED_WAVE_PATTERNS = [
    re.compile(r"\bwave_type\b", re.IGNORECASE),
    re.compile(r"\bwaves\b", re.IGNORECASE),
    re.compile(r"\bwave_id\b", re.IGNORECASE),
]


@pytest.mark.architecture
def test_cli_spec_zero_wave_definitions() -> None:
    """Assert schemas/cli-spec.json and packages/opendv-contracts/schemas/cli-spec.json
    contain zero occurrences of waves, wave_type, or wave_id.
    """
    spec_paths = [
        REPO_ROOT / "schemas" / "cli-spec.json",
        REPO_ROOT / "packages" / "opendv-contracts" / "schemas" / "cli-spec.json",
    ]

    violations: list[str] = []
    for spec_path in spec_paths:
        if not spec_path.is_file():
            continue
        spec_content = spec_path.read_text(encoding="utf-8")
        data = json.loads(spec_content)
        serialized = json.dumps(data)
        for pattern in BANNED_WAVE_PATTERNS:
            matches = list(pattern.finditer(serialized))
            if matches:
                violations.append(
                    f"{spec_path.relative_to(REPO_ROOT)}: found {len(matches)} match(es) for '{pattern.pattern}'"
                )

    assert not violations, (
        "CLI specification contains multi-wave remnant definitions:\n"
        + "\n".join(violations)
        + "\nRemove waves/wave_type/wave_id from cli-spec.json per ADR-019."
    )


@pytest.mark.architecture
def test_cli_src_zero_wave_references() -> None:
    """Assert apps/opendv-cli/src and apps/opendvctl/src contain zero occurrences
    of waves, wave_type, or wave_id in production code.
    """
    cli_dirs = [
        REPO_ROOT / "apps" / "opendv-cli" / "src",
        REPO_ROOT / "apps" / "opendvctl" / "src",
    ]

    violations: list[str] = []
    for cli_dir in cli_dirs:
        if not cli_dir.is_dir():
            continue
        for src_file in cli_dir.rglob("*"):
            if src_file.is_file() and src_file.suffix in (".py", ".ts"):
                content = src_file.read_text(encoding="utf-8")
                for lno, line in enumerate(content.splitlines(), start=1):
                    for pattern in BANNED_WAVE_PATTERNS:
                        if pattern.search(line):
                            violations.append(
                                f"{src_file.relative_to(REPO_ROOT)}:{lno}: {line.strip()}"
                            )

    assert not violations, (
        "Dual CLI implementations contain multi-wave remnants:\n"
        + "\n".join(violations)
        + "\nRefactor CLI to single-run pipeline architecture per ADR-019."
    )


@pytest.mark.architecture
def test_pipeline_contract_dtos_single_run_purity() -> None:
    """Assert single-run DTOs (PipelineInputDTO, SourceFeedDTO, CreateRunRequest)
    do not contain waves, wave_type, or wave_id fields.
    """
    from opendv_contracts.dtos.pipeline import PipelineInputDTO, SourceFeedDTO
    from opendv_contracts.dtos.runs import CreateRunRequest

    dto_classes = [PipelineInputDTO, SourceFeedDTO, CreateRunRequest]
    banned_field_names = {"waves", "wave_type", "wave_id"}

    violations: list[str] = []
    for cls in dto_classes:
        fields = set(cls.model_fields.keys())
        found = fields.intersection(banned_field_names)
        if found:
            violations.append(f"{cls.__name__} contains banned fields: {found}")

    assert not violations, (
        "Single-run contract DTOs contain multi-wave fields:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_service_pipeline_and_cli_spec_purity() -> None:
    """Assert service CLI definitions and pipeline use case files contain zero
    occurrences of waves, wave_type, or wave_id.
    """
    service_files = [
        REPO_ROOT / "packages" / "opendv-service" / "src" / "opendv_service" / "cli_spec" / "definitions.py",
        REPO_ROOT / "packages" / "opendv-service" / "src" / "opendv_service" / "cli_spec" / "pipeline_commands.py",
        REPO_ROOT / "packages" / "opendv-service" / "src" / "opendv_service" / "cli_spec" / "base_commands.py",
        REPO_ROOT / "packages" / "opendv-service" / "src" / "opendv_service" / "use_cases" / "pipeline.py",
        REPO_ROOT / "packages" / "opendv-service" / "src" / "opendv_service" / "use_cases" / "sources_inspect.py",
        REPO_ROOT / "packages" / "opendv-service" / "src" / "opendv_service" / "studio" / "blueprint_export.py",
    ]

    violations: list[str] = []
    for f in service_files:
        if not f.is_file():
            continue
        content = f.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            for pattern in BANNED_WAVE_PATTERNS:
                if pattern.search(line):
                    violations.append(
                        f"{f.relative_to(REPO_ROOT)}:{lno}: {line.strip()}"
                    )

    assert not violations, (
        "Production service modules contain multi-wave remnants:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_platform_api_zero_wave_type_and_single_run_routes() -> None:
    """Assert platform API runs and sources routers contain zero occurrences of wave_type."""
    router_files = [
        REPO_ROOT / "apps" / "opendv-platform-api" / "src" / "opendv_platform_api" / "routers" / "runs.py",
        REPO_ROOT / "apps" / "opendv-platform-api" / "src" / "opendv_platform_api" / "routers" / "sources.py",
        REPO_ROOT / "apps" / "opendv-platform-api" / "src" / "opendv_platform_api" / "routers" / "ontology_studio_blueprint.py",
    ]

    wave_type_pattern = re.compile(r"\bwave_type\b", re.IGNORECASE)
    violations: list[str] = []
    for rf in router_files:
        if not rf.is_file():
            continue
        content = rf.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            if wave_type_pattern.search(line):
                violations.append(
                    f"{rf.relative_to(REPO_ROOT)}:{lno}: {line.strip()}"
                )

    assert not violations, (
        "Platform API routers contain wave_type references:\n"
        + "\n".join(violations)
    )
