# Sample Financial & Regulatory PDFs for Ontology Studio & OCR Testing

This directory provides representative real-world financial and banking regulatory PDF documents alongside a hybrid vector/scanned OCR fixture for end-to-end Ontology Construction Studio testing (`http://127.0.0.1:8420/ontology` and `opendv ontology studio`).

## Included PDF Documents

| Filename | Source / Origin | Pages | Size (Bytes) | Extracted Chunks (`pypdf`) | Detected Tables | Layout & OCR Profile | SHA-256 |
|---|---|---|---|---|---|---|---|
| `nyfed_sr506_repo_infrastructure.pdf` | [Federal Reserve Bank of New York — Staff Report No. 506](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr506.pdf) (*Tri-Party Repo Infrastructure*) | 55 | 449,963 | 67 | 0 | Multi-page clean vector text + financial/market-structure terminology (`Repo`, `Dealer`, `ClearingBank`, `Collateral`) | `3ab4ebd85444e286ca94d8f33a3821e7b7c659f86e097a74aa58472812bfbe14` |
| `fdic_section3_2_loans_credit_risk.pdf` | [FDIC Risk Management Manual of Examination Policies — Section 3.2: Loans](https://www.fdic.gov/resources/supervision-and-examinations/examination-policies-manual/section3-2.pdf) | 88 | 1,115,319 | 153 | 0 | Regulatory examination policies, underwriting, commercial loan classifications, and credit risk controls | `1ce3aed369f9cf9bff19f4d32009ada8b3adde7c06572349eb5a13a7bf2bbd2c` |
| `occ_comptrollers_handbook_rating_credit_risk.pdf` | [OCC Comptroller's Handbook — Rating Credit Risk](https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/rating-credit-risk/pub-ch-rating-credit-risk.pdf) | 69 | 1,467,311 | 539 | 0 | Dense banking supervisory guidance, borrower risk ratings, facility collateral, and allowance governance | `a0c2b4653965d2f6b8ab80c5cd0e2fdb016239c93e3582357134d3a4270dd6` |
| `institutional_scanned_schedule_ocr_test.pdf` | Synthetic hybrid custody & tri-party repo schedule fixture (generated via `pypdf`) | 2 | 6,352 | 2 | 1 | **Page 1:** Clean vector definitions + 3-row Markdown pipe collateral table (`is_complex=False`). **Page 2:** Simulated scanned exhibit (`raster_ratio=0.6189 > 0.40`, `char_count=36 < 50`, `unmapped_glyphs=1` via `(cid:101)` -> `is_complex=True`) | `f303022abc2e2ad5130a39b409212c3c967b05846143e6ae15005ce29502fcd1` |

## Testing OCR Modes (`auto`, `force`, `off`)

Each PDF exercises the `LiteParseDocumentAdapter` and `TesseractOCRRouter` (`packages/opendv-sources/src/opendv_sources/ingestion/layout_evaluator.py`) strategy matrix:

- **`auto` (Recommended Default):** Evaluates every page (`char_count < 50`, `raster_ratio > 0.40`, or `unmapped_glyphs > 0`). Clean vector pages extract natively without OCR overhead; complex/scanned pages (such as Page 2 of `institutional_scanned_schedule_ocr_test.pdf`) route to Tesseract OCR when available on `PATH` or fall back gracefully (`ocr_skipped=True`) when Tesseract is absent.
- **`force`:** Enforces mandatory Tesseract OCR availability (`OCRUnavailableError("OCR_UNAVAILABLE")` / HTTP `501` if the `tesseract` binary is not installed). Useful for strict regulatory ingestion pipelines that must reject partial extraction without OCR.
- **`off`:** Unconditionally skips OCR (`ocr_skipped=True` on complex pages) and parses embedded vector text and tables deterministically.

## Quick CLI Verification

```bash
# Create an Ontology Studio session targeting capital markets & credit risk
opendv ontology studio create \
  --session regulatory-pdf-e2e-walkthrough \
  --domain-namespace capital_markets \
  --enterprise-owner "Institutional Risk Architecture"

# Upload all sample regulatory and OCR test PDFs
for pdf in examples/sample_pdfs/*.pdf; do
  opendv ontology studio upload --session regulatory-pdf-e2e-walkthrough --file "$pdf"
done

# Parse using smart auto OCR detection (falls back safely if Tesseract is absent)
opendv ontology studio parse --session regulatory-pdf-e2e-walkthrough --ocr-mode auto --wait
```
