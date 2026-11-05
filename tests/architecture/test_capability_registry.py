"""Capability registry equality and docs/17 test scenario coverage tests (T13).

Enforces:
1. Spec (cli-spec.json) ≡ Service capabilities (OpenDVService.list_capabilities())
2. Service capabilities ≡ API capability routes (accounting for reasoned exemptions)
3. docs/17 test scenario coverage: Every service capability has >= 1 test scenario
   in tests/parity/scenarios.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from opendv_contracts import load_cli_spec
from opendv_platform_api.main import create_app
from opendv_service import OpenDVService
from tests.architecture.conftest import REPO_ROOT
from tests.parity.scenarios import list_scenarios


def extract_api_capability_set(app: FastAPI) -> set[str]:
    """Map FastAPI OpenAPI routes to top-level platform capabilities."""
    openapi = app.openapi()
    capabilities: set[str] = set()

    for path in openapi.get("paths", {}):
        # Normalize /api/v1/<segment>/...
        cleaned = path.removeprefix("/api/v1/").removeprefix("/")
        segments = [s for s in cleaned.split("/") if s and not s.startswith("{")]
        if not segments:
            continue

        primary = segments[0]
        if primary == "health":
            capabilities.add("health")
        elif primary == "platform":
            capabilities.add("platform")
        elif primary == "projects":
            capabilities.add("projects")
        elif primary == "artifacts":
            capabilities.add("artifacts")
        elif primary == "pipelines":
            capabilities.add("pipeline")
        elif primary == "runs":
            if len(segments) > 1:
                sub = segments[1]
                if sub == "events":
                    capabilities.add("events")
                elif sub == "gates":
                    capabilities.add("verify")
                elif sub == "sandbox":
                    capabilities.add("sandbox")
                elif sub == "decisions":
                    capabilities.add("hold")
                elif sub == "workbench":
                    capabilities.add("workbench")
                else:
                    capabilities.add("run")
            else:
                capabilities.add("run")
        else:
            capabilities.add(primary)

    return capabilities


@pytest.mark.architecture
def test_spec_and_service_capability_registry_equality() -> None:
    """Assert that cli-spec.json command tree equals OpenDVService capabilities."""
    spec = load_cli_spec()
    spec_commands = {c["name"] for c in spec.get("commands", [])}

    service = OpenDVService()
    service_capabilities = set(service.list_capabilities())

    assert spec_commands == service_capabilities, (
        f"Spec commands and Service capabilities diverge:\n"
        f"In spec only: {spec_commands - service_capabilities}\n"
        f"In service only: {service_capabilities - spec_commands}"
    )


@pytest.mark.architecture
def test_service_and_api_capability_routes_consistency() -> None:
    """Assert that OpenDVService capabilities equal API routes with reasoned exemptions."""
    service = OpenDVService()
    service_capabilities = set(service.list_capabilities())

    app = create_app(workspace_root=REPO_ROOT)
    api_capabilities = extract_api_capability_set(app)

    # Reasoned transport-specific exemptions (Decision D2 & §3A.5)
    api_transport_exemptions = {"health", "events"}
    # Auxiliary entity storage / metadata routes
    api_storage_exemptions = {"platform", "projects", "artifacts", "workbench"}
    # Documented Phase P5 REST route parity additions (GAP-01 and GAP-02; profile added in P4.5)
    p5_pending_api_gaps = {"compile"}

    effective_api_capabilities = (
        api_capabilities - api_transport_exemptions - api_storage_exemptions
    )
    expected_service_capabilities = service_capabilities - p5_pending_api_gaps

    assert effective_api_capabilities == expected_service_capabilities, (
        f"API capability routes and Service capabilities diverge:\n"
        f"In API only: {effective_api_capabilities - expected_service_capabilities}\n"
        f"In service only: {expected_service_capabilities - effective_api_capabilities}"
    )


@pytest.mark.architecture
def test_docs17_scenario_coverage() -> None:
    """Assert that every core pipeline capability has >= 1 test scenario in scenarios.py."""
    scenarios = list_scenarios()
    assert len(scenarios) >= 10, f"Expected at least 10 docs/17 scenarios, found {len(scenarios)}"

    covered_ops = {op for s in scenarios for op in s.operations}

    # Core pipeline capabilities mapped to operations in scenarios.py
    pipeline_capabilities_map = {
        "verify": "verify",
        "compile": "compile",
        "sandbox": "sandbox",
        "pipeline": "pipeline_validate",
    }

    for cap, mapped_op in pipeline_capabilities_map.items():
        assert mapped_op in covered_ops, (
            f"Capability '{cap}' (operation '{mapped_op}') has no registered docs/17 scenarios!"
        )
        matching_scenarios = [s.id for s in scenarios if mapped_op in s.operations]
        assert len(matching_scenarios) >= 1, (
            f"Capability '{cap}' has {len(matching_scenarios)} scenarios; expected >= 1."
        )
