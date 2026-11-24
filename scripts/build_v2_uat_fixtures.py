#!/usr/bin/env python3
"""Deterministic generator for the 6 Phase V2-2 UAT fixtures under data/public_am/."""

from __future__ import annotations

import csv
import io
import sys
import zlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pypdf
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_v2_uat_duckdb import build_ds_cegar_12trap_duckdb  # noqa: E402

PUBLIC_AM_DIR = REPO_ROOT / "data" / "public_am"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _make_text_stream(lines: list[str]) -> DecodedStreamObject:
    ops = ["BT", "/F1 11 Tf", "54 740 Td", "15 TL"]
    for idx, line in enumerate(lines):
        esc = _pdf_escape(line)
        ops.append(f"({esc}) Tj" if idx == 0 else f"T* ({esc}) Tj")
    ops.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    return stream


def _make_font_dict() -> DictionaryObject:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    return DictionaryObject({NameObject("/F1"): font})


def _make_raster_image_xobject(width: int = 16, height: int = 16) -> DecodedStreamObject:
    pixels = bytes(((x * 13 + y * 29) % 256) for y in range(height) for x in range(width))
    img = DecodedStreamObject()
    img.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width),
            NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/FlateDecode"),
        }
    )
    compressed = zlib.compress(pixels)
    img[NameObject("/Length")] = NumberObject(len(compressed))
    img._data = compressed
    return img


def build_custody_voting_pdf(out_path: Path) -> Path:
    """Create 6-page am_custody_and_voting_guide.pdf with table, raster scan, & repeated text."""
    writer = pypdf.PdfWriter()
    page_specs: list[tuple[str, list[str], bool]] = [
        (
            "1. Custody Scope and Global Mandate",
            [
                "Open-DV Asset Management Custody and Proxy Voting Guide",
                "Scope: Institutional global custody safekeeping, SWIFT MT541/543 settlement,",
                "LEI issuer identification, and annual general meeting (AGM) proxy voting.",
                "Every custody account binds a legal entity via ISO 17442 LEI and custodian BIC.",
            ],
            False,
        ),
        (
            "2. Proxy Voting Policy",
            [
                "Section 2: Fiduciary Proxy Voting Policy",
                "All equity holdings held in custodial omnibus or segregated accounts must record",
                "each AGM ballot decision with issuer CUSIP, ballot identifier, and vote choice.",
                "Conflicts of interest require stewardship committee escalation prior to cutoff.",
            ],
            False,
        ),
        (
            "3. Embedded Vote Schedule",
            [
                "Section 3: Embedded Proxy Vote Schedule Table",
                "ballot_id  issuer_cusip  vote_choice",
                "PRX_NVDA_2024_1  67066G104  FOR",
                "PRX_AAPL_2024_2  037833100  AGAINST",
                "PRX_MSFT_2024_3  594918104  ABSTAIN",
            ],
            False,
        ),
        (
            "4. Scanned Custody Architecture Schedule",
            ["Scan p4"],
            True,
        ),
        (
            "5. Proxy Voting Exceptions",
            [
                "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE",
                "Section 5: Proxy Voting Exceptions and Split-Vote Overrides",
                "Split voting across sub-mandates is permitted when beneficial owners instruct",
                "divergent ESG stewardship elections prior to the depository cutoff timestamp.",
                "CONFIDENTIAL STEWARDSHIP EXCEPTION CLAUSE",
            ],
            False,
        ),
        (
            "6. Governance Appendix",
            [
                "Section 6: Governance Appendix and Audit Trail Retention",
                "Custody settlement instructions and AGM voting records are retained in immutable",
                "Data Vault 2.0 Raw Vault and Business Vault structures for seven years.",
            ],
            False,
        ),
    ]

    for title, lines, add_scan_image in page_specs:
        page = writer.add_blank_page(width=612, height=792)
        resources = DictionaryObject({NameObject("/Font"): _make_font_dict()})
        if add_scan_image:
            img_ref = writer._add_object(_make_raster_image_xobject())
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): img_ref})
            content_ops = (
                "q 400 0 0 240 100 400 cm /Im0 Do Q\n"
                "BT /F1 10 Tf 54 740 Td (Scan p4) Tj ET"
            )
            c_stream = DecodedStreamObject()
            c_stream.set_data(content_ops.encode("latin-1"))
        else:
            c_stream = _make_text_stream(lines)
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = ArrayObject([writer._add_object(c_stream)])
        writer.add_outline_item(title, len(writer.pages) - 1)

    writer.add_metadata({"/Title": "AM Custody and Voting Guide", "/Producer": "Open-DV V2"})
    buf = io.BytesIO()
    writer.write(buf)
    out_path.write_bytes(buf.getvalue())
    return out_path


def build_wave2_eu_custody_parquet(out_path: Path) -> Path:
    """Create 240-row wave2_eu_custody_lei_and_drift.parquet."""
    currencies = ("EUR", "GBP", "CHF", "SEK", "USD")
    bics = ("DEUTDEFFXXX", "BNPAFRPPXXX", "UBSWCHZH80A", "NDEASESSXXX")
    rows = 240
    table = pa.table(
        {
            "custody_acct_id": [f"EU-CUST-{1000 + i}" for i in range(rows)],
            "lei_code": [
                f"549300{100000000000 + i:012d}{10 + (i % 89):02d}" for i in range(rows)
            ],
            "isin": [f"DE000{700000 + i:06d}1" for i in range(rows)],
            "swift_bic": [bics[i % len(bics)] for i in range(rows)],
            "settlement_ccy": [currencies[i % len(currencies)] for i in range(rows)],
            "custodian_fee_bps": [round(1.5 + (i % 12) * 0.25, 2) for i in range(rows)],
            "effective_dts": [f"2026-11-{(i % 28) + 1:02d}T00:00:00Z" for i in range(rows)],
        }
    )
    pq.write_table(table, out_path, compression="zstd")
    return out_path


def build_wave2_bitemporal_csv(out_path: Path) -> Path:
    """Create 180-row wave2_bitemporal_nav_restatements.csv (incl. 216.28 -> 245.0)."""
    headers = [
        "fund_code",
        "share_class",
        "as_of_date",
        "applied_dts",
        "nav_per_share",
        "aum_usd",
        "restatement_reason",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    writer.writerow(
        [
            "AM-CORE-EQ",
            "INST_A",
            "2026-10-20",
            "2026-10-20T18:00:00Z",
            "216.28",
            "216280000.00",
            "INITIAL_CLOSE",
        ]
    )
    writer.writerow(
        [
            "AM-CORE-EQ",
            "INST_A",
            "2026-10-20",
            "2026-11-20T09:30:00Z",
            "245.0",
            "245000000.00",
            "DIVIDEND_ACCRUAL_RESTATEMENT",
        ]
    )
    funds = ("AM-CORE-EQ", "AM-RATES-FI", "AM-MULTI-STRAT")
    classes = ("INST_A", "INST_B", "RETAIL_C")
    for i in range(2, 180):
        day = (i % 28) + 1
        writer.writerow(
            [
                funds[i % len(funds)],
                classes[i % len(classes)],
                f"2026-10-{day:02d}",
                f"2026-11-{day:02d}T12:00:00Z",
                f"{100.0 + (i * 0.75):.2f}",
                f"{(100.0 + i) * 1000000:.2f}",
                "REGULAR_VALUATION" if i % 5 else "LATE_PRICING_ADJUSTMENT",
            ]
        )
    out_path.write_text(buf.getvalue(), encoding="utf-8", newline="")
    return out_path


def build_wave2_otc_collateral_csv(out_path: Path) -> Path:
    """Create 120-row wave2_ambiguous_otc_collateral.csv (ABA PII + U=1.0 competing keys)."""
    headers = [
        "trade_id",
        "uti_id",
        "isda_master_id",
        "counterparty_aba_raw",
        "ticker",
        "isin",
        "pledge_amount",
    ]
    tickers = ("AAPL", "MSFT", "NVDA", "JPM", "BLK")
    isins = (
        "US0378331005",
        "US5949181045",
        "US67066G1040",
        "US46625H1005",
        "US09247X1019",
    )
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for i in range(120):
        idx = i + 1
        writer.writerow(
            [
                f"OTC-TRD-{idx:05d}",
                f"UTI-LEI5493-{idx:06d}",
                f"ISDA-2026-{idx:05d}",
                "ABA 021000021",
                tickers[i % len(tickers)],
                isins[i % len(isins)],
                f"{250000 + i * 12500:.2f}",
            ]
        )
    out_path.write_text(buf.getvalue(), encoding="utf-8", newline="")
    return out_path


def build_prime_allocations_csv(out_path: Path) -> Path:
    """Create 150-row prime_allocations_w2.csv."""
    headers = ["block_id", "allocation_seq", "fund_account", "shares_allocated", "avg_price_dec"]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for i in range(150):
        block_num = (i // 3) + 1
        seq = (i % 3) + 1
        writer.writerow(
            [
                f"BLK-2026-{block_num:04d}",
                str(seq),
                f"FUND-ACCT-{100 + (i % 15)}",
                f"{1000 + i * 25}",
                f"{142.125 + (i % 9) * 0.375:.4f}",
            ]
        )
    out_path.write_text(buf.getvalue(), encoding="utf-8", newline="")
    return out_path


def build_all_v2_uat_fixtures(output_dir: Path = PUBLIC_AM_DIR) -> dict[str, Path]:
    """Generate all 6 Phase V2-2 UAT fixtures deterministically."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "pdf": build_custody_voting_pdf(output_dir / "am_custody_and_voting_guide.pdf"),
        "parquet": build_wave2_eu_custody_parquet(
            output_dir / "wave2_eu_custody_lei_and_drift.parquet"
        ),
        "bitemporal_csv": build_wave2_bitemporal_csv(
            output_dir / "wave2_bitemporal_nav_restatements.csv"
        ),
        "otc_csv": build_wave2_otc_collateral_csv(
            output_dir / "wave2_ambiguous_otc_collateral.csv"
        ),
        "duckdb": build_ds_cegar_12trap_duckdb(output_dir / "ds_cegar_12trap.duckdb"),
        "allocations_csv": build_prime_allocations_csv(output_dir / "prime_allocations_w2.csv"),
    }


def main() -> int:
    generated = build_all_v2_uat_fixtures()
    for name, path in generated.items():
        print(f"Generated {name}: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
