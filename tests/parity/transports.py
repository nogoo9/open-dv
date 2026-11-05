"""tests/parity/transports.py — Cross-surface Transport Protocol and implementations."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import yaml

from opendv_cli.main import app_main


class ParityGapError(RuntimeError):
    """Raised when an operation encounters an acknowledged parity gap (GAP-01..04)."""

    def __init__(self, gap_id: str, message: str) -> None:
        super().__init__(f"[{gap_id}] {message}")
        self.gap_id = gap_id


@runtime_checkable
class Transport(Protocol):
    """Protocol for executing Open-DV operations across different surfaces."""

    name: str

    def profile(self, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """Profile Bronze source datasets or validate Graph B."""
        ...

    def compile(
        self,
        contract: dict[str, Any] | Path | None = None,
        ir: dict[str, Any] | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compile a contract into Canonical IR and 4-Zone SQL."""
        ...

    def verify(
        self,
        contract: dict[str, Any] | Path | None = None,
        prior: dict[str, Any] | None = None,
        graph_a: dict[str, Any] | Path | None = None,
        graph_b: dict[str, Any] | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run 6-Gate symbolic verification."""
        ...

    def sandbox(
        self,
        contract: dict[str, Any] | Path | None = None,
        query_type: str = "ADVERSARIAL_BATCHES_1_TO_4",
        cutoff: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute DuckDB shadow sandbox queries."""
        ...

    def pipeline_validate(
        self,
        spec: dict[str, Any] | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Validate pipeline configuration and manifest."""
        ...

    def pipeline_run(
        self,
        spec: dict[str, Any] | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute complete pipeline run."""
        ...


def _is_fixture_dict(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and "contract" in doc
        and not any(k in doc for k in ("tables", "entities", "contract_version"))
    )


class StandaloneCliTransport:
    """Executes CLI subcommands locally in-process."""

    name = "STANDALONE_CLI"

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def _invoke(self, argv: list[str]) -> dict[str, Any]:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = stdout_buf, stderr_buf
            code = app_main(argv)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        out_str = stdout_buf.getvalue().strip()
        if not out_str:
            return {"exit_code": code, "stderr": stderr_buf.getvalue()}
        try:
            data = json.loads(out_str)
            if isinstance(data, dict):
                return data
            return {"result": data, "exit_code": code}
        except json.JSONDecodeError:
            return {
                "raw_output": out_str,
                "exit_code": code,
                "stderr": stderr_buf.getvalue(),
            }

    def _resolve_contract_arg(self, contract: Any) -> str:
        if isinstance(contract, (str, Path)):
            return str(contract)
        if isinstance(contract, dict):
            contract_file = self.workspace_dir / "contract.yaml"
            contract_file.write_text(yaml.safe_dump(contract), encoding="utf-8")
            return str(contract_file)
        raise ValueError(f"Invalid contract argument: {contract!r}")

    def verify(
        self,
        contract: dict[str, Any] | Path | None = None,
        prior: dict[str, Any] | None = None,
        graph_a: dict[str, Any] | Path | None = None,
        graph_b: dict[str, Any] | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if _is_fixture_dict(contract):
            fixtures = contract  # type: ignore[assignment]
            contract_target = fixtures["contract"]
            graph_a_target = fixtures.get("graph_a", graph_a)
            graph_b_target = fixtures.get("graph_b", graph_b)
        else:
            contract_target = contract
            graph_a_target = graph_a
            graph_b_target = graph_b

        contract_path = self._resolve_contract_arg(contract_target)
        argv = ["verify", "--contract", contract_path, "--json"]
        if graph_a_target:
            argv.extend(["--graph-a", str(graph_a_target)])
        if graph_b_target:
            argv.extend(["--graph-b", str(graph_b_target)])
        return self._invoke(argv)

    def compile(
        self,
        contract: dict[str, Any] | Path | None = None,
        ir: dict[str, Any] | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if _is_fixture_dict(contract):
            fixtures = contract  # type: ignore[assignment]
            contract_target = fixtures["contract"]
            ir_target = fixtures.get("canonical_ir") or fixtures.get("ir") or ir
        else:
            contract_target = contract
            ir_target = ir

        contract_path = self._resolve_contract_arg(contract_target)
        argv = ["compile", "--contract", contract_path, "--json"]
        if ir_target:
            argv.extend(["--canonical-ir", str(ir_target)])
        return self._invoke(argv)

    def sandbox(
        self,
        contract: dict[str, Any] | Path | None = None,
        query_type: str = "ADVERSARIAL_BATCHES_1_TO_4",
        cutoff: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if _is_fixture_dict(contract):
            fixtures = contract  # type: ignore[assignment]
            contract_target = fixtures["contract"]
            ir_target = fixtures.get("canonical_ir") or fixtures.get("ir")
        else:
            contract_target = contract
            ir_target = kwargs.get("canonical_ir") or kwargs.get("ir")

        contract_path = self._resolve_contract_arg(contract_target)
        argv = [
            "sandbox",
            "--contract",
            contract_path,
            "--query-type",
            query_type,
            "--json",
        ]
        if ir_target:
            argv.extend(["--canonical-ir", str(ir_target)])
        if cutoff:
            argv.extend(["--cutoff", cutoff])
        return self._invoke(argv)

    def profile(
        self,
        spec: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        spec = spec or {}
        argv = ["profile", "--json"]
        if "graph_b" in spec:
            argv.extend(["--graph-b", str(spec["graph_b"])])
        elif "source_csv" in spec:
            argv.extend(["--source-csv", str(spec["source_csv"])])
        return self._invoke(argv)

    def pipeline_validate(
        self,
        spec: dict[str, Any] | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(spec, (str, Path)):
            manifest = str(spec)
        elif isinstance(spec, dict):
            manifest_val = (
                spec.get("manifest")
                or spec.get("input_spec_path")
                or spec.get("input_path")
            )
            if manifest_val is not None:
                manifest = str(manifest_val)
            else:
                manifest_file = self.workspace_dir / "pipeline_manifest.yaml"
                manifest_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
                manifest = str(manifest_file)
        else:
            manifest = str(spec)

        argv = ["pipeline", "validate", "--input", manifest, "--json"]
        return self._invoke(argv)

    def pipeline_run(
        self,
        spec: dict[str, Any] | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(spec, (str, Path)):
            manifest = str(spec)
        elif isinstance(spec, dict):
            manifest_val = (
                spec.get("manifest")
                or spec.get("input_spec_path")
                or spec.get("input_path")
            )
            if manifest_val is not None:
                manifest = str(manifest_val)
            else:
                manifest_file = self.workspace_dir / "pipeline_manifest.yaml"
                manifest_file.write_text(yaml.safe_dump(spec), encoding="utf-8")
                manifest = str(manifest_file)
        else:
            manifest = str(spec)

        argv = ["pipeline", "run", "--input", manifest, "--json"]
        return self._invoke(argv)


class DirectRestTransport:
    """Executes requests against the platform REST/SSE API."""

    name = "DIRECT_REST"

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def profile(self, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        raise ParityGapError(
            "GAP-01", "POST /api/v1/profile is not implemented until Phase P5 (#89)"
        )

    def compile(
        self,
        contract: dict[str, Any] | Path | None = None,
        ir: dict[str, Any] | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise ParityGapError(
            "GAP-02", "POST /api/v1/compile is not implemented until Phase P5 (#89)"
        )

    def verify(
        self,
        contract: dict[str, Any] | Path | None = None,
        prior: dict[str, Any] | None = None,
        graph_a: dict[str, Any] | Path | None = None,
        graph_b: dict[str, Any] | Path | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise ParityGapError(
            "GAP-03", "POST /api/v1/verify is not implemented until Phase P5 (#89)"
        )

    def sandbox(
        self,
        contract: dict[str, Any] | Path | None = None,
        query_type: str = "ADVERSARIAL_BATCHES_1_TO_4",
        cutoff: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise ParityGapError(
            "GAP-04", "POST /api/v1/sandbox is not implemented until Phase P5 (#89)"
        )

    def pipeline_validate(
        self,
        spec: dict[str, Any] | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(spec, (str, Path)):
            input_path = str(spec)
        elif isinstance(spec, dict):
            input_path = str(
                spec.get("manifest")
                or spec.get("input_spec_path")
                or spec.get("input_path")
                or ""
            )
        else:
            input_path = str(spec)

        res = self.client.post("/api/v1/pipelines/validate", json={"input_spec_path": input_path})
        if res.status_code != 200:
            return {"status": "ERROR", "detail": res.text, "exit_code": 1}
        return res.json()

    def pipeline_run(
        self,
        spec: dict[str, Any] | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(spec, (str, Path)):
            input_path = str(spec)
        elif isinstance(spec, dict):
            input_path = str(
                spec.get("manifest")
                or spec.get("input_spec_path")
                or spec.get("input_path")
                or ""
            )
        else:
            input_path = str(spec)

        res = self.client.post("/api/v1/pipelines/runs", json={"input_spec_path": input_path})
        if res.status_code not in (200, 201):
            return {"status": "ERROR", "detail": res.text, "exit_code": 1}
        return res.json()
