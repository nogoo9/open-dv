"""Tests for RFC 8785 JCS, 34 ErrorCodes, CegarPolicy, ProjectState, 36-op OntologyDelta, and Wave IR."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from opendv.models import (
    AGENT_ADDITIVE_OPS,
    CEGAR_POLICY_SPECS,
    ERROR_MAPPINGS,
    HUMAN_ONLY_OPS,
    SERVER_ONLY_OPS,
    STEP4_DEFAULT_BUDGET,
    VALID_EXIT_CODES,
    AddAttribute,
    AddConcept,
    AddEnumeration,
    AddEnumerationCode,
    AddExternalMapping,
    AddKeyAuthorityPattern,
    AddMcpEvidence,
    AddRelationship,
    AddSourceCitation,
    AddSynonym,
    AddTableBinding,
    BronzeTableSpec,
    BusinessKeyPart,
    CanonicalBusinessConcept,
    CanonicalizationError,
    CertifiedHubSpec,
    CertifiedLinkSpec,
    CertifiedSatelliteSpec,
    CertifiedVaultIR,
    ChangeDomainNamespace,
    ClearParentConcept,
    CliEnvelope,
    CliExitCode,
    ColumnTriageEntry,
    ConceptAttribute,
    ConceptTableBinding,
    DecimalType,
    DeleteConcept,
    DeleteEnumeration,
    DeleteRelationship,
    DocumentCitationRef,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftSourceBinding,
    DraftVaultIR,
    EmitterDefectReport,
    ErrorCode,
    EvidenceGroundingApproval,
    EvolutionLockSpec,
    ExternalMapping,
    ExternalMcpEvidenceRef,
    FloatDomainError,
    GraphAOntology,
    HashRecipeSpec,
    IntegerDomainError,
    IRField,
    KeyAuthorityPattern,
    LinkParticipantSpec,
    LoadSemanticsSpec,
    MartViewSpec,
    OntologyCommitReq,
    OntologyEnumeration,
    OntologyRelationship,
    OntologyValidateReq,
    OpenDVError,
    PhysicalLayoutHints,
    PitBridgeSpec,
    PredicateLeaf,
    ProblemDetails,
    ProjectInitReq,
    ProjectSettingsSnapshot,
    ProjectState,
    ReferenceOntologyCorruptError,
    RefTableSpec,
    RemoveAttribute,
    RemoveEnumerationCode,
    RemoveExternalMapping,
    RemoveKeyAuthorityPattern,
    RemoveMcpEvidence,
    RemoveSourceCitation,
    RemoveSynonym,
    RemoveTableBinding,
    RenameConcept,
    ReparentConcept,
    RepinExternalMapping,
    RescoreExternalMapping,
    ReviseDescription,
    RunControlReq,
    RunStateResp,
    SchemaWaveDelta,
    SeedRecordsSpec,
    SetConceptGovernanceTags,
    SetConceptKeyAuthority,
    SetDescription,
    SetParentConcept,
    SourceBindingSpec,
    StagingViewSpec,
    UpdateAttributeMetadata,
    UpdateEnumerationMetadata,
    UpdateRelationshipMetadata,
    WaiverSubmitReq,
    WorkspaceSettings,
    compute_cegar_energy,
    create_problem_details,
    dumps_jcs,
    dumps_jcs_bytes,
    export_error_codes_catalog,
    loads_jcs,
    sha256_jcs,
    validate_domain_namespace,
    validate_project_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BM25_VECTOR_PATH = REPO_ROOT / "vectors" / "bm25.json"
GENESIS_VECTOR_PATH = REPO_ROOT / "vectors" / "genesis.json"
SQLITE_PATH = REPO_ROOT / "data" / "public_am" / "am_sec_treasury_ibor.sqlite"
PARQUET_PATH = REPO_ROOT / "data" / "public_am" / "am_esg_stewardship_proxy.parquet"
NOTES_PATH = REPO_ROOT / "data" / "public_am" / "am_stewardship_domain_notes.md"

_AI_PROV_ATTRS = [
    DraftField(name="extractor_version", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    DraftField(name="prompt_or_rule_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    DraftField(
        name="confidence_score",
        logical_type=DecimalType(precision=5, scale=4),
        nullable=False,
        is_pii=False,
    ),
    DraftField(name="review_status", logical_type="UTF8_STRING", nullable=False, is_pii=False),
    DraftField(name="citation_span", logical_type="JSON_DOCUMENT", nullable=False, is_pii=False),
    DraftField(name="source_doc_id", logical_type="UTF8_STRING", nullable=False, is_pii=False),
]


def _rev_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _rev_dict(v) for k, v in reversed(list(obj.items()))}
    if isinstance(obj, list):
        return [_rev_dict(x) for x in obj]
    return obj


class TestJsonHasherAndVectors:
    """RFC 8785 JCS canonicalization, domain errors, and vectors/{bm25,genesis}.json parity."""

    def test_primitives_ecmascript_floats_and_domain_errors(self) -> None:
        assert dumps_jcs(None) == "null"
        assert dumps_jcs(True) == "true"
        assert dumps_jcs(False) == "false"
        assert dumps_jcs(-0.0) == "0"
        assert dumps_jcs(0.0) == "0"
        assert dumps_jcs(1.0) == "1"
        assert dumps_jcs(12.80) == "12.8"
        assert dumps_jcs(1e20) == "100000000000000000000"
        assert dumps_jcs(1e21) == "1e+21"
        assert dumps_jcs(1e-6) == "0.000001"
        assert dumps_jcs(1e-7) == "1e-7"

        for bad_float in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(FloatDomainError):
                dumps_jcs_bytes(bad_float)

        assert dumps_jcs(2**53 - 1) == str(2**53 - 1)
        assert dumps_jcs(-(2**53) + 1) == str(-(2**53) + 1)
        for bad_int in (2**53, -(2**53)):
            with pytest.raises(IntegerDomainError):
                dumps_jcs_bytes(bad_int)

    def test_utf16be_sort_unicode_passthrough_and_non_string_key_rejection(self) -> None:
        # Surrogate pair U+10000 (0xD800 0xDC00) sorts BEFORE U+FFFF in UTF-16BE
        ordered = dumps_jcs({"\uffff": 1, "\U00010000": 2})
        assert ordered == '{"\U00010000":2,"\uffff":1}'
        assert dumps_jcs({"name": "Nordisk Suverän Fond 🚀", "ctrl": "a\nb\tc\"\\"}) == (
            '{"ctrl":"a\\nb\\tc\\"\\\\","name":"Nordisk Suverän Fond 🚀"}'
        )
        with pytest.raises(CanonicalizationError, match="Object keys must be strings"):
            dumps_jcs_bytes({1: "invalid"})
        with pytest.raises(CanonicalizationError, match="UTF-8"):
            dumps_jcs_bytes({"\ud800": 1})
        with pytest.raises(CanonicalizationError, match="UTF-8"):
            dumps_jcs_bytes({"k": "\ud800"})

    def test_vectors_bm25_and_genesis_hashes_plus_real_data_jcs(self) -> None:
        bm25_data = json.loads(BM25_VECTOR_PATH.read_text(encoding="utf-8"))
        bm25_bytes = dumps_jcs_bytes(bm25_data)
        assert b":1," in bm25_bytes
        assert b":0.65," in bm25_bytes
        assert (
            sha256_jcs(bm25_data)
            == "f50ecac519264edfd088d62b9d9640f8cc953aa2393ec188a16a395936d3d3e3"
        )
        assert sha256_jcs(_rev_dict(bm25_data)) == sha256_jcs(bm25_data)

        genesis_data = json.loads(GENESIS_VECTOR_PATH.read_text(encoding="utf-8"))
        validated_genesis = GraphAOntology.model_validate(genesis_data)
        expected_genesis_sha = (
            "0ae856bf9fdba74e83e793ea9f74106a0c14d21d73756e1e3a12abd426d0083a"
        )
        assert sha256_jcs(genesis_data) == expected_genesis_sha
        assert sha256_jcs(validated_genesis) == expected_genesis_sha
        assert loads_jcs(dumps_jcs_bytes(genesis_data)) == genesis_data

        v2_genesis = GraphAOntology(
            ontology_version="0.0.0",
            domain_namespace="general",
            concepts=[],
            relationships=[],
            enumerations=[],
        )
        assert sha256_jcs(v2_genesis) == sha256_jcs(v2_genesis.model_dump(mode="json"))

        with sqlite3.connect(SQLITE_PATH) as conn:
            conn.row_factory = sqlite3.Row
            issuer_row = dict(
                conn.execute("SELECT * FROM issuers WHERE cik = '0001067983'").fetchone()
            )
        assert sha256_jcs(issuer_row) == sha256_jcs(_rev_dict(issuer_row))

        pq_row = pq.read_table(PARQUET_PATH).to_pylist()[0]
        pq_json_safe = {
            k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in pq_row.items()
        }
        assert sha256_jcs(pq_json_safe) == sha256_jcs(_rev_dict(pq_json_safe))


class TestClosedErrorCodesAndEnvelopes:
    """34 closed ErrorCodes, 8 CliExitCodes, ProblemDetails, CliEnvelope, and OpenDVError."""

    def test_34_error_codes_and_v2_specific_mappings(self) -> None:
        assert len(ErrorCode) == 34
        assert len(ERROR_MAPPINGS) == 34
        expected_exit_codes = frozenset({0, 1, 2, 3, 4, 5, 40, 50})
        assert expected_exit_codes == VALID_EXIT_CODES
        assert {int(c) for c in CliExitCode} == VALID_EXIT_CODES

        assert ERROR_MAPPINGS[ErrorCode.OK].problem_type_uri == "urn:opendv:ok"
        assert ERROR_MAPPINGS[ErrorCode.UNSUPPORTED_MEDIA_TYPE].http_status == 415
        assert ERROR_MAPPINGS[ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE].http_status == 500
        assert ERROR_MAPPINGS[ErrorCode.REVISION_CONFLICT].http_status == 409
        assert ERROR_MAPPINGS[ErrorCode.REVISION_CONFLICT].exit_code == CliExitCode.CONFLICT_OR_LOCKED
        assert ERROR_MAPPINGS[ErrorCode.HOLD_CHECKPOINTED].http_status == 202
        assert ERROR_MAPPINGS[ErrorCode.HOLD_CHECKPOINTED].exit_code == CliExitCode.HOLD_OR_UNCERTIFIED
        assert ERROR_MAPPINGS[ErrorCode.UNCERTIFIED_DRAFT].http_status == 202
        assert ERROR_MAPPINGS[ErrorCode.REMOTE_BIND_UNAUTHENTICATED].http_status is None
        assert (
            ERROR_MAPPINGS[ErrorCode.REMOTE_BIND_UNAUTHENTICATED].exit_code
            == CliExitCode.REMOTE_BIND_UNAUTHENTICATED
        )
        assert (
            ERROR_MAPPINGS[ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED].waivable is True
            and ERROR_MAPPINGS[ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED].rationale_required
            is True
        )

    def test_problem_details_cli_envelope_and_opendv_error(self) -> None:
        defect = EmitterDefectReport(
            target_id="duckdb_local",
            stage="EVAL-AST",
            rule_id="AST-001",
            message="Command fallback node detected",
            sql_or_artifact_snippet="SELECT 1",
        )
        err = OpenDVError(
            "Emitter AST gate failed",
            error_code=ErrorCode.EMITTER_VERIFICATION_FAILED,
            defect_report=defect,
            run_id="run-001",
        )
        assert err.exit_code == CliExitCode.EMITTER_VERIFICATION_FAILED
        assert err.http_status == 500
        prob = err.to_problem_details(instance="/api/v1/projects/am-ibor-2026/storage/compile")
        assert isinstance(prob, ProblemDetails)
        assert prob.defect_report == defect
        assert prob.type == "urn:opendv:error:emitter-verification-failed"

        ref_err = ReferenceOntologyCorruptError("SHA-256 mismatch")
        assert ref_err.error_code == ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE
        assert ref_err.exit_code == CliExitCode.EXECUTION_ERROR

        cli_only_prob = create_problem_details(
            ErrorCode.REMOTE_BIND_UNAUTHENTICATED,
            "Refusing non-loopback bind",
        )
        env = CliEnvelope(
            ok=False,
            exit_code=int(CliExitCode.REMOTE_BIND_UNAUTHENTICATED),
            error_code=ErrorCode.REMOTE_BIND_UNAUTHENTICATED,
            problem=cli_only_prob,
            data=None,
        )
        assert env.problem is not None and env.problem.status is None

        catalog = export_error_codes_catalog()
        assert catalog["total_error_codes"] == 34

        with pytest.raises(ValidationError):
            ProblemDetails.model_validate({**prob.model_dump(), "unexpected_field": 1})
        with pytest.raises(ValidationError):
            CliEnvelope.model_validate({**env.model_dump(), "extra_key": "nope"})


class TestCegarPolicyAndEnergy:
    """UAT-S6-13: CegarPolicy presets, budgets, strict tier lock, and energy formula."""

    def test_policy_presets_and_budgets(self) -> None:
        assert STEP4_DEFAULT_BUDGET.max_turns == 24
        assert STEP4_DEFAULT_BUDGET.max_tokens == 300_000

        sym = CEGAR_POLICY_SPECS["symbolic_only"]
        assert (sym.k_max, sym.v7_floor, sym.v8_mode) == (0, None, "off")
        assert (sym.budget.max_turns, sym.budget.max_tokens, sym.budget.timeout_seconds) == (
            1,
            60_000,
            120,
        )

        fast = CEGAR_POLICY_SPECS["fast"]
        assert (fast.k_max, fast.v7_floor, fast.v8_mode) == (2, None, "on_demand")
        assert (fast.budget.max_turns, fast.budget.max_tokens, fast.budget.timeout_seconds) == (
            24,
            250_000,
            600,
        )

        bal = CEGAR_POLICY_SPECS["balanced"]
        assert (bal.k_max, bal.v7_floor, bal.v7_critic_tier, bal.v7_tier_locked, bal.v8_mode) == (
            4,
            90,
            "FRONTIER_CRITIC",
            False,
            "auto",
        )
        assert (bal.budget.max_turns, bal.budget.max_tokens, bal.budget.timeout_seconds) == (
            40,
            500_000,
            1_200,
        )

        strict = CEGAR_POLICY_SPECS["strict"]
        assert (
            strict.k_max,
            strict.v7_floor,
            strict.v7_critic_tier,
            strict.v7_tier_locked,
            strict.v8_mode,
            strict.require_zero_unwaived_v6b,
        ) == (6, 95, "FRONTIER_CRITIC", True, "auto", True)
        assert (
            strict.budget.max_turns,
            strict.budget.max_tokens,
            strict.budget.timeout_seconds,
        ) == (60, 800_000, 1_800)

        with pytest.raises(ValidationError, match="FRONTIER_CRITIC"):
            WorkspaceSettings(cegar_policy="strict", v7_critic_tier="STANDARD_WORKER")
        with pytest.raises(ValidationError, match="FRONTIER_CRITIC"):
            ProjectSettingsSnapshot(cegar_policy="strict", v7_critic_tier="STANDARD_WORKER")

        for field_name in ("max_turns", "max_tokens", "timeout_seconds"):
            for bad_val in (0, -1):
                with pytest.raises(ValidationError):
                    WorkspaceSettings(**{field_name: bad_val})
                with pytest.raises(ValidationError):
                    ProjectSettingsSnapshot(**{field_name: bad_val})
            assert getattr(WorkspaceSettings(**{field_name: 1}), field_name) == 1
            assert getattr(ProjectSettingsSnapshot(**{field_name: 1}), field_name) == 1

        for extra_field in ("extra_turns", "extra_tokens", "extra_timeout_seconds"):
            for bad_val in (0, -5):
                with pytest.raises(ValidationError):
                    RunControlReq(action="raise-budget", **{extra_field: bad_val})
            assert getattr(RunControlReq(action="raise-budget", **{extra_field: 1}), extra_field) == 1

    def test_cegar_energy_formula(self) -> None:
        # 100 * |V1..V6_fail| + 10 * |V6b_fail| + max(0, v7_floor - v7)
        assert compute_cegar_energy(2, 3, policy="balanced", v7_score=82) == 200 + 30 + 8
        assert compute_cegar_energy(0, 1, policy="strict", v7_score=91) == 10 + 4
        assert compute_cegar_energy(0, 0, policy="strict", v7_score=98) == 0
        assert compute_cegar_energy(1, 2, policy="fast", v7_score=50) == 120
        assert compute_cegar_energy(1, 2, policy="balanced", v7_score=None) == 120
        with pytest.raises(ValueError, match="non-negative"):
            compute_cegar_energy(-1, 0)


class TestProjectNamingAndState:
    """UAT-S1-06 & UAT-S6-10: Project naming guards and strict per-project isolation."""

    def test_project_id_and_namespace_validation(self) -> None:
        assert validate_project_id("am-ibor-2026") == "am-ibor-2026"
        assert validate_project_id("v2_test-01") == "v2_test-01"
        assert validate_domain_namespace("asset_management") == "asset_management"

        for bad_pid in (
            "",
            "import",
            "con",
            "prn",
            "aux",
            "nul",
            "com1",
            "com9",
            "lpt1",
            "lpt9",
            "UpperCase",
            "has space",
            "-leading-dash",
            "a" * 65,
        ):
            with pytest.raises(ValueError):
                validate_project_id(bad_pid)

        for bad_ns in ("", "123_abc", "My-Domain", "has-dash", "Upper"):
            with pytest.raises(ValueError):
                validate_domain_namespace(bad_ns)

        req = ProjectInitReq(project_id="am-ibor-2026", domain_namespace="asset_management")
        assert req.project_id == "am-ibor-2026"
        with pytest.raises(ValidationError):
            ProjectInitReq(project_id="con", domain_namespace="asset_management")

    def test_strict_per_project_isolation(self) -> None:
        proj_a = ProjectState(
            project_id="am-ibor-2026",
            domain_namespace="asset_management",
            active_ontology_rev_id="rev-am-001",
            active_wave_id="W001",
            settings_snapshot=ProjectSettingsSnapshot(),
            created_at="2026-11-23T00:00:00Z",
            updated_at="2026-11-23T00:00:00Z",
        )
        proj_b = ProjectState(
            project_id="chinook-erp",
            domain_namespace="digital_media_retail",
            active_ontology_rev_id="rev-ch-001",
            active_wave_id="W002",
            settings_snapshot=ProjectSettingsSnapshot(cegar_policy="strict"),
            created_at="2026-11-23T00:00:00Z",
            updated_at="2026-11-23T00:00:00Z",
        )
        lock_a = EvolutionLockSpec(wave_number=1, locked_hub_bks={"hub_issuer": ["cik"]})
        lock_b = EvolutionLockSpec(wave_number=2, locked_hub_bks={"hub_customer": ["customer_id"]})
        assert proj_a.project_id != proj_b.project_id
        assert proj_a.active_ontology_rev_id != proj_b.active_ontology_rev_id
        assert lock_a.locked_hub_bks != lock_b.locked_hub_bks

        run_a = RunStateResp(
            run_id="run-am-1",
            project_id="am-ibor-2026",
            step="step6",
            status="RUNNING",
            policy="balanced",
            phase="S6-04",
            attempt=1,
            budget_turns_used=3,
            budget_tokens_used=1200,
        )
        assert run_a.project_id == "am-ibor-2026"
        with pytest.raises(ValidationError):
            RunStateResp(
                run_id="run-bad",
                project_id="",
                step="step6",
                status="RUNNING",
                phase="S6-01",
                attempt=0,
                budget_turns_used=0,
                budget_tokens_used=0,
            )


class TestOntologyAnd36OpDelta:
    """All 36 PascalCase OntologyDelta ops (16 _AOps + 19 _DOps + 1 server op) & GraphAOntology."""

    def test_all_36_ops_round_trip_and_partition_invariants(self) -> None:
        assert len(AGENT_ADDITIVE_OPS) == 16
        assert len(HUMAN_ONLY_OPS) == 19
        assert len(SERVER_ONLY_OPS) == 1
        assert AGENT_ADDITIVE_OPS.isdisjoint(HUMAN_ONLY_OPS)
        assert AGENT_ADDITIVE_OPS.isdisjoint(SERVER_ONLY_OPS)
        assert HUMAN_ONLY_OPS.isdisjoint(SERVER_ONLY_OPS)
        all_op_names = AGENT_ADDITIVE_OPS | HUMAN_ONLY_OPS | SERVER_ONLY_OPS
        assert len(all_op_names) == 36

        attr = ConceptAttribute(
            name="cik",
            logical_type="UTF8_STRING",
            sql_type_hint="VARCHAR(10)",
            nullable=False,
            is_business_key=True,
            is_pii=False,
        )
        mapping = ExternalMapping(
            standard_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalEntity",
            reference_pack_id="fibo-core-v2",
            reference_pack_sha256="a" * 64,
            mapping_type="skos:exactMatch",
            confidence=Decimal("0.9100"),
        )
        binding = ConceptTableBinding(source_id="src_sqlite", table_id="issuers", col_name="cik")
        citation = DocumentCitationRef(
            doc_id="doc_stewardship",
            page_number=1,
            verbatim_quote="Issuer canonical identity prioritizes ISO 17442 lei_code",
        )
        mcp_ev = ExternalMcpEvidenceRef(
            receipt_id="rcpt-001",
            server_id="sec-edgar",
            tool_name="lookup_cik",
            content_sha256="b" * 64,
            masked_excerpt="CIK 0001067983 Berkshire Hathaway",
        )
        ka_pat = KeyAuthorityPattern(
            tier="TIER_1_GLOBAL_STANDARD",
            authority_name="GLEIF",
            column_patterns=["lei_code", "issuer_lei"],
            bkcc_policy="GLOBAL",
        )
        rel = OntologyRelationship(
            relationship_uri="urn:opendv:am:rel:issuer_security",
            relationship_name="issues_security",
            source_concept_uri="urn:opendv:am:concept:Issuer",
            target_concept_uri="urn:opendv:am:concept:Security",
            cardinality="ONE_TO_MANY",
            target_link="lnk_issuer_security",
            grain_semantics="One issuer issues zero or more securities",
            description="Issuer to Security relationship",
        )
        enum = OntologyEnumeration(
            enum_uri="urn:opendv:am:enum:BreachSeverity",
            enumeration_name="BreachSeverity",
            target_reference_table="ref_breach_severity",
            codes={"NONE": "No breach", "HARD_BREACH": "Hard policy breach"},
            description="Mandate breach severity levels",
        )

        ops = [
            # 16 _AOps
            AddConcept(
                concept_uri="urn:opendv:am:concept:Issuer",
                concept_name="Issuer",
                description="Legal issuer of securities",
            ),
            AddAttribute(concept_uri="urn:opendv:am:concept:Issuer", attribute=attr),
            AddRelationship(relationship=rel),
            AddEnumeration(enumeration=enum),
            AddEnumerationCode(
                enum_uri="urn:opendv:am:enum:BreachSeverity",
                code="SOFT_WARN",
                description="Soft warning",
            ),
            AddSynonym(concept_uri="urn:opendv:am:concept:Issuer", synonym="Legal Issuer"),
            AddExternalMapping(concept_uri="urn:opendv:am:concept:Issuer", mapping=mapping),
            SetParentConcept(
                concept_uri="urn:opendv:am:concept:Issuer",
                parent_concept_uri="urn:opendv:am:concept:Party",
            ),
            SetDescription(
                concept_uri="urn:opendv:am:concept:Issuer",
                description="Public securities issuer",
            ),
            SetConceptKeyAuthority(
                concept_uri="urn:opendv:am:concept:Issuer",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                bkcc_default="GLOBAL",
            ),
            SetConceptGovernanceTags(
                concept_uri="urn:opendv:am:concept:Issuer",
                governance_tags=["SFDR_ARTICLE_8"],
                governance_constraints={"bk_priority": ["lei_code", "cik"]},
            ),
            UpdateAttributeMetadata(
                concept_uri="urn:opendv:am:concept:Issuer",
                attribute_name="cik",
                nullable=False,
                standard_property_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/hasCik",
            ),
            AddTableBinding(concept_uri="urn:opendv:am:concept:Issuer", binding=binding),
            AddSourceCitation(concept_uri="urn:opendv:am:concept:Issuer", citation=citation),
            AddMcpEvidence(concept_uri="urn:opendv:am:concept:Issuer", evidence=mcp_ev),
            AddKeyAuthorityPattern(concept_uri="urn:opendv:am:concept:Issuer", pattern=ka_pat),
            # 19 _DOps
            ReparentConcept(
                concept_uri="urn:opendv:am:concept:Issuer",
                parent_concept_uri="urn:opendv:am:concept:LegalEntity",
            ),
            ClearParentConcept(concept_uri="urn:opendv:am:concept:Issuer"),
            DeleteConcept(concept_uri="urn:opendv:am:concept:TempConcept"),
            RenameConcept(
                concept_uri="urn:opendv:am:concept:Issuer",
                new_concept_name="SecurityIssuer",
            ),
            RemoveAttribute(
                concept_uri="urn:opendv:am:concept:Issuer",
                attribute_name="legacy_code",
            ),
            RemoveExternalMapping(
                concept_uri="urn:opendv:am:concept:Issuer",
                standard_uri=mapping.standard_uri,
            ),
            RemoveSynonym(concept_uri="urn:opendv:am:concept:Issuer", synonym="Legal Issuer"),
            DeleteRelationship(relationship_uri=rel.relationship_uri),
            DeleteEnumeration(enum_uri=enum.enum_uri),
            RemoveEnumerationCode(enum_uri=enum.enum_uri, code="SOFT_WARN"),
            ChangeDomainNamespace(domain_namespace="asset_management_v2"),
            ReviseDescription(
                concept_uri="urn:opendv:am:concept:Issuer",
                description="Revised description",
            ),
            RepinExternalMapping(
                concept_uri="urn:opendv:am:concept:Issuer",
                standard_uri=mapping.standard_uri,
                reference_pack_id="fibo-core-v2",
                reference_pack_sha256="c" * 64,
                replaced_standard_uri="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/OldEntity",
                grounding_receipt_id="grnd_000011112222",
            ),
            RemoveTableBinding(
                concept_uri="urn:opendv:am:concept:Issuer",
                source_id="src_sqlite",
                table_id="issuers",
                col_name="cik",
            ),
            RemoveSourceCitation(
                concept_uri="urn:opendv:am:concept:Issuer",
                doc_id=citation.doc_id,
                page_number=1,
                verbatim_quote=citation.verbatim_quote,
            ),
            RemoveMcpEvidence(
                concept_uri="urn:opendv:am:concept:Issuer",
                receipt_id="rcpt-001",
            ),
            RemoveKeyAuthorityPattern(
                concept_uri="urn:opendv:am:concept:Issuer",
                authority_name="GLEIF",
            ),
            UpdateRelationshipMetadata(
                relationship_uri=rel.relationship_uri,
                standard_property_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Relations/hasPart",
            ),
            UpdateEnumerationMetadata(
                enum_uri=enum.enum_uri,
                standard_uri="https://spec.edmcouncil.org/fibo/ontology/FND/Arrangements/Severity",
            ),
            # 1 Server-only op
            RescoreExternalMapping(
                concept_uri="urn:opendv:am:concept:Issuer",
                standard_uri=mapping.standard_uri,
                confidence=Decimal("0.9450"),
            ),
        ]
        assert {op.op_type for op in ops} == all_op_names

        val_req = OntologyValidateReq(operations=ops, base_revision_id="rev-001")
        dumped = val_req.model_dump(mode="json")
        restored = OntologyValidateReq.model_validate(dumped)
        assert restored == val_req

        commit_req = OntologyCommitReq(
            operations=ops[:2],
            base_revision_id="rev-001",
            rationale="Approved Steward waiver with full evidence grounding.",
            waive_quarantine=True,
            low_confidence_grounding_waiver=True,
            grounding_rationale="Verified against SEC 13F and ESG stewardship notes.",
            low_confidence_waiver_concepts=["urn:opendv:am:concept:Issuer"],
            evidence_grounding=[
                EvidenceGroundingApproval(
                    concept_uri="urn:opendv:am:concept:Issuer",
                    rationale="Grounded in SQLite issuers table",
                    source_refs=["src_sqlite:issuers.cik"],
                )
            ],
        )
        assert OntologyCommitReq.model_validate(commit_req.model_dump(mode="json")) == commit_req

        with pytest.raises(ValidationError):
            OntologyValidateReq()
        with pytest.raises(ValidationError):
            OntologyValidateReq(yaml_text="ontology_version: 1.0.0", operations=ops[:1])
        assert OntologyValidateReq(yaml_text=None, operations=[]).operations == []

        with pytest.raises(ValidationError):
            OntologyCommitReq(base_revision_id="rev-001", rationale="Missing both payloads")
        assert (
            OntologyCommitReq(
                yaml_text=None,
                operations=[],
                base_revision_id="rev-001",
                rationale="Empty operations list is valid",
            ).operations
            == []
        )

        with pytest.raises(ValidationError):
            OntologyCommitReq(
                yaml_text="ontology_version: 1.0.0",
                operations=ops[:1],
                base_revision_id="rev-001",
                rationale="Cannot supply both",
            )
        with pytest.raises(ValidationError):
            OntologyCommitReq(
                operations=ops[:1],
                base_revision_id="rev-001",
                rationale="too short",
                waive_quarantine=True,
            )
        with pytest.raises(ValidationError):
            WaiverSubmitReq(
                waiver_id="w-1",
                rule_id="DV-AP-003",
                entity_name="hub_issuer",
                rationale="short",
            )
        with pytest.raises(ValidationError):
            ColumnTriageEntry(
                table_id="issuers",
                col_name="extra",
                disposition="OMITTED_INTENTIONALLY",
                rationale="too short",
            )
        with pytest.raises(ValidationError):
            AddConcept.model_validate({"concept_uri": "u", "concept_name": "n", "extra": 1})


def _make_wave1_ontology() -> GraphAOntology:
    notes_text = NOTES_PATH.read_text(encoding="utf-8")
    assert "StewardshipEngagementCase" in notes_text
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:concept:Issuer",
                concept_name="Issuer",
                description="Public securities issuer",
                key_authority_tier="TIER_2_ENTERPRISE_SOR",
                bkcc_default="SEC_EDGAR",
                attributes=[
                    ConceptAttribute(
                        name="cik",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR(10)",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
                table_bindings=[
                    ConceptTableBinding(source_id="src_sqlite", table_id="issuers", col_name="cik")
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:am:concept:ComplianceBreachAlert",
                concept_name="ComplianceBreachAlert",
                description="Mandate compliance breach alert from ESG parquet",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                bkcc_default="ESG_PARQUET",
                attributes=[
                    ConceptAttribute(
                        name="esg_rating_id",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=False,
                        is_business_key=True,
                        is_pii=False,
                    )
                ],
            ),
        ],
        relationships=[
            OntologyRelationship(
                relationship_uri="urn:opendv:am:rel:issuer_breach",
                relationship_name="triggers_breach_alert",
                source_concept_uri="urn:opendv:am:concept:Issuer",
                target_concept_uri="urn:opendv:am:concept:ComplianceBreachAlert",
                cardinality="ONE_TO_MANY",
                target_link="lnk_issuer_compliance_breach",
                grain_semantics="Issuer ESG mandate breach",
                description="Links Issuer to ComplianceBreachAlert",
            )
        ],
        enumerations=[
            OntologyEnumeration(
                enum_uri="urn:opendv:am:enum:BreachSeverity",
                enumeration_name="BreachSeverity",
                target_reference_table="ref_breach_severity",
                codes={"NONE": "No breach", "SOFT_BREACH": "Warning", "HARD_BREACH": "Hard breach"},
                description="Stewardship mandate breach severity",
            )
        ],
    )


def _make_wave1_certified_ir() -> CertifiedVaultIR:
    hk_issuer_field = IRField(
        field_id=1, name="hk_issuer", logical_type="FIXED_BINARY_32", nullable=False, is_pii=False
    )
    return CertifiedVaultIR(
        project_id="am-ibor-2026",
        ontology_version="1.0.0",
        wave_number=1,
        bronze_tables=[
            BronzeTableSpec(
                table_name="brz_sqlite_issuers",
                source_id="src_sqlite",
                source_table_id="issuers",
                columns=[
                    IRField(
                        field_id=1, name="cik", logical_type="UTF8_STRING", nullable=False, is_pii=False
                    ),
                    IRField(
                        field_id=2, name="ticker", logical_type="UTF8_STRING", nullable=False, is_pii=False
                    ),
                ],
            )
        ],
        staging_views=[
            StagingViewSpec(
                view_name="stg_sqlite_issuers",
                source_id="src_sqlite",
                source_table_id="issuers",
                col_lineage_mappings={"cik": "cik", "ticker": "ticker"},
                hash_recipes=[
                    HashRecipeSpec(
                        target_col="hk_issuer",
                        recipe_kind="HUB_HK",
                        bkcc_literal="SEC_EDGAR",
                        source_columns=["cik"],
                        key_columns=["cik"],
                        sort_alphabetically=False,
                    )
                ],
                projected_fields=[
                    hk_issuer_field,
                    IRField(
                        field_id=2, name="cik", logical_type="UTF8_STRING", nullable=False, is_pii=False
                    ),
                ],
            )
        ],
        hubs=[
            CertifiedHubSpec(
                hub_name="hub_issuer",
                business_keys=[
                    BusinessKeyPart(
                        table_id="issuers",
                        col_name="cik",
                        source_id="src_sqlite",
                        key_tier="TIER_2_ENTERPRISE_SOR",
                        bkcc_value="SEC_EDGAR",
                    )
                ],
                key_tier="TIER_2_ENTERPRISE_SOR",
                columns=[hk_issuer_field],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_issuer"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_esg_rating",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id="am_esg_stewardship_proxy",
                        col_name="esg_rating_id",
                        source_id="src_esg",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ESG_PARQUET",
                    )
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_esg_rating"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_carbon_disclosure",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id="am_esg_stewardship_proxy",
                        col_name="carbon_disclosure_id",
                        source_id="src_esg",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ESG_PARQUET",
                    )
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_carbon_disclosure"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_proxy_ballot",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id="am_esg_stewardship_proxy",
                        col_name="proxy_ballot_id",
                        source_id="src_esg",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ESG_PARQUET",
                    )
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_proxy_ballot"]),
            ),
            CertifiedHubSpec(
                hub_name="hub_compliance_breach_alert",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[
                    BusinessKeyPart(
                        table_id="am_esg_stewardship_proxy",
                        col_name="esg_rating_id",
                        source_id="src_esg",
                        key_tier="TIER_3_LOCAL_SCOPED",
                        bkcc_value="ESG_PARQUET",
                    )
                ],
                physical_hints=PhysicalLayoutHints(
                    cluster_by_cols=["hk_compliance_breach_alert"]
                ),
            ),
            # Planned concepts from am_stewardship_domain_notes.md (physical_hints=None allowed)
            CertifiedHubSpec(
                hub_name="hub_stewardship_engagement_case",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[],
                source_bindings=[],
                is_planned_concept=True,
                physical_hints=None,
            ),
            CertifiedHubSpec(
                hub_name="hub_macro_regime_signal",
                key_tier="TIER_3_LOCAL_SCOPED",
                business_keys=[],
                source_bindings=[],
                is_planned_concept=True,
                physical_hints=None,
            ),
        ],
        links=[
            CertifiedLinkSpec(
                link_name="lnk_doc_stewardship_issuer",
                link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_issuer",
                        role_name="issuer",
                        fk_col_name="hk_issuer",
                        source_col_names=["cik"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_stewardship_engagement_case",
                        role_name="engagement_case",
                        fk_col_name="hk_stewardship_engagement_case",
                        source_col_names=["engagement_case_id"],
                    ),
                ],
                source_bindings=[
                    SourceBindingSpec(
                        source_id="doc_stewardship",
                        table_id="doc_stewardship",
                        col_mappings={"cik": "cik"},
                        load_semantics=LoadSemanticsSpec(
                            source_table_id="doc_stewardship",
                            require_review_status="APPROVED",
                        ),
                        doc_citations=[
                            DocumentCitationRef(
                                doc_id="doc_stewardship",
                                page_number=1,
                                verbatim_quote="StewardshipEngagementCase",
                            )
                        ],
                    )
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_lnk_doc_stewardship"]),
            )
        ],
        satellites=[
            CertifiedSatelliteSpec(
                sat_name="sat_compliance_breach_alert",
                sat_kind="STANDARD",
                parent_name="hub_compliance_breach_alert",
                source_id="src_esg",
                source_table_id="am_esg_stewardship_proxy",
                columns=[
                    IRField(
                        field_id=1,
                        name="hk_compliance_breach_alert",
                        logical_type="FIXED_BINARY_32",
                        nullable=False,
                        is_pii=False,
                    ),
                    IRField(
                        field_id=2,
                        name="breach_severity",
                        logical_type="UTF8_STRING",
                        nullable=False,
                        is_pii=False,
                    ),
                ],
                load_semantics=LoadSemanticsSpec(
                    source_table_id="am_esg_stewardship_proxy",
                    filter_ast=PredicateLeaf(
                        op="EQ", column="breach_severity", literal="HARD_BREACH"
                    ),
                ),
                physical_hints=PhysicalLayoutHints(
                    cluster_by_cols=["hk_compliance_breach_alert"]
                ),
            ),
            CertifiedSatelliteSpec(
                sat_name="sat_lnk_doc_stewardship_ai_prov",
                sat_kind="AI_PROVENANCE_SATELLITE",
                parent_name="lnk_doc_stewardship_issuer",
                source_id="doc_stewardship",
                source_table_id="doc_stewardship",
                attributes=_AI_PROV_ATTRS,
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_lnk_doc_stewardship"]),
            ),
        ],
        reference_tables=[
            RefTableSpec(
                ref_name="ref_breach_severity",
                enum_uri="urn:opendv:am:enum:BreachSeverity",
                code_col="code",
                label_col="label",
                codes={"NONE": "No breach", "HARD_BREACH": "Hard breach"},
            )
        ],
        pit_bridges=[
            PitBridgeSpec(
                object_name="pit_compliance_breach",
                object_kind="PIT",
                root_hub="hub_compliance_breach_alert",
                target_satellites_or_links=["sat_compliance_breach_alert"],
                grain_cols=["hk_compliance_breach_alert", "as_of_dts"],
            )
        ],
        information_marts=[
            MartViewSpec(
                view_name="mart_stewardship_breaches",
                root_entity="hub_compliance_breach_alert",
                joined_entities=["sat_compliance_breach_alert"],
                columns=[
                    IRField(
                        field_id=1,
                        name="hk_compliance_breach_alert",
                        logical_type="FIXED_BINARY_32",
                        nullable=False,
                        is_pii=False,
                    )
                ],
                select_exprs={"hk_compliance_breach_alert": "h.hk_compliance_breach_alert"},
            )
        ],
        seed_records=SeedRecordsSpec(),
        wave_delta=None,
    )


class TestVaultIRAndWaveDiffs:
    """UAT-S6-17 & Wave 1 -> Wave 2 OntologyDelta + SchemaWaveDelta real-data diff tests."""

    def test_certified_ir_validators_and_planned_concept_exemption(self) -> None:
        with pytest.raises(ValidationError, match="physical_hints"):
            CertifiedHubSpec(
                hub_name="hub_attached",
                key_tier="TIER_1_GLOBAL_STANDARD",
                is_planned_concept=False,
                physical_hints=None,
            )
        with pytest.raises(ValidationError, match="strictly increasing"):
            BronzeTableSpec(
                table_name="brz_bad",
                source_id="s1",
                source_table_id="t1",
                columns=[
                    IRField(
                        field_id=2, name="a", logical_type="UTF8_STRING", nullable=False, is_pii=False
                    ),
                    IRField(
                        field_id=2, name="b", logical_type="UTF8_STRING", nullable=False, is_pii=False
                    ),
                ],
            )

        draft = DraftVaultIR(
            project_id="am-ibor-2026",
            ontology_version="1.0.0",
            wave_number=1,
            hubs=[
                DraftHubSpec(
                    hub_name="hub_stewardship_engagement_case",
                    key_tier="TIER_3_LOCAL_SCOPED",
                    is_planned_concept=True,
                )
            ],
            links=[
                DraftLinkSpec(
                    link_name="lnk_doc_stewardship_issuer",
                    link_kind="UNSTRUCTURED_EXTRACTION_LINK",
                    participants=[
                        LinkParticipantSpec(
                            hub_name="hub_issuer",
                            role_name="issuer",
                            fk_col_name="hk_issuer",
                            source_col_names=["cik"],
                        )
                    ],
                    source_bindings=[
                        DraftSourceBinding(
                            source_id="doc_stewardship",
                            table_id="doc_stewardship",
                            col_mappings={"cik": "cik"},
                        )
                    ],
                )
            ],
            satellites=[
                DraftSatelliteSpec(
                    sat_name="sat_lnk_doc_stewardship_ai_prov",
                    sat_kind="AI_PROVENANCE_SATELLITE",
                    parent_name="lnk_doc_stewardship_issuer",
                    attributes=_AI_PROV_ATTRS,
                )
            ],
        )
        assert DraftVaultIR.model_validate(draft.model_dump(mode="json")) == draft

    def test_wave1_to_wave2_ontology_and_schema_wave_delta_diff(self) -> None:
        ont_w1 = _make_wave1_ontology()
        ir_w1 = _make_wave1_certified_ir()

        # Wave 2 OntologyDelta diff grounded in am_stewardship_domain_notes.md
        w2_ops = [
            AddAttribute(
                concept_uri="urn:opendv:am:concept:Issuer",
                attribute=ConceptAttribute(
                    name="lei_code",
                    logical_type="UTF8_STRING",
                    sql_type_hint="CHAR(20)",
                    nullable=True,
                    is_business_key=True,
                    is_pii=False,
                ),
            ),
            AddAttribute(
                concept_uri="urn:opendv:am:concept:ComplianceBreachAlert",
                attribute=ConceptAttribute(
                    name="waiver_expiry_date",
                    logical_type="DATE32",
                    sql_type_hint="DATE",
                    nullable=True,
                    is_business_key=False,
                    is_pii=False,
                ),
            ),
            AddConcept(
                concept_uri="urn:opendv:am:concept:ProxyBallot",
                concept_name="ProxyBallot",
                description="Shareholder proxy ballot vote",
                key_authority_tier="TIER_3_LOCAL_SCOPED",
                bkcc_default="ESG_PARQUET",
                attributes=[
                    ConceptAttribute(
                        name="stewardship_officer_email",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=True,
                        is_business_key=False,
                        is_pii=True,
                        pii_category="EMAIL",
                    )
                ],
            ),
        ]
        ont_w2 = ont_w1.model_copy(deep=True)
        ont_w2.ontology_version = "1.1.0"
        ont_w2.concepts[0].attributes.append(w2_ops[0].attribute)
        ont_w2.concepts[1].attributes.append(w2_ops[1].attribute)
        ont_w2.concepts.append(
            CanonicalBusinessConcept(
                concept_uri=w2_ops[2].concept_uri,
                concept_name=w2_ops[2].concept_name,
                description=w2_ops[2].description,
                key_authority_tier=w2_ops[2].key_authority_tier,
                bkcc_default=w2_ops[2].bkcc_default,
                attributes=w2_ops[2].attributes,
            )
        )

        # Wave 2 CertifiedVaultIR adding EXT companion, SAME_AS link, PII sat & SchemaWaveDelta
        ir_w2 = ir_w1.model_copy(deep=True)
        ir_w2.ontology_version = "1.1.0"
        ir_w2.wave_number = 2
        ir_w2.links.append(
            CertifiedLinkSpec(
                link_name="sal_issuer",
                link_kind="SAME_AS",
                participants=[
                    LinkParticipantSpec(
                        hub_name="hub_issuer",
                        role_name="canonical_lei",
                        fk_col_name="hk_issuer_canonical",
                        source_col_names=["lei_code"],
                    ),
                    LinkParticipantSpec(
                        hub_name="hub_issuer",
                        role_name="alias_cik",
                        fk_col_name="hk_issuer_alias",
                        source_col_names=["cik"],
                    ),
                ],
                physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_sal_issuer"]),
            )
        )
        ir_w2.satellites.extend(
            [
                CertifiedSatelliteSpec(
                    sat_name="sat_compliance_breach_alert_ext_v2",
                    sat_kind="EXT",
                    parent_name="hub_compliance_breach_alert",
                    source_id="src_esg",
                    source_table_id="am_esg_stewardship_proxy",
                    extends_sat_name="sat_compliance_breach_alert",
                    companion_epoch=2,
                    columns=[
                        IRField(
                            field_id=1,
                            name="hk_compliance_breach_alert",
                            logical_type="FIXED_BINARY_32",
                            nullable=False,
                            is_pii=False,
                        ),
                        IRField(
                            field_id=2,
                            name="waiver_expiry_date",
                            logical_type="DATE32",
                            nullable=True,
                            is_pii=False,
                        ),
                    ],
                    physical_hints=PhysicalLayoutHints(
                        cluster_by_cols=["hk_compliance_breach_alert"]
                    ),
                ),
                CertifiedSatelliteSpec(
                    sat_name="sat_proxy_ballot_pii",
                    sat_kind="PII",
                    parent_name="hub_proxy_ballot",
                    source_id="src_esg",
                    source_table_id="am_esg_stewardship_proxy",
                    columns=[
                        IRField(
                            field_id=1,
                            name="hk_proxy_ballot",
                            logical_type="FIXED_BINARY_32",
                            nullable=False,
                            is_pii=False,
                        ),
                        IRField(
                            field_id=2,
                            name="stewardship_officer_email",
                            logical_type="UTF8_STRING",
                            nullable=True,
                            is_pii=True,
                            classification_tag="EMAIL",
                        ),
                    ],
                    physical_hints=PhysicalLayoutHints(cluster_by_cols=["hk_proxy_ballot"]),
                ),
            ]
        )
        ir_w2.wave_delta = SchemaWaveDelta(
            from_wave=1,
            to_wave=2,
            added_entities=[
                "sal_issuer",
                "sat_compliance_breach_alert_ext_v2",
                "sat_proxy_ballot_pii",
            ],
            added_sat_extensions=["sat_compliance_breach_alert_ext_v2"],
            locked_entities=["hub_issuer", "hub_esg_rating", "hub_compliance_breach_alert"],
        )

        assert sha256_jcs(ont_w1) != sha256_jcs(ont_w2)
        assert sha256_jcs(ir_w1) != sha256_jcs(ir_w2)
        assert sha256_jcs(CertifiedVaultIR.model_validate(ir_w2.model_dump(mode="json"))) == sha256_jcs(
            ir_w2
        )

        serialized_ir = dumps_jcs(ir_w2)
        for sql_kw in ("CREATE TABLE", "INSERT INTO", "ALTER TABLE", "SELECT "):
            assert sql_kw not in serialized_ir

        for bad_from, bad_to in ((3, 2), (2, 2), (0, 2)):
            with pytest.raises(ValidationError):
                SchemaWaveDelta(from_wave=bad_from, to_wave=bad_to)
        valid_delta = SchemaWaveDelta(from_wave=2, to_wave=3)
        assert (valid_delta.from_wave, valid_delta.to_wave) == (2, 3)
