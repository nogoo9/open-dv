import type React from "react";
import { VERIFIER_GATES } from "../data/site-data";
import { SixGateVerifierDiagram } from "../illustrations/SixGateVerifierDiagram";

export function VerifierDeepDiveSection(): React.JSX.Element {
	return (
		<section
			id="verifier"
			className="band"
			tabIndex={-1}
			aria-labelledby="verifier-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">
							SYMBOLIC &amp; PROVER FIREWALL · L1, V1–V6B, V7 &amp; V8 HORIZON
						</p>
						<h2 id="verifier-heading">
							Mathematical proof before a single wave seals.
						</h2>
					</div>
					<p className="section-intro">
						Every candidate <code>DraftVaultIR</code> lowers into{" "}
						<code>VaultSchemaIR v2.0</code> and passes six deterministic and
						critic gates (<code>L1</code>, <code>V1_V5</code>, <code>V6</code>,{" "}
						<code>V6B</code>, <code>V7</code>, <code>V8</code>),{" "}
						<code>15/15</code> DuckDB dynamic invariants, and{" "}
						<code>BRK-01..08</code> future-horizon stress tests.
					</p>
				</div>

				<SixGateVerifierDiagram />

				<ol
					className="grid-3"
					style={{
						listStyle: "none",
						margin: "1.75rem 0 0",
						padding: 0,
					}}
					aria-label="CEGAR Gates L1 to V8"
				>
					{VERIFIER_GATES.map((gate) => (
						<li key={gate.id} className="card">
							<div className="badge-row" style={{ marginBlockEnd: "0.55rem" }}>
								<span className="badge badge-pass">{gate.id}</span>
								<span className="badge">{gate.rules}</span>
							</div>
							<h3>{gate.name}</h3>
							<p className="prose" style={{ fontSize: "0.92rem" }}>
								{gate.check}
							</p>
							<p
								className="prose"
								style={{
									fontSize: "0.86rem",
									marginBlockStart: "0.75rem",
									paddingBlockStart: "0.75rem",
									borderBlockStart: "1px solid var(--border)",
								}}
							>
								<strong>Repair:</strong> {gate.repair}
							</p>
						</li>
					))}
				</ol>
			</div>
		</section>
	);
}
