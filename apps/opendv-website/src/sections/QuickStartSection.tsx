import type React from "react";
import {
	AGENT_QUICKSTART_PROMPT,
	DOC_REFS,
	QUICKSTART_STEPS,
	SITE_META,
} from "../data/site-data";

export function QuickStartSection(): React.JSX.Element {
	return (
		<section
			id="quickstart"
			className="band"
			tabIndex={-1}
			aria-labelledby="quickstart-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">5-MINUTE QUICK START &amp; SPECIFICATIONS</p>
						<h2 id="quickstart-heading">
							Launch locally on :8420 or verify headlessly in CI.
						</h2>
					</div>
					<p className="section-intro">
						Start the unified Ontology Studio and Pipeline Builder on{" "}
						<code>http://127.0.0.1:8420</code>, or copy the setup prompt for
						your coding agent.
					</p>
				</div>

				<ol
					className="grid-3"
					style={{ listStyle: "none", margin: 0, padding: 0 }}
				>
					{QUICKSTART_STEPS.map((item) => (
						<li key={item.step} className="card">
							<div className="badge-row" style={{ marginBlockEnd: "0.55rem" }}>
								<span className="badge badge-gold">Step {item.step}</span>
							</div>
							<h3>{item.title}</h3>
							<div className="cmd-box" style={{ marginBlock: "0.75rem" }}>
								<code>{item.command}</code>
								<button
									type="button"
									className="copy-btn"
									data-copy-text={item.command}
									aria-label={`Copy step ${item.step} command`}
								>
									Copy
								</button>
							</div>
							<p className="prose" style={{ fontSize: "0.88rem" }}>
								{item.note}
							</p>
						</li>
					))}
				</ol>

				<div className="card" style={{ marginBlockStart: "1.5rem" }}>
					<div className="section-head" style={{ marginBlockEnd: "0.9rem" }}>
						<div className="section-head-copy">
							<div className="badge-row" style={{ marginBlockEnd: "0.45rem" }}>
								<span className="badge badge-gold">CODING AGENT HANDOFF</span>
							</div>
							<h3>Delegate setup to your coding agent</h3>
						</div>
						<button
							type="button"
							className="btn btn-primary"
							data-copy-text={AGENT_QUICKSTART_PROMPT}
						>
							Copy quickstart for your coding agent
						</button>
					</div>
					<div className="code-well">
						<div className="code-header">
							<span>agent_quickstart_prompt.txt</span>
						</div>
						<pre className="code-block">
							<code>{AGENT_QUICKSTART_PROMPT}</code>
						</pre>
					</div>
				</div>

				<div style={{ marginBlockStart: "2.25rem" }}>
					<div className="section-head" style={{ marginBlockEnd: "1rem" }}>
						<h3>Architecture &amp; Mathematical Specifications</h3>
						<a
							href={SITE_META.docsBaseUrl}
							target="_blank"
							rel="noopener noreferrer"
						>
							Browse all docs on GitHub ↗
						</a>
					</div>
					<div className="spec-table-wrap">
						<table className="spec-table">
							<caption className="visually-hidden">
								Open-DV normative documentation references
							</caption>
							<thead>
								<tr>
									<th scope="col">ID</th>
									<th scope="col">Specification Document</th>
									<th scope="col">Scope</th>
								</tr>
							</thead>
							<tbody>
								{DOC_REFS.map((doc) => (
									<tr key={doc.id}>
										<td>
											<code>{doc.id}</code>
										</td>
										<td>
											<a
												href={`${SITE_META.docsBaseUrl}/${doc.path}`}
												target="_blank"
												rel="noopener noreferrer"
											>
												{doc.path} ↗
											</a>
										</td>
										<td>{doc.topic}</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</div>
			</div>
		</section>
	);
}
