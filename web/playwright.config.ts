import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const e2eTmp =
	process.env.OPENDV_E2E_TMP ?? mkdtempSync(join(tmpdir(), "opendv_e2e_"));
process.env.OPENDV_E2E_TMP = e2eTmp;
const wsDir = join(e2eTmp, "ws");
const stateDir = join(e2eTmp, "state");
const homeDir = join(e2eTmp, "opendv_home");
const publicAmDir = resolve(repoRoot, "data", "public_am");
const serverScriptPath = join(e2eTmp, "run_mock_e2e_server.py");

mkdirSync(wsDir, { recursive: true });
mkdirSync(stateDir, { recursive: true });
mkdirSync(homeDir, { recursive: true });

const workspaceRoots = [publicAmDir, wsDir, repoRoot].join(delimiter);

const serverPy = `import asyncio, os, pathlib, re, shutil, sys, uvicorn
sys.path.insert(0, os.getcwd())
from opendv.api import create_app
from opendv.models import WorkspaceSettings
from opendv.models.diagnostics import ClarificationQuestion, HorizonOptionSpec, HorizonQuestion
from opendv.models.ontology import ConceptAttribute, ConceptTableBinding, DocumentCitationRef, ExternalMapping, ExternalMcpEvidenceRef
from opendv.models.ontology_deltas import AddConcept
from opendv.models.project import OntologyProposalSpec
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir import BusinessKeyPart, ColumnTriageEntry, DecimalType, DraftField, DraftHubSpec, DraftSatelliteSpec, DraftSourceBinding, DraftVaultIR
from opendv.models.wire_dtos_discovery import FiboQueryReq, ScoutDigest
from opendv.models.wire_dtos_vault import HorizonCriticVerdict, SemanticCriticVerdict
from opendv.service.app_service import OpenDVService
from opendv.service.external_mcp import ExternalMcpGateway
from opendv.sources.fibo.loader import CANONICAL_FIBO_CORE_SHA256
from tests.api._api_test_helpers import build_wave1_am_draft, build_wave1_commit_ops, build_wave2_am_draft, build_wave2_commit_ops, load_project_profiles
from tests.helpers.fake_external_mcp import LoopbackFakeExternalMcpServer
from tests.helpers.mock_harness import DeterministicMockHarness, MockTurnScript

tmp = pathlib.Path(os.environ["OPENDV_E2E_TMP"])
ws = tmp / "ws"
st = tmp / "state"
repo_am = pathlib.Path(os.getcwd()) / "data" / "public_am"
ws_am = ws / "public_am"
ws_am.mkdir(parents=True, exist_ok=True)
for item in repo_am.iterdir():
    if item.is_file():
        dst = ws_am / item.name
        if not dst.exists():
            shutil.copy2(item, dst)

seed_dir = ws / "seeds"
seed_dir.mkdir(parents=True, exist_ok=True)
(seed_dir / "am_seed.yaml").write_text(
    "ontology_version: '0.1.0'\\n"
    "domain_namespace: asset_management\\n"
    "concepts:\\n"
    "  - concept_uri: urn:opendv:asset_management:SeedPortfolio\\n"
    "    concept_name: SeedPortfolio\\n"
    "    description: Seeded asset management portfolio concept.\\n"
    "    key_authority_tier: TIER_2_ENTERPRISE_SOR\\n",
    encoding="utf-8",
)

svc_ref: list[OpenDVService] = []
w2_horizon_asked: set[str] = set()


def _resolve_pid(prompt: str) -> str:
    svc = svc_ref[0]
    m = re.search(r"Project:\\s*([a-z0-9][a-z0-9_-]*)", prompt, re.IGNORECASE)
    if m:
        return m.group(1)
    for summary in svc.list_projects():
        if svc.get_project(summary.project_id).has_active_lease:
            return summary.project_id
    projs = svc.list_projects()
    return projs[0].project_id


def _build_generic_draft(pid: str, ont_ver: str, profiles: list[SourceProfile], wave: int) -> DraftVaultIR:
    hubs, sats, triage = [], [], []
    for prof in profiles:
        for tbl in prof.tables:
            if not tbl.columns:
                continue
            bk = tbl.columns[0]
            h_name = f"hub_{re.sub(r'[^a-z0-9]+', '_', tbl.table_name.lower()).strip('_')}"[:48]
            hubs.append(
                DraftHubSpec(
                    hub_name=h_name,
                    key_tier="TIER_1_GLOBAL_STANDARD",
                    business_keys=[
                        BusinessKeyPart(
                            table_id=tbl.table_id,
                            col_name=bk.col_name,
                            source_id=prof.source_id,
                            key_tier="TIER_1_GLOBAL_STANDARD",
                            bkcc_value="GLOBAL",
                        )
                    ],
                    source_bindings=[
                        DraftSourceBinding(
                            source_id=prof.source_id,
                            table_id=tbl.table_id,
                            col_mappings={bk.col_name: bk.col_name},
                        )
                    ],
                )
            )
            triage.append(ColumnTriageEntry(table_id=tbl.table_id, col_name=bk.col_name, disposition="MODELED", target_ref=h_name))
            others = tbl.columns[1:]
            if others:
                s_name = f"sat_{h_name[4:]}"[:48]
                sats.append(
                    DraftSatelliteSpec(
                        sat_name=s_name,
                        sat_kind="STANDARD",
                        parent_name=h_name,
                        source_bindings=[
                            DraftSourceBinding(
                                source_id=prof.source_id,
                                table_id=tbl.table_id,
                                col_mappings={c.col_name: c.col_name for c in others},
                            )
                        ],
                        attributes=[
                            DraftField(
                                name=c.col_name,
                                logical_type=DecimalType(precision=18, scale=4) if any(k in c.sql_type.upper() for k in ("DEC", "NUM", "FLOAT", "DOUBLE", "REAL")) else ("INT64" if "INT" in c.sql_type.upper() else "UTF8_STRING"),
                                nullable=c.nullable,
                                is_pii=bool(c.pii_tags),
                            )
                            for c in others
                        ],
                    )
                )
                for c in others:
                    triage.append(ColumnTriageEntry(table_id=tbl.table_id, col_name=c.col_name, disposition="MODELED", target_ref=s_name))
    return DraftVaultIR(
        project_id=pid,
        ontology_version=ont_ver,
        wave_number=wave,
        hubs=hubs,
        links=[],
        satellites=sats,
        column_triage=triage,
    )


class _UatMockHarness(DeterministicMockHarness):
    async def run_turn(self, request, *, event_sink=None, scout_dispatcher=None):
        svc = svc_ref[0]
        pid = _resolve_pid(request.prompt)
        if request.agent_id == "ontology-first-mate":
            if "pause-probe" in request.prompt.lower() and request.checkpoint is None:
                await asyncio.sleep(1.2)
                return await super().run_turn(request, event_sink=event_sink, scout_dispatcher=scout_dispatcher)
            if request.checkpoint is None and not any("wave2_eu" in s.uri_or_filename for s in svc.list_sources(pid)):
                gw = ExternalMcpGateway(
                    state_store=svc.state_manager,
                    secret_store=svc.secret_store,
                    settings_store=svc.settings_store,
                )
                try:
                    await gw.invoke_tool(
                        project_id=pid,
                        run_id=request.run_id,
                        server_id="ext_doc",
                        tool_name="lookup_entity",
                        arguments={"symbol": "Filer"},
                    )
                except Exception:
                    pass
                tbls = svc.list_tables_index(pid)
                if scout_dispatcher is not None and tbls:
                    await scout_dispatcher.dispatch_scout(
                        "table_cluster",
                        [t.table_id for t in tbls[:2]],
                        run_id=request.run_id,
                    )
                script = MockTurnScript(
                    structured_output={
                        "proposed_ops": [],
                        "clarification_questions": [
                            ClarificationQuestion(
                                question_id="q-filer-bk",
                                concept_name="Institutional Filer",
                                title="Confirm SEC filer identifier",
                                prompt="Confirm that filer_cik uniquely identifies an Institutional Filer across custody feeds.",
                                sample_rows=[
                                    "filer_cik | filer_name",
                                    "0001166559 | Alpha Stewardship Management",
                                ],
                                impact_if_locked="Fixes the primary business identifier for Institutional Filer.",
                                recommended_answer="Confirm SEC filing identifier",
                            ).model_dump(mode="json")
                        ],
                        "summary": "Waiting for Steward confirmation of Institutional Filer key.",
                    }
                )
                return await self._finish_script(request, script, event_sink, scout_dispatcher)
        if request.agent_id == "ship-vault" and pid.endswith("-ctrl") and request.checkpoint is None:
            await asyncio.sleep(1.2)
        return await super().run_turn(request, event_sink=event_sink, scout_dispatcher=scout_dispatcher)

    async def _finish_script(self, request, script, event_sink, scout_dispatcher):
        return await super(DeterministicMockHarness, self).run_turn(request, event_sink=event_sink, scout_dispatcher=scout_dispatcher) if False else await DeterministicMockHarness(
            selector=lambda _r: script
        ).run_turn(request, event_sink=event_sink, scout_dispatcher=scout_dispatcher)


def _selector(req):
    svc = svc_ref[0]
    pid = _resolve_pid(req.prompt)
    if req.agent_id in ("table-cluster-scout", "page-range-scout"):
        m_t = re.search(r"target '([^']+)'", req.prompt)
        tid = m_t.group(1) if m_t else "unknown"
        sk = "table_cluster" if req.agent_id == "table-cluster-scout" else "page_range"
        return MockTurnScript(
            structured_output=ScoutDigest(
                scout_kind=sk,
                target_id=tid,
                candidate_concepts=["Institutional Filer"],
                key_columns=["filer_cik"],
            )
        )
    if req.agent_id == "ontology-first-mate":
        if "pause-probe" in req.prompt.lower():
            return MockTurnScript(
                structured_output={
                    "proposed_ops": [],
                    "clarification_questions": [],
                    "summary": "Resumed after pause checkpoint.",
                }
            )
        srcs = svc.list_sources(pid)
        if any("wave2_eu" in s.uri_or_filename for s in srcs):
            w2_ops = [op.model_dump(mode="json") for op in build_wave2_commit_ops([s.model_dump(mode="json") for s in srcs])]
            return MockTurnScript(
                structured_output={
                    "proposed_ops": w2_ops,
                    "clarification_questions": [],
                    "summary": "Staged Wave 2 incremental custody, NAV, OTC, and prime allocation concepts.",
                }
            )
        sqlite_src = next((s for s in srcs if s.kind == "sqlite"), None)
        pq_src = next((s for s in srcs if s.kind == "parquet"), None)
        md_src = next((s for s in srcs if "am_stewardship_domain_notes" in s.uri_or_filename), None) or next((s for s in srcs if s.kind in ("markdown", "domain_note")), None)
        q_line = ""
        if md_src:
            pgs = svc.get_document_pages(pid, md_src.source_id, page_start=1, page_end=1)
            if pgs and pgs[0].masked_text.strip():
                q_line = pgs[0].masked_text.splitlines()[0].strip()
        fibo_hits = svc.fibo_qa(FiboQueryReq(query="Financial Security", top_k=1)).matches
        fibo_uri = fibo_hits[0].concept_uri if fibo_hits else "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/Agents/AutonomousAgent"
        rcpts = svc.list_external_mcp_receipts(pid)
        mcp_refs = [
            ExternalMcpEvidenceRef(
                receipt_id=rcpts[0].receipt_id,
                server_id=rcpts[0].server_id,
                tool_name=rcpts[0].tool_name,
                content_sha256=rcpts[0].content_sha256,
                masked_excerpt=rcpts[0].masked_excerpt,
            ).model_dump(mode="json")
        ] if rcpts else []
        ops = []
        if sqlite_src and pq_src and md_src and q_line:
            ops = build_wave1_commit_ops(sqlite_src.source_id, pq_src.source_id, md_src.source_id, q_line)
            ops[0]["external_mappings"] = [
                ExternalMapping(standard_uri=fibo_uri, mapping_type="skos:exactMatch", confidence=0.92, reference_pack_id="fibo-core", reference_pack_sha256=CANONICAL_FIBO_CORE_SHA256).model_dump(mode="json")
            ]
            ops[0]["mcp_evidence"] = mcp_refs
        ops.append(
            AddConcept(
                concept_uri="urn:asset_management:UnverifiedCustodySignal",
                concept_name="Unverified Custody Signal",
                description="Candidate awaiting physical binding or steward waiver.",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
            ).model_dump(mode="json")
        )
        return MockTurnScript(
            structured_output={
                "proposed_ops": ops,
                "clarification_questions": [],
                "summary": "Staged PATH_A1, PATH_B1, and PATH_B2 discovery candidates.",
            }
        )
    if req.agent_id == "ship-vault":
        profs = [p for p in load_project_profiles(svc, pid) if p.tables]
        bundle = svc.get_ontology_bundle(pid)
        tnames = {t.table_name for p in profs for t in p.tables}
        proj = svc.get_project(pid)
        tool_evs = []
        if {"wave2_eu_custody_lei_and_drift", "wave2_bitemporal_nav_restatements", "wave2_ambiguous_otc_collateral", "prime_allocations_w2"}.issubset(tnames):
            draft = build_wave2_am_draft(pid, bundle.ontology.ontology_version, profs)
            tool_evs = [
                {
                    "ontology_proposals": [
                        OntologyProposalSpec(
                            proposal_id="PROP-custody-9a2f4c10",
                            summary="Add Custodian entity aligned with Wave 2 EU LEI custody feeds",
                            wave_number=2,
                            status="PENDING_STEWARD",
                            delta_ops=[
                                AddConcept(
                                    concept_uri="urn:asset_management:Custodian",
                                    concept_name="Custodian",
                                    description="Global sub-custodian safekeeping legal entity.",
                                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                                    rationale="Proposed during Wave 2 vault synthesis",
                                )
                            ],
                        ).model_dump(mode="json")
                    ]
                }
            ]
        elif {"holdings_13f", "am_esg_stewardship_proxy"}.issubset(tnames):
            draft = build_wave1_am_draft(pid, bundle.ontology.ontology_version, profs)
        else:
            wave_num = 2 if proj.active_wave_id else 1
            draft = _build_generic_draft(pid, bundle.ontology.ontology_version, profs, wave_num)
        return MockTurnScript(structured_output=draft, tool_events=tool_evs)
    if req.agent_id == "dv-semantic-critic":
        return MockTurnScript(
            structured_output=SemanticCriticVerdict(
                score=95,
                findings=["Clean DV 2.0 structure with PII isolation."],
                accepted_waiver_ids=["WAIV-AP004", "WAIV-AP004-NAV"],
            )
        )
    if req.agent_id == "dv-horizon-critic":
        has_w2 = any("wave2_eu" in s.uri_or_filename for s in svc.list_sources(pid))
        if has_w2 and pid not in w2_horizon_asked:
            w2_horizon_asked.add(pid)
            return MockTurnScript(
                structured_output=HorizonCriticVerdict(
                    score=76,
                    classification="HUMAN_REVIEW_REQUIRED",
                    absorbed_natively=["Nullable CUSIP in securities history"],
                    bucket_b_questions=[
                        HorizonQuestion(
                            question_id="hq-w2-otc",
                            brk_code="BRK-07",
                            title="Confirm OTC Collateral Key Scope",
                            prompt="Should OTC collateral identifiers stay scoped per prime broker feed or unify globally?",
                            options=[
                                HorizonOptionSpec(
                                    option_id="opt-scoped",
                                    label="Keep source-scoped",
                                    description="Keep identifiers separated by source feed.",
                                    implications="Prevents collisions when external feeds reuse IDs.",
                                    operator_hint="FLEXIBLE_SOURCE_SCOPED",
                                ),
                                HorizonOptionSpec(
                                    option_id="opt-global",
                                    label="Unify globally",
                                    description="Merge matching codes across all feeds.",
                                    implications="Locks in global key structure permanently.",
                                    operator_hint="LOCK_GLOBAL",
                                ),
                            ],
                            impact_summary="Protects future Wave 3 broker feeds from key collisions.",
                        )
                    ],
                    bucket_c_watchlist=["Monitor custodian fee column additions"],
                )
            )
        return MockTurnScript(
            structured_output=HorizonCriticVerdict(
                score=92,
                classification="REASONABLY_STABLE",
                absorbed_natively=["Nullable CUSIP in securities history"],
                bucket_c_watchlist=["Monitor custodian fee column additions"],
            )
        )
    return MockTurnScript(text_output="{}")


mcp = LoopbackFakeExternalMcpServer(server_id="ext_doc")
mcp.start()
try:
    svc = OpenDVService(ws, state_dir=st, harness=_UatMockHarness(selector=_selector))
    svc_ref.append(svc)
    svc.update_settings(WorkspaceSettings(secret_backend="user_state_file", harness_provider="agy"))
    svc.upsert_mcp_server(mcp.server_spec(allow_scout_calls=True, allow_document_pages=True))
    uvicorn.run(create_app(svc), host="127.0.0.1", port=8420, log_level="warning")
finally:
    mcp.stop()
`;

writeFileSync(serverScriptPath, serverPy, "utf-8");

export default defineConfig({
	testDir: "./e2e",
	testMatch: "**/*.pw.ts",
	workers: 1,
	fullyParallel: false,
	timeout: 120_000,
	use: {
		baseURL: "http://127.0.0.1:8420",
		trace: "retain-on-failure",
		launchOptions: {
			executablePath:
				process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
		},
	},
	projects: [
		{
			name: "chromium",
			use: { ...devices["Desktop Chrome"] },
		},
	],
	webServer: {
		command: `uv run --no-sync python ${JSON.stringify(serverScriptPath)}`,
		cwd: "..",
		url: "http://127.0.0.1:8420/api/v1/health",
		reuseExistingServer: false,
		timeout: 60_000,
		env: {
			...process.env,
			PYTHON_KEYRING_BACKEND: "keyring.backends.null.Keyring",
			OPENDV_E2E_TMP: e2eTmp,
			OPENDV_HOME: homeDir,
			XDG_STATE_HOME: stateDir,
			LOCALAPPDATA: stateDir,
			OPENDV_WORKSPACE_ROOTS: workspaceRoots,
		},
	},
});
