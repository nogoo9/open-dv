/**
 * Document Span Highlighter (`DocumentSpanHighlighter.tsx`)
 * Visualizes verbatim PDF text excerpts and exact [span_start:span_end] character highlights (VIZ-STEP-04) (`docs/06` §3.5).
 */

import { type ReactNode, useState } from "react";

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
		text: `CONFIDENTIAL LP SIDE LETTER AGREEMENT
DATE: February 14, 2026
PARTIES: Apex Horizon Buyout Fund IV GP LLC (the 'General Partner') and California Public Employees' Retirement System (the 'Investor', Legal Entity Identifier LEI: 5493006MHB84DD0ZWV18).

1. MANAGEMENT FEE DISCOUNT & MOST FAVORED NATION (MFN):
Notwithstanding Section 6.2 of the Limited Partnership Agreement dated January 15, 2026, the General Partner agrees that the Investor shall be entitled to an annualized management fee rate of 125 bps on invested capital during the commitment period, representing a 50 bps reduction from the standard 175 bps headline rate. The Investor shall also enjoy full Most Favored Nation rights with respect to all economic fee terms granted to any other limited partner in Fund IV.

2. CO-INVESTMENT ALLOCATION RIGHTS:
The General Partner hereby grants the Investor a contractual co-investment right of first refusal with respect to all primary buyout opportunities where equity syndication exceeds USD 50,000,000.

3. SPECIAL VEHICLES:
The Investor acknowledges ongoing discussions regarding participation in the Horizon Sovereign Co-Invest Vehicle, subject to definitive LPAC approval and sovereign tax structuring.`,
		spans: [
			{
				start: 44,
				end: 61,
				text: "February 14, 2026",
				attributeKey: "effective_date",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_lp_side_letter_llm_ext.effective_date",
			},
			{
				start: 71,
				end: 98,
				text: "Apex Horizon Buyout Fund IV",
				attributeKey: "governed_fund",
				tier: "Tier-U2",
				confidence: 0.97,
				targetEntity: "hub_fund (APEX_BO_IV)",
			},
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
				start: 607,
				end: 614,
				text: "175 bps",
				attributeKey: "headline_fee_bps",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_lp_side_letter_llm_ext.headline_fee_bps",
			},
			{
				start: 665,
				end: 691,
				text: "Most Favored Nation rights",
				attributeKey: "mfn_rights_flag",
				tier: "Tier-U2",
				confidence: 0.98,
				targetEntity: "sat_lp_side_letter_llm_ext.mfn_rights_flag",
			},
			{
				start: 878,
				end: 914,
				text: "co-investment right of first refusal",
				attributeKey: "co_invest_rofr",
				tier: "Tier-U2",
				confidence: 0.97,
				targetEntity: "sat_lp_side_letter_llm_ext.co_invest_rofr",
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
		text: `APEX HORIZON CAPITAL PARTNERS — INVESTMENT COMMITTEE VALUATION MEMORANDUM
MEMORANDUM DATE: October 14, 2026
VALUATION EFFECTIVE DATE: September 30, 2026 (Q3 2026 Valuation Cycle)
ASSET: Nordic Cloud SaaS AB (Registration LEI: 529900T8BM49AURSDO55, Deal Code: DEAL-NC-2024)
SPONSOR FUND: Apex Buyout Fund IV LP

1. EXECUTIVE VALUATION SUMMARY:
As of the September 30, 2026 valuation cycle, the Investment Committee has reviewed and approved the quarterly fair-value restatement for Nordic Cloud SaaS AB.
Under ASC 820 fair value accounting standards, this private operating asset is designated as an ASC_820_Level = 'Level 3' valuation based on unobservable discounted cashflow inputs and benchmark enterprise trading multiples.

2. QUANTITATIVE MULTIPLES & REVALUATION:
Based on LTM recurring ARR growth of 38% and LTM Normalized EBITDA of USD 16,896,550, the Committee approved an enterprise valuation multiple of EV/EBITDA = 14.5x (an expansion from the entry multiple of 12.8x).
Accordingly, the total implied Enterprise Value is USD 245,000,000.00, resulting in an equity Fair_Value_USD = 245000000.00 for the combined debt-free operating holding.

3. AUDIT SIGN-OFF & PROVENANCE:
Valuation Committee Lead: Marcus Vance (Partner, Software Practice).
External Auditor Review: PriceWaterhouseCoopers LLP (PwC Valuation Services).`,
		spans: [
			{
				start: 134,
				end: 152,
				text: "September 30, 2026",
				attributeKey: "effective_date",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_lnk_doc_portco_ai_provenance.effective_date",
			},
			{
				start: 186,
				end: 206,
				text: "Nordic Cloud SaaS AB",
				attributeKey: "portco_name",
				tier: "Tier-U2",
				confidence: 0.96,
				targetEntity: "hub_portfolio_company (Nordic Cloud SaaS AB)",
			},
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
				start: 259,
				end: 271,
				text: "DEAL-NC-2024",
				attributeKey: "portco_deal_code",
				tier: "Tier-U1",
				confidence: 1.0,
				targetEntity: "sat_portfolio_company_dealcloud.deal_code",
			},
			{
				start: 599,
				end: 624,
				text: "ASC_820_Level = 'Level 3'",
				attributeKey: "asc_820_level",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_portco_valuation_mnpi.asc_820_level",
			},
			{
				start: 915,
				end: 932,
				text: "EV/EBITDA = 14.5x",
				attributeKey: "ev_ebitda_multiple",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_portco_valuation_mnpi.ev_multiple",
			},
			{
				start: 1033,
				end: 1051,
				text: "USD 245,000,000.00",
				attributeKey: "enterprise_value_usd",
				tier: "Tier-U2",
				confidence: 0.99,
				targetEntity: "sat_portco_valuation_mnpi.enterprise_value_usd",
			},
			{
				start: 1076,
				end: 1105,
				text: "Fair_Value_USD = 245000000.00",
				attributeKey: "fair_value_usd",
				tier: "Tier-U2",
				confidence: 0.99,
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
				data-testid="document-text-viewport"
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
				{(() => {
					const sortedSpans = [...activeDoc.spans].sort(
						(a, b) => a.start - b.start,
					);
					const nodes: ReactNode[] = [];
					let cursor = 0;
					for (const sp of sortedSpans) {
						if (sp.start > cursor) {
							nodes.push(activeDoc.text.slice(cursor, sp.start));
						}
						const isSelected =
							selectedSpan?.start === sp.start && selectedSpan?.end === sp.end;
						const isHold = sp.isCaptainHold;
						let borderColor = "var(--dv-lnk-accent)";
						if (isHold) borderColor = "var(--dv-gate-askuser)";
						else if (sp.tier === "Tier-U1")
							borderColor = "var(--dv-hub-accent)";

						nodes.push(
							<mark
								key={`hl-${sp.start}-${sp.end}`}
								data-testid={`doc-highlight-${sp.start}-${sp.end}`}
								onClick={() => {
									setSelectedSpan(sp);
									if (isHold) {
										onOpenCaptainHold?.();
									}
								}}
								onKeyDown={(e) => {
									if (e.key === "Enter" || e.key === " ") {
										e.preventDefault();
										setSelectedSpan(sp);
										if (isHold) {
											onOpenCaptainHold?.();
										}
									}
								}}
								style={{
									backgroundColor: isSelected
										? "var(--dv-surface-3)"
										: "var(--dv-surface-1)",
									color: isHold
										? "var(--dv-gate-askuser)"
										: "var(--dv-text-primary)",
									borderBottom: `2px solid ${borderColor}`,
									borderRadius: "0.15rem",
									padding: "0 0.15rem",
									cursor: "pointer",
									fontWeight: isHold || isSelected ? 700 : 600,
								}}
							>
								{activeDoc.text.slice(sp.start, sp.end)}
							</mark>,
						);
						cursor = sp.end;
					}
					if (cursor < activeDoc.text.length) {
						nodes.push(activeDoc.text.slice(cursor));
					}
					return nodes;
				})()}
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
