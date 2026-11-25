import { useEffect, useId, useRef, useState } from "react";
import { api, askFiboQuery, parseProblemDetails } from "../api/client";
import type {
	CitationSpanResp,
	DocumentCitationRef,
	DocumentMap,
	ExternalMcpReceipt,
	FiboMatchSpec,
	ProblemDetails,
	StagedCandidateSpec,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export interface CitationInspectorDrawerProps {
	open: boolean;
	onClose: () => void;
	projectId: string;
	domainNamespace?: string;
	candidate: StagedCandidateSpec | null;
	documentMaps: DocumentMap[];
	receipts: ExternalMcpReceipt[];
	onOpenAskFibo?: (query: string) => void;
}

export function normalizeAlgA9bText(text: string): string {
	return text
		.replace(/\r\n/g, "\n")
		.replace(/\r/g, "\n")
		.replace(/[ \t]+/g, " ");
}

function findCodePointOccurrence(
	normText: string,
	normQuote: string,
	occurrence: number,
): [number, number] | null {
	if (!normQuote || occurrence < 1) return null;
	let pos = 0;
	for (let i = 1; i <= occurrence; i++) {
		const idx = normText.indexOf(normQuote, pos);
		if (idx === -1) return null;
		if (i === occurrence) {
			const start = Array.from(normText.slice(0, idx)).length;
			const len = Array.from(normQuote).length;
			return [start, start + len];
		}
		pos = idx + normQuote.length;
	}
	return null;
}

export function CitationInspectorDrawer({
	open,
	onClose,
	projectId,
	domainNamespace,
	candidate,
	documentMaps,
	receipts,
	onOpenAskFibo,
}: CitationInspectorDrawerProps) {
	const navigate = useProjectStore((s) => s.navigate);
	const dlgRef = useRef<HTMLDialogElement>(null);
	const titleId = useId();

	const [fiboMatches, setFiboMatches] = useState<FiboMatchSpec[]>([]);
	const [selectedIdx, setSelectedIdx] = useState(0);
	const [spanResp, setSpanResp] = useState<CitationSpanResp | null>(null);
	const [maskedPageText, setMaskedPageText] = useState<string | null>(null);
	const [loadingCite, setLoadingCite] = useState(false);
	const [citeProblem, setCiteProblem] = useState<ProblemDetails | null>(null);

	useEffect(() => {
		const el = dlgRef.current;
		if (!el) return;
		if (open && !el.open) {
			if (typeof el.showModal === "function") el.showModal();
			else el.setAttribute("open", "");
		} else if (!open && el.open) {
			el.returnValue = "";
			if (typeof el.close === "function") el.close();
			else el.removeAttribute("open");
		}
		const onClick = (e: MouseEvent) => {
			if (e.target === el) onClose();
		};
		el.addEventListener("click", onClick);
		return () => el.removeEventListener("click", onClick);
	}, [open, onClose]);

	useEffect(() => {
		if (!open || !candidate) return;
		setSelectedIdx(0);
		setSpanResp(null);
		setMaskedPageText(null);
		setCiteProblem(null);
		const ac = new AbortController();
		void askFiboQuery(
			projectId,
			{
				query: candidate.concept_name,
				domain_namespace: domainNamespace ?? null,
				top_k: 5,
			},
			ac.signal,
		)
			.then((res) => {
				if (!ac.signal.aborted) setFiboMatches(res.matches);
			})
			.catch(() => {
				if (!ac.signal.aborted) setFiboMatches([]);
			});
		return () => ac.abort();
	}, [open, candidate, projectId, domainNamespace]);

	const citations = candidate?.source_citations ?? [];
	const activeCitation: DocumentCitationRef | null =
		citations[selectedIdx] ?? null;

	useEffect(() => {
		if (!open || !activeCitation) {
			setSpanResp(null);
			setMaskedPageText(null);
			setCiteProblem(null);
			return;
		}
		const ac = new AbortController();
		setLoadingCite(true);
		setSpanResp(null);
		setMaskedPageText(null);
		setCiteProblem(null);

		void api
			.resolveCitationSpan(
				projectId,
				{
					doc_id: activeCitation.doc_id,
					page_number: activeCitation.page_number,
					verbatim_quote: activeCitation.verbatim_quote,
					occurrence: activeCitation.occurrence ?? 1,
					content_sha256: activeCitation.content_sha256 ?? null,
				},
				ac.signal,
			)
			.then(async (span) => {
				if (ac.signal.aborted) return;
				setSpanResp(span);
				if (span.source_kind === "canonical_text") {
					try {
						const pages = await api.getDocumentPages(
							projectId,
							activeCitation.doc_id,
							activeCitation.page_number,
							activeCitation.page_number,
							ac.signal,
						);
						if (!ac.signal.aborted) {
							setMaskedPageText(pages[0]?.masked_text ?? null);
						}
					} catch (pgErr) {
						if (!ac.signal.aborted) {
							setCiteProblem(parseProblemDetails(pgErr));
						}
					}
				}
			})
			.catch((err) => {
				if (!ac.signal.aborted) setCiteProblem(parseProblemDetails(err));
			})
			.finally(() => {
				if (!ac.signal.aborted) setLoadingCite(false);
			});

		return () => ac.abort();
	}, [open, projectId, activeCitation]);

	const cachedDoc = activeCitation
		? documentMaps.find((d) => d.doc_id === activeCitation.doc_id)
		: undefined;
	const cachedPageSpec = activeCitation
		? cachedDoc?.pages?.find(
				(p) => p.page_number === activeCitation.page_number,
			)
		: undefined;

	const renderHighlightedSpan = () => {
		if (!activeCitation || !maskedPageText || !spanResp) return null;
		const normText = normalizeAlgA9bText(maskedPageText);
		const normQuote = normalizeAlgA9bText(activeCitation.verbatim_quote);
		const chars = Array.from(normText);

		let start = spanResp.char_start ?? null;
		let end = spanResp.char_end ?? null;
		const serverVerified =
			start !== null &&
			end !== null &&
			chars.slice(start, end).join("") === normQuote;
		let fallbackUsed = false;
		if (!serverVerified) {
			const fb = findCodePointOccurrence(
				normText,
				normQuote,
				activeCitation.occurrence ?? 1,
			);
			if (fb) {
				[start, end] = fb;
				fallbackUsed = true;
			} else {
				start = null;
				end = null;
			}
		}

		if (start === null || end === null) {
			return (
				<div className="stack-sm">
					<div className="banner banner-warn">
						Span offsets could not be verified against masked page text; showing
						unhighlighted canonical page text.
					</div>
					<pre className="mono text-sm" style={{ whiteSpace: "pre-wrap" }}>
						{normText}
					</pre>
				</div>
			);
		}

		return (
			<div className="stack-sm">
				<div className="row">
					<span className="pill pill-ok">
						Verified Quote Span [{start}, {end}]
						{fallbackUsed ? " (occurrence fallback)" : ""}
					</span>
				</div>
				<pre
					className="mono text-sm"
					style={{
						whiteSpace: "pre-wrap",
						background: "var(--surface-soft)",
						padding: 10,
						borderRadius: "var(--radius-md)",
						margin: 0,
					}}
				>
					{chars.slice(0, start).join("")}
					<mark
						style={{
							backgroundColor: "var(--pii-fill)",
							color: "var(--primary-active)",
							padding: "0 2px",
						}}
					>
						{chars.slice(start, end).join("")}
					</mark>
					{chars.slice(end).join("")}
				</pre>
			</div>
		);
	};

	const linkedReceipts = candidate
		? receipts.filter(
				(r) =>
					candidate.mcp_evidence?.some((m) => m.receipt_id === r.receipt_id) ||
					(activeCitation &&
						r.doc_id === activeCitation.doc_id &&
						r.page_number === activeCitation.page_number),
			)
		: [];

	return (
		<dialog
			ref={dlgRef}
			className="drawer-dialog"
			aria-labelledby={titleId}
			{...({ closedby: "any" } as Record<string, string>)}
			onCancel={(e) => {
				e.preventDefault();
				onClose();
			}}
		>
			<div className="drawer-panel">
				<div className="row-between">
					<div>
						<h2 id={titleId} className="dialog-title">
							Citation &amp; Evidence Inspector
							{candidate ? ` · ${candidate.concept_name}` : ""}
						</h2>
						{candidate && (
							<p className="text-sm text-muted mono">
								{candidate.concept_uri} · {candidate.grounding_path} ·{" "}
								{candidate.status}
							</p>
						)}
					</div>
					<button
						type="button"
						className="btn btn-ghost btn-sm"
						onClick={onClose}
					>
						Close
					</button>
				</div>

				{candidate && (
					<>
						<section className="card-subtle stack-sm">
							<div className="row-between">
								<strong>1. FIBO Alignment Summary</strong>
								{onOpenAskFibo && (
									<button
										type="button"
										className="btn btn-sm"
										onClick={() => onOpenAskFibo(candidate.concept_name)}
									>
										Open Ask FIBO
									</button>
								)}
							</div>
							{fiboMatches.length === 0 ? (
								<p className="text-sm text-muted">
									No FIBO matches recorded (BM25 score:{" "}
									{candidate.bm25_score ?? "N/A"}).
								</p>
							) : (
								<div className="stack-sm">
									{fiboMatches.slice(0, 3).map((m) => (
										<div key={m.concept_uri} className="row-between text-sm">
											<span className="mono">{m.label}</span>
											<span className="pill">
												{m.suggested_path} · {m.normalized_score}
											</span>
										</div>
									))}
								</div>
							)}
						</section>

						<section className="card-subtle stack-sm">
							<div className="row-between">
								<strong>2. Server-Indexed Quote Spans</strong>
								<span className="text-sm text-muted">
									{citations.length} citation(s)
								</span>
							</div>
							{citations.length === 0 ? (
								<p className="text-sm text-muted">
									No document citations attached to this candidate.
								</p>
							) : (
								<>
									<div className="row">
										{citations.map((c, idx) => (
											<button
												key={`${c.doc_id}-${c.page_number}-${c.occurrence}`}
												type="button"
												className={`btn btn-sm ${idx === selectedIdx ? "btn-primary" : ""}`}
												onClick={() => setSelectedIdx(idx)}
											>
												{c.doc_id} p.{c.page_number}
											</button>
										))}
									</div>

									{activeCitation && (
										<div className="stack-sm">
											<div className="text-sm mono">
												Quote: &ldquo;{activeCitation.verbatim_quote}&rdquo;
												(occ {activeCitation.occurrence})
											</div>
											{loadingCite && (
												<p className="text-sm text-muted">
													Resolving citation span...
												</p>
											)}

											{citeProblem && (
												<div className="banner banner-warn">
													<strong>{citeProblem.error_code}:</strong>{" "}
													{citeProblem.detail}
													{citeProblem.error_code ===
														"UNREDACTED_SCAN_BLOCKED" && (
														<div style={{ marginTop: 6 }}>
															<button
																type="button"
																className="btn btn-sm"
																onClick={() => {
																	onClose();
																	navigate(
																		`/projects/${encodeURIComponent(projectId)}/step/3?sub=3b`,
																	);
																}}
															>
																Open Step 3b Scan Consent →
															</button>
														</div>
													)}
												</div>
											)}

											{(citeProblem?.error_code === "SOURCE_BINARY_ABSENT" ||
												spanResp?.source_kind === "absent") && (
												<div className="callout-teal stack-sm text-sm">
													<strong>
														Offline Metadata Fallback (source_kind: absent)
													</strong>
													<span>
														Document binary is absent; cached DocumentMap
														metadata preserved without synthetic offsets.
													</span>
													{cachedPageSpec && (
														<span className="mono">
															{cachedDoc?.filename} · p.
															{cachedPageSpec.page_number} ·{" "}
															{cachedPageSpec.char_count} chars · sha256:{" "}
															{cachedPageSpec.canonical_text_sha256}
														</span>
													)}
												</div>
											)}

											{spanResp?.source_kind === "canonical_text" &&
												renderHighlightedSpan()}

											{spanResp?.source_kind === "ocr_receipt" && (
												<div className="card-dark stack-sm">
													<div className="row-between">
														<strong>OCR Receipt Span (Metadata-Only)</strong>
														<span className="pill pill-warn">
															source_kind: ocr_receipt
														</span>
													</div>
													<div className="text-sm mono">
														resolved_sha256: {spanResp.resolved_sha256 ?? "N/A"}{" "}
														· Page OCR boxes:{" "}
														{cachedPageSpec?.ocr_box_count ?? 0}
													</div>
													<div className="text-sm">
														Verified Quote:{" "}
														<span className="mono">
															{activeCitation.verbatim_quote}
														</span>
													</div>
												</div>
											)}
										</div>
									)}
								</>
							)}
						</section>

						<section className="card-dark stack-sm">
							<strong>3. External MCP Receipts &amp; Table Grounding</strong>
							{(candidate.table_bindings ?? []).length > 0 && (
								<div className="stack-sm">
									{(candidate.table_bindings ?? []).map((tb) => (
										<div
											key={`${tb.source_id}:${tb.table_id}.${tb.col_name}`}
											className="card-dark-inner text-sm mono"
										>
											{tb.source_id}:{tb.table_id}.{tb.col_name}
										</div>
									))}
								</div>
							)}
							{(candidate.mcp_evidence ?? []).length === 0 &&
							linkedReceipts.length === 0 ? (
								<p
									className="text-sm"
									style={{ color: "var(--on-dark-soft)", margin: 0 }}
								>
									No external MCP receipts linked to this candidate.
								</p>
							) : (
								<div className="stack-sm">
									{(candidate.mcp_evidence ?? []).map((ev) => (
										<div
											key={ev.receipt_id}
											className="card-dark-inner stack-sm text-sm"
											style={{ gap: 4 }}
										>
											<div className="row-between mono">
												<span>
													{ev.receipt_id} · {ev.server_id}/{ev.tool_name}
												</span>
												<span>sha: {ev.content_sha256.slice(0, 12)}</span>
											</div>
											<div className="mono">{ev.masked_excerpt}</div>
										</div>
									))}
									{linkedReceipts
										.filter(
											(r) =>
												!candidate.mcp_evidence?.some(
													(m) => m.receipt_id === r.receipt_id,
												),
										)
										.map((r) => (
											<div
												key={r.receipt_id}
												className="card-dark-inner text-sm mono"
											>
												{r.receipt_id} · {r.server_id}/{r.tool_name} · sha:{" "}
												{r.content_sha256.slice(0, 12)}
											</div>
										))}
								</div>
							)}
						</section>
					</>
				)}
			</div>
		</dialog>
	);
}
