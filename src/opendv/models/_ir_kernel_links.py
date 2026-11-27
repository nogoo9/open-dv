"""Participant-aware Link Algorithm A.5 hashing kernel helpers (Layer 1)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from opendv.models.vault_ir import PortableLogicalType
from opendv.models.vault_ir_certified import HashRecipeSpec, LinkParticipantHashSpec


def _resolve_participant_bkcc_raw(
    participant: LinkParticipantHashSpec,
    row: Mapping[str, Any],
) -> str | None:
    if participant.key_tier == "TIER_1_GLOBAL_STANDARD":
        return None
    if participant.bkcc_col is not None and participant.bkcc_col in row:
        raw_val = row.get(participant.bkcc_col)
        if raw_val is not None and str(raw_val).strip() != "":
            return str(raw_val)
    return participant.bkcc_literal


def compute_participant_link_prehash(
    recipe: HashRecipeSpec,
    row: Mapping[str, Any],
    *,
    column_types: Mapping[str, PortableLogicalType] | None = None,
) -> tuple[str, list[tuple[str, bool]]]:
    """Compute participant-ordered Alg A.5 pre-hash string and Hub sentinel states."""
    from opendv.models.ir_kernel import (
        DEFAULT_DELIMITER,
        ERROR_NULL_TOKEN,
        NULL_TOKEN,
        encode_alg_a5_token,
        evaluate_predicate,
        format_scalar_alg_a5,
        resolve_bkcc,
    )

    tokens: list[str] = []
    hub_participant_states: list[tuple[str, bool]] = []

    for participant in recipe.participant_specs:
        if participant.is_degenerate:
            for col in participant.source_columns:
                ltype = column_types.get(col) if column_types else None
                norm = format_scalar_alg_a5(
                    row.get(col), logical_type=ltype, is_business_key=False
                )
                val_str = "UNKNOWN" if norm is None else norm
                tokens.append(f"{len(val_str.encode('utf-8'))}:{val_str}")
            continue

        raw_vals = [row.get(col) for col in participant.source_columns]
        formatted_vals = [
            format_scalar_alg_a5(
                row.get(col),
                logical_type=(column_types.get(col) if column_types else None),
                is_business_key=True,
            )
            for col in participant.source_columns
        ]
        all_blank = len(formatted_vals) == 0 or all(val is None for val in formatted_vals)
        single_minus2 = (
            len(raw_vals) == 1
            and raw_vals[0] is not None
            and str(raw_vals[0]).strip() == "-2"
        )
        single_minus1 = (
            len(raw_vals) == 1
            and raw_vals[0] is not None
            and str(raw_vals[0]).strip() == "-1"
        )
        err_pred = (
            participant.error_predicate is not None
            and evaluate_predicate(participant.error_predicate, row)
        )
        unk_pred = (
            participant.unknown_predicate is not None
            and evaluate_predicate(participant.unknown_predicate, row)
        )

        if err_pred or single_minus2 or (all_blank and participant.is_nullable is True):
            tokens.extend([ERROR_NULL_TOKEN] * (1 + len(participant.source_columns)))
            hub_participant_states.append(("error", True))
        elif unk_pred or single_minus1 or (all_blank and participant.is_nullable is not True):
            tokens.extend([NULL_TOKEN] * (1 + len(participant.source_columns)))
            hub_participant_states.append(("unknown", True))
        else:
            raw_bkcc = _resolve_participant_bkcc_raw(participant, row)
            resolved_bkcc = resolve_bkcc(participant.key_tier, raw_bkcc)
            tokens.append(encode_alg_a5_token(resolved_bkcc, is_business_key=True))
            for col in participant.source_columns:
                ltype = column_types.get(col) if column_types else None
                tokens.append(
                    encode_alg_a5_token(
                        row.get(col), logical_type=ltype, is_business_key=True
                    )
                )
            hub_participant_states.append(("valid", False))

    return DEFAULT_DELIMITER.join(tokens), hub_participant_states


def execute_participant_link_recipe(
    recipe: HashRecipeSpec,
    row: Mapping[str, Any],
    *,
    column_types: Mapping[str, PortableLogicalType] | None = None,
) -> str:
    """Execute a participant-aware LINK_HK recipe with per-participant sentinel handling."""
    from opendv.models.ir_kernel import (
        ZERO_KEY_ERROR,
        ZERO_KEY_UNKNOWN,
        evaluate_predicate,
    )

    if recipe.error_predicate is not None and evaluate_predicate(recipe.error_predicate, row):
        return ZERO_KEY_ERROR
    if recipe.unknown_predicate is not None and evaluate_predicate(recipe.unknown_predicate, row):
        return ZERO_KEY_UNKNOWN

    prehash, hub_states = compute_participant_link_prehash(
        recipe, row, column_types=column_types
    )
    if hub_states and all(is_sentinel for _, is_sentinel in hub_states):
        if all(state == "error" for state, _ in hub_states) or recipe.is_optional_inapplicable:
            return ZERO_KEY_ERROR
        return ZERO_KEY_UNKNOWN

    return hashlib.sha256(prehash.encode("utf-8")).hexdigest().lower()


__all__ = [
    "compute_participant_link_prehash",
    "execute_participant_link_recipe",
]
