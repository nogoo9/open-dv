import { useEffect, useState } from "react";
import type {
	ClarificationQuestion,
	StagedCandidateSpec,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

const PATHS: readonly [string, string][] = [
	["PATH_A1", "pill-ok"],
	["PATH_A2", "pill-warn"],
	["PATH_B1", "pill-sal"],
	["PATH_B2", "pill-err"],
];
const A: Record<string, Record<string, unknown>> = {
	s: { tabIndex: 0 },
	t: { role: "table" },
	r: { role: "row" },
	c: { role: "cell" },
	h: { role: "rowheader" },
};

export interface HoldQuestionCardProps {
	questions: ClarificationQuestion[];
	busy: boolean;
	draining: boolean;
	onSubmit: (answers: Record<string, string>) => Promise<void>;
}

export function HoldQuestionCard({
	questions,
	busy,
	draining,
	onSubmit,
}: HoldQuestionCardProps) {
	const setDirtyFlag = useProjectStore((s) => s.setDirtyFlag);
	const [idx, setIdx] = useState(0);
	const [ans, setAns] = useState<Record<string, string>>(() =>
		Object.fromEntries(
			questions.map((q) => [
				q.question_id,
				q.recommended_answer?.trim() ? q.recommended_answer : "",
			]),
		),
	);
	const [custom, setCustom] = useState<Record<string, boolean>>(() =>
		Object.fromEntries(
			questions.map((q) => [q.question_id, !q.recommended_answer?.trim()]),
		),
	);

	useEffect(() => {
		setDirtyFlag("step6Decisions", false);
	}, [setDirtyFlag]);

	const cur = questions[idx];
	if (!cur) return null;
	const hasRec = Boolean(cur.recommended_answer?.trim());
	const allOk = questions.every(
		(q) => (ans[q.question_id] ?? "").trim().length > 0,
	);
	const pick = (val: string, isCust: boolean) => {
		setCustom((p) => ({ ...p, [cur.question_id]: isCust }));
		setAns((p) => ({ ...p, [cur.question_id]: val }));
		setDirtyFlag("step6Decisions", true);
	};

	const rows = cur.sample_rows;
	const tbl =
		rows.length > 0 && rows.every((r) => r.includes("|"))
			? rows.map((r) => r.split("|").map((s) => s.trim()))
			: null;
	const hdr = tbl && tbl.length > 1 ? (tbl[0] ?? []) : [];
	const body = tbl ? (hdr.length ? tbl.slice(1) : tbl) : [];

	return (
		<section className="card-cream stack-md">
			<div className="row-between">
				<div>
					<h2 className="h2-serif">{cur.title}</h2>
					<span className="text-sm text-muted">
						Concept: {cur.concept_name}
					</span>
				</div>
				<div className="row">
					<span className="pill">
						Question {idx + 1} of {questions.length}
					</span>
					<button
						type="button"
						className="btn btn-sm"
						disabled={idx === 0}
						onClick={() => setIdx((i) => Math.max(0, i - 1))}
					>
						Prev
					</button>
					<button
						type="button"
						className="btn btn-sm"
						disabled={idx >= questions.length - 1}
						onClick={() => setIdx((i) => Math.min(questions.length - 1, i + 1))}
					>
						Next
					</button>
				</div>
			</div>
			<p style={{ margin: 0 }}>{cur.prompt}</p>
			<div className="callout-amber text-sm">
				<strong>Impact if locked:</strong> {cur.impact_if_locked}
			</div>
			{rows.length > 0 &&
				(!tbl ? (
					<div className="card-subtle stack-sm">
						<strong className="text-sm">Sample Evidence</strong>
						<ul className="mono text-sm" style={{ margin: 0, paddingLeft: 18 }}>
							{rows.map((r) => (
								<li key={r}>{r}</li>
							))}
						</ul>
					</div>
				) : (
					<div className="table-scroll-region" {...A.s}>
						<table className="responsive-table" {...A.t}>
							{hdr.length > 0 && (
								<thead>
									<tr {...A.r}>
										{hdr.map((c) => (
											<th key={c} scope="col">
												{c}
											</th>
										))}
									</tr>
								</thead>
							)}
							<tbody>
								{body.map((r, ri) => (
									<tr key={`${ri}-${r.join("|")}`} {...A.r}>
										{r.map((v, ci) => (
											<td
												key={`${ri}-${ci}-${hdr[ci] ?? ""}`}
												{...A.c}
												data-label={hdr[ci] ?? `Col ${ci + 1}`}
												className="mono"
											>
												{v}
											</td>
										))}
									</tr>
								))}
							</tbody>
						</table>
					</div>
				))}
			<fieldset className="radio-group" disabled={draining || busy}>
				<legend className="text-sm font-semibold">
					Steward Clarification Answer
				</legend>
				{hasRec ? (
					<>
						<label className="radio-row">
							<input
								type="radio"
								name={`hold-${cur.question_id}`}
								checked={!custom[cur.question_id]}
								onChange={() => pick(cur.recommended_answer ?? "", false)}
							/>
							<span>Recommended: {cur.recommended_answer}</span>
						</label>
						<label className="radio-row">
							<input
								type="radio"
								name={`hold-${cur.question_id}`}
								checked={Boolean(custom[cur.question_id])}
								onChange={() => {
									setCustom((p) => ({ ...p, [cur.question_id]: true }));
									setDirtyFlag("step6Decisions", true);
								}}
							/>
							<span>Custom Steward Answer</span>
						</label>
					</>
				) : (
					<p className="text-sm text-muted" style={{ margin: 0 }}>
						No preset recommendation — enter a custom Steward answer below.
					</p>
				)}
				{(!hasRec || custom[cur.question_id]) && (
					<textarea
						rows={2}
						className="textarea"
						aria-label={`Answer for ${cur.title}`}
						value={ans[cur.question_id] ?? ""}
						onChange={(e) => pick(e.target.value, true)}
						placeholder="Enter plain-English Steward guidance..."
					/>
				)}
			</fieldset>
			<div className="row-end">
				<button
					type="button"
					className="btn btn-primary"
					disabled={busy || draining || !allOk}
					onClick={() => void onSubmit(ans)}
				>
					Submit Answer &amp; Resume Discovery →
				</button>
			</div>
		</section>
	);
}

export interface GroundingMatrixProps {
	candidates: StagedCandidateSpec[];
	onInspect: (c: StagedCandidateSpec) => void;
}

export function GroundingMatrix({
	candidates,
	onInspect,
}: GroundingMatrixProps) {
	const counts = candidates.reduce(
		(a, c) => {
			a[c.grounding_path] = (a[c.grounding_path] ?? 0) + 1;
			return a;
		},
		{} as Record<string, number>,
	);
	return (
		<section className="panel stack-md">
			<div className="row-between">
				<h2 className="h2-serif">
					Staged Candidates &amp; 4-Path Grounding Matrix
				</h2>
				<div className="row">
					{PATHS.map(([k, cls]) => (
						<span key={k} className={`pill ${cls}`}>
							{k}: {counts[k] ?? 0}
						</span>
					))}
				</div>
			</div>
			{candidates.length === 0 ? (
				<p className="text-sm text-muted">
					No staged discovery candidates yet.
				</p>
			) : (
				<div className="table-scroll-region" {...A.s}>
					<table className="responsive-table" {...A.t}>
						<thead>
							<tr {...A.r}>
								{[
									"Concept",
									"Grounding Path",
									"Status & BM25",
									"Evidence",
									"Inspector",
								].map((h) => (
									<th key={h} scope="col">
										{h}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{candidates.map((c) => (
								<tr key={c.candidate_id} {...A.r}>
									<th scope="row" {...A.h}>
										<div>{c.concept_name}</div>
										<div className="text-sm text-muted mono">
											{c.concept_uri}
										</div>
									</th>
									<td {...A.c} data-label="Grounding Path">
										<span
											className={`pill ${PATHS.find(([k]) => k === c.grounding_path)?.[1] ?? "pill-err"}`}
										>
											{c.grounding_path}
										</span>
									</td>
									<td {...A.c} data-label="Status" className="mono text-sm">
										{c.status}
										{c.bm25_score ? ` · ${c.bm25_score}` : ""}
									</td>
									<td {...A.c} data-label="Evidence" className="text-sm">
										{c.table_bindings?.length ?? 0}t ·{" "}
										{c.source_citations?.length ?? 0}c ·{" "}
										{c.mcp_evidence?.length ?? 0} MCP
									</td>
									<td {...A.c} data-label="Inspector">
										<button
											type="button"
											className="btn btn-cite"
											onClick={() => onInspect(c)}
										>
											Inspect Citations
										</button>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</section>
	);
}
