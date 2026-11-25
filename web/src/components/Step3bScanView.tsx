import { useEffect, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type { PageTextView, ProblemDetails } from "../api/openapi";
import {
	type Step3ScanDraftEntry,
	useProjectStore,
} from "../store/projectStore";

const DOC_WINDOW = 5;
const EMPTY_DRAFT: Step3ScanDraftEntry = {
	pages: [],
	rationale: "",
	allowExternalOcr: false,
};

export interface Step3bScanViewProps {
	projectId: string;
	setProblem: (p: ProblemDetails | null) => void;
}

export function Step3bScanView({ projectId, setProblem }: Step3bScanViewProps) {
	const {
		documentMaps,
		sources,
		step3ScanDraft,
		setStep3ScanDraft,
		setDocumentMaps,
		announce,
	} = useProjectStore();
	const [docId, setDocId] = useState<string | null>(
		documentMaps[0]?.doc_id ?? null,
	);
	const [pageStart, setPageStart] = useState(1);
	const [pages, setPages] = useState<PageTextView[]>([]);
	const [loading, setLoading] = useState(false);
	const [busy, setBusy] = useState(false);

	const doc =
		documentMaps.find((d) => d.doc_id === docId) ?? documentMaps[0] ?? null;
	const docSrc = doc
		? (sources.find((s) => s.source_id === doc.doc_id) ??
			sources.find((s) => s.uri_or_filename === doc.filename))
		: undefined;
	const docOffline = docSrc?.status === "METADATA_ONLY_BINARY_ABSENT";

	useEffect(() => {
		if (!doc || docOffline) {
			setPages([]);
			setLoading(false);
			return;
		}
		const end = Math.min(doc.page_count, pageStart + DOC_WINDOW - 1);
		const approvedSet = new Set(doc.approved_scan_pages ?? []);
		const firstUnapproved = (doc.pages ?? []).find(
			(p) =>
				p.is_scanned_or_low_density &&
				p.page_number >= pageStart &&
				p.page_number <= end &&
				!approvedSet.has(p.page_number),
		);
		const safeEnd = firstUnapproved ? firstUnapproved.page_number - 1 : end;
		if (safeEnd < pageStart) {
			setPages([]);
			setLoading(false);
			return;
		}
		let ok = true;
		setLoading(true);
		setProblem(null);
		void api
			.getDocumentPages(projectId, doc.doc_id, pageStart, safeEnd)
			.then((r) => ok && setPages(r))
			.catch((e) => {
				if (ok) {
					setPages([]);
					setProblem(parseProblemDetails(e));
				}
			})
			.finally(() => ok && setLoading(false));
		return () => {
			ok = false;
		};
	}, [projectId, doc, pageStart, docOffline, setProblem]);

	const scanned = (doc?.pages ?? []).filter((p) => p.is_scanned_or_low_density);
	const draft = doc ? (step3ScanDraft[doc.doc_id] ?? EMPTY_DRAFT) : EMPTY_DRAFT;
	const upd = (patch: Partial<Step3ScanDraftEntry>) => {
		if (doc) setStep3ScanDraft(doc.doc_id, { ...draft, ...patch });
	};
	const togglePage = (num: number) => {
		const nextPages = draft.pages.includes(num)
			? draft.pages.filter((n) => n !== num)
			: [...draft.pages, num].sort((a, b) => a - b);
		upd({ pages: nextPages });
	};

	const onApprove = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!doc || draft.pages.length === 0 || !draft.rationale.trim()) return;
		setBusy(true);
		setProblem(null);
		try {
			const updated = await api.approveScan(projectId, doc.doc_id, {
				page_numbers: draft.pages,
				rationale: draft.rationale.trim(),
				allow_unredacted_external_ocr: draft.allowExternalOcr,
			});
			setDocumentMaps(
				documentMaps.map((d) => (d.doc_id === updated.doc_id ? updated : d)),
			);
			useProjectStore.setState((s) => {
				const next = { ...s.step3ScanDraft };
				delete next[doc.doc_id];
				return {
					step3ScanDraft: next,
					dirtyFlags: {
						...s.dirtyFlags,
						step3Scan: Object.keys(next).length > 0,
					},
				};
			});
			announce("polite", `Approved scan pages for ${doc.filename}.`);
		} catch (x) {
			setProblem(parseProblemDetails(x));
		} finally {
			setBusy(false);
		}
	};

	const endPage = doc
		? Math.min(doc.page_count, pageStart + DOC_WINDOW - 1)
		: 0;

	return (
		<div className="split-grid">
			<section className="panel stack-sm">
				<h2 className="h2-serif">DocumentMap Outlines</h2>
				{documentMaps.length === 0 ? (
					<p className="text-sm text-muted">No documents attached yet.</p>
				) : (
					documentMaps.map((d) => (
						<div
							key={d.doc_id}
							className={
								doc?.doc_id === d.doc_id
									? "card-selected stack-sm"
									: "card-subtle stack-sm"
							}
						>
							<div className="row-between">
								<button
									type="button"
									className="btn btn-sm btn-ghost mono"
									onClick={() => {
										setDocId(d.doc_id);
										setPageStart(1);
									}}
								>
									<strong>{d.filename}</strong>
								</button>
								<span className="pill">
									{d.format} · {d.page_count}p
								</span>
							</div>
							{(d.outline ?? []).length > 0 && (
								<ul style={{ margin: 0, paddingLeft: 18 }}>
									{(d.outline ?? []).map((n) => (
										<li key={`${n.page_start}-${n.title}`}>
											p.{n.page_start} · {n.title}
										</li>
									))}
								</ul>
							)}
							{(d.pages ?? []).map((pg) => (
								<div key={pg.page_number} className="row-between text-sm">
									<span className="mono">
										p.{pg.page_number} ({pg.char_count}c ·{" "}
										{pg.canonical_text_sha256.slice(0, 8)})
									</span>
									<span className="row">
										{(d.approved_scan_pages ?? []).includes(pg.page_number) && (
											<span className="pill pill-ok">
												p.{pg.page_number} · Approved ✓
											</span>
										)}
										{pg.is_scanned_or_low_density && (
											<span className="pill pill-warn">
												SCANNED · {pg.ocr_box_count} boxes
											</span>
										)}
									</span>
								</div>
							))}
						</div>
					))
				)}
			</section>

			{doc && (
				<div className="stack-md">
					<section className="panel stack-sm">
						<div className="row-between">
							<h2 className="h2-serif">Page Window · {doc.filename}</h2>
							<div className="row">
								<button
									type="button"
									className="btn btn-sm"
									disabled={pageStart <= 1}
									onClick={() =>
										setPageStart(Math.max(1, pageStart - DOC_WINDOW))
									}
								>
									Prev 5
								</button>
								<span className="text-sm mono">
									p.{pageStart}–{endPage} / {doc.page_count}
								</span>
								<button
									type="button"
									className="btn btn-sm"
									disabled={pageStart + DOC_WINDOW > doc.page_count}
									onClick={() => setPageStart(pageStart + DOC_WINDOW)}
								>
									Next 5
								</button>
							</div>
						</div>
						{docOffline ? (
							<div className="callout-teal">
								Offline fallback (METADATA_ONLY_BINARY_ABSENT): live `/pages`
								suppressed.
							</div>
						) : loading ? (
							<p className="text-sm text-muted">Loading 5-page window...</p>
						) : pages.length === 0 ? (
							<p className="text-sm text-muted">
								Approve scanned pages below to view page text.
							</p>
						) : (
							pages.map((pv) => (
								<article key={pv.page_number} className="card-subtle stack-sm">
									<div className="row-between">
										<strong className="mono">Page {pv.page_number}</strong>
										{pv.scan_approved && (
											<span className="pill pill-ok">Approved ✓</span>
										)}
									</div>
									<pre
										className="mono text-sm"
										style={{ whiteSpace: "pre-wrap", margin: 0 }}
									>
										{pv.masked_text}
									</pre>
								</article>
							))
						)}
					</section>

					{scanned.length > 0 && (
						<form
							className="callout-coral stack-sm"
							onSubmit={(e) => void onApprove(e)}
						>
							<h2 className="h2-serif">Steward Scan Consent Gate</h2>
							<p className="text-sm">
								Prior approved:{" "}
								<span className="mono">
									{(doc.approved_scan_pages ?? []).join(", ") || "None"}
								</span>
							</p>
							{scanned.map((sp) => (
								<label key={sp.page_number} className="radio-row text-sm">
									<input
										type="checkbox"
										checked={draft.pages.includes(sp.page_number)}
										onChange={() => togglePage(sp.page_number)}
									/>
									<span className="mono">
										Page {sp.page_number} ·{" "}
										{sp.ocr_box_count >= 1
											? `[ocr] ready (${sp.ocr_box_count} boxes)`
											: "[ocr] absent (0 boxes)"}
									</span>
								</label>
							))}
							<label className="field-col">
								<span className="field-label">Approval Rationale</span>
								<textarea
									rows={2}
									className="textarea"
									value={draft.rationale}
									onChange={(e) => upd({ rationale: e.target.value })}
								/>
							</label>
							<label className="radio-row text-sm">
								<input
									type="checkbox"
									checked={draft.allowExternalOcr}
									onChange={(e) => upd({ allowExternalOcr: e.target.checked })}
								/>
								<span>
									Allow unredacted external OCR when local [ocr] is absent
								</span>
							</label>
							<div className="row-end">
								<button
									type="submit"
									className="btn btn-primary"
									disabled={
										busy || draft.pages.length === 0 || !draft.rationale.trim()
									}
								>
									{busy
										? "Approving..."
										: draft.pages.length === 1
											? `Approve Page ${draft.pages[0]} Scan`
											: "Approve Selected Pages Scan"}
								</button>
							</div>
						</form>
					)}
				</div>
			)}
		</div>
	);
}
