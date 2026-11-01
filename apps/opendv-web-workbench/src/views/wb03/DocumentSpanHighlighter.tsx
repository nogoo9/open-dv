/**
 * Document Span Highlighter (`DocumentSpanHighlighter.tsx`)
 * Visualizes verbatim PDF text excerpts and exact [span_start:span_end] character highlights (VIZ-STEP-04) (`docs/06` §3.5).
 */

import { useState } from "react";

export interface DocumentSpan {
	start: number;
	end: number;
	text: string;
	attributeKey: string;
	tier: "Tier-U1" | "Tier-U2" | "Tier-U3";
	confidence: number;
	targetEntity: string;
	isCaptainHold?: boolean;
}

export interface DocumentInfo {
	id: string;
	tabTitle: string;
	s3Uri: string;
	sha256Hex: string;
	shortHashPill: string;
	text: string;
	spans: DocumentSpan[];
}

export const PE_DOCUMENTS: readonly DocumentInfo[] = [
	{
		id: "calpers-side-letter",
		tabTitle: "calpers_side_letter_fund_iv_2026.pdf",
		s3Uri:
			"s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf",
		sha256Hex:
			"864D5083723B9FD7FE69085C2C31B646BD5E3DEAEF61B64C08D545EDE502EE6B",
		shortHashPill: "[HK: 864D..EE6B | GLOBAL:... CalPERS Side Letter]",
		text: `CONFIDENTIAL SIDE LETTER AGREEMENT
Dated as of January 15, 2026

BETWEEN:
(1) APEX HORIZON CAPITAL PARTNERS IV GP, LLC (the "General Partner"); and
(2) California Public Employees' Retirement System ("CalPERS", Legal Entity Identifier: 5493006MHB84DD0ZWV18), an institutional investor.

WHEREAS the Investor has agreed to commit USD 500,000,000 to Apex Horizon Buyout Fund IV, L.P.
NOW THEREFORE IT IS AGREED:
1. Management Fee Cap: Notwithstanding Section 6.2 of the LPA, the quarterly management fee payable by CalPERS shall not exceed 125 bps per annum on invested capital.
2. Most Favored Nations: The Investor shall be entitled to Most Favored Nations rights with respect to all fee and liquidity terms.
3. Co-Investment Allocation: The General Partner covenants to offer CalPERS priority co-investment rights in follow-on technology buyout rounds.
4. Ambiguous Side Agreement: The parties note discussions regarding prospective participation in Horizon Sovereign Co-Invest Vehicle with potential co-ownership terms.`,
		spans: [
			{
				start: 134,
				end: 180,
				text: "California Public Employees' Retirement System",
				attributeKey: "lp_legal_name",
				tier: "Tier-U2",
				confidence: 0.98,
				targetEntity: "sat_limited_partner_investran.lp_legal_name",
			},
			{
				start: 227,
				end: 247,
				text: "5493006MHB84DD0ZWV18",
				attributeKey: "lei",
				tier: "Tier-U1",
				confidence: 1.0,
				targetEntity: "hub_limited_partner (LP-101 CalPERS)",
			},
			{
				start: 499,
				end: 506,
				text: "125 bps",
				attributeKey: "management_fee_cap_bps",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_lp_side_letter_llm_ext.management_fee_cap_bps",
			},
			{
				start: 615,
				end: 635,
				text: "Most Favored Nations",
				attributeKey: "mfn_rights_flag",
				tier: "Tier-U2",
				confidence: 0.97,
				targetEntity: "sat_lp_side_letter_llm_ext.mfn_rights_flag",
			},
			{
				start: 1112,
				end: 1147,
				text: "Horizon Sovereign Co-Invest Vehicle",
				attributeKey: "vehicle_name",
				tier: "Tier-U3",
				confidence: 0.81,
				targetEntity: "hub_fund (QUARANTINED FROM CREATION)",
				isCaptainHold: true,
			},
		],
	},
	{
		id: "ic-memo-nordic-cloud",
		tabTitle: "ic_q3_valuation_nordic_cloud.pdf",
		s3Uri:
			"s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf",
		sha256Hex:
			"99D8178E34640A825C99AC0D343A43DA6D3F6EE5C9A413386801492D88F04158",
		shortHashPill: "[HK: 99D8..4158 | GLOBAL:... Nordic Cloud Q3 IC Memo]",
		text: `INVESTMENT COMMITTEE QUARTERLY VALUATION MEMO
VALUATION DATE: 2026-09-30 (Q3 Valuation Quarter-End)
MEETING DATE: 2026-11-14

PORTFOLIO COMPANY: Nordic Cloud SaaS AB
LEGAL ENTITY IDENTIFIER: 529900T8BM49AURSDO55
FUND ALLOCATION: Apex Horizon Buyout Fund IV, L.P. (Fund Code: APEX_BO_IV)

EXECUTIVE VALUATION SUMMARY:
The Valuation Committee has reviewed the trading performance and audited financials of Nordic Cloud SaaS AB.
Under ASC 820 Fair Value Measurement guidelines, this investment is categorized as ASC_820_Level = 'Level 3'.
Based on updated ARR multiple comparables and audited trailing twelve month EBITDA of SEK 180M, the committee establishes:
1. Enterprise Valuation Multiple: EV/EBITDA = 14.5x (increased from entry multiple 12.8x).
2. Implied Enterprise Value: USD 245,000,000.00 (restated up from prior $216.28M).
3. Fair Market Value: Fair_Value_USD = 245000000.00.
CONFIDENTIAL & PROPRIETARY — RESTRICTED TO AUDITED INVESTMENT COMMITTEE MEMBERS`,
		spans: [
			{
				start: 226,
				end: 246,
				text: "529900T8BM49AURSDO55",
				attributeKey: "lei",
				tier: "Tier-U1",
				confidence: 1.0,
				targetEntity: "hub_portfolio_company (Nordic Cloud SaaS AB)",
			},
			{
				start: 486,
				end: 511,
				text: "ASC_820_Level = 'Level 3'",
				attributeKey: "asc_820_level",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_portco_valuation_mnpi.asc_820_level",
			},
			{
				start: 673,
				end: 690,
				text: "EV/EBITDA = 14.5x",
				attributeKey: "ev_ebitda_multiple",
				tier: "Tier-U2",
				confidence: 0.96,
				targetEntity: "sat_portco_valuation_mnpi.ev_multiple",
			},
			{
				start: 761,
				end: 779,
				text: "USD 245,000,000.00",
				attributeKey: "enterprise_value_usd",
				tier: "Tier-U2",
				confidence: 0.98,
				targetEntity: "sat_portco_valuation_mnpi.enterprise_value_usd",
			},
			{
				start: 846,
				end: 875,
				text: "Fair_Value_USD = 245000000.00",
				attributeKey: "fair_value_usd",
				tier: "Tier-U2",
				confidence: 0.98,
				targetEntity: "sat_portco_valuation_mnpi.fair_value_usd",
			},
		],
	},
];

export interface DocumentSpanHighlighterProps {
	onOpenCaptainHold?: () => void;
}

export function DocumentSpanHighlighter({
	onOpenCaptainHold,
}: DocumentSpanHighlighterProps) {
	const [activeDocId, setActiveDocId] = useState<string>("calpers-side-letter");
	const [selectedSpan, setSelectedSpan] = useState<DocumentSpan | null>(null);

	const defaultDoc = PE_DOCUMENTS[0] as DocumentInfo;
	const activeDoc =
		PE_DOCUMENTS.find((d) => d.id === activeDocId) ?? defaultDoc;

	return (
		<div
			aria-label="Document Span Highlighter"
			data-testid="document-span-highlighter"
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.5rem",
				padding: "1rem",
				display: "flex",
				flexDirection: "column",
				gap: "0.75rem",
			}}
		>
			{/* Document Tabs */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					borderBottom: "1px solid var(--dv-border-subtle)",
					paddingBottom: "0.5rem",
				}}
			>
				<div style={{ display: "flex", gap: "0.5rem" }}>
					{PE_DOCUMENTS.map((doc) => {
						const isSelected = doc.id === activeDocId;
						return (
							<button
								key={doc.id}
								type="button"
								data-testid={`pdf-tab-${doc.id === "calpers-side-letter" ? "calpers" : "nordic"}`}
								onClick={() => {
									setActiveDocId(doc.id);
									setSelectedSpan(null);
								}}
								style={{
									backgroundColor: isSelected
										? "var(--dv-surface-2)"
										: "transparent",
									color: isSelected
										? "var(--dv-text-primary)"
										: "var(--dv-text-secondary)",
									border: isSelected
										? "1px solid var(--dv-border-strong)"
										: "1px solid transparent",
									borderRadius: "0.25rem",
									padding: "0.3rem 0.6rem",
									fontSize: "0.75rem",
									fontWeight: isSelected ? 700 : 500,
									fontFamily: "ui-monospace, monospace",
									cursor: "pointer",
								}}
							>
								📄 {doc.tabTitle}
							</button>
						);
					})}
				</div>

				<span
					style={{
						fontFamily: "ui-monospace, monospace",
						fontSize: "0.7rem",
						color: "var(--dv-text-secondary)",
					}}
				>
					{activeDoc.shortHashPill}
				</span>
			</div>

			{/* Document Text Viewport with Interactive Highlight Tags */}
			<div
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.375rem",
					padding: "0.75rem",
					fontFamily: "ui-monospace, monospace",
					fontSize: "0.75rem",
					lineHeight: 1.6,
					whiteSpace: "pre-wrap",
					maxHeight: "260px",
					overflowY: "auto",
					color: "var(--dv-text-primary)",
				}}
			>
				{activeDoc.text}
			</div>

			{/* Spans List & Clickable Badges */}
			<div>
				<div
					style={{
						fontSize: "0.75rem",
						fontWeight: 600,
						color: "var(--dv-text-secondary)",
						marginBottom: "0.4rem",
					}}
				>
					Grounded Character Spans [span_start : span_end]:
				</div>
				<div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
					{activeDoc.spans.map((sp) => {
						const isSelected =
							selectedSpan?.start === sp.start && selectedSpan?.end === sp.end;
						const isHold = sp.isCaptainHold;

						let borderColor = "var(--dv-lnk-accent)";
						if (isHold) borderColor = "var(--dv-gate-askuser)";
						else if (sp.tier === "Tier-U1")
							borderColor = "var(--dv-hub-accent)";

						return (
							<button
								key={`${sp.start}-${sp.end}`}
								type="button"
								data-testid={`span-${sp.start}-${sp.end}`}
								onClick={() => {
									setSelectedSpan(sp);
									if (isHold) {
										onOpenCaptainHold?.();
									}
								}}
								style={{
									backgroundColor: isSelected
										? "var(--dv-surface-3)"
										: "var(--dv-surface-1)",
									color: isHold
										? "var(--dv-gate-askuser)"
										: "var(--dv-text-primary)",
									border: `1.5px solid ${borderColor}`,
									borderRadius: "0.25rem",
									padding: "0.25rem 0.5rem",
									fontSize: "0.7rem",
									fontFamily: "ui-monospace, monospace",
									cursor: "pointer",
									fontWeight: isHold || isSelected ? 700 : 500,
								}}
							>
								[{sp.start}:{sp.end}] "{sp.text}"{" "}
								{isHold ? "⚠️ [CAPTAIN HOLD]" : `(${sp.tier})`}
							</button>
						);
					})}
				</div>
			</div>

			{/* Selected Span Details */}
			{selectedSpan && (
				<div
					data-testid="selected-span-details"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: selectedSpan.isCaptainHold
							? "1.5px solid var(--dv-gate-askuser)"
							: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.25rem",
						padding: "0.5rem 0.75rem",
						fontSize: "0.75rem",
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "0.5rem",
					}}
				>
					<div>
						<strong>
							Span [{selectedSpan.start}:{selectedSpan.end}]:{" "}
						</strong>
						<span>"{selectedSpan.text}"</span>
						<div
							style={{
								fontSize: "0.7rem",
								color: "var(--dv-text-secondary)",
								marginTop: "0.2rem",
							}}
						>
							Attribute: <code>{selectedSpan.attributeKey}</code> →{" "}
							<code>{selectedSpan.targetEntity}</code> (Confidence:{" "}
							{(selectedSpan.confidence * 100).toFixed(0)}%)
						</div>
					</div>

					{selectedSpan.isCaptainHold && (
						<button
							type="button"
							onClick={onOpenCaptainHold}
							data-testid="btn-resolve-span-hold"
							style={{
								backgroundColor: "var(--dv-gate-askuser)",
								color: "#000",
								border: "none",
								borderRadius: "0.25rem",
								padding: "0.3rem 0.6rem",
								fontWeight: 700,
								fontSize: "0.7rem",
								cursor: "pointer",
							}}
						>
							Resolve Captain Hold ↗
						</button>
					)}
				</div>
			)}
		</div>
	);
}
