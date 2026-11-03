/**
 * Design Tokens, Glyphs, Status Pills & Typography Constants
 * for the Open-DV 3-Level Pipeline Explorer Workbench (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { StatusTag, VizKind } from "@opendv/protocol-ts";
import type { CSSProperties } from "react";

export const EXPLORER_FONTS = {
	sans: "'IBM Plex Sans', system-ui, -apple-system, sans-serif",
	mono: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
} as const;

export const EXPLORER_COLORS = {
	inkPrimary: "#16181D",
	inkBody: "#3B414C",
	inkMuted: "#545A66",
	inkLabel: "#6B7280",
	bgPage: "#F6F7F8",
	bgSurface: "#FFFFFF",
	bgSubtle: "#F2F3F5",
	bgWarmStrip: "#FAF9F6",
	borderLine: "#E3E5E8",
	borderRail: "#D4D7DC",
	borderDashed: "#C9CDD3",
	borderSelected: "#1D4ED8",
	glyphControl: "#B45309",
	glyphData: "#1D4ED8",
	glyphComponent: "#4B5563",
	calloutBg: "#FFF9F0",
	calloutBorder: "#F3D9B1",
	calloutText: "#8A3B07",
	codeBg: "#16181D",
	codeFg: "#E6E8EB",
	spanTierU1Bg: "#DBEAFE",
	spanTierU1Ring: "0 0 0 1px #93C5FD",
	spanTierU1Swatch: "#BFDBFE",
	spanTierU3Bg: "#FFEDD5",
	spanTierU3Ring: "0 0 0 1px #FDBA74",
	spanTierU3Swatch: "#FED7AA",
} as const;

export interface GlyphStyleSpec {
	bg: string;
	border: string;
	radius: string;
	clip: string;
}

export const EXPLORER_GLYPHS: Record<
	"control" | "data" | "setting" | "component",
	GlyphStyleSpec
> = {
	control: {
		bg: "#B45309",
		border: "none",
		radius: "1px",
		clip: "polygon(50% 0,100% 50%,50% 100%,0 50%)",
	},
	data: {
		bg: "#1D4ED8",
		border: "none",
		radius: "2px",
		clip: "none",
	},
	setting: {
		bg: "transparent",
		border: "1.5px solid #1D4ED8",
		radius: "2px",
		clip: "none",
	},
	component: {
		bg: "#4B5563",
		border: "none",
		radius: "999px",
		clip: "none",
	},
};

export const EXPLORER_STATUS_TAGS: Record<
	StatusTag,
	{ bg: string; fg: string }
> = {
	PASS: { bg: "#EEF0F2", fg: "#16181D" },
	AUTO_REPAIRED: { bg: "#FFF4E5", fg: "#8A3B07" },
	"ASK-USER": { bg: "#FFF4E5", fg: "#8A3B07" },
	"ASK-USER → resolved": { bg: "#FFF4E5", fg: "#8A3B07" },
	"REJECT-WITH-TRACE": { bg: "#FDECEA", fg: "#8F1D12" },
	"AUTO-FIX": { bg: "#EEF2FF", fg: "#1E3A8A" },
};

export const EXPLORER_VIZ_LABELS: Record<VizKind, string> = {
	ontology: "Ontology graph (Graph A)",
	dv: "Data Vault 2.0 topology",
	table: "Tabular view",
	kv: "Key / value view",
	checks: "Verification checks",
	events: "Event timeline",
	doc: "Document + span highlights",
	code: "Structured code & outline",
};

export function getStatusTagStyle(tag: StatusTag | string): {
	bg: string;
	fg: string;
} {
	return (
		EXPLORER_STATUS_TAGS[tag as StatusTag] ?? EXPLORER_STATUS_TAGS.PASS
	);
}

export function getGlyphCssStyle(
	glyph: GlyphStyleSpec,
	size = 10,
): CSSProperties {
	return {
		width: `${size}px`,
		height: `${size}px`,
		flexShrink: 0,
		display: "inline-block",
		background: glyph.bg,
		border: glyph.border,
		borderRadius: glyph.radius,
		clipPath: glyph.clip,
	};
}
