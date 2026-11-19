/**
 * Timeline & 4-Tab Dark Deep Inspector panes for SessionLogsInspector (`07`).
 */

import type { CSSProperties, RefObject } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import type { DeepLogTabId, SessionLogEntry } from "./logs-types";

const DEEP_TABS: ReadonlyArray<{ id: DeepLogTabId; label: string }> = [
	{ id: "prompt", label: "Masked Prompt & Context" },
	{ id: "response", label: "Streaming / Structured LLM Response" },
	{ id: "tools", label: "Tool Calls & @toon_v1 Traces" },
	{ id: "raw", label: "Raw JSON Event" },
];

const BADGE_STYLE: CSSProperties = {
	display: "inline-flex",
	alignItems: "center",
	gap: "5px",
	padding: "2px 9px",
	borderRadius: "9999px",
	fontSize: "10.5px",
	fontWeight: 500,
	border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
	background: CLAUDE_TOKENS.colors.canvas,
	color: CLAUDE_TOKENS.colors.ink,
	whiteSpace: "nowrap",
};

function formatTimeShort(ts: string): string {
	const tIdx = ts.indexOf("T");
	return tIdx < 0 ? ts : ts.slice(tIdx + 1, tIdx + 9);
}

export interface LogsTimelinePaneProps {
	timelineRef: RefObject<HTMLDivElement | null>;
	filteredEntries: readonly SessionLogEntry[];
	totalEntriesCount: number;
	selectedSeq: number | null;
	onSelectSeq: (seq: number) => void;
}

export function LogsTimelinePane({
	timelineRef,
	filteredEntries,
	totalEntriesCount,
	selectedSeq,
	onSelectSeq,
}: LogsTimelinePaneProps) {
	return (
		<div
			style={{
				background: CLAUDE_TOKENS.colors.canvas,
				border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
				borderRadius: "12px",
				padding: "10px 12px",
				display: "flex",
				flexDirection: "column",
				minHeight: 0,
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					paddingBottom: "6px",
					marginBottom: "8px",
				}}
			>
				<div>
					<div
						style={{
							fontSize: "10px",
							textTransform: "uppercase",
							fontWeight: 600,
							letterSpacing: "0.6px",
							color: CLAUDE_TOKENS.colors.primaryActive,
						}}
					>
						Unified Session Journal (sse_journal + run_ledger)
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.serif,
							fontSize: "18px",
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						Agent &amp; Execution Timeline
					</div>
				</div>
				<span
					style={{
						...BADGE_STYLE,
						fontFamily: EXPLORER_FONTS.mono,
						background: CLAUDE_TOKENS.colors.surfaceCard,
					}}
				>
					{filteredEntries.length} / {totalEntriesCount} events
				</span>
			</div>

			<div
				ref={timelineRef}
				style={{
					flex: 1,
					overflowY: "auto",
					display: "flex",
					flexDirection: "column",
					gap: "6px",
				}}
			>
				{filteredEntries.length === 0 ? (
					<div
						style={{
							padding: "18px",
							textAlign: "center",
							color: CLAUDE_TOKENS.colors.muted,
							fontSize: "12px",
						}}
					>
						{totalEntriesCount === 0
							? "No session log events recorded yet."
							: "No events match current stage/persona/search filters."}
					</div>
				) : (
					filteredEntries.map((entry) => {
						const isSel = selectedSeq === entry.seq;
						return (
							<button
								key={entry.seq}
								type="button"
								data-testid={`log-entry-row-${entry.seq}`}
								onClick={() => onSelectSeq(entry.seq)}
								style={{
									textAlign: "left",
									padding: "8px 10px",
									borderRadius: "8px",
									cursor: "pointer",
									background: isSel
										? CLAUDE_TOKENS.colors.surfaceCreamStrong
										: CLAUDE_TOKENS.colors.canvas,
									border: isSel
										? `1.5px solid ${CLAUDE_TOKENS.colors.primary}`
										: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
									gap: "8px",
								}}
							>
								<div style={{ minWidth: 0 }}>
									<div
										style={{
											display: "flex",
											alignItems: "center",
											gap: "6px",
											flexWrap: "wrap",
										}}
									>
										<span
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "10px",
												color: isSel
													? CLAUDE_TOKENS.colors.primaryActive
													: CLAUDE_TOKENS.colors.muted,
											}}
										>
											{formatTimeShort(entry.timestamp)} · #{entry.seq}
										</span>
										<strong
											style={{
												fontSize: "11.5px",
												color: CLAUDE_TOKENS.colors.ink,
											}}
										>
											{entry.persona || entry.stage_id || entry.event}
										</strong>
									</div>
									<div
										style={{
											fontSize: "10.5px",
											color: CLAUDE_TOKENS.colors.body,
											marginTop: "2px",
											overflow: "hidden",
											textOverflow: "ellipsis",
											whiteSpace: "nowrap",
										}}
									>
										{entry.headline || entry.event}
									</div>
								</div>
								<span
									style={{
										...BADGE_STYLE,
										fontSize: "9.5px",
										fontFamily: EXPLORER_FONTS.mono,
										background: isSel
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.surfaceSoft,
										color: isSel
											? CLAUDE_TOKENS.colors.onPrimary
											: CLAUDE_TOKENS.colors.ink,
									}}
								>
									{entry.duration_ms
										? `${(entry.duration_ms / 1000).toFixed(1)}s`
										: entry.status || "LOG"}
								</span>
							</button>
						);
					})
				)}
			</div>
		</div>
	);
}

export interface LogsDeepInspectorPaneProps {
	selectedEntry: SessionLogEntry | null;
	activeDeepTab: DeepLogTabId;
	copiedJson: boolean;
	onSelectTab: (tab: DeepLogTabId) => void;
	onCopyJson: () => void;
}

export function LogsDeepInspectorPane({
	selectedEntry,
	activeDeepTab,
	copiedJson,
	onSelectTab,
	onCopyJson,
}: LogsDeepInspectorPaneProps) {
	const tabContent = !selectedEntry
		? "// Select a timeline entry to inspect prompt, LLM output, tool calls, and raw JSON."
		: activeDeepTab === "prompt"
			? selectedEntry.masked_prompt ||
				"// No outbound prompt payload attached to this event."
			: activeDeepTab === "response"
				? selectedEntry.structured_or_raw_response ||
					"// No LLM response payload attached to this event."
				: activeDeepTab === "tools"
					? JSON.stringify(
							{
								tool_calls: selectedEntry.tool_calls ?? [],
								toon_diagnostics: selectedEntry.toon_diagnostics ?? [],
							},
							null,
							2,
						)
					: JSON.stringify(selectedEntry, null, 2);

	return (
		<div
			className="claude-card-dark"
			style={{
				background: CLAUDE_TOKENS.colors.surfaceDark,
				color: CLAUDE_TOKENS.colors.onDark,
				border: `1px solid ${CLAUDE_TOKENS.colors.surfaceDarkElevated}`,
				borderRadius: "12px",
				padding: "12px 14px",
				display: "flex",
				flexDirection: "column",
				gap: "8px",
				minHeight: 0,
				overflow: "hidden",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "flex-start",
					borderBottom: "1px solid #33312d",
					paddingBottom: "7px",
				}}
			>
				<div>
					<div
						style={{
							fontSize: "10px",
							textTransform: "uppercase",
							letterSpacing: "0.6px",
							color: CLAUDE_TOKENS.colors.accentAmber,
							fontWeight: 600,
						}}
					>
						{selectedEntry
							? `Event #${selectedEntry.seq} · ${selectedEntry.persona || selectedEntry.origin || "studio"}`
							: "No Event Selected"}
					</div>
					<div
						style={{
							fontSize: "13.5px",
							color: CLAUDE_TOKENS.colors.onDark,
							fontWeight: 500,
							marginTop: "2px",
						}}
					>
						{selectedEntry?.headline ||
							"Masked Prompt, LLM Response & @toon_v1 Trace"}
					</div>
				</div>
				{selectedEntry && (
					<span
						style={{
							padding: "2px 8px",
							borderRadius: "9999px",
							background: CLAUDE_TOKENS.colors.surfaceDarkElevated,
							border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "10px",
							color: CLAUDE_TOKENS.colors.onDark,
						}}
					>
						{selectedEntry.job_id || selectedEntry.event} ·{" "}
						{selectedEntry.status || "OK"}
					</span>
				)}
			</div>

			<div
				role="tablist"
				aria-label="Deep Log Inspector Tabs"
				style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}
			>
				{DEEP_TABS.map((t) => {
					const isActive = activeDeepTab === t.id;
					return (
						<button
							key={t.id}
							type="button"
							role="tab"
							aria-selected={isActive}
							data-testid={`deep-tab-${t.id}`}
							onClick={() => onSelectTab(t.id)}
							style={{
								padding: "4px 10px",
								borderRadius: "9999px",
								fontSize: "10.5px",
								fontWeight: 500,
								cursor: "pointer",
								border: isActive
									? "none"
									: `1px solid ${CLAUDE_TOKENS.colors.body}`,
								background: isActive
									? CLAUDE_TOKENS.colors.primary
									: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								color: isActive
									? CLAUDE_TOKENS.colors.onPrimary
									: CLAUDE_TOKENS.colors.onDark,
							}}
						>
							{t.label}
						</button>
					);
				})}
			</div>

			<pre
				data-testid="logs-deep-inspector-content"
				style={{
					flex: 1,
					margin: 0,
					padding: "10px 12px",
					borderRadius: "8px",
					border: "1px solid #33312d",
					background: CLAUDE_TOKENS.colors.surfaceDarkSoft,
					color: CLAUDE_TOKENS.colors.onDark,
					fontFamily: EXPLORER_FONTS.mono,
					fontSize: "11px",
					lineHeight: 1.5,
					overflow: "auto",
					whiteSpace: "pre-wrap",
					wordBreak: "break-word",
				}}
			>
				{tabContent}
			</pre>

			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					fontSize: "10.5px",
					color: CLAUDE_TOKENS.colors.onDarkSoft,
				}}
			>
				<span>
					{selectedEntry?.provider
						? `${selectedEntry.provider} / ${selectedEntry.model_id ?? "default"}`
						: "PII-masked telemetry · 0 unmasked PII tokens"}
				</span>
				<button
					type="button"
					onClick={onCopyJson}
					style={{
						padding: "3px 9px",
						borderRadius: "6px",
						fontSize: "10.5px",
						cursor: "pointer",
						background: CLAUDE_TOKENS.colors.surfaceDarkElevated,
						border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
						color: CLAUDE_TOKENS.colors.onDark,
					}}
				>
					{copiedJson ? "Copied ✓" : "Copy Event JSON"}
				</button>
			</div>
		</div>
	);
}
