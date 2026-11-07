"""Unified CLI entrypoint (`opendv-platform`) for the Open-DV Platform API & Web Workbench."""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.settings import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for `opendv-platform` launcher."""
    parser = argparse.ArgumentParser(
        prog="opendv-platform",
        description="Launch the unified Open-DV Platform API and Web Workbench server.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Server bind host address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Server bind port (default: 8420)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to opendv.config.yaml configuration file",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root directory path",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        default=False,
        help="Open the Web Workbench in the default browser on startup",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Open-DV Platform API & Web Workbench server."""
    parser = build_parser()
    args = parser.parse_args(argv)

    eff_workspace = args.workspace.resolve() if args.workspace is not None else None
    if (
        eff_workspace is None
        and args.config is None
        and not os.environ.get("OPENDV_WORKSPACE_ROOT", "").strip()
        and not os.environ.get("OPENDV_CONFIG", "").strip()
    ):
        eff_workspace = Path.cwd().resolve()

    settings = OpenDVSettings.load(
        workspace_root=eff_workspace,
        config_file=args.config,
        is_server=True,
    )

    host = args.host or settings.server_host or DEFAULT_SERVER_HOST
    port = int(args.port or settings.server_port or DEFAULT_SERVER_PORT)
    settings.server_host = host
    settings.server_port = port

    app = create_app(
        settings=settings,
        workspace_root=settings.workspace_root,
        storage_root=settings.state_dir,
    )

    if args.open:
        browser_host = DEFAULT_SERVER_HOST if host in {"0.0.0.0", "::"} else host
        url = f"http://{browser_host}:{port}"
        timer = threading.Timer(0.6, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()

    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
