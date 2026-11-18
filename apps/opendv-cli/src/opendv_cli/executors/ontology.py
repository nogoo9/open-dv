"""Ontology execution mixin for LocalExecutor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from opendv_contracts import (
    OntologyDecisionRequest,
    OntologyDiffRequest,
    OntologySuggestionRequest,
    OntologyValidateDeltaRequest,
)


class OntologyExecutionMixin:
    """Mixin providing ontology management and evolution for LocalExecutor."""

    def _execute_ontology(self, args: argparse.Namespace) -> tuple[int, Any]:
        action = getattr(args, "ontology_action", None)
        if not action:
            return 2, {
                "status": "ERROR",
                "error": (
                    "Missing ontology subcommand (suggest, validate-delta, diff, accept, reject)"
                ),
            }

        action_norm = action.lower()

        if action_norm == "studio":
            from opendv_cli.commands.ontology_studio import run_ontology_studio_command

            return run_ontology_studio_command(args, executor=self)

        if action_norm == "suggest":
            req = OntologySuggestionRequest(
                base_ontology_path=getattr(args, "base", None),
                empirical_profile_path=getattr(args, "profile", None),
                bronze_source_path=getattr(args, "source", None),
                context_doc_path=getattr(args, "context_doc", None),
                mode=getattr(args, "mode", "DELTA"),
                domain_namespace=getattr(args, "domain_namespace", None),
                recommender_id=getattr(args, "recommender_id", None),
            )
            res = self.service.suggest_ontology(req)
            payload = res.model_dump(mode="json")
            payload["mode"] = "local"
            output_path = getattr(args, "output", None)
            if output_path:
                Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return 0, payload

        if action_norm == "validate-delta":
            delta_path = getattr(args, "delta", None)
            base_path = getattr(args, "base", None)
            if not delta_path or not base_path:
                return 2, {
                    "status": "ERROR",
                    "error": "--delta and --base are required for 'ontology validate-delta'",
                }
            req = OntologyValidateDeltaRequest(
                delta_path=delta_path,
                base_ontology_path=base_path,
                contract_path=getattr(args, "contract", None),
            )
            val = self.service.validate_ontology_delta(req)
            payload = val.model_dump(mode="json")
            payload["mode"] = "local"
            exit_code = 0 if payload.get("valid", False) else 1
            return exit_code, payload

        if action_norm == "diff":
            base_path = getattr(args, "base", None)
            target_path = getattr(args, "target", None)
            if not base_path or not target_path:
                return 2, {
                    "status": "ERROR",
                    "error": "--base and --target are required for 'ontology diff'",
                }
            req = OntologyDiffRequest(
                base_ontology_path=base_path,
                target_ontology_path=target_path,
            )
            diff_res = self.service.diff_ontology(req)
            payload = diff_res.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload

        if action_norm == "accept":
            delta_path = getattr(args, "delta", None)
            base_path = getattr(args, "base", None)
            if not delta_path or not base_path:
                return 2, {
                    "status": "ERROR",
                    "error": "--delta and --base are required for 'ontology accept'",
                }
            op_id_arg = getattr(args, "op_id", None)
            op_ids = None
            if op_id_arg:
                op_ids = [op.strip() for op in op_id_arg.split(",") if op.strip()]

            dec_req = OntologyDecisionRequest(
                action="ACCEPT",
                delta_path=delta_path,
                base_ontology_path=base_path,
                contract_path=getattr(args, "contract", None),
                accepted_op_ids=op_ids,
                decided_by=getattr(args, "decided_by", None) or "steward",
                steward_notes=getattr(args, "notes", None) or "",
            )
            dec_res = self.service.decide_ontology(dec_req)
            payload = dec_res.model_dump(mode="json")
            payload["mode"] = "local"
            output_path = getattr(args, "output", None)
            if output_path and dec_res.resulting_ontology_yaml:
                Path(output_path).write_text(dec_res.resulting_ontology_yaml, encoding="utf-8")
            return 0, payload

        if action_norm == "reject":
            dec_req = OntologyDecisionRequest(
                action="REJECT",
                proposal_id=getattr(args, "proposal_id", None),
                delta_path=getattr(args, "delta", None),
                base_ontology_path=getattr(args, "base", None),
                decided_by=getattr(args, "decided_by", None) or "steward",
                steward_notes=getattr(args, "notes", None) or "",
            )
            dec_res = self.service.decide_ontology(dec_req)
            payload = dec_res.model_dump(mode="json")
            payload["mode"] = "local"
            return 0, payload

        return 2, {
            "status": "ERROR",
            "error": f"Unknown ontology subcommand: '{action}'",
        }


__all__ = ["OntologyExecutionMixin"]
