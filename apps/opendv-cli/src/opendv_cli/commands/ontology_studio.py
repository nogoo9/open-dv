"""`opendv ontology studio` command entrypoint and backward-compatible facade."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from opendv_cli.commands.ontology_studio_agent import (
    _normalize_answer_item_dict,
    handle_answer,
    handle_clarify,
    handle_construct,
    handle_fibo_qa,
)
from opendv_cli.commands.ontology_studio_common import (
    MIN_PROMOTION_RATIONALE_LENGTH,
    MIN_WAIVER_RATIONALE_LENGTH,
    StudioLocalError,
    _atomic_write_json,
    _atomic_write_text,
    _build_governance_validator,
    _build_signed_waiver_record,
    _build_workspace_deployment_provider,
    _derive_session_status,
    _err,
    _err_from_opendv_error,
    _extract_proposal_base_sha256,
    _extract_proposal_operations,
    _extract_proposal_proposer,
    _get_session_manager,
    _get_studio_root,
    _is_reparse_or_symlink,
    _load_and_verify_waiver_credentials,
    _load_session_version_ontology,
    _normalize_delta_operation,
    _normalize_external_mapping,
    _require_session,
    _require_session_context,
    _resolve_configured_workspace_roots,
    _resolve_local_workspace_root,
    _resolve_proposal_path,
    _resolve_studio_principal,
    _validate_out_path,
    _validate_session,
    resolve_studio_principal,
)
from opendv_cli.commands.ontology_studio_deltas import handle_delta_apply, handle_delta_validate
from opendv_cli.commands.ontology_studio_exports import (
    _resolve_workspace_target_contract,
    handle_bundle_clone,
    handle_bundle_export,
    handle_bundle_import,
    handle_export,
    handle_promote,
)
from opendv_cli.commands.ontology_studio_logs import handle_logs
from opendv_cli.commands.ontology_studio_proposals import (
    handle_proposal_accept,
    handle_proposal_list,
    handle_proposal_reject,
    handle_proposal_show,
    handle_staged_list,
    handle_staged_promote,
)
from opendv_cli.commands.ontology_studio_rebase import (
    _load_intervening_deltas_strict,
    handle_proposal_rebase,
    handle_proposal_refine,
)
from opendv_cli.commands.ontology_studio_sessions import (
    handle_create,
    handle_delete,
    handle_init,
    handle_list,
    handle_starter_pack_apply,
    handle_starter_pack_list,
    handle_status,
)
from opendv_cli.commands.ontology_studio_sources import (
    handle_attach_source,
    handle_parse,
    handle_text_add,
    handle_upload,
)

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor

_STUDIO_DISPATCH = {
    "init": handle_init,
    "create": handle_create,
    "delete": handle_delete,
    "list": handle_list,
    "status": handle_status,
    "upload": handle_upload,
    "attach-source": handle_attach_source,
    "attach_source": handle_attach_source,
    "parse": handle_parse,
    "clarify": handle_clarify,
    "answer": handle_answer,
    "construct": handle_construct,
    "fibo-qa": handle_fibo_qa,
    "fibo_qa": handle_fibo_qa,
    "logs": handle_logs,
    ("delta", "validate"): handle_delta_validate,
    ("delta", "apply"): handle_delta_apply,
    ("proposal", "list"): handle_proposal_list,
    ("proposal", "show"): handle_proposal_show,
    ("proposal", "accept"): handle_proposal_accept,
    ("proposal", "reject"): handle_proposal_reject,
    ("proposal", "rebase"): handle_proposal_rebase,
    ("proposal", "refine"): handle_proposal_refine,
    ("starter-pack", "list"): handle_starter_pack_list,
    ("starter_pack", "list"): handle_starter_pack_list,
    ("starter-pack", "apply"): handle_starter_pack_apply,
    ("starter_pack", "apply"): handle_starter_pack_apply,
    ("text", "add"): handle_text_add,
    ("staged", "list"): handle_staged_list,
    ("staged", "promote"): handle_staged_promote,
    "export": handle_export,
    "promote": handle_promote,
    ("bundle", "export"): handle_bundle_export,
    ("bundle", "import"): handle_bundle_import,
    ("bundle", "clone"): handle_bundle_clone,
}


def run_ontology_studio_command(args: argparse.Namespace, *, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """Top-level entrypoint for `opendv ontology studio <cmd>` in-process execution."""
    action = getattr(args, "studio_action", None)
    if not action:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing studio action (e.g. init, create, delete, list, status, ...)")
    action_norm = (action or "").replace("-", "_")
    sub_action = getattr(args, f"studio_{action_norm}_action", None) or getattr(args, f"studio_{action}_action", None)
    handler = _STUDIO_DISPATCH.get((action, sub_action)) or _STUDIO_DISPATCH.get((action_norm, sub_action)) if sub_action else (_STUDIO_DISPATCH.get(action) or _STUDIO_DISPATCH.get(action_norm))
    if not handler:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Unknown studio subcommand '{action}'")
    return handler(args, executor=executor)


def register_studio_subparsers(parser: argparse.ArgumentParser) -> None:
    """No-op hook retained for backwards compatibility (`cli-spec.json` drives parser)."""
    _ = parser


__all__ = [
    "MIN_PROMOTION_RATIONALE_LENGTH",
    "MIN_WAIVER_RATIONALE_LENGTH",
    "StudioLocalError",
    "_atomic_write_json",
    "_atomic_write_text",
    "_build_governance_validator",
    "_build_signed_waiver_record",
    "_build_workspace_deployment_provider",
    "_derive_session_status",
    "_err",
    "_err_from_opendv_error",
    "_extract_proposal_base_sha256",
    "_extract_proposal_operations",
    "_extract_proposal_proposer",
    "_get_session_manager",
    "_get_studio_root",
    "_is_reparse_or_symlink",
    "_load_and_verify_waiver_credentials",
    "_load_intervening_deltas_strict",
    "_load_session_version_ontology",
    "_normalize_answer_item_dict",
    "_normalize_delta_operation",
    "_normalize_external_mapping",
    "_require_session",
    "_require_session_context",
    "_resolve_configured_workspace_roots",
    "_resolve_local_workspace_root",
    "_resolve_proposal_path",
    "_resolve_studio_principal",
    "_resolve_workspace_target_contract",
    "_validate_out_path",
    "_validate_session",
    "handle_answer",
    "handle_attach_source",
    "handle_bundle_clone",
    "handle_bundle_export",
    "handle_bundle_import",
    "handle_clarify",
    "handle_construct",
    "handle_create",
    "handle_delete",
    "handle_delta_apply",
    "handle_delta_validate",
    "handle_export",
    "handle_fibo_qa",
    "handle_init",
    "handle_list",
    "handle_logs",
    "handle_parse",
    "handle_promote",
    "handle_proposal_accept",
    "handle_proposal_list",
    "handle_proposal_rebase",
    "handle_proposal_refine",
    "handle_proposal_reject",
    "handle_proposal_show",
    "handle_staged_list",
    "handle_staged_promote",
    "handle_starter_pack_apply",
    "handle_starter_pack_list",
    "handle_status",
    "handle_text_add",
    "handle_upload",
    "register_studio_subparsers",
    "resolve_studio_principal",
    "run_ontology_studio_command",
]
