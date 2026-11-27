"""UAT-V25-SVC-03 (M2A): ADR-008 BM25, FIBO integrity, citations, OCR receipts & Turtle."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest
import rdflib

from opendv.grounding.kernel.lexical import LexicalIndex, load_lexical_matcher_config
from opendv.grounding.providers.fibo import (
    FINANCIAL_ACRONYMS,
    FiboPackQueryAdapter,
    FiboReferenceOntologyLoader,
)
from opendv.models.errors import ErrorCode, OpenDVError, ReferenceOntologyCorruptError
from opendv.models.events import OcrPageReceipt
from opendv.models.grounding_spi import KnowledgeItem
from opendv.models.ontology import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
    OntologyEnumeration,
)
from opendv.models.wire_dtos_discovery import CitationResolveReq, FiboQueryReq
from opendv.service.citations import ensure_no_redacted_placeholder, resolve_citation
from opendv.service.turtle_writer import export_ontology_turtle, write_ontology_turtle
from opendv.sources.document_map import (
    DocumentMapBuilder,
    compute_canonical_text_sha256,
    normalize_page_text,
)
from opendv.sources.starter_packs import get_starter_pack_ontology

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bm25_golden_vectors_and_schema_validation() -> None:
    cfg_path = REPO_ROOT / "schemas" / "v2" / "matchers" / "bm25_fibo_v1.json"
    schema_path = REPO_ROOT / "schemas" / "v2" / "bm25_fibo_v1.schema.json"
    cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=cfg_data, schema=schema_data)

    cfg = load_lexical_matcher_config(cfg_path)
    assert cfg.matcher_id == "bm25_fibo_v1"

    vectors = json.loads((REPO_ROOT / "vectors" / "bm25.json").read_text(encoding="utf-8"))
    idx = LexicalIndex(acronyms=FINANCIAL_ACRONYMS)

    # 1. Tokenization vectors
    for vec in vectors["tokenization_vectors"]:
        assert idx.tokenize(vec["input"]) == vec["expected_tokens"], vec["description"]

    # 2. Quantization boundary vectors (ROUND_HALF_EVEN)
    for vec in vectors["quantization_boundary_vectors"]:
        q = idx.quantize_score(Decimal(vec["raw_normalized_decimal"]))
        assert float(q) == pytest.approx(vec["expected_quantized"]), vec["description"]
        assert idx.is_grounded_score(q) is vec["is_grounded"]

    # 3. Hit-gate vectors (0.6400 cap)
    for vec in vectors["hit_gate_vectors"]:
        capped = idx.apply_hit_gate(
            Decimal(vec["definition_match_raw"]),
            has_name_or_syn_hit=bool(vec["has_name_hit"] or vec["has_synonym_hit"]),
        )
        assert float(capped) == pytest.approx(vec["expected_capped"]), vec["description"]
        assert idx.is_grounded_score(capped) is vec["is_grounded"]

    # 4. Scoring vectors
    for vec in vectors["scoring_vectors"]:
        items = [
            KnowledgeItem(
                entry_id=c["concept_uri"],
                label=c["concept_name"],
                entry_kind="class",
                definition=c["definition"],
                aliases=c["synonyms"],
            )
            for c in vec["corpus"]
        ]
        vec_idx = LexicalIndex(acronyms=FINANCIAL_ACRONYMS)
        vec_idx.index_items(items)
        hits = vec_idx.match(vec["query"], top_k=5)
        target_hit = next(h for h in hits if h.entry_id == vec["target_uri"])
        assert float(target_hit.raw_score) == pytest.approx(vec["expected_normalized_score"])
        assert vec_idx.is_grounded_score(target_hit.raw_score) is vec["is_grounded"]


def test_bm25_real_fibo_pack_query_and_integrity_failure(tmp_path: Path) -> None:
    matcher = FiboPackQueryAdapter()
    resp = matcher.query(FiboQueryReq(query="Legal Entity", domain_namespace="asset_management"))
    assert resp.matches
    top = resp.matches[0]
    assert "LegalEntity" in top.concept_uri or "legal entity" in top.label.lower()
    assert top.normalized_score == "1.0000"
    assert top.suggested_path == "PATH_A1"

    close_resp = matcher.query(FiboQueryReq(query="custody legal entity account", top_k=5))
    assert close_resp.matches
    assert any(m.suggested_path == "PATH_A2" for m in close_resp.matches)

    # Corrupt fibo-core-pack.json digest raises REFERENCE_PACK_INTEGRITY_FAILURE
    src_pack = (
        REPO_ROOT / "src" / "opendv" / "grounding" / "providers" / "fibo" / "fibo-core-pack.json"
    )
    orig_sha8 = matcher.loader.pack_sha256_prefix
    tampered = tmp_path / f"fibo-core-{orig_sha8}.json"
    payload = json.loads(src_pack.read_text(encoding="utf-8"))
    payload["metadata"]["tampered"] = True
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReferenceOntologyCorruptError) as exc_info:
        FiboReferenceOntologyLoader(pack_path=tampered, verify_integrity=True)
    assert exc_info.value.error_code == ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE

    # Even if preloaded without verify_integrity, FiboPackQueryAdapter rejects unverified loader
    unverified_loader = FiboReferenceOntologyLoader(pack_path=tampered, verify_integrity=False)
    with pytest.raises(ReferenceOntologyCorruptError) as matcher_exc:
        FiboPackQueryAdapter(loader=unverified_loader)
    assert matcher_exc.value.error_code == ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE


def test_citations_against_real_public_am_pdf_and_markdown_and_ocr() -> None:
    builder = DocumentMapBuilder()
    pdf_path = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    pdf_map, pdf_pages = builder.build_from_path(
        source_id="doc_custody_pdf",
        path=pdf_path,
        doc_format="pdf",
        run_ocr=False,
    )
    parsed_pdf = {idx + 1: text for idx, text in enumerate(pdf_pages)}
    assert pdf_map.pages[3].is_scanned_or_low_density is True  # page 4 scanned gate

    quote = "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE"
    p5_sha = pdf_map.pages[4].canonical_text_sha256

    occ1 = resolve_citation(
        CitationResolveReq(
            doc_id="doc_custody_pdf",
            page_number=5,
            verbatim_quote=quote,
            occurrence=1,
            content_sha256=p5_sha,
        ),
        parsed_pages=parsed_pdf,
    )
    assert occ1.matched is True
    assert occ1.source_kind == "canonical_text"
    assert occ1.char_start == 0
    assert occ1.char_end == 41

    occ2 = resolve_citation(
        CitationResolveReq(
            doc_id="doc_custody_pdf",
            page_number=5,
            verbatim_quote=quote,
            occurrence=2,
            content_sha256=p5_sha,
        ),
        parsed_pages=parsed_pdf,
    )
    assert occ2.char_start == 258
    assert occ2.char_end == 299
    assert occ2.resolved_sha256 == p5_sha

    with pytest.raises(OpenDVError) as occ3_exc:
        resolve_citation(
            CitationResolveReq(
                doc_id="doc_custody_pdf",
                page_number=5,
                verbatim_quote=quote,
                occurrence=3,
            ),
            parsed_pages=parsed_pdf,
        )
    assert occ3_exc.value.error_code == ErrorCode.CITATION_SPAN_UNRESOLVED

    # Real Markdown domain note + CRLF/tab whitespace normalization
    md_path = REPO_ROOT / "data" / "public_am" / "am_stewardship_domain_notes.md"
    md_map, md_pages = builder.build_from_path(
        source_id="doc_notes",
        path=md_path,
        doc_format="markdown",
    )
    md_parsed = {1: md_pages[0].replace("\n", "\r\n").replace(" ", "\t")}
    md_span = resolve_citation(
        CitationResolveReq(
            doc_id="doc_notes",
            page_number=1,
            verbatim_quote="StewardshipEngagementCase",
            occurrence=1,
            content_sha256=md_map.pages[0].canonical_text_sha256,
        ),
        parsed_pages=md_parsed,
    )
    assert md_span.matched is True
    assert md_span.char_start is not None

    # OCR receipt fallback for real scanned page 4 ('Scan p4' in parsed_pdf + pdf_map)
    assert parsed_pdf[4].strip() == "Scan p4"
    ocr_text = "Custody Omnibus Segregation\nScanned Annex Clause 4"
    ocr_sha = compute_canonical_text_sha256(ocr_text)
    ocr_receipt = OcrPageReceipt(
        receipt_id="ocr-p4-1",
        doc_id="doc_custody_pdf",
        page_number=4,
        ocr_text_masked=normalize_page_text(ocr_text),
        content_sha256=ocr_sha,
        ocr_box_count=3,
    )
    # Real parsed_pdf + document_map=pdf_map WITH req.content_sha256=ocr_sha
    ocr_span_with_sha = resolve_citation(
        CitationResolveReq(
            doc_id="doc_custody_pdf",
            page_number=4,
            verbatim_quote="Scanned Annex Clause 4",
            occurrence=1,
            content_sha256=ocr_sha,
        ),
        parsed_pages=parsed_pdf,
        ocr_receipts=[ocr_receipt],
        document_map=pdf_map,
    )
    assert ocr_span_with_sha.source_kind == "ocr_receipt"
    assert ocr_span_with_sha.matched is True
    assert ocr_span_with_sha.resolved_sha256 == ocr_sha

    # Real parsed_pdf + document_map=pdf_map WITH req.content_sha256=None
    ocr_span_no_sha = resolve_citation(
        CitationResolveReq(
            doc_id="doc_custody_pdf",
            page_number=4,
            verbatim_quote="Scanned Annex Clause 4",
            occurrence=1,
            content_sha256=None,
        ),
        parsed_pages=parsed_pdf,
        ocr_receipts=[ocr_receipt],
        document_map=pdf_map,
    )
    assert ocr_span_no_sha.source_kind == "ocr_receipt"
    assert ocr_span_no_sha.matched is True
    assert ocr_span_no_sha.resolved_sha256 == ocr_sha

    # Without OCR receipt, scanned page 4 fails to resolve quote from 'Scan p4'
    with pytest.raises(OpenDVError) as no_ocr_exc:
        resolve_citation(
            CitationResolveReq(
                doc_id="doc_custody_pdf",
                page_number=4,
                verbatim_quote="Scanned Annex Clause 4",
                occurrence=1,
            ),
            parsed_pages=parsed_pdf,
            document_map=pdf_map,
        )
    assert no_ocr_exc.value.error_code == ErrorCode.CITATION_SPAN_UNRESOLVED

    # Metadata-only binary absent: stored digest match succeeds with source_kind="absent"
    absent_ok = resolve_citation(
        CitationResolveReq(
            doc_id="doc_custody_pdf",
            page_number=5,
            verbatim_quote=quote,
            occurrence=2,
            content_sha256=p5_sha,
        ),
        metadata_only_binary_absent=True,
        document_map=pdf_map,
    )
    assert absent_ok.source_kind == "absent"
    assert absent_ok.matched is True
    assert absent_ok.char_start is None
    assert absent_ok.resolved_sha256 == p5_sha

    # Absent binary without digest raises SOURCE_BINARY_ABSENT
    with pytest.raises(OpenDVError) as absent_exc:
        resolve_citation(
            CitationResolveReq(doc_id="doc_custody_pdf", page_number=5, verbatim_quote=quote),
            metadata_only_binary_absent=True,
        )
    assert absent_exc.value.error_code == ErrorCode.SOURCE_BINARY_ABSENT

    # Redacted placeholder leak rejection
    for leaked in ("Holdings for <MASKED_PII> account", "Token [REDACTED_SSN] verified"):
        with pytest.raises(OpenDVError) as leak_exc:
            ensure_no_redacted_placeholder(leaked)
        assert leak_exc.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK

        with pytest.raises(OpenDVError) as cit_leak_exc:
            resolve_citation(
                CitationResolveReq(doc_id="doc_custody_pdf", page_number=5, verbatim_quote=leaked),
                parsed_pages=parsed_pdf,
            )
        assert cit_leak_exc.value.error_code == ErrorCode.REDACTED_PLACEHOLDER_LEAK


def test_citations_reject_tampered_ocr_receipt_self_digest() -> None:
    builder = DocumentMapBuilder()
    pdf_path = REPO_ROOT / "data" / "public_am" / "am_custody_and_voting_guide.pdf"
    pdf_map, pdf_pages = builder.build_from_path(
        source_id="doc_custody_pdf",
        path=pdf_path,
        doc_format="pdf",
        run_ocr=False,
    )
    parsed_pdf = {idx + 1: text for idx, text in enumerate(pdf_pages)}
    assert pdf_map.pages[3].is_scanned_or_low_density is True
    assert pdf_map.pages[3].ocr_box_count == 0

    ocr_text = "Custody Omnibus Segregation\nScanned Annex Clause 4"
    ocr_sha = compute_canonical_text_sha256(ocr_text)

    # (a) Tampered OcrPageReceipt.content_sha256 ("0"*64) under pre-OCR scanned DocumentMap
    tampered_sha_receipt = OcrPageReceipt(
        receipt_id="ocr-p4-tampered-sha",
        doc_id="doc_custody_pdf",
        page_number=4,
        ocr_text_masked=normalize_page_text(ocr_text),
        content_sha256="0" * 64,
        ocr_box_count=3,
    )
    for req_sha, absent in (
        ("0" * 64, False),
        (None, False),
        ("0" * 64, True),
        (None, True),
    ):
        with pytest.raises(OpenDVError) as exc_info:
            resolve_citation(
                CitationResolveReq(
                    doc_id="doc_custody_pdf",
                    page_number=4,
                    verbatim_quote="Scanned Annex Clause 4",
                    occurrence=1,
                    content_sha256=req_sha,
                ),
                parsed_pages=None if absent else parsed_pdf,
                ocr_receipts=[tampered_sha_receipt],
                metadata_only_binary_absent=absent,
                document_map=pdf_map,
            )
        assert exc_info.value.error_code == ErrorCode.CITATION_SPAN_UNRESOLVED

    # (b) Tampered OcrPageReceipt.ocr_text_masked keeping original valid content_sha256
    tampered_text_receipt = OcrPageReceipt(
        receipt_id="ocr-p4-tampered-text",
        doc_id="doc_custody_pdf",
        page_number=4,
        ocr_text_masked="Tampered OCR payload containing Scanned Annex Clause 4",
        content_sha256=ocr_sha,
        ocr_box_count=3,
    )
    for req_sha in (ocr_sha, None):
        with pytest.raises(OpenDVError) as exc_info:
            resolve_citation(
                CitationResolveReq(
                    doc_id="doc_custody_pdf",
                    page_number=4,
                    verbatim_quote="Scanned Annex Clause 4",
                    occurrence=1,
                    content_sha256=req_sha,
                ),
                parsed_pages=parsed_pdf,
                ocr_receipts=[tampered_text_receipt],
                document_map=pdf_map,
            )
        assert exc_info.value.error_code == ErrorCode.CITATION_SPAN_UNRESOLVED


def test_pure_python_turtle_writer_round_trip_and_iri_validation() -> None:
    import ast

    writer_src = (REPO_ROOT / "src" / "opendv" / "service" / "turtle_writer.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(writer_src)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "rdflib" not in imported

    ibor_ont = get_starter_pack_ontology("multi_asset_ibor")
    # Augment with tricky literal chars (quotes, newlines, backslashes, tabs) and enumeration
    augmented = ibor_ont.model_copy(
        update={
            "enumerations": [
                OntologyEnumeration(
                    enum_uri="urn:opendv:enum:currency_code",
                    enumeration_name="CurrencyCode",
                    target_reference_table="ref_currency_code",
                    codes={"USD": 'US "Dollar" \\ ISO\nLine 2\tTab'},
                    description='ISO "4217" currency codes',
                )
            ]
        }
    )
    ttl_text, media_type = export_ontology_turtle(augmented)
    assert media_type == "text/turtle"

    # Parse generated Turtle via rdflib in test to prove W3C Turtle 1.1 compliance
    g = rdflib.Graph()
    g.parse(data=ttl_text, format="turtle")
    assert len(g) > 20
    assert (
        rdflib.URIRef("urn:opendv:enum:currency_code"),
        rdflib.RDF.type,
        rdflib.RDFS.Datatype,
    ) in g

    # Malformed IRI rejected with SYNTAX_INVALID_ARGUMENT (including C1 control chars)
    for bad_iri in ("https:///missing-host", "urn:opendv:bad\u0081iri"):
        bad_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="asset_management",
            concepts=[
                CanonicalBusinessConcept(
                    concept_uri=bad_iri,
                    concept_name="Broken",
                    description="Broken IRI",
                    key_authority_tier="TIER_1_GLOBAL_STANDARD",
                )
            ],
        )
        with pytest.raises(OpenDVError) as exc_info:
            write_ontology_turtle(bad_ont)
        assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    # Tiny Decimal confidence formats without scientific E-notation for xsd:decimal
    tiny_conf_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="asset_management",
        concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:opendv:concept:tiny_conf",
                concept_name="TinyConf",
                description="Decimal format check",
                key_authority_tier="TIER_1_GLOBAL_STANDARD",
                external_mappings=[
                    ExternalMapping(
                        standard_uri="https://spec.edmcouncil.org/fibo/ontology/Tiny",
                        reference_pack_id="fibo-core",
                        reference_pack_sha256="a" * 64,
                        mapping_type="skos:closeMatch",
                        confidence=Decimal("1E-7"),
                    )
                ],
            )
        ],
    )
    tiny_ttl = write_ontology_turtle(tiny_conf_ont)
    assert '"0.0000001"^^xsd:decimal' in tiny_ttl
    assert "1E-7" not in tiny_ttl
    rdflib.Graph().parse(data=tiny_ttl, format="turtle")
