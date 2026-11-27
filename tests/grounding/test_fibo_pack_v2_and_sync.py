"""M1.2 tests for FIBO Pack v2 compiler, archive/XML/SSRF hardening, and pure-JSON fibo_pack_ops."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import rdflib
from rdflib import OWL, RDF, RDFS, SKOS
from rdflib.namespace import DCTERMS

from opendv.grounding.providers.fibo import (
    FiboReferenceOntologyLoader,
    get_bundled_fibo_pack_sha256,
    minimal_taxonomic_leaves,
)
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.events import GroundingReceipt
from opendv.models.json_hasher import dumps_jcs_bytes
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ConceptAttribute,
    ConceptTableBinding,
    ExternalMapping,
    GraphAOntology,
    OntologyEnumeration,
    OntologyRelationship,
)
from opendv.models.project import ProjectSettingsSnapshot, RevisionMetadata
from opendv.models.wire_dtos_discovery import ProjectInitReq
from opendv.service.bundles.zip_guard import safe_open_no_follow
from opendv.service.fibo_pack_ops import (
    check_project_drift,
    install_fibo_pack,
    load_installed_pack,
    plan_repin_with_receipt_verification,
)
from opendv.service.os_platform import verify_owner_only_permissions
from opendv.service.state_store import ProjectStateManager

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.vendor_fibo import main as vendor_main  # noqa: E402
from scripts.vendor_fibo_archive import (  # noqa: E402
    ArchiveSecurityError,
    download_fibo_archive,
    ensure_fibo_source,
    safe_extract_archive,
    validate_https_url_no_ssrf,
    validate_xml_ontology_bytes,
)
from scripts.vendor_fibo_extract import (  # noqa: E402
    CMNS_AV_ABBREVIATION,
    CMNS_AV_ACRONYM,
    CMNS_AV_REPLACED_BY,
    CMNS_AV_SYNONYM,
    FIBO_BASE_URI_PREFIX,
    PROD_MODULES,
    compile_fibo_pack,
    extract_fibo_graph_v2,
    load_fibo_from_directory,
)
from scripts.vendor_fibo_verify import (  # noqa: E402
    MAX_DISK_SIZE_BYTES,
    MAX_LOAD_LATENCY_MS,
    MAX_MEMORY_BYTES,
    verify_pack,
)


def _write_pack_v2_file(
    path: Path,
    *,
    deprecated_uri: str | None = None,
    replacement_uri: str | None = None,
    omit_uri: str | None = None,
) -> tuple[bytes, str]:
    base = FIBO_BASE_URI_PREFIX
    classes: dict[str, object] = {
        f"{base}FND/AgentsAndPeople/Agents/AutonomousAgent": {
            "uri": f"{base}FND/AgentsAndPeople/Agents/AutonomousAgent",
            "label": "Autonomous Agent",
            "definition": "An autonomous agent.",
            "module": "FND",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": False,
        },
        f"{base}SEC/Debt/Bonds/Bond": {
            "uri": f"{base}SEC/Debt/Bonds/Bond",
            "label": "Bond",
            "definition": "A debt security.",
            "module": "SEC",
            "synonyms": ["Debt Security"],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": False,
        },
    }
    if deprecated_uri and replacement_uri:
        classes[deprecated_uri] = {
            "uri": deprecated_uri,
            "label": "Legacy Bond",
            "definition": "Deprecated bond.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
            "replaced_by": replacement_uri,
        }
    if omit_uri and omit_uri in classes:
        del classes[omit_uri]

    props: dict[str, object] = {
        f"{base}SEC/Debt/Bonds/hasMaturityDate": {
            "uri": f"{base}SEC/Debt/Bonds/hasMaturityDate",
            "label": "has maturity date",
            "definition": "Maturity date.",
            "module": "SEC",
            "property_type": "DatatypeProperty",
            "domain": [f"{base}SEC/Debt/Bonds/Bond"],
            "range": [],
            "sub_property_of": [],
            "inverse_of": [],
            "is_functional": True,
            "is_inverse_functional": False,
            "deprecated": False,
        },
        f"{base}FND/Parties/Parties/hasOldParty": {
            "uri": f"{base}FND/Parties/Parties/hasOldParty",
            "label": "has old party",
            "definition": "Deprecated party link.",
            "module": "FND",
            "property_type": "ObjectProperty",
            "domain": [],
            "range": [],
            "sub_property_of": [],
            "inverse_of": [],
            "is_functional": False,
            "is_inverse_functional": False,
            "deprecated": True,
            "replaced_by": f"{base}FND/Parties/Parties/hasParty",
        },
        f"{base}FND/Parties/Parties/hasParty": {
            "uri": f"{base}FND/Parties/Parties/hasParty",
            "label": "has party",
            "definition": "Active party link.",
            "module": "FND",
            "property_type": "ObjectProperty",
            "domain": [],
            "range": [],
            "sub_property_of": [],
            "inverse_of": [],
            "is_functional": False,
            "is_inverse_functional": False,
            "deprecated": False,
        },
    }
    raw_bytes, _, sha_full, _ = compile_fibo_pack(
        classes,  # type: ignore[arg-type]
        props,  # type: ignore[arg-type]
        individuals={
            f"{base}FND/Dates/BusinessDayConvention/ModifiedFollowing": {
                "uri": f"{base}FND/Dates/BusinessDayConvention/ModifiedFollowing",
                "label": "Modified Following",
                "module": "FND",
                "aliases": ["MF"],
                "types": [f"{base}SEC/Debt/Bonds/Bond"],
                "deprecated": False,
            }
        },
        upstream_tag="2026Q3",
        upstream_commit="2d73435a4419b66b7c9f44cb9763e35fe8ac7890",
        modules=PROD_MODULES,
    )
    path.write_bytes(raw_bytes)
    return raw_bytes, sha_full


def test_live_synced_pack_v2_schema_jcs_budgets_and_baselines() -> None:
    pack_path = (
        REPO_ROOT / "src" / "opendv" / "grounding" / "providers" / "fibo" / "fibo-core-pack.json"
    )
    assert pack_path.is_file()
    raw_bytes = pack_path.read_bytes()
    assert not raw_bytes.endswith(b"\n")
    parsed = json.loads(raw_bytes.decode("utf-8"))
    assert dumps_jcs_bytes(parsed) == raw_bytes

    baselines = json.loads(
        (REPO_ROOT / "schemas" / "v2" / "baseline-digests.json").read_text(encoding="utf-8")
    )
    fibo_meta = baselines["fibo_core_pack"]
    bundled_sha = get_bundled_fibo_pack_sha256()
    assert bundled_sha == fibo_meta["sha256"]
    assert fibo_meta["pack_schema_version"] == "2.0.0"

    v_res = verify_pack(pack_path, allow_fixture=False, require_production_counts=True)
    assert v_res.is_valid, v_res.error_message
    assert v_res.disk_size_bytes < MAX_DISK_SIZE_BYTES
    assert v_res.memory_peak_bytes < MAX_MEMORY_BYTES
    assert v_res.cold_load_ms < MAX_LOAD_LATENCY_MS
    assert v_res.class_count == fibo_meta["concept_count"]
    assert v_res.property_count == fibo_meta["property_count"]
    assert v_res.individual_count == fibo_meta["individual_count"]
    assert 0 < v_res.individual_count <= 2500

    loader = FiboReferenceOntologyLoader()
    assert loader.pack_schema_version == "2.0.0"
    assert set(PROD_MODULES).issubset(set(loader.modules))

    lei_cls = loader.lookup_by_uri(
        f"{FIBO_BASE_URI_PREFIX}BE/LegalEntities/LEIEntities/LegalEntityIdentifier"
    )
    assert lei_cls is not None
    assert lei_cls["archetype"] == "identifier"
    assert lei_cls["identified_entity_uris"]

    custodian = loader.lookup_by_uri(
        f"{FIBO_BASE_URI_PREFIX}SEC/Equities/EquityInstruments/Custodian"
    )
    assert custodian is not None
    assert custodian["archetype"] == "party_role"
    assert custodian["is_played_by"]

    # Ensure ISO Currency/Country individuals are excluded (handled by kernel/iso_codes.json)
    indivs = loader.get_individuals()
    assert not any("ISO4217" in uri or "ISO3166" in uri for uri in indivs)


def test_vendor_extract_cmns_restrictions_archetypes_and_individuals() -> None:
    g = rdflib.Graph()
    base = FIBO_BASE_URI_PREFIX
    cmns_org = "https://www.omg.org/spec/Commons/Organizations/"
    cmns_rlcmp = "https://www.omg.org/spec/Commons/RolesAndCompositions/"
    cmns_id = "https://www.omg.org/spec/Commons/Identifiers/"
    cmns_dsg = "https://www.omg.org/spec/Commons/Designators/"

    # Classes: Party -> LegalPerson -> Corporation; Role -> Custodian; Identifier -> OrgId / ELFId
    party = rdflib.URIRef(f"{cmns_org}Organization")
    legal_person = rdflib.URIRef(f"{cmns_org}LegalPerson")
    corp = rdflib.URIRef(f"{base}BE/LegalEntities/CorporateBodies/Corporation")
    role_cls = rdflib.URIRef(f"{base}FND/Parties/Roles/PartyRole")
    custodian = rdflib.URIRef(f"{base}SEC/Equities/EquityInstruments/Custodian")
    org_id = rdflib.URIRef(f"{base}BE/LegalEntities/LEIEntities/OrganizationIdentifier")
    elf_id = rdflib.URIRef(f"{base}BE/LegalEntities/LEIEntities/EntityLegalFormIdentifier")
    bdc_cls = rdflib.URIRef(f"{base}FND/DatesAndTimes/FinancialDates/BusinessDayConvention")
    exch_cls = rdflib.URIRef(f"{base}FBC/FunctionalEntities/Markets/Exchange")
    ccy_cls = rdflib.URIRef(f"{base}FND/Accounting/CurrencyAmount/Currency")
    old_bond = rdflib.URIRef(f"{base}SEC/Debt/Bonds/OldBond")
    new_bond = rdflib.URIRef(f"{base}SEC/Debt/Bonds/Bond")

    for cls_ref in (party, legal_person, corp, role_cls, custodian, org_id, elf_id, bdc_cls, exch_cls, ccy_cls, old_bond, new_bond):
        g.add((cls_ref, RDF.type, OWL.Class))
    g.add((corp, RDFS.label, rdflib.Literal("Corporation")))
    g.add((corp, CMNS_AV_SYNONYM, rdflib.Literal("Body Corporate")))
    g.add((corp, CMNS_AV_ABBREVIATION, rdflib.Literal("Corp")))
    g.add((corp, CMNS_AV_ACRONYM, rdflib.Literal("CORP")))
    g.add((legal_person, RDFS.subClassOf, party))
    g.add((corp, RDFS.subClassOf, legal_person))

    # Role isPlayedBy restriction via owl:unionOf + owl:onClass inheritance + leaf reduction
    is_played_by = rdflib.URIRef(f"{cmns_rlcmp}isPlayedBy")
    g.add((is_played_by, RDF.type, OWL.ObjectProperty))
    r_parent = rdflib.BNode()
    g.add((r_parent, RDF.type, OWL.Restriction))
    g.add((r_parent, OWL.onProperty, is_played_by))
    g.add((r_parent, OWL.someValuesFrom, party))
    g.add((role_cls, RDFS.subClassOf, r_parent))

    r_child = rdflib.BNode()
    g.add((r_child, RDF.type, OWL.Restriction))
    g.add((r_child, OWL.onProperty, is_played_by))
    union_node = rdflib.BNode()
    list_node1 = rdflib.BNode()
    list_node2 = rdflib.BNode()
    g.add((list_node1, RDF.first, corp))
    g.add((list_node1, RDF.rest, list_node2))
    g.add((list_node2, RDF.first, legal_person))
    g.add((list_node2, RDF.rest, RDF.nil))
    g.add((union_node, OWL.unionOf, list_node1))
    g.add((r_child, OWL.onClass, union_node))
    g.add((r_child, OWL.qualifiedCardinality, rdflib.Literal(1)))
    g.add((custodian, RDFS.subClassOf, role_cls))
    g.add((custodian, RDFS.subClassOf, r_child))

    # Identifier denotes/identifies + EntityLegalFormIdentifier code_classifier rule
    identifies = rdflib.URIRef(f"{cmns_id}identifies")
    denotes = rdflib.URIRef(f"{cmns_dsg}denotes")
    g.add((identifies, RDF.type, OWL.ObjectProperty))
    g.add((denotes, RDF.type, OWL.ObjectProperty))
    r_id = rdflib.BNode()
    g.add((r_id, RDF.type, OWL.Restriction))
    g.add((r_id, OWL.onProperty, identifies))
    g.add((r_id, OWL.onClass, corp))
    g.add((org_id, RDFS.subClassOf, r_id))
    g.add((elf_id, RDFS.subClassOf, org_id))

    # Symmetric disjointWith + inverseOf + functional + deprecated replacement
    g.add((corp, OWL.disjointWith, old_bond))
    has_p = rdflib.URIRef(f"{base}FBC/Products/hasParty")
    is_p_of = rdflib.URIRef(f"{base}FBC/Products/isPartyOf")
    g.add((has_p, RDF.type, OWL.ObjectProperty))
    g.add((has_p, RDF.type, OWL.FunctionalProperty))
    g.add((is_p_of, RDF.type, OWL.ObjectProperty))
    g.add((is_p_of, RDF.type, OWL.InverseFunctionalProperty))
    g.add((has_p, OWL.inverseOf, is_p_of))

    g.add((old_bond, OWL.deprecated, rdflib.Literal(True)))
    g.add((old_bond, CMNS_AV_REPLACED_BY, new_bond))
    g.add((old_bond, DCTERMS.isReplacedBy, new_bond))

    # Individuals: BusinessDayConvention (included), Currency (excluded), 360 Exchanges (capped at 350)
    bdc_ind = rdflib.URIRef(f"{base}FND/DatesAndTimes/Following")
    g.add((bdc_ind, RDF.type, OWL.NamedIndividual))
    g.add((bdc_ind, RDF.type, bdc_cls))
    g.add((bdc_ind, RDFS.label, rdflib.Literal("Following")))

    usd_ind = rdflib.URIRef(f"{base}FND/Accounting/ISO4217-CurrencyCodes/USD")
    g.add((usd_ind, RDF.type, OWL.NamedIndividual))
    g.add((usd_ind, RDF.type, ccy_cls))
    g.add((usd_ind, RDFS.label, rdflib.Literal("USD")))

    for i in range(360):
        ex_ind = rdflib.URIRef(f"{base}FBC/FunctionalEntities/Markets/EX{i:03d}")
        g.add((ex_ind, RDF.type, OWL.NamedIndividual))
        g.add((ex_ind, RDF.type, exch_cls))
        g.add((ex_ind, RDFS.label, rdflib.Literal(f"Exchange {i}")))

    classes, props, indivs = extract_fibo_graph_v2(g, modules=PROD_MODULES, include_cmns=True)
    assert str(party) in classes
    assert classes[str(party)]["label"] == "organization"
    assert set(classes[str(corp)]["synonyms"]) == {"Body Corporate", "CORP", "Corp"}
    assert classes[str(custodian)]["is_played_by"] == [str(corp)]  # leaf reduction removed party & legal_person!
    assert classes[str(custodian)]["archetype"] == "party_role"
    assert classes[str(org_id)]["archetype"] == "identifier"
    assert classes[str(org_id)]["identified_entity_uris"] == [str(corp)]
    assert classes[str(elf_id)]["archetype"] == "code_classifier"
    assert str(old_bond) in classes[str(corp)]["disjoint_with"]
    assert classes[str(old_bond)]["deprecated"] is True
    assert classes[str(old_bond)]["replaced_by"] == str(new_bond)

    assert props[str(has_p)]["is_functional"] is True
    assert props[str(is_p_of)]["is_inverse_functional"] is True
    assert str(is_p_of) in props[str(has_p)]["inverse_of"]
    assert str(has_p) in props[str(is_p_of)]["inverse_of"]

    assert str(bdc_ind) in indivs
    assert str(usd_ind) not in indivs
    ex_count = sum(1 for u in indivs if "/Markets/EX" in u)
    assert ex_count == 350


def test_archive_xml_ssrf_git_and_update_repo_gate_hardening(tmp_path: Path) -> None:
    # 1. XML pre-check rejects UTF-16 BOM, NUL, DOCTYPE, ENTITY
    for bad_xml in (
        b"\xff\xfe<rdf:RDF/>",
        b"\xfe\xff<rdf:RDF/>",
        b"\x00\x00\xfe\xff<rdf:RDF/>",
        b"<rdf:RDF>\x00</rdf:RDF>",
        b"<!DOCTYPE rdf:RDF><rdf:RDF/>",
        b"<!entity xxe 'evil'><rdf:RDF/>",
    ):
        with pytest.raises(ArchiveSecurityError):
            validate_xml_ontology_bytes(bad_xml, "evil.rdf")

    # load_fibo_from_directory triggers XML security error before rdflib parses
    bad_dir = tmp_path / "xml_bad"
    (bad_dir / "FND").mkdir(parents=True)
    (bad_dir / "FND" / "xxe.rdf").write_bytes(b"<!DOCTYPE x [<!ENTITY a '1'>]><rdf:RDF/>")
    with pytest.raises(ArchiveSecurityError):
        load_fibo_from_directory(bad_dir, allow_parse_errors=True)

    # 2. Zip directory entries (legit passes; traversal/ADS/reserved/reparse rejected)
    good_zip = tmp_path / "good_dirs.zip"
    with zipfile.ZipFile(good_zip, "w") as zf:
        zf.writestr("FND/", "")
        zf.writestr("FND/Ok.ttl", b"@prefix owl: <http://www.w3.org/2002/07/owl#> .\n")
    safe_extract_archive(good_zip, tmp_path / "out_good")
    assert (tmp_path / "out_good" / "FND" / "Ok.ttl").is_file()

    for bad_member in ("FND/sub:ads/", "FND/CON/", "FND/evil:stream.ttl", "FND/NUL.ttl"):
        bad_zip = tmp_path / f"bad_{abs(hash(bad_member))}.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr(bad_member, b"" if bad_member.endswith("/") else b"x")
        with pytest.raises(ArchiveSecurityError):
            safe_extract_archive(bad_zip, tmp_path / "out_bad")

    # 3. Tar compression ratio bomb (> 1 MB floor and > 50x)
    bomb_tar = tmp_path / "bomb.tar.gz"
    payload = b"\x00" * (1_200_000)
    with tarfile.open(bomb_tar, "w:gz") as tar:
        ti = tarfile.TarInfo(name="FND/huge.ttl")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))
    with pytest.raises(ArchiveSecurityError, match="compression ratio"):
        safe_extract_archive(bomb_tar, tmp_path / "out_tar_bomb")

    # 4. SSRF URL + redirect checks
    for bad_url in (
        "http://spec.edmcouncil.org/fibo/prod.ttl.zip",
        "https://127.0.0.1/prod.ttl.zip",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/prod.ttl.zip",
        "https://[::1]/prod.ttl.zip",
    ):
        with pytest.raises(ArchiveSecurityError):
            validate_https_url_no_ssrf(bad_url)

    # Redirect SSRF hop blocked
    redirect_resp = httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
    with (
        patch("scripts.vendor_fibo_archive.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]),
        patch.object(httpx.Client, "stream") as mock_stream,
    ):
        mock_stream.return_value.__enter__.return_value = redirect_resp
        with pytest.raises(ArchiveSecurityError, match="SSRF"):
            download_fibo_archive("https://example.org/prod.ttl.zip", tmp_path / "dl.zip")

    # 5. Hardened git invocation flags & env
    cache = tmp_path / "git_cache"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="2d73435a4419b66b7c9f44cb9763e35fe8ac7890\n"),
        ]
        ensure_fibo_source(cache_dir=cache, source_mode="git")
        first_call = mock_run.call_args_list[0]
        cmd = first_call.args[0]
        assert "protocol.allow=never" in cmd
        assert "protocol.https.allow=always" in cmd
        assert any(arg.startswith("core.hooksPath=") for arg in cmd)
        assert first_call.kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert first_call.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert first_call.kwargs["timeout"] == 120

    # 6. --update-repo starter-pack gate failure exits 2 with VERIFICATION_GATE_FAILED
    with patch(
        "scripts.vendor_fibo.verify_starter_packs_path_a",
        side_effect=OpenDVError("broken starter URI", error_code=ErrorCode.VERIFICATION_GATE_FAILED),
    ):
        code = vendor_main(["--update-repo", "--cache-dir", str(REPO_ROOT / ".opendv" / "cache" / "fibo")])
        assert code == 2


def test_fibo_pack_ops_install_acl_index_drift_and_r5_b1_repin(tmp_path: Path) -> None:
    # Confirm zero rdflib inside fibo_pack_ops.py
    ops_src = (REPO_ROOT / "src" / "opendv" / "service" / "fibo_pack_ops.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(ops_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name != "rdflib" for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "rdflib"

    base = FIBO_BASE_URI_PREFIX
    old_cls = f"{base}SEC/Debt/Bonds/OldBond"
    new_cls = f"{base}SEC/Debt/Bonds/Bond"
    old_prop = f"{base}FND/Parties/Parties/hasOldParty"
    new_prop = f"{base}FND/Parties/Parties/hasParty"
    removed_cls = f"{base}SEC/Debt/Bonds/RemovedBond"

    src_pack = tmp_path / "fibo-2026Q3-staging.json"
    _, expected_sha = _write_pack_v2_file(
        src_pack,
        deprecated_uri=old_cls,
        replacement_uri=new_cls,
    )
    state_dir = tmp_path / "user_state"

    # Bad filename digest suffix fails closed
    bad_named = tmp_path / "fibo-2026Q3-deadbeef.json"
    bad_named.write_bytes(src_pack.read_bytes())
    with pytest.raises(ReferenceOntologyCorruptError):
        install_fibo_pack(bad_named, tag="2026Q3", state_dir=state_dir)

    # UNC / ADS / reserved names rejected with SYNTAX_INVALID_ARGUMENT
    for bad_path in ("\\\\server\\share\\pack.json", "C:\\tmp\\pack.json:stream", "CON"):
        with pytest.raises(OpenDVError) as err_exc:
            install_fibo_pack(bad_path, state_dir=state_dir)
        assert err_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    resp = install_fibo_pack(src_pack, tag="2026Q3", state_dir=state_dir)
    assert resp.pack_sha256 == expected_sha
    assert resp.upstream_tag == "2026Q3"
    assert resp.installed_pack_path is not None
    installed_path = Path(resp.installed_pack_path)
    assert installed_path.name == f"fibo-2026Q3-{expected_sha[:8]}.json"
    assert verify_owner_only_permissions(installed_path)
    assert verify_owner_only_permissions(installed_path.parent)

    # safe_open_no_follow with stop_roots works at packs_dir
    fd = safe_open_no_follow(installed_path, os.O_RDONLY, stop_roots=(installed_path.parent,))
    os.close(fd)

    loaded_path, loaded = load_installed_pack(tag="2026Q3", state_dir=state_dir)
    assert loaded_path == installed_path
    assert loaded.pack_sha256 == expected_sha

    # Tampering packs_index.json hash triggers integrity error
    index_path = installed_path.parent / "packs_index.json"
    idx_data = json.loads(index_path.read_text(encoding="utf-8"))
    idx_data["packs"]["2026Q3"]["sha256"] = "not-a-64-hex-hash"
    index_path.write_text(json.dumps(idx_data), encoding="utf-8")
    with pytest.raises(ReferenceOntologyCorruptError):
        load_installed_pack(tag="2026Q3", state_dir=state_dir)

    # Re-install cleanly for 3-tier drift check across all 4 target_kinds
    install_fibo_pack(src_pack, tag="2026Q3", state_dir=state_dir)
    ws = tmp_path / "ws"
    mgr = ProjectStateManager(ws)
    mgr.initialize()
    proj = mgr.create_project("drift-proj", "asset_management", ProjectSettingsSnapshot())

    old_sha = "1" * 64
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:concept:bond",
                concept_name="Bond",
                description="Bond",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                attributes=[
                    ConceptAttribute(
                        name="party_ref",
                        logical_type="UTF8_STRING",
                        sql_type_hint="VARCHAR",
                        nullable=True,
                        is_business_key=False,
                        is_pii=False,
                        standard_property_uri=old_prop,
                    )
                ],
                external_mappings=[
                    ExternalMapping(
                        standard_uri=old_cls,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256=old_sha,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9100"),
                    ),
                    # Glossary / non-FIBO URI must be ignored by FIBO drift audit
                    ExternalMapping(
                        standard_uri="urn:opendv:glossary:custom_bond",
                        reference_pack_id="custom-glossary",
                        reference_pack_sha256="2" * 64,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9500"),
                    ),
                ],
            )
        ],
        relationships=[
            OntologyRelationship(
                relationship_uri="urn:opendv:rel:party",
                relationship_name="party",
                source_concept_uri="urn:opendv:concept:bond",
                target_concept_uri="urn:opendv:concept:bond",
                cardinality="MANY_TO_ONE",
                target_link="lnk_party",
                grain_semantics="party",
                description="party",
                standard_property_uri=old_prop,
            )
        ],
        enumerations=[
            OntologyEnumeration(
                enum_uri="urn:opendv:enum:removed",
                enumeration_name="Removed",
                target_reference_table="ref_removed",
                codes={"A": "A"},
                description="Removed enum",
                standard_uri=removed_cls,
            )
        ],
    )
    mgr.save_revision(
        "drift-proj",
        revision_id="rev-drift-1",
        rev_kind="ontology_version",
        rev_key="ontology:1.0.0",
        parent_rev_id=proj.active_ontology_rev_id,
        payload=ont,
        metadata=RevisionMetadata(actor="human", reason="test drift audit"),
    )
    mgr.update_project_pointers("drift-proj", active_ontology_rev_id="rev-drift-1")

    report = check_project_drift(mgr, "drift-proj", tag="2026Q3", state_dir=state_dir)
    assert report.pack_sha256_orig == old_sha
    assert report.pack_sha256_new == expected_sha
    assert report.repin_deltas_applied == 0
    by_kind_tier = {(it.target_kind, it.tier, it.replacement_uri) for it in report.items}
    assert ("concept", "pack_sha256_mismatch", None) in by_kind_tier
    assert ("concept", "deprecated_with_replacement", new_cls) in by_kind_tier
    assert ("attribute", "deprecated_with_replacement", new_prop) in by_kind_tier
    assert ("relationship", "deprecated_with_replacement", new_prop) in by_kind_tier
    assert ("enumeration", "removed_or_split", None) in by_kind_tier
    assert not any("urn:opendv:glossary" in it.old_standard_uri for it in report.items)

    # M2 Step 8: live apply_repin=True executes 36-op repin deltas for deprecated_with_replacement items
    repin_report = check_project_drift(
        mgr, "drift-proj", tag="2026Q3", state_dir=state_dir, apply_repin=True,
    )
    assert repin_report.repin_deltas_applied == 3
    repinned_proj = mgr.get_project("drift-proj")
    assert repinned_proj.active_ontology_rev_id is not None
    assert repinned_proj.active_ontology_rev_id != "rev-drift-1"
    repinned_rev = mgr.get_revision("drift-proj", repinned_proj.active_ontology_rev_id)
    repinned_ont = GraphAOntology.model_validate(repinned_rev["payload"])
    repinned_bond = next(c for c in repinned_ont.concepts if c.concept_uri == "urn:opendv:concept:bond")
    assert {m.standard_uri for m in repinned_bond.external_mappings} == {
        new_cls,
        "urn:opendv:glossary:custom_bond",
    }
    assert repinned_bond.attributes[0].standard_property_uri == new_prop
    assert repinned_ont.relationships[0].standard_property_uri == new_prop
    assert repinned_ont.enumerations[0].standard_uri == removed_cls

    # Pre-Step-4 project with active_ontology_rev_id is None returns empty 0-item report
    mgr.create_project("empty-rev-proj", "asset_management", ProjectSettingsSnapshot())
    empty_report = check_project_drift(
        mgr, "empty-rev-proj", tag="2026Q3", state_dir=state_dir, apply_repin=True,
    )
    assert empty_report.items == []
    assert empty_report.repin_deltas_applied == 0

    # R5-B1 receipt verification helper tests
    fp = "f" * 64
    verified_rcpt = GroundingReceipt(
        target_kind="column",
        table_id="src1::bonds",
        source_column="bond_type",
        schema_fingerprint=fp,
        standard_uri=old_cls,
        reference_pack_id="fibo-core",
        reference_pack_sha256=old_sha,
        confidence=Decimal("0.9300"),
        mapping_type="skos:exactMatch",
        provenance="server_verified",
    )
    imported_rcpt = verified_rcpt.model_copy(
        update={"receipt_id": "grnd_imported1234", "provenance": "imported_unverified"}
    )
    concept_bound = CanonicalBusinessConcept(
        concept_uri="urn:opendv:concept:bond",
        concept_name="Bond",
        description="Bond",
        key_authority_tier="TIER_1_GLOBAL_STANDARD",
        table_bindings=[
            ConceptTableBinding(source_id="src1", table_id="src1::bonds", col_name="bond_type")
        ],
    )
    mapping_verified = ExternalMapping(
        standard_uri=old_cls,
        reference_pack_id="fibo-core",
        reference_pack_sha256=old_sha,
        mapping_type="skos:exactMatch",
        confidence=Decimal("0.9300"),
        grounding_receipt_id=verified_rcpt.receipt_id,
    )

    # 1. Valid server_verified receipt matching table_bindings + fingerprint preserves max confidence
    fresh, conf, rid = plan_repin_with_receipt_verification(
        concept=concept_bound,
        prior_mapping=mapping_verified,
        replacement_uri=new_cls,
        new_pack_id="fibo-core",
        new_pack_sha256=expected_sha,
        lexical_score=Decimal("0.7100"),
        receipts_by_id={verified_rcpt.receipt_id: verified_rcpt},
        table_fingerprints={"src1::bonds": fp},
    )
    assert fresh is not None and rid == fresh.receipt_id
    assert fresh.provenance == "server_verified"
    assert fresh.standard_uri == new_cls
    assert conf == Decimal("0.9300")

    # 2. imported_unverified receipt is blocked -> falls back strictly to lexical_score + None receipt
    mapping_imp = mapping_verified.model_copy(
        update={"grounding_receipt_id": imported_rcpt.receipt_id}
    )
    fresh_imp, conf_imp, rid_imp = plan_repin_with_receipt_verification(
        concept=concept_bound,
        prior_mapping=mapping_imp,
        replacement_uri=new_cls,
        new_pack_id="fibo-core",
        new_pack_sha256=expected_sha,
        lexical_score=Decimal("0.7100"),
        receipts_by_id={imported_rcpt.receipt_id: imported_rcpt},
        table_fingerprints={"src1::bonds": fp},
    )
    assert fresh_imp is None and rid_imp is None and conf_imp == Decimal("0.7100")

    # 3. Empty table_bindings (e.g. .opendv.zip document-only concept) blocks laundering!
    concept_unbound = concept_bound.model_copy(update={"table_bindings": []})
    fresh_unb, conf_unb, rid_unb = plan_repin_with_receipt_verification(
        concept=concept_unbound,
        prior_mapping=mapping_verified,
        replacement_uri=new_cls,
        new_pack_id="fibo-core",
        new_pack_sha256=expected_sha,
        lexical_score=Decimal("0.7100"),
        receipts_by_id={verified_rcpt.receipt_id: verified_rcpt},
        table_fingerprints={"src1::bonds": fp},
    )
    assert fresh_unb is None and rid_unb is None and conf_unb == Decimal("0.7100")


def _create_dir_link(link_path: Path, target_path: Path) -> None:
    """Create a directory symlink (POSIX) or unprivileged NTFS junction (Windows)."""
    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        os.symlink(target_path, link_path, target_is_directory=True)


def _remove_dir_link(link_path: Path) -> None:
    if not os.path.lexists(link_path):
        return
    if sys.platform == "win32" and not link_path.is_symlink():
        os.rmdir(link_path)
    else:
        link_path.unlink()


def test_zip_guard_stop_root_junction_and_leaf_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: stop_root that is itself a junction/symlink stops cleanly; leaf/inner still rejected."""
    real_root = tmp_path / "real_root"
    leaf_dir = real_root / "sub" / "leaf_dir"
    leaf_dir.mkdir(parents=True)
    data_file = leaf_dir / "data.json"
    data_file.write_text('{"ok": true}', encoding="utf-8")
    direct_file = real_root / "direct.json"
    direct_file.write_text('{"direct": true}', encoding="utf-8")

    junc_root = tmp_path / "junc_root"
    inner_junc = real_root / "inner_junc"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text('{"secret": true}', encoding="utf-8")

    _create_dir_link(junc_root, real_root)
    _create_dir_link(inner_junc, outside)
    try:
        # 1. stop_roots=[junc_root] allows opening descendants even though junc_root itself is a junction
        fd = safe_open_no_follow(
            junc_root / "sub" / "leaf_dir" / "data.json",
            os.O_RDONLY,
            stop_roots=(junc_root,),
        )
        os.close(fd)

        fd2 = safe_open_no_follow(
            junc_root / "direct.json",
            os.O_RDONLY,
            stop_roots=(junc_root,),
        )
        os.close(fd2)

        # 2. Without stop_roots, junc_root is rejected
        with pytest.raises(OpenDVError) as exc_no_stop:
            safe_open_no_follow(junc_root / "sub" / "leaf_dir" / "data.json", os.O_RDONLY)
        assert exc_no_stop.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

        # 3. Inner junction strictly below stop_root is still rejected
        with pytest.raises(OpenDVError) as exc_inner:
            safe_open_no_follow(
                inner_junc / "secret.json",
                os.O_RDONLY,
                stop_roots=(real_root,),
            )
        assert exc_inner.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

        # 4. Passing the junction itself as target leaf is always rejected even if in stop_roots
        with pytest.raises(OpenDVError) as exc_self_leaf:
            safe_open_no_follow(junc_root, os.O_RDONLY, stop_roots=(junc_root,))
        assert exc_self_leaf.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

        # 5. Symlink/reparse leaf file inside stop_root is always rejected
        target_leaf = junc_root / "direct.json"
        import opendv.service.bundles.zip_guard as zip_guard_mod

        orig_is_reparse = zip_guard_mod._is_reparse_or_symlink
        monkeypatch.setattr(
            zip_guard_mod,
            "_is_reparse_or_symlink",
            lambda p: True if p == target_leaf else orig_is_reparse(p),
        )
        with pytest.raises(OpenDVError) as exc_leaf:
            safe_open_no_follow(target_leaf, os.O_RDONLY, stop_roots=(junc_root,))
        assert exc_leaf.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    finally:
        _remove_dir_link(inner_junc)
        _remove_dir_link(junc_root)


def test_fibo_pack_ops_rejects_junctions_and_deprecated_chain_terminals(tmp_path: Path) -> None:
    """Regression: no .resolve() before no-follow open; deprecated replacement chains return None."""
    base = FIBO_BASE_URI_PREFIX
    chain_start = f"{base}SEC/Debt/Bonds/ChainStart"
    mid_dep_dead = f"{base}SEC/Debt/Bonds/MidDepDead"
    chain_active_start = f"{base}SEC/Debt/Bonds/ChainActiveStart"
    mid_dep_live = f"{base}SEC/Debt/Bonds/MidDepLive"
    active_bond = f"{base}SEC/Debt/Bonds/Bond"
    cycle_a = f"{base}SEC/Debt/Bonds/CycleA"
    cycle_b = f"{base}SEC/Debt/Bonds/CycleB"

    classes: dict[str, object] = {
        active_bond: {
            "uri": active_bond,
            "label": "Bond",
            "definition": "Active bond.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": False,
        },
        mid_dep_dead: {
            "uri": mid_dep_dead,
            "label": "Mid Deprecated Dead",
            "definition": "Deprecated terminal.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
        },
        chain_start: {
            "uri": chain_start,
            "label": "Chain Start",
            "definition": "Points to deprecated terminal.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
            "replaced_by": mid_dep_dead,
        },
        mid_dep_live: {
            "uri": mid_dep_live,
            "label": "Mid Deprecated Live",
            "definition": "Points to active bond.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
            "replaced_by": active_bond,
        },
        chain_active_start: {
            "uri": chain_active_start,
            "label": "Chain Active Start",
            "definition": "Multi-hop to active bond.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
            "replaced_by": mid_dep_live,
        },
        cycle_a: {
            "uri": cycle_a,
            "label": "Cycle A",
            "definition": "Cycle A.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
            "replaced_by": cycle_b,
        },
        cycle_b: {
            "uri": cycle_b,
            "label": "Cycle B",
            "definition": "Cycle B.",
            "module": "SEC",
            "synonyms": [],
            "sub_class_of": [],
            "archetype": "entity",
            "deprecated": True,
            "replaced_by": cycle_a,
        },
    }
    props: dict[str, object] = {
        f"{base}SEC/Debt/Bonds/hasMaturityDate": {
            "uri": f"{base}SEC/Debt/Bonds/hasMaturityDate",
            "label": "has maturity date",
            "definition": "Maturity date.",
            "module": "SEC",
            "property_type": "DatatypeProperty",
            "domain": [active_bond],
            "range": [],
            "sub_property_of": [],
            "inverse_of": [],
            "is_functional": True,
            "is_inverse_functional": False,
            "deprecated": False,
        }
    }
    raw_bytes, _, sha_full, _ = compile_fibo_pack(
        classes,  # type: ignore[arg-type]
        props,  # type: ignore[arg-type]
        individuals={},
        upstream_tag="2026Q3",
        upstream_commit="2d73435a4419b66b7c9f44cb9763e35fe8ac7890",
        modules=PROD_MODULES,
    )

    real_src_dir = tmp_path / "real_src"
    real_src_dir.mkdir()
    src_pack = real_src_dir / "fibo-2026Q3-staging.json"
    src_pack.write_bytes(raw_bytes)
    junc_src_dir = tmp_path / "junc_src"
    _create_dir_link(junc_src_dir, real_src_dir)

    state_dir = tmp_path / "user_state"
    ws = tmp_path / "ws"
    mgr = ProjectStateManager(ws)
    mgr.initialize()
    proj = mgr.create_project("chain-proj", "asset_management", ProjectSettingsSnapshot())
    ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:concept:dead_chain",
                concept_name="DeadChain",
                description="Dead chain",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                external_mappings=[
                    ExternalMapping(
                        standard_uri=chain_start,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256=sha_full,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9000"),
                    )
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:concept:live_chain",
                concept_name="LiveChain",
                description="Live chain",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                external_mappings=[
                    ExternalMapping(
                        standard_uri=chain_active_start,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256=sha_full,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9000"),
                    )
                ],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:concept:cycle_chain",
                concept_name="CycleChain",
                description="Cycle chain",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                external_mappings=[
                    ExternalMapping(
                        standard_uri=cycle_a,
                        reference_pack_id="fibo-core",
                        reference_pack_sha256=sha_full,
                        mapping_type="skos:exactMatch",
                        confidence=Decimal("0.9000"),
                    )
                ],
            ),
        ],
    )
    mgr.save_revision(
        "chain-proj",
        revision_id="rev-chain-1",
        rev_kind="ontology_version",
        rev_key="ontology:1.0.0",
        parent_rev_id=proj.active_ontology_rev_id,
        payload=ont,
        metadata=RevisionMetadata(actor="human", reason="test chain drift"),
    )
    mgr.update_project_pointers("chain-proj", active_ontology_rev_id="rev-chain-1")

    try:
        # 1. install_fibo_pack & check_project_drift(pack_file=...) must reject junction parents
        with pytest.raises(OpenDVError) as exc_inst:
            install_fibo_pack(junc_src_dir / src_pack.name, tag="2026Q3", state_dir=state_dir)
        assert exc_inst.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

        with pytest.raises(OpenDVError) as exc_drift_src:
            check_project_drift(mgr, "chain-proj", pack_file=junc_src_dir / src_pack.name)
        assert exc_drift_src.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    finally:
        _remove_dir_link(junc_src_dir)

    # 2. Install cleanly and verify replacement chain semantics
    resp = install_fibo_pack(src_pack, tag="2026Q3", state_dir=state_dir)
    installed_path = Path(resp.installed_pack_path or "")
    report = check_project_drift(mgr, "chain-proj", tag="2026Q3", state_dir=state_dir)
    by_concept = {it.target_id: (it.tier, it.replacement_uri) for it in report.items}
    assert by_concept["urn:opendv:concept:dead_chain"] == ("removed_or_split", None)
    assert by_concept["urn:opendv:concept:live_chain"] == (
        "deprecated_with_replacement",
        active_bond,
    )
    assert by_concept["urn:opendv:concept:cycle_chain"] == ("removed_or_split", None)

    # 3. load_installed_pack must reject intra-packs_dir junction masked by .resolve()
    packs_dir = installed_path.parent
    inner_junc = packs_dir / "inner_junc"
    _create_dir_link(inner_junc, packs_dir)
    index_path = packs_dir / "packs_index.json"
    idx_data = json.loads(index_path.read_text(encoding="utf-8"))
    idx_data["packs"]["2026Q3"]["pack_path"] = str(inner_junc / installed_path.name)
    index_path.write_text(json.dumps(idx_data), encoding="utf-8")
    try:
        with pytest.raises(OpenDVError) as exc_load_junc:
            load_installed_pack(tag="2026Q3", state_dir=state_dir)
        assert exc_load_junc.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    finally:
        _remove_dir_link(inner_junc)

    # 4. load_installed_pack must also reject lexical path escaping packs_dir
    outside_pack = tmp_path / "outside.json"
    outside_pack.write_bytes(raw_bytes)
    idx_data["packs"]["2026Q3"]["pack_path"] = str(outside_pack)
    index_path.write_text(json.dumps(idx_data), encoding="utf-8")
    with pytest.raises(OpenDVError) as exc_load_escape:
        load_installed_pack(tag="2026Q3", state_dir=state_dir)
    assert exc_load_escape.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
