"""tests/parity/normalize.py — Strict allowlist normalizer for cross-surface outputs."""

from __future__ import annotations

import re
from typing import Any

_PATH_PATTERN = re.compile(
    r"(?:[a-zA-Z]:[/\\](?:Users|tmp|temp|var|home|opt)[^\s\"',]+|[/\\](?:Users|tmp|temp|var|home|opt)[^\s\"',]+)"
)


def normalize_for_parity(
    payload: Any,
    *,
    run_id_map: dict[str, str] | None = None,
    path_root: str | None = None,
) -> Any:
    """Normalize payload by masking ONLY strictly allowlisted volatile fields.

    Allowlisted volatile fields:
    - mode: replaced with "<MODE>"
    - run_id / *_run_id: replaced with deterministic "<RUN_ID_N>" keeping referential integrity
    - timestamps (*_at, timestamp_utc, created_at, updated_at, dts, load_dts):
      replaced with "<TIMESTAMP>"
    - durations (*_duration_ms, duration_ms, elapsed_ms, duration_seconds):
      replaced with "<DURATION>"
    - telemetry IDs (trace_id, span_id, parent_span_id): replaced with "<TELEMETRY_ID>"
    - machine-specific file paths: rewritten to canonical "<WORKSPACE>" or "<OUT>"
    - floating-point values: rounded to 6 decimal places to prevent epsilon jitter
    - path separators: Windows backslashes normalized to forward slashes in string outputs

    All other keys, values, structures, and arrays must match bit-for-bit.
    """
    if run_id_map is None:
        run_id_map = {}

    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key in sorted(payload.keys()):
            val = payload[key]

            # 1. Mode stripping
            if key == "mode":
                result[key] = "<MODE>"
                continue

            # 2. Telemetry IDs
            if key in ("trace_id", "span_id", "parent_span_id"):
                result[key] = "<TELEMETRY_ID>"
                continue

            # 3. Run ID rewriting with consistent cross-referencing
            if (key == "run_id" or key.endswith("_run_id")) and isinstance(val, str) and val:
                placeholder = run_id_map.setdefault(val, f"<RUN_ID_{len(run_id_map) + 1}>")
                result[key] = placeholder
                continue

            # 4. Timestamps
            if (
                key.endswith(("_at", "timestamp_utc", "created_at", "updated_at"))
                or key in ("dts", "timestamp", "load_dts")
            ):
                result[key] = "<TIMESTAMP>"
                continue

            # 5. Durations
            if key.endswith(("_duration_ms", "duration_ms", "elapsed_ms", "duration_seconds")):
                result[key] = "<DURATION>"
                continue

            # Recursive normalization
            result[key] = normalize_for_parity(
                val, run_id_map=run_id_map, path_root=path_root
            )
        return result

    if isinstance(payload, list):
        return [
            normalize_for_parity(item, run_id_map=run_id_map, path_root=path_root)
            for item in payload
        ]

    if isinstance(payload, float):
        return round(payload, 6)

    if isinstance(payload, str):
        normalized_str = payload
        # Rewrite known run_ids
        for orig_id, placeholder in run_id_map.items():
            if orig_id in normalized_str:
                normalized_str = normalized_str.replace(orig_id, placeholder)

        # Rewrite filesystem paths
        if path_root:
            path_root_fwd = path_root.replace("\\", "/")
            path_root_back = path_root.replace("/", "\\")
            if path_root in normalized_str:
                normalized_str = normalized_str.replace(path_root, "<WORKSPACE>")
            if path_root_fwd in normalized_str:
                normalized_str = normalized_str.replace(path_root_fwd, "<WORKSPACE>")
            if path_root_back in normalized_str:
                normalized_str = normalized_str.replace(path_root_back, "<WORKSPACE>")

        normalized_str = _PATH_PATTERN.sub("<OUT>", normalized_str)
        # Normalize Windows path separators in outputs
        normalized_str = normalized_str.replace("\\", "/")
        return normalized_str

    return payload
