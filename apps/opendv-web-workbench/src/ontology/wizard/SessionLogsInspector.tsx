/**
 * Live-Streaming Session Agent & Execution Logs Inspector (`07`)
 * (`FR-LOG-01..07`, `docs/28 §4.4`, `D21`, `D24`).
 */

import { type CSSProperties, useMemo } from "react";
import {
	type SessionCostLogEntry,
	aggregateSessionCostFromLogs,
} from "../../explorer/cost-meter-utils";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import {
	safeUseEffect,
	safeUseRef,
	safeUseState,
} from "../../explorer/modals/safeHooks";
import { LogsDeepInspectorPane, LogsTimelinePane } from "./logs-deep-pane";
import {
	type ConnectSessionLogsStreamOptions,
	type DeepLogTabId,
	STAGE_PILL_OPTIONS,
	type SessionLogEntry,
	buildLogsExportUrl,
	buildLogsSnapshotUrl,
	connectSessionLogsStream,
	normalizeStageFilterToPill,
	resolvePillToCanonicalStages,
} from "./logs-types";

export {
	connectSessionLogsStream,
	resolvePillToCanonicalStages,
	type ConnectSessionLogsStreamOptions,
	type DeepLogTabId,
	type SessionLogEntry,
};

export interface SessionLogsInspectorProps {
	isOpen: boolean;
	sessionId?: string | null | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	initialStageFilter?: string | null | undefined;
	initialStepFilter?: number | null | undefined;
	initialPersonaFilter?: string | null | undefined;
	entries?: readonly SessionLogEntry[] | undefined;
	selectedSeq?: number | null | undefined;
	activeDeepTab?: DeepLogTabId | undefined;
	followLiveTail?: boolean | undefined;
	onToggleFollowLiveTail?: ((next: boolean) => void) | undefined;
	onClose: () => void;
	onOpenRunInExplorer?: ((runId: string) => void) | undefined;
}

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

const SELECT_STYLE: CSSProperties = {
	padding: "4px 8px",
	borderRadius: "6px",
	border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
	background: CLAUDE_TOKENS.colors.canvas,
	fontSize: "11.5px",
	color: CLAUDE_TOKENS.colors.ink,
};

export function mergeEntriesBySeq(
	prev: readonly SessionLogEntry[],
	incoming: SessionLogEntry,
): SessionLogEntry[] {
	const last = prev[prev.length - 1];
	if (last === undefined || incoming.seq > last.seq) {
		return [...prev, incoming];
	}
	const idx = prev.findIndex((e) => e.seq === incoming.seq);
	if (idx >= 0) {
		const next = [...prev];
		next[idx] = incoming;
		return next;
	}
	return [...prev, incoming].sort((a, b) => a.seq - b.seq);
}

export function SessionLogsInspector({
	isOpen,
	sessionId,
	apiUrl,
	fetchImpl,
	initialStageFilter,
	initialStepFilter,
	initialPersonaFilter,
	entries: controlledEntries,
	selectedSeq: controlledSelectedSeq,
	activeDeepTab: controlledDeepTab,
	followLiveTail: controlledLiveTail,
	onToggleFollowLiveTail,
	onClose,
	onOpenRunInExplorer,
}: SessionLogsInspectorProps) {
	const nextInitialStage = normalizeStageFilterToPill(initialStageFilter);
	const nextInitialStep =
		initialStepFilter !== undefined && initialStepFilter !== null
			? String(initialStepFilter)
			: "all";
	const [liveEntries, setLiveEntries] = safeUseState<SessionLogEntry[]>([]);
	const [searchQuery, setSearchQuery] = safeUseState("");
	const [stageFilter, setStageFilter] = safeUseState(nextInitialStage);
	const [personaFilter, setPersonaFilter] = safeUseState(
		initialPersonaFilter ?? "all",
	);
	const [stepFilter, setStepFilter] = safeUseState<string>(nextInitialStep);
	const syncKey = `${isOpen}:${nextInitialStage}:${nextInitialStep}`;
	const [prevSyncKey, setPrevSyncKey] = safeUseState(syncKey);
	if (syncKey !== prevSyncKey) {
		setPrevSyncKey(syncKey);
		if (isOpen) {
			setStageFilter(nextInitialStage);
			setStepFilter(nextInitialStep);
		}
	}
	const [internalSelectedSeq, setInternalSelectedSeq] = safeUseState<
		number | null
	>(controlledSelectedSeq ?? null);
	const [internalDeepTab, setInternalDeepTab] = safeUseState<DeepLogTabId>(
		controlledDeepTab ?? "prompt",
	);
	const [internalLiveTail, setInternalLiveTail] = safeUseState<boolean>(
		controlledLiveTail ?? true,
	);
	const [streamStatus, setStreamStatus] = safeUseState<string>("IDLE");
	const [copiedJson, setCopiedJson] = safeUseState(false);

	const timelineRef = safeUseRef<HTMLDivElement | null>(null);

	safeUseEffect(() => {
		if (!isOpen || !sessionId || controlledEntries !== undefined) return;
		let cancelled = false;
		const fetchFn =
			fetchImpl ??
			(typeof globalThis.fetch === "function"
				? globalThis.fetch.bind(globalThis)
				: undefined);
		let lastSeenSeq: number | undefined;

		const loadSnapshot = async () => {
			if (!fetchFn) return;
			try {
				const resp = await fetchFn(
					buildLogsSnapshotUrl(sessionId, {
						apiUrl,
						stageFilter,
						wizardStep: stepFilter,
					}),
					{ headers: { Accept: "application/json" } },
				);
				if (!resp.ok || cancelled) return;
				const body = (await resp.json()) as { entries?: SessionLogEntry[] };
				if (cancelled) return;
				const list = body.entries ?? [];
				setLiveEntries(list);
				if (list.length > 0) lastSeenSeq = list[list.length - 1]?.seq;
			} catch {
				// Best-effort initial fetch
			}
		};

		let streamHandle: ReturnType<typeof connectSessionLogsStream> | null = null;
		void loadSnapshot().then(() => {
			if (cancelled) return;
			setStreamStatus("LIVE");
			streamHandle = connectSessionLogsStream({
				sessionId,
				apiUrl,
				lastEventId: lastSeenSeq,
				onLogEntry: (entry) => {
					if (!cancelled)
						setLiveEntries((prev) => mergeEntriesBySeq(prev, entry));
				},
				onStreamEnd: (payload) => {
					if (!cancelled) setStreamStatus(payload.status || "COMPLETED");
				},
				onError: () => {
					if (!cancelled) setStreamStatus("RECONNECTING");
				},
			});
		});

		return () => {
			cancelled = true;
			streamHandle?.close();
		};
	}, [
		isOpen,
		sessionId,
		apiUrl,
		fetchImpl,
		controlledEntries,
		stageFilter,
		stepFilter,
	]);

	const allEntries = controlledEntries ?? liveEntries;
	const followLiveTail = controlledLiveTail ?? internalLiveTail;
	const activeDeepTab = controlledDeepTab ?? internalDeepTab;

	safeUseEffect(() => {
		if (!followLiveTail || !timelineRef.current) return;
		timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
	}, [allEntries.length, followLiveTail]);

	const personas = useMemo(() => {
		const set = new Set<string>();
		for (const e of allEntries) if (e.persona) set.add(e.persona);
		return Array.from(set).sort();
	}, [allEntries]);

	const filteredEntries = useMemo(() => {
		const q = searchQuery.trim().toLowerCase();
		const allowedStages = resolvePillToCanonicalStages(stageFilter);
		const stepNum =
			stepFilter !== "all" ? Number.parseInt(stepFilter, 10) : null;
		return allEntries.filter((e) => {
			if (
				allowedStages.length > 0 &&
				!allowedStages.includes((e.stage_id ?? "").toUpperCase())
			) {
				return false;
			}
			if (
				personaFilter !== "all" &&
				(e.persona ?? "").toLowerCase() !== personaFilter.toLowerCase()
			) {
				return false;
			}
			if (
				stepNum !== null &&
				Number.isFinite(stepNum) &&
				e.wizard_step !== stepNum
			) {
				return false;
			}
			if (q) {
				const hay = [
					e.headline,
					e.stage_id,
					e.persona,
					e.event,
					e.masked_prompt,
					e.structured_or_raw_response,
				]
					.join(" ")
					.toLowerCase();
				if (!hay.includes(q)) return false;
			}
			return true;
		});
	}, [allEntries, searchQuery, stageFilter, personaFilter, stepFilter]);

	const effectiveSelectedSeq =
		controlledSelectedSeq ??
		internalSelectedSeq ??
		filteredEntries[filteredEntries.length - 1]?.seq ??
		allEntries[0]?.seq ??
		null;

	const selectedEntry =
		allEntries.find((e) => e.seq === effectiveSelectedSeq) ??
		filteredEntries[0] ??
		allEntries[0] ??
		null;

	const { totalTokens, totalUsd } = useMemo(
		() =>
			aggregateSessionCostFromLogs(
				allEntries as readonly SessionCostLogEntry[],
			),
		[allEntries],
	);
	const mockCount = useMemo(
		() => allEntries.filter((e) => e.data?.mock === true).length,
		[allEntries],
	);

	if (!isOpen) return null;

	const exportUrl = sessionId ? buildLogsExportUrl(sessionId, apiUrl) : "#";
	const linkedRunId =
		allEntries.find((e) => e.origin === "pipeline_ledger" && e.job_id)
			?.job_id ?? null;

	const handleToggleTail = () => {
		const next = !followLiveTail;
		setInternalLiveTail(next);
		onToggleFollowLiveTail?.(next);
	};

	const handleCopyJson = async () => {
		if (!selectedEntry || typeof navigator === "undefined") return;
		try {
			await navigator.clipboard?.writeText(
				JSON.stringify(selectedEntry, null, 2),
			);
			setCopiedJson(true);
			setTimeout(() => setCopiedJson(false), 1800);
		} catch {
			// Ignore clipboard failures in headless mode
		}
	};

	return (
		<div
			data-testid="session-logs-inspector-backdrop"
			style={{
				position: "fixed",
				inset: 0,
				background: "rgba(20, 20, 19, 0.42)",
				zIndex: 1200,
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				padding: "14px",
			}}
		>
			<dialog
				open
				aria-modal="true"
				aria-label="Session Agent and Execution Logs Inspector"
				data-testid="session-logs-inspector"
				style={{
					width: "min(1380px, 96vw)",
					height: "min(860px, 92vh)",
					margin: 0,
					padding: 0,
					borderRadius: "12px",
					border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
					background: CLAUDE_TOKENS.colors.canvas,
					color: CLAUDE_TOKENS.colors.ink,
					fontFamily: EXPLORER_FONTS.sans,
					display: "flex",
					flexDirection: "column",
					overflow: "hidden",
					boxShadow: "0 18px 48px rgba(20, 20, 19, 0.24)",
				}}
			>
				<div
					className="claude-subbar"
					style={{
						background: CLAUDE_TOKENS.colors.surfaceSoft,
						borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						padding: "8px 16px",
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "6px",
							flexWrap: "wrap",
						}}
					>
						<h2
							className="claude-h2"
							style={{
								margin: 0,
								fontFamily: EXPLORER_FONTS.serif,
								fontSize: "20px",
								fontWeight: 400,
								marginRight: "6px",
							}}
						>
							Session &amp; Agent Logs
						</h2>
						<button
							type="button"
							onClick={() => {
								setStageFilter("all");
								setStepFilter("all");
							}}
							style={{
								...SELECT_STYLE,
								cursor: "pointer",
								background:
									stageFilter === "all" && stepFilter === "all"
										? CLAUDE_TOKENS.colors.primary
										: CLAUDE_TOKENS.colors.canvas,
								color:
									stageFilter === "all" && stepFilter === "all"
										? CLAUDE_TOKENS.colors.onPrimary
										: CLAUDE_TOKENS.colors.ink,
							}}
						>
							All Session ({allEntries.length})
						</button>
						{STAGE_PILL_OPTIONS.map((opt) => (
							<button
								key={opt.pill}
								type="button"
								onClick={() => {
									setStepFilter("all");
									setStageFilter(opt.pill);
								}}
								style={{
									...SELECT_STYLE,
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									cursor: "pointer",
									background:
										stageFilter === opt.pill
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.canvas,
									color:
										stageFilter === opt.pill
											? CLAUDE_TOKENS.colors.onPrimary
											: CLAUDE_TOKENS.colors.ink,
								}}
							>
								S{opt.pill}
							</button>
						))}
					</div>

					<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
						<button
							type="button"
							data-testid="btn-toggle-live-tail"
							onClick={handleToggleTail}
							style={{
								...SELECT_STYLE,
								cursor: "pointer",
								fontWeight: 500,
								borderColor: followLiveTail
									? CLAUDE_TOKENS.colors.success
									: CLAUDE_TOKENS.colors.hairlineStrong,
							}}
						>
							{followLiveTail ? "● Follow Live Tail" : "⏸ Pause Auto-Scroll"}
						</button>
						<button
							type="button"
							data-testid="btn-close-logs-inspector"
							onClick={onClose}
							style={{ ...SELECT_STYLE, cursor: "pointer", fontWeight: 500 }}
						>
							← Close Logs
						</button>
					</div>
				</div>

				<div
					style={{
						background: CLAUDE_TOKENS.colors.surfaceCard,
						borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						padding: "8px 16px",
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "10px",
						flexWrap: "wrap",
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "8px",
							flexWrap: "wrap",
						}}
					>
						<span data-testid="logs-cost-meter" style={BADGE_STYLE}>
							<strong>${totalUsd.toFixed(4)}</strong>
							<span style={{ color: CLAUDE_TOKENS.colors.muted }}>·</span>
							<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
								{totalTokens.toLocaleString()} tok
							</span>
						</span>
						<span style={BADGE_STYLE}>{mockCount} Mocks</span>
						<span style={BADGE_STYLE}>&lt;MASKED_PII&gt;</span>
						<span style={{ ...BADGE_STYLE, fontFamily: EXPLORER_FONTS.mono }}>
							SSE: {streamStatus}
						</span>
					</div>

					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "6px",
							flexWrap: "wrap",
						}}
					>
						<select
							data-testid="logs-filter-step"
							aria-label="Filter by wizard step"
							value={stepFilter}
							onChange={(ev) => setStepFilter(ev.target.value)}
							style={SELECT_STYLE}
						>
							<option value="all">All Steps</option>
							{[1, 2, 3, 4, 5, 6].map((s) => (
								<option key={s} value={String(s)}>
									Step {s}
								</option>
							))}
						</select>
						<select
							data-testid="logs-filter-stage"
							aria-label="Filter by stage"
							value={stageFilter}
							onChange={(ev) => setStageFilter(ev.target.value)}
							style={SELECT_STYLE}
						>
							<option value="all">All Stages</option>
							{STAGE_PILL_OPTIONS.map((opt) => (
								<option key={opt.pill} value={opt.pill}>
									{opt.label}
								</option>
							))}
						</select>
						<select
							data-testid="logs-filter-persona"
							aria-label="Filter by agent persona"
							value={personaFilter}
							onChange={(ev) => setPersonaFilter(ev.target.value)}
							style={SELECT_STYLE}
						>
							<option value="all">All Personas</option>
							{personas.map((p) => (
								<option key={p} value={p}>
									{p}
								</option>
							))}
						</select>
						<input
							type="search"
							data-testid="logs-filter-search"
							placeholder="Search stage, persona, tool, SQL..."
							value={searchQuery}
							onChange={(ev) => setSearchQuery(ev.target.value)}
							style={{ ...SELECT_STYLE, width: "210px" }}
						/>
					</div>
				</div>

				<div
					className="claude-responsive-grid"
					style={{
						flex: 1,
						minHeight: 0,
						padding: "12px 16px",
						display: "grid",
						gridTemplateColumns: "0.94fr 1.16fr",
						gap: "12px",
						overflow: "hidden",
					}}
				>
					<LogsTimelinePane
						timelineRef={timelineRef}
						filteredEntries={filteredEntries}
						totalEntriesCount={allEntries.length}
						selectedSeq={selectedEntry?.seq ?? null}
						onSelectSeq={(seq) => setInternalSelectedSeq(seq)}
					/>
					<LogsDeepInspectorPane
						selectedEntry={selectedEntry}
						activeDeepTab={activeDeepTab}
						copiedJson={copiedJson}
						onSelectTab={(tab) => setInternalDeepTab(tab)}
						onCopyJson={() => void handleCopyJson()}
					/>
				</div>

				<div
					className="claude-footer"
					style={{
						background: CLAUDE_TOKENS.colors.canvas,
						borderTop: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						padding: "8px 16px",
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
						{linkedRunId && onOpenRunInExplorer && (
							<button
								type="button"
								onClick={() => onOpenRunInExplorer(linkedRunId)}
								style={{ ...SELECT_STYLE, cursor: "pointer" }}
							>
								Open Linked Run ({linkedRunId})
							</button>
						)}
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: CLAUDE_TOKENS.colors.muted,
							}}
						>
							{sessionId
								? `opendv ontology studio logs --session-id ${sessionId} --follow`
								: "No active session selected"}
						</span>
					</div>

					<a
						data-testid="btn-download-logs-jsonl"
						href={exportUrl}
						download={
							sessionId ? `${sessionId}-logs.jsonl` : "session-logs.jsonl"
						}
						style={{
							display: "inline-flex",
							alignItems: "center",
							padding: "7px 14px",
							borderRadius: "8px",
							fontSize: "12px",
							fontWeight: 500,
							textDecoration: "none",
							background: CLAUDE_TOKENS.colors.primary,
							color: CLAUDE_TOKENS.colors.onPrimary,
						}}
					>
						Download Session Logs (.jsonl)
					</a>
				</div>
			</dialog>
		</div>
	);
}
