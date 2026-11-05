"""Human-readable and structured output presenter for spec-driven Open-DV CLIs."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from opendv_cli_common.jcs_printer import print_jcs


def _extract_field(payload: Any, field_name: str) -> Any:
    """Safely extract attribute or dictionary key from payload."""
    if isinstance(payload, dict):
        return payload.get(field_name)
    if hasattr(payload, field_name):
        return getattr(payload, field_name)
    return None


def _extract_rendered_toon(payload: Any) -> str | None:
    """Extract TOON diagnostic trace string from rendered DTO or payload."""
    rendered = _extract_field(payload, "rendered")
    if rendered is not None:
        toon = _extract_field(rendered, "toon")
        if toon:
            return str(toon)
    toon_text = _extract_field(payload, "toon_text")
    if toon_text:
        return str(toon_text)
    return None


def _extract_rendered_text(payload: Any) -> str | None:
    """Extract pre-rendered human-readable text from rendered DTO or payload."""
    rendered = _extract_field(payload, "rendered")
    if rendered is not None:
        text = _extract_field(rendered, "text")
        if text:
            return str(text)
    return None


def format_presenter_output(
    payload: Any,
    *,
    command: str = "opendv",
    format_type: str | None = None,
    args: argparse.Namespace | None = None,
) -> str:
    """Format command result according to the requested format (json, toon, or text)."""
    # 1. Determine requested format
    eff_format = "text"
    if args is not None:
        if getattr(args, "json", False) or getattr(args, "json_output", False):
            eff_format = "json"
        elif getattr(args, "toon", False):
            eff_format = "toon"
        elif getattr(args, "format", None):
            eff_format = str(args.format).lower()

    if format_type is not None:
        eff_format = format_type.lower()

    # 2. TOON format
    if eff_format == "toon":
        toon = _extract_rendered_toon(payload)
        if toon:
            return toon.rstrip()

    # 3. Pre-rendered text
    rendered_text = _extract_rendered_text(payload)
    if eff_format == "text" and rendered_text:
        return rendered_text.rstrip()

    # 4. Standard text summary with status label
    dict_payload = payload
    if hasattr(payload, "model_dump"):
        dict_payload = payload.model_dump(mode="json")
    elif not isinstance(payload, dict):
        return str(payload)

    status_label = (
        dict_payload.get("status")
        or dict_payload.get("verdict")
        or ("PASS" if dict_payload.get("overall_passed") else None)
        or ("PASS" if dict_payload.get("all_batches_passed") else None)
        or "OK"
    )
    run_id = dict_payload.get("run_id") or dict_payload.get("resumed_run_id") or ""
    header = f"[opendv {command}] status={status_label} run_id={run_id}".strip()
    body = json.dumps(dict_payload, indent=2, sort_keys=True)
    return f"{header}\n{body}"


def present_result(
    payload: Any,
    *,
    command: str = "opendv",
    format_type: str | None = None,
    args: argparse.Namespace | None = None,
    file: TextIO | None = None,
) -> None:
    """Emit formatted output to stdout (or specified file stream)."""
    target = file if file is not None else sys.stdout

    is_json = bool(
        (args is not None and (getattr(args, "json", False) or getattr(args, "json_output", False)))
        or (format_type and format_type.lower() == "json")
        or (args is not None and getattr(args, "format", None) == "json")
    )

    if is_json:
        print_jcs(payload, file=target)
        return

    output = format_presenter_output(payload, command=command, format_type=format_type, args=args)
    print(output, file=target)


__all__ = ["format_presenter_output", "present_result"]
