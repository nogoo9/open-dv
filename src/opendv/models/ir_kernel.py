"""Bounded pure-Python IR reference oracle: Alg A.5 hashing, PredicateNode, and SeedRecords.

Strictly Layer 1 (stdlib + Layer-1 IR models only). Contains zero SQL compilation
and zero semantic validation checks.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import Any

from opendv.models._ir_kernel_links import (
    compute_participant_link_prehash,
    execute_participant_link_recipe,
)
from opendv.models.diagnostics import KeyTierEnum
from opendv.models.vault_ir import (
    ArrayType,
    DecimalType,
    MapType,
    PortableLogicalType,
    PredicateBranch,
    PredicateLeaf,
    PredicateNode,
)
from opendv.models.vault_ir_certified import HashRecipeSpec, SeedRecordsSpec

NULL_TOKEN = "-1:^^"
ERROR_NULL_TOKEN = "-2:^^"
ZERO_KEY_UNKNOWN = "0" * 64
ZERO_KEY_ERROR = "f" * 64
DEFAULT_DELIMITER = "||"


def _normalize_decimal(val: Decimal | int | float | str) -> str:
    if isinstance(val, float):
        if val != val or val in (float("inf"), float("-inf")):
            raise ValueError(f"Non-finite float {val!r} cannot be hashed")
        dec = Decimal(str(val))
    elif isinstance(val, Decimal):
        if not val.is_finite():
            raise ValueError(f"Non-finite Decimal {val!r} cannot be hashed")
        dec = val
    else:
        dec = Decimal(str(val).strip())
    if not dec.is_finite():
        raise ValueError(f"Non-finite Decimal {val!r} cannot be hashed")
    with localcontext() as ctx:
        ctx.prec = max(64, len(dec.as_tuple().digits))
        norm = dec.normalize()
    if norm == 0:
        return "0"
    rendered = format(norm, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _canonical_json_key(key: Any, key_type: PortableLogicalType | None = None) -> str:
    if key_type == "BOOLEAN" or isinstance(key, bool):
        if isinstance(key, bool):
            return "true" if key else "false"
        text = str(key).strip().lower()
        if text in ("true", "1", "t", "yes"):
            return "true"
        if text in ("false", "0", "f", "no"):
            return "false"
        raise ValueError(f"Invalid BOOLEAN map key {key!r}")
    if key_type == "TIMESTAMP_UTC_US" or isinstance(key, datetime):
        if isinstance(key, datetime):
            dt = key.replace(tzinfo=UTC) if key.tzinfo is None else key.astimezone(UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if isinstance(key, date):
            return f"{key.isoformat()}T00:00:00.000000Z"
        raw = str(key).strip().replace(" ", "T")
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if key_type == "DATE32" or isinstance(key, date):
        if isinstance(key, datetime):
            dt = key if key.tzinfo is None else key.astimezone(UTC)
            return dt.date().isoformat()
        if isinstance(key, date):
            return key.isoformat()
        return date.fromisoformat(str(key).strip()[:10]).isoformat()
    if isinstance(key_type, DecimalType) or isinstance(key, Decimal):
        return _normalize_decimal(key)
    return str(key)


def _canonical_json_value(value: Any, ltype: PortableLogicalType | None = None) -> Any:
    if value is None:
        return None
    if isinstance(ltype, MapType):
        items = value.items() if isinstance(value, Mapping) else value
        return {
            _canonical_json_key(k, ltype.key_type): _canonical_json_value(v, ltype.value_type)
            for k, v in items
        }
    if isinstance(ltype, ArrayType):
        return [_canonical_json_value(v, ltype.element_type) for v in value]
    if isinstance(value, Mapping):
        return {_canonical_json_key(k): _canonical_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(v) for v in value]
    if isinstance(ltype, DecimalType) or isinstance(value, Decimal):
        return _normalize_decimal(value)
    if ltype == "TIMESTAMP_UTC_US" or isinstance(value, datetime):
        if isinstance(value, datetime):
            dt = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if isinstance(value, date):
            return f"{value.isoformat()}T00:00:00.000000Z"
        raw = str(value).strip().replace(" ", "T")
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if ltype == "DATE32" or isinstance(value, date):
        if isinstance(value, datetime):
            dt = value if value.tzinfo is None else value.astimezone(UTC)
            return dt.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    if ltype == "BOOLEAN" or isinstance(value, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1", "t", "yes"):
            return True
        if text in ("false", "0", "f", "no"):
            return False
        raise ValueError(f"Invalid BOOLEAN scalar {value!r}")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def format_scalar_alg_a5(
    value: Any,
    logical_type: PortableLogicalType | None = None,
    *,
    is_business_key: bool = True,
) -> str | None:
    """Normalize a Python scalar to canonical Alg A.5 text before byte-length prefixing."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None

    if isinstance(logical_type, (ArrayType, MapType)) or isinstance(value, (list, Mapping)):
        parsed_val: Any = value
        if isinstance(value, str):
            try:
                parsed_val = json.loads(value)
            except json.JSONDecodeError:
                parsed_val = None
        if parsed_val is not None and not isinstance(parsed_val, str):
            norm_obj = _canonical_json_value(parsed_val, logical_type)
            return json.dumps(
                norm_obj,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )

    if logical_type == "BOOLEAN" or isinstance(value, bool):
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        if text in ("true", "1", "t", "yes"):
            return "true"
        if text in ("false", "0", "f", "no"):
            return "false"
        raise ValueError(f"Invalid BOOLEAN scalar {value!r}")

    if logical_type == "DATE32":
        if isinstance(value, datetime):
            dt = value if value.tzinfo is None else value.astimezone(UTC)
            return dt.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return date.fromisoformat(str(value).strip()[:10]).isoformat()

    if logical_type == "TIMESTAMP_UTC_US":
        if isinstance(value, datetime):
            dt = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if isinstance(value, date):
            return f"{value.isoformat()}T00:00:00.000000Z"
        raw = str(value).strip().replace(" ", "T")
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    if isinstance(value, datetime):
        dt = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(logical_type, DecimalType) or isinstance(value, (Decimal, float)):
        return _normalize_decimal(value)

    if isinstance(value, int):
        return str(value)

    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if text == "":
        return None
    return text.upper() if is_business_key else text


def encode_alg_a5_token(
    value: Any,
    logical_type: PortableLogicalType | None = None,
    *,
    is_business_key: bool = True,
) -> str:
    """Encode a single scalar value as `<utf8_byte_len>:<val>` or `'-1:^^'`."""
    normalized = format_scalar_alg_a5(
        value,
        logical_type=logical_type,
        is_business_key=is_business_key,
    )
    if normalized is None:
        return NULL_TOKEN
    byte_len = len(normalized.encode("utf-8"))
    return f"{byte_len}:{normalized}"


def resolve_bkcc(
    key_tier: KeyTierEnum,
    bkcc_value: str | None = None,
) -> str:
    """Resolve canonical BKCC scope according to 3-tier Key Authority policy."""
    cleaned = (
        bkcc_value.replace("\r\n", "\n").replace("\r", "\n").strip().upper()
        if isinstance(bkcc_value, str) and bkcc_value.strip()
        else None
    )
    if key_tier == "TIER_1_GLOBAL_STANDARD":
        return "GLOBAL"
    if key_tier == "TIER_2_ENTERPRISE_SOR":
        return cleaned or "GLOBAL"
    if not cleaned:
        raise ValueError("TIER_3_LOCAL_SCOPED requires a non-empty bkcc_value")
    return cleaned


def evaluate_predicate(node: PredicateNode, row: Mapping[str, Any]) -> bool:
    """Evaluate a PredicateNode AST against a single row mapping."""
    if isinstance(node, PredicateBranch):
        if node.op == "AND":
            return all(evaluate_predicate(child, row) for child in node.children)
        return any(evaluate_predicate(child, row) for child in node.children)

    if isinstance(node, PredicateLeaf):
        val = row.get(node.column)
        if node.op == "IS_NOT_NULL":
            return val is not None
        # EQ: guard Python's bool == int equivalence (True == 1, False == 0)
        if val is None or node.literal is None:
            return False
        if isinstance(val, bool) != isinstance(node.literal, bool):
            return False
        return bool(val == node.literal)

    raise TypeError(f"Unsupported predicate node: {type(node).__name__}")


def _is_sentinel_value(value: Any, sentinel: str) -> bool:
    if value is None:
        return False
    return str(value).strip() == sentinel


def compute_alg_a5_prehash(
    values: Sequence[Any] | Mapping[str, Any],
    *,
    is_business_key: bool = True,
    bkcc: str | None = None,
    key_tier: KeyTierEnum | None = None,
    sort_alphabetically: bool = False,
    column_types: Mapping[str, PortableLogicalType] | None = None,
) -> str:
    """Compute the `||`-joined length-prefixed Alg A.5 pre-hash string."""
    parts: list[str] = []
    if is_business_key:
        resolved_bkcc: str | None = None
        if key_tier is not None:
            resolved_bkcc = resolve_bkcc(key_tier, bkcc)
        elif bkcc is not None and bkcc.strip():
            resolved_bkcc = bkcc.strip().upper()
        if resolved_bkcc is not None:
            parts.append(encode_alg_a5_token(resolved_bkcc, is_business_key=True))

    if isinstance(values, Mapping):
        keys = sorted(values.keys()) if (sort_alphabetically or not is_business_key) else list(
            values.keys()
        )
        for col in keys:
            ltype = column_types.get(col) if column_types else None
            parts.append(
                encode_alg_a5_token(
                    values[col], logical_type=ltype, is_business_key=is_business_key
                )
            )
    else:
        for val in values:
            parts.append(encode_alg_a5_token(val, is_business_key=is_business_key))

    return DEFAULT_DELIMITER.join(parts)


def compute_alg_a5_hash(
    values: Sequence[Any] | Mapping[str, Any],
    *,
    is_business_key: bool = True,
    bkcc: str | None = None,
    key_tier: KeyTierEnum | None = None,
    sort_alphabetically: bool = False,
    column_types: Mapping[str, PortableLogicalType] | None = None,
    row: Mapping[str, Any] | None = None,
    unknown_predicate: PredicateNode | None = None,
    error_predicate: PredicateNode | None = None,
    is_optional_inapplicable: bool = False,
) -> str:
    """Compute lowercase 64-hex Alg A.5 SHA-256 digest with -2/-1 sentinel short-circuit."""
    eval_row: Mapping[str, Any] = (
        row if row is not None else (values if isinstance(values, Mapping) else {})
    )
    raw_vals = list(values.values()) if isinstance(values, Mapping) else list(values)

    if error_predicate is not None and evaluate_predicate(error_predicate, eval_row):
        return ZERO_KEY_ERROR
    if is_business_key and len(raw_vals) == 1 and _is_sentinel_value(raw_vals[0], "-2"):
        return ZERO_KEY_ERROR

    if unknown_predicate is not None and evaluate_predicate(unknown_predicate, eval_row):
        return ZERO_KEY_UNKNOWN
    if is_business_key and len(raw_vals) == 1 and _is_sentinel_value(raw_vals[0], "-1"):
        return ZERO_KEY_UNKNOWN

    if is_business_key:
        if isinstance(values, Mapping):
            all_blank = len(values) == 0 or all(
                format_scalar_alg_a5(
                    val,
                    logical_type=(column_types.get(col) if column_types else None),
                    is_business_key=True,
                )
                is None
                for col, val in values.items()
            )
        else:
            all_blank = len(raw_vals) == 0 or all(
                format_scalar_alg_a5(v, is_business_key=True) is None for v in raw_vals
            )
        if all_blank:
            return ZERO_KEY_ERROR if is_optional_inapplicable else ZERO_KEY_UNKNOWN

    prehash = compute_alg_a5_prehash(
        values,
        is_business_key=is_business_key,
        bkcc=bkcc,
        key_tier=key_tier,
        sort_alphabetically=sort_alphabetically,
        column_types=column_types,
    )
    return hashlib.sha256(prehash.encode("utf-8")).hexdigest().lower()


def execute_hash_recipe(
    recipe: HashRecipeSpec,
    row: Mapping[str, Any],
    *,
    key_tier: KeyTierEnum = "TIER_2_ENTERPRISE_SOR",
    column_types: Mapping[str, PortableLogicalType] | None = None,
) -> str:
    """Execute a HashRecipeSpec deterministically against a row mapping."""
    if recipe.recipe_kind == "LINK_HK" and recipe.participant_specs:
        return execute_participant_link_recipe(recipe, row, column_types=column_types)

    is_bk = recipe.recipe_kind in ("HUB_HK", "LINK_HK")
    cols = recipe.key_columns if (is_bk and recipe.key_columns) else recipe.source_columns
    bkcc_val: str | None = recipe.bkcc_literal
    if recipe.bkcc_col is not None:
        raw_bkcc = row.get(recipe.bkcc_col)
        if raw_bkcc is not None and str(raw_bkcc).strip() != "":
            bkcc_val = str(raw_bkcc)

    subset: dict[str, Any] = {col: row.get(col) for col in cols}
    return compute_alg_a5_hash(
        subset,
        is_business_key=is_bk,
        bkcc=bkcc_val if is_bk else None,
        key_tier=key_tier if is_bk else None,
        sort_alphabetically=recipe.sort_alphabetically,
        column_types=column_types,
        row=row,
        unknown_predicate=recipe.unknown_predicate,
        error_predicate=recipe.error_predicate,
        is_optional_inapplicable=recipe.is_optional_inapplicable,
    )


def build_seed_records(spec: SeedRecordsSpec) -> dict[str, dict[str, Any]]:
    """Construct canonical Unknown (-1), Error (-2), and Ghost seed records."""
    return {
        "unknown": {
            "hk_hex": spec.unknown_hk_hex,
            "bkcc": "GLOBAL",
            "business_key": spec.default_string,
            "sentinel_code": spec.default_num,
            "hash_input": spec.default_hash_input,
            "load_dts": spec.ghost_load_dts,
            "rec_src": spec.ghost_rec_src,
        },
        "error": {
            "hk_hex": spec.error_hk_hex,
            "bkcc": "GLOBAL",
            "business_key": "ERROR",
            "sentinel_code": -2,
            "hash_input": spec.default_hash_input,
            "load_dts": spec.ghost_load_dts,
            "rec_src": spec.ghost_rec_src,
        },
        "ghost": {
            "hk_hex": spec.unknown_hk_hex,
            "hash_diff_hex": spec.ghost_hash_diff_hex,
            "load_dts": spec.ghost_load_dts,
            "rec_src": spec.ghost_rec_src,
        },
    }


__all__ = [
    "DEFAULT_DELIMITER",
    "ERROR_NULL_TOKEN",
    "NULL_TOKEN",
    "ZERO_KEY_ERROR",
    "ZERO_KEY_UNKNOWN",
    "build_seed_records",
    "compute_alg_a5_hash",
    "compute_alg_a5_prehash",
    "compute_participant_link_prehash",
    "encode_alg_a5_token",
    "evaluate_predicate",
    "execute_hash_recipe",
    "execute_participant_link_recipe",
    "format_scalar_alg_a5",
    "resolve_bkcc",
]
