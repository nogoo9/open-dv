"""tests/parity/transports.py — Cross-surface Transport Protocol and implementations."""

from __future__ import annotations

import contextlib
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

    def _resolve_contract_arg(self, contract: Any, filename: str = "contract.yaml") -> str:
        if isinstance(contract, (str, Path)):
            return str(contract)
        if isinstance(contract, dict):
            contract_file = self.workspace_dir / filename
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
            prior_target = fixtures.get("prior_contract", prior)
            compiled_sql_models = fixtures.get("compiled_sql_models") or kwargs.get("compiled_sql_models")
            compiled_sql = fixtures.get("compiled_sql") or kwargs.get("compiled_sql")
        else:
            contract_target = contract
            graph_a_target = graph_a
            graph_b_target = graph_b
            prior_target = prior or kwargs.get("prior_contract")
            compiled_sql_models = kwargs.get("compiled_sql_models")
            compiled_sql = kwargs.get("compiled_sql")

        contract_path = self._resolve_contract_arg(contract_target, filename="candidate_contract.yaml")
        argv = ["verify", "--contract", contract_path, "--json"]
        if graph_a_target:
            argv.extend(["--graph-a", str(graph_a_target)])
        if graph_b_target:
            argv.extend(["--graph-b", str(graph_b_target)])
        if prior_target:
            prior_path = self._resolve_contract_arg(prior_target, filename="prior_contract.yaml")
            argv.extend(["--prior-contract", str(prior_path)])
        apply_codemods = kwargs.get("apply_codemods")
        if _is_fixture_dict(contract) and "apply_codemods" in contract:
            apply_codemods = contract["apply_codemods"]
        if apply_codemods is False:
            argv.append("--no-apply-codemods")
        if compiled_sql_models:
            sql_lines = [f"{sql_text}\n" for sql_text in compiled_sql_models.values()]
            sql_file = self.workspace_dir / "compiled_models.sql"
            sql_file.write_text("\n".join(sql_lines), encoding="utf-8")
            argv.extend(["--compiled-sql", str(sql_file)])
        elif compiled_sql:
            if isinstance(compiled_sql, Path) or (
                isinstance(compiled_sql, str) and Path(compiled_sql).is_file()
            ):
                argv.extend(["--compiled-sql", str(compiled_sql)])
            else:
                sql_file = self.workspace_dir / "compiled.sql"
                sql_file.write_text(str(compiled_sql), encoding="utf-8")
                argv.extend(["--compiled-sql", str(sql_file)])
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
        if "initial_contract" in kwargs and kwargs["initial_contract"]:
            argv.extend(["--initial-contract", str(kwargs["initial_contract"])])
        elif "initial_contract_path" in kwargs and kwargs["initial_contract_path"]:
            argv.extend(["--initial-contract", str(kwargs["initial_contract_path"])])
        if "harness_config" in kwargs and kwargs["harness_config"]:
            argv.extend(["--harness-config", str(kwargs["harness_config"])])
        if "output_dir" in kwargs and kwargs["output_dir"]:
            argv.extend(["--output-dir", str(kwargs["output_dir"])])
        if "wave_id" in kwargs and kwargs["wave_id"]:
            argv.extend(["--wave-id", str(kwargs["wave_id"])])
        return self._invoke(argv)


class DirectRestTransport:
    """Executes requests against the platform REST/SSE API."""

    name = "DIRECT_REST"

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def _wrap_res(self, res: httpx.Response) -> dict[str, Any]:
        if res.status_code >= 400:
            return {"status": "ERROR", "detail": res.text, "exit_code": 1}
        data = res.json()
        if isinstance(data, dict):
            data["mode"] = "remote"
        return data

    def profile(self, spec: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        spec = spec or {}
        body: dict[str, Any] = {}
        if "graph_b" in spec:
            body["graph_b_path"] = str(spec["graph_b"])
            gp = Path(spec["graph_b"])
            if gp.is_file():
                with contextlib.suppress(Exception):
                    body["graph_b_content"] = json.loads(gp.read_text(encoding="utf-8"))
        elif "source_csv" in spec:
            body["source_path"] = str(spec["source_csv"])
            sp = Path(spec["source_csv"])
            if sp.is_file():
                with contextlib.suppress(Exception):
                    body["file_content"] = sp.read_text(encoding="utf-8")
                    body["client_path"] = str(spec["source_csv"])
        elif "source" in spec:
            body["source"] = spec["source"]
        elif "file_content" in spec:
            body["file_content"] = spec["file_content"]
            body["client_path"] = spec.get("client_path")

        if "source_system" in spec:
            body["source_system"] = spec["source_system"]
        if "table_name" in spec:
            body["table_name"] = spec["table_name"]
        if "project_id" in spec:
            body["project_id"] = spec["project_id"]

        res = self.client.post("/api/v1/profile", json=body)
        return self._wrap_res(res)

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

        body: dict[str, Any] = {}
        if isinstance(contract_target, (str, Path)):
            body["contract_path"] = str(contract_target)
            cp = Path(contract_target)
            if cp.is_file():
                with contextlib.suppress(Exception):
                    body["contract_content"] = cp.read_text(encoding="utf-8")
        elif isinstance(contract_target, dict):
            body["contract_content"] = yaml.safe_dump(contract_target)

        if ir_target is not None:
            if isinstance(ir_target, (str, Path)):
                body["canonical_ir_path"] = str(ir_target)
                ip = Path(ir_target)
                if ip.is_file():
                    with contextlib.suppress(Exception):
                        body["canonical_ir_content"] = ip.read_text(encoding="utf-8")
            elif isinstance(ir_target, dict):
                body["canonical_ir_content"] = json.dumps(ir_target)

        if "run_id" in kwargs and kwargs["run_id"]:
            body["run_id"] = kwargs["run_id"]
        if "project_id" in kwargs and kwargs["project_id"]:
            body["project_id"] = kwargs["project_id"]

        res = self.client.post("/api/v1/compile", json=body)
        return self._wrap_res(res)

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
            prior_target = fixtures.get("prior_contract", prior)
        else:
            contract_target = contract
            graph_a_target = graph_a
            graph_b_target = graph_b
            prior_target = prior or kwargs.get("prior_contract")

        body: dict[str, Any] = {}
        if isinstance(contract_target, (str, Path)):
            body["contract_path"] = str(contract_target)
            cp = Path(contract_target)
            if cp.is_file():
                with contextlib.suppress(Exception):
                    body["contract_content"] = cp.read_text(encoding="utf-8")
        elif isinstance(contract_target, dict):
            body["contract_content"] = yaml.safe_dump(contract_target)

        if graph_a_target:
            if isinstance(graph_a_target, (str, Path)):
                body["graph_a_path"] = str(graph_a_target)
                gap = Path(graph_a_target)
                if gap.is_file():
                    with contextlib.suppress(Exception):
                        body["graph_a_content"] = gap.read_text(encoding="utf-8")
            elif isinstance(graph_a_target, dict):
                body["graph_a_content"] = yaml.safe_dump(graph_a_target)

        if graph_b_target:
            if isinstance(graph_b_target, (str, Path)):
                body["graph_b_path"] = str(graph_b_target)
                gbp = Path(graph_b_target)
                if gbp.is_file():
                    with contextlib.suppress(Exception):
                        body["graph_b_content"] = json.loads(gbp.read_text(encoding="utf-8"))
            elif isinstance(graph_b_target, dict):
                body["graph_b_content"] = graph_b_target

        if prior_target:
            if isinstance(prior_target, (str, Path)):
                body["prior_contract_path"] = str(prior_target)
                pcp = Path(prior_target)
                if pcp.is_file():
                    with contextlib.suppress(Exception):
                        body["prior_contract_content"] = pcp.read_text(encoding="utf-8")
            elif isinstance(prior_target, dict):
                body["prior_contract_content"] = yaml.safe_dump(prior_target)

        compiled_sql_models = kwargs.get("compiled_sql_models")
        compiled_sql = kwargs.get("compiled_sql")
        if _is_fixture_dict(contract):
            fixtures = contract  # type: ignore[assignment]
            compiled_sql_models = fixtures.get("compiled_sql_models") or compiled_sql_models
            compiled_sql = fixtures.get("compiled_sql") or compiled_sql
        if compiled_sql_models:
            body["compiled_sql_models"] = compiled_sql_models
        elif compiled_sql:
            if isinstance(compiled_sql, Path) or (
                isinstance(compiled_sql, str) and Path(compiled_sql).is_file()
            ):
                body["compiled_sql"] = Path(compiled_sql).read_text(encoding="utf-8")
            else:
                body["compiled_sql"] = str(compiled_sql)

        apply_codemods = kwargs.get("apply_codemods")
        if _is_fixture_dict(contract) and "apply_codemods" in contract:
            apply_codemods = contract["apply_codemods"]
        if apply_codemods is not None:
            body["apply_codemods"] = apply_codemods

        res = self.client.post("/api/v1/verify", json=body)
        return self._wrap_res(res)

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

        body: dict[str, Any] = {
            "query_type": query_type,
        }
        if cutoff:
            body["assertion_cutoff_ts"] = cutoff

        if isinstance(contract_target, (str, Path)):
            body["contract_path"] = str(contract_target)
            cp = Path(contract_target)
            if cp.is_file():
                with contextlib.suppress(Exception):
                    body["contract_content"] = cp.read_text(encoding="utf-8")
        elif isinstance(contract_target, dict):
            body["contract_content"] = yaml.safe_dump(contract_target)

        if ir_target is not None:
            if isinstance(ir_target, (str, Path)):
                body["canonical_ir_path"] = str(ir_target)
                ip = Path(ir_target)
                if ip.is_file():
                    with contextlib.suppress(Exception):
                        body["canonical_ir_content"] = ip.read_text(encoding="utf-8")
            elif isinstance(ir_target, dict):
                body["canonical_ir_content"] = json.dumps(ir_target)

        res = self.client.post("/api/v1/sandbox", json=body)
        return self._wrap_res(res)

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
        return self._wrap_res(res)

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

        body: dict[str, Any] = {"input_spec_path": input_path}
        if "initial_contract_path" in kwargs and kwargs["initial_contract_path"]:
            body["initial_contract_path"] = str(kwargs["initial_contract_path"])
        elif "initial_contract" in kwargs and kwargs["initial_contract"]:
            body["initial_contract"] = kwargs["initial_contract"]
        if "wave_id" in kwargs and kwargs["wave_id"]:
            body["wave_id"] = str(kwargs["wave_id"])

        res = self.client.post("/api/v1/pipelines/runs", json=body)
        if res.status_code not in (200, 201, 202):
            return {"status": "ERROR", "detail": res.text, "exit_code": 1}

        body = res.json()
        if res.status_code == 202:
            import time

            run_id = body.get("run_id")
            if run_id:
                for _ in range(100):
                    time.sleep(0.05)
                    st_res = self.client.get(f"/api/v1/runs/{run_id}")
                    if st_res.status_code == 200:
                        st = st_res.json().get("status")
                        if st in ("COMPLETED", "FAILED", "ABORTED", "PAUSED_HOLD"):
                            rep_res = self.client.get(f"/api/v1/runs/{run_id}/report")
                            if rep_res.status_code == 200:
                                return self._wrap_res(rep_res)
                            return self._wrap_res(st_res)
        return self._wrap_res(res)
