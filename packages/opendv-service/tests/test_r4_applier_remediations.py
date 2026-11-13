"""Regression tests for R4-A Ontology Applier remediations (P05a::MAJ-01 & P05a::MAJ-02).

Covers:
- `P05a::MAJ-02`: `_is_placeholder_definition` exact placeholder matching so real definitions
  beginning with `'Canonical'` are never overwritten by `ReviseDescriptionOp` or `AddConceptOp`.
- `P05a::MAJ-01`: Complete Wave-1 `_legacy_v1_projection` (`metadata.benchmark == "PAR-ONT-HOLD"`)
  fidelity across `AddConceptOp`, `AddRelationshipOp`, and `AddSourceHintOp`.
- Canonical SHA-256 verification for `examples/private_equity/pe_graph_a_ontology.yaml` and GT-06.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddRelationshipOp,
    AddSourceHintOp,
    OntologyDelta,
    ReviseDescriptionOp,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.applier import (
    _is_placeholder_definition,
    apply,
    apply_ontology_delta,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _base_ontology(*concepts: CanonicalBusinessConcept) -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="pe_asset_management",
        enterprise_owner="Test Steward",
        canonical_business_concepts=list(concepts),
        canonical_relationships=[],
        reference_enumerations=[],
    )


class TestPlaceholderDefinitionGuard:
    """Regression suite for P05a::MAJ-02 (`_is_placeholder_definition`)."""

    @pytest.mark.parametrize(
        "placeholder_defn",
        [
            None,
            "",
            "   \t\n  ",
            "Canonical business concept: Counterparty.",
            "Canonical business concept: Counterparty",
            "  Canonical business concept: Counterparty.  ",
            "Canonical concept for Counterparty.",
            "Canonical concept for Counterparty",
        ],
    )
    def test_exact_placeholders_detected(self, placeholder_defn: str | None) -> None:
        assert _is_placeholder_definition(placeholder_defn, "Counterparty") is True

    @pytest.mark.parametrize(
        "real_defn",
        [
            "Canonical record of a counterparty entering ISDA master agreements.",
            "Canonical business concept: Counterparty — legally registered entity.",
            "Canonical concept for Counterparty in bilateral OTC derivatives.",
            "Canonical business concept: OtherConcept.",
            "Canonical concept for OtherConcept.",
            "Canonical entity representing an institutional limited partner.",
        ],
    )
    def test_real_definitions_starting_with_canonical_are_not_placeholders(
        self, real_defn: str
    ) -> None:
        assert _is_placeholder_definition(real_defn, "Counterparty") is False

    def test_revise_description_preserves_real_definition_starting_with_canonical(self) -> None:
        real_defn = "Canonical record of a counterparty entering ISDA master agreements."
        base = _base_ontology(
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:counterparty",
                concept_name="Counterparty",
                elm_archetype="WHO_COUNTERPARTY",
                definition=real_defn,
                target_hub="hub_counterparty",
            )
        )
        base_sha = compute_ontology_sha256(base)
        delta = OntologyDelta(
            delta_id="delta-revise-real",
            parent_sha256=base_sha,
            domain_namespace="pe_asset_management",
            base_ontology_version="1.0.0",
            operations=[
                ReviseDescriptionOp(
                    op_id="op-rev-1",
                    concept_uri="urn:ontology:entity:counterparty",
                    description="Updated operational notes for onboarding.",
                )
            ],
        )

        updated = apply(base, delta)
        cp = updated.canonical_business_concepts[0]
        assert cp.description == "Updated operational notes for onboarding."
        assert cp.definition == real_defn

    def test_revise_description_replaces_both_v1_and_v2_generated_placeholders(self) -> None:
        for placeholder in (
            "Canonical business concept: Counterparty.",
            "Canonical concept for Counterparty.",
            "Canonical business concept: Counterparty",
            "Canonical concept for Counterparty",
        ):
            base = _base_ontology(
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:counterparty",
                    concept_name="Counterparty",
                    elm_archetype="WHO_COUNTERPARTY",
                    definition=placeholder,
                    target_hub="hub_counterparty",
                )
            )
            base_sha = compute_ontology_sha256(base)
            delta = OntologyDelta(
                delta_id="delta-revise-placeholder",
                parent_sha256=base_sha,
                domain_namespace="pe_asset_management",
                base_ontology_version="1.0.0",
                operations=[
                    ReviseDescriptionOp(
                        op_id="op-rev-2",
                        concept_uri="urn:ontology:entity:counterparty",
                        description="Authoritative counterparty definition.",
                    )
                ],
            )
            updated = apply(base, delta)
            cp = updated.canonical_business_concepts[0]
            assert cp.definition == "Authoritative counterparty definition."

    def test_add_concept_merge_preserves_real_definition_starting_with_canonical(self) -> None:
        curated_defn = "Canonical business concept: Counterparty — curated by Legal."
        base = _base_ontology(
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:counterparty",
                concept_name="Counterparty",
                elm_archetype="WHO_COUNTERPARTY",
                definition=curated_defn,
                target_hub="hub_counterparty",
            )
        )
        base_sha = compute_ontology_sha256(base)
        delta = OntologyDelta(
            delta_id="delta-merge-real",
            parent_sha256=base_sha,
            domain_namespace="pe_asset_management",
            base_ontology_version="1.0.0",
            operations=[
                AddConceptOp(
                    op_id="op-merge-1",
                    concept_uri="urn:ontology:entity:counterparty",
                    concept_name="Counterparty",
                    definition="Attempted overwrite via additive merge.",
                    synonyms=["Trading Counterparty"],
                )
            ],
        )

        updated = apply(base, delta)
        cp = updated.canonical_business_concepts[0]
        assert cp.definition == curated_defn
        assert cp.synonyms == ["Trading Counterparty"]


class TestLegacyV1ProjectionFidelity:
    """Regression suite for P05a::MAJ-01 (`_legacy_v1_projection` / `PAR-ONT-HOLD`)."""

    def test_par_ont_hold_projection_suppresses_wave2_fields_and_preserves_wave1_defaults(
        self,
    ) -> None:
        existing_party = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:party",
            concept_name="Party",
            elm_archetype="WHO_LEGAL_ENTITY",
            definition="Canonical concept for Party.",
            target_hub="hub_party",
        )
        existing_fund = CanonicalBusinessConcept(
            concept_uri="urn:ontology:entity:fund",
            concept_name="Fund",
            elm_archetype="WHO_WHAT_INVESTMENT_VEHICLE",
            definition="Canonical concept for Fund.",
            target_hub="hub_fund",
        )
        base = _base_ontology(existing_party, existing_fund)
        base_sha = compute_ontology_sha256(base)

        ops = [
            # New concept with empty definition + Wave-2 optional fields
            AddConceptOp(
                op_id="op-v1-add-new",
                concept_uri="urn:ontology:entity:deal_sponsor",
                concept_name="Deal Sponsor",
                elm_archetype="WHO_EXTERNAL_SPONSOR",
                target_hub="hub_deal_sponsor",
                parent_concept_uri="urn:ontology:entity:party",
                synonyms=["Sponsor", "Lead GP"],
                external_mappings=[
                    {
                        "scheme": "fibo",
                        "target_uri": "https://spec.edmcouncil.org/fibo/ontology/BE/Sponsor",
                        "predicate": "skos:exactMatch",
                        "confidence": 0.93,
                    }
                ],
                attributes=["sponsor_name", "jurisdiction"],
                metadata={"source_wave": "wave_2"},
                source_hints=["crm.sponsors.id"],
            ),
            # Existing concept merge attempting to populate Wave-2 optional fields
            AddConceptOp(
                op_id="op-v1-merge-existing",
                concept_uri="urn:ontology:entity:fund",
                concept_name="Fund",
                parent_concept_uri="urn:ontology:entity:party",
                synonyms=["Vehicle"],
                external_mappings=[
                    {
                        "scheme": "fibo",
                        "target_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Fund",
                        "predicate": "skos:exactMatch",
                    }
                ],
                metadata={"extra": True},
                source_hints=["erp.funds.fund_id"],
            ),
            # Relationship without explicit grain_semantics
            AddRelationshipOp(
                op_id="op-v1-rel",
                relationship_uri="urn:ontology:relationship:fund_deal_sponsor",
                relationship_name="FundDealSponsor",
                target_link="lnk_fund_deal_sponsor",
                endpoints=[
                    "urn:ontology:entity:deal_sponsor",
                    "urn:ontology:entity:fund",
                ],
                metadata={"rel_meta": "suppressed_in_v1"},
            ),
            # AddSourceHintOp must be a complete no-op under PAR-ONT-HOLD
            AddSourceHintOp(
                op_id="op-v1-hint",
                concept_uri="urn:ontology:entity:deal_sponsor",
                source_system="crm",
                table_name="tbl_sponsor",
                column_name="sponsor_id",
            ),
        ]

        legacy_delta = OntologyDelta(
            delta_id="delta-par-ont-hold",
            parent_sha256=base_sha,
            domain_namespace="pe_asset_management",
            base_ontology_version="1.0.0",
            operations=ops,
            metadata={"benchmark": "PAR-ONT-HOLD"},
        )

        # apply_ontology_delta() honors metadata.benchmark; direct apply() requires explicit flag
        legacy_via_service = apply_ontology_delta(base, legacy_delta)
        legacy_via_apply = apply(base, legacy_delta, _legacy_v1_projection=True)
        assert compute_ontology_sha256(legacy_via_service) == compute_ontology_sha256(
            legacy_via_apply
        )

        by_uri = {c.concept_uri: c for c in legacy_via_service.canonical_business_concepts}
        sponsor = by_uri["urn:ontology:entity:deal_sponsor"]
        fund = by_uri["urn:ontology:entity:fund"]

        # Wave-1 default definition + suppressed Wave-2 optional fields
        assert sponsor.definition == "Canonical concept for Deal Sponsor."
        assert sponsor.description is None
        assert sponsor.attributes is None
        assert sponsor.parent_concept_uri is None
        assert sponsor.synonyms is None
        assert sponsor.external_mappings is None
        assert sponsor.metadata is None
        assert sponsor.source_hints is None

        assert fund.parent_concept_uri is None
        assert fund.synonyms is None
        assert fund.external_mappings is None
        assert fund.metadata is None
        assert fund.source_hints is None

        # Wave-1 default grain_semantics (no trailing period) & suppressed metadata
        rel = legacy_via_service.canonical_relationships[0]
        assert rel.grain_semantics == "Unit-of-work link lnk_fund_deal_sponsor"
        assert rel.metadata is None

        # Contrast with standard Wave-2 mode (_legacy_v1_projection is False)
        modern_delta = legacy_delta.model_copy(update={"metadata": {"author": "studio"}})
        modern_target = apply_ontology_delta(base, modern_delta)
        modern_by_uri = {c.concept_uri: c for c in modern_target.canonical_business_concepts}
        modern_sponsor = modern_by_uri["urn:ontology:entity:deal_sponsor"]

        assert modern_sponsor.definition == "Canonical business concept: Deal Sponsor."
        assert modern_sponsor.parent_concept_uri == "urn:ontology:entity:party"
        assert modern_sponsor.synonyms == ["Sponsor", "Lead GP"]
        assert (
            modern_sponsor.external_mappings is not None
            and len(modern_sponsor.external_mappings) == 1
        )
        assert modern_sponsor.metadata == {"source_wave": "wave_2"}
        assert modern_sponsor.source_hints == [
            "crm.sponsors.id",
            "crm.tbl_sponsor.sponsor_id",
        ]
        modern_rel = modern_target.canonical_relationships[0]
        assert modern_rel.grain_semantics == "Unit-of-work link lnk_fund_deal_sponsor."
        assert modern_rel.metadata == {"rel_meta": "suppressed_in_v1"}

    def test_apply_and_studio_commit_preserve_wave2_fields_with_par_ont_hold_metadata(
        self, tmp_path: Path
    ) -> None:
        """P05a::MAJ-01: Studio apply() & commit_delta() preserve Wave-2 fields on PAR-ONT-HOLD."""
        from opendv_service.studio.atomic_commit import AtomicCommitService
        from opendv_service.studio.session_manager import StudioSessionManager
        from opendv_service.studio.workspace_security import WorkspaceSecurityManager

        fibo_map = {
            "scheme": "fibo",
            "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
            "predicate": "skos:exactMatch",
            "confidence": 0.95,
        }
        party_map = {
            "scheme": "fibo",
            "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractParty",
            "predicate": "skos:exactMatch",
            "confidence": 0.95,
        }

        studio_ops = [
            AddConceptOp(
                op_id="op-party",
                concept_uri="urn:ontology:entity:party",
                concept_name="Contract Party",
                elm_archetype="WHO_LEGAL_ENTITY",
                target_hub="hub_party",
                external_mappings=[party_map],
            ),
            AddConceptOp(
                op_id="op-acct",
                concept_uri="urn:ontology:entity:account",
                concept_name="Account",
                elm_archetype="WHAT_FINANCIAL_ACCOUNT",
                target_hub="hub_account",
                description="Extended Wave-2 account description.",
                parent_concept_uri="urn:ontology:entity:party",
                synonyms=["Client Account", "Custody Account"],
                external_mappings=[fibo_map],
                attributes=["account_number", "currency_code"],
                metadata={"curated_by": "studio"},
                source_hints=["core.accounts.account_id"],
            ),
            AddRelationshipOp(
                op_id="op-rel-party-acct",
                relationship_uri="urn:ontology:relationship:party_holds_account",
                relationship_name="PartyHoldsAccount",
                target_link="lnk_party_account",
                endpoints=[
                    "urn:ontology:entity:party",
                    "urn:ontology:entity:account",
                ],
                cardinality="one_to_many",
                metadata={"link_tier": "core"},
            ),
            AddSourceHintOp(
                op_id="op-hint-acct",
                concept_uri="urn:ontology:entity:account",
                source_system="ledger",
                table_name="tbl_account",
                column_name="acct_pk",
            ),
        ]

        sec = WorkspaceSecurityManager(studio_root=tmp_path / "studio")
        mgr = StudioSessionManager(studio_root=tmp_path / "studio", security_manager=sec)
        svc = AtomicCommitService(session_manager=mgr, security_manager=sec)
        meta = mgr.create_session("sess-par-ont-hold-safe")
        genesis_ont = GraphAOntology.model_validate_json(
            (
                mgr.get_session_dir("sess-par-ont-hold-safe")
                / "versions"
                / "v0.0.0"
                / "ontology.json"
            ).read_text(encoding="utf-8")
        )

        studio_delta = OntologyDelta(
            delta_id="d-par-hold-studio",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=meta.head_sha256,
            operations=studio_ops,
            metadata={"benchmark": "PAR-ONT-HOLD"},
        )

        # 1. Direct apply() preserves all Wave-2 fields despite metadata.benchmark == 'PAR-ONT-HOLD'
        direct_out = apply(genesis_ont, studio_delta)
        acct_direct = {c.concept_uri: c for c in direct_out.canonical_business_concepts}[
            "urn:ontology:entity:account"
        ]
        assert acct_direct.description == "Extended Wave-2 account description."
        assert acct_direct.parent_concept_uri == "urn:ontology:entity:party"
        assert acct_direct.synonyms == ["Client Account", "Custody Account"]
        assert acct_direct.external_mappings is not None and len(acct_direct.external_mappings) == 1
        assert acct_direct.attributes is not None and len(acct_direct.attributes) == 2
        assert acct_direct.metadata == {"curated_by": "studio"}
        assert acct_direct.source_hints == [
            "core.accounts.account_id",
            "ledger.tbl_account.acct_pk",
        ]
        rel_direct = direct_out.canonical_relationships[0]
        assert str(rel_direct.cardinality) == "one_to_many"
        assert rel_direct.metadata == {"link_tier": "core"}

        # 2. AtomicCommitService.commit_delta() succeeds and preserves all Wave-2 fields
        res = svc.commit_delta("sess-par-ont-hold-safe", studio_delta)
        assert res.version == "v1.0.0"
        committed_ont = GraphAOntology.model_validate_json(
            (
                mgr.get_session_dir("sess-par-ont-hold-safe")
                / "versions"
                / "v1.0.0"
                / "ontology.json"
            ).read_text(encoding="utf-8")
        )
        acct_committed = {c.concept_uri: c for c in committed_ont.canonical_business_concepts}[
            "urn:ontology:entity:account"
        ]
        assert acct_committed.description == "Extended Wave-2 account description."
        assert acct_committed.parent_concept_uri == "urn:ontology:entity:party"
        assert acct_committed.synonyms == ["Client Account", "Custody Account"]
        assert (
            acct_committed.external_mappings is not None
            and len(acct_committed.external_mappings) == 1
        )
        assert acct_committed.attributes is not None and len(acct_committed.attributes) == 2
        assert acct_committed.source_hints == [
            "core.accounts.account_id",
            "ledger.tbl_account.acct_pk",
        ]
        rel_committed = committed_ont.canonical_relationships[0]
        assert str(rel_committed.cardinality) == "one_to_many"
        assert rel_committed.metadata == {"link_tier": "core"}

    def test_pe_graph_a_ontology_canonical_sha256_preserved(self) -> None:
        pe_yaml_path = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
        raw_data = yaml.safe_load(pe_yaml_path.read_text(encoding="utf-8"))
        pe_ont = GraphAOntology.model_validate(raw_data)
        assert (
            compute_ontology_sha256(pe_ont)
            == "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81"
        )
