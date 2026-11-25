import type React from "react";
import {
	CONNECTORS,
	DIALECT_SNIPPETS,
	HARNESS_PROVIDERS,
} from "../data/site-data";

export function EcosystemCliSection(): React.JSX.Element {
	return (
		<section
			id="ecosystem"
			className="band band-soft"
			tabIndex={-1}
			aria-labelledby="ecosystem-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">
							CONNECTORS, 11 TARGETS &amp; UNIFIED OPENDV CLI
						</p>
						<h2 id="ecosystem-heading">
							Universal connectors, VaultSchemaIR v2.0, and unified CLI/MCP
							parity.
						</h2>
					</div>
					<p className="section-intro">
						Profile sources via 6 static adapters, compile{" "}
						<code>VaultSchemaIR v2.0</code> across 11 targets (8 SQL dialects +
						Iceberg, Parquet, and dbt), and invoke every workflow via Python CLI
						or 17-tool <code>stdio</code> MCP.
					</p>
				</div>

				<div
					className="grid-6"
					style={{ marginBlockEnd: "2.25rem" }}
					aria-label="Supported source connectors"
				>
					{CONNECTORS.map((connector) => (
						<article key={connector.id} className="card">
							<h3 style={{ marginBlockEnd: "0.45rem" }}>{connector.name}</h3>
							<div className="badge-row" style={{ marginBlockEnd: "0.65rem" }}>
								<code className="badge badge-hub">
									{connector.executionClass}
								</code>
							</div>
							<p className="prose" style={{ fontSize: "0.88rem" }}>
								{connector.introspection}
							</p>
						</article>
					))}
				</div>

				<div className="feature-split">
					<div data-tabs-group="dialects">
						<div role="tablist" aria-label="Output dialects, CLIs, and guards">
							{DIALECT_SNIPPETS.map((snippet, idx) => (
								<button
									key={snippet.id}
									id={`dialect-tab-${snippet.id}`}
									type="button"
									role="tab"
									aria-selected={idx === 0 ? "true" : "false"}
									tabIndex={idx === 0 ? 0 : -1}
									data-tab-target={`dialect-panel-${snippet.id}`}
								>
									{snippet.label}
								</button>
							))}
						</div>

						<div className="tab-panels">
							{DIALECT_SNIPPETS.map((snippet, idx) => (
								<div
									key={snippet.id}
									id={`dialect-panel-${snippet.id}`}
									role="tabpanel"
									className="tab-panel"
									aria-labelledby={`dialect-tab-${snippet.id}`}
									{...(idx === 0
										? { tabIndex: 0 }
										: ({ hidden: "until-found" } as Record<string, unknown>))}
								>
									<div className="tab-panel-card">
										<div className="code-well">
											<div className="code-header">
												<div className="badge-row">
													<code>{snippet.filename}</code>
													<span className="badge">
														{snippet.lang.toUpperCase()}
													</span>
												</div>
												<button
													type="button"
													className="copy-btn"
													data-copy-text={snippet.code}
													aria-label={`Copy ${snippet.label} snippet`}
												>
													Copy
												</button>
											</div>
											<pre className="code-block">
												<code className={`language-${snippet.lang}`}>
													{snippet.code}
												</code>
											</pre>
										</div>
									</div>
								</div>
							))}
						</div>
					</div>

					<aside className="card">
						<div className="badge-row" style={{ marginBlockEnd: "0.55rem" }}>
							<span className="badge badge-gold">
								UNIFIED CLI · 17-TOOL MCP · 34 ERROR CODES
							</span>
						</div>
						<h3>
							<code>opendv</code> CLI &amp; <code>stdio</code> MCP
						</h3>
						<p className="prose" style={{ fontSize: "0.92rem" }}>
							Generated canonically from <code>schemas/v2/cli-spec.json</code>{" "}
							with RFC 8785 JCS and 34-code parity across{" "}
							<code>project init</code>, <code>source attach</code>,{" "}
							<code>ontology fibo-qa</code>, <code>vault build</code>,{" "}
							<code>vault logs</code>, and <code>storage query</code>.
						</p>
						<dl className="scenario-readout" style={{ marginBlock: "1rem" }}>
							<div>
								<dt>In-Process or --api-url</dt>
								<dd>
									Runs directly against <code>OpenDVService</code> over 5-table{" "}
									<code>project.sqlite</code> or targets a live{" "}
									<code>:8420</code> server.
								</dd>
							</div>
							<div>
								<dt>17-Tool stdio MCP &amp; Run Explorer</dt>
								<dd>
									<code>opendv mcp serve</code> binds IDE agents cleanly while{" "}
									<code>Run Explorer</code> streams <code>Last-Event-ID</code>{" "}
									timelines, attempt snapshots, and receipts.
								</dd>
							</div>
						</dl>

						<div>
							<p
								className="eyebrow"
								style={{ marginBlockEnd: "0.45rem", fontSize: "0.7rem" }}
							>
								4 Supported Agent Harness Providers
							</p>
							<ul
								className="badge-row"
								style={{ listStyle: "none", margin: 0, padding: 0 }}
							>
								{HARNESS_PROVIDERS.map((provider) => (
									<li key={provider}>
										<code className="badge">{provider}</code>
									</li>
								))}
							</ul>
						</div>
					</aside>
				</div>
			</div>
		</section>
	);
}
