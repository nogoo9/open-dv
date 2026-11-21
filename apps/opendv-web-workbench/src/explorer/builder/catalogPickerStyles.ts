/**
 * Claude DESIGN.md styles for CatalogTablePickerModal.
 */

import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";

const C = CLAUDE_TOKENS.colors;
const row = { display: "flex", alignItems: "center" } as const;
const between = { ...row, justifyContent: "space-between" } as const;
const mono = { fontFamily: EXPLORER_FONTS.mono } as const;
const bLine = `1px solid ${C.hairline}`;
const inputBase = {
	padding: "4px 7px",
	borderRadius: 4,
	border: `1px solid ${C.hairlineStrong}`,
	backgroundColor: C.canvas,
	color: C.ink,
	fontSize: 12,
} as const;
const monoInput = { ...inputBase, ...mono } as const;

export const PICKER_STYLES = {
	row,
	between,
	input: inputBase,
	monoInput,
	btn: {
		border: `1px solid ${C.hairlineStrong}`,
		borderRadius: 6,
		backgroundColor: C.canvas,
		color: C.ink,
		padding: "3px 8px",
		fontSize: 11,
		cursor: "pointer",
	} as const,
	pk: {
		backgroundColor: C.surfaceCreamStrong,
		color: C.primaryActive,
		padding: "1px 4px",
		borderRadius: 3,
		fontWeight: 700,
		marginRight: 4,
	} as const,
	fk: {
		backgroundColor: C.surfaceCard,
		color: C.ink,
		border: `1px solid ${C.hairlineStrong}`,
		padding: "1px 4px",
		borderRadius: 3,
		fontWeight: 600,
	} as const,
	tag: {
		backgroundColor: C.surfaceSoft,
		color: C.primaryActive,
		border: `1px solid ${C.hairline}`,
		padding: "1px 4px",
		borderRadius: 3,
		fontWeight: 600,
		marginRight: 4,
	} as const,
	dialog: {
		margin: "auto",
		padding: 0,
		border: "none",
		background: "transparent",
		width: "min(760px, 94vw)",
		maxWidth: "760px",
		maxHeight: "88vh",
		overflow: "visible",
	} as const,
	panel: {
		width: "100%",
		maxWidth: 760,
		maxHeight: "88vh",
		backgroundColor: C.canvas,
		color: C.ink,
		borderRadius: 10,
		border: `1px solid ${C.hairlineStrong}`,
		display: "flex",
		flexDirection: "column" as const,
		overflow: "hidden" as const,
		boxShadow: "0 18px 48px rgba(20, 20, 19, 0.24)",
	},
	header: {
		...between,
		padding: "12px 16px",
		borderBottom: bLine,
		backgroundColor: C.surfaceSoft,
	} as const,
	subbar: {
		padding: "8px 16px",
		backgroundColor: C.surfaceSoft,
		borderBottom: bLine,
		display: "flex",
		flexDirection: "column" as const,
		gap: 6,
	},
	body: {
		flex: 1,
		overflowY: "auto" as const,
		padding: "12px 16px",
		display: "flex",
		flexDirection: "column" as const,
		gap: 8,
		backgroundColor: C.canvas,
	},
	footer: {
		...between,
		padding: "10px 16px",
		borderTop: bLine,
		backgroundColor: C.surfaceSoft,
	} as const,
	tblHdr: {
		...row,
		gap: 8,
		padding: "6px 10px",
		backgroundColor: C.surfaceCard,
		borderRadius: 6,
		border: bLine,
		fontSize: 12,
		fontWeight: 600,
	} as const,
	tbl: { width: "100%", fontSize: 11, borderCollapse: "collapse" as const },
	td: { padding: 3 },
	tdName: { ...mono, padding: 3, fontWeight: 600, color: C.ink },
	tdType: { ...mono, padding: 3, color: C.muted },
	colRow: { borderTop: `1px solid ${C.hairlineSoft}` },
	nsBadge: { ...mono, color: C.primaryActive },
	closeBtn: {
		background: "none",
		border: "none",
		fontSize: 16,
		cursor: "pointer",
		color: C.muted,
	},
	rowEst: {
		fontSize: 11,
		color: C.muted,
		backgroundColor: C.surfaceSoft,
		padding: "1px 5px",
		borderRadius: 3,
	},
	colPanel: {
		borderTop: bLine,
		padding: "6px 10px",
		backgroundColor: C.canvas,
	},
	loading: {
		textAlign: "center" as const,
		padding: 16,
		color: C.muted,
		fontSize: 12,
	},
	err: {
		padding: "8px 12px",
		backgroundColor: C.surfaceSoft,
		color: C.error,
		border: `1px solid ${C.error}`,
		borderRadius: 6,
		fontSize: 12,
		marginBottom: 8,
	},
};

export const pickerBadgeStyle = (isSql: boolean) => ({
	...mono,
	padding: "2px 7px",
	borderRadius: 6,
	fontSize: 11,
	fontWeight: 700,
	backgroundColor: isSql ? C.surfaceCreamStrong : C.surfaceCard,
	color: isSql ? C.primaryActive : C.ink,
	border: `1px solid ${C.hairlineStrong}`,
});

export const pickerApplyBtnStyle = (disabled: boolean) => ({
	padding: "6px 14px",
	borderRadius: 6,
	border: "none",
	backgroundColor: disabled ? C.primaryDisabled : C.primary,
	color: disabled ? C.muted : C.onPrimary,
	fontWeight: 600,
	fontSize: 12,
	cursor: disabled ? "not-allowed" : "pointer",
});

export const pickerRowWrapStyle = (checked: boolean) => ({
	borderRadius: 6,
	border: `1px solid ${checked ? C.primary : C.hairline}`,
	backgroundColor: checked ? C.surfaceSoft : C.canvas,
});
