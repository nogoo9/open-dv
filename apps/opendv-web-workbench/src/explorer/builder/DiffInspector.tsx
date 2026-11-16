/** Visual Evolution Diff & Post-Run Inspector */
import type {
	BlueprintEvolutionDiffDTO,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import { useEffect, useState } from "react";
import { parse as parseYaml } from "yaml";
import { EXPLORER_COLORS as C, EXPLORER_FONTS as F } from "../design-tokens";
import { serializeSpecToYaml } from "./wizardSpecMapper";
import { downloadBlob, resolveHubKeyTier } from "./specBuilderUtils";
import {
	EMPTY_DIFF,
	S_BOX,
	S_BTN,
	S_TAG,
	S_TITLE,
} from "./diffInspectorStyles";
import { executeSyncOntologyDelta } from "./diffInspectorSync";

export interface DiffInspectorProps {
	runId?: string | undefined;
	blueprint?: VaultBlueprintDTO | null | undefined;
	diff?: BlueprintEvolutionDiffDTO | null | undefined;
	contractContent?: string | undefined;
	blueprintContent?: string | undefined;
	duckdbSql?: string | undefined;
	databricksSql?: string | undefined;
	gateScorecard?: Record<string, unknown> | null | undefined;
	activeSessionId?: string | null | undefined;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	onEvolveSchema?: ((newBlueprint: VaultBlueprintDTO) => void) | undefined;
	onSyncOntologyDelta?: ((sessionId: string) => Promise<void>) | undefined;
}

export function DiffInspector({
	runId = "run-main",
	blueprint,
	diff,
	contractContent,
	blueprintContent,
	duckdbSql,
	databricksSql,
	gateScorecard,
	activeSessionId,
	apiUrl = "/api/v1",
	fetchImpl = fetch,
	onEvolveSchema,
	onSyncOntologyDelta,
}: DiffInspectorProps) {
	const [isSyncing, setIsSyncing] = useState(false);
	const [syncMsg, setSyncMsg] = useState<string | null>(null);
	const [syncError, setSyncError] = useState<string | null>(null);
	const [resolvedBp, setResolvedBp] = useState<VaultBlueprintDTO | null>(blueprint ?? null);

	useEffect(() => {
		if (blueprint) {
			const b = { ...blueprint };
			if (!b.contract && contractContent) {
				try {
					b.contract = parseYaml(contractContent) as any;
				} catch {}
			}
			setResolvedBp(b);
		} else {
			setResolvedBp(null);
		}
	}, [blueprint, contractContent]);

	const d = diff ?? EMPTY_DIFF;
	const bp = resolvedBp;

	const rawBp = blueprintContent || (bp ? serializeSpecToYaml(bp) : "");
	const rawContract =
		contractContent || (bp?.contract ? serializeSpecToYaml(bp.contract) : "");
	const rawDuck = duckdbSql || "";
	const rawDbx = databricksSql || "";

	const sc = (gateScorecard ?? {}) as Record<string, unknown>;
	const staticGatesPassed =
		gateScorecard != null
			? Boolean(
					sc.gates_passed ??
						sc.overall_passed ??
						sc.passed ??
						sc.axi_passed ??
						sc.static_passed ??
						(Boolean(sc.gate_0_profile_drift) &&
							sc.gate_0_profile_drift !== "REJECT-WITH-TRACE" &&
							sc.gate_1_contract_grain !== "REJECT-WITH-TRACE" &&
							sc.gate_2_sqlglot_ast !== "REJECT-WITH-TRACE" &&
							sc.gate_3_networkx_dag !== "REJECT-WITH-TRACE" &&
							sc.gate_4_yagni_cost_guard !== "REJECT-WITH-TRACE"),
				)
			: false;
	const shadowSandboxPassed =
		gateScorecard != null
			? Boolean(
					sc.shadow_passed ??
						sc.sandbox_passed ??
						sc.four_zone_passed ??
						(sc.gate_5_duckdb_sandbox === "PASS" || sc.gate_5_duckdb_sandbox === "AUTO-FIX"
							? true
							: Boolean((sc.sandbox_summary as Record<string, unknown>)?.passed)),
				)
			: false;

	const handleSyncDelta = async () => {
		const sId = activeSessionId;
		if (!sId) {
			setSyncError("No active Studio session ID available to sync deltas");
			return;
		}
		setIsSyncing(true);
		setSyncMsg(null);
		setSyncError(null);
		try {
			const res = await executeSyncOntologyDelta({
				sessionId: sId,
				apiUrl,
				fetchImpl,
				operations: (d.ontology_deltas_applied ?? []) as Array<Record<string, unknown>>,
				onSyncOntologyDelta,
			});
			if (res.success) {
				setSyncMsg(res.message || `Ontology Deltas synced to Studio Session ${sId}!`);
			} else {
				setSyncError(res.error || "Sync failed");
			}
		} catch (err) {
			setSyncError(err instanceof Error ? err.message : "Sync failed");
		} finally {
			setIsSyncing(false);
		}
	};

	return (
		<div data-testid="diff-inspector" style={{ backgroundColor: C.bgSurface, borderRadius: 8, border: `1px solid ${C.borderLine}`, padding: 16, marginTop: 16 }}>
			<div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
				<div>
					<h3 style={{ fontSize: 14, fontWeight: 700, margin: 0, display: "flex", alignItems: "center", gap: 6 }}>
						<span>Data Vault 2.0 Evolution Diff</span>
						<span style={S_TAG("#DCFCE7", "#166534", "#BBF7D0")}>Run Complete</span>
					</h3>
					<div style={{ fontSize: 11, color: C.inkMuted, marginTop: 2 }}>
						Revision: {bp?.lineage?.blueprint_revision ?? 1} · Domain: {bp?.domain_namespace ?? "default"}
					</div>
				</div>

				<div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
					<button
						type="button"
						data-testid="btn-sync-ontology-delta"
						onClick={handleSyncDelta}
						disabled={isSyncing}
						style={S_BTN(false)}
					>
						<span>{isSyncing ? "..." : "🔄"}</span>
						<span>{(d.ontology_deltas_applied ?? []).length === 0 ? "Sync Ontology Deltas" : "Sync Ontology Delta to Studio Session"}</span>
					</button>

					<button
						type="button"
						data-testid="btn-evolve-schema"
						onClick={() => bp && onEvolveSchema?.(bp)}
						disabled={!bp}
						style={S_BTN(true)}
					>
						<span>Evolve Schema with New Data ➔</span>
					</button>
				</div>
			</div>

			{syncError && (
				<div data-testid="sync-delta-error-banner" style={{ padding: "6px 10px", borderRadius: 4, backgroundColor: "#FEF2F2", border: "1px solid #FCA5A5", color: "#991B1B", fontSize: 12, fontWeight: 600, marginBottom: 10 }}>
					✕ {syncError}
				</div>
			)}

			{syncMsg && (
				<div data-testid="sync-delta-toast" style={{ padding: "6px 10px", borderRadius: 4, backgroundColor: "#F0FDF4", border: "1px solid #BBF7D0", color: "#166534", fontSize: 12, fontWeight: 600, marginBottom: 10 }}>
					✓ {syncMsg}
				</div>
			)}

			{/* Scorecard */}
			<div data-testid="scorecard-container" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
				<div style={{ padding: "6px 8px", borderRadius: 4, backgroundColor: staticGatesPassed ? "#F0FDF4" : "#FEF2F2", border: `1px solid ${staticGatesPassed ? "#BBF7D0" : "#FCA5A5"}` }}>
					<div style={{ fontSize: 10, color: staticGatesPassed ? "#166534" : "#991B1B", fontWeight: 600 }}>STATIC VERIFICATION</div>
					<div data-testid="scorecard-gates-badge" style={{ fontSize: 12, fontWeight: 700, color: staticGatesPassed ? "#166534" : "#991B1B" }}>
						{staticGatesPassed ? "✓ Gate G0-G5 Passed" : "✕ Gate G0-G5 Failed"}
					</div>
				</div>

				<div style={{ padding: "6px 8px", borderRadius: 4, backgroundColor: shadowSandboxPassed ? "#F0FDF4" : "#FEF2F2", border: `1px solid ${shadowSandboxPassed ? "#BBF7D0" : "#FCA5A5"}` }}>
					<div style={{ fontSize: 10, color: shadowSandboxPassed ? "#166534" : "#991B1B", fontWeight: 600 }}>DUCKDB SHADOW SANDBOX</div>
					<div data-testid="scorecard-shadow-badge" style={{ fontSize: 12, fontWeight: 700, color: shadowSandboxPassed ? "#166534" : "#991B1B" }}>
						{shadowSandboxPassed ? "✓ 4-Zone Verification Passed" : "✕ 4-Zone Verification Failed"}
					</div>
				</div>
			</div>

			{/* Added Hubs, Links, Satellites */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
					gap: "8px",
					marginBottom: "10px",
				}}
			>
				<div data-testid="diff-added-hubs" style={S_BOX}>
					<div style={S_TITLE}>
						<span>Added Hubs</span>
						<span style={S_TAG("#E0F2FE", "#0369A1", "#BAE6FD")}>
							{d.added_hubs?.length ?? 0}
						</span>
					</div>
					<div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
						{d.added_hubs?.map((hub) => (
							<div
								key={hub}
								style={{
									display: "flex",
									justifyContent: "space-between",
									fontSize: "12px",
									fontFamily: F.mono,
								}}
							>
								<span>{hub}</span>
								{(() => {
									const tier = resolveHubKeyTier(hub, bp);
									const isTier1 = tier === "TIER_1";
									return (
										<span
											style={S_TAG(
												isTier1 ? "#FEF3C7" : "#F1F5F9",
												isTier1 ? "#92400E" : "#475569",
												isTier1 ? "#FDE68A" : "#CBD5E1",
											)}
										>
											{tier}
										</span>
									);
								})()}
							</div>
						))}
					</div>
				</div>

				<div data-testid="diff-added-links" style={S_BOX}>
					<div style={S_TITLE}>
						<span>Added Links</span>
						<span style={S_TAG("#E0F2FE", "#0369A1", "#BAE6FD")}>
							{d.added_links?.length ?? 0}
						</span>
					</div>
					<div
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "3px",
							fontSize: "12px",
							fontFamily: F.mono,
						}}
					>
						{d.added_links?.map((link) => (
							<div key={link}>{link}</div>
						))}
					</div>
				</div>

				<div data-testid="diff-added-satellites" style={S_BOX}>
					<div style={S_TITLE}>
						<span>Added / Widened Satellites</span>
						<span style={S_TAG("#E0F2FE", "#0369A1", "#BAE6FD")}>
							{d.added_satellites?.length ?? 0}
						</span>
					</div>
					<div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
						{d.added_satellites?.map((sat) => {
							const ext =
								sat.endsWith("_ext") || d.companion_satellites?.includes(sat);
							return (
								<div
									key={sat}
									style={{
										display: "flex",
										justifyContent: "space-between",
										fontSize: "12px",
										fontFamily: F.mono,
									}}
								>
									<span>{sat}</span>
									{ext && (
										<span style={S_TAG("#EDE9FE", "#6D28D9", "#DDD6FE")}>
											sat_*_ext
										</span>
									)}
								</div>
							);
						})}
					</div>
				</div>
			</div>

			{/* Schema Drift */}
			<div data-testid="diff-drift-repairs" style={S_BOX}>
				<div style={S_TITLE}>
					<span>Detected & Repaired Schema Drift Events</span>
					<span style={S_TAG("#FEF3C7", "#92400E", "#FDE68A")}>
						{(d.widened_columns?.length ?? 0) +
							(d.companion_satellites?.length ?? 0)}
					</span>
				</div>
				<div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
					{d.widened_columns?.map((item) => {
						const tableName = String((item as any).table_name || (item as any).table || "");
						const colName = String((item as any).column_name || (item as any).column || "");
						const priorType = String((item as any).prior_type || (item as any).from || "");
						const newType = String((item as any).new_type || (item as any).to || "");
						return (
							<div key={`w-${tableName}.${colName}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
								<span style={S_TAG("#DCFCE7", "#166534", "#BBF7D0")}>WIDENED</span>
								<span style={{ fontFamily: F.mono }}>{tableName}.{colName}</span>
								<span style={{ color: C.inkMuted }}>from {priorType} ➔ to {newType}</span>
							</div>
						);
					})}
					{d.companion_satellites?.map((sat) => (
						<div key={`c-${sat}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
							<span style={S_TAG("#EDE9FE", "#6D28D9", "#DDD6FE")}>COMPANION EXT</span>
							<span style={{ fontFamily: F.mono }}>{sat}</span>
							<span style={{ color: C.inkMuted }}>Isolated drift into companion satellite</span>
						</div>
					))}
				</div>
			</div>

			{/* Applied Ontology Deltas */}
			<div data-testid="diff-ontology-deltas" style={S_BOX}>
				<div style={S_TITLE}>
					<span>Applied Ontology Deltas</span>
					<span style={S_TAG("#DCFCE7", "#166534", "#BBF7D0")}>{d.ontology_deltas_applied?.length ?? 0}</span>
				</div>
				<div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
					{d.ontology_deltas_applied?.map((delta) => {
						const opType = String((delta as any).op_type || (delta as any).op || "Delta");
						const conceptName = String((delta as any).concept_name || (delta as any).concept || (delta as any).concept_uri || "Concept");
						const extraDesc = (delta as any).attribute_name ? `attribute: ${String((delta as any).attribute_name)}` : (delta as any).attribute ? `attribute: ${String((delta as any).attribute)}` : (delta as any).mapping ? `mapping: ${String((delta as any).mapping)}` : "";
						return (
							<div key={`od-${opType}-${conceptName}-${extraDesc}`} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
								<div style={{ display: "flex", alignItems: "center", gap: 6 }}>
									<span style={S_TAG("#E0F2FE", "#0369A1", "#BAE6FD")}>{opType}</span>
									<strong>{conceptName}</strong>
									<span style={{ color: C.inkMuted }}>{extraDesc}</span>
								</div>
								{typeof (delta as any).confidence === "number" && (
									<span style={{ fontFamily: F.mono, fontSize: 11, color: C.inkMuted }}>conf: {((delta as any).confidence as number).toFixed(2)}</span>
								)}
							</div>
						);
					})}
				</div>
			</div>

			{/* Artifact Downloads */}
			<div
				style={{ borderTop: `1px solid ${C.borderLine}`, paddingTop: "10px" }}
			>
				<div style={S_TITLE}>Pipeline Artifact Downloads</div>
				<div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
					<button
						type="button"
						data-testid="btn-download-contract"
						onClick={() =>
							downloadBlob(rawContract, "final_contract.yaml", "text/yaml")
						}
						style={S_BTN(false)}
					>
						📥 final_contract.yaml
					</button>
					<button
						type="button"
						data-testid="btn-download-blueprint"
						onClick={() =>
							downloadBlob(rawBp, "vault_blueprint.yaml", "text/yaml")
						}
						style={S_BTN(false)}
					>
						📥 vault_blueprint.yaml
					</button>
					<button
						type="button"
						data-testid="btn-download-duckdb-sql"
						onClick={() =>
							downloadBlob(rawDuck, "dv_duckdb_schema.sql", "text/sql")
						}
						style={S_BTN(false)}
					>
						📥 dv_duckdb_schema.sql
					</button>
					<button
						type="button"
						data-testid="btn-download-databricks-sql"
						onClick={() =>
							downloadBlob(rawDbx, "dv_databricks_schema.sql", "text/sql")
						}
						style={S_BTN(false)}
					>
						📥 dv_databricks_schema.sql
					</button>
				</div>
			</div>
		</div>
	);
}
