import type React from "react";
import { DOC_REFS, SITE_META } from "../data/site-data";
import { withBase } from "../lib/base-path";

export interface LayoutProps {
	readonly title: string;
	readonly description: string;
	readonly canonicalPath: string;
	readonly activeRoute: "home" | "tour";
	readonly basePath?: string | undefined;
	readonly children?: React.ReactNode | undefined;
}

interface NavLinkItem {
	readonly label: string;
	readonly target: string;
	readonly isRoute?: boolean;
}

const SECTION_LINKS: readonly NavLinkItem[] = [
	{ label: "Why Open-DV", target: "#why" },
	{ label: "How It Works", target: "#how" },
	{ label: "6-Gate Verifier", target: "#verifier" },
	{ label: "Workbench", target: "#workbench" },
	{ label: "Ecosystem", target: "#ecosystem" },
	{ label: "Quick Start", target: "#quickstart" },
	{ label: "Video Tour", target: "/tour/", isRoute: true },
];

const SITE_ORIGIN = "https://nogoo9.github.io";

function SpikeMark({ color }: { readonly color: string }): React.JSX.Element {
	return (
		<svg
			className="brand-mark"
			width="16"
			height="16"
			viewBox="0 0 24 24"
			fill="none"
			aria-hidden="true"
		>
			<g stroke={color} strokeWidth="2.1" strokeLinecap="round">
				<line x1="12" y1="2.5" x2="12" y2="21.5" />
				<line x1="2.5" y1="12" x2="21.5" y2="12" />
				<line x1="5.3" y1="5.3" x2="18.7" y2="18.7" />
				<line x1="18.7" y1="5.3" x2="5.3" y2="18.7" />
			</g>
		</svg>
	);
}

function buildCanonicalUrl(canonicalPath: string, basePath?: string): string {
	const resolved = withBase(canonicalPath, basePath);
	return `${SITE_ORIGIN}${resolved.startsWith("/") ? resolved : `/${resolved}`}`;
}

export function DocumentShell(props: LayoutProps): React.JSX.Element {
	const { title, description, canonicalPath, activeRoute, basePath, children } =
		props;

	const resolveNavHref = (item: NavLinkItem): string => {
		if (item.isRoute) return withBase(item.target, basePath);
		return activeRoute === "tour"
			? `${withBase("/", basePath)}${item.target}`
			: item.target;
	};

	const quickstartHref =
		activeRoute === "tour"
			? `${withBase("/", basePath)}#quickstart`
			: "#quickstart";
	const canonicalUrl = buildCanonicalUrl(canonicalPath, basePath);
	const posterUrl = `${SITE_ORIGIN}${withBase("/video/opendv-tour-poster.webp", basePath)}`;

	return (
		<html lang="en">
			<head>
				<meta charSet="utf-8" />
				<meta name="viewport" content="width=device-width, initial-scale=1" />
				<meta name="color-scheme" content="light" />
				<meta name="referrer" content="no-referrer" />
				<title>{title}</title>
				<meta name="description" content={description} />
				<link rel="canonical" href={canonicalUrl} />
				<meta property="og:type" content="website" />
				<meta property="og:title" content={title} />
				<meta property="og:description" content={description} />
				<meta property="og:url" content={canonicalUrl} />
				<meta property="og:image" content={posterUrl} />
				<link
					rel="icon"
					type="image/svg+xml"
					href={withBase("/favicon.svg", basePath)}
				/>
				<link
					rel="preload"
					href={withBase("/fonts/instrument-serif-latin.woff2", basePath)}
					as="font"
					type="font/woff2"
					crossOrigin="anonymous"
				/>
				<link
					rel="preload"
					href={withBase("/fonts/dm-sans-latin.woff2", basePath)}
					as="font"
					type="font/woff2"
					crossOrigin="anonymous"
				/>
				<link rel="stylesheet" href={withBase("/assets/site.css", basePath)} />
			</head>
			<body>
				<a className="skip-link" href="#main-content">
					Skip to main content
				</a>

				<header className="site-header" data-site-header>
					<div className="header-inner">
						<a className="site-brand" href={withBase("/", basePath)}>
							<SpikeMark color="#141413" />
							<span className="brand-name">{SITE_META.name}</span>
							<span className="brand-version">{SITE_META.version}</span>
						</a>

						<nav className="site-nav" aria-label="Primary">
							<button
								type="button"
								className="menu-button"
								popoverTarget="site-menu"
								popoverTargetAction="toggle"
								aria-controls="site-menu"
							>
								<svg
									width="16"
									height="16"
									viewBox="0 0 16 16"
									fill="none"
									aria-hidden="true"
								>
									<path
										d="M2.5 4h11M2.5 8h11M2.5 12h11"
										stroke="currentColor"
										strokeWidth="1.5"
										strokeLinecap="round"
									/>
								</svg>
								<span>Menu</span>
							</button>

							<ul id="site-menu" className="site-menu" popover="auto">
								{SECTION_LINKS.map((item) => {
									const isCurrentTour =
										item.isRoute === true && activeRoute === "tour";
									return (
										<li key={item.target}>
											<a
												href={resolveNavHref(item)}
												{...(isCurrentTour ? { "aria-current": "page" } : {})}
											>
												{item.label}
											</a>
										</li>
									);
								})}
							</ul>
						</nav>

						<div className="header-actions">
							<a
								className="header-github"
								href={SITE_META.repoUrl}
								target="_blank"
								rel="noopener noreferrer"
							>
								GitHub
								<span className="visually-hidden"> (opens in new tab)</span>
							</a>
							<a className="header-cta" href={quickstartHref}>
								Quick Start
							</a>
						</div>
					</div>
				</header>

				<main id="main-content" tabIndex={-1}>
					{children}
				</main>

				<footer className="site-footer">
					<div className="wrap">
						<div className="footer-grid">
							<div className="footer-meta">
								<a
									className="site-brand footer-brand"
									href={withBase("/", basePath)}
								>
									<SpikeMark color="#faf9f5" />
									<span className="brand-name">{SITE_META.name}</span>
									<span className="brand-version">{SITE_META.version}</span>
								</a>
								<p className="footer-copy">
									Agent-first 6-Step Wizard, 4-View DV Schema Explorer, 6-gate{" "}
									<code>axi-dv</code> formal verification, and deterministic
									multi-dialect 5-Zone (Z0–Z4) + <code>information_mart</code>{" "}
									SQL compilation.
								</p>
							</div>

							<div>
								<h2 className="footer-col-title">Platform</h2>
								<ul className="footer-links">
									<li>
										<a href={withBase("/", basePath)}>Home</a>
									</li>
									<li>
										<a
											href={resolveNavHref({
												label: "Workbench",
												target: "#workbench",
											})}
										>
											12-Screen Tour
										</a>
									</li>
									<li>
										<a href={withBase("/tour/", basePath)}>Video Tour</a>
									</li>
									<li>
										<a href={quickstartHref}>Quick Start</a>
									</li>
								</ul>
							</div>

							<div>
								<h2 className="footer-col-title">Specifications</h2>
								<ul className="footer-links">
									{DOC_REFS.slice(0, 4).map((doc) => (
										<li key={doc.id}>
											<a
												href={`${SITE_META.docsBaseUrl}/${doc.path}`}
												target="_blank"
												rel="noopener noreferrer"
											>
												{doc.id}
											</a>
										</li>
									))}
								</ul>
							</div>

							<div>
								<h2 className="footer-col-title">Runtime &amp; Parity</h2>
								<ul className="footer-links">
									<li>
										<a
											href={SITE_META.repoUrl}
											target="_blank"
											rel="noopener noreferrer"
										>
											GitHub Repository
										</a>
									</li>
									<li>
										<a
											href={`${SITE_META.docsBaseUrl}/16-human-and-agent-usage-guide.md`}
											target="_blank"
											rel="noopener noreferrer"
										>
											Dual-CLI (opendv / opendvctl)
										</a>
									</li>
									<li>
										<a
											href={resolveNavHref({
												label: "Ecosystem",
												target: "#ecosystem",
											})}
										>
											Loopback &amp; Remote Guard
										</a>
									</li>
								</ul>
							</div>
						</div>

						<div className="footer-bottom">
							<span>
								{SITE_META.name} {SITE_META.version} · {SITE_META.license}{" "}
								License
							</span>
							<span>
								Loopback Studio: <code>http://127.0.0.1:8420</code> ·{" "}
								<code>--allow-remote-unauthenticated</code> guard
							</span>
						</div>
					</div>
				</footer>

				<dialog
					id="media-lightbox"
					className="media-lightbox"
					{...({ closedby: "any" } as Record<string, string>)}
					aria-labelledby="lightbox-title"
				>
					<div className="lightbox-bar">
						<h2
							id="lightbox-title"
							className="lightbox-caption"
							data-lightbox-caption
						>
							Lightbox Preview
						</h2>
						<form method="dialog">
							<button
								type="submit"
								className="lightbox-close"
								data-lightbox-close
							>
								✕ Close
							</button>
						</form>
					</div>
					<div className="lightbox-stage" data-lightbox-stage />
				</dialog>

				<script type="module" src={withBase("/assets/hydrate.js", basePath)} />
			</body>
		</html>
	);
}
