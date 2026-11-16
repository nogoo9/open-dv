"""tests/e2e/run_e2e_platform_server.py.

Dedicated launcher for Platform API server in browser E2E test environments.
Wires DuckDBUnityCatalogEmulator and deterministic SDK shims via clean dependency injection
without polluting production code (R3-C1 item 4).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run E2E Open-DV Platform Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8420, help="Bind port")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]

    # Configure environment for deterministic test runner
    existing_hosts = os.environ.get("OPENDV_ALLOWED_HOSTS")
    e2e_allowed = "127.0.0.1,localhost,am_prod,uc://am_prod"
    os.environ["OPENDV_ALLOWED_HOSTS"] = (
        f"{existing_hosts},{e2e_allowed}" if existing_hosts else e2e_allowed
    )

    from opendv_agent_harness.pipeline import (
        run_agentic_vault_pipeline as orig_run_pipeline,
    )
    from opendv_platform_api.main import create_app
    from opendv_service import OpenDVSettings, OpenDVService
    from opendv_service.seams import set_pipeline_runner_override
    from opendv_testkit import build_deterministic_agy_sdk_shim
    from opendv_testkit.uc_emulator import DuckDBUnityCatalogEmulator

    settings = OpenDVSettings.load(workspace_root=repo_root, is_server=True)
    settings.server_host = args.host
    settings.server_port = args.port

    emulator = DuckDBUnityCatalogEmulator(default_catalog="am_prod", include_walkthrough=True)

    # Register Databricks adapter with emulator in source registry for pipeline and profiling
    from opendv_sources.adapters.databricks_uc import DatabricksUnityCatalogAdapter
    from opendv_sources.registry.source_registry import SourceAdapterRegistry, get_source_registry
    import opendv_platform_api.routers.sources as sources_router

    dbx_adapter = DatabricksUnityCatalogAdapter(emulator=emulator)
    get_source_registry().register_tabular_adapter("databricks", dbx_adapter)
    get_source_registry().register_tabular_adapter("uc", dbx_adapter)

    # Patch SourceAdapterRegistry.get_tabular_adapter to ensure newly instantiated jailed adapters retain emulator
    orig_get_tabular = SourceAdapterRegistry.get_tabular_adapter

    def _e2e_get_tabular(self_reg: Any, name: str, **kwargs: Any) -> Any:
        adapter = orig_get_tabular(self_reg, name, **kwargs)
        if name.lower().strip() in ("databricks", "uc") and getattr(adapter, "_emulator", None) is None:
            adapter._emulator = emulator
        return adapter

    SourceAdapterRegistry.get_tabular_adapter = _e2e_get_tabular

    # Wrap router inspect_source to inject emulator for Databricks sources
    orig_inspect_source = sources_router.inspect_source

    def _e2e_inspect_source(request_dto: Any, *s_args: Any, **s_kwargs: Any) -> Any:
        adapter_val = getattr(request_dto, "adapter", None)
        uri_val = getattr(request_dto, "uri", None) or ""
        if isinstance(request_dto, dict):
            adapter_val = request_dto.get("adapter")
            uri_val = request_dto.get("uri") or ""
            if adapter_val in ("databricks", "uc") or uri_val.startswith(("uc://", "databricks://")):
                opts = dict(request_dto.get("options") or {})
                if "emulator" not in opts:
                    opts["emulator"] = emulator
                    request_dto = {**request_dto, "options": opts}
        elif hasattr(request_dto, "model_copy"):
            if adapter_val in ("databricks", "uc") or uri_val.startswith(("uc://", "databricks://")):
                opts = dict(getattr(request_dto, "options", None) or {})
                if "emulator" not in opts:
                    opts["emulator"] = emulator
                    request_dto = request_dto.model_copy(update={"options": opts})
        return orig_inspect_source(request_dto, *s_args, **s_kwargs)

    sources_router.inspect_source = _e2e_inspect_source

    # Inject deterministic SDK shim via clean seam dependency injection
    def _shim_runner(*r_args: Any, **r_kwargs: Any) -> Any:
        return orig_run_pipeline(
            *r_args,
            **{**r_kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    set_pipeline_runner_override(_shim_runner)
    service = OpenDVService(
        workspace_root=repo_root,
        settings=settings,
        is_server=True,
        pipeline_runner=_shim_runner,
    )
    service.uc_emulator = emulator
    service.state.uc_emulator = emulator

    app = create_app(service=service, workspace_root=repo_root, settings=settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
