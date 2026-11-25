import { useCallback, useEffect, useId, useRef, useState } from "react";
import { askFiboQuery, parseProblemDetails } from "../api/client";
import type { FiboMatchSpec, ProblemDetails } from "../api/openapi";

export interface AskFiboDrawerProps {
	open: boolean;
	onClose: () => void;
	projectId: string | null;
	domainNamespace?: string;
	initialQuery?: string;
	canStageToDraft?: boolean;
	onStageConcept?: (match: FiboMatchSpec) => void;
	onSetDescription?: (conceptUri: string, match: FiboMatchSpec) => void;
}

export function AskFiboDrawer({
	open,
	onClose,
	projectId,
	domainNamespace,
	initialQuery = "",
	canStageToDraft = false,
	onStageConcept,
	onSetDescription,
}: AskFiboDrawerProps) {
	const dialogRef = useRef<HTMLDialogElement>(null);
	const titleId = useId();
	const abortRef = useRef<AbortController | null>(null);

	const [query, setQuery] = useState(initialQuery);
	const [targetConceptUri, setTargetConceptUri] = useState("");
	const [matches, setMatches] = useState<FiboMatchSpec[]>([]);
	const [busy, setBusy] = useState(false);
	const [copiedUri, setCopiedUri] = useState<string | null>(null);
	const [problem, setProblem] = useState<ProblemDetails | null>(null);

	const runSearch = useCallback(
		async (qText: string) => {
			const clean = qText.trim();
			if (!clean) return;
			abortRef.current?.abort();
			const ac = new AbortController();
			abortRef.current = ac;
			setBusy(true);
			setProblem(null);
			try {
				const resp = await askFiboQuery(
					projectId,
					{
						query: clean,
						domain_namespace: domainNamespace ?? null,
						top_k: 10,
					},
					ac.signal,
				);
				if (!ac.signal.aborted) setMatches(resp.matches);
			} catch (err) {
				if (!ac.signal.aborted) setProblem(parseProblemDetails(err));
			} finally {
				if (!ac.signal.aborted) setBusy(false);
			}
		},
		[projectId, domainNamespace],
	);

	useEffect(() => {
		if (!open) {
			abortRef.current?.abort();
			return;
		}
		setCopiedUri(null);
		setProblem(null);
		if (initialQuery) {
			setQuery(initialQuery);
			void runSearch(initialQuery);
		}
	}, [open, initialQuery, runSearch]);

	useEffect(() => {
		const el = dialogRef.current;
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

	const handleCopy = async (uri: string) => {
		try {
			await navigator.clipboard?.writeText(uri);
		} catch {
			/* clipboard may be restricted in headless contexts */
		}
		setCopiedUri(uri);
	};

	return (
		<dialog
			ref={dialogRef}
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
							Ask FIBO · BM25 Reference Search
						</h2>
						<p className="text-muted text-sm mono">
							Scope: {projectId ?? "Global"} · Domain:{" "}
							{domainNamespace ?? "all"}
						</p>
					</div>
					<button
						type="button"
						className="btn btn-ghost btn-sm"
						onClick={onClose}
					>
						Close
					</button>
				</div>

				<form
					className="row"
					onSubmit={(e) => {
						e.preventDefault();
						void runSearch(query);
					}}
				>
					<input
						className="input"
						style={{ flex: 1 }}
						aria-label="FIBO search query"
						value={query}
						onChange={(e) => setQuery(e.target.value)}
						placeholder="Search FIBO concepts, definitions, or synonyms..."
					/>
					<button
						type="submit"
						className="btn btn-primary"
						disabled={busy || !query.trim()}
					>
						{busy ? "Searching..." : "Search FIBO"}
					</button>
				</form>

				{canStageToDraft && onSetDescription && (
					<label className="field-col">
						<span className="field-label">
							Target Concept URI for Description Update (optional)
						</span>
						<input
							className="input mono"
							value={targetConceptUri}
							onChange={(e) => setTargetConceptUri(e.target.value)}
							placeholder="urn:opendv:..."
						/>
					</label>
				)}

				{problem && (
					<div className="banner banner-danger" role="alert">
						<strong>{problem.title}</strong> ({problem.error_code}):{" "}
						{problem.detail}
					</div>
				)}

				<div className="stack-sm">
					{matches.length === 0 && !busy ? (
						<p className="text-sm text-muted">
							Enter a term above to inspect matching FIBO core concepts
							(skos:exactMatch / skos:closeMatch).
						</p>
					) : (
						matches.map((m) => (
							<article key={m.concept_uri} className="card-subtle stack-sm">
								<div className="row-between">
									<div>
										<strong>{m.label}</strong>
										<div className="text-sm text-muted mono">
											{m.concept_uri}
										</div>
									</div>
									<div className="row">
										<span
											className={`pill ${m.suggested_path === "PATH_A1" ? "pill-ok" : "pill-warn"}`}
										>
											{m.suggested_path} · {m.normalized_score}
										</span>
									</div>
								</div>
								<p className="text-sm" style={{ margin: 0 }}>
									{m.definition}
								</p>
								<div className="row-between">
									<span className="text-sm text-muted mono">
										BM25: {m.raw_bm25}
										{m.parent_uri ? ` · Parent: ${m.parent_uri}` : ""}
									</span>
									<div className="row">
										<button
											type="button"
											className="btn btn-sm"
											onClick={() => void handleCopy(m.concept_uri)}
										>
											{copiedUri === m.concept_uri ? "Copied IRI" : "Copy IRI"}
										</button>
										{canStageToDraft && onStageConcept && (
											<button
												type="button"
												className="btn btn-sm btn-primary"
												onClick={() => onStageConcept(m)}
											>
												Stage Concept
											</button>
										)}
										{canStageToDraft &&
											onSetDescription &&
											targetConceptUri.trim() && (
												<button
													type="button"
													className="btn btn-sm btn-secondary"
													onClick={() =>
														onSetDescription(targetConceptUri.trim(), m)
													}
												>
													Set Description
												</button>
											)}
									</div>
								</div>
							</article>
						))
					)}
				</div>
			</div>
		</dialog>
	);
}
