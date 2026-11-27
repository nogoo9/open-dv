"""M2.2 tests for GlossaryKnowledgeSource, YAML loader security, and ReDoS guard."""

from __future__ import annotations

import pytest

from opendv.grounding import KnowledgeSource
from opendv.grounding.providers.glossary import (
    MAX_GLOSSARY_BYTES,
    GlossaryKnowledgeSource,
    get_example_glossary_path,
    load_glossary,
    validate_safe_regex,
)
from opendv.grounding.structured import ColumnGroundingEngine
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.grounding import (
    ColumnEvidence,
    GroundingPolicy,
    GroundingRequest,
    TableEvidence,
)


def _det_policy() -> GroundingPolicy:
    return GroundingPolicy(mode="deterministic_only", skip_reason="glossary unit test")


def test_example_glossary_loads_and_covers_all_kinds() -> None:
    """Bundled example_glossary.yaml loads cleanly and maps all 6 glossary kinds."""
    src = GlossaryKnowledgeSource()
    assert isinstance(src, KnowledgeSource)
    assert get_example_glossary_path().is_file()
    assert src.calibration_params is None
    assert src.metadata.anchor_entry_kinds == frozenset({"glossary_term"})
    assert src.metadata.origin_kind == "reference_standard"

    cust = src.get_item("urn:opendv:glossary:customer")
    assert cust is not None and cust.entry_kind == "glossary_term" and cust.semantic_context.archetype == "entity"

    inv = src.get_item("urn:opendv:glossary:billing_invoice")
    assert inv is not None and inv.entry_kind == "glossary_term" and inv.semantic_context.archetype == "event_occurrence"

    cid = src.get_item("urn:opendv:glossary:customer_id")
    assert cid is not None and cid.semantic_context.archetype == "identifier"
    assert cid.semantic_context.identified_entity_uri == "urn:opendv:glossary:customer"

    amt = src.get_item("urn:opendv:glossary:order_total_amount")
    assert amt is not None and amt.semantic_context.archetype == "value_object"

    status = src.get_item("urn:opendv:glossary:subscription_status")
    assert status is not None and status.semantic_context.archetype == "code_classifier"

    email = src.get_item("urn:opendv:glossary:customer_email")
    assert email is not None and email.semantic_context.pii_category_hint == "EMAIL"

    # R5-M12: kind: relationship -> ("object_property", None), excluded from anchor recall
    rel = src.get_item("urn:opendv:glossary:billed_to_customer")
    assert rel is not None
    assert rel.entry_kind == "object_property"
    assert rel.semantic_context.archetype is None
    assert rel.semantic_context.domain_entity_uri == "urn:opendv:glossary:billing_invoice"
    assert rel.semantic_context.target_entity_uri == "urn:opendv:glossary:customer"
    anchor_hits = src.recall_by_text("billed to customer", entry_kinds=src.metadata.anchor_entry_kinds)
    assert all(h.entry_id != "urn:opendv:glossary:billed_to_customer" for h in anchor_hits)


def test_glossary_bytes_size_limit_and_yaml_anchor_alias_rejection() -> None:
    """Verify pre-read bytes loading (R5-M2), >2MB rejection, and YAML anchor/alias blocking."""
    raw_bytes = get_example_glossary_path().read_bytes()
    src = GlossaryKnowledgeSource(glossary_bytes=raw_bytes)
    assert src.get_item("urn:opendv:glossary:customer") is not None

    oversized = b"version: 1\nterms:\n  t:\n    label: T\n    definition: " + b"a" * (MAX_GLOSSARY_BYTES + 10)
    with pytest.raises(OpenDVError) as size_exc:
        load_glossary(glossary_bytes=oversized)
    assert size_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    yaml_with_alias = """
version: 1
terms:
  base_term: &anchor_base
    label: Base Term
    definition: Base definition
  aliased_term: *anchor_base
"""
    with pytest.raises(OpenDVError) as alias_exc:
        load_glossary(glossary_text=yaml_with_alias)
    assert alias_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

    yaml_with_lone_anchor = """
version: 1
terms:
  lone_term: &lone_anchor
    label: Lone Term
    definition: Lone definition
"""
    with pytest.raises(OpenDVError) as anchor_exc:
        load_glossary(glossary_text=yaml_with_lone_anchor)
    assert anchor_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_glossary_redos_regex_guard() -> None:
    """Pure-Python ReDoS guard rejects nested/sequential quantifiers, (?x), (?#...), lookarounds, backrefs."""
    for valid in (
        r"^CUST-[0-9]{4,10}$",
        r"^INV-[0-9]{4}-[0-9]{4,8}$",
        r"^(?:USD|EUR)?$",
        r"^(?:foo|bar)$",
        r"^(?:[A-Z]{2})?$",
        r"^(?:foo)+$",
        r"[+?*]",
        r"^[+?*]=$",
        r"^[\(\[]?[A-Z]+[\)\]]?$",
        r"^(?P<idx>[A-Z]{2})$",
        r"(?i)^foo+$",
        r"^(?i:foo)+$",
        r"^[0-9]{1,4}-[0-9]{1,2}-[0-9]{1,2}$",
        r"^(?:USD|EUR|GBP|CAD|JPY)$",
        r"^USD|EUR|GBP|CAD|JPY$",
        r"^[A-Z]{1,100}$",
        r"^(?:USD|EUR)-([0-9]{1,4})-([0-9]{1,2})-([0-9]{1,2})$",
        r"^(?:a|aa)(?:b|bb)(?:c|cc)$",
        r"^(?:(?:A|B)|(?:C|D))$",
        r"^a{1000}a{1000}a{1000}a{1000}b$",
    ):
        compiled = validate_safe_regex(valid)
        assert compiled is not None
    assert validate_safe_regex(r"^CUST-[0-9]{4,10}$").fullmatch("CUST-12345") is not None

    for evil in (
        r"^(a+)+$",
        r"^(a*)*$",
        r"^(a|a)+$",
        r"^(?:[a-z]+)*$",
        r"^(?=[a-z]+)+a$",
        r"^(a)\1+$",
        r"((?:a|aa))+$",
        r"(a?){25}a{25}$",
        r"(a{0,1}){25}a{25}$",
        r"((?:a)?){25}a{25}$",
        r"(a{,25})+$",
        r"(a+){,25}b$",
        r"(a|aa){,25}$",
        r"(a{,1}){25}a{25}$",
        r"(?x)(a|aa) +$",
        r"(?x)(a ?) {25} a{25}$",
        "(?x)#[\n(a|aa)+$\n#]",
        r"(?x:a|aa)+$",
        r"(?im-x:a|aa)+$",
        r"(?#[)(a|aa)+$(?#])",
        "a*" * 18 + "b$",
        "(?:a*)" * 18 + "b$",
        ".*" * 12 + "b$",
        "a?" * 25 + "a{25}$",
        r"(a{,})+$",
        r"^(a{,})+$",
        r"(a{,})+?$",
        r"(a+){,}b$",
        r"(a{,}){,}b$",
        r"(a|aa){,}b$",
        "a{,}" * 18 + "b$",
        r"(a*a*a*)$",
        r"^.*.*.*b$",
        "(?:a|)" * 30 + "b$",
        "(?:a|aa)" * 35 + "b$",
        "(?i:a|aa)" * 35 + "b$",
        "(?:a|aa|aaa)" * 20 + "b$",
        "(a|aa)" * 30 + "b$",
        r"(a*a*)$",
        r"(a+a+)$",
        r"^a{0,500}a{0,500}a{0,500}a{0,500}b$",
        r"^a*a{0,500}a{0,500}a{0,500}b$",
        r"^a{0,64}a{0,64}a{0,64}a{0,64}b$",
        r"^a{0,200}a{0,200}a{0,200}a{0,200}b$",
        r"a{,9999}a{,9999}a{,9999}a{,9999}b$",
        r"^a*a{0,16}a{0,16}a{0,16}b$",
        r"^a{0,2000}a{0,16}a{0,16}a{0,16}b$",
        r"^a*(?:a|aa)(?:a|aa)(?:a|aa)a{0,16}a{0,16}a{0,16}b$",
        (
            r"^a*(?:a|aa|aaa|aaaa|aaaaa|aaaaaa)"
            r"(?:a|aa|aaa|aaaa|aaaaa|aaaaaa)"
            r"(?:a|aa|aaa|aaaa|aaaaa|aaaaaa)a{0,16}a{0,16}a{0,16}b$"
        ),
        (
            r"^(?:a|aa|aaa|aaaa|aaaaa|aaaaaa|aaaaaaa|aaaaaaaa|aaaaaaaaa|aaaaaaaaaa)"
            r"(?:a|aa|aaa|aaaa|aaaaa|aaaaaa|aaaaaaa|aaaaaaaa|aaaaaaaaa|aaaaaaaaaa)"
            r"(?:a|aa|aaa|aaaa|aaaaa|aaaaaa|aaaaaaa|aaaaaaaa|aaaaaaaaa|aaaaaaaaaa)"
            r"a{0,16}a{0,16}a{0,16}a{0,16}b$"
        ),
        f"^a{{0,{'9' * 400}}}$",
        "a" * 513,
        "[unclosed",
        r"^.*a{0,15}[a-z]{990}b$",
        r"^.*a{0,3}a{0,3}[a-z]{990}b$",
        r"^.*(?:a|aa|aaa|aaaa)(?:a|aa|aaa|aaaa)[a-z]{990}b$",
        r"^.*" + "(?=[a-z]{1000})" * 30 + "b$",
        r"^.*a{0,3}a{0,3}" + "(?=[a-z]{1000})" * 32 + "b$",
        r"^a{0,7}a{0,7}a{0,7}" + "(?=[a-z]{1000})" * 32 + "b$",
        r"^.*a{0,3}a{0,3}" + "a" * 490 + "b$",
        r"^(?=a)a$",
        r"^(?!b)a$",
        r"^a(?<=a)$",
        r"^a(?<!b)$",
        r"^a(?#comment)b$",
        r"^a{0,3}" + "(" * 100 + "a" + ")" * 100 + "*b$",
        r"^" + "(" * 126 + "a" + ")" * 126 + "*b$",
        r"^(?:" + "(" * 126 + "aa" + ")" * 126 + ")*b$",
    ):
        with pytest.raises(OpenDVError) as exc_info:
            validate_safe_regex(evil)
        assert exc_info.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT


def test_glossary_no_fibo_acronyms_or_checksums_and_surface_scoring() -> None:
    """GlossaryKnowledgeSource disables FIBO acronyms/checksums and scores allowed_values & patterns."""
    src = GlossaryKnowledgeSource()
    assert src.expand_query_terms(["nav", "lei", "sec"]) == ["nav", "lei", "sec"]

    # allowed_values match
    status_hits = src.match_values_or_mentions(["ACTIVE", "TRIALING", "PAST_DUE"])
    assert any(h.entry_id == "urn:opendv:glossary:subscription_status" for h in status_hits)

    # regex pattern match
    cust_hits = src.match_values_or_mentions(["CUST-10001", "CUST-20002"])
    assert any(h.entry_id == "urn:opendv:glossary:customer_id" for h in cust_hits)

    # expected_type hard veto (numeric column vs date term)
    col = ColumnEvidence(column_name="invoice_issued_date", data_type="BIGINT", sample_values=[10, 20])
    tbl_req = GroundingRequest(
        tables=[TableEvidence(table_name="billing_invoice", columns=[col])],
        policy=_det_policy(),
    )
    resp = ColumnGroundingEngine(src).ground(tbl_req)
    col_res = resp.tables[0].columns[0]
    assert all(c.entry_id != "urn:opendv:glossary:invoice_issued_date" for c in col_res.choices)


def test_flat_glossary_dynamic_anchor_entry_kinds_and_engine_integration() -> None:
    """Flat glossaries without entity/event terms emit empty anchor_entry_kinds; structured glossaries emit anchors."""
    flat_yaml = """
version: 1
terms:
  order_id:
    label: Order ID
    definition: Enterprise order key.
    kind: identifier
  order_status:
    label: Order Status
    definition: Status of customer order.
    kind: code
    allowed_values:
      - OPEN
      - CLOSED
"""
    flat_src = GlossaryKnowledgeSource(glossary_text=flat_yaml)
    assert flat_src.metadata.anchor_entry_kinds == frozenset()

    flat_req = GroundingRequest(
        tables=[
            TableEvidence(
                table_name="orders",
                columns=[
                    ColumnEvidence(
                        column_name="order_id",
                        data_type="VARCHAR",
                        is_pk=True,
                        sample_values=[f"ORD-{i}" for i in range(15)],
                    ),
                    ColumnEvidence(
                        column_name="order_status",
                        data_type="VARCHAR",
                        sample_values=["OPEN", "CLOSED"] * 10,
                    ),
                ],
            )
        ],
        policy=_det_policy(),
    )
    flat_resp = ColumnGroundingEngine(flat_src).ground(flat_req)
    assert flat_resp.tables[0].inferred_table_anchors == []
    by_col = {c.column_name: c for c in flat_resp.tables[0].columns}
    assert by_col["order_status"].choices[0].entry_id == "urn:opendv:glossary:order_status"

    # Structured example glossary produces non-empty table anchor and zero FIBO URIs
    struct_src = GlossaryKnowledgeSource()
    struct_req = GroundingRequest(
        tables=[
            TableEvidence(
                table_name="billing_invoice",
                columns=[
                    ColumnEvidence(
                        column_name="invoice_id",
                        data_type="VARCHAR",
                        is_pk=True,
                        sample_values=[f"INV-2026-{i:04d}" for i in range(15)],
                    ),
                    ColumnEvidence(
                        column_name="order_total_amount",
                        data_type="DECIMAL(18,2)",
                        sample_values=[100.5 + i for i in range(15)],
                    ),
                    ColumnEvidence(
                        column_name="iso_currency_code",
                        data_type="VARCHAR",
                        sample_values=["USD", "EUR"] * 8,
                    ),
                ],
            )
        ],
        policy=_det_policy(),
    )
    struct_resp = ColumnGroundingEngine(struct_src).ground(struct_req)
    assert struct_resp.tables[0].inferred_table_anchors
    assert struct_resp.tables[0].inferred_table_anchors[0].entry_id == "urn:opendv:glossary:billing_invoice"
    for col_res in struct_resp.tables[0].columns:
        for ch in col_res.choices:
            assert "edmcouncil.org/fibo" not in ch.entry_id
