"""Unit tests for Asset Management Core Spine, Vertical Packs & Presets."""

import pytest

from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_sources.asset_management import (
    AssetManagementStarterPackRegistry,
    compose_asset_management_ontology,
    compute_pack_hash,
    get_all_domains_pack,
    get_core_spine_pack,
    get_custody_clearing_pack,
    get_fixed_income_rates_pack,
    get_multi_asset_ibor_pack,
    get_private_credit_direct_lending_pack,
    get_private_equity_buyout_pack,
    get_public_equities_oms_pack,
)


def test_core_spine_pack_invariants() -> None:
    spine = get_core_spine_pack()
    assert isinstance(spine, GraphAOntology)
    assert len(spine.canonical_business_concepts) == 6
    assert len(spine.canonical_relationships) == 6

    expected_concept_names = {
        "LegalEntity",
        "Account",
        "Agreement",
        "FinancialInstrument",
        "Transaction",
        "Document",
    }
    actual_names = {c.concept_name for c in spine.canonical_business_concepts}
    assert actual_names == expected_concept_names

    sha = compute_pack_hash(spine)
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_all_six_vertical_packs_load() -> None:
    loaders = [
        ("public_equities_oms", get_public_equities_oms_pack, 4, 4),
        ("fixed_income_rates", get_fixed_income_rates_pack, 4, 4),
        ("multi_asset_ibor", get_multi_asset_ibor_pack, 4, 4),
        ("private_equity_buyout", get_private_equity_buyout_pack, 5, 4),
        ("private_credit_direct_lending", get_private_credit_direct_lending_pack, 4, 4),
        ("custody_clearing", get_custody_clearing_pack, 4, 3),
    ]

    for _name, loader_fn, exp_vertical_concepts, exp_vertical_rels in loaders:
        pack = loader_fn(include_spine=True)
        assert isinstance(pack, GraphAOntology)
        # Total concepts = spine(6) + vertical
        assert len(pack.canonical_business_concepts) == 6 + exp_vertical_concepts
        # Total links = spine(6) + vertical
        assert len(pack.canonical_relationships) == 6 + exp_vertical_rels


def test_all_domains_preset_composition_counts() -> None:
    all_domains = get_all_domains_pack()
    assert isinstance(all_domains, GraphAOntology)
    assert len(all_domains.canonical_business_concepts) == 31
    assert len(all_domains.canonical_relationships) == 29
    assert len(all_domains.reference_enumerations) == 7

    # Hash determinism
    sha1 = compute_ontology_sha256(all_domains)
    sha2 = compute_pack_hash(all_domains)
    assert sha1 == sha2
    assert len(sha1) == 64


def test_registry_discovery_and_loader() -> None:
    packs = AssetManagementStarterPackRegistry.list_packs()
    assert len(packs) == 8

    resp = AssetManagementStarterPackRegistry.list_response()
    assert len(resp.packs) == 8
    assert resp.default_preset == AssetManagementPackId.ALL_DOMAINS

    # Load each pack by ID
    for p in AssetManagementPackId:
        loaded = AssetManagementStarterPackRegistry.load_pack(p)
        assert isinstance(loaded, GraphAOntology)
        meta = AssetManagementStarterPackRegistry.get_metadata(p)
        assert meta.pack_id == p

    # Invalid ID
    with pytest.raises(ValueError):
        AssetManagementStarterPackRegistry.load_pack("non_existent_pack")


def test_custom_composition() -> None:
    # Compose just spine + public equities
    custom = compose_asset_management_ontology(
        include_packs=["public_equities_oms"],
        domain_namespace="custom_equities",
    )
    assert len(custom.canonical_business_concepts) == 10  # 6 spine + 4 equities
    assert len(custom.canonical_relationships) == 10  # 6 spine + 4 equities
    assert custom.domain_namespace == "custom_equities"


def test_sources_config_magic_numbers_m7() -> None:
    """Verify M7: magic numbers like bernoulli_sample_percentage are configured in SourcesConfig."""
    from opendv_sources.config import SourcesConfig, get_source_defaults

    cfg = get_source_defaults()
    assert isinstance(cfg, SourcesConfig)
    assert cfg.bernoulli_sample_percentage == 10.0


def test_vertical_packs_submodule_token_budgets_m9() -> None:
    """Verify M9: vertical_packs submodules strictly maintain <5,000 token budget."""
    from pathlib import Path

    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    submodule_dir = (
        Path(__file__).parents[1] / "src" / "opendv_sources" / "asset_management" / "vertical_packs"
    )
    assert submodule_dir.is_dir()

    python_files = list(submodule_dir.glob("*.py"))
    assert len(python_files) >= 6

    for py_file in python_files:
        content = py_file.read_text(encoding="utf-8")
        tokens = len(enc.encode(content))
        assert tokens < 5000, f"{py_file.name} exceeded 5,000 token limit: {tokens} tokens"

    # Also verify vertical_packs/__init__.py is small dispatcher
    init_file = submodule_dir / "__init__.py"
    init_tokens = len(enc.encode(init_file.read_text(encoding="utf-8")))
    assert init_tokens < 1000
