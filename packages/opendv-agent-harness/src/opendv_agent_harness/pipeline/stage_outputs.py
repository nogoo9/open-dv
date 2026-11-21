"""Structured Stage 00-04 LLM / symbolic output schemas and schema-driven sanitization."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opendv_core.schema_registry import (
    CONTRACT_V5_SCHEMA_FILENAME,
    get_default_schema_registry,
)


def _resolve_json_pointer(ref: str, root_schema: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    cur: Any = root_schema
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur if isinstance(cur, dict) else None


def _resolve_schema_node(
    node: dict[str, Any],
    root_schema: dict[str, Any],
    seen_refs: frozenset[str],
) -> tuple[dict[str, Any], frozenset[str]]:
    cur = node
    refs = seen_refs
    while "$ref" in cur and isinstance(cur["$ref"], str):
        ref = cur["$ref"]
        if ref in refs:
            break
        target = _resolve_json_pointer(ref, root_schema)
        if target is None:
            break
        refs = refs | {ref}
        siblings = {k: v for k, v in cur.items() if k != "$ref"}
        cur = {**target, **siblings} if siblings else target
    return cur, refs


def _merge_all_of_node(
    node: dict[str, Any],
    root_schema: dict[str, Any],
    seen_refs: frozenset[str],
) -> dict[str, Any]:
    branches = node.get("allOf")
    if not isinstance(branches, list):
        return node
    merged_props: dict[str, Any] = {}
    merged_pat_props: dict[str, Any] = {}
    merged_required: list[str] = list(node.get("required", []))
    forbid_extra = node.get("additionalProperties") is False
    for raw_branch in branches:
        if not isinstance(raw_branch, dict):
            continue
        branch, _ = _resolve_schema_node(raw_branch, root_schema, seen_refs)
        if "allOf" in branch:
            branch = _merge_all_of_node(branch, root_schema, seen_refs)
        if isinstance(branch.get("properties"), dict):
            merged_props.update(branch["properties"])
        if isinstance(branch.get("patternProperties"), dict):
            merged_pat_props.update(branch["patternProperties"])
        for req in branch.get("required", []):
            if req not in merged_required:
                merged_required.append(req)
        if branch.get("additionalProperties") is False:
            forbid_extra = True
    if isinstance(node.get("properties"), dict):
        merged_props.update(node["properties"])
    if isinstance(node.get("patternProperties"), dict):
        merged_pat_props.update(node["patternProperties"])
    out = {k: v for k, v in node.items() if k != "allOf"}
    if merged_props:
        out["properties"] = merged_props
    if merged_pat_props:
        out["patternProperties"] = merged_pat_props
    if merged_required:
        out["required"] = merged_required
    if forbid_extra:
        out["additionalProperties"] = False
    return out


def _select_union_branch(
    value: Any,
    branches: list[Any],
    root_schema: dict[str, Any],
    seen_refs: frozenset[str],
) -> dict[str, Any] | None:
    non_null = [
        b
        for b in branches
        if isinstance(b, dict) and b.get("type") != "null"
    ]
    if not non_null:
        return None
    if len(non_null) == 1:
        return non_null[0]
    if isinstance(value, dict):
        best_branch: dict[str, Any] | None = None
        best_score: tuple[int, int, int] = (-1, -1, -1)
        for raw_b in non_null:
            b, _ = _resolve_schema_node(raw_b, root_schema, seen_refs)
            if "allOf" in b:
                b = _merge_all_of_node(b, root_schema, seen_refs)
            b_props = b.get("properties") if isinstance(b.get("properties"), dict) else {}
            disqualified = False
            for k, v in value.items():
                p_schema = b_props.get(k)
                if not isinstance(p_schema, dict):
                    continue
                if "const" in p_schema and v != p_schema["const"]:
                    disqualified = True
                    break
                if (
                    "enum" in p_schema
                    and isinstance(p_schema["enum"], list)
                    and v not in p_schema["enum"]
                ):
                    disqualified = True
                    break
            if disqualified:
                continue
            reqs = [r for r in b.get("required", []) if isinstance(r, str)]
            missing_req = sum(1 for r in reqs if r not in value)
            matched = sum(1 for k in value if k in b_props)
            score = (1 if missing_req == 0 else 0, -missing_req, matched)
            if score > best_score:
                best_score = score
                best_branch = raw_b
        return best_branch
    if isinstance(value, list):
        for raw_b in non_null:
            b, _ = _resolve_schema_node(raw_b, root_schema, seen_refs)
            if b.get("type") == "array" or "items" in b or "prefixItems" in b:
                return raw_b
    return None


def _schema_allows_null(
    node: dict[str, Any],
    root_schema: dict[str, Any],
    seen_refs: frozenset[str],
) -> bool:
    res, refs = _resolve_schema_node(node, root_schema, seen_refs)
    t = res.get("type")
    if t == "null" or (isinstance(t, list) and "null" in t):
        return True
    for kw in ("anyOf", "oneOf"):
        branches = res.get(kw)
        if isinstance(branches, list) and any(
            isinstance(b, dict) and _schema_allows_null(b, root_schema, refs)
            for b in branches
        ):
            return True
    return False


def sanitize_value_against_schema(
    value: Any,
    schema_node: dict[str, Any],
    root_schema: dict[str, Any],
    *,
    strip_none: bool = True,
    _seen_refs: frozenset[str] = frozenset(),
    _depth: int = 0,
) -> Any:
    """Recursively sanitize a payload against a Draft 2020-12 JSON Schema node."""
    node, refs = _resolve_schema_node(schema_node, root_schema, _seen_refs)
    if "allOf" in node:
        node = _merge_all_of_node(node, root_schema, refs)

    for union_kw in ("oneOf", "anyOf"):
        if union_kw in node and isinstance(node[union_kw], list):
            chosen = _select_union_branch(value, node[union_kw], root_schema, refs)
            if chosen is not None:
                siblings = {
                    k: v
                    for k, v in node.items()
                    if k not in ("oneOf", "anyOf")
                }
                if siblings and isinstance(siblings.get("properties"), dict):
                    chosen_res, _ = _resolve_schema_node(chosen, root_schema, refs)
                    merged = {
                        **chosen_res,
                        "properties": {
                            **siblings.get("properties", {}),
                            **(chosen_res.get("properties") or {}),
                        },
                    }
                    return sanitize_value_against_schema(
                        value,
                        merged,
                        root_schema,
                        strip_none=strip_none,
                        _seen_refs=refs,
                        _depth=_depth,
                    )
                return sanitize_value_against_schema(
                    value,
                    chosen,
                    root_schema,
                    strip_none=strip_none,
                    _seen_refs=refs,
                    _depth=_depth,
                )

    if isinstance(value, dict):
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        pat_props = (
            node.get("patternProperties")
            if isinstance(node.get("patternProperties"), dict)
            else {}
        )
        add_props = node.get("additionalProperties", True)
        forbid_extra = add_props is False

        out: dict[str, Any] = {}
        for k, v in value.items():
            sub_schema: dict[str, Any] | None = None
            if k in props and isinstance(props[k], dict):
                sub_schema = props[k]
            elif pat_props:
                for pat, ps in pat_props.items():
                    if isinstance(ps, dict) and re.search(pat, k):
                        sub_schema = ps
                        break

            if sub_schema is not None:
                if (
                    strip_none
                    and v is None
                    and (_depth < 2 or not _schema_allows_null(sub_schema, root_schema, refs))
                ):
                    continue
                out[k] = sanitize_value_against_schema(
                    v,
                    sub_schema,
                    root_schema,
                    strip_none=strip_none,
                    _seen_refs=refs,
                    _depth=_depth + 1,
                )
            elif forbid_extra:
                continue
            elif isinstance(add_props, dict):
                if (
                    strip_none
                    and v is None
                    and (_depth < 2 or not _schema_allows_null(add_props, root_schema, refs))
                ):
                    continue
                out[k] = sanitize_value_against_schema(
                    v,
                    add_props,
                    root_schema,
                    strip_none=strip_none,
                    _seen_refs=refs,
                    _depth=_depth + 1,
                )
            else:
                out[k] = v
        return out

    if isinstance(value, list):
        items_schema = node.get("items") if isinstance(node.get("items"), dict) else None
        prefix_items = (
            node.get("prefixItems") if isinstance(node.get("prefixItems"), list) else None
        )
        out_list: list[Any] = []
        for idx, elem in enumerate(value):
            elem_schema = (
                prefix_items[idx]
                if prefix_items and idx < len(prefix_items) and isinstance(prefix_items[idx], dict)
                else items_schema
            )
            if elem_schema is not None:
                out_list.append(
                    sanitize_value_against_schema(
                        elem,
                        elem_schema,
                        root_schema,
                        strip_none=strip_none,
                        _seen_refs=refs,
                        _depth=_depth,
                    )
                )
            elif isinstance(elem, dict):
                out_list.append(
                    {ik: iv for ik, iv in elem.items() if not (strip_none and iv is None)}
                )
            else:
                out_list.append(elem)
        return out_list

    return value


def get_contract_v5_schema() -> dict[str, Any]:
    """Load the normative `no-mistake-dv2-contract-v5.schema.json`."""
    return get_default_schema_registry().load_schema(CONTRACT_V5_SCHEMA_FILENAME)


def sanitize_contract_dict(
    cnt: dict[str, Any] | None,
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Strip None values and schema-disallowed keys from a v5 contract dictionary."""
    if not cnt:
        return cnt
    root_schema = schema or get_contract_v5_schema()
    cleaned = sanitize_value_against_schema(cnt, root_schema, root_schema, strip_none=True)
    if isinstance(cleaned, dict) and isinstance(cleaned.get("links"), list):
        for lnk in cleaned["links"]:
            if isinstance(lnk, dict) and "degenerate_columns" not in lnk:
                lnk["degenerate_columns"] = []
    return cleaned


def sanitize_column_triage_entry(
    entry: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sanitize a single `column_triage_ledger` entry against the v5 schema."""
    root_schema = schema or get_contract_v5_schema()
    item_schema = (
        root_schema.get("properties", {})
        .get("column_triage_ledger", {})
        .get("items", {})
    )
    if not isinstance(item_schema, dict) or not item_schema:
        return {k: v for k, v in entry.items() if v is not None}
    sanitized = sanitize_value_against_schema(entry, item_schema, root_schema, strip_none=True)
    return sanitized if isinstance(sanitized, dict) else entry


class Stage00OrchestrationPlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_summary: str = ""
    inferred_action: str = "COLD_START_N1"
    active_stages: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6, 7])
    per_table_plan: dict[str, str] = Field(default_factory=dict)


class Stage01FeedDiscoveryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_system: str
    table_name: str
    column_count: int = 0
    row_count: int = 0
    primary_keys: list[str] = Field(default_factory=list)
    foreign_keys: list[dict[str, Any]] = Field(default_factory=list)
    column_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    record_source_code: str = ""
    target_hub_name: str = ""
    business_key_columns: list[str] = Field(default_factory=list)
    surrogate_key_columns: list[str] = Field(default_factory=list)
    timestamp_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)
    key_authority_tier: str = "TIER_1_GLOBAL_STANDARD"
    bkcc_namespace: str = "GLOBAL"
    foreign_key_mappings: dict[str, str] = Field(default_factory=dict)
    cbc_mapping_hints: dict[str, str] = Field(default_factory=dict)


class Stage01SourceDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    discovered_feeds: list[Stage01FeedDiscoveryItem] = Field(default_factory=list)
    profile_summaries: dict[str, Any] = Field(default_factory=dict)


class Stage02UnstructuredDiscoveryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str = ""
    target_hub_name: str = ""
    link_name: str = ""
    satellite_name: str = ""
    extracted_candidates: list[Any] = Field(default_factory=list)
    discovered_entities: list[str] = Field(default_factory=list)
    spans_extracted: int = 0
    profiled_docs: list[dict[str, Any]] = Field(default_factory=list)


class Stage03ColumnTriageOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    column_triage_ledger: list[dict[str, Any]] = Field(default_factory=list)
    ontology_deltas_proposed: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sanitize_ledger_entries(self) -> Stage03ColumnTriageOutput:
        schema = get_contract_v5_schema()
        if self.column_triage_ledger:
            self.column_triage_ledger = [
                sanitize_column_triage_entry(e, schema=schema) if isinstance(e, dict) else e
                for e in self.column_triage_ledger
            ]
        if self.triage_ledger:
            self.triage_ledger = [
                sanitize_column_triage_entry(e, schema=schema) if isinstance(e, dict) else e
                for e in self.triage_ledger
            ]
        return self


class Stage04ContractSynthesisOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contract: dict[str, Any] = Field(default_factory=dict)
    evolved_contract: dict[str, Any] | None = None
    drift_findings: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sanitize_contracts(self) -> Stage04ContractSynthesisOutput:
        schema = get_contract_v5_schema()
        if self.contract:
            self.contract = sanitize_contract_dict(self.contract, schema=schema) or {}
        if self.evolved_contract:
            self.evolved_contract = sanitize_contract_dict(
                self.evolved_contract, schema=schema
            )
        return self


__all__ = [
    "Stage00OrchestrationPlanOutput",
    "Stage01FeedDiscoveryItem",
    "Stage01SourceDiscoveryOutput",
    "Stage02UnstructuredDiscoveryOutput",
    "Stage03ColumnTriageOutput",
    "Stage04ContractSynthesisOutput",
    "get_contract_v5_schema",
    "sanitize_column_triage_entry",
    "sanitize_contract_dict",
    "sanitize_value_against_schema",
]
