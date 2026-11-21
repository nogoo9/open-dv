"""Regression test for `opendv hold list` parity with `GET /api/v1/runs/{run_id}/decisions`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from opendv_cli.executor import LocalExecutor
from opendv_cli.main import app_main
from opendv_service import OpenDVService, OpenDVSettings, PipelineRunRequest
from opendv_service.models import CaptainHoldDecisionRequest
from opendv_testkit import deterministic_pipeline_seam

REPO_ROOT = Path(__file__).resolve().parents[3]
GC_DIR = REPO_ROOT / "examples" / "pipeline_samples" / "global_commerce_vault"


def test_hold_list_matches_service_list_run_decisions_after_resolution(
    tmp_path: Path,
    capsys: object,
) -> None:
    """`opendv hold list` must return `list_run_decisions` shape (active_holds + decisions)."""
    import yaml

    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    ont_path = ws / "commerce_graph_a_ontology.yaml"
    shutil.copy2(GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml", ont_path)
    w0_spec = yaml.safe_load((GC_DIR / "step_0_cold_start.yaml").read_text(encoding="utf-8"))
    w0_spec["project_id"] = "proj-hold-cli-parity"
    w0_spec["graph_a_ontology_path"] = str(ont_path.resolve())
    for src in w0_spec.get("sources", []):
        src["data_path"] = str((GC_DIR / src["data_path"]).resolve())

    doc_path = ws / "sla.txt"
    quote = "Enterprise SLA Penalty applies if settlement exceeds T+2 business days."
    doc_path.write_text(quote, encoding="utf-8")
    w0_spec["unstructured_documents"] = [
        {
            "doc_id": "doc-sla-01",
            "file_path": str(doc_path),
            "content": quote,
            "candidates": [
                {
                    "candidate_id": "cand-sla-01",
                    "entity_name": "SLA_PENALTY",
                    "concept_uri": "urn:ontology:entity:sla_penalty",
                    "verbatim_quote": quote,
                    "char_start": 0,
                    "char_end": len(quote),
                    "confidence_score": 0.72,
                    "er_tier": "Tier-U3",
                }
            ],
        }
    ]

    with deterministic_pipeline_seam():
        paused_rep = service.execute_pipeline_run_sync(
            PipelineRunRequest(input_spec=w0_spec, steward_hold_policy="pause")
        )
    run_id = str(paused_rep["run_id"])
    hold = service.list_holds(run_id)[0]
    service.resolve_captain_hold(
        run_id,
        CaptainHoldDecisionRequest(
            hold_id=str(hold["hold_id"]),
            event_id=str(hold["event_id"]),
            action="APPROVE",
            resolution_notes="CLI parity test approval",
            steward_id="steward-cli-test",
        ),
    )

    executor = LocalExecutor(service=service, workspace_root=ws)
    code = app_main(["hold", "list", "-r", run_id, "--json"], executor=executor)
    assert code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    cli_payload = json.loads(captured.out)
    rest_payload = service.list_run_decisions(run_id)

    assert cli_payload["active_holds"] == rest_payload["active_holds"] == []
    assert cli_payload["decisions"] == rest_payload["decisions"]
    assert len(cli_payload["decisions"]) == 1


def test_run_explorer_serializes_aliases_matching_rest_api(
    tmp_path: Path,
    capsys: object,
) -> None:
    """`opendv run explorer` must serialize DTO aliases (`by_alias=True`) matching FastAPI."""
    import yaml

    ws = tmp_path / "ws2"
    state_dir = tmp_path / "state2"
    ws.mkdir(parents=True, exist_ok=True)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    service = OpenDVService(settings=settings, workspace_root=ws, storage_root=state_dir)

    ont_path = ws / "commerce_graph_a_ontology.yaml"
    shutil.copy2(GC_DIR / "ontology" / "commerce_graph_a_ontology.yaml", ont_path)
    w0_spec = yaml.safe_load((GC_DIR / "step_0_cold_start.yaml").read_text(encoding="utf-8"))
    w0_spec["project_id"] = "proj-explorer-cli-parity"
    w0_spec["graph_a_ontology_path"] = str(ont_path.resolve())
    for src in w0_spec.get("sources", []):
        src["data_path"] = str((GC_DIR / src["data_path"]).resolve())

    with deterministic_pipeline_seam():
        rep = service.execute_pipeline_run_sync(PipelineRunRequest(input_spec=w0_spec))
    run_id = str(rep["run_id"])

    executor = LocalExecutor(service=service, workspace_root=ws)
    code = app_main(["run", "explorer", "--run-id", run_id, "--json"], executor=executor)
    assert code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    cli_payload = json.loads(captured.out)
    ontology_art = cli_payload["artifacts"]["ontology"]["ontology"]
    concepts = ontology_art["concepts"]
    assert concepts, "Expected non-empty ontology concepts"
    assert "def" in concepts[0] and "def_" not in concepts[0]
