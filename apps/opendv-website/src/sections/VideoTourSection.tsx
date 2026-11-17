import type React from "react";
import { TOUR_CHAPTERS, YOUTUBE_VIDEO_ID } from "../data/site-data";
import { withBase } from "../lib/base-path";

export function VideoTourSection({
	basePath,
}: {
	readonly basePath?: string | undefined;
}): React.JSX.Element {
	const hasYoutube = YOUTUBE_VIDEO_ID.trim().length > 0;
	const embedUrl = `https://www.youtube-nocookie.com/embed/${YOUTUBE_VIDEO_ID}?autoplay=1`;

	return (
		<section
			id="tour"
			className="band"
			tabIndex={-1}
			aria-labelledby="tour-heading"
		>
			<div className="wrap">
				<div className="section-head">
					<div className="section-head-copy">
						<p className="eyebrow">VIDEO WALKTHROUGH · 2:45 END-TO-END</p>
						<h2 id="tour-heading">
							See Ontology Studio, 6-Gate proofs, and Databricks UC drift in
							action.
						</h2>
					</div>
					<p className="section-intro">
						Jump directly to any of the six chapters or open the full searchable
						transcript page with downloadable MP4 and WebVTT captions.
					</p>
				</div>

				<div className="feature-split">
					<div className="video-frame">
						{hasYoutube ? (
							<div
								className="youtube-facade"
								data-youtube-facade={embedUrl}
								data-youtube-title="Open-DV v6.0 Technical Walkthrough"
							>
								<img
									src={withBase("/video/opendv-tour-poster.webp", basePath)}
									width={1920}
									height={1080}
									alt="Open-DV v6.0 2:45 walkthrough poster"
									loading="lazy"
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
								id="home-tour-video"
								controls
								preload="none"
								width={1920}
								height={1080}
								poster={withBase("/video/opendv-tour-poster.webp", basePath)}
								{...({ loading: "lazy" } as Record<string, unknown>)}
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

					<aside className="card" aria-label="Video chapters">
						<div className="badge-row" style={{ marginBlockEnd: "0.85rem" }}>
							<span className="badge badge-gold">02:45</span>
							<h3 style={{ margin: 0 }}>Walkthrough Chapters</h3>
						</div>

						<ol className="chapter-list">
							{TOUR_CHAPTERS.map((ch) => (
								<li key={ch.id}>
									<button
										type="button"
										className="chapter-btn"
										data-video-seek={ch.startSec}
										data-video-target="home-tour-video"
										data-chapter-id={ch.id}
										data-start-sec={ch.startSec}
										data-end-sec={ch.endSec}
									>
										<span className="chapter-time">{ch.timestamp}</span>
										<span className="chapter-title">{ch.title}</span>
									</button>
								</li>
							))}
						</ol>

						<div style={{ marginBlockStart: "1rem" }}>
							<a
								className="btn btn-secondary"
								href={withBase("/tour/", basePath)}
							>
								Open Full Interactive Transcript →
							</a>
						</div>
					</aside>
				</div>
			</div>
		</section>
	);
}
