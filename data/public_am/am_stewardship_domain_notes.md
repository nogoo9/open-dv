# Asset Management Stewardship, Risk & Identifier Governance Rules

## 1. Stewardship Escalation Cases (New Concept: StewardshipEngagementCase)
Whenever an issuer triggers a `HARD_BREACH` on `breach_severity` under the `PARIS_ALIGNED_BENCHMARK` mandate policy (specifically `carbon_intensity_tco2e_per_musd > 350.0`), the Stewardship Desk must open a formal `StewardshipEngagementCase` identified by `engagement_case_id` (format `ENG-YYYY-NNNN`).
Each `StewardshipEngagementCase` links an `InstitutionalFiler` (`filer_cik`) to the target `Issuer` (`cik`) and tracks:
- `escalation_tier` (`DIALOGUE`, `BOARD_LETTER`, `CO_FILING`, `DIVESTMENT_REVIEW`)
- `target_resolution_date` (SLA: within 180 calendar days of breach detection)
- `divestment_trigger_flag` (boolean)
- `waiver_expiry_date` (new governance attribute on `ComplianceBreachAlert` if an exception is granted by the Investment Committee)

## 2. Proxy Voting Rationale & Officer Sign-Off (New Attributes on ProxyBallot)
For any `ProxyBallot` (`proxy_ballot_id`) where `vote_cast` opposes `management_recommendation` on `CLIMATE_TRANSITION` or `AI_GOVERNANCE` proposals, stewardship records must capture:
- `vote_rationale_summary` (mandatory public disclosure text for SFDR Article 8 reporting)
- `stewardship_officer_email` (confidential PII attribute — must be isolated in a dedicated PII satellite)
- `public_disclosure_cutoff_days` (integer SLA, default 10 business days post-AGM)

## 3. Macro Regime & Intraday Margin Haircut Rules (New Concepts: MacroRegimeSignal, IntradayMarginHaircutSchedule)
Daily `yield_curves` observations where `spread_10y_2y < 0` produce a `MacroRegimeSignal` identified by `regime_signal_id` (`curve_inversion_flag`, `stress_regime_label`, `effective_as_of_date`) linked to `YieldCurve`.
During an inverted curve regime, the Risk Committee applies an `IntradayMarginHaircutSchedule` (`haircut_schedule_id`, `liquidity_tier`, `haircut_bps`, `collateral_eligibility_flag`, `effective_cutoff_utc`) to pledged `FinancialInstrument` / `Security` collateral.

## 4. Canonical Business Key Authority Rules (New Identifier Attributes)
- `Issuer` canonical identity prioritizes ISO 17442 `lei_code` (Tier-1 Global Standard, new attribute) ahead of SEC `cik`.
- `Security` canonical identity prioritizes ISO 6166 `isin` (Tier-1 Global Standard, new attribute) ahead of `cusip` and exchange `ticker`.
