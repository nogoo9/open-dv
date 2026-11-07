"""Run creation options normalization and initial contract resolution helper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from opendv_contracts import LifecycleStage
from opendv_core.paths import resolve_workspace_path


@dataclass(frozen=True)
class NormalizedRunOptions:
    """Normalized run execution configuration."""

    provider_override: str | None
    has_initial_contract: bool
    initial_contract_path: str | None
    lifecycle_stage: str
    target_wave_id: str | None


def resolve_run_creation_options(
    req: Any,
    workspace_root: Path,
) -> NormalizedRunOptions:
    """Normalize run creation options and validate/resolve initial contract inputs.

    Raises:
        ValueError: If initial_contract_path does not exist or is not a regular file,
                    or if initial_contract contains malformed YAML syntax or hostile tags.
    """
    harness_provider = getattr(req, "harness_provider", None)
    harness_config_path = getattr(req, "harness_config_path", None)
    provider_override = (
        harness_provider
        if (harness_config_path is None or harness_provider != "antigravity")
        else getattr(req, "provider_override", None)
    )

    has_initial_contract = False
    initial_contract_path = getattr(req, "initial_contract_path", None)
    lifecycle_stage = LifecycleStage.COLD_START_N1.value

    if initial_contract_path is not None:
        resolved_path = resolve_workspace_path(
            str(initial_contract_path),
            workspace_root=workspace_root,
            allow_temp=True,
        )
        if not resolved_path.is_file():
            raise ValueError(
                f"Contract file does not exist or is not a regular file: '{initial_contract_path}'"
            )
        try:
            content = resolved_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"Unreadable initial_contract_path '{initial_contract_path}': {exc}"
            ) from exc

        try:
            yaml.safe_load(content)
        except (yaml.YAMLError, Exception) as exc:
            raise ValueError(f"Malformed initial_contract YAML: {exc}") from exc

        has_initial_contract = True
        lifecycle_stage = LifecycleStage.MULTI_SOURCE_EXPANSION.value
    else:
        inline_contract = getattr(req, "initial_contract", None) or getattr(
            req, "initial_contract_content", None
        )
        if inline_contract is not None:
            try:
                yaml.safe_load(str(inline_contract))
            except (yaml.YAMLError, Exception) as exc:
                raise ValueError(f"Malformed initial_contract YAML: {exc}") from exc
            has_initial_contract = True
            lifecycle_stage = LifecycleStage.MULTI_SOURCE_EXPANSION.value

    target_wave_id = getattr(req, "wave_id", None)

    return NormalizedRunOptions(
        provider_override=provider_override,
        has_initial_contract=has_initial_contract,
        initial_contract_path=initial_contract_path,
        lifecycle_stage=lifecycle_stage,
        target_wave_id=target_wave_id,
    )
