"""Unit tests for Phase-1 Layer-1 modeling preferences, PII tokenizer, IR schemas, and kernel."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from opendv.models import (
    BALANCED_BLOCKING_V6B_RULES,
    CEGAR_POLICY_SPECS,
    ERROR_NULL_TOKEN,
    NULL_TOKEN,
    VALID_V6B_RULE_IDS,
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    BridgeHopSpec,
    BusinessKeyPart,
    CertifiedHubSpec,
    ColumnProfile,
    DraftBridgeSpec,
    DraftField,
    DraftHubSpec,
    DraftLinkSpec,
    DraftSatelliteSpec,
    DraftShardIR,
    DraftVaultIR,
    FragilitySurfaceSummary,
    GraphAOntology,
    HashRecipeSpec,
    HorizonActionReq,
    LinkParticipantHashSpec,
    LinkParticipantSpec,
    ModelingPreferences,
    PhysicalLayoutHints,
    ProjectSettingsSnapshot,
    ProjectSettingsUpdateReq,
    SourceProfile,
    TableProfile,
    ValueFreqItem,
    WorkspaceSettings,
    compute_deterministic_energy,
    compute_participant_link_prehash,
    compute_salt_digest,
    compute_workspace_agents_sha256,
    execute_hash_recipe,
    redact_pii_value_frequencies,
    tokenize_pii_scalar,
    tokenize_row_pii,
)


def test_modeling_preferences_policy_specs_and_descriptions() -> None:
    prefs = ModelingPreferences()
    assert prefs.max_survey_questions == 5
    assert prefs.clarify_mode == "auto"
    assert FragilitySurfaceSummary.__module__.startswith("opendv.models")

    for model_cls in (
        ModelingPreferences,
        GraphAOntology,
        SourceProfile,
        TableProfile,
        ColumnProfile,
        DraftVaultIR,
        DraftHubSpec,
        DraftLinkSpec,
        DraftSatelliteSpec,
        DraftBridgeSpec,
        BridgeHopSpec,
        DraftField,
        BusinessKeyPart,
        LinkParticipantSpec,
    ):
        for name, field_info in model_cls.model_fields.items():
            assert field_info.description, f"{model_cls.__name__}.{name} missing Field description"

    assert CEGAR_POLICY_SPECS["balanced"].blocking_v6b_rules == BALANCED_BLOCKING_V6B_RULES
    assert CEGAR_POLICY_SPECS["balanced"].allow_preference_exemptions is True
    assert CEGAR_POLICY_SPECS["strict"].blocking_v6b_rules == VALID_V6B_RULE_IDS
    assert CEGAR_POLICY_SPECS["strict"].allow_preference_exemptions is False
    assert compute_deterministic_energy(2, 3) == 230


def test_pii_tokenizer_idempotent_column_wide_and_freq_redaction() -> None:
    salt = b"test-project-salt-32-bytes-long!"
    assert len(compute_salt_digest(salt)) == 16

    tok1 = tokenize_pii_scalar("ops@beacon.io", salt=salt)
    assert tok1 is not None and tok1.startswith("<PII:") and tok1.endswith(">")
    assert tokenize_pii_scalar(tok1, salt=salt) == tok1
    assert tokenize_pii_scalar(None, salt=salt) is None

    row = tokenize_row_pii(
        {"email": "custom_non_regex", "notes": "reach ops@beacon.io", "tier": "AAA"},
        pii_columns={"email"},
        salt=salt,
    )
    assert str(row["email"]).startswith("<PII:")
    assert str(row["notes"]).startswith("<PII:")
    assert row["tier"] == "AAA"

    freqs = [ValueFreqItem(value="ops@beacon.io", count=4), ValueFreqItem(value="USD", count=9)]
    redacted_col = redact_pii_value_frequencies(freqs, is_pii_col=True)
    assert [f.value for f in redacted_col] == ["<MASKED_PII_0>", "<MASKED_PII_1>"]
    redacted_val = redact_pii_value_frequencies(freqs, is_pii_col=False)
    assert [f.value for f in redacted_val] == ["<MASKED_PII_0>", "USD"]


def test_workspace_override_hash_ignores_root_agents_md_and_normalizes_crlf(tmp_path: Path) -> None:
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text("# root repo instructions v1\n", encoding="utf-8")
    assert compute_workspace_agents_sha256(tmp_path) is None

    guide = tmp_path / ".opendv" / "agents" / "dv2-modeling-guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_bytes(b"line1\r\nline2\r\n")
    digest_crlf = compute_workspace_agents_sha256(tmp_path)
    assert digest_crlf is not None

    root_agents.write_text("# edited unrelated root file\n", encoding="utf-8")
    guide.write_bytes(b"line1\nline2\n")
    assert compute_workspace_agents_sha256(tmp_path) == digest_crlf


def test_project_settings_update_and_inv05_hub_validation() -> None:
    ws = WorkspaceSettings()
    assert "business_context_hints" not in WorkspaceSettings.model_fields
    ps = ProjectSettingsSnapshot()
    assert ps.proving_sample_rows == 200
    assert ps.real_row_proving is True

    patch = ProjectSettingsUpdateReq(proving_sample_rows=500)
    assert patch.model_fields_set == {"proving_sample_rows"}
    with pytest.raises(ValidationError):
        ProjectSettingsUpdateReq(cegar_policy="strict", v7_critic_tier="STANDARD_WORKER")

    with pytest.raises(ValidationError, match="INV-05"):
        DraftHubSpec(
            hub_name="hub_cust",
            key_tier="TIER_2_ENTERPRISE_SOR",
            attributes=[
                DraftField(name="tier", logical_type="UTF8_STRING", nullable=True, is_pii=False)
            ],
        )

    with pytest.raises(ValidationError, match="INV-05"):
        CertifiedHubSpec(
            hub_name="hub_cust",
            key_tier="TIER_2_ENTERPRISE_SOR",
            physical_hints=PhysicalLayoutHints(),
            attributes=[
                DraftField(name="tier", logical_type="UTF8_STRING", nullable=True, is_pii=False)
            ],
        )

    shard = DraftShardIR(cluster_id="c1")
    assert not hasattr(shard, "bridges")
    bridge = DraftBridgeSpec(
        bridge_name="brg_order_acct",
        root_hub="hub_order",
        hops=[BridgeHopSpec(link_name="lnk_order_acct")],
    )
    draft = DraftVaultIR(project_id="p1", ontology_version="1", wave_number=1, bridges=[bridge])
    assert draft.bridges[0].bridge_name == "brg_order_acct"
    HorizonActionReq(action="revert")


def test_participant_link_hashing_sentinels_bkcc_and_degenerates() -> None:
    recipe = HashRecipeSpec(
        target_col="hk_lnk_trade_acct",
        recipe_kind="LINK_HK",
        source_columns=["trade_id", "acct_code", "acct_sub", "seq_note"],
        sort_alphabetically=False,
        participant_specs=[
            LinkParticipantHashSpec(
                hub_name="hub_trade",
                role_name="trade",
                bkcc_literal="OMS",
                bkcc_col="ignored_col",
                key_tier="TIER_1_GLOBAL_STANDARD",
                source_columns=["trade_id"],
                is_nullable=False,
            ),
            LinkParticipantHashSpec(
                hub_name="hub_acct",
                role_name="acct",
                bkcc_literal="FALLBACK_BKCC",
                bkcc_col="acct_bkcc",
                key_tier="TIER_3_LOCAL_SCOPED",
                source_columns=["acct_code", "acct_sub"],
                is_nullable=True,
            ),
            LinkParticipantHashSpec(
                hub_name="",
                role_name="seq",
                source_columns=["seq_note"],
                is_degenerate=True,
            ),
        ],
    )

    # 1) Partial-NULL composite BK keeps resolved BKCC ('US_BOOK'), sets missing sub-col to '-1:^^',
    #    suppresses bkcc_col on TIER_1_GLOBAL_STANDARD -> 'GLOBAL', and preserves case on degenerate_keys.
    prehash_comp, _ = compute_participant_link_prehash(
        recipe,
        {
            "trade_id": "t-100",
            "ignored_col": "SHOULD_IGNORE",
            "acct_bkcc": "us_book",
            "acct_code": "a-1",
            "acct_sub": None,
            "seq_note": "  FixOrd  ",
        },
    )
    assert prehash_comp == f"6:GLOBAL||5:T-100||7:US_BOOK||3:A-1||{NULL_TOKEN}||6:FixOrd"

    # 2) Nullable all-blank participant (-2:^^) differs from non-nullable unknown (-1:^^) -> injective LINK_HK
    hk_err_null = execute_hash_recipe(
        recipe,
        {"trade_id": "t-100", "acct_code": None, "acct_sub": None, "seq_note": None},
    )
    non_null_recipe = recipe.model_copy(
        update={
            "participant_specs": [
                recipe.participant_specs[0],
                recipe.participant_specs[1].model_copy(update={"is_nullable": False}),
                recipe.participant_specs[2],
            ]
        }
    )
    hk_unk_null = execute_hash_recipe(
        non_null_recipe,
        {"trade_id": "t-100", "acct_code": None, "acct_sub": None, "seq_note": None},
    )
    assert hk_err_null != hk_unk_null
    pre_err, _ = compute_participant_link_prehash(
        recipe,
        {"trade_id": "t-100", "acct_code": None, "acct_sub": None, "seq_note": None},
    )
    assert f"{ERROR_NULL_TOKEN}||{ERROR_NULL_TOKEN}||{ERROR_NULL_TOKEN}||7:UNKNOWN" in pre_err

    # 3) All non-degenerate Hub participants blank short-circuit even with non-blank degenerate key
    assert (
        execute_hash_recipe(
            recipe,
            {"trade_id": None, "acct_code": None, "acct_sub": None, "seq_note": "FixOrd"},
        )
        == ZERO_KEY_UNKNOWN
    )
    all_optional_recipe = recipe.model_copy(
        update={
            "participant_specs": [
                recipe.participant_specs[0].model_copy(update={"is_nullable": True}),
                recipe.participant_specs[1],
                recipe.participant_specs[2],
            ]
        }
    )
    assert (
        execute_hash_recipe(
            all_optional_recipe,
            {"trade_id": None, "acct_code": None, "acct_sub": None, "seq_note": "FixOrd"},
        )
        == ZERO_KEY_ERROR
    )
