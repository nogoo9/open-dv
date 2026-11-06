"""tests/parity/transports.py — Cross-surface Transport Protocol and implementations."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import yaml

from opendv_cli.main import app_main

REPO_ROOT = Path(__file__).resolve().parents[2]


class TransportType(StrEnum):
    """Enumeration of supported cross-surface transport implementations."""

    STANDALONE_CLI = "STANDALONE_CLI"
    CLIENT_CLI = "CLIENT_CLI"
    DIRECT_REST = "DIRECT_REST"


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

    def suggest_ontology(
        self,
        base: Any | None = None,
        mode: str = "DELTA",
        domain_namespace: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate ontology proposals (IP-1)."""
        ...

    def validate_ontology_delta(
        self,
        delta: Any,
        base: Any,
        contract: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Validate additive ontology delta (ONT-EVO-001..003)."""
        ...

    def diff_ontology(
        self,
        base: Any,
        target: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compute structural diff between two Graph A ontologies."""
        ...

    def decide_ontology(
        self,
        action: str,
        delta: Any | None = None,
        base: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record steward decision and accept/reject ontology delta."""
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
    transport_type = TransportType.STANDALONE_CLI

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def invoke_raw(self, argv: list[str]) -> tuple[int, str, str]:
        """Invoke CLI in-process and return (exit_code, stdout, stderr)."""
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = stdout_buf, stderr_buf
            code = app_main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        return code, stdout_buf.getvalue(), stderr_buf.getvalue()

    def _invoke(self, argv: list[str]) -> dict[str, Any]:
        code, stdout_str, stderr_str = self.invoke_raw(argv)
        out_str = stdout_str.strip()
        if not out_str:
            return {"exit_code": code, "stderr": stderr_str}
        try:
            data = json.loads(out_str)
            if isinstance(data, dict):
                return data
            return {"result": data, "exit_code": code}
        except json.JSONDecodeError:
            return {
                "raw_output": out_str,
                "exit_code": code,
                "stderr": stderr_str,
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
            compiled_sql_models = fixtures.get("compiled_sql_models") or kwargs.get(
                "compiled_sql_models"
            )
            compiled_sql = fixtures.get("compiled_sql") or kwargs.get("compiled_sql")
        else:
            contract_target = contract
            graph_a_target = graph_a
            graph_b_target = graph_b
            prior_target = prior or kwargs.get("prior_contract")
            compiled_sql_models = kwargs.get("compiled_sql_models")
            compiled_sql = kwargs.get("compiled_sql")

        contract_path = self._resolve_contract_arg(
            contract_target, filename="candidate_contract.yaml"
        )
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
                spec.get("manifest") or spec.get("input_spec_path") or spec.get("input_path")
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
                spec.get("manifest") or spec.get("input_spec_path") or spec.get("input_path")
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
        if kwargs.get("initial_contract"):
            argv.extend(["--initial-contract", str(kwargs["initial_contract"])])
        elif kwargs.get("initial_contract_path"):
            argv.extend(["--initial-contract", str(kwargs["initial_contract_path"])])
        if kwargs.get("harness_config"):
            argv.extend(["--harness-config", str(kwargs["harness_config"])])
        if kwargs.get("output_dir"):
            argv.extend(["--output-dir", str(kwargs["output_dir"])])
        if kwargs.get("wave_id"):
            argv.extend(["--wave-id", str(kwargs["wave_id"])])
        return self._invoke(argv)

    def _resolve_delta_arg(self, delta: Any, filename: str = "delta.json") -> str:
        if isinstance(delta, (str, Path)):
            return str(delta)
        if isinstance(delta, dict):
            delta_file = self.workspace_dir / filename
            delta_file.write_text(json.dumps(delta, indent=2), encoding="utf-8")
            return str(delta_file)
        if hasattr(delta, "model_dump"):
            delta_file = self.workspace_dir / filename
            delta_file.write_text(json.dumps(delta.model_dump(mode="json"), indent=2), encoding="utf-8")
            return str(delta_file)
        raise ValueError(f"Invalid delta argument: {delta!r}")

    def suggest_ontology(
        self,
        base: Any | None = None,
        mode: str = "DELTA",
        domain_namespace: str | None = None,
        profile: Any | None = None,
        source: Any | None = None,
        context_doc: Any | None = None,
        recommender_id: str | None = None,
        output: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(base, dict) and "base_ontology" in base:
            fixtures = base
            base = fixtures.get("base_ontology")
            mode = fixtures.get("mode", mode)
            domain_namespace = fixtures.get("domain_namespace", domain_namespace)
            recommender_id = fixtures.get("recommender_id", recommender_id)
            profile = fixtures.get("profile", profile)
            source = fixtures.get("source", source)
            context_doc = fixtures.get("context_doc", context_doc)
        from opendv_service.ontology.registry import get_ontology_recommender_registry
        reg = get_ontology_recommender_registry()
        if not reg.has_recommender():
            reg.discover(force=True)
        argv = ["ontology", "suggest", "--json"]
        if base:
            base_path = (
                self._resolve_contract_arg(base, filename="base_ontology.yaml")
                if isinstance(base, dict) else str(base)
            )
            argv.extend(["--base", base_path])
        if mode:
            argv.extend(["--mode", mode])
        if domain_namespace:
            argv.extend(["--domain-namespace", domain_namespace])
        if profile:
            argv.extend(["--profile", str(profile)])
        if source:
            argv.extend(["--source", str(source)])
        if context_doc:
            argv.extend(["--context-doc", str(context_doc)])
        if recommender_id:
            argv.extend(["--recommender-id", recommender_id])
        if output:
            argv.extend(["--output", str(output)])
        return self._invoke(argv)

    def validate_ontology_delta(
        self,
        delta: Any = None,
        base: Any = None,
        contract: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(delta, dict) and (
            "base_ontology" in delta or "proposal" in delta or "delta" in delta
        ):
            fixtures = delta
            delta = fixtures.get("proposal") or fixtures.get("delta") or fixtures.get("validation")
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
            contract = fixtures.get("contract", contract)
        delta_path = self._resolve_delta_arg(delta)
        base_path = (
            self._resolve_contract_arg(base, filename="base_ontology.yaml")
            if isinstance(base, dict) else str(base)
        )
        argv = ["ontology", "validate-delta", "--delta", delta_path, "--base", base_path, "--json"]
        if contract:
            contract_path = (
                self._resolve_contract_arg(contract, filename="deployed_contract.yaml")
                if isinstance(contract, dict) else str(contract)
            )
            argv.extend(["--contract", contract_path])
        return self._invoke(argv)

    def diff_ontology(
        self,
        base: Any = None,
        target: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(base, dict) and (
            "base_ontology" in base or "target_ontology" in base or "accepted_ontology" in base
        ):
            fixtures = base
            target = fixtures.get("target_ontology") or fixtures.get("accepted_ontology") or target
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
        base_path = (
            self._resolve_contract_arg(base, filename="base_ontology.yaml")
            if isinstance(base, dict) else str(base)
        )
        target_path = (
            self._resolve_contract_arg(target, filename="target_ontology.yaml")
            if isinstance(target, dict) else str(target)
        )
        argv = ["ontology", "diff", "--base", base_path, "--target", target_path, "--json"]
        return self._invoke(argv)

    def decide_ontology(
        self,
        action: Any = "ACCEPT",
        delta: Any | None = None,
        base: Any | None = None,
        proposal_id: str | None = None,
        contract: Any | None = None,
        op_id: str | list[str] | None = None,
        decided_by: str | None = None,
        notes: str | None = None,
        output: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(action, dict):
            fixtures = action
            action = fixtures.get("action", "ACCEPT")
            delta = fixtures.get("proposal") or fixtures.get("delta") or delta
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
            proposal_id = fixtures.get("proposal_id", proposal_id)
            contract = fixtures.get("contract", contract)
            op_id = fixtures.get("op_id", op_id)
            decided_by = fixtures.get("decided_by", decided_by)
            notes = fixtures.get("notes", notes)
            output = fixtures.get("output", output)
        sub = "accept" if str(action).upper() == "ACCEPT" else "reject"
        argv = ["ontology", sub, "--json"]
        if delta:
            delta_path = self._resolve_delta_arg(delta)
            argv.extend(["--delta", delta_path])
        if base:
            base_path = (
                self._resolve_contract_arg(base, filename="base_ontology.yaml")
                if isinstance(base, dict) else str(base)
            )
            argv.extend(["--base", base_path])
        if proposal_id:
            argv.extend(["--proposal-id", proposal_id])
        if contract:
            contract_path = (
                self._resolve_contract_arg(contract, filename="deployed_contract.yaml")
                if isinstance(contract, dict) else str(contract)
            )
            argv.extend(["--contract", contract_path])
        if op_id:
            op_val = ",".join(op_id) if isinstance(op_id, list) else str(op_id)
            argv.extend(["--op-id", op_val])
        if decided_by:
            argv.extend(["--decided-by", decided_by])
        if notes:
            argv.extend(["--notes", notes])
        if output:
            argv.extend(["--output", str(output)])
        return self._invoke(argv)


class ClientCliTransport:
    """Executes Open-DV operations via the opendvctl Bun platform client against a live server."""

    name = "CLIENT_CLI"
    transport_type = TransportType.CLIENT_CLI

    def __init__(
        self,
        workspace_dir: Path,
        server_url: str,
        binary_path: Path | None = None,
        role: str = "INTERNAL_MODELER",
    ) -> None:
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.server_url = server_url.rstrip("/")
        self.binary_path = binary_path or self._resolve_host_binary()
        self.role = role

    @classmethod
    def _resolve_host_binary(cls) -> Path | None:
        if env_bin := os.environ.get("OPENDVCTL_BIN"):
            candidate = Path(env_bin)
            if candidate.is_file():
                return candidate

        release_dir = REPO_ROOT / "apps" / "opendvctl" / "dist" / "release"
        if not release_dir.is_dir():
            return None

        if sys.platform == "win32":
            win_bin = release_dir / "opendvctl-windows-x64.exe"
            return win_bin if win_bin.is_file() else None
        elif sys.platform == "darwin":
            mac_bin = (
                release_dir / "opendvctl-darwin-arm64"
                if "arm" in sys.version.lower()
                else release_dir / "opendvctl-darwin-x64"
            )
            return mac_bin if mac_bin.is_file() else None
        else:
            linux_bin = release_dir / "opendvctl-linux-x64"
            return linux_bin if linux_bin.is_file() else None

    def invoke_raw(self, argv: list[str]) -> tuple[int, str, str]:
        """Invoke Bun client CLI subprocess and return (exit_code, stdout, stderr)."""
        env = {
            **os.environ,
            "OPENDV_API_URL": self.server_url,
            "OPENDV_ROLE": self.role,
        }

        if self.binary_path and Path(self.binary_path).is_file():
            cmd = [str(self.binary_path), *argv]
        else:
            cli_ts = REPO_ROOT / "apps" / "opendvctl" / "src" / "main.ts"
            cmd = ["bun", "run", str(cli_ts), *argv]

        if "--api-url" not in argv and self.server_url:
            cmd.extend(["--api-url", self.server_url])
        if "--role" not in argv and self.role:
            cmd.extend(["--role", self.role])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(self.workspace_dir),
            encoding="utf-8",
            env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _invoke(self, argv: list[str]) -> dict[str, Any]:
        code, stdout_str, stderr_str = self.invoke_raw(argv)
        out_str = stdout_str.strip()
        if not out_str:
            return {"exit_code": code, "stderr": stderr_str}
        try:
            data = json.loads(out_str)
            if isinstance(data, dict):
                if "mode" not in data:
                    data["mode"] = "client"
                return data
            return {"result": data, "exit_code": code}
        except json.JSONDecodeError:
            return {
                "raw_output": out_str,
                "exit_code": code,
                "stderr": stderr_str,
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
            compiled_sql_models = fixtures.get("compiled_sql_models") or kwargs.get(
                "compiled_sql_models"
            )
            compiled_sql = fixtures.get("compiled_sql") or kwargs.get("compiled_sql")
        else:
            contract_target = contract
            graph_a_target = graph_a
            graph_b_target = graph_b
            prior_target = prior or kwargs.get("prior_contract")
            compiled_sql_models = kwargs.get("compiled_sql_models")
            compiled_sql = kwargs.get("compiled_sql")

        contract_path = self._resolve_contract_arg(
            contract_target, filename="candidate_contract.yaml"
        )
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
                spec.get("manifest") or spec.get("input_spec_path") or spec.get("input_path")
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
                spec.get("manifest") or spec.get("input_spec_path") or spec.get("input_path")
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
        if kwargs.get("initial_contract"):
            argv.extend(["--initial-contract", str(kwargs["initial_contract"])])
        elif kwargs.get("initial_contract_path"):
            argv.extend(["--initial-contract", str(kwargs["initial_contract_path"])])
        if kwargs.get("harness_config"):
            argv.extend(["--harness-config", str(kwargs["harness_config"])])
        if kwargs.get("output_dir"):
            argv.extend(["--output-dir", str(kwargs["output_dir"])])
        if kwargs.get("wave_id"):
            argv.extend(["--wave-id", str(kwargs["wave_id"])])
        return self._invoke(argv)

    def _resolve_delta_arg(self, delta: Any, filename: str = "delta.json") -> str:
        if isinstance(delta, (str, Path)):
            return str(delta)
        if isinstance(delta, dict):
            delta_file = self.workspace_dir / filename
            delta_file.write_text(json.dumps(delta, indent=2), encoding="utf-8")
            return str(delta_file)
        if hasattr(delta, "model_dump"):
            delta_file = self.workspace_dir / filename
            delta_file.write_text(json.dumps(delta.model_dump(mode="json"), indent=2), encoding="utf-8")
            return str(delta_file)
        raise ValueError(f"Invalid delta argument: {delta!r}")

    def suggest_ontology(
        self,
        base: Any | None = None,
        mode: str = "DELTA",
        domain_namespace: str | None = None,
        profile: Any | None = None,
        source: Any | None = None,
        context_doc: Any | None = None,
        recommender_id: str | None = None,
        output: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(base, dict) and "base_ontology" in base:
            fixtures = base
            base = fixtures.get("base_ontology")
            mode = fixtures.get("mode", mode)
            domain_namespace = fixtures.get("domain_namespace", domain_namespace)
            recommender_id = fixtures.get("recommender_id", recommender_id)
            profile = fixtures.get("profile", profile)
            source = fixtures.get("source", source)
            context_doc = fixtures.get("context_doc", context_doc)
        argv = ["ontology", "suggest", "--json"]
        if base:
            base_path = self._resolve_contract_arg(base, filename="base_ontology.yaml") if isinstance(base, dict) else str(base)
            argv.extend(["--base", base_path])
        if mode:
            argv.extend(["--mode", mode])
        if domain_namespace:
            argv.extend(["--domain-namespace", domain_namespace])
        if profile:
            argv.extend(["--profile", str(profile)])
        if source:
            argv.extend(["--source", str(source)])
        if context_doc:
            argv.extend(["--context-doc", str(context_doc)])
        if recommender_id:
            argv.extend(["--recommender-id", recommender_id])
        if output:
            argv.extend(["--output", str(output)])
        return self._invoke(argv)

    def validate_ontology_delta(
        self,
        delta: Any = None,
        base: Any = None,
        contract: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(delta, dict) and (
            "base_ontology" in delta or "proposal" in delta or "delta" in delta
        ):
            fixtures = delta
            delta = fixtures.get("proposal") or fixtures.get("delta") or fixtures.get("validation")
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
            contract = fixtures.get("contract", contract)
        delta_path = self._resolve_delta_arg(delta)
        base_path = (
            self._resolve_contract_arg(base, filename="base_ontology.yaml")
            if isinstance(base, dict) else str(base)
        )
        argv = ["ontology", "validate-delta", "--delta", delta_path, "--base", base_path, "--json"]
        if contract:
            contract_path = (
                self._resolve_contract_arg(contract, filename="deployed_contract.yaml")
                if isinstance(contract, dict) else str(contract)
            )
            argv.extend(["--contract", contract_path])
        return self._invoke(argv)

    def diff_ontology(
        self,
        base: Any = None,
        target: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(base, dict) and (
            "base_ontology" in base or "target_ontology" in base or "accepted_ontology" in base
        ):
            fixtures = base
            target = fixtures.get("target_ontology") or fixtures.get("accepted_ontology") or target
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
        base_path = (
            self._resolve_contract_arg(base, filename="base_ontology.yaml")
            if isinstance(base, dict) else str(base)
        )
        target_path = (
            self._resolve_contract_arg(target, filename="target_ontology.yaml")
            if isinstance(target, dict) else str(target)
        )
        argv = ["ontology", "diff", "--base", base_path, "--target", target_path, "--json"]
        return self._invoke(argv)

    def decide_ontology(
        self,
        action: Any = "ACCEPT",
        delta: Any | None = None,
        base: Any | None = None,
        proposal_id: str | None = None,
        contract: Any | None = None,
        op_id: str | list[str] | None = None,
        decided_by: str | None = None,
        notes: str | None = None,
        output: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(action, dict):
            fixtures = action
            action = fixtures.get("action", "ACCEPT")
            delta = fixtures.get("proposal") or fixtures.get("delta") or delta
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
            proposal_id = fixtures.get("proposal_id", proposal_id)
            contract = fixtures.get("contract", contract)
            op_id = fixtures.get("op_id", op_id)
            decided_by = fixtures.get("decided_by", decided_by)
            notes = fixtures.get("notes", notes)
            output = fixtures.get("output", output)
        sub = "accept" if str(action).upper() == "ACCEPT" else "reject"
        argv = ["ontology", sub, "--json"]
        if delta:
            delta_path = self._resolve_delta_arg(delta)
            argv.extend(["--delta", delta_path])
        if base:
            base_path = (
                self._resolve_contract_arg(base, filename="base_ontology.yaml")
                if isinstance(base, dict) else str(base)
            )
            argv.extend(["--base", base_path])
        if proposal_id:
            argv.extend(["--proposal-id", proposal_id])
        if contract:
            contract_path = (
                self._resolve_contract_arg(contract, filename="deployed_contract.yaml")
                if isinstance(contract, dict) else str(contract)
            )
            argv.extend(["--contract", contract_path])
        if op_id:
            op_val = ",".join(op_id) if isinstance(op_id, list) else str(op_id)
            argv.extend(["--op-id", op_val])
        if decided_by:
            argv.extend(["--decided-by", decided_by])
        if notes:
            argv.extend(["--notes", notes])
        if output:
            argv.extend(["--output", str(output)])
        return self._invoke(argv)


class DirectRestTransport:
    """Executes requests against the platform REST/SSE API."""

    name = "DIRECT_REST"
    transport_type = TransportType.DIRECT_REST

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

        if kwargs.get("run_id"):
            body["run_id"] = kwargs["run_id"]
        if kwargs.get("project_id"):
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
                spec.get("manifest") or spec.get("input_spec_path") or spec.get("input_path") or ""
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
                spec.get("manifest") or spec.get("input_spec_path") or spec.get("input_path") or ""
            )
        else:
            input_path = str(spec)

        body: dict[str, Any] = {"input_spec_path": input_path}
        if kwargs.get("initial_contract_path"):
            body["initial_contract_path"] = str(kwargs["initial_contract_path"])
        elif kwargs.get("initial_contract"):
            body["initial_contract"] = kwargs["initial_contract"]
        if kwargs.get("wave_id"):
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

    def suggest_ontology(
        self,
        base: Any | None = None,
        mode: str = "DELTA",
        domain_namespace: str | None = None,
        profile: Any | None = None,
        source: Any | None = None,
        context_doc: Any | None = None,
        recommender_id: str | None = None,
        output: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(base, dict) and "base_ontology" in base:
            fixtures = base
            base = fixtures.get("base_ontology")
            mode = fixtures.get("mode", mode)
            domain_namespace = fixtures.get("domain_namespace", domain_namespace)
            recommender_id = fixtures.get("recommender_id", recommender_id)
            profile = fixtures.get("profile", profile)
            source = fixtures.get("source", source)
            context_doc = fixtures.get("context_doc", context_doc)
        from opendv_service.ontology.registry import get_ontology_recommender_registry
        reg = get_ontology_recommender_registry()
        if not reg.has_recommender():
            reg.discover(force=True)
        body: dict[str, Any] = {"mode": mode}
        if domain_namespace:
            body["domain_namespace"] = domain_namespace
        if recommender_id:
            body["recommender_id"] = recommender_id
        if base:
            if isinstance(base, (str, Path)):
                body["base_ontology_path"] = str(base)
                bp = Path(base)
                if bp.is_file():
                    with contextlib.suppress(Exception):
                        body["base_ontology_content"] = bp.read_text(encoding="utf-8")
            elif isinstance(base, dict):
                body["base_ontology_content"] = yaml.safe_dump(base)
        if profile:
            if isinstance(profile, (str, Path)):
                body["empirical_profile_path"] = str(profile)
            elif isinstance(profile, dict):
                body["empirical_profile_content"] = json.dumps(profile)
        if source:
            if isinstance(source, (str, Path)):
                body["bronze_source_path"] = str(source)
            elif isinstance(source, str):
                body["bronze_source_content"] = source
        if context_doc:
            if isinstance(context_doc, (str, Path)):
                body["context_doc_path"] = str(context_doc)
            elif isinstance(context_doc, str):
                body["context_doc_content"] = context_doc

        res = self.client.post("/api/v1/ontology/suggestions", json=body)
        return self._wrap_res(res)

    def validate_ontology_delta(
        self,
        delta: Any = None,
        base: Any = None,
        contract: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(delta, dict) and (
            "base_ontology" in delta or "proposal" in delta or "delta" in delta
        ):
            fixtures = delta
            delta = fixtures.get("proposal") or fixtures.get("delta") or fixtures.get("validation")
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
            contract = fixtures.get("contract", contract)
        body: dict[str, Any] = {}
        if isinstance(delta, (str, Path)):
            body["delta_path"] = str(delta)
            dp = Path(delta)
            if dp.is_file():
                with contextlib.suppress(Exception):
                    body["delta_content"] = dp.read_text(encoding="utf-8")
        elif isinstance(delta, dict):
            body["delta_content"] = json.dumps(delta)
        elif hasattr(delta, "model_dump"):
            body["delta_content"] = json.dumps(delta.model_dump(mode="json"))

        if isinstance(base, (str, Path)):
            body["base_ontology_path"] = str(base)
            bp = Path(base)
            if bp.is_file():
                with contextlib.suppress(Exception):
                    body["base_ontology_content"] = bp.read_text(encoding="utf-8")
        elif isinstance(base, dict):
            body["base_ontology_content"] = yaml.safe_dump(base)
        elif hasattr(base, "model_dump"):
            body["base_ontology_content"] = yaml.safe_dump(base.model_dump(mode="json"))

        if contract:
            if isinstance(contract, (str, Path)):
                body["contract_path"] = str(contract)
                cp = Path(contract)
                if cp.is_file():
                    with contextlib.suppress(Exception):
                        body["contract_content"] = cp.read_text(encoding="utf-8")
            elif isinstance(contract, dict):
                body["contract_content"] = yaml.safe_dump(contract)

        res = self.client.post("/api/v1/ontology/validate-delta", json=body)
        return self._wrap_res(res)

    def diff_ontology(
        self,
        base: Any = None,
        target: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(base, dict) and (
            "base_ontology" in base or "target_ontology" in base or "accepted_ontology" in base
        ):
            fixtures = base
            target = fixtures.get("target_ontology") or fixtures.get("accepted_ontology") or target
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
        body: dict[str, Any] = {}
        if isinstance(base, (str, Path)):
            body["base_ontology_path"] = str(base)
            bp = Path(base)
            if bp.is_file():
                with contextlib.suppress(Exception):
                    body["base_ontology_content"] = bp.read_text(encoding="utf-8")
        elif isinstance(base, dict):
            body["base_ontology_content"] = yaml.safe_dump(base)
        elif hasattr(base, "model_dump"):
            body["base_ontology_content"] = yaml.safe_dump(base.model_dump(mode="json"))

        if isinstance(target, (str, Path)):
            body["target_ontology_path"] = str(target)
            tp = Path(target)
            if tp.is_file():
                with contextlib.suppress(Exception):
                    body["target_ontology_content"] = tp.read_text(encoding="utf-8")
        elif isinstance(target, dict):
            body["target_ontology_content"] = yaml.safe_dump(target)
        elif hasattr(target, "model_dump"):
            body["target_ontology_content"] = yaml.safe_dump(target.model_dump(mode="json"))

        res = self.client.post("/api/v1/ontology/diff", json=body)
        return self._wrap_res(res)

    def decide_ontology(
        self,
        action: Any = "ACCEPT",
        delta: Any | None = None,
        base: Any | None = None,
        proposal_id: str | None = None,
        contract: Any | None = None,
        op_id: str | list[str] | None = None,
        decided_by: str | None = None,
        notes: str | None = None,
        output: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if isinstance(action, dict):
            fixtures = action
            action = fixtures.get("action", "ACCEPT")
            delta = fixtures.get("proposal") or fixtures.get("delta") or delta
            base = fixtures.get("base_ontology") or fixtures.get("base") or base
            proposal_id = fixtures.get("proposal_id", proposal_id)
            contract = fixtures.get("contract", contract)
            op_id = fixtures.get("op_id", op_id)
            decided_by = fixtures.get("decided_by", decided_by)
            notes = fixtures.get("notes", notes)
            output = fixtures.get("output", output)
        body: dict[str, Any] = {"action": str(action).upper()}
        if proposal_id:
            body["proposal_id"] = proposal_id
        if decided_by:
            body["decided_by"] = decided_by
        if notes:
            body["steward_notes"] = notes

        if delta:
            if isinstance(delta, (str, Path)):
                body["delta_path"] = str(delta)
                dp = Path(delta)
                if dp.is_file():
                    with contextlib.suppress(Exception):
                        body["delta_content"] = dp.read_text(encoding="utf-8")
            elif isinstance(delta, dict):
                body["delta_content"] = json.dumps(delta)
            elif hasattr(delta, "model_dump"):
                body["delta_content"] = json.dumps(delta.model_dump(mode="json"))

        if base:
            if isinstance(base, (str, Path)):
                body["base_ontology_path"] = str(base)
                bp = Path(base)
                if bp.is_file():
                    with contextlib.suppress(Exception):
                        body["base_ontology_content"] = bp.read_text(encoding="utf-8")
            elif isinstance(base, dict):
                body["base_ontology_content"] = yaml.safe_dump(base)
            elif hasattr(base, "model_dump"):
                body["base_ontology_content"] = yaml.safe_dump(base.model_dump(mode="json"))

        if contract:
            if isinstance(contract, (str, Path)):
                body["contract_path"] = str(contract)
                cp = Path(contract)
                if cp.is_file():
                    with contextlib.suppress(Exception):
                        body["contract_content"] = cp.read_text(encoding="utf-8")
            elif isinstance(contract, dict):
                body["contract_content"] = yaml.safe_dump(contract)

        if op_id:
            body["accepted_op_ids"] = op_id if isinstance(op_id, list) else [op_id]

        res = self.client.post("/api/v1/ontology/decisions", json=body)
        return self._wrap_res(res)


__all__ = [
    "ClientCliTransport",
    "DirectRestTransport",
    "ParityGapError",
    "StandaloneCliTransport",
    "Transport",
    "TransportType",
]
