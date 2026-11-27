"""M3 tests for stratified FIBO benchmark, 5-fold OOF calibration & selection invariants."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from opendv.grounding.kernel.calibration import (
    CandidateChannelInputs,
    ColumnCalibrationContext,
    compute_active_channels,
    compute_observed_channels,
    compute_phi_agent,
    compute_phi_anchor,
    compute_phi_lex,
    compute_sigma_disagree,
    compute_sigma_eff,
    determine_suggested_predicate,
    normalize_column_tokens,
    normalize_property_label,
    select_and_calibrate,
    softmax_with_null,
)
from opendv.grounding.providers.fibo import FiboKnowledgeSource
from opendv.grounding.structured import ColumnGroundingEngine
from opendv.grounding.structured.validators import is_valid_cusip
from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    GroundingRequest,
    SemanticContext,
    TableEvidence,
)
from opendv.models.grounding_spi import CalibrationParams, TableRerankAssignment

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.calibrate_fibo_grounding import (  # noqa: E402
    SPLIT_SEED,
    _eval_view,
    _extract_view,
    load_benchmark,
    split_dev_heldout_and_folds,
)
from scripts.calibrate_fibo_grounding import (  # noqa: E402
    main as calibrate_main,
)
BENCHMARK_PATH = REPO_ROOT / "tests" / "grounding" / "benchmarks" / "fibo_stratified_benchmark_v1.json"
CALIBRATION_PATH = (
    REPO_ROOT / "src" / "opendv" / "grounding" / "providers" / "fibo" / "fibo_calibration.json"
)

EXPECTED_STRATA = frozenset(
    {
        "GLEIF",
        "SEC EDGAR 13F/XBRL",
        "OpenFIGI",
        "FRED/ECB",
        "FDIC",
        "ISDA/OTC",
        "NO_MATCH",
    }
)
EXPECTED_ACTIVE_MODULES = frozenset({"BE", "CAE", "CMNS", "DER", "FBC", "FND", "IND", "LOAN", "SEC"})
EXPECTED_NO_MATCH_CATEGORIES = frozenset(
    {
        "technical_surrogate",
        "system_audit_metadata",
        "insufficient_evidence",
        "out_of_vocabulary_domain",
        "below_threshold",
    }
)


@pytest.fixture(scope="module")
def fibo_source() -> FiboKnowledgeSource:
    return FiboKnowledgeSource()


def _det_policy(*, enable_table_context: bool = True) -> GroundingPolicy:
    return GroundingPolicy(
        mode="deterministic_only",
        skip_reason="m3 calibration and selection test",
        enable_table_context=enable_table_context,
    )


def test_benchmark_integrity_strata_modules_and_5fold_group_split(
    fibo_source: FiboKnowledgeSource,
) -> None:
    """Benchmark has 180-200 cols, 7 strata, 9 active modules, valid URIs, & zero group leakage."""
    records = load_benchmark(BENCHMARK_PATH)
    assert 180 <= len(records) <= 200

    strata_counts: Counter[str] = Counter()
    module_counts: Counter[str] = Counter()
    entry_kind_counts: Counter[str] = Counter()
    no_match_counts: Counter[str] = Counter()
    sample_ids: set[str] = set()

    for rec in records:
        assert set(rec.keys()) == {
            "sample_id",
            "table_group",
            "stratum",
            "table",
            "gold_uri",
            "gold_no_match_category",
        }
        sid = rec["sample_id"]
        assert sid not in sample_ids
        sample_ids.add(sid)

        tbl = TableEvidence.model_validate(rec["table"])
        assert len(tbl.columns) >= 2
        strata_counts[rec["stratum"]] += 1

        g_uri = rec["gold_uri"]
        if g_uri is not None:
            item = fibo_source.get_item(g_uri)
            assert item is not None, f"Missing or deprecated gold_uri: {g_uri}"
            module_counts[str(item.metadata.get("module", ""))] += 1
            entry_kind_counts[item.entry_kind] += 1
            assert rec["gold_no_match_category"] is None
        else:
            assert rec["gold_no_match_category"] in EXPECTED_NO_MATCH_CATEGORIES
            no_match_counts[str(rec["gold_no_match_category"])] += 1

    assert set(strata_counts.keys()) == EXPECTED_STRATA
    assert all(25 <= cnt <= 32 for cnt in strata_counts.values())
    assert set(module_counts.keys()) == EXPECTED_ACTIVE_MODULES
    assert {"class", "datatype_property", "object_property"} <= set(entry_kind_counts.keys())
    assert set(no_match_counts.keys()) == EXPECTED_NO_MATCH_CATEGORIES

    # Deterministic table_group split & 5-fold GroupKFold with zero leakage
    dev_recs, heldout_recs, group_to_fold = split_dev_heldout_and_folds(
        records, seed=SPLIT_SEED, n_folds=5
    )
    assert 120 <= len(dev_recs) <= 140
    assert len(heldout_recs) == 60
    dev_groups = {r["table_group"] for r in dev_recs}
    held_groups = {r["table_group"] for r in heldout_recs}
    assert dev_groups.isdisjoint(held_groups)
    assert set(group_to_fold.keys()) == dev_groups
    assert set(group_to_fold.values()) == {0, 1, 2, 3, 4}

    held_strata = Counter(r["stratum"] for r in heldout_recs)
    assert set(held_strata.keys()) == EXPECTED_STRATA
    assert all(7 <= cnt <= 10 for cnt in held_strata.values())


def test_calibration_artifact_loading_validation_and_verify_heldout(tmp_path: Path) -> None:
    """Validate fibo_calibration.json, missing-path None fallback, StrictModel, & --verify-heldout."""
    assert CALIBRATION_PATH.is_file()
    raw = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["split_seed"] == SPLIT_SEED
    assert 0.5 <= raw["T"] <= 2.5
    assert -2.0 <= raw["s_null_base"] <= 2.0
    assert 0.0 <= raw["gamma_null"] <= 0.5
    assert 0.5 <= raw["c_cal"] <= 3.0
    assert set(raw["channel_weights"].keys()) == {"lex", "surface", "shape_range", "anchor", "agent"}

    metrics = raw["heldout_metrics"]
    assert metrics["top1_acc"] >= 0.80
    assert metrics["overall_acc"] >= 0.80
    assert metrics["top3_recall"] >= 0.92
    assert metrics["no_match_f1"] >= 0.80
    assert metrics["path_b1_f1"] >= 0.80
    assert metrics["abstain_ECE"] <= 0.08
    assert 0.82 <= metrics["ci90_coverage"] <= 0.96
    assert metrics["cusip_fast_path_ok"] is True
    assert metrics["starter_packs_ok"] is True

    default_src = FiboKnowledgeSource()
    cal = default_src.calibration_params
    assert isinstance(cal, CalibrationParams)
    assert cal.calibration_mode == "empirical_calibrated"
    assert pytest.approx(raw["T"]) == cal.T
    assert pytest.approx(raw["s_null_base"]) == cal.s_null_base
    assert pytest.approx(raw["gamma_null"]) == cal.gamma_null
    assert pytest.approx(raw["c_cal"]) == cal.c_cal

    assert FiboKnowledgeSource(calibration_path=tmp_path / "missing.json").calibration_params is None

    bad_cal = tmp_path / "bad_cal.json"
    bad_cal.write_text(json.dumps({"T": "not_a_float"}), encoding="utf-8")
    with pytest.raises(ValidationError):
        _ = FiboKnowledgeSource(calibration_path=bad_cal).calibration_params

    out_tmp = tmp_path / "fibo_calibration_test.json"
    exit_code = calibrate_main(
        ["--benchmark", str(BENCHMARK_PATH), "--out", str(out_tmp), "--verify-heldout"]
    )
    assert exit_code == 0
    assert out_tmp.is_file()
    regen = json.loads(out_tmp.read_text(encoding="utf-8"))
    assert regen["T"] == raw["T"]
    assert regen["c_cal"] == raw["c_cal"]


def test_modality_dropout_robustness_shape_and_anchors(fibo_source: FiboKnowledgeSource) -> None:
    """Engine remains accurate under deterministic 25% shape-drop and 25% anchor-drop views."""
    records = load_benchmark(BENCHMARK_PATH)
    dev_recs, _, _ = split_dev_heldout_and_folds(records, seed=SPLIT_SEED, n_folds=5)
    pos_dev = [r for r in dev_recs if r["gold_uri"] is not None]
    rng = random.Random(SPLIT_SEED + 99)
    sample_recs = rng.sample(pos_dev, 24)
    shape_drops = set(range(0, 6))
    anchor_drops = set(range(6, 12))

    engine = ColumnGroundingEngine(fibo_source)
    top3_hits = 0
    for idx, rec in enumerate(sample_recs):
        tbl = TableEvidence.model_validate(rec["table"])
        if idx in shape_drops:
            tbl = tbl.model_copy(
                update={"columns": [c.model_copy(update={"precomputed_shape": None}) for c in tbl.columns]}
            )
        pol = _det_policy(enable_table_context=(idx not in anchor_drops))
        draft = engine.ground_symbolic(GroundingRequest(tables=[tbl], policy=pol))
        cd = next(iter(draft.table_drafts.values())).column_drafts[0]
        top3 = [c.entry_id for c in cd.symbolic_outcome.scored_candidates[:3]]
        if rec["gold_uri"] in top3:
            top3_hits += 1
    assert top3_hits / len(sample_recs) >= 0.90


def test_calibration_channels_fast_path_pruning_and_predicates(
    fibo_source: FiboKnowledgeSource,
) -> None:
    """Verify normalized channels, M_obs/M_active, fast-path, k_90 + 0.03 pruning, & predicates."""
    params = fibo_source.calibration_params or CalibrationParams.analytic_defaults()
    assert compute_phi_lex(1.0) == 1.0
    assert compute_phi_lex(0.0) == -1.0
    assert compute_phi_anchor(0.8, 0.3) == 1.0
    assert compute_phi_anchor(-0.7, -0.4) == -1.0

    # Monotonic phi_agent across [0, 1] including < 1/3
    for conf in (0.1, 0.25, 0.9):
        asgn = TableRerankAssignment(column_name="x", selected_index=0, abstain=False, confidence=conf)
        p_sel, pen_sel = compute_phi_agent(0, asgn, w_agent=2.0)
        p_other, pen_other = compute_phi_agent(1, asgn, w_agent=2.0)
        assert p_sel == pytest.approx(conf)
        assert p_other == pytest.approx(-conf)
        assert p_sel >= p_other
        assert pen_sel == 0.0 and pen_other == 0.0

    # M_obs / M_active including symbolic_only mode (rerank_results=None)
    cand = CandidateChannelInputs(
        entry_id="lei",
        label="legal entity identifier",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        has_surface_rule=True,
    )
    ctx_sym = ColumnCalibrationContext(
        column_name="custodian_lei", n_eff=100, has_column_surface_pattern=True, anchor_enabled=True
    )
    assert "agent" not in compute_observed_channels(ctx_sym, [cand])
    assert "agent" not in compute_active_channels(ctx_sym, cand)

    # Neutral phi_anchor == 0.0 excluded from list-based sigma_disagree
    sigma_d = compute_sigma_disagree(
        {"lex": 0.9, "surface": 0.9, "shape_range": 0.3, "anchor": 0.0},
        frozenset({"lex", "surface", "shape_range", "anchor"}),
        frozenset({"lex", "surface", "shape_range", "anchor"}),
    )
    assert sigma_d == pytest.approx(0.2828427, rel=1e-4)
    assert compute_sigma_eff(params, 200, 1.0, 0.0) == pytest.approx(
        compute_sigma_eff(params, 5000, 1.0, 0.0)
    )

    # Fast-path verified for both cusip (engine) and custodian_lei (gate with sigma_eff <= 0.65)
    eng = ColumnGroundingEngine(fibo_source)
    cusip_tbl = TableEvidence(
        table_name="securities",
        columns=[
            ColumnEvidence(
                column_name="cusip", data_type="VARCHAR", is_pk=True, sample_values=["037833100"] * 100
            )
        ],
    )
    cusip_req = GroundingRequest(tables=[cusip_tbl], policy=GroundingPolicy(mode="agentic"))
    cusip_draft = eng.ground_symbolic(cusip_req)
    assert "cusip" in cusip_draft.fast_path_mask["securities"]
    cusip_sym_only = eng.finalize(cusip_req, cusip_draft, rerank_results=None)
    assert cusip_sym_only.tables[0].columns[0].execution_mode_used == "symbolic_fast_path"

    lei_cand = CandidateChannelInputs(
        entry_id="https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LEIEntities/LegalEntityIdentifier",
        label="legal entity identifier",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="identifier"),
        lex_score=0.95,
        surface_score=1.0,
        has_surface_rule=True,
        shape_range_score=1.0,
        base_anchor_score=1.0,
        corroborated=True,
    )
    lei_ctx = ColumnCalibrationContext(
        column_name="custodian_lei",
        n_eff=200,
        has_column_surface_pattern=True,
        has_checksum_pattern=True,
        anchor_enabled=True,
        has_top_table_anchor=True,
    )
    lei_out = select_and_calibrate(lei_ctx, [lei_cand], params, _det_policy())
    assert lei_out.fast_path_eligible is True
    assert lei_out.scored_candidates[0].sigma_eff <= params.fast_path_max_eff_sigma
    assert lei_out.choices[0].suggested_predicate == "skos:closeMatch"

    # K=1 abstention preservation & k_90 + 0.03 pruning
    weak_single = CandidateChannelInputs(
        entry_id="weak_one",
        label="weak one",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(),
        lex_score=0.2,
        shape_range_score=-0.5,
    )
    scored_1, p_null_1, _ = softmax_with_null(
        ColumnCalibrationContext(column_name="unknown_col", n_eff=20), [weak_single], params
    )
    assert scored_1[0].probability + p_null_1 == pytest.approx(1.0)
    assert p_null_1 > 0.5

    # min(3, k_90) + 0.03 pruning: rank 2 with p < 0.03 gets pruned
    c_main = CandidateChannelInputs(
        entry_id="c_main",
        label="trade date",
        entry_kind="object_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="temporal"),
        lex_score=0.95,
        shape_range_score=1.0,
    )
    c_tiny = CandidateChannelInputs(
        entry_id="c_tiny",
        label="other date",
        entry_kind="object_property",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(archetype="temporal"),
        lex_score=0.10,
        shape_range_score=0.2,
    )
    pruned_out = select_and_calibrate(
        ColumnCalibrationContext(column_name="trade_dt", n_eff=50),
        [c_main, c_tiny],
        params,
        _det_policy(),
    )
    assert len(pruned_out.choices) == 1
    assert pruned_out.choices[0].entry_id == "c_main"

    # Predicate normalization & suggested_predicate_hint=="related" short-circuit
    assert normalize_property_label("hasTradeDate") == ["trade", "date"]
    assert normalize_property_label("isCallable") == ["callable"]
    assert normalize_column_tokens("trade_dt", archetype="temporal") == ["trade"]
    assert normalize_column_tokens("country_cd", archetype="code_classifier") == ["country"]
    assert normalize_column_tokens("issuer_id", archetype="identifier") == ["issuer"]
    assert normalize_column_tokens("callable_ind", archetype=None, is_boolean_datatype=True) == [
        "callable"
    ]
    assert (
        determine_suggested_predicate(
            "trade_dt",
            CandidateChannelInputs(
                entry_id="x",
                label="hasTradeDate",
                entry_kind="object_property",
                origin_kind="reference_standard",
                semantic_context=SemanticContext(archetype="temporal"),
                suggested_predicate_hint="related",
            ),
            0.99,
            "MATCHED",
        )
        == "skos:relatedMatch"
    )


def test_review_reason_no_match_priority_surrogate_batch_id_and_scd2(
    fibo_source: FiboKnowledgeSource,
) -> None:
    """Verify opaque headers, review_reason/no_match_category priorities, surrogate AND-gate, batch_id & SCD2."""
    params = CalibrationParams.analytic_defaults()
    pol = _det_policy()

    # (?i) opaque header regex -> insufficient_evidence
    for hdr in ("COL_01", "c_2", "Field_99", "v_3", "A"):
        res = select_and_calibrate(
            ColumnCalibrationContext(column_name=hdr, n_eff=25), [], params, pol
        )
        assert res.status == "NO_MATCH"
        assert res.no_match_category == "insufficient_evidence"

    # Ordered review_reason: unverified_masked_values > elevated_null_probability > competing_candidates
    cand_a = CandidateChannelInputs(
        entry_id="a",
        label="a",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(),
        lex_score=0.9,
        shape_range_score=0.8,
    )
    cand_b = CandidateChannelInputs(
        entry_id="b",
        label="b",
        entry_kind="class",
        origin_kind="reference_standard",
        semantic_context=SemanticContext(),
        lex_score=0.88,
        shape_range_score=0.8,
    )
    masked_out = select_and_calibrate(
        ColumnCalibrationContext(column_name="masked_col", n_eff=0, masked_count=10),
        [cand_a, cand_b],
        params,
        pol,
    )
    assert masked_out.status == "AMBIGUOUS"
    assert masked_out.review_reason == "unverified_masked_values"

    competing_out = select_and_calibrate(
        ColumnCalibrationContext(column_name="competing_col", n_eff=50),
        [cand_a, cand_b],
        params,
        pol,
    )
    assert competing_out.status == "AMBIGUOUS"
    assert competing_out.review_reason == "competing_candidates"

    # Engine: AND-gated technical_surrogate, batch_id PK exemption, and SCD2 filtering
    eng = ColumnGroundingEngine(fibo_source)
    batches_tbl = TableEvidence(
        table_name="batches",
        columns=[
            ColumnEvidence(
                column_name="id",
                data_type="BIGINT",
                is_pk=True,
                sample_values=[str(i) for i in range(1, 20)],
            ),
            ColumnEvidence(
                column_name="batch_id",
                data_type="BIGINT",
                is_pk=False,
                sample_values=[str(i) for i in range(1, 20)],
            ),
        ],
    )
    b_resp = eng.ground(GroundingRequest(tables=[batches_tbl], policy=pol))
    b_cols = {c.column_name: c for c in b_resp.tables[0].columns}
    assert b_cols["id"].no_match_category != "technical_surrogate"
    assert b_cols["batch_id"].no_match_category != "system_audit_metadata"

    distinct_cusips = [
        next(f"037833{i:02d}{d}" for d in range(10) if is_valid_cusip(f"037833{i:02d}{d}"))
        for i in range(10, 30)
    ]
    scd_tbl = TableEvidence(
        table_name="positions",
        columns=[
            ColumnEvidence(
                column_name="surrogate_id",
                data_type="BIGINT",
                is_pk=True,
                sample_values=[str(i) for i in range(1, 21)],
            ),
            ColumnEvidence(
                column_name="cusip",
                data_type="VARCHAR",
                is_pk=False,
                sample_values=distinct_cusips,
            ),
            ColumnEvidence(
                column_name="batch_id",
                data_type="BIGINT",
                is_pk=False,
                sample_values=["100"] * 20,
            ),
            ColumnEvidence(
                column_name="load_end_dts",
                data_type="TIMESTAMP",
                sample_values=["2026-10-01T00:00:00Z"] * 20,
            ),
            ColumnEvidence(
                column_name="applied_dts",
                data_type="TIMESTAMP",
                sample_values=["2026-10-01T00:00:00Z"] * 20,
            ),
            ColumnEvidence(
                column_name="effective_from",
                data_type="DATE",
                sample_values=["2026-01-01"] * 20,
            ),
        ],
    )
    s_resp = eng.ground(GroundingRequest(tables=[scd_tbl], policy=pol))
    s_cols = {c.column_name: c for c in s_resp.tables[0].columns}
    assert s_cols["surrogate_id"].no_match_category == "technical_surrogate"
    assert s_cols["batch_id"].no_match_category == "system_audit_metadata"
    assert s_cols["load_end_dts"].no_match_category == "system_audit_metadata"
    assert s_cols["applied_dts"].no_match_category != "system_audit_metadata"
    assert s_cols["effective_from"].no_match_category != "system_audit_metadata"


def test_calibration_view_parity_with_engine_across_benchmark(
    fibo_source: FiboKnowledgeSource,
) -> None:
    """_eval_view(_extract_view(...)) matches ColumnGroundingEngine.ground() across all 190 records."""
    cal = fibo_source.calibration_params
    assert cal is not None
    eng = ColumnGroundingEngine(fibo_source, calibration=cal)
    pol = _det_policy()
    records = load_benchmark(BENCHMARK_PATH)
    assert len(records) == 190

    seen: dict[str, tuple[str, str | None]] = {}
    for rec in records:
        view = _extract_view(rec, 0, eng, fibo_source)
        cached_out, _ = _eval_view(view, cal, pol, fibo_source)
        tbl = TableEvidence.model_validate(rec["table"])
        full_out = eng.ground(GroundingRequest(tables=[tbl], policy=pol)).tables[0].columns[0]

        cached_ids = [c.entry_id for c in cached_out.choices]
        full_ids = [c.entry_id for c in full_out.choices]
        assert cached_out.status == full_out.status, rec["sample_id"]
        assert cached_out.no_match_category == full_out.no_match_category, rec["sample_id"]
        assert cached_ids == full_ids, rec["sample_id"]
        seen[str(rec["sample_id"])] = (cached_out.status, cached_out.no_match_category)

    assert seen["nomatch_surrogate_keys__record_seq_pk"] == ("NO_MATCH", "technical_surrogate")
    assert seen["nomatch_surrogate_keys__trade_guid"] == ("NO_MATCH", "technical_surrogate")

    # _SEQ_RE parity: line_no monotonic_int PK alongside a checksummed ISIN is NOT technical_surrogate
    seq_rec = {
        "sample_id": "seq_parity_probe",
        "table_group": "seq_parity_grp",
        "stratum": "OpenFIGI",
        "gold_uri": None,
        "gold_no_match_category": None,
        "table": {
            "table_name": "trade_legs",
            "columns": [
                {
                    "column_name": "line_no",
                    "data_type": "BIGINT",
                    "is_pk": True,
                    "sample_values": [str(i) for i in range(1, 21)],
                },
                {
                    "column_name": "isin",
                    "data_type": "VARCHAR",
                    "sample_values": ["US0378331005"] * 20,
                },
            ],
        },
    }
    seq_view = _extract_view(seq_rec, 0, eng, fibo_source)
    assert seq_view.siblings_for_owner == ()
    seq_cached, _ = _eval_view(seq_view, cal, pol, fibo_source)
    seq_full = (
        eng.ground(GroundingRequest(tables=[TableEvidence.model_validate(seq_rec["table"])], policy=pol))
        .tables[0]
        .columns[0]
    )
    assert seq_cached.status == seq_full.status
    assert seq_cached.no_match_category == seq_full.no_match_category
    assert seq_cached.no_match_category != "technical_surrogate"


def test_calibration_params_bounds_and_unknown_key_rejection(tmp_path: Path) -> None:
    """CalibrationParams enforces finite/positive bounds and FiboKnowledgeSource rejects unknown keys."""
    for bad_kwargs in (
        {"T": 0.0},
        {"T": -1.0},
        {"T": float("nan")},
        {"T": float("inf")},
        {"c_cal": 0.0},
        {"c_cal": -2.0},
        {"c_cal": float("nan")},
        {"gamma_null": -0.1},
        {"gamma_null": float("nan")},
        {"s_null_base": float("nan")},
        {"s_null_base": float("-inf")},
    ):
        with pytest.raises(ValidationError):
            CalibrationParams(**bad_kwargs)

    for bad_payload in (
        {"T": 0.5, "t": 0.5},
        {"T": 0.7, "extra_rogue_key": 123},
        {"T": 0.0},
        {"c_cal": -1.0},
        {"gamma_null": -0.5},
        [1, 2, 3],
    ):
        cal_file = tmp_path / "invalid_cal.json"
        cal_file.write_text(json.dumps(bad_payload), encoding="utf-8")
        with pytest.raises(ValidationError):
            _ = FiboKnowledgeSource(calibration_path=cal_file).calibration_params

    valid_file = tmp_path / "valid_cal.json"
    valid_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "split_seed": SPLIT_SEED,
                "heldout_metrics": {"top1_acc": 0.95},
                "T": 0.75,
                "c_cal": 1.5,
                "s_null_base": -0.5,
                "gamma_null": 0.1,
            }
        ),
        encoding="utf-8",
    )
    loaded = FiboKnowledgeSource(calibration_path=valid_file).calibration_params
    assert loaded is not None
    assert loaded.calibration_mode == "empirical_calibrated"
    assert pytest.approx(0.75) == loaded.T
    assert pytest.approx(1.5) == loaded.c_cal
