"""Source inspection execution mixin for LocalExecutor."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from opendv_contracts.errors import OpenDVError
from opendv_service.use_cases.sources_inspect import inspect_source


class SourceExecutionMixin:
    """Mixin providing source inspection commands for LocalExecutor."""

    def _execute_source(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "source_action", None) or "inspect"
        if action != "inspect":
            return 2, {"status": "ERROR", "error": f"Unknown source action: '{action}'"}

        uri_arg = getattr(args, "uri", None)
        if not uri_arg:
            return 2, {
                "status": "ERROR",
                "error_code": "SYNTAX_INVALID_ARGUMENT",
                "error": "--uri is required for 'source inspect'",
                "detail": "--uri is required for 'source inspect'",
            }

        adapter_arg = getattr(args, "adapter", None)
        namespace_arg = getattr(args, "namespace", None)
        table_arg = getattr(args, "table", None)
        credentials_ref_arg = getattr(args, "credentials_ref", None)

        from opendv_contracts.dtos.sources import (
            NamespaceSpecDTO,
            SourceInspectRequestDTO,
        )

        ns_dto: NamespaceSpecDTO | None = None
        if namespace_arg:
            parts = str(namespace_arg).split(".")
            if len(parts) >= 3:
                ns_dto = NamespaceSpecDTO(
                    catalog=parts[0],
                    schema_name=parts[1],
                )
                if not table_arg:
                    table_arg = parts[2]
            elif len(parts) == 2:
                ns_dto = NamespaceSpecDTO(
                    catalog=parts[0],
                    schema_name=parts[1],
                )
            else:
                ns_dto = NamespaceSpecDTO(
                    catalog=None,
                    schema_name=parts[0],
                )

        options: dict[str, Any] = {}
        if table_arg:
            options["table"] = str(table_arg)
            options["table_selection"] = {
                "mode": "EXPLICIT",
                "tables": [str(table_arg)],
            }

        try:
            req = SourceInspectRequestDTO(
                uri=str(uri_arg),
                adapter=str(adapter_arg) if adapter_arg else None,
                credentials_ref=str(credentials_ref_arg) if credentials_ref_arg else None,
                namespace=ns_dto,
                options=options,
            )
            cas_store = getattr(self.service, "cas_store", None) or getattr(
                getattr(self.service, "state", None), "cas_store", None
            )
            ws_root = getattr(self.service, "workspace_root", None) or getattr(
                getattr(self.service, "state", None), "workspace_root", None
            )
            cand_p = Path(
                str(uri_arg)
                .replace("sqlite:///", "")
                .replace("sqlite://", "")
                .split("?")[0]
                .split("#")[0]
            ).resolve()
            extra_allowed: list[Path] = []
            if cand_p.is_file():
                extra_allowed.append(cand_p.parent)
            res = inspect_source(
                req,
                cas_store=cas_store,
                workspace_root=ws_root,
                allowed_roots=extra_allowed,
            )
            payload = res.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload
        except (OpenDVError, ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
            err_msg = str(exc)
            err_code = (
                "SYNTAX_INVALID_ARGUMENT"
                if ("credentials_ref" in err_msg or "literal credentials" in err_msg)
                else "RESOURCE_NOT_FOUND"
                if "not found" in err_msg.lower()
                else "INTERNAL_ERROR"
            )
            exit_code = 3 if err_code == "RESOURCE_NOT_FOUND" else 2
            return exit_code, {
                "status": "ERROR",
                "error_code": err_code,
                "error": err_msg,
                "detail": err_msg,
            }


__all__ = ["SourceExecutionMixin"]
