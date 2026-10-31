"""Gate 2 Length-Prefixed SHA-256 Hashing & Zero-Key Test Suite (`tests/test_hashing_and_keys.py`).

Validates:
1. Reference implementation of `compute_dv2_length_prefixed_sha256` (`docs/01` §4.2, §6.1 & `docs/06` §1.4, §2.4).
2. All 12 normative SHA-256 test vectors from `docs/06` Table 2.4 & `docs/07` Scene 6/7:
   - `HUB_FUND` (Fund IV & Fund I Co-Invest)
   - `HUB_LIMITED_PARTNER` (CalPERS, Bavarian Pension Tier-3, Nordic Sovereign SAL Master, Nordisk Suverän Fond SAL Alias)
   - `HUB_PORTFOLIO_COMPANY` (Nordic Cloud SaaS AB)
   - `LNK_LP_COMMITMENT` (CalPERS Fund IV Commitment)
   - `LNK_LP_COMMITMENT_CO_INVEST` (CalPERS Nordic Cloud Co-Investment)
   - `SAT_LP_COMMITMENT_INVESTRAN` (`hash_diff` initial t0 & mutated t2)
   - `HUB_DOCUMENT` Unstructured PDF URIs (CalPERS Side Letter PDF & Nordic Cloud Q3 IC Valuation Memo PDF)
3. Delimiter-collision immunity (`['AB', 'C'] != ['A', 'BC']`, `['A||B', 'C'] != ['A', 'B||C']`),
   whitespace/case normalization (`trim` + `UPPER` for BKs vs case-preserving `trim` for Satellite `hash_diff`),
   null (`-1`) vs empty string (`-2`) sentinel distinction, and Zero Key constants (`0x00..00` for `-1`, `0xff..ff` for `-2`).
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib

import pytest

# ============================================================================
# Normative Constants & Reference Implementation
# ============================================================================

ZERO_KEY_UNKNOWN_TOKEN = "-1"
ZERO_KEY_ERROR_TOKEN = "-2"

ZERO_KEY_UNKNOWN_HEX = "0" * 64
ZERO_KEY_ERROR_HEX = "f" * 64
ZERO_KEY_UNKNOWN_BYTES = b"\x00" * 32
ZERO_KEY_ERROR_BYTES = b"\xff" * 32


def resolve_dv2_zero_key_hex(sentinel_token: str | None) -> str:
    """Resolve system Zero Key sentinel tokens (`-1` or `-2`) to 64-char hex constants.

    - `-1` (or `None`): Unknown / Missing Mandatory FK -> `0x00..00` (`'0' * 64`)
    - `-2`: Not Applicable / Optional FK / Error -> `0xff..ff` (`'f' * 64`)
    """
    if sentinel_token is None or sentinel_token == ZERO_KEY_UNKNOWN_TOKEN:
        return ZERO_KEY_UNKNOWN_HEX
    if sentinel_token == ZERO_KEY_ERROR_TOKEN:
        return ZERO_KEY_ERROR_HEX
    raise ValueError(f"Unsupported Zero Key sentinel token: {sentinel_token!r}")


def format_dv2_length_prefixed_prehash(
    tokens: Sequence[str | None],
    *,
    is_business_key: bool = True,
    delimiter: str = "||",
    distinguish_empty_string: bool = True,
) -> str:
    """Construct the canonical UTF-8 byte-length-prefixed pre-hash string (`<byte_len>:<token>`).

    Canonicalization Pipeline (`docs/01` §4.2 & `docs/06` §1.4):
      1. If a token is `None`, emit the Null sentinel `"-1:NULL"` (or `"2:-1"` when `distinguish_empty_string=False`).
      2. Strip leading/trailing whitespace (`str(token).strip()`).
      3. If the stripped string is empty `""`:
         - When `distinguish_empty_string=True`, emit the Empty-String sentinel `"-2:EMPTY"`
           so `None` (`-1`) and `""` (`-2`) never collide with each other or with literal `"-1"` / `"-2"`.
         - When `distinguish_empty_string=False` (`docs/06` §1.4 SQL macro mode), emit `"2:-1"`.
      4. If `is_business_key=True`, uppercase the normalized string (`s.upper()`);
         if `is_business_key=False` (Satellite `hash_diff`), preserve case.
      5. Prefix the token with its UTF-8 octet length and `:` (`f"{len(s.encode('utf-8'))}:{s}"`).
      6. Join all encoded tokens with `delimiter` (`"||"`).
    """
    encoded_parts: list[str] = []
    for tok in tokens:
        if tok is None:
            encoded_parts.append("-1:NULL" if distinguish_empty_string else "2:-1")
            continue

        cleaned = str(tok).strip()
        if cleaned == "":
            encoded_parts.append("-2:EMPTY" if distinguish_empty_string else "2:-1")
            continue

        if is_business_key:
            cleaned = cleaned.upper()

        utf8_byte_len = len(cleaned.encode("utf-8"))
        encoded_parts.append(f"{utf8_byte_len}:{cleaned}")

    return delimiter.join(encoded_parts)


def compute_dv2_length_prefixed_sha256(
    tokens: Sequence[str | None],
    *,
    is_business_key: bool = True,
    delimiter: str = "||",
    distinguish_empty_string: bool = True,
) -> str:
    """Compute the 64-character uppercase hex SHA-256 digest of a length-prefixed token tuple."""
    prehash = format_dv2_length_prefixed_prehash(
        tokens,
        is_business_key=is_business_key,
        delimiter=delimiter,
        distinguish_empty_string=distinguish_empty_string,
    )
    return hashlib.sha256(prehash.encode("utf-8")).hexdigest().upper()


# ============================================================================
# 1. All 12 Normative SHA-256 Test Vectors (`docs/06` Table 2.4 & `docs/07`)
# ============================================================================

NORMATIVE_PE_SHA256_TEST_VECTORS = [
    pytest.param(
        "V01_HUB_FUND_APEX_BO_IV",
        ["GLOBAL", "APEX_BO_IV"],
        True,
        "6:GLOBAL||10:APEX_BO_IV",
        "94A60FCDA278C5AEC284C595413935793D5CEC113C511F20690046061D388759",
        id="01-HUB_FUND-Apex_Buyout_Fund_IV",
    ),
    pytest.param(
        "V02_HUB_FUND_APEX_CO_I",
        ["GLOBAL", "APEX_CO_I"],
        True,
        "6:GLOBAL||9:APEX_CO_I",
        "E77712A297CFF46051593303A3380820CA747A9A15C98254C518F7AF499E4C7E",
        id="02-HUB_FUND-Apex_Co_Invest_I",
    ),
    pytest.param(
        "V03_HUB_LIMITED_PARTNER_CALPERS",
        ["GLOBAL", "LP-101"],
        True,
        "6:GLOBAL||6:LP-101",
        "5D15EB10C8D7621D3167849C41C5F9CD9244FD79FB720CB17104D49E4B820AE4",
        id="03-HUB_LIMITED_PARTNER-CalPERS_GLOBAL_LP101",
    ),
    pytest.param(
        "V04_HUB_LIMITED_PARTNER_BAVARIAN_TIER3",
        ["LEGACY_GP_ADMIN", "LP-101"],
        True,
        "15:LEGACY_GP_ADMIN||6:LP-101",
        "6B7C7180B62164F31F14EEBCD16CC5FD2B0CE58143DE66A4B67521113EC5D8B9",
        id="04-HUB_LIMITED_PARTNER-Bavarian_Pension_Tier3_LP101",
    ),
    pytest.param(
        "V05_HUB_LIMITED_PARTNER_NORDIC_MASTER",
        ["GLOBAL", "LP-104"],
        True,
        "6:GLOBAL||6:LP-104",
        "970AE134F322F208ADAFE424B8394FC039FB4FD77A3D11A4D2147130561E5106",
        id="05-HUB_LIMITED_PARTNER-Nordic_Sovereign_SAL_Master",
    ),
    pytest.param(
        "V06_HUB_LIMITED_PARTNER_NORDISK_ALIAS",
        ["LEGACY_GP_ADMIN", "LP-102"],
        True,
        "15:LEGACY_GP_ADMIN||6:LP-102",
        "0B27D3AFD414BBAC781FC852588019FA77DAF121231396351646BAF93CBF9033",
        id="06-HUB_LIMITED_PARTNER-Nordisk_Suveran_SAL_Alias",
    ),
    pytest.param(
        "V07_HUB_PORTFOLIO_COMPANY_NORDIC_CLOUD",
        ["GLOBAL", "529900T8BM49AURSDO55"],
        True,
        "6:GLOBAL||20:529900T8BM49AURSDO55",
        "0C1850CAC2E686A1F9233FA223F3986DC0949963DE1A32DFFDB25E1139B0454E",
        id="07-HUB_PORTFOLIO_COMPANY-Nordic_Cloud_SaaS_LEI",
    ),
    pytest.param(
        "V08_LNK_LP_COMMITMENT_CALPERS_BO_IV",
        ["GLOBAL", "APEX_BO_IV", "GLOBAL", "LP-101", "COMM-2026-001"],
        True,
        "6:GLOBAL||10:APEX_BO_IV||6:GLOBAL||6:LP-101||13:COMM-2026-001",
        "EB156109CC1A7C9A609169970B396D095950BD2309527D229017AD0D357A7C4E",
        id="08-LNK_LP_COMMITMENT-CalPERS_Fund_IV_COMM_2026_001",
    ),
    pytest.param(
        "V09_LNK_LP_COMMITMENT_CO_INVEST_CALPERS_NORDIC_CLOUD",
        ["GLOBAL", "APEX_CO_I", "GLOBAL", "LP-101", "GLOBAL", "529900T8BM49AURSDO55", "COMM-2026-005"],
        True,
        "6:GLOBAL||9:APEX_CO_I||6:GLOBAL||6:LP-101||6:GLOBAL||20:529900T8BM49AURSDO55||13:COMM-2026-005",
        "4889CABE373F2931BB620F519EFAD4A6219A3145CB8A340A15AC1B63F7610255",
        id="09-LNK_LP_COMMITMENT_CO_INVEST-3Way_Grain_COMM_2026_005",
    ),
    pytest.param(
        "V10_SAT_LP_COMMITMENT_INVESTRAN_HASH_DIFF_T0",
        ["125000000.00", "500000000.00", "131250000.00", "Y", "1.084200", "1.296500"],
        False,
        "12:125000000.00||12:500000000.00||12:131250000.00||1:Y||8:1.084200||8:1.296500",
        "28952739B5305882FAE9BACA69537ACE9FB4393FED781E15474340710DED3306",
        id="10-SAT_LP_COMMITMENT_INVESTRAN-HashDiff_Initial_t0",
    ),
    pytest.param(
        "V11_HUB_DOCUMENT_CALPERS_SIDE_LETTER_PDF",
        ["GLOBAL", "s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf"],
        True,
        "6:GLOBAL||81:S3://APEX-PE-VAULT-BRONZE/LEGAL/SIDE-LETTERS/CALPERS_SIDE_LETTER_FUND_IV_2026.PDF",
        "864D5083723B9FD7FE69085C2C31B646BD5E3DEAEF61B64C08D545EDE502EE6B",
        id="11-HUB_DOCUMENT-CalPERS_Side_Letter_PDF",
    ),
    pytest.param(
        "V12_HUB_DOCUMENT_NORDIC_CLOUD_IC_MEMO_PDF",
        ["GLOBAL", "s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf"],
        True,
        "6:GLOBAL||75:S3://APEX-PE-VAULT-BRONZE/IC-MEMOS/2026-Q3/IC_Q3_VALUATION_NORDIC_CLOUD.PDF",
        "99D8178E34640A825C99AC0D343A43DA6D3F6EE5C9A413386801492D88F04158",
        id="12-HUB_DOCUMENT-Nordic_Cloud_Q3_IC_Valuation_Memo_PDF",
    ),
]


@pytest.mark.parametrize(
    ("vector_id", "tokens", "is_bk", "expected_prehash", "expected_sha256"),
    NORMATIVE_PE_SHA256_TEST_VECTORS,
)
def test_normative_12_pe_sha256_vectors(
    vector_id: str,
    tokens: list[str],
    is_bk: bool,
    expected_prehash: str,
    expected_sha256: str,
) -> None:
    """Verify exact pre-hash string and 64-char SHA-256 digest for all 12 normative PE vectors."""
    actual_prehash = format_dv2_length_prefixed_prehash(tokens, is_business_key=is_bk)
    actual_sha256 = compute_dv2_length_prefixed_sha256(tokens, is_business_key=is_bk)
    assert actual_prehash == expected_prehash, f"{vector_id} prehash mismatch"
    assert actual_sha256 == expected_sha256, f"{vector_id} sha256 mismatch"
    assert len(actual_sha256) == 64


def test_sat_lp_commitment_investran_hash_diff_mutation_detection() -> None:
    """Verify `SAT_LP_COMMITMENT_INVESTRAN` `hash_diff` changes when `called_usd` mutates at t2."""
    t0_cols = ["125000000.00", "500000000.00", "131250000.00", "Y", "1.084200", "1.296500"]
    t2_cols = ["160000000.00", "500000000.00", "168000000.00", "Y", "1.084200", "1.296500"]
    hd_t0 = compute_dv2_length_prefixed_sha256(t0_cols, is_business_key=False)
    hd_t2 = compute_dv2_length_prefixed_sha256(t2_cols, is_business_key=False)
    assert hd_t0 == "28952739B5305882FAE9BACA69537ACE9FB4393FED781E15474340710DED3306"
    assert hd_t2 == "D2DB735997A40484F51D8161116FD040DEFEAD1EB453BCDF79D0E7880DCE0774"
    assert hd_t0 != hd_t2


def test_tier3_bkcc_prevents_calpers_bavarian_collision() -> None:
    """Verify `GLOBAL:LP-101` (CalPERS) and `LEGACY_GP_ADMIN:LP-101` (Bavarian) never collide."""
    hk_calpers = compute_dv2_length_prefixed_sha256(["GLOBAL", "LP-101"], is_business_key=True)
    hk_bavarian = compute_dv2_length_prefixed_sha256(["LEGACY_GP_ADMIN", "LP-101"], is_business_key=True)
    assert hk_calpers != hk_bavarian
    assert hk_calpers.startswith("5D15EB10") and hk_calpers.endswith("4B820AE4")
    assert hk_bavarian.startswith("6B7C7180") and hk_bavarian.endswith("3EC5D8B9")


# ============================================================================
# 2. Delimiter-Collision Immunity Tests
# ============================================================================


@pytest.mark.parametrize(
    ("tuple_a", "tuple_b", "expected_prehash_a", "expected_prehash_b"),
    [
        (["AB", "C"], ["A", "BC"], "2:AB||1:C", "1:A||2:BC"),
        (["A||B", "C"], ["A", "B||C"], "4:A||B||1:C", "1:A||4:B||C"),
        (["10:APEX", "BO_IV"], ["10", "APEX||BO_IV"], "7:10:APEX||5:BO_IV", "2:10||11:APEX||BO_IV"),
        (["A:1", "B"], ["A", "1:B"], "3:A:1||1:B", "1:A||3:1:B"),
    ],
)
def test_delimiter_and_boundary_collision_immunity(
    tuple_a: list[str],
    tuple_b: list[str],
    expected_prehash_a: str,
    expected_prehash_b: str,
) -> None:
    """Prove length-prefixed hashing is injective across shifted boundaries and embedded `||` delimiters."""
    pre_a = format_dv2_length_prefixed_prehash(tuple_a, is_business_key=True)
    pre_b = format_dv2_length_prefixed_prehash(tuple_b, is_business_key=True)
    assert pre_a == expected_prehash_a
    assert pre_b == expected_prehash_b
    assert pre_a != pre_b

    hash_a = compute_dv2_length_prefixed_sha256(tuple_a, is_business_key=True)
    hash_b = compute_dv2_length_prefixed_sha256(tuple_b, is_business_key=True)
    assert hash_a != hash_b


def test_doc06_wb11_adversarial_delimiter_vectors() -> None:
    """Verify exact digests from `docs/06` §3.8 `WB-11` (`('A||B', 'C')` vs `('A', 'B||C')`)."""
    h1 = compute_dv2_length_prefixed_sha256(["A||B", "C"], is_business_key=True)
    h2 = compute_dv2_length_prefixed_sha256(["A", "B||C"], is_business_key=True)
    assert h1.startswith("D9FD") and h1.endswith("549C")
    assert h2.startswith("51F8") and h2.endswith("DFB6")
    assert h1 != h2


# ============================================================================
# 3. Whitespace & Asymmetric Case Normalization (`trim` + `UPPER` vs Payload)
# ============================================================================


def test_business_key_trim_and_uppercase_normalization() -> None:
    """Business keys (`is_business_key=True`) must strip outer whitespace and uppercase ASCII/UTF-8."""
    canonical = compute_dv2_length_prefixed_sha256(["GLOBAL", "LP-101"], is_business_key=True)
    noisy_variants = [
        [" global ", " lp-101 "],
        ["\tGLOBAL\n", "  lp-101\r\n"],
        ["Global", "Lp-101"],
    ]
    for variant in noisy_variants:
        assert compute_dv2_length_prefixed_sha256(variant, is_business_key=True) == canonical


def test_satellite_payload_preserves_case_but_trims_whitespace() -> None:
    """Satellite `hash_diff` (`is_business_key=False`) must preserve case while trimming outer whitespace."""
    h_mixed = compute_dv2_length_prefixed_sha256(["Nordic Cloud SaaS AB"], is_business_key=False)
    h_padded = compute_dv2_length_prefixed_sha256(["  Nordic Cloud SaaS AB \t"], is_business_key=False)
    h_lower = compute_dv2_length_prefixed_sha256(["nordic cloud saas ab"], is_business_key=False)
    assert h_mixed == h_padded
    assert h_mixed != h_lower


def test_multibyte_utf8_byte_length_prefixing() -> None:
    """Verify UTF-8 byte length (not character count) is used for non-ASCII characters (`ä` = 2 bytes)."""
    prehash = format_dv2_length_prefixed_prehash(["Nordisk Suverän Fond"], is_business_key=False)
    # 20 Unicode codepoints, 21 UTF-8 bytes
    assert prehash == "21:Nordisk Suverän Fond"


# ============================================================================
# 4. Null (`-1`) vs Empty String (`-2`) Sentinel Distinction & Zero Key Constants
# ============================================================================


def test_null_vs_empty_string_vs_literal_sentinel_distinction() -> None:
    """Prove `None` (`-1`), empty string `""` (`-2`), and literal `"-1"`/`"-2"` produce distinct digests."""
    tuple_null = [None, "APEX_BO_IV"]
    tuple_empty = ["", "APEX_BO_IV"]
    tuple_whitespace = ["   \t  ", "APEX_BO_IV"]
    tuple_lit_minus1 = ["-1", "APEX_BO_IV"]
    tuple_lit_minus2 = ["-2", "APEX_BO_IV"]

    h_null = compute_dv2_length_prefixed_sha256(tuple_null)
    h_empty = compute_dv2_length_prefixed_sha256(tuple_empty)
    h_ws = compute_dv2_length_prefixed_sha256(tuple_whitespace)
    h_lit1 = compute_dv2_length_prefixed_sha256(tuple_lit_minus1)
    h_lit2 = compute_dv2_length_prefixed_sha256(tuple_lit_minus2)

    # Whitespace-only normalizes to empty string after trim
    assert h_empty == h_ws
    # All four semantic states (NULL, empty string, literal '-1', literal '-2') are mutually distinct
    assert len({h_null, h_empty, h_lit1, h_lit2}) == 4


def test_zero_key_constants_for_unknown_and_error_sentinels() -> None:
    """Verify Zero Key constants (`0x00..00` for `-1` unknown, `0xff..ff` for `-2` error/optional)."""
    zk_unknown = resolve_dv2_zero_key_hex("-1")
    zk_none = resolve_dv2_zero_key_hex(None)
    zk_error = resolve_dv2_zero_key_hex("-2")

    assert zk_unknown == "0" * 64
    assert zk_none == "0" * 64
    assert zk_error == "f" * 64
    assert bytes.fromhex(zk_unknown) == ZERO_KEY_UNKNOWN_BYTES
    assert bytes.fromhex(zk_error) == ZERO_KEY_ERROR_BYTES
    assert zk_unknown != zk_error

    with pytest.raises(ValueError, match="Unsupported Zero Key sentinel token"):
        resolve_dv2_zero_key_hex("-3")
