"""M2.2 tests for FiboKnowledgeSource, steward_context, and ColumnGroundingEngine."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from opendv.grounding import KnowledgeSource
from opendv.grounding.kernel import load_iso_codes
from opendv.grounding.kernel.pii_core import MASKED_PII_TOKEN, has_redacted_placeholder
from opendv.grounding.providers.fibo import (
    FiboKnowledgeSource,
    FiboReferenceOntologyLoader,
    get_default_fibo_loader,
)
from opendv.grounding.providers.fibo.closure_rules import FIBO_PII_MAP, uri_local_name
from opendv.grounding.structured import ColumnGroundingEngine, SymbolicGroundingDraft
from opendv.grounding.structured.steward_context import (
    build_table_anchor_query,
    clean_steward_text,
    extract_steward_table_context,
    strip_uris_and_pii,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    GroundingRequest,
    InferredValueProfile,
    SemanticContext,
    TableAnchorCandidate,
    TableEvidence,
)
from opendv.models.grounding_spi import (
    CalibrationParams,
    RecallHit,
    TableReasoningContext,
    TableRerankAssignment,
    TableRerankResult,
)
from opendv.models.sources import ColumnShapeStats
from opendv.sources.starter_packs import STARTER_PACK_IDS, get_starter_pack_ontology


@pytest.fixture(scope="module")
def fibo_source() -> FiboKnowledgeSource:
    return FiboKnowledgeSource()


def _det_policy() -> GroundingPolicy:
    return GroundingPolicy(mode="deterministic_only", skip_reason="m2.2 unit test")


def _make_synthetic_pack_bytes() -> bytes:
    base = "https://spec.edmcouncil.org/fibo/ontology"
    payload = {
        "metadata": {
            "pack_id": "fibo-synth",
            "pack_schema_version": "2.0.0",
            "version": "2.0.0-synth",
            "modules": ["BE", "FBC", "FND", "IND", "SEC"],
        },
        "classes": {
            f"{base}/BE/LegalEntities/LegalPersons/LegalEntity": {
                "uri": f"{base}/BE/LegalEntities/LegalPersons/LegalEntity",
                "label": "legal entity",
                "definition": "Autonomous legal person.",
                "module": "BE",
                "synonyms": ["Company"],
                "sub_class_of": [],
                "disjoint_with": [f"{base}/SEC/Debt/Bonds/Bond"],
                "archetype": "entity",
                "deprecated": False,
            },
            f"{base}/SEC/Debt/Bonds/Bond": {
                "uri": f"{base}/SEC/Debt/Bonds/Bond",
                "label": "bond",
                "definition": "Debt security.",
                "module": "SEC",
                "synonyms": [],
                "sub_class_of": [],
                "disjoint_with": [f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"],
                "archetype": "agreement",
                "deprecated": False,
            },
            f"{base}/SEC/Debt/Bonds/OldBond": {
                "uri": f"{base}/SEC/Debt/Bonds/OldBond",
                "label": "old bond",
                "definition": "Deprecated bond.",
                "module": "SEC",
                "synonyms": [],
                "sub_class_of": [],
                "archetype": "agreement",
                "deprecated": True,
                "replaced_by": f"{base}/SEC/Debt/Bonds/Bond",
            },
            f"{base}/FND/Places/VirtualPlaces/ElectronicMailAddress": {
                "uri": f"{base}/FND/Places/VirtualPlaces/ElectronicMailAddress",
                "label": "electronic mail address",
                "definition": "Email address.",
                "module": "FND",
                "synonyms": ["email"],
                "sub_class_of": [],
                "archetype": "value_object",
                "deprecated": False,
            },
            f"{base}/FND/Places/VirtualPlaces/CorporateEmailAddress": {
                "uri": f"{base}/FND/Places/VirtualPlaces/CorporateEmailAddress",
                "label": "corporate email address",
                "definition": "Work email address.",
                "module": "FND",
                "synonyms": [],
                "sub_class_of": [f"{base}/FND/Places/VirtualPlaces/ElectronicMailAddress"],
                "archetype": "value_object",
                "deprecated": False,
            },
            f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate": {
                "uri": f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate",
                "label": "explicit date",
                "definition": "Calendar date.",
                "module": "FND",
                "synonyms": [],
                "sub_class_of": [],
                "archetype": "temporal",
                "deprecated": False,
            },
            f"{base}/IND/InterestRates/InterestRates/ReferenceInterestRate": {
                "uri": f"{base}/IND/InterestRates/InterestRates/ReferenceInterestRate",
                "label": "reference interest rate",
                "definition": "Benchmark interest rate.",
                "module": "IND",
                "synonyms": [],
                "sub_class_of": [],
                "archetype": "quantity_measure",
                "deprecated": False,
            },
            f"{base}/SEC/Securities/SecuritiesIdentificationIndividuals/FinancialInstrumentGlobalIdentifier": {
                "uri": f"{base}/SEC/Securities/SecuritiesIdentificationIndividuals/FinancialInstrumentGlobalIdentifier",
                "label": "financial instrument global identifier",
                "definition": "OpenFIGI security identifier.",
                "module": "SEC",
                "synonyms": ["FIGI"],
                "sub_class_of": [],
                "identified_entity_uris": [f"{base}/SEC/Debt/Bonds/Bond"],
                "archetype": "entity",
                "deprecated": False,
            },
        },
        "properties": {
            f"{base}/SEC/Debt/Bonds/issuedBy": {
                "uri": f"{base}/SEC/Debt/Bonds/issuedBy",
                "label": "issued by",
                "definition": "Issuer of bond.",
                "module": "SEC",
                "property_type": "ObjectProperty",
                "domain": [f"{base}/SEC/Debt/Bonds/Bond"],
                "range": [f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"],
                "sub_property_of": [],
                "inverse_of": [f"{base}/BE/LegalEntities/LegalPersons/issues"],
                "is_functional": True,
                "is_inverse_functional": False,
                "deprecated": False,
            },
            f"{base}/BE/LegalEntities/LegalPersons/issues": {
                "uri": f"{base}/BE/LegalEntities/LegalPersons/issues",
                "label": "issues",
                "definition": "Issues bond.",
                "module": "BE",
                "property_type": "ObjectProperty",
                "domain": [f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"],
                "range": [f"{base}/SEC/Debt/Bonds/Bond"],
                "sub_property_of": [],
                "inverse_of": [f"{base}/SEC/Debt/Bonds/issuedBy"],
                "is_functional": False,
                "is_inverse_functional": True,
                "deprecated": False,
            },
            f"{base}/FND/DatesAndTimes/FinancialDates/hasMaturityDate": {
                "uri": f"{base}/FND/DatesAndTimes/FinancialDates/hasMaturityDate",
                "label": "has maturity date",
                "definition": "Maturity date of bond.",
                "module": "FND",
                "property_type": "ObjectProperty",
                "domain": [f"{base}/SEC/Debt/Bonds/Bond"],
                "range": [f"{base}/FND/DatesAndTimes/FinancialDates/ExplicitDate"],
                "sub_property_of": [],
                "inverse_of": [],
                "is_functional": True,
                "deprecated": False,
            },
            f"{base}/FND/Parties/Parties/hasOldProp": {
                "uri": f"{base}/FND/Parties/Parties/hasOldProp",
                "label": "has old prop",
                "definition": "Deprecated property.",
                "module": "FND",
                "property_type": "DatatypeProperty",
                "domain": [],
                "range": [],
                "sub_property_of": [],
                "deprecated": True,
            },
            f"{base}/FND/Places/VirtualPlaces/hasCorporateEmail": {
                "uri": f"{base}/FND/Places/VirtualPlaces/hasCorporateEmail",
                "label": "has corporate email",
                "definition": "Corporate email property.",
                "module": "FND",
                "property_type": "ObjectProperty",
                "domain": [f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"],
                "range": [f"{base}/FND/Places/VirtualPlaces/CorporateEmailAddress"],
                "sub_property_of": [],
                "deprecated": False,
            },
        },
        "individuals": {
            f"{base}/IND/InterestRates/USD-SOFR": {
                "uri": f"{base}/IND/InterestRates/USD-SOFR",
                "label": "USD-SOFR",
                "module": "IND",
                "aliases": ["SOFR"],
                "types": [f"{base}/IND/InterestRates/InterestRates/ReferenceInterestRate"],
                "deprecated": False,
            },
            f"{base}/SEC/Debt/Bonds/OldInd": {
                "uri": f"{base}/SEC/Debt/Bonds/OldInd",
                "label": "DEPRECATED_IND",
                "module": "SEC",
                "aliases": [],
                "types": [f"{base}/SEC/Debt/Bonds/Bond"],
                "deprecated": True,
            },
        },
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def test_fibo_protocol_metadata_country_fallback_figi_and_starter_pack_parity(
    fibo_source: FiboKnowledgeSource, tmp_path: Path
) -> None:
    """FiboKnowledgeSource satisfies KnowledgeSource SPI, Country fallback, FIGI override & >=0.65 parity."""
    assert isinstance(fibo_source, KnowledgeSource)
    assert fibo_source.metadata.anchor_entry_kinds == frozenset({"class"})
    assert fibo_source.metadata.origin_kind == "reference_standard"
    cal_params = fibo_source.calibration_params
    assert isinstance(cal_params, CalibrationParams)
    assert cal_params.calibration_mode == "empirical_calibrated"
    assert FiboKnowledgeSource(calibration_path=tmp_path / "missing.json").calibration_params is None

    iso = load_iso_codes()
    country_item = fibo_source.get_item(iso.fibo_country_uri)
    assert country_item is not None
    assert country_item.semantic_context.archetype == "code_classifier"

    figi_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/"
        "SecuritiesIdentificationIndividuals/FinancialInstrumentGlobalIdentifier"
    )
    figi_item = fibo_source.get_item(figi_uri)
    assert figi_item is not None
    assert figi_item.semantic_context.archetype == "identifier"
    assert figi_item.semantic_context.standard_authority == "OPENFIGI"

    custodian_uri = "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/Custodian"
    cust_item = fibo_source.get_item(custodian_uri)
    assert cust_item is not None
    assert cust_item.semantic_context.archetype == "party_role"
    assert cust_item.neighborhood_links["is_played_by"]
    assert fibo_source.get_ancestors(custodian_uri, max_depth=4)
    # Single-leaf party_role populates unambiguous semantic_context.played_by_uri
    func_ent_uri = (
        "https://spec.edmcouncil.org/fibo/ontology/BE/FunctionalEntities/"
        "FunctionalEntities/FunctionalEntity"
    )
    fe_item = fibo_source.get_item(func_ent_uri)
    assert fe_item is not None and fe_item.semantic_context.played_by_uri is not None

    # All 31 unique starter_packs/ URIs score >= 0.6500 on their pack label
    loader = get_default_fibo_loader()
    checked_uris: set[str] = set()
    for pid in STARTER_PACK_IDS:
        ont = get_starter_pack_ontology(pid)
        for concept in ont.concepts:
            for mapping in concept.external_mappings:
                if mapping.standard_uri in checked_uris:
                    continue
                checked_uris.add(mapping.standard_uri)
                item = fibo_source.get_item(mapping.standard_uri)
                assert item is not None
                sc = fibo_source.score(item.label, mapping.standard_uri)
                assert sc >= Decimal("0.6500"), (mapping.standard_uri, item.label, sc)
                assert loader.lookup_by_uri(mapping.standard_uri) is not None
    assert len(checked_uris) == 26


def test_fibo_pack_bytes_deprecated_replaces_and_pii_17_ancestors() -> None:
    """Verify pack_bytes loading, deprecated exclusion + replaces_uris, FIGI override, and 17-ancestor PII."""
    raw = _make_synthetic_pack_bytes()
    src = FiboKnowledgeSource(pack_bytes=raw, verify_integrity=False)
    base = "https://spec.edmcouncil.org/fibo/ontology"

    old_bond = f"{base}/SEC/Debt/Bonds/OldBond"
    bond = f"{base}/SEC/Debt/Bonds/Bond"
    old_prop = f"{base}/FND/Parties/Parties/hasOldProp"
    email_uri = f"{base}/FND/Places/VirtualPlaces/ElectronicMailAddress"
    mat_prop = f"{base}/FND/DatesAndTimes/FinancialDates/hasMaturityDate"

    assert src.get_item(old_bond) is None
    assert src.get_item(old_prop) is None
    assert src.score("old bond", old_bond) == Decimal("0.0000")
    assert all(h.entry_id != old_bond for h in src.recall_by_text("old bond", top_k=10))
    assert src.match_values_or_mentions(["DEPRECATED_IND"]) == []

    bond_item = src.get_item(bond)
    assert bond_item is not None
    assert old_bond in bond_item.replaces_uris

    email_item = src.get_item(email_uri)
    assert email_item is not None
    assert email_item.semantic_context.pii_category_hint == "EMAIL"

    corp_email_item = src.get_item(f"{base}/FND/Places/VirtualPlaces/CorporateEmailAddress")
    assert corp_email_item is not None
    assert corp_email_item.semantic_context.pii_category_hint == "EMAIL"

    corp_email_prop = src.get_item(f"{base}/FND/Places/VirtualPlaces/hasCorporateEmail")
    assert corp_email_prop is not None
    assert corp_email_prop.semantic_context.pii_category_hint == "EMAIL"

    # Reified ObjectProperty targeting ExplicitDate preserves temporal archetype
    mat_item = src.get_item(mat_prop)
    assert mat_item is not None
    assert mat_item.semantic_context.archetype == "temporal"


def test_fibo_real_pack_pii_17_ancestor_subclass_inheritance(
    fibo_source: FiboKnowledgeSource,
) -> None:
    """All real FIBO pack PII subclasses inherit pii_category_hint with zero misses."""
    base = "https://spec.edmcouncil.org/fibo/ontology"
    expected = {
        f"{base}/FND/AgentsAndPeople/Agents/TaxpayerIdentificationNumber": "TAX_ID",
        f"{base}/FND/Organizations/FormalOrganizations/EmployerIdentificationNumber": "TAX_ID",
        f"{base}/FND/Accounting/CurrencyAmount/BasicBankAccountIdentifier": "BANK_ACCOUNT",
        f"{base}/FBC/ProductsAndServices/ClientsAndAccounts/InternationalBankAccountIdentifier": "IBAN",
        f"{base}/FND/Places/Addresses/PrivateMailBoxAddress": "ADDRESS",
        f"{base}/FND/Places/Addresses/PuertoRicoAddress": "ADDRESS",
    }
    by_local = {
        uri_local_name(it.entry_id): it.semantic_context.pii_category_hint
        for it in fibo_source._items.values()
        if it.entry_kind == "class"
    }
    for uri, hint in expected.items():
        loc = uri_local_name(uri)
        assert by_local.get(loc) == hint, (loc, by_local.get(loc), hint)

    am = fibo_source._loader.derived_indexes.ancestors_with_self
    misses = [
        uri_local_name(u)
        for u, it in fibo_source._items.items()
        if it.entry_kind == "class"
        and it.semantic_context.pii_category_hint is None
        and any(uri_local_name(a) in FIBO_PII_MAP for a in am.get(u, ()))
    ]
    assert misses == []


def test_fibo_axioms_inverse_disjoint_functional_and_r5_m11_benchmark() -> None:
    """Test inverse_of, property-only disjoint_with, functional key boost, and R5-M11 individual_lifted."""
    src = FiboKnowledgeSource(pack_bytes=_make_synthetic_pack_bytes(), verify_integrity=False)
    base = "https://spec.edmcouncil.org/fibo/ontology"
    bond = f"{base}/SEC/Debt/Bonds/Bond"
    le = f"{base}/BE/LegalEntities/LegalPersons/LegalEntity"
    issued_by = f"{base}/SEC/Debt/Bonds/issuedBy"
    issues = f"{base}/BE/LegalEntities/LegalPersons/issues"
    figi = f"{base}/SEC/Securities/SecuritiesIdentificationIndividuals/FinancialInstrumentGlobalIdentifier"
    ref_rate = f"{base}/IND/InterestRates/InterestRates/ReferenceInterestRate"

    # R5-M11: Benchmark individual lifted -> code_classifier on categorical string, vetoed on numeric/date
    ind_hits = src.match_values_or_mentions(["SOFR", "USD-SOFR"])
    assert len(ind_hits) == 1
    assert ind_hits[0].entry_id == ref_rate
    assert ind_hits[0].entry_kind == "individual_lifted"

    cat_col = ColumnEvidence(column_name="rate_index", data_type="VARCHAR", sample_values=["SOFR", "USD-SOFR"])
    cat_prof = InferredValueProfile(
        observed_sample_count=2,
        non_null_count=2,
        masked_count=0,
        declared_logical_type="UTF8_STRING",
        inferred_logical_type="UTF8_STRING",
        type_confidence=1.0,
        shape=ColumnShapeStats(profiled_non_null_count=2, distinct_ratio=1.0, looks_like_enum=False),
    )
    res_cat = src.evaluate_structured_candidates(cat_col, cat_prof, ind_hits)
    assert len(res_cat) == 1
    assert res_cat[0].hard_veto is False
    assert res_cat[0].semantic_context is not None
    assert res_cat[0].semantic_context.archetype == "code_classifier"
    assert res_cat[0].shape_range_score == 0.9

    num_prof = cat_prof.model_copy(
        update={"declared_logical_type": "INT64", "inferred_logical_type": "INT64"}
    )
    res_num = src.evaluate_structured_candidates(cat_col, num_prof, ind_hits)
    assert res_num[0].hard_veto is True

    # inverse_of (+0.3 forward vs -0.4 reversed) & property disjoint_with vs identifier FK
    t_ctx = TableReasoningContext(
        table_name="bonds",
        anchor_hypotheses=[
            TableAnchorCandidate(
                entry_id=bond,
                label="bond",
                archetype="agreement",
                origin_kind="reference_standard",
                confidence=0.90,
                suggested_predicate="skos:exactMatch",
            )
        ],
        fk_targets={"issuer_id": le},
    )
    fk_col = ColumnEvidence(column_name="issuer_id", data_type="VARCHAR", is_pk=True, fk_ref="legal_entities.id")
    fk_prof = cat_prof.model_copy(
        update={"shape": ColumnShapeStats(profiled_non_null_count=10, distinct_ratio=1.0)}
    )
    hits = [
        RecallHit(entry_id=issued_by, label="issued by", entry_kind="object_property", raw_score=Decimal("0.8500"), origin_source_id="fibo-synth", origin_kind="reference_standard"),
        RecallHit(entry_id=issues, label="issues", entry_kind="object_property", raw_score=Decimal("0.8500"), origin_source_id="fibo-synth", origin_kind="reference_standard"),
        RecallHit(entry_id=figi, label="financial instrument global identifier", entry_kind="class", raw_score=Decimal("0.8000"), origin_source_id="fibo-synth", origin_kind="reference_standard"),
    ]
    eval_res = {r.entry_id: r for r in src.evaluate_structured_candidates(fk_col, fk_prof, hits, table_context=t_ctx)}
    assert eval_res[issued_by].inverse_direction_score == 0.3
    assert "functional_key_signal" in eval_res[issued_by].support_detail
    assert "disjoint_with_anchor" not in eval_res[issued_by].support_detail
    assert eval_res[issues].inverse_direction_score == -0.4
    assert "disjoint_with_anchor" in eval_res[issues].support_detail
    assert "disjoint_with_anchor" not in eval_res[figi].support_detail


def test_sec_guard_and_raw_pii_stripping_in_steward_context(fibo_source: FiboKnowledgeSource) -> None:
    """SEC expands only in filing/13f/cik tables and never when security_id/seconds/sector is present."""
    raw_note = "Contact officer@example.com or SSN 123-45-6789 regarding <MASKED_PII> and [REDACTED_EMAIL]."
    cleaned = clean_steward_text(raw_note)
    assert cleaned is not None
    assert "officer@example.com" not in cleaned
    assert "123-45-6789" not in cleaned
    assert not has_redacted_placeholder(cleaned)
    assert "[REDACTED_PII]" not in cleaned
    assert MASKED_PII_TOKEN not in cleaned

    uri_clean = strip_uris_and_pii("See https://example.com/a and urn:x:1 for 123-45-6789")
    assert "https://" not in uri_clean and "urn:" not in uri_clean and "123-45-6789" not in uri_clean

    req_13f = GroundingRequest(
        tables=[
            TableEvidence(
                table_name="holdings_13f",
                columns=[
                    ColumnEvidence(column_name="cik", data_type="VARCHAR"),
                    ColumnEvidence(column_name="sec_code", data_type="VARCHAR"),
                ],
                steward_notes=["Quarterly 13F filing submitted to regulator"],
            )
        ],
        policy=_det_policy(),
    )
    ctx_13f = extract_steward_table_context(req_13f.tables[0], req_13f, fibo_source)
    assert ctx_13f.sec_expansion_allowed is True
    exp_13f = fibo_source.expand_query_terms(["sec", "code"], context_text=ctx_13f.context_text)
    assert "securities" in exp_13f and "commission" in exp_13f

    req_sec_id = GroundingRequest(
        tables=[
            TableEvidence(
                table_name="securities_master",
                columns=[
                    ColumnEvidence(column_name="security_id", data_type="VARCHAR"),
                    ColumnEvidence(column_name="sec", data_type="VARCHAR"),
                ],
                steward_notes=["13f reference lookup by security_id"],
            )
        ],
        policy=_det_policy(),
    )
    ctx_sec_id = extract_steward_table_context(req_sec_id.tables[0], req_sec_id, fibo_source)
    assert ctx_sec_id.sec_expansion_allowed is False
    exp_sec_id = fibo_source.expand_query_terms(["sec"], context_text=ctx_sec_id.context_text)
    assert exp_sec_id == ["sec"]

    addr_tbl = TableEvidence(
        table_name="addresses",
        columns=[ColumnEvidence(column_name="address_id", data_type="VARCHAR")],
    )
    addr_req = GroundingRequest(tables=[addr_tbl], policy=_det_policy())
    addr_ctx = extract_steward_table_context(addr_tbl, addr_req, fibo_source)
    addr_sq, _ = build_table_anchor_query(addr_tbl, addr_ctx, fibo_source)
    assert addr_sq == "address"


def test_engine_prescreening_surrogate_and_gate_pairs_and_anchors(fibo_source: FiboKnowledgeSource) -> None:
    """Verify ETL/SCD2 pre-screen, AND-gated technical_surrogate, paired_columns, and anchor reconciliation."""
    engine = ColumnGroundingEngine(fibo_source)
    table = TableEvidence(
        table_name="bonds",
        columns=[
            ColumnEvidence(
                column_name="surrogate_id",
                data_type="BIGINT",
                is_pk=True,
                sample_values=[str(i) for i in range(1, 21)],
            ),
            ColumnEvidence(
                column_name="isin",
                data_type="VARCHAR",
                is_pk=False,
                sample_values=[
                    f"US0378331{i:02d}"
                    + str((10 - sum(
                        int(d) if p % 2 == 1 else (int(d) * 2 // 10 + int(d) * 2 % 10)
                        for p, d in enumerate(reversed("".join(
                            str(ord(c) - 55) if c.isalpha() else c for c in f"US0378331{i:02d}"
                        )))
                    ) % 10) % 10)
                    for i in range(20)
                ],
            ),
            ColumnEvidence(
                column_name="notional_amount",
                data_type="DECIMAL(18,2)",
                sample_values=[1000.0 + i for i in range(20)],
            ),
            ColumnEvidence(
                column_name="settlement_ccy",
                data_type="VARCHAR",
                sample_values=["USD", "EUR"] * 10,
            ),
            ColumnEvidence(
                column_name="effective_from",
                data_type="DATE",
                sample_values=["2026-01-01"] * 20,
            ),
            ColumnEvidence(
                column_name="effective_to",
                data_type="DATE",
                sample_values=["2027-01-01"] * 20,
            ),
            ColumnEvidence(
                column_name="applied_dts",
                data_type="TIMESTAMP",
                sample_values=["2026-01-01T00:00:00Z"] * 20,
            ),
            ColumnEvidence(
                column_name="load_end_dts",
                data_type="TIMESTAMP",
                sample_values=["2026-01-01T00:00:00Z"] * 20,
            ),
            ColumnEvidence(
                column_name="batch_id",
                data_type="BIGINT",
                sample_values=["42"] * 20,
            ),
        ],
    )
    req = GroundingRequest(tables=[table], policy=_det_policy())
    resp = engine.ground(req)
    by_col = {c.column_name: c for c in resp.tables[0].columns}

    assert by_col["surrogate_id"].no_match_category == "technical_surrogate"
    assert by_col["load_end_dts"].no_match_category == "system_audit_metadata"
    assert by_col["batch_id"].no_match_category == "system_audit_metadata"
    assert by_col["applied_dts"].no_match_category != "system_audit_metadata"
    assert by_col["effective_from"].no_match_category != "system_audit_metadata"
    assert by_col["isin"].status == "MATCHED"

    pair_kinds = {p.kind: p.columns for p in resp.tables[0].paired_columns}
    assert pair_kinds["effective_interval"] == ["effective_from", "effective_to"]
    assert pair_kinds["amount_currency"] == ["notional_amount", "settlement_ccy"]

    # Sole id PK without natural BK is NEVER technical_surrogate
    sole_tbl = TableEvidence(
        table_name="batches",
        columns=[
            ColumnEvidence(column_name="id", data_type="BIGINT", is_pk=True, sample_values=[str(i) for i in range(1, 16)]),
            ColumnEvidence(column_name="batch_id", data_type="BIGINT", is_pk=False, sample_values=[str(i) for i in range(1, 16)]),
        ],
    )
    sole_resp = engine.ground(GroundingRequest(tables=[sole_tbl], policy=_det_policy()))
    sole_cols = {c.column_name: c for c in sole_resp.tables[0].columns}
    assert sole_cols["id"].no_match_category != "technical_surrogate"
    assert sole_cols["batch_id"].no_match_category != "system_audit_metadata"

    # Ground() in agentic mode raises AGENT_NOT_CONFIGURED
    with pytest.raises(OpenDVError) as exc_info:
        engine.ground(GroundingRequest(tables=[sole_tbl], policy=GroundingPolicy(mode="agentic")))
    assert exc_info.value.error_code == ErrorCode.AGENT_NOT_CONFIGURED


def test_r5_m6_modes_bic_no_fast_path_and_hazard1_rerank_index(
    fibo_source: FiboKnowledgeSource,
) -> None:
    """Verify R5-M6 symbolic_only vs fast_path, BIC fast-path exclusion, and HAZARD-1 index alignment."""
    engine = ColumnGroundingEngine(fibo_source)
    sec_tbl = TableEvidence(
        table_name="securities",
        columns=[
            ColumnEvidence(
                column_name="cusip",
                data_type="VARCHAR",
                is_pk=True,
                sample_values=["037833100"] * 100,
            ),
        ],
    )
    notes_tbl = TableEvidence(
        table_name="notes_log",
        columns=[
            ColumnEvidence(
                column_name="swift_bic",
                data_type="VARCHAR",
                sample_values=["DEUTDEFF"] * 15,
            ),
            ColumnEvidence(
                column_name="bond_maturity_date",
                data_type="VARCHAR",
                sample_values=["2027-01-15"] * 15,
            ),
        ],
    )
    req = GroundingRequest(tables=[sec_tbl, notes_tbl], policy=GroundingPolicy(mode="agentic"))
    draft = engine.ground_symbolic(req)

    # cusip on securities fast-paths; standalone swift_bic without anchor MUST NOT fast-path
    assert "cusip" in draft.fast_path_mask["securities"]
    assert "swift_bic" not in draft.fast_path_mask["notes_log"]

    # R5-M6: finalize(..., rerank_results=None) emits symbolic_fast_path + symbolic_only
    sym_resp = engine.finalize(req, draft, rerank_results=None)
    sec_cols = {c.column_name: c for c in sym_resp.tables[0].columns}
    notes_cols = {c.column_name: c for c in sym_resp.tables[1].columns}
    assert sec_cols["cusip"].execution_mode_used == "symbolic_fast_path"
    assert notes_cols["swift_bic"].execution_mode_used == "symbolic_only"
    assert sym_resp.mode_used != "deterministic_only"

    # HAZARD-1: inject a hard_veto candidate BEFORE sent index 0 and an unsent tail candidate (-1)
    t_draft = draft.table_drafts["notes_log"]
    mat_draft = next(d for d in t_draft.column_drafts if d.column.column_name == "bond_maturity_date")
    assert mat_draft.rerank_packet is not None and len(mat_draft.rerank_packet.candidates) >= 2
    sent_cands = [c for c in mat_draft.candidates if c.rerank_index is not None and c.rerank_index >= 0]
    target_idx = 1
    chosen_cand = next(c for c in sent_cands if c.rerank_index == target_idx)

    vetoed_head = replace(
        sent_cands[0],
        entry_id="urn:vetoed:head",
        label="vetoed head",
        hard_veto=True,
        rerank_index=None,
    )
    tail_cand = replace(
        sent_cands[0],
        entry_id="urn:tail:unsent",
        label="unsent tail",
        hard_veto=False,
        rerank_index=-1,
        lex_score=0.99,
        shape_range_score=0.99,
    )
    mat_draft.candidates = [vetoed_head, *sent_cands, tail_cand]

    rerank_res = {
        "notes_log": TableRerankResult(
            assignments=[
                TableRerankAssignment(
                    column_name="bond_maturity_date",
                    selected_index=target_idx,
                    abstain=False,
                    confidence=0.92,
                )
            ]
        )
    }
    reranked_resp = engine.finalize(req, draft, rerank_results=rerank_res)
    reranked_cols = {c.column_name: c for c in reranked_resp.tables[1].columns}
    mat_res = reranked_cols["bond_maturity_date"]
    assert mat_res.execution_mode_used == "agentic_reranked"
    assert mat_res.choices[0].entry_id == chosen_cand.entry_id
