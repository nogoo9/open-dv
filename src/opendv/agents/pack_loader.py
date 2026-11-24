"""Canonical SKILL.md pack loader and trusted workspace override verification."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunStep
from opendv.models.policy import ModelTier
from opendv.models.project import TrustedWorkspaceSpec
from opendv.models.wire_dtos_discovery import AgentPackDetail, AgentPackSummary

AGENT_PACK_IDS: tuple[str, ...] = (
    "ontology-first-mate",
    "table-cluster-scout",
    "page-range-scout",
    "ship-vault",
    "dv-semantic-critic",
    "dv-horizon-critic",
)

_BUILTIN_PACKS_DIR = Path(__file__).resolve().parent / "packs"


class AgentPackConfig(StrictModel):
    """Validated YAML frontmatter parsed from a single-format SKILL.md pack."""

    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    stage: RunStep
    model_tier: ModelTier
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)


def _normalize_text(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def compute_pack_sha256(content: str) -> str:
    """Compute canonical SHA-256 hex digest for a single SKILL.md document."""
    normalized = _normalize_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_skill_markdown(raw_text: str, *, expected_agent_id: str) -> tuple[AgentPackConfig, str]:
    normalized = _normalize_text(raw_text)
    if not normalized.startswith("---\n"):
        raise OpenDVError(
            f"Agent pack '{expected_agent_id}' is missing YAML frontmatter delimiter.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    parts = normalized.split("\n---\n", 1)
    if len(parts) != 2:
        raise OpenDVError(
            f"Agent pack '{expected_agent_id}' has unterminated YAML frontmatter.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    frontmatter_raw = parts[0][4:]
    body = parts[1].strip()
    try:
        loaded = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as exc:
        raise OpenDVError(
            f"Invalid YAML frontmatter in agent pack '{expected_agent_id}': {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    if not isinstance(loaded, dict):
        raise OpenDVError(
            f"Agent pack '{expected_agent_id}' frontmatter must be a mapping.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    try:
        cfg = AgentPackConfig.model_validate(loaded)
    except ValueError as exc:
        raise OpenDVError(
            f"Agent pack '{expected_agent_id}' failed metadata validation: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    if cfg.agent_id != expected_agent_id:
        raise OpenDVError(
            f"Agent pack directory '{expected_agent_id}' does not match "
            f"frontmatter agent_id '{cfg.agent_id}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if not body:
        raise OpenDVError(
            f"Agent pack '{expected_agent_id}' markdown body must not be empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return cfg, body


def _override_files(workspace_root: Path) -> list[Path]:
    override_root = workspace_root / ".opendv" / "agents"
    if not override_root.is_dir():
        return []
    return sorted(
        (p for p in override_root.glob("*/SKILL.md") if p.is_file()),
        key=lambda p: p.parent.name,
    )


def compute_workspace_agents_sha256(workspace_root: Path) -> str | None:
    """Compute a deterministic SHA-256 digest over all workspace .opendv/agents/*/SKILL.md files."""
    files = _override_files(workspace_root)
    if not files:
        return None
    override_root = workspace_root / ".opendv" / "agents"
    hasher = hashlib.sha256()
    for path in files:
        rel = path.relative_to(override_root).as_posix()
        text = _normalize_text(path.read_text(encoding="utf-8"))
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(text.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def verify_workspace_override_trust(
    workspace_root: Path,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
) -> str | None:
    """Verify workspace .opendv/agents overrides against WorkspaceSettings.trusted_workspaces."""
    digest = compute_workspace_agents_sha256(workspace_root)
    if digest is None:
        return None

    resolved_root = str(workspace_root.resolve())
    for spec in trusted_workspaces:
        try:
            spec_root = str(Path(spec.workspace_root).resolve())
        except OSError:
            continue
        if spec_root == resolved_root and spec.agent_pack_sha256 == digest:
            return digest

    raise OpenDVError(
        f"Untrusted workspace agent override under '{resolved_root}' (digest {digest}). "
        "Trust the workspace digest explicitly before loading overrides.",
        error_code=ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE,
    )


def _read_pack_file(
    agent_id: str,
    *,
    workspace_root: Path | None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec],
) -> tuple[AgentPackConfig, str, Literal["builtin", "workspace_override"], str]:
    if agent_id not in AGENT_PACK_IDS:
        raise OpenDVError(
            f"Unknown agent pack '{agent_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    if workspace_root is not None:
        verify_workspace_override_trust(workspace_root, trusted_workspaces)
        override_file = workspace_root / ".opendv" / "agents" / agent_id / "SKILL.md"
        if override_file.is_file():
            raw = override_file.read_text(encoding="utf-8")
            cfg, body = _parse_skill_markdown(raw, expected_agent_id=agent_id)
            return cfg, body, "workspace_override", compute_pack_sha256(raw)

    builtin_file = _BUILTIN_PACKS_DIR / agent_id / "SKILL.md"
    if not builtin_file.is_file():
        raise OpenDVError(
            f"Builtin agent pack file missing for '{agent_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    raw = builtin_file.read_text(encoding="utf-8")
    cfg, body = _parse_skill_markdown(raw, expected_agent_id=agent_id)
    return cfg, body, "builtin", compute_pack_sha256(raw)


def load_agent_pack_with_config(
    agent_id: str,
    *,
    workspace_root: Path | None = None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
) -> tuple[AgentPackDetail, AgentPackConfig]:
    """Load full AgentPackDetail alongside parsed tool/metadata config."""
    cfg, body, source, sha = _read_pack_file(
        agent_id,
        workspace_root=workspace_root,
        trusted_workspaces=trusted_workspaces,
    )
    detail = AgentPackDetail(
        summary=AgentPackSummary(
            agent_id=cfg.agent_id,
            name=cfg.name,
            role=cfg.role,
            stage=cfg.stage,
            source=source,
            sha256=sha,
        ),
        markdown_body=body,
        model_tier=cfg.model_tier,
        trusted=True,
    )
    return detail, cfg


def load_agent_pack(
    agent_id: str,
    *,
    workspace_root: Path | None = None,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
) -> AgentPackDetail:
    """Load a single builtin or trusted workspace-override SKILL.md pack."""
    detail, _ = load_agent_pack_with_config(
        agent_id,
        workspace_root=workspace_root,
        trusted_workspaces=trusted_workspaces,
    )
    return detail


def list_agent_packs(
    workspace_root: Path | None = None,
    *,
    trusted_workspaces: Sequence[TrustedWorkspaceSpec] = (),
) -> list[AgentPackSummary]:
    """List summaries for all 6 canonical agent packs (enforcing workspace trust if overridden)."""
    if workspace_root is not None:
        verify_workspace_override_trust(workspace_root, trusted_workspaces)
    summaries: list[AgentPackSummary] = []
    for agent_id in AGENT_PACK_IDS:
        detail = load_agent_pack(
            agent_id,
            workspace_root=workspace_root,
            trusted_workspaces=trusted_workspaces,
        )
        summaries.append(detail.summary)
    return summaries


__all__ = [
    "AGENT_PACK_IDS",
    "AgentPackConfig",
    "compute_pack_sha256",
    "compute_workspace_agents_sha256",
    "list_agent_packs",
    "load_agent_pack",
    "load_agent_pack_with_config",
    "verify_workspace_override_trust",
]
