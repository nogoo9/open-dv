"""Module entrypoint supporting `python -m opendv.cli`."""

from __future__ import annotations

from opendv.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
