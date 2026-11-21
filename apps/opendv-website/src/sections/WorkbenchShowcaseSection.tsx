import type React from "react";
import { WORKBENCH_SCREENS } from "../data/site-data";
import { withBase } from "../lib/base-path";

export function WorkbenchShowcaseSection({
	basePath,
}: {
	readonly basePath?: string | undefined;
}): React.JSX.Element {
	return (
		<section
			id="workbench"
			className="band band-soft"
			tabIndex={-1}
			aria-labelledby="workbench-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">
							4-TAB WORKBENCH · ONTOLOGY &amp; PIPELINE | DV SCHEMA EXPLORER |
							RUN EXPLORER | SETTINGS
						</p>
						<h2 id="workbench-heading">
							12-Screen Tour: 6-Step Wizard, 4-View DV Schema Explorer &amp;
							Session Logs.
						</h2>
					</div>
					<p className="section-intro">
						Explore all 12 screens at 1440×920 (2× Retina) across the Agent-only
						6-Step Wizard, the 4-View DV Schema Explorer (Graph, 100% Column
						Triage, W1→W3 Wave Diff, and Safe Read-Only SQL via{" "}
						<code>validate_safe_sql()</code>), plus live Cost Meter and{" "}
						<code>Logs &amp; Traces</code>.
					</p>
				</div>

				<div data-tabs-group="workbench">
					<div role="tablist" aria-label="Open-DV Workbench 12-screen tour">
						{WORKBENCH_SCREENS.map((screen, idx) => (
							<button
								key={screen.id}
								id={`wb-tab-${screen.id}`}
								type="button"
								role="tab"
								aria-selected={idx === 0 ? "true" : "false"}
								tabIndex={idx === 0 ? 0 : -1}
								data-tab-target={`wb-panel-${screen.id}`}
							>
								{screen.tabLabel}
							</button>
						))}
					</div>

					<div className="tab-panels">
						{WORKBENCH_SCREENS.map((screen, idx) => (
							<div
								key={screen.id}
								id={`wb-panel-${screen.id}`}
								role="tabpanel"
								className="tab-panel"
								aria-labelledby={`wb-tab-${screen.id}`}
								{...(idx === 0
									? { tabIndex: 0 }
									: ({ hidden: "until-found" } as Record<string, unknown>))}
							>
								<div className="tab-panel-card" data-pin-scope={screen.id}>
									<div
										className="section-head"
										style={{ marginBlockEnd: "1rem" }}
									>
										<div className="section-head-copy">
											<h3>{screen.title}</h3>
											<p className="prose" style={{ fontSize: "0.94rem" }}>
												{screen.summary}
											</p>
										</div>
										<div className="badge-row">
											<code className="badge">{screen.route}</code>
											<button
												type="button"
												className="btn btn-secondary btn-sm"
												data-lightbox-open={`workbench-${screen.id}`}
												data-lightbox-image={withBase(
													screen.webpPath,
													basePath,
												)}
												data-lightbox-title={screen.title}
											>
												Enlarge 2× Preview
											</button>
										</div>
									</div>

									<div className="gallery-bezel">
										<div className="screenshot-stage" data-theme={screen.theme}>
											<picture>
												<source
													srcSet={withBase(screen.webpPath, basePath)}
													type="image/webp"
												/>
												<img
													src={withBase(screen.pngPath, basePath)}
													width={screen.width}
													height={screen.height}
													loading="lazy"
													fetchPriority={idx > 0 ? "low" : undefined}
													alt={screen.alt}
												/>
											</picture>

											{screen.pins.map((pin, pIdx) => (
												<button
													key={pin.id}
													type="button"
													className="pin-btn"
													data-pin-btn={pin.id}
													aria-pressed={pIdx === 0 ? "true" : "false"}
													aria-label={`Spotlight ${pIdx + 1}: ${pin.label}`}
													style={{
														left: `${pin.xPct}%`,
														top: `${pin.yPct}%`,
													}}
												>
													{pIdx + 1}
												</button>
											))}
										</div>
									</div>

									<div className="pin-detail-list">
										{screen.pins.map((pin, pIdx) => (
											<div
												key={pin.id}
												className="pin-detail-card"
												data-pin-detail={pin.id}
												data-active={pIdx === 0 ? "true" : "false"}
											>
												<strong>
													{pIdx + 1}. {pin.label}
												</strong>
												<p className="prose">{pin.detail}</p>
											</div>
										))}
									</div>
								</div>
							</div>
						))}
					</div>
				</div>
			</div>
		</section>
	);
}
