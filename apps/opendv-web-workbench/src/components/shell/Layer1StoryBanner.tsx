/**
 * Layer 1 Executive Story Banner (`Layer1StoryBanner.tsx`) (`docs/06` §2.2 & `docs/10` §3.2).
 *
 * Enforces the 5-Second Scan Standard:
 * - Sticky 56px top bar with `aria-live="polite"`
 * - Plain-English `headline` (<= 140 chars) and `business_impact` (<= 200 chars)
 * - Zero raw 64-character hex SHA-256 digests permitted in Layer 1
 * - WCAG AA Dual-Encoded `verdict_pill` (`PASS`, `AUTO-FIX`, `AUTO_REPAIRED`, `REJECT-WITH-TRACE`, `ASK-USER`)
 * - Mode selector (`Live SSE` vs `Offline Replay`), Playback transport, `[Inspect Proof & Audit Drawer]`, and `[Theme Toggle]`
 */

import type {
	VaultObservabilityEventDTO,
	VerdictStatus,
} from "@opendv/protocol-ts";
import {
	type WorkbenchExecutionMode,
	useWorkbenchStore,
} from "../../store/workbench-store";
import { ThemeToggle } from "../../theme/ThemeToggle";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { PlaybackControls } from "./PlaybackControls";

export const LAYER1_MAX_HEADLINE_CHARS = 140;
export const LAYER1_MAX_BUSINESS_IMPACT_CHARS = 200;

const RAW_SHA256_REGEX = /\b([0-9a-fA-F]{64})\b/g;

/**
 * Sanitize Layer 1 text so that no raw 64-character hexadecimal SHA-256 string is ever
 * rendered in the Executive Story Banner (`docs/06` §1.3 & §2.1).
 */
export function sanitizeLayer1Text(text: string, maxChars: number): string {
	const scrubbed = text.replace(RAW_SHA256_REGEX, (match) => {
		const upper = match.toUpperCase();
		return `[HK: ${upper.slice(0, 4)}..${upper.slice(-4)}]`;
	});
	if (scrubbed.length <= maxChars) {
		return scrubbed;
	}
	return `${scrubbed.slice(0, Math.max(0, maxChars - 1))}…`;
}

export interface Layer1StoryBannerProps {
	currentEvent?: VaultObservabilityEventDTO | null | undefined;
	mode?: WorkbenchExecutionMode | undefined;
	isProofDrawerOpen?: boolean | undefined;
	onModeChange?: ((mode: WorkbenchExecutionMode) => void) | undefined;
	onToggleProofDrawer?: (() => void) | undefined;
}

export function Layer1StoryBanner(props: Layer1StoryBannerProps) {
	const storeCurrentEvent = useWorkbenchStore((s) => s.currentEvent);
	const storeMode = useWorkbenchStore((s) => s.mode);
	const storeIsProofOpen = useWorkbenchStore((s) => s.isProofDrawerOpen);
	const storeSetMode = useWorkbenchStore((s) => s.setMode);
	const storeToggleProof = useWorkbenchStore((s) => s.toggleProofDrawer);

	const event =
		props.currentEvent !== undefined ? props.currentEvent : storeCurrentEvent;
	const mode = props.mode ?? storeMode;
	const isProofDrawerOpen = props.isProofDrawerOpen ?? storeIsProofOpen;
	const handleModeChange = props.onModeChange ?? storeSetMode;
	const handleToggleProof = props.onToggleProofDrawer ?? storeToggleProof;

	const rawHeadline =
		event?.layer1_story_banner.headline ??
		"Open-DV Explainability & Observability Workbench Ready — Select a view or start replay.";
	const rawImpact =
		event?.layer1_story_banner.business_impact ??
		"3-Layer Progressive Disclosure enforces auditable Data Vault 2.0 invariants across 99 checks.";
	const verdict: VerdictStatus =
		event?.layer1_story_banner.verdict_pill ?? event?.status ?? "PASS";

	const headline = sanitizeLayer1Text(rawHeadline, LAYER1_MAX_HEADLINE_CHARS);
	const businessImpact = sanitizeLayer1Text(
		rawImpact,
		LAYER1_MAX_BUSINESS_IMPACT_CHARS,
	);
	const verdictEncoding =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;

	return (
		<header
			aria-live="polite"
			aria-atomic="true"
			data-layer="layer-1-story-banner"
			data-testid="layer1-story-banner"
			style={{
				position: "sticky",
				top: 0,
				zIndex: 30,
				minHeight: "56px",
				backgroundColor: "var(--dv-surface-1)",
				borderBottom: "1px solid var(--dv-border-strong)",
				padding: "0.5rem 1rem",
				display: "flex",
				flexWrap: "wrap",
				alignItems: "center",
				justifyContent: "space-between",
				gap: "0.75rem",
			}}
		>
			{/* Verdict Pill + Executive Headline & Business Impact */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					gap: "0.75rem",
					flex: "1 1 360px",
					minWidth: 0,
				}}
			>
				<span
					data-testid="layer1-verdict-pill"
					data-verdict={verdict}
					style={{
						display: "inline-flex",
						alignItems: "center",
						gap: "0.25rem",
						padding: "0.25rem 0.625rem",
						borderRadius: "9999px",
						border: `2px solid var(${verdictEncoding.cssColorVar})`,
						backgroundColor: "var(--dv-surface-2)",
						color: `var(${verdictEncoding.cssColorVar})`,
						fontWeight: 700,
						fontSize: "0.75rem",
						whiteSpace: "nowrap",
					}}
				>
					<span aria-hidden="true">{verdictEncoding.shapeSymbol}</span>
					<span>{verdictEncoding.prefixBadge}</span>
				</span>

				<div style={{ minWidth: 0 }}>
					<div
						data-testid="layer1-headline"
						style={{
							fontWeight: 700,
							fontSize: "0.875rem",
							color: "var(--dv-text-primary)",
							lineHeight: 1.25,
						}}
					>
						{headline}
					</div>
					<div
						data-testid="layer1-business-impact"
						style={{
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
							lineHeight: 1.25,
						}}
					>
						{businessImpact}
					</div>
				</div>
			</div>

			{/* Mode Selector, Playback Transport, Proof Drawer Button & Theme Toggle */}
			<div
				style={{
					display: "flex",
					flexWrap: "wrap",
					alignItems: "center",
					gap: "0.5rem",
				}}
			>
				<label
					htmlFor="dv-execution-mode-select"
					style={{
						display: "inline-flex",
						alignItems: "center",
						gap: "0.25rem",
						fontSize: "0.75rem",
						color: "var(--dv-text-secondary)",
						fontWeight: 600,
					}}
				>
					<span>Mode</span>
					<select
						id="dv-execution-mode-select"
						aria-label="Select execution stream mode"
						data-testid="execution-mode-select"
						value={mode}
						onChange={(ev) =>
							handleModeChange(ev.target.value as WorkbenchExecutionMode)
						}
						style={{
							backgroundColor: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.375rem",
							padding: "0.2rem 0.4rem",
							fontSize: "0.75rem",
						}}
					>
						<option value="offline-replay">Offline Replay (.jsonl)</option>
						<option value="live-sse">Live SSE (/api/v1)</option>
					</select>
				</label>

				<PlaybackControls />

				<button
					type="button"
					aria-expanded={isProofDrawerOpen}
					aria-controls="dv-layer3-proof-drawer"
					data-testid="inspect-proof-drawer-btn"
					onClick={handleToggleProof}
					style={{
						backgroundColor: isProofDrawerOpen
							? "var(--dv-hub-accent)"
							: "var(--dv-surface-2)",
						color: isProofDrawerOpen
							? "var(--dv-text-inverse)"
							: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.375rem",
						padding: "0.25rem 0.625rem",
						fontSize: "0.75rem",
						fontWeight: 600,
						cursor: "pointer",
					}}
				>
					Inspect Proof (P)
				</button>

				<ThemeToggle compact />
			</div>
		</header>
	);
}
