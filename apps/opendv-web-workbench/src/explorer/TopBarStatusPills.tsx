/**
 * TopBar Status Pills (`TopBarStatusPills.tsx`)
 *
 * Live Agent Readiness pill, Live Cost Meter pill (`FR-AGT-10`, `D21`),
 * `Logs & Traces (<count>)` trigger, and dark `#181715` session digest pill.
 */

import type { PlatformConfigResponse } from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "./design-tokens";
import { isActiveProviderReady } from "./settings/usePlatformConfigActions";

export interface TopBarStatusPillsProps {
	platformConfig?: PlatformConfigResponse | null | undefined;
	agentReady?: boolean | undefined;
	sessionCostTokens?: number | undefined;
	sessionCostUsd?: number | undefined;
	sessionLogsCount?: number | undefined;
	sessionDigest?: string | null | undefined;
	onGoSettings?: (() => void) | undefined;
	onOpenLogs?: (() => void) | undefined;
}

const PILL_BASE_STYLE: CSSProperties = {
	display: "inline-flex",
	alignItems: "center",
	gap: "6px",
	padding: "4px 10px",
	borderRadius: "9999px",
	fontSize: "11.5px",
	fontWeight: 500,
	fontFamily: EXPLORER_FONTS.mono,
	whiteSpace: "nowrap",
};

function formatUsd(usd: number): string {
	const safe = Number.isFinite(usd) && usd > 0 ? usd : 0;
	return `$${safe.toFixed(4)}`;
}

export function TopBarStatusPills({
	platformConfig,
	agentReady,
	sessionCostTokens = 0,
	sessionCostUsd = 0,
	sessionLogsCount = 0,
	sessionDigest,
	onGoSettings,
	onOpenLogs,
}: TopBarStatusPillsProps) {
	const computed = platformConfig
		? isActiveProviderReady(platformConfig)
		: {
				ready: false,
				activeProvider: undefined,
				activeModel: "",
				reason: "Agent Setup Required",
			};
	const isReady = agentReady ?? computed.ready;
	const providerName = platformConfig?.harness.provider ?? "agy";
	const modelLabel = computed.activeModel
		? `${providerName} · ${computed.activeModel}`
		: providerName;

	return (
		<div
			className="claude-topbar-right"
			style={{
				display: "flex",
				alignItems: "center",
				gap: "8px",
				maxWidth: "100%",
				minWidth: 0,
			}}
		>
			{/* Live Agent Readiness Pill */}
			<button
				type="button"
				data-testid="topbar-agent-readiness-pill"
				onClick={onGoSettings}
				title={computed.reason}
				style={{
					...PILL_BASE_STYLE,
					border: `1px solid ${
						isReady
							? CLAUDE_TOKENS.colors.hairlineStrong
							: CLAUDE_TOKENS.colors.primary
					}`,
					background: isReady
						? CLAUDE_TOKENS.colors.surfaceCard
						: CLAUDE_TOKENS.colors.surfaceSoft,
					color: CLAUDE_TOKENS.colors.ink,
					cursor: onGoSettings ? "pointer" : "default",
				}}
			>
				<span
					style={{
						width: "7px",
						height: "7px",
						borderRadius: "50%",
						backgroundColor: isReady
							? CLAUDE_TOKENS.colors.success
							: CLAUDE_TOKENS.colors.warning,
						display: "inline-block",
						flexShrink: 0,
					}}
				/>
				<span>
					{isReady ? `Agent Ready: ${modelLabel}` : "Agent Setup Required"}
				</span>
			</button>

			{/* Live Cost Meter Pill (FR-AGT-10, D21) */}
			<span
				data-testid="topbar-cost-meter-pill"
				style={{
					...PILL_BASE_STYLE,
					background: CLAUDE_TOKENS.colors.surfaceSoft,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					color: CLAUDE_TOKENS.colors.bodyStrong,
				}}
			>
				<span>{formatUsd(sessionCostUsd)}</span>
				<span style={{ color: CLAUDE_TOKENS.colors.muted }}>·</span>
				<span>{sessionCostTokens.toLocaleString()} tok</span>
			</span>

			{/* Logs & Traces Trigger */}
			<button
				type="button"
				data-testid="btn-logs-traces"
				onClick={onOpenLogs}
				style={{
					...PILL_BASE_STYLE,
					fontFamily: EXPLORER_FONTS.sans,
					background: CLAUDE_TOKENS.colors.canvas,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
					color: CLAUDE_TOKENS.colors.ink,
					cursor: "pointer",
				}}
			>
				Logs &amp; Traces ({sessionLogsCount})
			</button>

			{/* Dark #181715 Session Digest Pill (pill-dark) */}
			{sessionDigest && (
				<span
					data-testid="topbar-session-digest-pill"
					className="claude-pill-dark"
					title={sessionDigest}
					style={{
						...PILL_BASE_STYLE,
						background: CLAUDE_TOKENS.colors.surfaceDark,
						color: CLAUDE_TOKENS.colors.onDark,
						maxWidth: "220px",
						overflow: "hidden",
						textOverflow: "ellipsis",
					}}
				>
					{sessionDigest}
				</span>
			)}
		</div>
	);
}
