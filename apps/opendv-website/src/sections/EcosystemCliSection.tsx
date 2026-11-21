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
							CONNECTORS, 5-ZONE SQL &amp; CONTRACT-IDENTICAL CLIS
						</p>
						<h2 id="ecosystem-heading">
							Universal connectors, 5-Zone IR, and Dual-CLI parity.
						</h2>
					</div>
					<p className="section-intro">
						Profile catalogs locally or push aggregates to remote warehouses,
						compile 5-Zone (Z0–Z4) <code>information_mart</code> SQL, and invoke
						every subcommand from Python or standalone Bun binaries.
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
								DUAL-CLI &amp; 6 HARNESS ADAPTERS
							</span>
						</div>
						<h3>
							<code>opendv</code> (Python) &amp; <code>opendvctl</code> (Bun)
						</h3>
						<p className="prose" style={{ fontSize: "0.92rem" }}>
							Both binaries generate from <code>schemas/v1/cli-spec.json</code>{" "}
							with 3-transport RFC 8785 JCS parity across{" "}
							<code>run dv-schema</code>,{" "}
							<code>ontology studio attach-source</code>,{" "}
							<code>ontology studio fibo-qa</code>, and{" "}
							<code>ontology studio logs</code>.
						</p>
						<dl className="scenario-readout" style={{ marginBlock: "1rem" }}>
							<div>
								<dt>opendv (Python In-Process)</dt>
								<dd>
									Runs directly against <code>opendv_service</code> with zero
									HTTP server; <code>serve --open</code> defaults to loopback
									open access.
								</dd>
							</div>
							<div>
								<dt>opendvctl (Bun Platform Binary)</dt>
								<dd>
									Streams SSE progress and session logs from{" "}
									<code>http://127.0.0.1:8420</code> with{" "}
									<code>Logs &amp; Traces</code> 50 MB retention.
								</dd>
							</div>
						</dl>

						<div>
							<p
								className="eyebrow"
								style={{ marginBlockEnd: "0.45rem", fontSize: "0.7rem" }}
							>
								6 Supported Agent Harness Providers
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
