"""tests/parity/golden_fixtures.py — Golden Fixture Builders and Renderers.

Provides:
- Canonical bad input fixtures for GT-03 (dropped hub, Tier-1 without GLOBAL,
  reflexive SAL, satellite GROUP BY, path traversal, schema invalid).
- Deterministic text snapshot renderer for GT-05 Explorer render verification.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from opendv_agent_harness.pipeline import run_agentic_vault_pipeline
from opendv_contracts import PipelineRunRequest, RbacRole
from opendv_service.service import OpenDVService
from opendv_testkit import build_deterministic_agy_sdk_shim
from tests.parity.normalize import normalize_for_parity, normalize_pipeline_report
from tests.parity.transports import DirectRestTransport, StandaloneCliTransport


def build_gt03_01_dropped_hub(ref_contract: dict[str, Any]) -> dict[str, Any]:
    """Build GT-03 case 01: dropped hub candidate contract violating DV-EVO-002."""
    candidate = copy.deepcopy(ref_contract)
    # Drop first hub from candidate contract
    if "hubs" in candidate and len(candidate["hubs"]) > 0:
        candidate["hubs"] = candidate["hubs"][1:]
    return candidate


def build_gt03_02_tier1_without_global(ref_contract: dict[str, Any]) -> dict[str, Any]:
    """Build GT-03 case 02: Tier-1 hub with non-GLOBAL bkcc violating DV-COL-013."""
    candidate = copy.deepcopy(ref_contract)
    for hub in candidate.get("hubs", []):
        for bks in hub.get("business_key_sources", []):
            tier = str(bks.get("key_authority_tier") or "")
            if tier == "TIER_1_GLOBAL_STANDARD":
                bks["require_src_qualifier"] = True
                bks["bkcc_prefix"] = "DEALCLOUD_LOCAL_SILO"
                return candidate
    # If no TIER_1 found, force first hub source
    if candidate.get("hubs") and candidate["hubs"][0].get("business_key_sources"):
        bks = candidate["hubs"][0]["business_key_sources"][0]
        bks["key_authority_tier"] = "TIER_1_GLOBAL_STANDARD"
        bks["require_src_qualifier"] = True
        bks["bkcc_prefix"] = "DEALCLOUD_LOCAL_SILO"
    return candidate


def build_gt03_03_reflexive_sal(ref_contract: dict[str, Any]) -> dict[str, Any]:
    """Build GT-03 case 03: reflexive Same-As Link violating DV-STR-015."""
    candidate = copy.deepcopy(ref_contract)
    for lnk in candidate.get("links", []):
        if lnk.get("name") == "sal_limited_partner":
            lnk["hub_references"][0]["source_fk_columns"] = ["lp_id"]
            lnk["hub_references"][1]["source_fk_columns"] = ["lp_id"]
            return candidate
    for lnk in candidate.get("links", []):
        if lnk.get("link_type") == "SAME_AS_LINK" or "sal_" in lnk.get("name", ""):
            refs = lnk.get("hub_references", [])
            if len(refs) >= 2:
                refs[1]["source_fk_columns"] = list(refs[0]["source_fk_columns"])
                return candidate
    return candidate


def build_gt03_04_satellite_group_by(
    ref_contract: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build GT-03 case 04: satellite load SQL containing GROUP BY violating DV-AST-006."""
    contract = copy.deepcopy(ref_contract)
    bad_sql_models = {
        "sat_fund_investran": (
            "INSERT INTO sat_fund_investran (fund_id, val) "
            "SELECT fund_id, count(val) FROM bronze_funds GROUP BY fund_id;"
        )
    }
    return contract, bad_sql_models


def build_gt03_05_path_traversal() -> str:
    """Build GT-03 case 05: path traversal input triggering PathContainmentError (400)."""
    return "../../outside_workspace_contract.yaml"


def build_gt03_06_schema_invalid() -> dict[str, Any]:
    """Build GT-03 case 06: structurally invalid schema triggering SchemaInvalid (422)."""
    return {
        "completely_invalid_key": "not_a_valid_contract",
        "missing_version": True,
        "bogus": 12345,
    }


def render_explorer_text_snapshot(bundle: dict[str, Any]) -> str:
    """Render a deterministic, human-readable text snapshot of an Explorer bundle (GT-05)."""
    lines = [
        "============================================================",
        "OPEN-DV PIPELINE EXPLORER SNAPSHOT",
        "============================================================",
        f"Project:     {bundle.get('display_name') or bundle.get('displayName') or bundle.get('project_id') or bundle.get('projectId')}",
        "Run ID:      <NORMALIZED_RUN_ID>",
        f"Spec File:   {bundle.get('spec_file') or bundle.get('specFile')}",
        f"Status:      {bundle.get('status_label') or bundle.get('statusLabel')}",
        f"Waves:       {bundle.get('waves_count') if bundle.get('waves_count') is not None else bundle.get('wavesCount')}",
        f"RBAC Role:   {bundle.get('rbac_role') or bundle.get('rbacRole')}",
        f"Gates:       {bundle.get('gates_passed', bundle.get('gatesPassed', 0))}/{bundle.get('gates_total', bundle.get('gatesTotal', 6))} passed",
    ]

    status = bundle.get("status_label") or bundle.get("statusLabel")
    is_hold = status == "PAUSED_HOLD" or any(
        s.get("tag") in ("HOLD", "ASK-USER")
        or s.get("status_tag") in ("HOLD", "ASK-USER")
        or s.get("statusTag") in ("HOLD", "ASK-USER")
        for s in bundle.get("stages", [])
    )
    if is_hold:
        lines.extend([
            "------------------------------------------------------------",
            "⚠️ STEWARD HOLD ACTIVE: Unstructured extraction requires review",
            "------------------------------------------------------------",
        ])

    lines.extend([
        "------------------------------------------------------------",
        "STAGES",
        "------------------------------------------------------------",
    ])
    for s in bundle.get("stages", []):
        stag = s.get("tag") or s.get("status_tag") or s.get("statusTag") or "PENDING"
        key = s.get("key") or ""
        name = s.get("name") or ""
        lines.append(f"[{stag:14}] {key:20} {name}")

    lines.extend([
        "------------------------------------------------------------",
        "ARTIFACTS INDEX",
        "------------------------------------------------------------",
    ])
    artifacts = bundle.get("artifacts", {})
    for art_id in sorted(artifacts.keys()):
        art = artifacts[art_id]
        grp = art.get("group") or ""
        name = art.get("name") or ""
        fmt = art.get("fmt") or art.get("format") or ""
        lines.append(f"* {art_id:15} [{grp:12}] [{fmt:5}] {name}")

    lines.append("============================================================\n")
    return "\n".join(lines)


def run_gt01_lifecycle_waves(
    golden_manager: Any,
    repo_root: Path,
    workspace_dir: Path,
) -> dict[str, Any]:
    """Execute GT-01 4-wave chained lifecycle and assert/update 25 golden files.

    Returns the Wave 3 Explorer bundle dictionary for GT-05 snapshot rendering.
    """
    def shim_runner(*args: Any, **kwargs: Any) -> Any:
        return run_agentic_vault_pipeline(
            *args,
            **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
        )

    sample_dir = (repo_root / "examples" / "pipeline_samples" / "global_commerce_vault").resolve()
    harness_cfg = (sample_dir / "harness_config.yaml").resolve()
    waves = [
        (0, (sample_dir / "wave0_cold_start.yaml").resolve(), "W0"),
        (1, (sample_dir / "wave1_incremental_billing.yaml").resolve(), "W1"),
        (2, (sample_dir / "wave2_schema_drift.yaml").resolve(), "W2"),
        (3, (sample_dir / "wave3_unstructured_msa.yaml").resolve(), "W3"),
    ]

    service = OpenDVService(
        workspace_root=repo_root,
        storage_root=workspace_dir / "state",
        pipeline_runner=shim_runner,
    )

    prior_contract: Path | None = None
    wave3_bundle_dict: dict[str, Any] = {}

    for w_idx, w_spec, w_id in waves:
        out_dir = workspace_dir / f"wave{w_idx}"
        req = PipelineRunRequest(
            input_spec_path=str(w_spec),
            harness_config_path=str(harness_cfg),
            output_dir=str(out_dir),
            initial_contract_path=str(prior_contract) if prior_contract else None,
            wave_id=w_id,
            provider_override="agy",
        )
        res = service.execute_pipeline_run_sync(req)
        run_id = str(res.get("run_id", ""))

        # 1. final_contract.yaml
        final_contract_path = out_dir / "final_contract.yaml"
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-01",
            relative_file=f"GT-01/wave{w_idx}/final_contract.yaml",
            actual_bytes=final_contract_path.read_bytes(),
            generated_from=f"PAR-S1-W{w_idx}: opendv pipeline run",
        )
        prior_contract = final_contract_path

        # 2. final_canonical_ir.json
        final_ir_path = out_dir / "final_canonical_ir.json"
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-01",
            relative_file=f"GT-01/wave{w_idx}/final_canonical_ir.json",
            actual_bytes=final_ir_path.read_bytes(),
            generated_from=f"PAR-S1-W{w_idx}: opendv pipeline run",
        )

        # 3. final_four_zone.sql
        final_sql_path = out_dir / "final_four_zone.sql"
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-01",
            relative_file=f"GT-01/wave{w_idx}/final_four_zone.sql",
            actual_bytes=final_sql_path.read_bytes(),
            generated_from=f"PAR-S1-W{w_idx}: opendv pipeline run",
        )

        # 4. pipeline_report.json (normalized)
        norm_rep = normalize_pipeline_report(res, path_root=str(repo_root))
        rep_bytes = (json.dumps(norm_rep, indent=2, sort_keys=True) + "\n").encode("utf-8")
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-01",
            relative_file=f"GT-01/wave{w_idx}/pipeline_report.json",
            actual_bytes=rep_bytes,
            generated_from=f"PAR-S1-W{w_idx}: opendv pipeline run",
        )

        # 5. explorer_bundle.json (normalized)
        bundle_obj = service.get_explorer_bundle(run_id, role=RbacRole.DATA_STEWARD)
        raw_bundle = bundle_obj.model_dump(mode="json")
        if w_idx == 3:
            wave3_bundle_dict = raw_bundle
        norm_bundle = normalize_for_parity(raw_bundle, path_root=str(repo_root))
        bundle_bytes = (json.dumps(norm_bundle, indent=2, sort_keys=True) + "\n").encode("utf-8")
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-01",
            relative_file=f"GT-01/wave{w_idx}/explorer_bundle.json",
            actual_bytes=bundle_bytes,
            generated_from=f"PAR-S1-W{w_idx}: opendv pipeline run",
        )

        # 6. events.jsonl (stage_id, status, wave_index)
        event_lines = [
            json.dumps(
                {
                    "stage_id": st["stage_id"],
                    "status": st["status"],
                    "wave_index": st["wave_index"],
                },
                sort_keys=True,
            )
            for st in res.get("step_reports", [])
        ]
        events_bytes = ("\n".join(event_lines) + "\n").encode("utf-8")
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-01",
            relative_file=f"GT-01/wave{w_idx}/events.jsonl",
            actual_bytes=events_bytes,
            generated_from=f"PAR-S1-W{w_idx}: opendv pipeline run",
        )

        # 7. wave 3 hold_record.json
        if w_idx == 3:
            holds = service.list_holds(run_id)
            assert len(holds) > 0, "Expected at least 1 hold record in wave 3"
            norm_hold = normalize_for_parity(holds[0], path_root=str(repo_root))
            hold_bytes = (json.dumps(norm_hold, indent=2, sort_keys=True) + "\n").encode("utf-8")
            golden_manager.assert_golden_byte_equality(
                golden_id="GT-01",
                relative_file="GT-01/wave3/hold_record.json",
                actual_bytes=hold_bytes,
                generated_from="PAR-S8-W3: opendv hold list",
            )

    return wave3_bundle_dict


def capture_gt03_rejections(
    golden_manager: Any,
    repo_root: Path,
    workspace_dir: Path,
    rest_transport: DirectRestTransport | None = None,
    client_cli_transport: Any | None = None,
) -> None:
    """Execute GT-03 6 canonical bad inputs and assert/update 10 golden files."""
    pe_contract_path = repo_root / "examples" / "private_equity" / "dv_pe_model_contract.yaml"
    pe_contract = yaml.safe_load(pe_contract_path.read_text(encoding="utf-8"))
    graph_a = repo_root / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
    graph_b = repo_root / "examples" / "private_equity" / "pe_graph_b_profile.json"

    cli = StandaloneCliTransport(workspace_dir=workspace_dir / "gt03_cli")

    for case_id, candidate, extra_kwargs in [
        (
            "01_dropped_hub",
            build_gt03_01_dropped_hub(pe_contract),
            {"prior": pe_contract, "graph_a": graph_a, "graph_b": graph_b},
        ),
        (
            "02_tier1_without_global",
            build_gt03_02_tier1_without_global(pe_contract),
            {"graph_a": graph_a, "graph_b": graph_b, "apply_codemods": False},
        ),
        (
            "03_reflexive_sal",
            build_gt03_03_reflexive_sal(pe_contract),
            {"graph_a": graph_a, "graph_b": graph_b},
        ),
    ]:
        res_cli = cli.verify(contract=candidate, **extra_kwargs)
        assert res_cli.get("verdict") == "REJECT-WITH-TRACE"
        norm_cli = normalize_for_parity(res_cli, path_root=str(repo_root))
        if rest_transport:
            res_rest = rest_transport.verify(contract=candidate, **extra_kwargs)
            norm_rest = normalize_for_parity(res_rest, path_root=str(repo_root))
            assert norm_cli == norm_rest, f"Parity mismatch on GT-03/{case_id}"
        if client_cli_transport:
            res_client = client_cli_transport.verify(contract=candidate, **extra_kwargs)
            norm_client = normalize_for_parity(res_client, path_root=str(repo_root))
            assert norm_cli == norm_client, f"Client CLI parity mismatch on GT-03/{case_id}"

        verdict_bytes = (json.dumps(norm_cli, indent=2, sort_keys=True) + "\n").encode("utf-8")
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-03",
            relative_file=f"GT-03/{case_id}/verdict.json",
            actual_bytes=verdict_bytes,
            generated_from=f"GT-03: opendv verify {case_id}",
        )
        toon_text = res_cli.get("toon_text", "")
        golden_manager.assert_golden_byte_equality(
            golden_id="GT-03",
            relative_file=f"GT-03/{case_id}/trace.toon",
            actual_bytes=(toon_text.strip() + "\n").encode("utf-8"),
            generated_from=f"GT-03: opendv verify {case_id}",
        )

    # Case 04: satellite GROUP BY
    c4, sql4 = build_gt03_04_satellite_group_by(pe_contract)
    res_cli_4 = cli.verify(contract=c4, graph_a=graph_a, graph_b=graph_b, compiled_sql_models=sql4)
    assert res_cli_4.get("verdict") == "REJECT-WITH-TRACE"
    norm_cli_4 = normalize_for_parity(res_cli_4, path_root=str(repo_root))
    if rest_transport:
        res_rest_4 = rest_transport.verify(
            contract=c4, graph_a=graph_a, graph_b=graph_b, compiled_sql_models=sql4
        )
        norm_rest_4 = normalize_for_parity(res_rest_4, path_root=str(repo_root))
        assert norm_cli_4 == norm_rest_4, "Parity mismatch on GT-03/04_satellite_group_by"
    if client_cli_transport:
        res_client_4 = client_cli_transport.verify(
            contract=c4, graph_a=graph_a, graph_b=graph_b, compiled_sql_models=sql4
        )
        norm_client_4 = normalize_for_parity(res_client_4, path_root=str(repo_root))
        assert norm_cli_4 == norm_client_4, "Client CLI parity mismatch on GT-03/04_satellite_group_by"

    verdict_4_bytes = (json.dumps(norm_cli_4, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-03",
        relative_file="GT-03/04_satellite_group_by/verdict.json",
        actual_bytes=verdict_4_bytes,
        generated_from="GT-03: opendv verify 04_satellite_group_by",
    )
    toon_4_text = res_cli_4.get("toon_text", "")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-03",
        relative_file="GT-03/04_satellite_group_by/trace.toon",
        actual_bytes=(toon_4_text.strip() + "\n").encode("utf-8"),
        generated_from="GT-03: opendv verify 04_satellite_group_by",
    )

    # Case 05: path traversal
    c5 = build_gt03_05_path_traversal()
    res_cli_5 = cli.verify(contract=c5)
    assert res_cli_5.get("status") == "ERROR"
    problem_5 = {
        "detail": "Path containment violation: requested path is outside permitted workspace boundaries.",
        "instance": "/api/v1/verify",
        "status": 400,
        "title": "Path Containment Violation",
        "type": "https://open-dv.dev/problems/path-containment-error",
    }
    if rest_transport:
        res_rest_5 = rest_transport.verify(contract=c5)
        assert res_rest_5.get("status") == "ERROR"
        if "detail" in res_rest_5 and res_rest_5["detail"].startswith("{"):
            problem_5 = json.loads(res_rest_5["detail"])
    prob_5_bytes = (json.dumps(problem_5, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-03",
        relative_file="GT-03/05_path_traversal/problem.json",
        actual_bytes=prob_5_bytes,
        generated_from="GT-03: opendv verify 05_path_traversal",
    )

    # Case 06: schema invalid
    c6 = build_gt03_06_schema_invalid()
    res_cli_6 = cli.verify(contract=c6)
    assert res_cli_6.get("status") == "ERROR"
    problem_6 = {
        "detail": "Invalid contract schema: 'contract_version' is a required property",
        "instance": "/api/v1/verify",
        "status": 422,
        "title": "Schema Validation Error",
        "type": "https://open-dv.dev/problems/schema-validation-error",
    }
    if rest_transport:
        res_rest_6 = rest_transport.verify(contract=c6)
        assert res_rest_6.get("status") == "ERROR"
        if "detail" in res_rest_6 and res_rest_6["detail"].startswith("{"):
            problem_6 = json.loads(res_rest_6["detail"])
    norm_prob_6 = normalize_for_parity(problem_6, path_root=str(repo_root))
    prob_6_bytes = (json.dumps(norm_prob_6, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-03",
        relative_file="GT-03/06_schema_invalid/problem.json",
        actual_bytes=prob_6_bytes,
        generated_from="GT-03: opendv verify 06_schema_invalid",
    )
