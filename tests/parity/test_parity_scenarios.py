"""tests/parity/test_parity_scenarios.py — Cross-surface parity test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tests.parity.normalize import normalize_for_parity
from tests.parity.scenarios import ScenarioSpec, list_scenarios
from tests.parity.transports import (
    DirectRestTransport,
    ParityGapError,
    StandaloneCliTransport,
)


@pytest.mark.parity
@pytest.mark.parametrize("spec", list_scenarios(), ids=lambda s: s.id)
def test_scenario_parity(
    spec: ScenarioSpec,
    tmp_path: Path,
    platform_api_client: TestClient,
) -> None:
    """Verify cross-surface parity between Standalone CLI and Direct REST transports."""
    cli_transport = StandaloneCliTransport(tmp_path / "cli_ws")
    rest_transport = DirectRestTransport(platform_api_client)

    for op in spec.operations:
        # 1. Standalone CLI execution
        try:
            cli_res = getattr(cli_transport, op)(spec.fixtures)
        except Exception as exc:
            pytest.fail(f"STANDALONE_CLI failed on operation '{op}' for {spec.id}: {exc}")

        # 2. Direct REST execution with strict xfail for Phase P1 acknowledged gaps
        try:
            rest_res = getattr(rest_transport, op)(spec.fixtures)
        except ParityGapError as gap:
            pytest.xfail(
                f"Acknowledged parity gap {gap.gap_id}: {gap} (resolved in Phase P5)"
            )
        except Exception as exc:
            pytest.fail(f"DIRECT_REST failed on operation '{op}' for {spec.id}: {exc}")

        # 3. Parity assertion: normalized outputs MUST match bit-for-bit
        norm_cli = normalize_for_parity(cli_res, path_root=str(tmp_path))
        norm_rest = normalize_for_parity(rest_res, path_root=str(tmp_path))
        assert norm_cli == norm_rest, (
            f"Parity divergence detected on operation '{op}' for {spec.id}:\n"
            f"CLI:  {norm_cli}\n"
            f"REST: {norm_rest}"
        )

        # 4. Apply scenario-specific expectations
        spec.expected(norm_cli)
