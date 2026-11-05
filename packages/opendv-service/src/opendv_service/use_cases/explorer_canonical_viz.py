"""Canonical visualizer fixtures and builders for Explorer.

Conforms to docs/design/open-dv-workbench/HANDOFF.md, Decision D3, and Directive R9.
"""

from __future__ import annotations

from opendv_contracts.dtos.explorer import (
    DocCandidateOption,
    DocExtractionSpec,
    DocVizDTO,
    DvSvgEdgeSpec,
    DvSvgNodeSpec,
    DvVizDTO,
    OntologyConceptSpec,
    OntologySvgEdgeSpec,
    OntologySvgNodeSpec,
    OntologyVizDTO,
)


def build_canonical_ontology_viz() -> OntologyVizDTO:
    """Build the Graph A ontology visualizer specifications."""
    return OntologyVizDTO(
        concepts=[
            OntologyConceptSpec(
                name="CBC_CUSTOMER",
                arch="WHO_PARTY",
                def_="B2B or B2C counterparty.",
                keys="T1 customer_tax_id · T3 stripe_code",
            ),
            OntologyConceptSpec(
                name="CBC_ORDER",
                arch="WHAT_EVENT_ANCHOR",
                def_="Sales order committing pricing.",
                keys="T1 order_id",
            ),
            OntologyConceptSpec(
                name="CBC_INVOICE",
                arch="WHAT_FINANCIAL_INSTRUMENT",
                def_="Settlement invoice against order.",
                keys="T3 invoice_number",
            ),
            OntologyConceptSpec(
                name="CBC_PROMOTION",
                arch="HOW_CONTRACTUAL_RULE",
                def_="Promotional campaign on orders.",
                keys="T1 promo_code",
            ),
            OntologyConceptSpec(
                name="CBC_CONTRACT_DOCUMENT",
                arch="WHAT_UNSTRUCTURED_ARTIFACT",
                def_="Signed MSA governing terms.",
                keys="T1 doc_id",
            ),
        ],
        nodes=[
            OntologySvgNodeSpec(
                id="c_cust", label="CBC_CUSTOMER", sub="WHO_PARTY", x=80, y=100, w=160, h=60
            ),
            OntologySvgNodeSpec(
                id="c_ord", label="CBC_ORDER", sub="WHAT_EVENT", x=320, y=100, w=160, h=60
            ),
            OntologySvgNodeSpec(
                id="c_inv", label="CBC_INVOICE", sub="WHAT_FINANCIAL", x=320, y=240, w=160, h=60
            ),
            OntologySvgNodeSpec(
                id="c_prm", label="CBC_PROMOTION", sub="HOW_RULE", x=540, y=100, w=160, h=60
            ),
            OntologySvgNodeSpec(
                id="c_doc", label="CBC_CONTRACT_DOC", sub="WHAT_DOC", x=80, y=240, w=160, h=60
            ),
        ],
        edges=[
            OntologySvgEdgeSpec(path="M 240 130 L 320 130", label="REL_PLACED_BY", lx=280, ly=120),
            OntologySvgEdgeSpec(
                path="M 400 160 L 400 240", label="REL_BILLED_UNDER", lx=410, ly=200
            ),
            OntologySvgEdgeSpec(path="M 480 130 L 540 130", label="REL_APPLIED", lx=510, ly=120),
            OntologySvgEdgeSpec(path="M 160 160 L 160 240", label="REL_GOVERNS", lx=170, ly=200),
        ],
    )


def build_canonical_dv_viz() -> DvVizDTO:
    """Build the Data Vault 2.0 graph visualizer specifications."""
    return DvVizDTO(
        nodes=[
            DvSvgNodeSpec(
                id="h_cust",
                kind="hub",
                label="hub_customer",
                sub="HK_CUSTOMER",
                x=60,
                y=80,
                w=140,
                h=50,
            ),
            DvSvgNodeSpec(
                id="h_ord",
                kind="hub",
                label="hub_order",
                sub="HK_ORDER",
                x=280,
                y=80,
                w=140,
                h=50,
            ),
            DvSvgNodeSpec(
                id="l_ord_cust",
                kind="link",
                label="lnk_order_customer",
                sub="HK_LNK_ORDER_CUST",
                x=170,
                y=80,
                w=170,
                h=40,
            ),
            DvSvgNodeSpec(
                id="s_cust_core",
                kind="sat",
                label="sat_customer_core",
                sub="HD_CUST_CORE",
                x=60,
                y=180,
                w=140,
                h=45,
            ),
            DvSvgNodeSpec(
                id="s_cust_pii",
                kind="sat_pii",
                label="sat_customer_pii",
                sub="HD_CUST_PII",
                x=60,
                y=250,
                w=140,
                h=45,
            ),
        ],
        edges=[
            DvSvgEdgeSpec(x1=130, y1=130, x2=130, y2=180, dashed=False),
            DvSvgEdgeSpec(x1=130, y1=225, x2=130, y2=250, dashed=True),
            DvSvgEdgeSpec(x1=200, y1=100, x2=280, y2=100, dashed=False),
        ],
    )


def build_canonical_doc_viz(is_hold: bool = False) -> DocVizDTO:
    """Build the unstructured document extraction visualizer model."""
    doc_text = (
        "MASTER SERVICES AGREEMENT - ENTERPRISE ADDENDUM (2026-W3)\n\n"
        "1. COUNTERPARTY IDENTIFICATION\n"
        "This Master Services Agreement Addendum is entered into by and between "
        'Northwind Industrial Supply Corp. (US Federal Tax ID: TAX-US-900101) ("Customer") '
        "and Global Commerce Vault Operations LLC, effective 2026-01-12.\n\n"
        "2. SETTLEMENT & INVOICING GUARANTOR\n"
        "Cross-border European settlement obligations under Invoice INV-EU-8801 shall be "
        "guaranteed by Rheinland Precision Holdings Europe, subject to parent-entity "
        "verification by the Data Governance Steward.\n\n"
        "3. DATA PROTECTION & RESTRICTED PII\n"
        "Customer contact electronic mail addresses and phone numbers constitute "
        "RESTRICTED_PII and must be isolated in a dedicated GDPR satellite."
    )
    extractions = [
        DocExtractionSpec(
            id="ext-u1-northwind",
            quote="Northwind Industrial Supply Corp. (US Federal Tax ID: TAX-US-900101)",
            start=117,
            end=186,
            tier="Tier-U1",
            conf="0.98",
            decision="Exact canonical match to CBC_CUSTOMER (TAX-US-900101)",
            resolved=True,
            resolutionAction="APPROVE",
            selectedCandidateId="TAX-US-900101",
        ),
        DocExtractionSpec(
            id="ext-u3-rheinland",
            quote="Rheinland Precision Holdings Europe",
            start=381,
            end=416,
            tier="Tier-U3",
            conf="0.68",
            decision=(
                "Steward hold pending resolution"
                if is_hold
                else "Resolved by auto_approve_top_candidate"
            ),
            holdId="hold-tier-u3-rheinland-precision",
            eventId="evt-hold-u3-001",
            resolved=not is_hold,
            resolutionAction=None if is_hold else "APPROVE",
            selectedCandidateId=None if is_hold else "TAX-DE-800202",
            candidates=[
                DocCandidateOption(
                    id="TAX-DE-800202",
                    label="Rheinland Precision AG (EORI: DE800202)",
                    score=0.91,
                ),
                DocCandidateOption(
                    id="TAX-NL-104928",
                    label="Rheinland Logistics BV (BTW: NL104928)",
                    score=0.74,
                ),
            ],
        ),
    ]
    return DocVizDTO(
        documentName="enterprise_msa_addendum_2026.txt",
        sourceText=doc_text,
        extractions=extractions,
    )
