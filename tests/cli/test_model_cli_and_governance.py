"""Tests for stateless `opendv model {spec,profile,verify,run}` and governance CLI parity."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from opendv.agents.ports import AgentTurnRequest, AgentTurnResult
from opendv.cli.main import app
from opendv.modeling.token_utils import count_tokens
from opendv.models import (
    BusinessKeyPart,
    ClarificationQuestion,
    CliEnvelope,
    CliExitCode,
    ColumnTriageEntry,
    DraftField,
    DraftHubSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    ErrorCode,
    HarnessProvider,
    HorizonCriticVerdict,
    ModelingPreferences,
    OpenDVError,
    PII_TOKEN_RE,
    ProfileBundle,
    ProjectInitReq,
    ProjectSettingsUpdateReq,
    SemanticCriticVerdict,
    SourceAttachReq,
    VaultBuildReq,
)
from opendv.service import OpenDVService
from opendv.service.workflows._discovery_run import Step4CoordinatorOutput
from opendv.service.workflows.modeling_stateless import profile_sources_stateless
from tests.cli._cli_test_helpers import (
    build_account_ontology,
    build_account_ontology_yaml,
    run_loopback_api_server,
    sample_question,
)

_RUNNER = CliRunner()


def _invoke_cli(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, Any] | None]:
    result = _RUNNER.invoke(app, args, env=env)
    parsed: dict[str, Any] | None = None
    if "--json" in args and result.stdout.strip():
        parsed = json.loads(result.stdout)
        CliEnvelope.model_validate(parsed)
    return result.exit_code, result.stdout, parsed


def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    state_root = tmp_path / "user_state"
    state_root.mkdir(parents=True, exist_ok=True)
    state_dir = state_root / "opendv"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_HOME", str(work_dir / ".opendv"))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("HOME", str(state_root))
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)
    monkeypatch.chdir(work_dir)
    return state_dir


def _build_valid_account_draft(
    project_id: str,
    source_id: str,
    table_id: str,
    *,
    include_email_pii: bool = False,
    use_surrogate_bk: bool = False,
    missing_triage: bool = False,
    hold_question: ClarificationQuestion | None = None,
) -> DraftVaultIR:
    bk_col = "row_id" if use_surrogate_bk else "account_id"
    triage: list[ColumnTriageEntry] = []
    if not missing_triage:
        triage = [
            ColumnTriageEntry(
                table_id=table_id,
                col_name=bk_col,
                disposition="MODELED",
                target_ref="hub_account",
                rationale="Business key",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="account_name",
                disposition="MODELED",
                target_ref="sat_account_core",
                rationale="Descriptive attribute",
            ),
            ColumnTriageEntry(
                table_id=table_id,
                col_name="status",
                disposition="MODELED",
                target_ref="sat_account_core",
                rationale="Status attribute",
            ),
        ]
        if use_surrogate_bk:
            triage.append(
                ColumnTriageEntry(
                    table_id=table_id,
                    col_name="account_id",
                    disposition="MODELED",
                    target_ref="sat_account_core",
                    rationale="Alternate natural key attribute",
                )
            )
        if include_email_pii:
            triage.append(
                ColumnTriageEntry(
                    table_id=table_id,
                    col_name="email",
                    disposition="MODELED",
                    target_ref="sat_account_pii",
                    rationale="Customer email attribute",
                )
            )

    core_mappings = {"account_name": "account_name", "status": "status"}
    core_attrs = [
        DraftField(
            name="account_name",
            logical_type="UTF8_STRING",
            is_pii=False,
            nullable=False,
        ),
        DraftField(
            name="status",
            logical_type="UTF8_STRING",
            is_pii=False,
            nullable=False,
        ),
    ]
    if use_surrogate_bk:
        core_mappings["account_id"] = "account_id"
        core_attrs.append(
            DraftField(
                name="account_id",
                logical_type="UTF8_STRING",
                is_pii=False,
                nullable=False,
            )
        )
    sats: list[DraftSatelliteSpec] = []
    if include_email_pii:
        sats.append(
            DraftSatelliteSpec(
                sat_name="sat_account_pii",
                sat_kind="PII",
                parent_name="hub_account",
                source_bindings=[
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=table_id,
                        col_mappings={"email": "email"},
                    )
                ],
                attributes=[
                    DraftField(
                        name="email",
                        logical_type="UTF8_STRING",
                        is_pii=True,
                        nullable=False,
                    )
                ],
            )
        )

    sats.insert(
        0,
        DraftSatelliteSpec(
            sat_name="sat_account_core",
            sat_kind="STANDARD",
            parent_name="hub_account",
            source_bindings=[
                DraftSourceBinding(
                    source_id=source_id,
                    table_id=table_id,
                    col_mappings=core_mappings,
                )
            ],
            attributes=core_attrs,
        ),
    )

    return DraftVaultIR(
        project_id=project_id,
        ontology_version="1.0.0",
        wave_number=1,
        hubs=[
            DraftHubSpec(
                hub_name="hub_account",
                key_tier="TIER_1_GLOBAL_STANDARD",
                business_keys=[
                    BusinessKeyPart(
                        table_id=table_id,
                        col_name=bk_col,
                        source_id=source_id,
                        key_tier="TIER_1_GLOBAL_STANDARD",
                        bkcc_value="GLOBAL" if use_surrogate_bk else "IBOR",
                    )
                ],
                source_bindings=[
                    DraftSourceBinding(
                        source_id=source_id,
                        table_id=table_id,
                        col_mappings={bk_col: bk_col},
                    )
                ],
                attributes=[],
            )
        ],
        satellites=sats,
        column_triage=triage,
        pending_clarifications=[hold_question] if hold_question is not None else [],
    )


class _DeterministicModelHarness:
    """Minimal deterministic `AgentHarnessPort` emitting valid INV-05-clean DraftVaultIR."""

    provider_name: HarnessProvider = "agy"

    def __init__(
        self,
        *,
        project_id: str,
        source_id: str,
        table_id: str,
        hold_once: bool = False,
    ) -> None:
        self.project_id = project_id
        self.source_id = source_id
        self.table_id = table_id
        self.hold_once = hold_once
        self._held_runs: set[str] = set()
        self.calls: int = 0

    async def run_turn(
        self,
        request: AgentTurnRequest,
        *,
        event_sink: Any = None,
        scout_dispatcher: Any = None,
    ) -> AgentTurnResult:
        _ = (event_sink, scout_dispatcher)
        self.calls += 1
        out: Any
        if request.step == "step4":
            out = Step4CoordinatorOutput(
                proposed_ops=[],
                clarification_questions=[],
                staged_candidate_ids=[],
                summary="Baseline ontology confirmed.",
            )
        elif request.output_schema is SemanticCriticVerdict:
            out = SemanticCriticVerdict(score=95, findings=[], accepted_waiver_ids=[])
        elif request.output_schema is HorizonCriticVerdict:
            out = HorizonCriticVerdict(
                score=95,
                classification="REASONABLY_STABLE",
                absorbed_natively=[],
                bucket_a_ops=[],
                bucket_b_questions=[],
                bucket_c_watchlist=[],
            )
        else:
            q: ClarificationQuestion | None = None
            if self.hold_once and request.run_id not in self._held_runs:
                self._held_runs.add(request.run_id)
                q = sample_question("q1")
            out = _build_valid_account_draft(
                self.project_id,
                self.source_id,
                self.table_id,
                hold_question=q,
            )
        return AgentTurnResult(
            agent_id=request.agent_id,
            provider="agy",
            model_id="scripted-model",
            text_output=out.model_dump_json(),
            structured_output=out,
        )


def test_stateless_model_spec_and_profile_zero_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stateless `model spec` and `model profile` emit bounded specs/PII-safe bundles with 0 disk."""
    work_dir = tmp_path / "stateless_ws"
    _isolate_env(tmp_path, monkeypatch, work_dir)

    rc, _, spec_env = _invoke_cli(["--json", "model", "spec", "--part", "all"])
    assert rc == 0 and spec_env is not None and spec_env["ok"] is True
    spec_data = spec_env["data"]
    assert {"DraftVaultIR", "DraftHubSpec", "ProfileBundle", "ModelingPreferences"} <= set(
        spec_data["schemas"].keys()
    )
    assert "inv_05" in spec_data["constraints"]
    assert "OMITTED_INTENTIONALLY" in spec_data["constraints"]["column_triage"]
    assert "15" in spec_data["constraints"]["column_triage"]
    assert "ANTI_SILO_VIOLATION" in spec_data["guide_markdown"]
    assert "OMITTED_INTENTIONALLY" in spec_data["guide_markdown"]
    assert count_tokens(spec_data["guide_markdown"]) <= 2500
    assert spec_data["preferences_schema"] == ModelingPreferences.model_json_schema()
    assert not (work_dir / ".opendv").exists()

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = sources_dir / "crm.sqlite"
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            """
            CREATE TABLE customers (
                customer_code TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                status TEXT NOT NULL,
                tier_code TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO customers (customer_code, email, status, tier_code) VALUES (?, ?, ?, ?)",
            [
                ("C001", "alice@example.com", "ACTIVE", "GOLD"),
                ("C002", "bob@example.com", "INACTIVE", "SILVER"),
                ("C003", "carol@example.com", "ACTIVE", "GOLD"),
                ("C004", "dave@example.com", "ACTIVE", "BRONZE"),
                ("C005", "eve@example.com", "INACTIVE", "SILVER"),
            ],
        )
        conn.commit()

    erp_customers = sources_dir / "erp_customers.csv"
    erp_customers.write_text(
        "customer_code,email,segment_code\n"
        "C001,alice@corp.example,ENT\n"
        "C002,bob@corp.example,SMB\n"
        "C003,carol@corp.example,ENT\n"
        "C004,dave@corp.example,SMB\n"
        "C005,eve@corp.example,ENT\n",
        encoding="utf-8",
    )
    erp_orders = sources_dir / "erp_orders.csv"
    erp_orders.write_text(
        "order_id,customer_code,status,currency_code\n"
        "O1,C001,OPEN,USD\n"
        "O2,C002,CLOSED,EUR\n"
        "O3,C001,CLOSED,USD\n"
        "O4,C003,OPEN,GBP\n"
        "O5,C004,OPEN,USD\n",
        encoding="utf-8",
    )
    manifest_yaml = sources_dir / "sources.yaml"
    manifest_yaml.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {"kind": "csv", "uri": str(erp_customers)},
                    {"kind": "csv", "uri": str(erp_orders)},
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    sqlite_spec = f"sqlite:{sqlite_path}"
    if os.name == "nt":
        assert ":" in str(sqlite_path)
    bundle_out = tmp_path / "profile_bundle.json"

    rc, _, prof_env = _invoke_cli(
        [
            "--json",
            "model",
            "profile",
            "--source",
            sqlite_spec,
            "--sources-file",
            str(manifest_yaml),
            "--sample-rows",
            "5",
            "--max-questions",
            "2",
            "--questions-offset",
            "0",
            "--out",
            str(bundle_out),
        ]
    )
    assert rc == 0 and prof_env is not None and prof_env["ok"] is True
    assert bundle_out.is_file()
    bundle = ProfileBundle.model_validate_json(bundle_out.read_text(encoding="utf-8"))
    assert len(bundle.profiles) == 3
    assert bundle.pk_containment_overlaps

    email_rows_checked = 0
    for rows in bundle.source_rows_by_table.values():
        for row in rows:
            if "email" in row and row["email"] is not None:
                email_rows_checked += 1
                assert PII_TOKEN_RE.fullmatch(str(row["email"]))
    assert email_rows_checked > 0

    assert bundle.total_candidates >= 3
    assert bundle.returned_count == 2
    assert len(bundle.questions) == 2
    assert bundle.offset == 0
    assert bundle.remaining_count == bundle.total_candidates - 2

    rc, _, page2_env = _invoke_cli(
        [
            "--json",
            "model",
            "profile",
            "--source",
            sqlite_spec,
            "--sources-file",
            str(manifest_yaml),
            "--sample-rows",
            "5",
            "--max-questions",
            "0",
            "--questions-offset",
            "2",
        ]
    )
    assert rc == 0 and page2_env is not None and page2_env["ok"] is True
    page2 = ProfileBundle.model_validate(page2_env["data"])
    assert page2.offset == 2
    assert page2.returned_count == page2.total_candidates - 2
    assert len(page2.questions) == page2.total_candidates - 2
    assert page2.remaining_count == 0
    page1_qids = {q.question_id for q in bundle.questions}
    page2_qids = {q.question_id for q in page2.questions}
    assert page1_qids.isdisjoint(page2_qids)
    assert not (work_dir / ".opendv").exists()
    assert not (tmp_path / ".opendv").exists()


def test_stateless_model_verify_exit_codes_and_inv05(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stateless `model verify` validates drafts, prints full JSON + exit 5 on fail/INV-05."""
    work_dir = tmp_path / "verify_ws"
    _isolate_env(tmp_path, monkeypatch, work_dir)

    csv_path = work_dir / "accounts.csv"
    csv_path.write_text(
        "account_id,account_name,status,email\n"
        "ACC1,Alpha,OPEN,alpha@example.com\n"
        "ACC2,Beta,CLOSED,beta@example.com\n",
        encoding="utf-8",
    )
    bundle = profile_sources_stateless([f"csv:{csv_path}"], proving_sample_rows=5)
    profile_path = tmp_path / "bundle.json"
    profile_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    sid = bundle.profiles[0].source_id
    tid = bundle.profiles[0].tables[0].table_id

    req_path = tmp_path / "verify_req.json"
    req_path.write_text(json.dumps({"policy": "balanced"}), encoding="utf-8")

    valid_draft = _build_valid_account_draft("p_verify", sid, tid, include_email_pii=True)
    valid_draft_path = tmp_path / "valid_draft.json"
    valid_draft_path.write_text(valid_draft.model_dump_json(indent=2), encoding="utf-8")

    rc, _, ok_env = _invoke_cli(
        [
            "--json",
            "model",
            "verify",
            "--input",
            str(req_path),
            "--draft-ir",
            str(valid_draft_path),
            "--profile",
            str(profile_path),
        ]
    )
    assert rc == int(CliExitCode.OK)
    assert ok_env is not None and ok_env["ok"] is True
    assert ok_env["data"]["status"] == "CERTIFIED_FAST"
    assert ok_env["data"]["next_action"] == "READY_FOR_DOWNSTREAM"

    missing_triage_draft = _build_valid_account_draft(
        "p_verify", sid, tid, include_email_pii=True, missing_triage=True
    )
    missing_triage_path = tmp_path / "missing_triage.json"
    missing_triage_path.write_text(
        missing_triage_draft.model_dump_json(indent=2), encoding="utf-8"
    )
    rc, stdout_fail, fail_env = _invoke_cli(
        [
            "--json",
            "model",
            "verify",
            "--input",
            str(req_path),
            "--draft-ir",
            str(missing_triage_path),
            "--profile",
            str(profile_path),
        ]
    )
    assert rc == int(CliExitCode.HOLD_OR_UNCERTIFIED)
    assert stdout_fail.strip().startswith("{")
    assert fail_env is not None and fail_env["ok"] is False
    assert fail_env["data"]["status"] == "UNCERTIFIED_DRAFT"
    assert fail_env["data"]["next_action"] == "REPAIR_DRAFT_IR"

    surr_csv = work_dir / "accounts_surrogate.csv"
    surr_csv.write_text(
        "row_id,account_id,account_name,status,email\n"
        "1,ACC1,Alpha,OPEN,alpha@example.com\n"
        "2,ACC2,Beta,CLOSED,beta@example.com\n",
        encoding="utf-8",
    )
    surr_bundle = profile_sources_stateless([f"csv:{surr_csv}"], proving_sample_rows=5)
    surr_profile_path = tmp_path / "surr_bundle.json"
    surr_profile_path.write_text(surr_bundle.model_dump_json(indent=2), encoding="utf-8")
    surr_sid = surr_bundle.profiles[0].source_id
    surr_tid = surr_bundle.profiles[0].tables[0].table_id
    surr_draft = _build_valid_account_draft(
        "p_verify", surr_sid, surr_tid, include_email_pii=True, use_surrogate_bk=True
    )
    surr_draft_path = tmp_path / "surr_draft.json"
    surr_draft_path.write_text(surr_draft.model_dump_json(indent=2), encoding="utf-8")
    rc, _, surr_env = _invoke_cli(
        [
            "--json",
            "model",
            "verify",
            "--input",
            str(req_path),
            "--draft-ir",
            str(surr_draft_path),
            "--profile",
            str(surr_profile_path),
        ]
    )
    assert rc == int(CliExitCode.HOLD_OR_UNCERTIFIED)
    assert surr_env is not None and surr_env["ok"] is False
    assert surr_env["data"]["status"] == "UNCERTIFIED_DRAFT"
    assert surr_env["data"]["next_action"] == "WAIVE_OR_ADJUST_PREFERENCES"
    assert any(
        d["rule_id"] == "DV-AP-003"
        for d in surr_env["data"]["verifier_report"]["v6b_diagnostics"]
    )

    inv05_raw = valid_draft.model_dump(mode="json")
    inv05_raw["hubs"][0]["attributes"] = [
        {
            "name": "account_name",
            "logical_type": "UTF8_STRING",
            "nullable": False,
            "is_pii": False,
        }
    ]
    inv05_path = tmp_path / "inv05_draft.json"
    inv05_path.write_text(json.dumps(inv05_raw, indent=2), encoding="utf-8")
    rc, _, inv05_env = _invoke_cli(
        [
            "--json",
            "model",
            "verify",
            "--input",
            str(req_path),
            "--draft-ir",
            str(inv05_path),
            "--profile",
            str(profile_path),
        ]
    )
    assert rc == int(CliExitCode.HOLD_OR_UNCERTIFIED)
    assert inv05_env is not None and inv05_env["ok"] is False
    assert inv05_env["data"]["status"] == "UNCERTIFIED_DRAFT"
    assert inv05_env["data"]["next_action"] == "REPAIR_DRAFT_IR"
    v1_rules = {
        d["rule_id"] for d in inv05_env["data"]["verifier_report"]["v1_v6_diagnostics"]
    }
    assert "INV-05" in v1_rules

    def _forbid_prover(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("DuckDbProverAdapter must not be instantiated under --dry-run")

    monkeypatch.setattr(
        "opendv.service.workflows.modeling_stateless.DuckDbProverAdapter",
        _forbid_prover,
    )
    rc, _, dry_env = _invoke_cli(
        [
            "--json",
            "model",
            "verify",
            "--input",
            str(req_path),
            "--draft-ir",
            str(valid_draft_path),
            "--profile",
            str(profile_path),
            "--dry-run",
        ]
    )
    assert rc == int(CliExitCode.OK)
    assert dry_env is not None and dry_env["ok"] is True
    assert dry_env["data"]["dry_run"] is True
    assert dry_env["data"]["valid"] is True


def test_stateless_model_run_dry_run_guide_security_and_hold_exit5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stateless `model run` enforces --guide containment, override trust, and exit 5/0."""
    work_dir = tmp_path / "run_ws"
    _isolate_env(tmp_path, monkeypatch, work_dir)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(work_dir))

    csv_path = work_dir / "accounts.csv"
    csv_path.write_text(
        "account_id,account_name,status\nACC1,Alpha,OPEN\nACC2,Beta,CLOSED\n",
        encoding="utf-8",
    )
    bundle = profile_sources_stateless([f"csv:{csv_path}"], proving_sample_rows=5)
    profile_path = tmp_path / "bundle.json"
    profile_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    sid = bundle.profiles[0].source_id
    tid = bundle.profiles[0].tables[0].table_id
    req_path = tmp_path / "model_req.json"
    req_path.write_text(
        json.dumps(
            {
                "project_id": "p_run",
                "policy": "balanced",
                "ontology": build_account_ontology().model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    evil_guide = outside_dir / "evil_guide.md"
    evil_guide.write_text("# Untrusted external guide\n", encoding="utf-8")

    rc, _, err_env = _invoke_cli(
        [
            "--json",
            "model",
            "run",
            "--input",
            str(req_path),
            "--profile",
            str(profile_path),
            "--guide",
            str(evil_guide),
            "--dry-run",
        ]
    )
    assert rc == int(CliExitCode.VALIDATION_ERROR)
    assert err_env is not None and err_env["ok"] is False
    assert err_env["error_code"] == ErrorCode.SYNTAX_INVALID_ARGUMENT.value

    trusted_guide = work_dir / "team_guide.md"
    guide_body = "# Trusted Team Guide\nAlways separate PII into sat_*_pii.\n"
    trusted_guide.write_text(guide_body, encoding="utf-8")
    expected_guide_sha = hashlib.sha256(guide_body.encode("utf-8")).hexdigest()

    harness_calls = 0

    def _no_llm_harness(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal harness_calls
        harness_calls += 1
        raise OpenDVError(
            "LLM harness must not be resolved during --dry-run",
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        )

    monkeypatch.setattr(
        "opendv.service.workflows.modeling_stateless.resolve_harness",
        _no_llm_harness,
    )
    rc, _, dry_env = _invoke_cli(
        [
            "--json",
            "model",
            "run",
            "--input",
            str(req_path),
            "--profile",
            str(profile_path),
            "--guide",
            str(trusted_guide),
            "--dry-run",
        ]
    )
    assert rc == int(CliExitCode.OK)
    assert dry_env is not None and dry_env["ok"] is True
    assert dry_env["data"]["dry_run"] is True
    assert dry_env["data"]["valid"] is True
    assert dry_env["data"]["guide_sha256"] == expected_guide_sha
    assert harness_calls == 0

    untrusted_override = work_dir / ".opendv" / "agents" / "dv2-modeling-guide.md"
    untrusted_override.parent.mkdir(parents=True, exist_ok=True)
    untrusted_override.write_text("# Tampered override\n", encoding="utf-8")
    rc, _, untrusted_env = _invoke_cli(
        [
            "--json",
            "model",
            "run",
            "--input",
            str(req_path),
            "--profile",
            str(profile_path),
            "--dry-run",
        ]
    )
    assert rc in (int(CliExitCode.VALIDATION_ERROR), int(CliExitCode.NOT_FOUND))
    assert untrusted_env is not None and untrusted_env["ok"] is False
    assert untrusted_env["error_code"] == ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE.value

    clean_ws = tmp_path / "clean_run_ws"
    _isolate_env(tmp_path, monkeypatch, clean_ws)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(clean_ws))

    hold_harness = _DeterministicModelHarness(
        project_id="p_run", source_id=sid, table_id=tid, hold_once=True
    )
    monkeypatch.setattr(
        "opendv.service.workflows.modeling_stateless.resolve_harness",
        lambda *_a, **_kw: hold_harness,
    )
    rc, _, hold_env = _invoke_cli(
        [
            "--json",
            "model",
            "run",
            "--input",
            str(req_path),
            "--profile",
            str(profile_path),
        ]
    )
    assert rc == int(CliExitCode.HOLD_OR_UNCERTIFIED)
    assert hold_env is not None and hold_env["ok"] is False
    assert hold_env["error_code"] == ErrorCode.HOLD_CHECKPOINTED.value
    assert hold_env["data"]["status"] == "HOLD_CHECKPOINTED"

    cert_harness = _DeterministicModelHarness(
        project_id="p_run", source_id=sid, table_id=tid, hold_once=False
    )
    monkeypatch.setattr(
        "opendv.service.workflows.modeling_stateless.resolve_harness",
        lambda *_a, **_kw: cert_harness,
    )
    rc, _, cert_env = _invoke_cli(
        [
            "--json",
            "model",
            "run",
            "--input",
            str(req_path),
            "--profile",
            str(profile_path),
        ]
    )
    assert rc == int(CliExitCode.OK)
    assert cert_env is not None and cert_env["ok"] is True
    assert cert_env["data"]["status"] == "CERTIFIED"
    assert cert_env["data"]["next_action"] == "READY_FOR_DOWNSTREAM"


def test_settings_project_set_and_vault_status_local_and_http_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `settings project-set`, `vault status`, and pre-cert `vault waive` parity."""
    ws = tmp_path / "gov_ws"
    state_dir = _isolate_env(tmp_path, monkeypatch, ws)
    csv_path = ws / "accounts.csv"
    csv_path.write_text(
        "account_id,account_name,status\nACC1,Alpha,OPEN\nACC2,Beta,CLOSED\n",
        encoding="utf-8",
    )

    harness = _DeterministicModelHarness(
        project_id="proj_gov",
        source_id="src_tmp",
        table_id="tbl_tmp",
        hold_once=True,
    )
    svc = OpenDVService(ws, state_dir=state_dir, harness=harness)
    svc.init_project(
        ProjectInitReq(
            project_id="proj_gov",
            domain_namespace="ibor",
            baseline_content=build_account_ontology_yaml(),
            baseline_format="yaml",
        )
    )
    src = svc.attach_source(
        "proj_gov",
        SourceAttachReq(kind="csv", uri_or_filename=str(csv_path)),
    )
    tid = svc.list_tables_index("proj_gov")[0].table_id
    harness.source_id = src.source_id
    harness.table_id = tid

    rc, _, waive_env = _invoke_cli(
        [
            "--json",
            "vault",
            "waive",
            "--project",
            "proj_gov",
            "--rule",
            "DV-AP-009",
            "--entity",
            "sat_bad",
            "--rationale",
            "Accepted legacy PII migration exception for test",
        ]
    )
    assert rc == int(CliExitCode.OK)
    assert waive_env is not None and waive_env["ok"] is True
    assert waive_env["data"]["rule_id"] == "DV-AP-009"
    assert waive_env["data"]["entity_name"] == "sat_bad"
    assert str(waive_env["data"]["waiver_id"]).startswith("WAIV-")

    rc, _, local_pre_status = _invoke_cli(
        ["--json", "vault", "status", "--project", "proj_gov"]
    )
    assert rc == int(CliExitCode.OK) and local_pre_status is not None
    assert [w["rule_id"] for w in local_pre_status["data"]["waived_rules"]] == ["DV-AP-009"]

    project_set_args = [
        "settings",
        "project-set",
        "--project",
        "proj_gov",
        "--pref",
        "multi_source_hub_strategy=companion_hub_with_sal",
        "--pref",
        "disabled_v6b_blocks=DV-AP-001",
        "--proving-sample-rows",
        "50",
        "--real-row-proving",
        "--hints-text",
        "Customer CRM takes priority.",
    ]

    with run_loopback_api_server(svc) as api_url:
        rc, _, local_set_env = _invoke_cli(["--json", *project_set_args])
        assert rc == int(CliExitCode.OK)
        assert local_set_env is not None and local_set_env["ok"] is True

        svc.update_project_settings(
            "proj_gov",
            ProjectSettingsUpdateReq(
                modeling_preferences=ModelingPreferences(),
                proving_sample_rows=200,
                real_row_proving=False,
                business_context_hints=None,
            ),
        )

        rc, _, http_set_env = _invoke_cli(["--api-url", api_url, "--json", *project_set_args])
        assert rc == int(CliExitCode.OK)
        assert http_set_env is not None and http_set_env["ok"] is True

        for env_payload in (local_set_env, http_set_env):
            snap = env_payload["data"]["settings_snapshot"]
            assert (
                snap["modeling_preferences"]["multi_source_hub_strategy"]
                == "companion_hub_with_sal"
            )
            assert snap["modeling_preferences"]["disabled_v6b_blocks"] == ["DV-AP-001"]
            assert snap["proving_sample_rows"] == 50
            assert snap["real_row_proving"] is True
            assert snap["business_context_hints"] == "Customer CRM takes priority."

        run_hold = asyncio.run(svc.build_vault("proj_gov", VaultBuildReq(policy="balanced")))
        assert run_hold.status == "HOLD_CHECKPOINTED"

        rc, _, local_holds_env = _invoke_cli(
            ["--json", "vault", "status", "--project", "proj_gov", "--holds-only"]
        )
        rc_http, _, http_holds_env = _invoke_cli(
            [
                "--api-url",
                api_url,
                "--json",
                "vault",
                "status",
                "--project",
                "proj_gov",
                "--holds-only",
            ]
        )
        assert rc == int(CliExitCode.OK) and rc_http == int(CliExitCode.OK)
        assert local_holds_env is not None and http_holds_env is not None
        assert local_holds_env["data"] == http_holds_env["data"]
        assert local_holds_env["data"]["status"] == "HOLD_CHECKPOINTED"
        assert local_holds_env["data"]["next_action"] == "ASK_HUMAN_HOLD"
        assert len(local_holds_env["data"]["open_holds"]) == 1
        assert local_holds_env["data"]["open_holds"][0]["question_id"] == "q1"
        assert local_holds_env["data"]["waived_rules"] == []
