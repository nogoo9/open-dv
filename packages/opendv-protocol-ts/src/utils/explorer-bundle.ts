/**
 * Pure Utility and Lineage Functions for Explorer Bundles
 * (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { ProjectDTO, ProjectRunDTO } from "../types/api-dtos";
import type { RbacRole } from "../types/common";
import type { VaultObservabilityEventDTO } from "../types/observability-event";
import type {
	Artifact,
	ExplorerBundleDTO,
	Stage,
	StatusTag,
	WaveFilterId,
} from "../types/workbench-explorer";
import {
	CANONICAL_STAGE_DEFINITIONS,
	EXPLORER_PHASES,
} from "./stage-definitions";

export { CANONICAL_STAGE_DEFINITIONS, EXPLORER_PHASES };

/**
 * Creates an empty, valid ExplorerBundleDTO with canonical stages and phases.
 */
export function createEmptyExplorerBundle(): ExplorerBundleDTO {
	return {
		projectId: "",
		displayName: "No active run",
		runId: "",
		specFile: "",
		wavesCount: 0,
		waves: [],
		statusLabel: "Idle",
		gatesPassed: 0,
		gatesTotal: 6,
		rbacRole: "INTERNAL_MODELER",
		hasInitialContract: false,
		phases: [...EXPLORER_PHASES],
		stages: CANONICAL_STAGE_DEFINITIONS.map((s) => ({
			...s,
			steps: s.steps.map((st) => ({
				...st,
				reads: [...st.reads],
				writes: [...st.writes],
			})),
			comps: s.comps.map((c) => [...c] as [string, string, string, string]),
			ins: [...s.ins],
			outs: [...s.outs],
		})),
		artifacts: {},
		settingIds: [],
		inputIds: [],
		outputIds: [],
	};
}

/**
 * Dynamically build or hydrate an `ExplorerBundleDTO` from
 * `ProjectDTO`, `ProjectRunDTO`, and `VaultObservabilityEventDTO[]`.
 */
export function buildExplorerBundleFromObservabilityEvents(
	project?: Partial<ProjectDTO> | undefined,
	run?: Partial<ProjectRunDTO> | undefined,
	events: readonly VaultObservabilityEventDTO[] = [],
	rbacRole: RbacRole = "INTERNAL_MODELER",
	baseBundle?: Partial<ExplorerBundleDTO> | undefined,
): ExplorerBundleDTO {
	const bundle = createEmptyExplorerBundle();
	const projectId = project?.project_id ?? baseBundle?.projectId ?? bundle.projectId;
	const runId = run?.run_id ?? baseBundle?.runId ?? bundle.runId;

	const clonedArtifacts: Record<string, Artifact> = {};
	if (baseBundle?.artifacts) {
		for (const [k, v] of Object.entries(baseBundle.artifacts)) {
			clonedArtifacts[k] = {
				...v,
				waves: v.waves ? [...v.waves] : undefined,
				cols: v.cols ? [...v.cols] : undefined,
				rows: v.rows ? v.rows.map((r) => [...r]) : undefined,
				kv: v.kv ? v.kv.map((pair) => [...pair] as [string, string]) : undefined,
				checks: v.checks
					? v.checks.map((c) => [...c] as [string, string, StatusTag])
					: undefined,
				events: v.events
					? v.events.map((e) => [...e] as [string, string, StatusTag])
					: undefined,
			};
		}
	}

	if (events.length > 0) {
		const mappedEvents: [string, string, StatusTag][] = events.map((ev) => {
			const shortStage = ev.stage_id.slice(0, 8);
			const statusTag: StatusTag =
				ev.status === "PASS" ||
				ev.status === "AUTO_REPAIRED" ||
				ev.status === "ASK-USER" ||
				ev.status === "REJECT-WITH-TRACE" ||
				ev.status === "AUTO-FIX"
					? ev.status
					: "PASS";
			return [shortStage, ev.layer1_story_banner?.headline ?? "", statusTag];
		});

		const prevLedger = clonedArtifacts.ledger;
		clonedArtifacts.ledger = {
			id: "ledger",
			name: "vault-ledger.jsonl",
			short: `${events.length} events · RBAC-redacted stream`,
			group: "Final output",
			type: "JSONL",
			fmt: "JSONL",
			viz: "events",
			desc: "Observability audit trail and event ledger.",
			raw: events
				.map((ev, idx) =>
					JSON.stringify({
						seq: idx + 1,
						event_id: ev.event_id,
						stage: ev.stage_id,
						status: ev.status,
						headline: ev.layer1_story_banner?.headline ?? "",
					}),
				)
				.join("\n"),
			events: mappedEvents,
			waves: prevLedger?.waves ? [...prevLedger.waves] : undefined,
		};
	}

	return {
		...bundle,
		...baseBundle,
		projectId,
		displayName: project?.project_id || baseBundle?.displayName || bundle.displayName,
		runId,
		rbacRole,
		artifacts: clonedArtifacts,
	};
}

/**
 * Compute which stage(s) produced a given artifact ID.
 */
export function getArtifactProducedBy(
	stages: readonly Stage[],
	artifactId: string,
): string {
	const producers: string[] = [];
	for (const stage of stages) {
		if (stage.outs.includes(artifactId)) {
			producers.push(stage.key.slice(0, 8));
		}
	}
	return producers.join(", ") || "— (provided)";
}

/**
 * Compute which stage(s) consumed a given artifact ID.
 */
export function getArtifactConsumedBy(
	stages: readonly Stage[],
	artifactId: string,
): string {
	const consumers: string[] = [];
	for (const stage of stages) {
		if (stage.ins.includes(artifactId)) {
			consumers.push(stage.key.slice(0, 8));
		}
	}
	return consumers.join(", ") || "— (final)";
}

/**
 * Filter input artifact IDs by active wave (`all` | `W0` | `W1` | `W2` | `W3`).
 */
export function filterArtifactIdsByWave(
	artifactIds: readonly string[],
	artifacts: Readonly<Record<string, Artifact>>,
	wave: WaveFilterId,
): string[] {
	if (wave === "all") {
		return [...artifactIds];
	}
	return artifactIds.filter((id) => {
		const art = artifacts[id];
		if (!art || !art.waves || art.waves.length === 0) {
			return true;
		}
		return art.waves.includes(wave);
	});
}
