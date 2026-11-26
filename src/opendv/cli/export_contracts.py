"""Contract/Schema exporter (`schemas/v2/*`), CI `--check`, and Markdown CLI reference."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

import click
import jsonschema  # type: ignore[import-untyped]
import typer
import typer.main

from opendv.models import BundleManifest, CertifiedVaultIR, CliExitCode, export_error_codes_catalog

CLI_SPEC_VERSION = "2.0.0"

_CLI_HTTP_ROUTES: dict[str, tuple[str | None, str | None]] = {
    "serve": (None, None),
    "doctor": ("GET", "/api/v1/system/doctor"),
    "settings show": ("GET", "/api/v1/settings"),
    "settings set": ("PUT", "/api/v1/settings"),
    "settings models": ("GET", "/api/v1/agents/readiness"),
    "settings trust-workspace": ("PUT", "/api/v1/settings"),
    "settings profile list": ("GET", "/api/v1/settings"),
    "settings profile set": ("PUT", "/api/v1/settings"),
    "settings profile delete": ("PUT", "/api/v1/settings"),
    "settings profile test": ("POST", "/api/v1/sources/inspect"),
    "agents list": ("GET", "/api/v1/agents"),
    "agents inspect": ("GET", "/api/v1/agents/{agent_id}"),
    "mcp list": ("GET", "/api/v1/mcp/servers"),
    "mcp register": ("POST", "/api/v1/mcp/servers"),
    "mcp remove": ("DELETE", "/api/v1/mcp/servers/{server_id}"),
    "mcp serve": (None, None),
    "secrets set": ("POST", "/api/v1/secrets"),
    "secrets list": ("GET", "/api/v1/secrets"),
    "secrets delete": ("DELETE", "/api/v1/secrets/{key}"),
    "project init": ("POST", "/api/v1/projects"),
    "project list": ("GET", "/api/v1/projects"),
    "project show": ("GET", "/api/v1/projects/{project_id}"),
    "project settings show": ("GET", "/api/v1/projects/{project_id}"),
    "project settings set": ("PATCH", "/api/v1/projects/{project_id}/settings"),
    "project delete": ("DELETE", "/api/v1/projects/{project_id}"),
    "project export": ("GET", "/api/v1/projects/{project_id}/bundle"),
    "project import": ("POST", "/api/v1/projects/import"),
    "source adapters": ("GET", "/api/v1/sources/adapters"),
    "source inspect": ("POST", "/api/v1/sources/inspect"),
    "source list": ("GET", "/api/v1/projects/{project_id}/sources"),
    "source attach": ("POST", "/api/v1/projects/{project_id}/sources"),
    "source reattach": ("PATCH", "/api/v1/projects/{project_id}/sources/{sid}"),
    "source note": ("POST", "/api/v1/projects/{project_id}/sources/upload"),
    "source approve-scan": (
        "POST",
        "/api/v1/projects/{project_id}/evidence/documents/{did}/approve-scan",
    ),
    "source detach": ("DELETE", "/api/v1/projects/{project_id}/sources/{sid}"),
    "ontology construct": ("POST", "/api/v1/projects/{project_id}/ontology/construct"),
    "ontology control": ("POST", "/api/v1/projects/{project_id}/ontology/runs/{rid}/controls"),
    "ontology answer": ("POST", "/api/v1/projects/{project_id}/ontology/runs/{rid}/answers"),
    "ontology fibo-qa": ("POST", "/api/v1/ontology/fibo-qa"),
    "ontology validate": ("POST", "/api/v1/projects/{project_id}/ontology/validate"),
    "ontology commit": ("POST", "/api/v1/projects/{project_id}/ontology/commit"),
    "ontology export": ("GET", "/api/v1/projects/{project_id}/ontology/export"),
    "vault build": ("POST", "/api/v1/projects/{project_id}/runs"),
    "vault verify": ("POST", "/api/v1/projects/{project_id}/vault/verify"),
    "vault waive": ("POST", "/api/v1/projects/{project_id}/vault/waivers"),
    "vault hold": ("POST", "/api/v1/projects/{project_id}/runs/{rid}/decisions"),
    "vault control": ("POST", "/api/v1/projects/{project_id}/runs/{rid}/controls"),
    "vault horizon": ("POST", "/api/v1/projects/{project_id}/runs/{rid}/horizon"),
    "vault logs": ("GET", "/api/v1/projects/{project_id}/runs/{rid}/logs"),
    "storage compile": ("POST", "/api/v1/projects/{project_id}/storage/compile"),
    "storage migrate": ("POST", "/api/v1/projects/{project_id}/storage/migrate"),
    "storage regenerate": ("POST", "/api/v1/projects/{project_id}/storage/regenerate"),
    "storage query": ("POST", "/api/v1/projects/{project_id}/storage/query"),
}


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the `open-dv` repository root containing `pyproject.toml` and `schemas/v2/`."""
    candidate = (start or Path(__file__)).resolve()
    for parent in (candidate, *candidate.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "schemas" / "v2").is_dir():
            return parent
    raise FileNotFoundError("Could not locate Open-DV repository root containing schemas/v2/.")


def _extract_param_choices(param: Any) -> list[str] | None:
    param_type = getattr(param, "type", None)
    if isinstance(param_type, click.Choice):
        return [c.value if isinstance(c, Enum) else str(c) for c in param_type.choices]
    descriptor = getattr(param, "type_descriptor", None)
    if descriptor is not None and getattr(descriptor, "is_choice", False):
        raw_choices = getattr(descriptor, "choices", ()) or ()
        return [c.value if isinstance(c, Enum) else str(c) for c in raw_choices]
    return None


def _click_param_type(param: Any) -> str:
    param_type = getattr(param, "type", None)
    if isinstance(param_type, click.Choice):
        return "choice"
    descriptor = getattr(param, "type_descriptor", None)
    if descriptor is not None:
        if getattr(descriptor, "is_choice", False):
            return "choice"
        ann = descriptor.annotation
        if ann is bool:
            return "boolean"
        if ann is int:
            return "integer"
        if ann is float:
            return "number"
        if ann is Path:
            return "path"
        return "string"
    name = getattr(param_type, "name", "string")
    return name.lower() if isinstance(name, str) and name else "string"


def _json_default(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str | list | dict):
        return value
    if isinstance(value, Path):
        return str(value)
    if inspect.isclass(value):
        return None
    return str(value)


def _extract_param_spec(param: Any, *, include_choices: bool) -> dict[str, Any]:
    opts: list[str] = list(getattr(param, "opts", ()))
    name_str = str(getattr(param, "name", "") or "")
    long_flag = next(
        (opt for opt in opts if opt.startswith("--")),
        f"--{name_str.replace('_', '-')}",
    )
    short_flag = next(
        (opt for opt in opts if opt.startswith("-") and not opt.startswith("--")),
        None,
    )
    default_val = _json_default(getattr(param, "default", None))
    is_option = getattr(param, "param_type_name", "option") == "option"
    help_text = str(getattr(param, "help", "") or "")
    if not help_text and not is_option:
        help_text = f"Positional argument `{name_str}`."
    type_str = _click_param_type(param)
    if not is_option:
        type_str = f"argument:{type_str}"
    entry: dict[str, Any] = {
        "name": name_str or long_flag.lstrip("-").replace("-", "_"),
        "flag": long_flag,
        "short": short_flag,
        "type": type_str,
        "required": bool(getattr(param, "required", False)),
        "default": default_val,
        "help": help_text,
    }
    if include_choices:
        entry["choices"] = _extract_param_choices(param)
    return entry


_SKIP_GLOBAL_PARAMS = frozenset({"help", "install_completion", "show_completion"})


def _collect_click_commands(
    cmd: Any,
    prefix: tuple[str, ...],
    out: list[dict[str, Any]],
) -> None:
    if hasattr(cmd, "commands"):
        for sub_name in sorted(cmd.commands):
            sub_cmd = cmd.commands[sub_name]
            _collect_click_commands(sub_cmd, (*prefix, sub_name), out)
        return

    path_str = " ".join(prefix)
    http_method, http_route = _CLI_HTTP_ROUTES.get(path_str, (None, None))
    options = [
        _extract_param_spec(param, include_choices=True)
        for param in cmd.params
        if param.name not in _SKIP_GLOBAL_PARAMS
    ]
    out.append(
        {
            "path": list(prefix),
            "help": (cmd.help or cmd.short_help or "").strip(),
            "http_method": http_method,
            "http_route": http_route,
            "options": options,
        }
    )


def build_cli_spec() -> dict[str, Any]:
    """Introspect the unified Typer `opendv` CLI app into `schemas/v2/cli-spec.schema.json`."""
    from opendv.cli.main import build_cli_app

    root_click = typer.main.get_command(build_cli_app())
    global_options = [
        _extract_param_spec(param, include_choices=False)
        for param in root_click.params
        if param.name not in _SKIP_GLOBAL_PARAMS
    ]
    subcommands: list[dict[str, Any]] = []
    _collect_click_commands(root_click, (), subcommands)
    return {
        "cli_version": CLI_SPEC_VERSION,
        "root_command": "opendv",
        "global_options": global_options,
        "subcommands": subcommands,
    }


def validate_cli_spec(spec: dict[str, Any], repo_root: Path | None = None) -> None:
    """Validate `cli-spec.json` strictly against `schemas/v2/cli-spec.schema.json`."""
    root = find_repo_root(repo_root)
    schema_path = root / "schemas" / "v2" / "cli-spec.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=spec, schema=schema)


def export_openapi_via_subprocess(repo_root: Path | None = None) -> dict[str, Any]:
    """Export FastAPI OpenAPI 3.1 JSON inside a clean child Python process (Layer-4 safe)."""
    root = find_repo_root(repo_root)
    src_dir = str(root / "src")
    env = dict(os.environ)
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir

    script = (
        "import json; "
        "from opendv.api.server import export_openapi_schema; "
        "print(json.dumps(export_openapi_schema(), indent=2, sort_keys=True))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"OpenAPI subprocess export failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    parsed = json.loads(proc.stdout)
    if not isinstance(parsed, dict) or "paths" not in parsed:
        raise RuntimeError("Invalid OpenAPI payload returned from child subprocess.")
    return parsed


def build_vault_ir_schema() -> dict[str, Any]:
    """Build JSON Schema for `CertifiedVaultIR` (`VaultSchemaIR v2.0`)."""
    schema = CertifiedVaultIR.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://opendv.dev/schemas/v2/vault-schema-ir-v2.schema.json"
    schema["title"] = "VaultSchemaIR v2.0"
    return schema


def build_project_bundle_schema() -> dict[str, Any]:
    """Build JSON Schema for `BundleManifest` (`project-bundle-v2`)."""
    schema = BundleManifest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://opendv.dev/schemas/v2/project-bundle-v2.schema.json"
    schema["title"] = "Open-DV V2 Project Bundle Manifest"
    return schema


def build_all_contracts(repo_root: Path | None = None) -> dict[Path, str]:
    """Compute canonical serialized JSON contract files under `schemas/v2/`."""
    root = find_repo_root(repo_root)
    schemas_v2 = root / "schemas" / "v2"
    cli_spec = build_cli_spec()
    validate_cli_spec(cli_spec, repo_root=root)

    contracts: dict[Path, Any] = {
        schemas_v2 / "openapi.json": export_openapi_via_subprocess(root),
        schemas_v2 / "cli-spec.json": cli_spec,
        schemas_v2 / "vault-schema-ir-v2.schema.json": build_vault_ir_schema(),
        schemas_v2 / "project-bundle-v2.schema.json": build_project_bundle_schema(),
    }
    return {
        path: json.dumps(payload, indent=2, sort_keys=True) + "\n"
        for path, payload in contracts.items()
    }


def generate_cli_reference_markdown(spec: dict[str, Any] | None = None) -> str:
    """Render deterministic GitHub-Flavored Markdown documentation from V2 `cli-spec.json`."""
    resolved = spec if spec is not None else build_cli_spec()
    lines: list[str] = [
        "# Open-DV V2 CLI Reference",
        "",
        f"> Generated from `schemas/v2/cli-spec.json` (`cli_version: {resolved['cli_version']}`).",
        "",
        "## Global Options",
        "",
        "| Flag | Type | Default | Description |",
        "| --- | --- | --- | --- |",
    ]
    for opt in resolved["global_options"]:
        default_str = f"`{opt['default']}`" if opt.get("default") is not None else "—"
        lines.append(f"| `{opt['flag']}` | `{opt['type']}` | {default_str} | {opt['help']} |")

    lines.extend(["", "## Commands", ""])
    for cmd in resolved["subcommands"]:
        cmd_path = " ".join(cmd["path"]) if isinstance(cmd["path"], list) else str(cmd["path"])
        lines.append(f"### `opendv {cmd_path}`")
        lines.append("")
        if cmd["help"]:
            lines.append(cmd["help"])
            lines.append("")
        if cmd.get("http_method") and cmd.get("http_route"):
            lines.append(f"- **HTTP Route:** `{cmd['http_method']} {cmd['http_route']}`")
        else:
            lines.append("- **Execution:** Local / In-process")
        lines.append("")
        if cmd["options"]:
            lines.extend(
                [
                    "| Option | Type | Required | Default | Description |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for opt in cmd["options"]:
                req_str = "yes" if opt["required"] else "no"
                default_str = f"`{opt['default']}`" if opt.get("default") is not None else "—"
                flag, typ, hlp = opt["flag"], opt["type"], opt["help"]
                lines.append(f"| `{flag}` | `{typ}` | {req_str} | {default_str} | {hlp} |")
            lines.append("")

    lines.extend(
        [
            "## Exit Codes",
            "",
            "| Code | Name |",
            "| --- | --- |",
            *[f"| `{int(code)}` | `{code.name}` |" for code in CliExitCode],
            "",
        ]
    )
    return "\n".join(lines)


def check_contracts(repo_root: Path | None = None) -> list[str]:
    """Return a list of drift errors across generated `schemas/v2/*` contract files."""
    root = find_repo_root(repo_root)
    expected = build_all_contracts(root)
    drifts: list[str] = []
    for path, expected_text in expected.items():
        if not path.is_file():
            drifts.append(f"Missing exported contract file: {path.relative_to(root)}")
            continue
        actual_text = path.read_text(encoding="utf-8")
        if actual_text != expected_text:
            drifts.append(f"Contract drift detected: {path.relative_to(root)}")

    err_path = root / "schemas" / "v2" / "error-codes.json"
    if not err_path.is_file():
        drifts.append("Missing error catalog: schemas/v2/error-codes.json")
    else:
        actual_err = json.loads(err_path.read_text(encoding="utf-8"))
        if actual_err != export_error_codes_catalog():
            drifts.append("Error catalog drift detected: schemas/v2/error-codes.json")
    return drifts


def write_contracts(repo_root: Path | None = None) -> list[Path]:
    """Write all V2 contract JSON artifacts into `schemas/v2/` deterministically."""
    root = find_repo_root(repo_root)
    written: list[Path] = []
    for path, content in build_all_contracts(root).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `python -m opendv.cli.export_contracts [--check]`."""
    parser = argparse.ArgumentParser(
        prog="opendv.cli.export_contracts",
        description="Export Open-DV V2 JSON Schemas & OpenAPI contracts or check for CI drift.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify checked-in `schemas/v2/*` files match generated output without writing.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = find_repo_root()
    if args.check:
        drifts = check_contracts(root)
        if drifts:
            for item in drifts:
                print(item, file=sys.stderr)
            return 1
        print("All schemas/v2 contracts match generated definitions.")
        return 0

    written = write_contracts(root)
    for path in written:
        print(f"Wrote {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
