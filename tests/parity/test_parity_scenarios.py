"""tests/parity/test_parity_scenarios.py — Cross-surface parity test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tests.parity.normalize import normalize_for_parity
from tests.parity.scenarios import ScenarioSpec, list_scenarios
from tests.parity.transports import (
    ClientCliTransport,
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
    client_cli_transport: ClientCliTransport,
) -> None:
    """Verify cross-surface parity between Standalone CLI, Direct REST, and Client CLI transports."""
    cli_transport = StandaloneCliTransport(tmp_path / "cli_ws")
    rest_transport = DirectRestTransport(platform_api_client)

    gaps: list[tuple[str, ParityGapError]] = []
    for op in spec.operations:
        # 1. Standalone CLI execution
        try:
            cli_res = getattr(cli_transport, op)(spec.fixtures)
        except Exception as exc:
            pytest.fail(f"STANDALONE_CLI failed on operation '{op}' for {spec.id}: {exc}")

        norm_cli = normalize_for_parity(cli_res, path_root=str(tmp_path))

        # 2. Validate scenario expectation on norm_cli immediately after CLI execution
        try:
            spec.expected(norm_cli, op=op)
        except Exception as exc:
            pytest.fail(
                f"Scenario expectation failed on CLI operation '{op}' for {spec.id}: {exc}"
            )

        # 3. Direct REST execution with clean handling of acknowledged gaps
        try:
            rest_res = getattr(rest_transport, op)(spec.fixtures)
        except ParityGapError as gap:
            gaps.append((op, gap))
            continue
        except Exception as exc:
            pytest.fail(f"DIRECT_REST failed on operation '{op}' for {spec.id}: {exc}")

        # 4. Parity assertion: normalized outputs MUST match bit-for-bit
        norm_rest = normalize_for_parity(rest_res, path_root=str(tmp_path))
        assert norm_cli == norm_rest, (
            f"Parity divergence detected on operation '{op}' for {spec.id}:\n"
            f"CLI:  {norm_cli}\n"
            f"REST: {norm_rest}"
        )

        # 4b. Client CLI (opendvctl) parity assertion across supported operations
        if op in ("compile", "verify", "sandbox", "profile") and hasattr(client_cli_transport, op):
            try:
                client_res = getattr(client_cli_transport, op)(spec.fixtures)
                norm_client = normalize_for_parity(client_res, path_root=str(tmp_path))
                assert norm_cli == norm_client, (
                    f"Client CLI parity divergence detected on operation '{op}' for {spec.id}:\n"
                    f"CLI:    {norm_cli}\n"
                    f"CLIENT: {norm_client}"
                )
            except ParityGapError:
                pass
            except Exception as exc:
                pytest.fail(f"CLIENT_CLI failed on operation '{op}' for {spec.id}: {exc}")

    # 5. If acknowledged parity gaps were encountered on REST, mark xfail after all ops complete
    if gaps:
        gap_details = "; ".join(f"{op} ({gap.gap_id})" for op, gap in gaps)
        pytest.xfail(
            f"Acknowledged parity gap(s) for {spec.id}: {gap_details} (resolved in Phase P5)"
        )
