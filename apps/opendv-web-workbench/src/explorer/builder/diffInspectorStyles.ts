import type { BlueprintEvolutionDiffDTO } from "@opendv/protocol-ts";
import { EXPLORER_COLORS as C, EXPLORER_FONTS as F } from "../design-tokens";

export const S_BOX = {
	backgroundColor: C.bgPage,
	borderRadius: 6,
	border: `1px solid ${C.borderLine}`,
	padding: 10,
	marginBottom: 10,
} as const;

export const S_TITLE = {
	fontSize: 13,
	fontWeight: 700,
	color: C.inkPrimary,
	marginBottom: 6,
	display: "flex",
	alignItems: "center",
	gap: 6,
} as const;

export const S_TAG = (bg: string, fg: string, border: string) =>
	({
		padding: "1px 6px",
		borderRadius: 10,
		fontSize: 11,
		fontFamily: F.mono,
		fontWeight: 600,
		backgroundColor: bg,
		color: fg,
		border: `1px solid ${border}`,
		display: "inline-flex",
		alignItems: "center",
	}) as const;

export const S_BTN = (primary: boolean) =>
	({
		padding: "6px 12px",
		borderRadius: 5,
		fontSize: 12,
		fontWeight: 600,
		border: primary ? "none" : `1px solid ${C.borderLine}`,
		backgroundColor: primary ? C.borderSelected : C.bgSurface,
		color: primary ? "#FFF" : C.inkPrimary,
		cursor: "pointer",
		display: "flex",
		alignItems: "center",
		gap: 6,
	}) as const;

export const EMPTY_DIFF: BlueprintEvolutionDiffDTO = {
	added_hubs: [],
	added_links: [],
	added_satellites: [],
	widened_columns: [],
	companion_satellites: [],
	ontology_deltas_applied: [],
};
