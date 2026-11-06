"""Entry point for exporting CLI specification: `python -m opendv_service.export_cli_spec`."""

from __future__ import annotations

from opendv_service.cli_spec.exporter import export_cli_spec


def main() -> int:
    """Export CLI specification to canonical paths."""
    written = export_cli_spec()
    print(f"Successfully exported cli-spec.json to {len(written)} locations:")
    for path in written:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
