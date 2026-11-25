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
			"Grounds concepts via FIBO PATH_A1/A2 (BM25 >= 0.65) and Beyond-FIBO PATH_B1 across Steps 4–5, reconciling feeds via BKCC and sal_*.",
	},
	{
		id: "Proof 2",
		title: "100% Column Triage & Surrogate Quarantine",
		summary:
			"L1 + V1 (DV-COL-001) ensure every column carries a triage disposition while Graph B profiling quarantines auto-increment surrogate IDs.",
	},
	{
		id: "Proof 3",
		title: "Injective Length-Prefixed SHA-256 (Alg A.5)",
		summary:
			"Emits <len>:<val> canonical digests with -1:^^ null framing, -1/-2 Dual Zero Keys, Ghost 0*64 records, and sparse FK Link splits (Alg A.4).",
	},
	{
		id: "Proof 4",
		title: "Insert-Only AST & 11-Target Evaluation",
		summary:
			"V6 DuckDbProverAdapter (15 DV-DYN) and Step 7 TargetEvalGate (EVAL-AST/RUN/FMT) verify 8 SQL dialects + Iceberg, Parquet & dbt across Z0–Z4.",
	},
	{
		id: "Proof 5",
		title: "Span-Grounded Docs, PII Split & Budget Meter",
		summary:
			"Alg A.9b verifies [char_start, char_end] or OcrPageReceipt spans, isolates sat_*_pii (<MASKED_PII>), and enforces live Budget Meter holds.",
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
SELECT UNHEX(SHA256(
  '33:AM_SEC_TREASURY_IBOR_HOLDINGS_13F||' ||
  COALESCE(CAST(OCTET_LENGTH(ENCODE(NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''))) AS VARCHAR)
  || ':' || NULLIF(UPPER(TRIM(CAST(holding_id AS VARCHAR))), ''), '-1:^^')
)) AS hk_holding FROM bronze.stg_src_holdings_13f;`;

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
						Open-DV separates brokered Agent discovery from deterministic CEGAR
						proofs.
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
								<span>targets/duckdb_local/ddl/full_schema_w2.sql</span>
								<span className="badge badge-pass">
									<span aria-hidden="true">✓</span> V1–V8 Sealed
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
