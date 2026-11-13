"""Unit and integration test suite for scripts/vendor_fibo.py.

Verifies:
1. CLI argument parsing, flags, and help menus.
2. OWL/RDF extraction logic across all 6 core modules (FND, BE, FBC, SEC, LOAN, IND).
3. Canonical RFC 8785 serialization and SHA-256 deterministic hashing.
4. Strict resource budget assertions (< 4.0 MB disk, < 18.0 MB memory, < 70 ms cold load).
5. Security guards: path-traversal, symlink escapes, sibling-folder escapes (MAJ-01).
6. Safe atomic compilation and prior pack preservation on failure (MAJ-02).
7. Production pack verification, alias identity, and fixture rejection (MAJ-03).
8. Provenance manifest and git commit validation (MAJ-01, MAJ-04).
9. Real pack verification against baseline invariants.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import rdflib

from opendv_contracts.jcs import dumps_jcs_bytes

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.vendor_fibo import (  # noqa: E402
    CORE_MODULES,
    DEFAULT_COMPILED_AT,
    DEFAULT_OUTPUT_DIR,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    MAX_DISK_SIZE_BYTES,
    MAX_LOAD_LATENCY_MS,
    MAX_MEMORY_BYTES,
    UPSTREAM_COMMIT,
    UPSTREAM_TAG,
    ArchiveSecurityError,
    _extract_annotations,
    _hash_file_sha256,
    _load_baseline_fibo_config,
    _pick_best_literal,
    compile_fibo_pack,
    create_mock_fibo_graph,
    ensure_fibo_source,
    extract_fibo_graph,
    get_fibo_module,
    load_fibo_from_directory,
    main,
    safe_extract_archive,
    verify_pack,
)


@pytest.fixture
def sample_ontology_graph() -> rdflib.Graph:
    """Fixture returning a minimal OWL ontology graph exercising all 6 modules."""
    return create_mock_fibo_graph()


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --help flag prints usage information and exits with 0."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "FIBO compilation and verification script" in captured.out
    assert "--output-dir" in captured.out
    assert "--mock-fixture" in captured.out
    assert "--verify-pack" in captured.out
    assert "--verify-only" in captured.out


def test_get_fibo_module_resolution() -> None:
    """Verify correct classification of URIs into the 6 core modules and rejection of non-FIBO."""
    fnd_uri = "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/Agreement"
    be_uri = "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/LegalEntity"
    fbc_uri = "https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/Security"
    sec_uri = "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/Share"
    loan_uri = "https://spec.edmcouncil.org/fibo/ontology/LOAN/LoansGeneral/Loans/Loan"
    ind_uri = "https://spec.edmcouncil.org/fibo/ontology/IND/Indicators/Indicators/FinancialIndex"

    assert get_fibo_module(fnd_uri) == "FND"
    assert get_fibo_module(be_uri) == "BE"
    assert get_fibo_module(fbc_uri) == "FBC"
    assert get_fibo_module(sec_uri) == "SEC"
    assert get_fibo_module(loan_uri) == "LOAN"
    assert get_fibo_module(ind_uri) == "IND"

    # Non-FIBO URIs containing module letters must be strictly rejected (MIN-01)
    assert get_fibo_module("https://example.org/IND/index") is None
    assert get_fibo_module("https://example.com/BE/company") is None
    assert get_fibo_module("https://www.omg.org/spec/Commons/Agent") is None
    assert get_fibo_module("urn:custom:concept") is None


def test_extraction_logic(sample_ontology_graph: rdflib.Graph) -> None:
    """Verify extraction of classes, properties, labels, definitions, synonyms, and subClassOf."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)

    assert len(classes) >= 12
    modules_found = {c["module"] for c in classes.values()}
    assert modules_found == set(CORE_MODULES)

    agreement_uri = "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/Agreement"
    assert agreement_uri in classes
    agreement = classes[agreement_uri]
    assert agreement["label"] == "agreement"
    assert "enforceable understanding" in agreement["definition"]
    assert "compact" in agreement["synonyms"]
    assert agreement["module"] == "FND"
    assert agreement["sub_class_of"] == []

    contract_uri = "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract"
    assert contract_uri in classes
    contract = classes[contract_uri]
    assert contract["label"] == "contract"
    assert agreement_uri in contract["sub_class_of"]

    assert len(properties) >= 6
    prop_modules = {p["module"] for p in properties.values()}
    assert prop_modules == set(CORE_MODULES)

    has_party_uri = "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/hasParty"
    assert has_party_uri in properties
    has_party = properties[has_party_uri]
    assert has_party["label"] == "has party"
    assert has_party["property_type"] == "ObjectProperty"
    assert agreement_uri in has_party["domain"]


def test_canonical_jcs_pack_serialization(sample_ontology_graph: rdflib.Graph) -> None:
    """Verify RFC 8785 canonical JSON serialization, hashing, and filename pattern."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    raw_bytes, _pack, sha256_full, sha256_8 = compile_fibo_pack(
        classes,
        properties,
        upstream_tag=UPSTREAM_TAG,
        upstream_commit=UPSTREAM_COMMIT,
        compiled_at=DEFAULT_COMPILED_AT,
        modules=CORE_MODULES,
    )

    assert isinstance(raw_bytes, bytes)
    assert len(sha256_full) == 64
    assert len(sha256_8) == 8
    assert sha256_full.startswith(sha256_8)

    # Re-serialization of parsed dict must yield bit-for-bit identical bytes (RFC 8785)
    parsed = json.loads(raw_bytes)
    assert dumps_jcs_bytes(parsed) == raw_bytes

    assert parsed["metadata"]["upstream_tag"] == UPSTREAM_TAG
    assert parsed["metadata"]["upstream_commit"] == UPSTREAM_COMMIT
    assert parsed["metadata"]["modules"] == sorted(list(CORE_MODULES))
    assert parsed["metadata"]["pack_id"] == "fibo-core"


def test_determinism_across_runs(sample_ontology_graph: rdflib.Graph) -> None:
    """Verify that multiple compiles of the same graph produce identical SHA-256 hashes (MIN-07)."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    bytes1, _, hash1, _ = compile_fibo_pack(classes, properties)
    bytes2, _, hash2, _ = compile_fibo_pack(classes, properties)

    assert bytes1 == bytes2
    assert hash1 == hash2


def test_budget_verification_success(tmp_path: Path, sample_ontology_graph: rdflib.Graph) -> None:
    """Verify budget verification passes for a well-formed compiled pack."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    raw_bytes, _, _, sha256_8 = compile_fibo_pack(classes, properties)

    pack_path = tmp_path / f"fibo-core-{sha256_8}.json"
    pack_path.write_bytes(raw_bytes)

    result = verify_pack(pack_path, allow_fixture=True)
    assert result.is_valid is True
    assert result.error_message is None
    assert result.disk_size_bytes == len(raw_bytes)
    assert result.disk_size_bytes < MAX_DISK_SIZE_BYTES
    assert result.memory_peak_bytes < MAX_MEMORY_BYTES
    assert result.cold_load_ms < MAX_LOAD_LATENCY_MS
    assert result.sha256_8 == sha256_8


def test_budget_verification_missing_file(tmp_path: Path) -> None:
    """Verify verify_pack fails cleanly on a non-existent file."""
    result = verify_pack(tmp_path / "nonexistent.json")
    assert result.is_valid is False
    assert "does not exist" in (result.error_message or "")


def test_budget_verification_corrupt_json(tmp_path: Path) -> None:
    """Verify verify_pack rejects malformed JSON."""
    corrupt_path = tmp_path / "fibo-core-12345678.json"
    corrupt_path.write_text("{not valid json", encoding="utf-8")

    result = verify_pack(corrupt_path)
    assert result.is_valid is False
    assert "Invalid JSON" in (result.error_message or "")


def test_budget_verification_hash_mismatch(
    tmp_path: Path, sample_ontology_graph: rdflib.Graph
) -> None:
    """Verify verify_pack detects filename hash prefix mismatch."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    raw_bytes, _, _, _ = compile_fibo_pack(classes, properties)

    wrong_path = tmp_path / "fibo-core-deadbeef.json"
    wrong_path.write_bytes(raw_bytes)

    result = verify_pack(wrong_path, allow_fixture=True)
    assert result.is_valid is False
    assert "does not match digest" in (result.error_message or "")


def test_budget_verification_non_canonical_drift(
    tmp_path: Path, sample_ontology_graph: rdflib.Graph
) -> None:
    """Verify verify_pack rejects non-canonical JSON with superfluous whitespace."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    _, pack, _, sha256_8 = compile_fibo_pack(classes, properties)

    drift_path = tmp_path / f"fibo-core-{sha256_8}.json"
    drift_path.write_text(json.dumps(pack, indent=4), encoding="utf-8")

    result = verify_pack(drift_path, allow_fixture=True)
    assert result.is_valid is False
    assert "RFC 8785" in (result.error_message or "")


def test_budget_verification_missing_module(
    tmp_path: Path, sample_ontology_graph: rdflib.Graph
) -> None:
    """Verify verify_pack fails if a core module is missing from classes."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    classes_no_ind = {k: v for k, v in classes.items() if v["module"] != "IND"}
    raw_bytes = dumps_jcs_bytes(
        {
            "classes": classes_no_ind,
            "metadata": {
                "compiled_at": DEFAULT_COMPILED_AT,
                "modules": sorted(list(CORE_MODULES)),
                "pack_id": "fibo-core",
                "upstream_commit": UPSTREAM_COMMIT,
                "upstream_tag": UPSTREAM_TAG,
                "version": "1.0.0",
            },
            "properties": properties,
        }
    )
    pack_path = tmp_path / "fibo-core-pack.json"
    pack_path.write_bytes(raw_bytes)

    result = verify_pack(pack_path, allow_fixture=True)
    assert result.is_valid is False
    assert "No classes found for core module 'IND'" in (result.error_message or "")


def test_budget_verification_fixture_rejection_in_production(
    tmp_path: Path, sample_ontology_graph: rdflib.Graph
) -> None:
    """Verify verify_pack rejects synthetic fixtures when allow_fixture=False (MAJ-03)."""
    classes, properties = extract_fibo_graph(sample_ontology_graph)
    raw_bytes, _, _, sha256_8 = compile_fibo_pack(classes, properties, is_fixture=True)

    pack_path = tmp_path / f"fibo-core-{sha256_8}.json"
    pack_path.write_bytes(raw_bytes)

    result = verify_pack(pack_path, allow_fixture=False)
    assert result.is_valid is False
    assert "synthetic fixture" in (result.error_message or "")


def test_safe_extract_tar_archive_and_security(tmp_path: Path) -> None:
    """Verify safe tar extraction and detection of traversal/symlink escapes (MAJ-01, MIN-03)."""
    tar_path = tmp_path / "fibo.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        data = b"<rdf:RDF></rdf:RDF>"
        ti = tarfile.TarInfo(name="FND/test.rdf")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    dest = tmp_path / "extracted_tar"
    safe_extract_archive(tar_path, dest)
    assert (dest / "FND" / "test.rdf").is_file()

    # 1. Path traversal via ../ escape
    bad_tar = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_tar, "w:gz") as tar:
        ti = tarfile.TarInfo(name="../escape.txt")
        ti.size = 5
        tar.addfile(ti, io.BytesIO(b"hello"))

    with pytest.raises(ValueError, match="Path traversal"):
        safe_extract_archive(bad_tar, tmp_path / "extract_bad")

    # 2. Path traversal via sibling directory with shared prefix (MAJ-01)
    sibling_tar = tmp_path / "sibling.tar.gz"
    with tarfile.open(sibling_tar, "w:gz") as tar:
        payload = b"evil"
        ti = tarfile.TarInfo(name="../extracted_tar_evil/payload.txt")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Path traversal"):
        safe_extract_archive(sibling_tar, dest)

    # 3. Outright rejection of symlinks in archive (MIN-01)
    symlink_tar = tmp_path / "symlink.tar.gz"
    with tarfile.open(symlink_tar, "w:gz") as tar:
        ti_sym = tarfile.TarInfo(name="evil_symlink")
        ti_sym.type = tarfile.SYMTYPE
        ti_sym.linkname = "../../etc/passwd"
        tar.addfile(ti_sym)

    with pytest.raises(ValueError, match="Links and special files prohibited"):
        safe_extract_archive(symlink_tar, tmp_path / "extract_symlink")

    # 4. Outright rejection of hardlinks in archive (MIN-01)
    hardlink_tar = tmp_path / "hardlink.tar.gz"
    with tarfile.open(hardlink_tar, "w:gz") as tar:
        ti_hl = tarfile.TarInfo(name="sub/evil_hardlink")
        ti_hl.type = tarfile.LNKTYPE
        ti_hl.linkname = "../../../outside.txt"
        tar.addfile(ti_hl)

    with pytest.raises(ValueError, match="Links and special files prohibited"):
        safe_extract_archive(hardlink_tar, tmp_path / "extract_hardlink")


def test_safe_extract_zip_archive_and_security(tmp_path: Path) -> None:
    """Verify safe extraction of zip archives and rejection of traversal attempts (MAJ-01)."""
    zip_path = tmp_path / "fibo.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("BE/LegalEntities.rdf", b"<rdf:RDF></rdf:RDF>")

    dest = tmp_path / "extracted_zip"
    safe_extract_archive(zip_path, dest)
    assert (dest / "BE" / "LegalEntities.rdf").is_file()

    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../escape_zip.txt", b"evil")

    with pytest.raises(ValueError, match="Path traversal"):
        safe_extract_archive(bad_zip, tmp_path / "extract_bad_zip")


def test_cli_mock_fixture_prohibits_production_directory() -> None:
    """Verify --mock-fixture fails if outputting to production package folder (MAJ-03)."""
    ret = main(["--mock-fixture", "--output-dir", str(DEFAULT_OUTPUT_DIR)])
    assert ret == 1


def test_cli_mock_compilation_and_verify(tmp_path: Path) -> None:
    """Verify end-to-end CLI execution with --mock-fixture in isolated folder."""
    out_dir = tmp_path / "fibo_out"
    ret = main(["--mock-fixture", "--output-dir", str(out_dir)])
    assert ret == 0
    assert (out_dir / "fibo-core-pack.json").is_file()

    # Run verification against generated output
    verify_ret = main(["--verify-only", "--output-dir", str(out_dir)])
    assert verify_ret == 0


def test_failed_compilation_preserves_prior_pack(tmp_path: Path) -> None:
    """Verify atomic replace preserves existing valid pack when compilation fails (MAJ-02)."""
    out_dir = tmp_path / "fibo_preservation"
    out_dir.mkdir(parents=True)

    # Establish an initial valid pack and alias
    prior_file = out_dir / "fibo-core-initial1.json"
    prior_content = b'{"classes":{},"metadata":{"pack_id":"initial"},"properties":{}}'
    prior_file.write_bytes(prior_content)
    alias_file = out_dir / "fibo-core-pack.json"
    alias_file.write_bytes(prior_content)

    # Simulate compilation failure by injecting a failing verify_pack
    with patch("scripts.vendor_fibo.verify_pack") as mock_verify:
        mock_verify.return_value = MagicMock(
            is_valid=False, error_message="Simulated budget failure"
        )
        ret = main(["--mock-fixture", "--output-dir", str(out_dir)])
        assert ret == 1

    # Assert prior valid pack and alias were untouched!
    assert prior_file.is_file()
    assert prior_file.read_bytes() == prior_content
    assert alias_file.is_file()
    assert alias_file.read_bytes() == prior_content


def test_load_fibo_from_directory_empty_fails(tmp_path: Path) -> None:
    """Verify load_fibo_from_directory raises RuntimeError when no ontology files exist."""
    with pytest.raises(RuntimeError, match="No ontology files"):
        load_fibo_from_directory(tmp_path)


def test_ensure_fibo_source_offline_archive(tmp_path: Path) -> None:
    """Verify ensure_fibo_source unpacks offline archive with SHA-256 caching (MIN-02)."""
    tar_path = tmp_path / "fibo.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for m in CORE_MODULES:
            data = b"<rdf:RDF></rdf:RDF>"
            ti = tarfile.TarInfo(name=f"{m}/Ontology.rdf")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

    cache = tmp_path / "cache"
    tar_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    source, commit = ensure_fibo_source(
        cache_dir=cache,
        offline_archive=tar_path,
        archive_sha256=tar_sha,
        expected_commit=UPSTREAM_COMMIT,
    )
    assert source.is_dir()
    assert commit == f"unverified-{tar_sha[:12]}"
    for m in CORE_MODULES:
        assert (source / m / "Ontology.rdf").is_file()


def test_ensure_fibo_source_git_rev_parse_failure(tmp_path: Path) -> None:
    """Verify ensure_fibo_source raises RuntimeError if git rev-parse fails on clone."""
    cache = tmp_path / "cache"
    with patch("subprocess.run") as mock_run:
        # Clone succeeds, but rev-parse fails
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git clone
            MagicMock(returncode=1, stderr="fatal: ambiguous argument 'HEAD'"),  # git rev-parse
        ]
        with pytest.raises(RuntimeError, match="rev-parse HEAD failed"):
            ensure_fibo_source(cache_dir=cache)


def test_real_pack_verification_if_present() -> None:
    """Assert real compiled pack in packages/opendv-sources satisfies all budgets (MAJ-02)."""
    pack_dir = DEFAULT_OUTPUT_DIR
    pack_candidates = sorted(
        [p for p in pack_dir.glob("fibo-core-*.json") if p.name != "fibo-core-pack.json"]
    )
    if not pack_candidates:
        pytest.skip("Real FIBO pack not compiled in default directory.")

    assert len(pack_candidates) == 1, f"Expected 1 real pack, found: {pack_candidates}"
    target_pack = pack_candidates[0]
    result = verify_pack(target_pack, allow_fixture=False, require_production_counts=True)
    assert result.is_valid is True, f"Real pack validation failed: {result.error_message}"
    assert result.disk_size_bytes < MAX_DISK_SIZE_BYTES
    assert result.memory_peak_bytes < MAX_MEMORY_BYTES
    assert result.cold_load_ms < MAX_LOAD_LATENCY_MS
    assert result.class_count >= 2000
    assert result.property_count >= 800


def test_ensure_fibo_source_unmanaged_dir_raises(tmp_path: Path) -> None:
    """Verify ensure_fibo_source refuses to mutate/delete unmanaged directory (MAJ-03)."""
    unmanaged = tmp_path / "user_dir"
    unmanaged.mkdir()
    user_file = unmanaged / "user_data.txt"
    user_file.write_text("precious data", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Refusing to mutate or overwrite unmanaged"):
        ensure_fibo_source(cache_dir=unmanaged)

    assert user_file.is_file()
    assert user_file.read_text(encoding="utf-8") == "precious data"


def test_ensure_fibo_source_fresh_clone_head_mismatch_raises(tmp_path: Path) -> None:
    """Verify ensure_fibo_source raises RuntimeError if clone HEAD != expected commit (MAJ-02)."""
    cache = tmp_path / "cache"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git clone
            MagicMock(returncode=0, stdout="mismatched_commit_abc123\n"),  # rev-parse
        ]
        with pytest.raises(RuntimeError, match="does not match expected pinned commit"):
            ensure_fibo_source(cache_dir=cache)


def test_ensure_fibo_source_cached_checkout_failure_raises(tmp_path: Path) -> None:
    """Verify ensure_fibo_source raises RuntimeError if cached checkout fails (MAJ-01)."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ".opendv_cache_manifest.json").write_text("{}", encoding="utf-8")
    git_cache = cache / "git"
    git_cache.mkdir()
    (git_cache / ".git").mkdir()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="old_commit\n"),  # rev-parse
            MagicMock(returncode=1, stderr="error"),  # checkout
            MagicMock(returncode=1, stderr="error"),  # fetch
            MagicMock(returncode=1, stderr="error"),  # checkout
            MagicMock(returncode=0, stdout="old_commit\n"),  # rev-parse
        ]
        with pytest.raises(RuntimeError, match="Failed to check out expected commit"):
            ensure_fibo_source(cache_dir=cache)


def test_ensure_fibo_source_offline_archive_sha_validation(tmp_path: Path) -> None:
    """Verify offline archive SHA-256 validation and production guard (MAJ-02)."""
    tar_path = tmp_path / "fibo.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for m in CORE_MODULES:
            data = b"<rdf:RDF></rdf:RDF>"
            ti = tarfile.TarInfo(name=f"{m}/Ontology.rdf")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

    cache = tmp_path / "cache"
    # 1. Mismatch SHA-256 raises RuntimeError
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        ensure_fibo_source(
            cache_dir=cache,
            offline_archive=tar_path,
            archive_sha256="deadbeef" * 8,
        )

    # 2. Production target without archive_sha256 raises RuntimeError
    with pytest.raises(RuntimeError, match="Cannot compile to production package"):
        ensure_fibo_source(
            cache_dir=cache,
            offline_archive=tar_path,
            archive_sha256=None,
            is_production=True,
        )


def test_staging_tmp_cleaned_up_on_failure(tmp_path: Path) -> None:
    """Verify staging .tmp files are removed on compilation failure (MIN-02)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with patch("scripts.vendor_fibo.verify_pack") as mock_verify:
        mock_verify.return_value = MagicMock(is_valid=False, error_message="budget fail")
        ret = main(["--mock-fixture", "--output-dir", str(out_dir)])
        assert ret == 1
    tmp_files = list(out_dir.glob("*.tmp"))
    assert tmp_files == []


def test_language_preference_variants() -> None:
    """Verify _pick_best_literal handles case-insensitive English variants (MIN-03)."""
    lit_fr = rdflib.Literal("Société", lang="fr")
    lit_ca = rdflib.Literal("Canadian Corporation", lang="en-CA")
    lit_us = rdflib.Literal("US Corporation", lang="EN-US")
    result = _pick_best_literal([lit_fr, lit_ca, lit_us])
    assert result == "Canadian Corporation"


def test_blank_definition_fallback() -> None:
    """Verify whitespace-only skos:definition falls back to rdfs:comment (MIN-04)."""
    g = rdflib.Graph()
    node = rdflib.URIRef("https://spec.edmcouncil.org/fibo/ontology/FND/Test/ClassA")
    g.add((node, rdflib.SKOS.definition, rdflib.Literal("   ")))
    g.add((node, rdflib.RDFS.comment, rdflib.Literal("Fallback definition")))
    _, definition, _ = _extract_annotations(g, node)
    assert definition == "Fallback definition"


def test_verify_mode_alias_mismatch_error(tmp_path: Path) -> None:
    """Verify --verify-only fails when alias file content does not match pack (MIN-05)."""
    out_dir = tmp_path / "out"
    assert main(["--mock-fixture", "--output-dir", str(out_dir)]) == 0
    alias_file = out_dir / "fibo-core-pack.json"
    alias_file.write_bytes(b'{"corrupted": true}')
    ret = main(["--verify-only", "--output-dir", str(out_dir)])
    assert ret == 1


def test_verify_mode_multiple_candidates_error(tmp_path: Path) -> None:
    """Verify --verify-only fails when multiple hash-named packs are present (MIN-05)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "fibo-core-11111111.json").write_text("{}", encoding="utf-8")
    (out_dir / "fibo-core-22222222.json").write_text("{}", encoding="utf-8")
    ret = main(["--verify-only", "--output-dir", str(out_dir)])
    assert ret == 1


def test_offline_then_git_same_cache_dir(tmp_path: Path) -> None:
    """Verify offline extraction followed by git compile works with same cache_dir (MAJ-02)."""
    tar_path = tmp_path / "fibo.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for m in CORE_MODULES:
            data = b"<rdf:RDF></rdf:RDF>"
            ti = tarfile.TarInfo(name=f"{m}/Ontology.rdf")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

    cache = tmp_path / "cache"
    source1, _ = ensure_fibo_source(cache_dir=cache, offline_archive=tar_path)
    assert source1.is_dir()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git clone
            MagicMock(returncode=0, stdout=f"{UPSTREAM_COMMIT}\n"),  # rev-parse HEAD
        ]
        source2, commit2 = ensure_fibo_source(cache_dir=cache)
        assert source2.is_dir()
        assert commit2 == UPSTREAM_COMMIT
        assert (cache / "git").is_dir()
        assert (cache / "offline").is_dir()


def test_cached_git_dirty_working_tree_rejected(tmp_path: Path) -> None:
    """Verify cached git repository with dirty working tree raises RuntimeError (MIN-03)."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ".opendv_cache_manifest.json").write_text("{}", encoding="utf-8")
    git_cache = cache / "git"
    git_cache.mkdir()
    (git_cache / ".git").mkdir()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=f"{UPSTREAM_COMMIT}\n"),  # rev-parse HEAD
            MagicMock(returncode=0, stdout=" M FND/Agreements.rdf\n"),  # git status --porcelain
        ]
        with pytest.raises(RuntimeError, match="uncommitted or untracked changes"):
            ensure_fibo_source(cache_dir=cache)


def test_verify_pack_non_dict_rejected(tmp_path: Path) -> None:
    """Verify verify_pack returns failed result when pack root is not a dict (MIN-01)."""
    bad_pack = tmp_path / "list_pack.json"
    bad_pack.write_bytes(dumps_jcs_bytes([]))
    res = verify_pack(bad_pack)
    assert res.is_valid is False
    assert "Pack root must be a JSON object" in (res.error_message or "")


def test_load_fibo_from_directory_parse_error(tmp_path: Path) -> None:
    """Verify parse errors fail when allow_parse_errors=False and pass when True (MIN-07)."""
    mod_dir = tmp_path / "FND"
    mod_dir.mkdir()
    (mod_dir / "broken.rdf").write_text("NOT VALID RDF XML", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Production compile failed"):
        load_fibo_from_directory(tmp_path, allow_parse_errors=False)

    g = load_fibo_from_directory(tmp_path, allow_parse_errors=True)
    assert isinstance(g, rdflib.Graph)


def test_verify_pack_rejects_unverified_commit_in_production(tmp_path: Path) -> None:
    """Verify verify_pack rejects unverified or unpinned commit in production (MAJ-01)."""
    pack = tmp_path / "pack.json"
    data = {
        "classes": {
            "https://spec.edmcouncil.org/fibo/ontology/FND/Test": {
                "uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Test",
                "module": "FND",
            }
        },
        "metadata": {
            "compiled_at": DEFAULT_COMPILED_AT,
            "modules": list(CORE_MODULES),
            "pack_id": "fibo-core",
            "upstream_commit": "unverified-123456789abc",
            "upstream_tag": UPSTREAM_TAG,
            "version": "1.0.0",
        },
        "properties": {
            "https://spec.edmcouncil.org/fibo/ontology/FND/p": {
                "uri": "https://spec.edmcouncil.org/fibo/ontology/FND/p",
                "module": "FND",
                "property_type": "DatatypeProperty",
            }
        },
    }
    pack.write_bytes(dumps_jcs_bytes(data))
    res = verify_pack(pack, require_production_counts=True)
    assert res.is_valid is False
    assert "must match pinned commit" in (res.error_message or "")


def test_offline_archive_forged_git_rejected_in_production(tmp_path: Path) -> None:
    """Verify offline archive with forged .git directory is rejected in production (MAJ-01)."""
    tar_path = tmp_path / "forged_fibo.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        # Add core module file
        for m in CORE_MODULES:
            data = b"<rdf:RDF></rdf:RDF>"
            ti = tarfile.TarInfo(name=f"{m}/Ontology.rdf")
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        # Add fake .git/HEAD file pointing to pinned commit
        fake_head = f"{UPSTREAM_COMMIT}\n".encode()
        head_ti = tarfile.TarInfo(name=".git/HEAD")
        head_ti.size = len(fake_head)
        tar.addfile(head_ti, io.BytesIO(fake_head))

    cache = tmp_path / "cache"
    # In production mode, uncertified archive must be rejected even with fake .git/HEAD
    with pytest.raises(RuntimeError, match="untrusted offline archives prohibited"):
        ensure_fibo_source(
            cache_dir=cache,
            offline_archive=tar_path,
            is_production=True,
        )


def test_git_cache_manifest_stored_outside_working_tree(tmp_path: Path) -> None:
    """Verify git cache manifest is stored outside git working tree (MAJ-02)."""
    cache = tmp_path / "cache"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git clone
            MagicMock(returncode=0, stdout=f"{UPSTREAM_COMMIT}\n"),  # rev-parse HEAD
        ]
        git_dir, commit = ensure_fibo_source(cache_dir=cache)
        assert git_dir == cache / "git"
        assert commit == UPSTREAM_COMMIT

        # Manifest must be in cache_dir, NOT inside git working tree
        assert (cache / "git_manifest.json").is_file()
        assert not (cache / "git" / ".opendv_cache_manifest.json").exists()
        assert not (cache / "git" / "git_manifest.json").exists()


def test_dual_declared_property_priority() -> None:
    """Verify ObjectProperty takes precedence over DatatypeProperty on dual declaration (MIN-05)."""
    g = rdflib.Graph()
    uri = "https://spec.edmcouncil.org/fibo/ontology/FND/Test/testProp"
    prop_ref = rdflib.URIRef(uri)
    domain_ref = rdflib.URIRef("https://spec.edmcouncil.org/fibo/ontology/FND/Test/ClassD")
    range_ref = rdflib.URIRef("https://spec.edmcouncil.org/fibo/ontology/FND/Test/ClassR")

    g.add((prop_ref, rdflib.RDF.type, rdflib.OWL.ObjectProperty))
    g.add((prop_ref, rdflib.RDF.type, rdflib.OWL.DatatypeProperty))
    g.add((prop_ref, rdflib.RDFS.label, rdflib.Literal("test property")))
    g.add((prop_ref, rdflib.SKOS.definition, rdflib.Literal("A test dual property.")))
    g.add((prop_ref, rdflib.RDFS.domain, domain_ref))
    g.add((prop_ref, rdflib.RDFS.range, range_ref))

    _, properties = extract_fibo_graph(g)
    assert uri in properties
    p = properties[uri]
    assert p["property_type"] == "ObjectProperty"
    assert str(domain_ref) in p["domain"]
    assert str(range_ref) in p["range"]


def test_hash_file_sha256_chunked(tmp_path: Path) -> None:
    """Verify chunked file hashing produces correct SHA-256 (MIN-06)."""
    test_file = tmp_path / "sample.bin"
    payload = b"Hello OpenDV FIBO" * 5000
    test_file.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _hash_file_sha256(test_file, chunk_size=128) == expected


def test_load_baseline_fibo_config() -> None:
    """Verify _load_baseline_fibo_config loads upstream_commit and upstream_tag (MIN-03)."""
    commit, tag, _ = _load_baseline_fibo_config()
    assert commit == "69dbbe7a6a4e32049ba3f3988b48877bc954f061"
    assert tag == "master_2024Q4"


def test_main_compile_offline_archive_non_production(tmp_path: Path) -> None:
    """Verify main() end-to-end compiles from offline archive in non-production mode (MAJ-01)."""
    tar_path = tmp_path / "fibo_source.tar.gz"
    mock_g = create_mock_fibo_graph()
    ttl_bytes = mock_g.serialize(format="turtle").encode()

    with tarfile.open(tar_path, "w:gz") as tar:
        for m in CORE_MODULES:
            ti = tarfile.TarInfo(name=f"ontology/{m}/Ontology.ttl")
            ti.size = len(ttl_bytes)
            tar.addfile(ti, io.BytesIO(ttl_bytes))

    out_dir = tmp_path / "non_prod_out"
    cache_dir = tmp_path / "cache"

    ret = main(
        [
            "--offline-archive",
            str(tar_path),
            "--output-dir",
            str(out_dir),
            "--cache-dir",
            str(cache_dir),
        ]
    )
    assert ret == 0

    packs = list(out_dir.glob("fibo-core-*.json"))
    assert len(packs) == 2  # hash-named pack + fibo-core-pack.json alias
    canonical = next(p for p in packs if p.name != "fibo-core-pack.json")
    data = json.loads(canonical.read_text(encoding="utf-8"))
    assert data["metadata"]["upstream_commit"].startswith("unverified-")


def test_archive_member_count_limit_exceeded(tmp_path: Path) -> None:
    """Verify safe_extract_archive rejects archives exceeding MAX_ARCHIVE_MEMBERS (MIN-06)."""
    tar_path = tmp_path / "too_many_members.tar.gz"
    with tarfile.open(tar_path, "w:gz"):
        pass  # create valid empty archive

    with patch("tarfile.TarFile.getmembers") as mock_members:
        # Simulate member count exceeding limit
        fake_list = [MagicMock()] * (MAX_ARCHIVE_MEMBERS + 1)
        mock_members.return_value = fake_list
        with pytest.raises(ValueError, match=r"Archive member count .* exceeds limit"):
            safe_extract_archive(tar_path, tmp_path / "out")


def test_archive_uncompressed_bytes_limit_exceeded(tmp_path: Path) -> None:
    """Verify safe_extract_archive rejects archives exceeding uncompressed limit (MIN-06)."""
    tar_path = tmp_path / "too_large.tar.gz"
    with tarfile.open(tar_path, "w:gz"):
        pass

    with patch("tarfile.TarFile.getmembers") as mock_members:
        fake_member = MagicMock(
            issym=lambda: False,
            islnk=lambda: False,
            isdev=lambda: False,
            ischr=lambda: False,
            isblk=lambda: False,
            isfifo=lambda: False,
            size=MAX_ARCHIVE_UNCOMPRESSED_BYTES + 1,
            name="huge.bin",
        )
        mock_members.return_value = [fake_member]
        with pytest.raises(ValueError, match="Archive uncompressed size exceeds limit"):
            safe_extract_archive(tar_path, tmp_path / "out")


def test_ensure_fibo_source_invalid_commit_or_tag_format(tmp_path: Path) -> None:
    """Verify ensure_fibo_source validates commit hex length and tag prefix (MIN-03)."""
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="must be a 40-character hex SHA"):
        ensure_fibo_source(cache_dir=cache, expected_commit="short-commit")

    with pytest.raises(ValueError, match="cannot start with '-'"):
        ensure_fibo_source(
            cache_dir=cache,
            tag="--inject-option",
            expected_commit="0" * 40,
        )


def test_obsolete_packs_cleanup_on_compile(tmp_path: Path) -> None:
    """Verify obsolete fibo-core-<sha>.json packs are cleaned up after compile (MIN-06)."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale_pack = out_dir / "fibo-core-ffffffff.json"
    stale_pack.write_text("{}", encoding="utf-8")

    ret = main(["--mock-fixture", "--output-dir", str(out_dir)])
    assert ret == 0
    assert not stale_pack.exists()
    assert (out_dir / "fibo-core-pack.json").is_file()


def test_cached_git_repo_url_mismatch_reclones(tmp_path: Path) -> None:
    """Verify cached git repository with mismatched remote origin triggers fresh clone (MIN-03)."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ".opendv_cache_manifest.json").write_text("{}", encoding="utf-8")
    git_cache = cache / "git"
    git_cache.mkdir()
    git_meta = git_cache / ".git"
    git_meta.mkdir()
    (git_meta / "config").write_text("[core]\n", encoding="utf-8")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="https://evil.org/fibo.git\n"),  # remote get-url origin
            MagicMock(returncode=0),  # fresh git clone
            MagicMock(returncode=0, stdout=f"{UPSTREAM_COMMIT}\n"),  # rev-parse HEAD
        ]
        source, commit = ensure_fibo_source(cache_dir=cache)
        assert source == git_cache
        assert commit == UPSTREAM_COMMIT


def test_zip_rejects_unix_symlink_and_windows_reparse_point(tmp_path: Path) -> None:
    """Regression 040b4599::MAJ-06: Reject Unix symlinks & Windows reparse points in .zip."""
    # 1. Unix symlink entry (S_IFLNK = 0o120000 in upper 16 bits of external_attr)
    unix_sym_zip = tmp_path / "unix_symlink.zip"
    with zipfile.ZipFile(unix_sym_zip, "w") as zf:
        zi = zipfile.ZipInfo("FND/evil_link.ttl")
        zi.create_system = 3  # Unix
        zi.external_attr = (0o120777) << 16
        zf.writestr(zi, "/etc/passwd")

    with pytest.raises(ArchiveSecurityError, match="Links and special files prohibited"):
        safe_extract_archive(unix_sym_zip, tmp_path / "out_unix")

    # 2. Windows FILE_ATTRIBUTE_REPARSE_POINT (0x400 in lower 16 bits of external_attr)
    win_reparse_zip = tmp_path / "win_reparse.zip"
    with zipfile.ZipFile(win_reparse_zip, "w") as zf:
        zi = zipfile.ZipInfo("FND/junction.ttl")
        zi.create_system = 0  # Windows / FAT
        zi.external_attr = 0x0400
        zf.writestr(zi, "junction target")

    with pytest.raises(ArchiveSecurityError, match="Links and special files prohibited"):
        safe_extract_archive(win_reparse_zip, tmp_path / "out_win")


def test_zip_rejects_zip_slip_and_streaming_decompression_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression 040b4599::MAJ-06: Zip-slip rejection & streaming uncompressed byte limit."""
    # 1. Zip-slip relative and drive-prefixed paths
    for bad_name in ("../../escape.ttl", "C:/Windows/win.ini", "/etc/shadow"):
        slip_zip = tmp_path / f"slip_{abs(hash(bad_name))}.zip"
        with zipfile.ZipFile(slip_zip, "w") as zf:
            zf.writestr(bad_name, "malicious")
        with pytest.raises(ArchiveSecurityError):
            safe_extract_archive(slip_zip, tmp_path / "out_slip")

    # 2. Streaming decompression bomb: header file_size lies (10 bytes), stream yields 8192 bytes
    bomb_zip = tmp_path / "streaming_bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("FND/huge.ttl", b"A" * 8192)

    monkeypatch.setattr("scripts.vendor_fibo.MAX_ARCHIVE_UNCOMPRESSED_BYTES", 1024)
    orig_infolist = zipfile.ZipFile.infolist

    def spoofed_infolist(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = orig_infolist(self)
        for item in infos:
            item.file_size = 10
        return infos

    # 2a. Real ZipExtFile CRC/size mismatch under spoofed header is caught as ArchiveSecurityError
    with (
        patch.object(zipfile.ZipFile, "infolist", spoofed_infolist),
        pytest.raises(ArchiveSecurityError),
    ):
        safe_extract_archive(bomb_zip, tmp_path / "out_bomb_crc")

    # 2b. Stream returning >1024 decompressed bytes directly hits streaming byte cap
    with (
        patch.object(zipfile.ZipFile, "infolist", spoofed_infolist),
        patch.object(zipfile.ZipFile, "open", return_value=io.BytesIO(b"A" * 8192)),
        pytest.raises(ArchiveSecurityError, match="Archive uncompressed size exceeds limit"),
    ):
        safe_extract_archive(bomb_zip, tmp_path / "out_bomb_stream")
