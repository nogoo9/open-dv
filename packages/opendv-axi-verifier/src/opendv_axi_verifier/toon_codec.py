"""`@toon_v1` Token-Oriented Object Notation Serializer & Parser.

Implements the compact, token-dense `@toon_v1` diagnostic format used by the
6-Gate `axi-dv` verifier loop to return structured violation traces to the
LLM repair loop (~60% fewer tokens than verbose JSON diagnostics).

Example format:
```text
@toon_v1
run_id: run-pe-apex-horizon-20261030
attempt: 1/3
status: REJECT-WITH-TRACE
violations[2]{gate,rule,action,target,fix}:
 G1,DV-COL-012,REJECT,hub_limited_partner,"Set require_src_qualifier=true"
 G4,ALG-A1,AUTO-FIX,sat_lp_comm_fx,"Merged 3 cols into sat_lp_commitment_inv"
```
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_VIOLATIONS_HEADER_RE = re.compile(r"^violations\[(\d+)\]\{([a-zA-Z0-9_,]+)\}:\s*$")
_QUOTED_ENTITY_RE = re.compile(r"'([a-zA-Z0-9_.\-]+)'")
_RULE_CODE_RE = re.compile(
    r"\b(DV-[A-Z0-9]+-[A-Z0-9]+|ALG-A[0-9a-zA-Z]+|ANTI_SILO_VIOLATION|"
    r"PHANTOM_HUB_REJECTED|SURROGATE_KEY_REJECTED|BLOB_OR_VEC_IN_RAW_SAT|"
    r"MISSING_PROVENANCE_SAT|SPAN_HALLUCINATION|TIER_U3_CAPTAIN_HOLD|"
    r"SATELLITE_DELETED|HUB_DELETED|LINK_DELETED|BK_SIGNATURE_MUTATED|"
    r"SAT_COLUMN_DROPPED|SAT_HASHDIFF_INVALIDATED)\b"
)
_GATE_NUM_RE = re.compile(r"\bGate\s*([0-6])\b|\bDV-G([0-6])-\d+\b", re.IGNORECASE)


@dataclass(frozen=True)
class ToonViolation:
    """Single row inside the `violations[N]{gate,rule,action,target,fix}` table."""

    gate: str
    rule: str
    action: str
    target: str
    fix: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ToonTrace:
    """Parsed or constructed `@toon_v1` verification diagnostic trace."""

    run_id: str
    attempt: str
    status: str
    violations: list[ToonViolation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "attempt": self.attempt,
            "status": self.status,
            "violations": [v.to_dict() for v in self.violations],
        }


def _quote_csv_field(value: str, force_quotes: bool = False) -> str:
    escaped = value.replace('"', '""')
    if force_quotes or any(ch in value for ch in (",", '"', " ", "\n", ":", ";", "=")):
        return f'"{escaped}"'
    return escaped


def serialize_toon_v1(trace: ToonTrace | dict[str, Any]) -> str:
    """Serialize a `ToonTrace` (or dict representation) into canonical `@toon_v1` text."""
    if isinstance(trace, dict):
        raw_violations = trace.get("violations", [])
        violations = [
            v if isinstance(v, ToonViolation) else ToonViolation(**v) for v in raw_violations
        ]
        trace_obj = ToonTrace(
            run_id=str(trace.get("run_id", "run-axi-dv-default")),
            attempt=str(trace.get("attempt", "1/3")),
            status=str(trace.get("status", "PASS")),
            violations=violations,
        )
    else:
        trace_obj = trace

    lines: list[str] = [
        "@toon_v1",
        f"run_id: {trace_obj.run_id}",
        f"attempt: {trace_obj.attempt}",
        f"status: {trace_obj.status}",
        f"violations[{len(trace_obj.violations)}]{{gate,rule,action,target,fix}}:",
    ]

    for v in trace_obj.violations:
        row_str = ",".join(
            [
                _quote_csv_field(v.gate),
                _quote_csv_field(v.rule),
                _quote_csv_field(v.action),
                _quote_csv_field(v.target),
                _quote_csv_field(v.fix, force_quotes=True),
            ]
        )
        lines.append(f" {row_str}")

    return "\n".join(lines)


def parse_toon_v1(toon_text: str) -> ToonTrace:
    """Parse canonical `@toon_v1` text into a `ToonTrace` instance."""
    raw_lines = [line.rstrip("\r") for line in toon_text.strip().splitlines()]
    if not raw_lines or raw_lines[0].strip() != "@toon_v1":
        raise ValueError("Invalid @toon_v1 payload: missing '@toon_v1' header line")

    metadata: dict[str, str] = {}
    violations: list[ToonViolation] = []
    expected_count: int | None = None
    in_violations_block = False

    for line in raw_lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue

        if not in_violations_block:
            m_hdr = _VIOLATIONS_HEADER_RE.match(stripped)
            if m_hdr:
                expected_count = int(m_hdr.group(1))
                in_violations_block = True
                continue

            if ":" in stripped:
                key, val = stripped.split(":", 1)
                metadata[key.strip()] = val.strip()
            else:
                raise ValueError(f"Malformed @toon_v1 metadata line: '{line}'")
        else:
            reader = csv.reader(io.StringIO(stripped), quotechar='"', skipinitialspace=True)
            row = next(reader, [])
            if len(row) < 5:
                raise ValueError(
                    f"Malformed @toon_v1 violation row "
                    f"(expected 5 fields, got {len(row)}): '{line}'"
                )
            violations.append(
                ToonViolation(
                    gate=row[0].strip(),
                    rule=row[1].strip(),
                    action=row[2].strip(),
                    target=row[3].strip(),
                    fix=",".join(row[4:]).strip(),
                )
            )

    if expected_count is not None and len(violations) != expected_count:
        raise ValueError(
            f"@toon_v1 violation count mismatch: header declared {expected_count}, "
            f"parsed {len(violations)}"
        )

    for required_key in ("run_id", "attempt", "status"):
        if required_key not in metadata:
            raise ValueError(f"Missing required @toon_v1 field '{required_key}'")

    return ToonTrace(
        run_id=metadata["run_id"],
        attempt=metadata["attempt"],
        status=metadata["status"],
        violations=violations,
    )


def diagnostic_string_to_toon_violation(diag: str) -> ToonViolation:
    """Convert a human-readable verifier diagnostic string into a structured `ToonViolation`."""
    upper = diag.upper()
    if upper.startswith("ALL_GATES_GREEN"):
        return ToonViolation(
            gate="G6",
            rule="ALL_GATES_GREEN",
            action="PASS",
            target="contract",
            fix=diag,
        )

    # Infer gate
    gate = "G1"
    m_gate = _GATE_NUM_RE.search(diag)
    if m_gate:
        g_num = m_gate.group(1) or m_gate.group(2)
        gate = f"G{g_num}"
    elif "DV-STR-" in diag or "DV-COL-" in diag:
        gate = "G3"
    elif "DV-AST-" in diag:
        gate = "G6"
    elif "DV-EVO-" in diag or "BREAKING_EVOLUTION" in diag:
        gate = "G3"
    elif "UNSTRUCTURED_VIOLATION" in diag:
        gate = "G5"

    # Infer rule code
    m_rule = _RULE_CODE_RE.search(diag)
    rule = m_rule.group(1) if m_rule else "DV-RULE-001"

    # Infer action
    if "ASK_USER" in upper or "ASK-USER" in upper or "CAPTAIN_HOLD" in upper:
        action = "ASK-USER"
    elif "AUTO-FIX" in upper or "AUTO_FIX" in upper:
        action = "AUTO-FIX"
    else:
        action = "REJECT"

    # Infer target entity
    m_target = _QUOTED_ENTITY_RE.search(diag)
    target = m_target.group(1) if m_target else "contract"

    # Clean message for fix column
    fix_msg = diag.split(":", 1)[-1].strip() if ":" in diag else diag.strip()
    return ToonViolation(
        gate=gate,
        rule=rule,
        action=action,
        target=target,
        fix=fix_msg,
    )


def build_toon_trace_from_diagnostics(
    diagnostics: list[str],
    *,
    run_id: str = "run-axi-dv-default",
    attempt: int = 1,
    max_attempts: int = 3,
    status: str | None = None,
    extra_violations: list[ToonViolation] | None = None,
) -> ToonTrace:
    """Build a `ToonTrace` from diagnostic strings and optional `ToonViolation` records."""
    violations: list[ToonViolation] = list(extra_violations or [])
    for diag in diagnostics:
        if diag.startswith("ALL_GATES_GREEN"):
            continue
        violations.append(diagnostic_string_to_toon_violation(diag))

    if status is None:
        if any(v.action == "REJECT" for v in violations):
            resolved_status = "REJECT-WITH-TRACE"
        elif any(v.action == "ASK-USER" for v in violations):
            resolved_status = "ASK-USER"
        else:
            resolved_status = "PASS"
    else:
        resolved_status = status.replace("_", "-")

    return ToonTrace(
        run_id=run_id,
        attempt=f"{attempt}/{max_attempts}",
        status=resolved_status,
        violations=violations,
    )
