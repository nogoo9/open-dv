import type React from "react";
import { DocumentShell } from "../components/Layout";
import { SITE_META, TOUR_CHAPTERS, YOUTUBE_VIDEO_ID } from "../data/site-data";
import { withBase } from "../lib/base-path";

export function TourPage({
	basePath,
}: {
	readonly basePath?: string | undefined;
}): React.JSX.Element {
	const hasYoutube = YOUTUBE_VIDEO_ID.trim().length > 0;
	const embedUrl = `https://www.youtube-nocookie.com/embed/${YOUTUBE_VIDEO_ID}?autoplay=1`;

	return (
		<DocumentShell
			title="open-dv Technical Walkthrough — 7-Step Workbench, DV Schema Explorer & Run Explorer"
			description="Watch the 2:45 Open-DV V2 walkthrough covering loopback open access, the 7-Step Guided ⇄ Observatory Workbench, L1 + V1–V8 CEGAR proofs, 4-View DV Schema Explorer, Run Explorer, 11 storage targets, and unified opendv CLI."
			canonicalPath="/tour/"
			activeRoute="tour"
			{...(basePath !== undefined ? { basePath } : {})}
		>
			<section
				id="tour-overview"
				className="band hero-band"
				tabIndex={-1}
				aria-labelledby="tour-page-title"
			>
				<div className="wrap">
					<p style={{ margin: "0 0 0.75rem" }}>
						<a href={withBase("/", basePath)}>← Back to Open-DV Overview</a>
					</p>
					<p className="eyebrow">
						TECHNICAL WALKTHROUGH · {SITE_META.version} · 1080P60 + CAPTIONS
					</p>
					<h1 id="tour-page-title" className="display-title" tabIndex={-1}>
						Open-DV {SITE_META.version} Technical Walkthrough
					</h1>
					<p className="lede">
						Complete 2:45 guided tour across loopback open access, the 7-Step
						Guided ⇄ Observatory Workbench, L1 + V1–V8 CEGAR proofs, 4-View DV
						Schema Explorer, Run Explorer, 11 storage targets spanning 5-Zone
						(Z0–Z4) + <code>information_mart</code>, and the unified{" "}
						<code>opendv</code> CLI.
					</p>

					<div className="video-frame">
						{hasYoutube ? (
							<div
								className="youtube-facade"
								data-youtube-facade={embedUrl}
								data-youtube-title="Open-DV Technical Walkthrough"
							>
								<img
									src={withBase("/video/opendv-tour-poster.webp", basePath)}
									width={1920}
									height={1080}
									alt="Open-DV 2:45 technical walkthrough poster"
									fetchPriority="high"
								/>
								<button
									type="button"
									className="btn btn-primary"
									data-youtube-trigger
								>
									▶ Play Walkthrough (YouTube)
								</button>
							</div>
						) : (
							<video
								id="tour-page-video"
								controls
								preload="metadata"
								width={1920}
								height={1080}
								poster={withBase("/video/opendv-tour-poster.webp", basePath)}
							>
								<source
									src={withBase(
										"/video/opendv-walkthrough-1080p60.mp4",
										basePath,
									)}
									type="video/mp4"
								/>
								<track
									kind="captions"
									srcLang="en"
									label="English"
									src={withBase("/video/opendv-tour-en.vtt", basePath)}
									default
								/>
							</video>
						)}
					</div>

					<div
						className="btn-row"
						style={{ marginBlockStart: "1rem", marginBlockEnd: 0 }}
					>
						<a
							className="btn btn-secondary btn-sm"
							href={withBase("/video/opendv-walkthrough-1080p60.mp4", basePath)}
							download
						>
							Download MP4 (1080p60)
						</a>
						<a
							className="btn btn-secondary btn-sm"
							href={withBase("/video/opendv-tour-en.vtt", basePath)}
							download
						>
							Captions (.vtt)
						</a>
						<a
							className="btn btn-secondary btn-sm"
							href={withBase("/video/opendv-tour-en.srt", basePath)}
							download
						>
							Subtitles (.srt)
						</a>
						<a
							className="btn btn-secondary btn-sm"
							href={withBase("/video/youtube-chapters.txt", basePath)}
							download
						>
							Chapter Notes (.txt)
						</a>
					</div>
				</div>
			</section>

			<section
				id="transcript"
				className="band"
				tabIndex={-1}
				aria-labelledby="transcript-heading"
			>
				<div className="wrap">
					<div className="section-head">
						<div className="section-head-copy">
							<h2 id="transcript-heading">
								Synchronized Chapters &amp; Transcript
							</h2>
							<p className="prose" aria-live="polite" data-transcript-count>
								Showing {TOUR_CHAPTERS.length} of {TOUR_CHAPTERS.length}{" "}
								chapters
							</p>
						</div>
						<div style={{ display: "grid", gap: "0.35rem" }}>
							<label htmlFor="transcript-search" style={{ fontWeight: 500 }}>
								Filter chapters &amp; transcript
							</label>
							<input
								id="transcript-search"
								type="search"
								className="search-input"
								placeholder="Search FIBO, 5-Zone, CEGAR, Horizon, DuckDB..."
								data-transcript-filter
							/>
						</div>
					</div>

					<ol className="chapter-list">
						{TOUR_CHAPTERS.map((ch) => (
							<li
								key={ch.id}
								className="card chapter-row"
								data-chapter-row={ch.id}
								data-start-sec={ch.startSec}
								data-end-sec={ch.endSec}
							>
								<div
									className="section-head"
									style={{ marginBlockEnd: "0.75rem" }}
								>
									<button
										type="button"
										className="chapter-btn"
										style={{ inlineSize: "auto" }}
										data-video-seek={ch.startSec}
										data-video-target="tour-page-video"
										data-chapter-id={ch.id}
										data-start-sec={ch.startSec}
										data-end-sec={ch.endSec}
									>
										<span className="chapter-time">
											0{ch.number} · {ch.timestamp}
										</span>
										<span className="chapter-title">{ch.title}</span>
									</button>

									<ul
										className="badge-row"
										style={{ listStyle: "none", margin: 0, padding: 0 }}
										aria-label="Chapter technical callouts"
									>
										{ch.callouts.map((callout) => (
											<li key={callout}>
												<code className="badge">{callout}</code>
											</li>
										))}
									</ul>
								</div>

								<p className="prose">{ch.transcript}</p>
							</li>
						))}
					</ol>
				</div>
			</section>
		</DocumentShell>
	);
}
