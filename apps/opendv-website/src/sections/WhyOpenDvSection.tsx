import type React from "react";
import { NAIVE_TRAPS } from "../data/site-data";

const PROVEN_GUARANTEES: readonly {
	readonly id: string;
	readonly title: string;
	readonly summary: string;
}[] = [
	{
		id: "Proof 1",
		title: "Ontology-Grounded 3-Tier BKCC & SAL",
		summary:
			"Grounds core business entities against FIBO + Beyond-FIBO in Steps 1–5 and reconciles cross-feed identities via Same-As Links.",
	},
	{
		id: "Proof 2",
		title: "Deterministic Surrogate Quarantine & Triage",
		summary:
			"Graph B profiling blocks auto-increment surrogate IDs at Gate G1 and audits 100% of profiled columns in DV Schema Explorer (06c).",
	},
	{
		id: "Proof 3",
		title: "Injective Length-Prefixed SHA-256 (Alg A.5)",
		summary:
			"Emits <len>:<val> canonical digests and nullable FK Link splits (Alg A.4) to guarantee zero boundary-shift collisions.",
	},
	{
		id: "Proof 4",
		title: "Insert-Only AST & 5-Zone (Z0–Z4) Compilation",
		summary:
			"SQLGlot AST checks ban UPDATE/DELETE on Raw Vault while Alg A.9a routes drift into _ext satellites across bronze to information_mart.",
	},
	{
		id: "Proof 5",
		title: "Span-Grounded Docs, PII Masking & Cost Meter",
		summary:
			"LiteParse anchors every claim to [char_start, char_end] spans, redacts PII/MNPI before LLM calls, and enforces live Cost Meter budget holds.",
	},
];

const NAIVE_SQL_SNIPPET = `-- Trap 2 + 3 + 4: Surrogate key, colliding hash, destructive UPDATE
CREATE TABLE hub_customer (
  customer_sk INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  hk_customer VARCHAR DEFAULT MD5(CONCAT_WS('|', src_id, code))
);

UPDATE sat_customer
   SET risk_tier = 'HIGH'
 WHERE customer_sk = 402; -- erases raw vault audit history`;

const PROVEN_SQL_SNIPPET = `-- Z1->Z4 5-Zone SQL (Alg A.5 injective SHA-256 + information_mart)
CREATE SCHEMA IF NOT EXISTS information_mart;
SELECT LOWER(SHA256(
  '33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F||' ||
  CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR)
  || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), '')
)) AS hk_holding FROM bronze.src_hub_holding;`;

export function WhyOpenDvSection(): React.JSX.Element {
	return (
		<section
			id="why"
			className="band"
			tabIndex={-1}
			aria-labelledby="why-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">
							WHY OPEN-DV · FORMAL INVARIANTS VS. RAW PROMPTING
						</p>
						<h2 id="why-heading">
							Why naive LLM SQL silently breaks Data Vault 2.0 auditability.
						</h2>
					</div>
					<p className="section-intro">
						Asking a language model to template warehouse DDL/DML directly lets
						subtle key, hash, and mutability defects slip into production.
						Open-DV separates Agent-first discovery from deterministic proof.
					</p>
				</div>

				<div className="grid-2">
					<article className="card compare-card compare-card-risk">
						<div className="badge-row">
							<span className="badge badge-risk">
								<span aria-hidden="true">✕</span> [RISK]
							</span>
							<h3>Naive LLM SQL Generation</h3>
						</div>

						<ol className="trap-list">
							{NAIVE_TRAPS.map((trap) => (
								<li key={trap.id} className="trap-item">
									<strong>
										{trap.id}: {trap.title}
									</strong>
									<p>{trap.summary}</p>
								</li>
							))}
						</ol>

						<div className="code-well">
							<div className="code-header">
								<span>unverified_template.sql</span>
								<span className="badge badge-risk">
									<span aria-hidden="true">!</span> Unchecked
								</span>
							</div>
							<pre className="code-block">
								<code className="language-sql">{NAIVE_SQL_SNIPPET}</code>
							</pre>
						</div>
					</article>

					<article className="card compare-card compare-card-proven">
						<div className="badge-row">
							<span className="badge badge-pass">
								<span aria-hidden="true">✓</span> [PROVEN]
							</span>
							<h3>Open-DV Formal Verification &amp; 5-Zone Compiler</h3>
						</div>

						<ol className="trap-list">
							{PROVEN_GUARANTEES.map((item) => (
								<li key={item.id} className="trap-item">
									<strong>
										{item.id}: {item.title}
									</strong>
									<p>{item.summary}</p>
								</li>
							))}
						</ol>

						<div className="code-well">
							<div className="code-header">
								<span>dv_duckdb_schema.sql (5-Zone Z0–Z4)</span>
								<span className="badge badge-pass">
									<span aria-hidden="true">✓</span> G0–G5 Sealed
								</span>
							</div>
							<pre className="code-block">
								<code className="language-sql">{PROVEN_SQL_SNIPPET}</code>
							</pre>
						</div>
					</article>
				</div>
			</div>
		</section>
	);
}
