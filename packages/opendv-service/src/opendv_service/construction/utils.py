"""Shared utility functions for construction subsystem."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(target_path: Path, content: str) -> None:
    """Safely write text to a file via atomic temporary file replacement."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f".tmp_{target_path.name}_",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target_path)
    except Exception:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
