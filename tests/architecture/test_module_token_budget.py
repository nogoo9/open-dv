"""Architecture Gate: Module Token Budget Enforcement (< 5,000 tokens per module).

Per ADR-020 and Doc 26 §1.1, all newly created and decomposed modules in the single-run
data-driven pipeline architecture must strictly maintain a token count < 5,000 tokens
measured using tiktoken with encoding 'cl100k_base'.
"""

from __future__ import annotations

from pathlib import Path
import pytest
import tiktoken

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN_LIMIT = 5000  # Strictly < 5,000 cl100k_base tokens per module
ENCODING_NAME = "cl100k_base"


def _count_tokens(path: Path, enc: tiktoken.Encoding) -> int:
    text = path.read_text(encoding="utf-8")
    return len(enc.encode(text))


@pytest.mark.architecture
def test_pipeline_decomposed_modules_token_budget() -> None:
    """Assert all 15 decomposed pipeline modules and the backward-compatibility facade
    in packages/opendv-agent-harness/ are strictly < 5,000 tokens.
    """
    enc = tiktoken.get_encoding(ENCODING_NAME)
    pipeline_dir = (
        REPO_ROOT
        / "packages"
        / "opendv-agent-harness"
        / "src"
        / "opendv_agent_harness"
        / "pipeline"
    )
    assert pipeline_dir.is_dir(), f"Pipeline directory missing: {pipeline_dir}"

    modules = sorted(pipeline_dir.glob("*.py"))
    assert len(modules) >= 15, f"Expected at least 15 decomposed modules, found {len(modules)}"

    violations: list[str] = []
    for mod in modules:
        tokens = _count_tokens(mod, enc)
        if tokens >= TOKEN_LIMIT:
            violations.append(
                f"{mod.relative_to(REPO_ROOT)}: {tokens} tokens (exceeds {TOKEN_LIMIT})"
            )

    facade = (
        REPO_ROOT
        / "packages"
        / "opendv-agent-harness"
        / "src"
        / "opendv_agent_harness"
        / "pipeline.py"
    )
    if facade.is_file():
        tokens = _count_tokens(facade, enc)
        if tokens >= TOKEN_LIMIT:
            violations.append(
                f"{facade.relative_to(REPO_ROOT)}: {tokens} tokens (exceeds {TOKEN_LIMIT})"
            )

    assert not violations, "Pipeline modules exceed 5,000 token budget:\n" + "\n".join(violations)


@pytest.mark.architecture
def test_builder_frontend_modules_token_budget() -> None:
    """Assert all wizard components in apps/opendv-web-workbench/src/explorer/builder/
    are strictly < 5,000 tokens per module.
    """
    enc = tiktoken.get_encoding(ENCODING_NAME)
    builder_dir = (
        REPO_ROOT
        / "apps"
        / "opendv-web-workbench"
        / "src"
        / "explorer"
        / "builder"
    )
    assert builder_dir.is_dir(), f"Builder directory missing: {builder_dir}"

    modules = sorted(
        f for f in builder_dir.glob("*") if f.is_file() and f.suffix in [".ts", ".tsx"]
    )
    assert len(modules) >= 8, f"Expected at least 8 builder modules, found {len(modules)}"

    violations: list[str] = []
    for mod in modules:
        tokens = _count_tokens(mod, enc)
        if tokens >= TOKEN_LIMIT:
            violations.append(
                f"{mod.relative_to(REPO_ROOT)}: {tokens} tokens (exceeds {TOKEN_LIMIT})"
            )

    assert not violations, "Builder modules exceed 5,000 token budget:\n" + "\n".join(violations)


@pytest.mark.architecture
def test_newly_created_production_modules_token_budget() -> None:
    """Assert all newly created connector, contract, core, and service modules
    in ADR-020 scope are strictly < 5,000 tokens.
    """
    enc = tiktoken.get_encoding(ENCODING_NAME)

    target_files = [
        # opendv-core
        REPO_ROOT / "packages/opendv-core/src/opendv_core/config.py",
        REPO_ROOT / "packages/opendv-core/src/opendv_core/models/vault_blueprint.py",
        # opendv-contracts
        REPO_ROOT / "packages/opendv-contracts/src/opendv_contracts/dtos/blueprint.py",
        REPO_ROOT / "packages/opendv-contracts/src/opendv_contracts/dtos/sources.py",
        # opendv-sources
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/adapters/databricks_profiler.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/adapters/databricks_uc.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/adapters/sqlite_inspector.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/config/defaults.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/registry/scheme_registry.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/spi/catalog.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/asset_management/vertical_packs/custody_clearing.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/asset_management/vertical_packs/fixed_income_rates.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/asset_management/vertical_packs/multi_asset_ibor.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/asset_management/vertical_packs/private_credit_direct_lending.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/asset_management/vertical_packs/private_equity_buyout.py",
        REPO_ROOT / "packages/opendv-sources/src/opendv_sources/asset_management/vertical_packs/public_equities_oms.py",
        # opendv-duckdb-sandbox
        REPO_ROOT / "packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/dialects/databricks_transpiler.py",
        # opendv-service
        REPO_ROOT / "packages/opendv-service/src/opendv_service/studio/blueprint_export.py",
        REPO_ROOT / "packages/opendv-service/src/opendv_service/use_cases/sources_inspect.py",
        REPO_ROOT / "packages/opendv-service/src/opendv_service/cli_spec/definitions.py",
        REPO_ROOT / "packages/opendv-service/src/opendv_service/cli_spec/pipeline_commands.py",
        REPO_ROOT / "packages/opendv-service/src/opendv_service/cli_spec/base_commands.py",
        # opendv-testkit
        REPO_ROOT / "packages/opendv-testkit/src/opendv_testkit/multi_wave_runner.py",
        REPO_ROOT / "packages/opendv-testkit/src/opendv_testkit/uc_emulator.py",
        # opendv-platform-api
        REPO_ROOT / "apps/opendv-platform-api/src/opendv_platform_api/routers/ontology_studio_blueprint.py",
        REPO_ROOT / "apps/opendv-platform-api/src/opendv_platform_api/routers/sources.py",
        # opendv-cli
        REPO_ROOT / "apps/opendv-cli/src/opendv_cli/commands/source.py",
    ]

    violations: list[str] = []
    for mod in target_files:
        assert mod.is_file(), f"Target module missing: {mod}"
        tokens = _count_tokens(mod, enc)
        if tokens >= TOKEN_LIMIT:
            violations.append(
                f"{mod.relative_to(REPO_ROOT)}: {tokens} tokens (exceeds {TOKEN_LIMIT})"
            )

    assert not violations, "Target modules exceed 5,000 token budget:\n" + "\n".join(violations)


@pytest.mark.architecture
def test_dynamic_git_diff_token_budget() -> None:
    """Enforce < 5,000 tokens across all newly created/modified .py/.ts/.tsx files
    in ADR-020 scope compared to baseline commit dd8691a.
    Excludes untouched Ontology Studio files, generated OpenAPI schemas, test suites,
    and pre-existing baseline modules that already exceeded the limit prior to ADR-020.
    """
    import subprocess

    enc = tiktoken.get_encoding(ENCODING_NAME)
    try:
        diff_output = subprocess.check_output(
            ["git", "diff", "--name-only", "dd8691a"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except Exception as exc:
        pytest.skip(f"Git diff against dd8691a unavailable: {exc}")

    try:
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
        all_files = sorted(set(diff_output) | set(untracked))
    except Exception:
        all_files = diff_output

    code_diff = [f for f in all_files if f.endswith((".py", ".ts", ".tsx"))]

    baseline_exceeding_prior_to_adr020 = {
        "packages/opendv-sources/src/opendv_sources/adapters/libreoffice_adapter.py",
        "packages/opendv-sources/src/opendv_sources/fibo/closure.py",
        "packages/opendv-sources/src/opendv_sources/ingestion/layout_evaluator.py",
        "packages/opendv-service/src/opendv_service/cli_spec/studio_commands.py",
        "packages/opendv-service/src/opendv_service/service.py",
        "packages/opendv-service/src/opendv_service/studio/session_manager.py",
        "packages/opendv-service/src/opendv_service/studio/workspace_security.py",
        "packages/opendv-axi-verifier/src/opendv_axi_verifier/codemods/alg_a4_cold_start.py",
        "packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/adversarial_batches.py",
        "packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/ir_lowerer.py",
        "apps/opendv-platform-api/src/opendv_platform_api/job_supervisor.py",
    }

    violations: list[str] = []
    for f in code_diff:
        p = Path(f)
        # Exclude tests and fixtures
        if any(part in ("tests", "fixtures") for part in p.parts):
            continue
        # Exclude generated OpenAPI artifacts and schemas
        if "schemas" in p.parts or f.endswith("openapi.ts") or p.name == "playwright.config.ts":
            continue
        # Exclude legacy pre-existing frontend ontology workbench modules only
        if "opendv-web-workbench" in p.parts and "ontology" in p.parts:
            continue
        # Exclude legacy pre-existing files that already exceeded budget prior to ADR-020
        if f.replace("\\", "/") in baseline_exceeding_prior_to_adr020:
            continue

        file_path = REPO_ROOT / f
        if not file_path.is_file():
            continue

        tokens = _count_tokens(file_path, enc)
        if tokens >= TOKEN_LIMIT:
            violations.append(
                f"{f}: {tokens} tokens (exceeds {TOKEN_LIMIT})"
            )

    assert not violations, (
        f"Dynamic git diff token budget violations (>= {TOKEN_LIMIT} tokens):\n"
        + "\n".join(violations)
    )

