/**
 * Deterministic Human-Readable Text Snapshot Renderer for Explorer Bundles (GT-05).
 * Matches Python implementation in `tests/parity/golden_fixtures.py` (lines 98-151).
 */

export interface ExplorerBundleSnapshotInput {
	readonly display_name?: string | null | undefined;
	readonly displayName?: string | null | undefined;
	readonly project_id?: string | null | undefined;
	readonly projectId?: string | null | undefined;
	readonly spec_file?: string | null | undefined;
	readonly specFile?: string | null | undefined;
	readonly status_label?: string | null | undefined;
	readonly statusLabel?: string | null | undefined;
	readonly waves_count?: number | null | undefined;
	readonly wavesCount?: number | null | undefined;
	readonly rbac_role?: string | null | undefined;
	readonly rbacRole?: string | null | undefined;
	readonly gates_passed?: number | null | undefined;
	readonly gatesPassed?: number | null | undefined;
	readonly gates_total?: number | null | undefined;
	readonly gatesTotal?: number | null | undefined;
	readonly stages?:
		| readonly {
				readonly status_tag?: string | null | undefined;
				readonly statusTag?: string | null | undefined;
				readonly key?: string | null | undefined;
				readonly name?: string | null | undefined;
				readonly [key: string]: unknown;
		  }[]
		| undefined;
	readonly artifacts?:
		| Record<
				string,
				{
					readonly group?: string | null | undefined;
					readonly name?: string | null | undefined;
					readonly format?: string | null | undefined;
					readonly [key: string]: unknown;
				}
		  >
		| undefined;
	readonly [key: string]: unknown;
}

function padRight(str: string, width: number): string {
	return str.length >= width ? str : str.padEnd(width, " ");
}

/**
 * Render a deterministic, human-readable text snapshot of an Explorer bundle (GT-05).
 * Output matches `render_explorer_text_snapshot` from Python golden fixtures bit-for-bit.
 */
export function renderExplorerTextSnapshot(
	bundle: ExplorerBundleSnapshotInput,
): string {
	const project =
		bundle.display_name ||
		bundle.displayName ||
		bundle.project_id ||
		bundle.projectId ||
		"";

	const specFile = bundle.spec_file || bundle.specFile || "";
	const statusLabel = bundle.status_label || bundle.statusLabel || "";

	const wavesCount =
		bundle.waves_count !== undefined && bundle.waves_count !== null
			? bundle.waves_count
			: (bundle.wavesCount ?? "");

	const rbacRole = bundle.rbac_role || bundle.rbacRole || "";

	const gatesPassed =
		bundle.gates_passed !== undefined && bundle.gates_passed !== null
			? bundle.gates_passed
			: (bundle.gatesPassed ?? 0);

	const gatesTotal =
		bundle.gates_total !== undefined && bundle.gates_total !== null
			? bundle.gates_total
			: (bundle.gatesTotal ?? 6);

	const stages = bundle.stages ?? [];

	const isHold =
		statusLabel === "PAUSED_HOLD" ||
		stages.some(
			(s) =>
				s.status_tag === "HOLD" ||
				s.statusTag === "HOLD" ||
				s.status_tag === "ASK-USER" ||
				s.statusTag === "ASK-USER",
		);

	const lines: string[] = [
		"============================================================",
		"OPEN-DV PIPELINE EXPLORER SNAPSHOT",
		"============================================================",
		`Project:     ${project}`,
		"Run ID:      <NORMALIZED_RUN_ID>",
		`Spec File:   ${specFile}`,
		`Status:      ${statusLabel}`,
		`Waves:       ${wavesCount}`,
		`RBAC Role:   ${rbacRole}`,
		`Gates:       ${gatesPassed}/${gatesTotal} passed`,
	];

	if (isHold) {
		lines.push(
			"------------------------------------------------------------",
			"⚠️ STEWARD HOLD ACTIVE: Unstructured extraction requires review",
			"------------------------------------------------------------",
		);
	}

	lines.push(
		"------------------------------------------------------------",
		"STAGES",
		"------------------------------------------------------------",
	);

	for (const s of stages) {
		const stag = s.status_tag || s.statusTag || "PENDING";
		const key = s.key || "";
		const name = s.name || "";
		lines.push(`[${padRight(stag, 14)}] ${padRight(key, 20)} ${name}`);
	}

	lines.push(
		"------------------------------------------------------------",
		"ARTIFACTS INDEX",
		"------------------------------------------------------------",
	);

	const artifacts = bundle.artifacts ?? {};
	const sortedArtIds = Object.keys(artifacts).sort();

	for (const artId of sortedArtIds) {
		const art = artifacts[artId] ?? {};
		const grp = art.group || "";
		const name = art.name || "";
		const fmt = art.format || "";
		lines.push(
			`* ${padRight(artId, 15)} [${padRight(grp, 12)}] [${padRight(fmt, 5)}] ${name}`,
		);
	}

	lines.push("============================================================\n");
	return lines.join("\n");
}
