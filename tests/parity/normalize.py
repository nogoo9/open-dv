"""tests/parity/normalize.py — Strict allowlist normalizer for cross-surface outputs."""

from __future__ import annotations

import re
from typing import Any

_PATH_PATTERN = re.compile(
    r"(?:[a-zA-Z]:[/\\](?:Users|tmp|temp|var|home|opt)[/\\][^\s\"',]+|[/\\](?:Users|tmp|temp|var|home|opt)[/\\][^\s\"',]+)"
)

_DATETIME_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


def _is_timestamp_value(val: Any) -> bool:
    """Return True if val is an ISO/date formatted string or numeric epoch timestamp."""
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, str):
        return bool(_DATETIME_REGEX.match(val.strip()))
    return False


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

            # 1b. Contract path canonicalization across CLI / REST
            if key in ("contract_path", "contract_uri") and (isinstance(val, str) or val is None):
                result[key] = "<CONTRACT_PATH>"
                continue

            # 2. Telemetry IDs
            if key in ("trace_id", "span_id", "parent_span_id"):
                result[key] = "<TELEMETRY_ID>"
                continue

            # 2b. Decision / Hold IDs
            if (
                key in ("decision_id", "hold_id")
                or key.endswith(("_decision_id", "_hold_id"))
            ) and isinstance(val, str) and val:
                placeholder = run_id_map.setdefault(val, f"<HOLD_ID_{len(run_id_map) + 1}>")
                result[key] = placeholder
                continue

            # 2c. Task / Session IDs
            if (
                key in ("task_id", "session_id")
                or key.endswith(("_task_id", "_session_id"))
            ) and isinstance(val, str) and val:
                placeholder = run_id_map.setdefault(val, f"<TASK_ID_{len(run_id_map) + 1}>")
                result[key] = placeholder
                continue

            # 2d. Event IDs
            if (
                key == "event_id" or key.endswith("_event_id")
            ) and isinstance(val, str) and val:
                result[key] = re.sub(r"evt-[a-zA-Z0-9_\-]+-[0-9a-f]{6,12}", "<EVENT_ID>", val)
                continue

            # 3. Run ID rewriting with consistent cross-referencing
            if (key == "run_id" or key.endswith("_run_id")) and isinstance(val, str) and val:
                placeholder = run_id_map.setdefault(val, f"<RUN_ID_{len(run_id_map) + 1}>")
                result[key] = placeholder
                continue

            # 4. Timestamps
            if (
                key.endswith(("_at_utc", "_at", "timestamp_utc", "created_at", "updated_at"))
                or key in ("dts", "timestamp", "load_dts")
            ) and _is_timestamp_value(val):
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


_EVENT_ID_REGEX = re.compile(r"evt-[a-zA-Z0-9_\-]+-[0-9a-f]{6,12}")


def normalize_pipeline_report(
    report_dict: dict[str, Any],
    *,
    path_root: str | None = None,
) -> dict[str, Any]:
    """Normalize an AgenticPipelineReport dictionary for bit-for-bit golden comparison.

    Masks volatile self-referential artifacts (pipeline_report.json,
    pipeline_report.md, vault-ledger.jsonl, and *graph_b_profile.json)
    and formats durations and IDs in the human-readable markdown summary.
    """
    import copy

    res = copy.deepcopy(report_dict)
    volatile_names = {
        "pipeline_report.json",
        "pipeline_report.md",
        "vault-ledger.jsonl",
    }
    volatile_shas: set[str] = set()

    # 1. Mask volatile final artifacts
    for _k, art in list(res.get("final_artifacts", {}).items()):
        name = art.get("name", "")
        if name in volatile_names or name.endswith(("graph_b_profile.json", "ontology_coverage.json")):
            if art.get("sha256"):
                volatile_shas.add(art["sha256"])
            art["sha256"] = "<VOLATILE_SHA256>"
            art["byte_length"] = 0

    # 2. Mask volatile intermediate artifacts
    for art in res.get("intermediate_artifacts", []):
        name = art.get("name", "")
        if name in volatile_names or name.endswith((
            "graph_b_profile.json",
            "unstructured_grounding.json",
            "ontology_coverage.json",
        )):
            if art.get("sha256"):
                volatile_shas.add(art["sha256"])
            art["sha256"] = "<VOLATILE_SHA256>"
            art["byte_length"] = 0

    # 3. Mask volatile artifact references in step_reports
    for step in res.get("step_reports", []):
        if step.get("stage_id") == "STAGE_01_SCOUT_SOURCE":
            step["output_artifact_sha256s"] = ["<VOLATILE_SHA256>"]
        else:
            step["output_artifact_sha256s"] = [
                "<VOLATILE_SHA256>" if s in volatile_shas else s
                for s in step.get("output_artifact_sha256s", [])
            ]
        step["input_artifact_sha256s"] = [
            "<VOLATILE_SHA256>" if s in volatile_shas else s
            for s in step.get("input_artifact_sha256s", [])
        ]

    # 4. Normalize markdown summary
    md = res.get("markdown_summary", "")
    md = re.sub(
        r"-\s*\*\*Total Duration\*\*:\s*`[^`]+`",
        "- **Total Duration**: `<DURATION>`",
        md,
    )

    # Normalize duration column in step execution ledger table
    lines: list[str] = []
    for line in md.splitlines():
        if line.strip().startswith("| `step-"):
            parts = line.split("|")
            if len(parts) >= 10:
                parts[8] = " <DURATION> "
                lines.append("|".join(parts))
                continue
        lines.append(line)
    md = "\n".join(lines)

    # Normalize volatile sha prefixes in tables
    for vsha in volatile_shas:
        if len(vsha) >= 16:
            prefix = vsha[:16].upper()
            md = md.replace(prefix, "<VOLATILE_SHA256_PREFIX>")
    for vname in volatile_names:
        md = re.sub(
            rf"(`{re.escape(vname)}`[^\n]*?\|\s*)\d+(\s*\|)",
            r"\g<1>0\g<2>",
            md,
        )
    md = re.sub(
        r"(`wave_\d+_graph_b_profile\.json`[^\n]*?\|\s*)\d+(\s*\|)",
        r"\g<1>0\g<2>",
        md,
    )
    md = re.sub(
        r"(`wave_\d+_unstructured_grounding\.json`[^\n]*?\|\s*)\d+(\s*\|)",
        r"\g<1>0\g<2>",
        md,
    )
    md = re.sub(
        r"(`wave_\d+_ontology_coverage\.json`[^\n]*?\|\s*)\d+(\s*\|)",
        r"\g<1>0\g<2>",
        md,
    )
    res["markdown_summary"] = md

    # 5. Normalize event_ids in observability_events
    for ev in res.get("observability_events", []):
        if "event_id" in ev and ev["event_id"]:
            ev["event_id"] = _EVENT_ID_REGEX.sub("<EVENT_ID>", ev["event_id"])

    # 6. Exclude recommender_errors when None and default ontology_policy for bit-for-bit golden parity
    if res.get("recommender_errors") is None and "recommender_errors" in res:
        del res["recommender_errors"]
    hs = res.get("harness_setup")
    if isinstance(hs, dict) and hs.get("ontology_policy") == "off":
        del hs["ontology_policy"]

    return normalize_for_parity(res, path_root=path_root)
