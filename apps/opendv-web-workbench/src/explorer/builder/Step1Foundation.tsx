/** Step 1: Foundation & Inferred Mode Selector */
import type { VaultBlueprintDTO } from "@opendv/protocol-ts";
import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parse as parseYaml } from "yaml";
import { getStudioAuthHeaders } from "../../ontology/store/studio-store";
import { EXPLORER_COLORS as C, EXPLORER_FONTS as F } from "../design-tokens";
import type { FoundationMode, Step1FoundationProps } from "./wizard-types";
import { formStateToPipelineInputDto } from "./wizardSpecMapper";

interface StudioSessionSummary {
	session_id: string;
	domain_namespace?: string | undefined;
	concepts_count?: number | undefined;
}

const S = {
	sec: { backgroundColor: C.bgSurface, borderRadius: 8, border: `1px solid ${C.borderLine}`, padding: 20 },
	inp: { width: "100%", padding: "8px 12px", borderRadius: 5, border: `1px solid ${C.borderLine}`, fontFamily: F.mono, fontSize: 13, boxSizing: "border-box" as const },
	lbl: { display: "block", fontSize: 12, fontWeight: 600, marginBottom: 6 },
	btnUp: { padding: "8px 12px", borderRadius: 5, backgroundColor: C.bgPage, border: `1px solid ${C.borderLine}`, fontSize: 12, fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" as const },
	box: { marginBottom: 16, display: "flex", flexDirection: "column" as const, gap: 8 },
	row: { display: "flex", gap: 8, alignItems: "center" as const },
	between: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
	clearBtn: { background: "none", border: "none", color: "#DC2626", fontSize: 11, cursor: "pointer", textDecoration: "underline" as const },
	radio: (sel: boolean) => ({ display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 12px", borderRadius: 6, border: `1px solid ${sel ? C.borderSelected : C.borderLine}`, backgroundColor: sel ? "#EFF6FF" : C.bgSurface, cursor: "pointer" as const }),
};

export function Step1Foundation({
	formState,
	onChange,
	availableSessions: initialSessions,
	isLoadingSessions: initialLoading,
	onSelectSession,
	apiUrl = "/api/v1",
	fetchImpl,
}: Step1FoundationProps) {
	const [sessions, setSessions] = useState<StudioSessionSummary[]>(initialSessions ?? []);
	const [isLoading, setIsLoading] = useState<boolean>(initialLoading ?? !initialSessions);
	const [sessionError, setSessionError] = useState<string | null>(null);

	useEffect(() => {
		if (initialSessions && initialSessions.length > 0) {
			setSessions(initialSessions);
			setIsLoading(false);
			return;
		}
		let isMounted = true;
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
		if (!fetcher) { setIsLoading(false); return; }
		setIsLoading(true);
		const sessionsUrl = `${apiUrl}/ontology/studio/sessions`;
		fetcher(sessionsUrl, { credentials: "include", headers: getStudioAuthHeaders(undefined, sessionsUrl) })
			.then((res) => (res.ok ? res.json() : null))
			.then((d) => {
				if (!isMounted) return;
				setSessions(Array.isArray(d) ? d : Array.isArray(d?.sessions) ? d.sessions : []);
				setIsLoading(false);
			})
			.catch((err) => {
				if (!isMounted) return;
				setSessionError(err instanceof Error ? err.message : "Failed to load studio sessions");
				setIsLoading(false);
			});
		return () => { isMounted = false; };
	}, [apiUrl, fetchImpl, initialSessions]);

	const loadSessionBlueprint = useCallback(
		(sid: string) => {
			const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
			if (!fetcher) return;
			const bpUrl = `${apiUrl}/ontology/studio/sessions/${encodeURIComponent(sid)}/export-blueprint`;
			fetcher(bpUrl, { credentials: "include", headers: getStudioAuthHeaders(undefined, bpUrl) })
				.then((r) => (r.ok ? r.json() : null))
				.then((d) => {
					if (d?.blueprint) {
						onChange({
							activeSessionId: sid,
							blueprint: d.blueprint,
							contract: d.blueprint.contract ?? null,
							domainNamespace: d.blueprint.domain_namespace ?? formState.domainNamespace,
						});
					}
				})
				.catch(() => {});
		},
		[apiUrl, fetchImpl, formState.domainNamespace, onChange],
	);

	const hasSeededUrlRef = useRef(false);
	useEffect(() => {
		if (hasSeededUrlRef.current) return;
		hasSeededUrlRef.current = true;
		const sidFromUrl = typeof window !== "undefined" && window.location?.search ? new URLSearchParams(window.location.search).get("session_id") : null;
		if (sidFromUrl) {
			onChange({ activeSessionId: sidFromUrl, foundationMode: "studio_session", blueprint: null, contract: null });
			onSelectSession?.(sidFromUrl);
			loadSessionBlueprint(sidFromUrl);
		}
	}, [loadSessionBlueprint, onChange, onSelectSession]);

	const prevSessionIdRef = useRef<string | null>(formState.activeSessionId);
	useEffect(() => {
		if (prevSessionIdRef.current !== formState.activeSessionId) {
			prevSessionIdRef.current = formState.activeSessionId;
			if (formState.activeSessionId && formState.foundationMode === "studio_session") {
				onChange({ blueprint: null, contract: null });
				loadSessionBlueprint(formState.activeSessionId);
			}
		} else if (formState.activeSessionId && !formState.blueprint && formState.foundationMode === "studio_session") {
			loadSessionBlueprint(formState.activeSessionId);
		}
	}, [formState.activeSessionId, formState.blueprint, formState.foundationMode, loadSessionBlueprint, onChange]);

	useEffect(() => {
		let isMounted = true;
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
		if (!fetcher) return;
		const timer = setTimeout(() => {
			fetcher(`${apiUrl}/runs/validate`, {
				method: "POST",
				credentials: "include",
				headers: getStudioAuthHeaders({ "Content-Type": "application/json" }),
				body: JSON.stringify(formStateToPipelineInputDto(formState)),
			})
				.then((r) => (r.ok ? r.json() : null))
				.then((plan) => { if (isMounted && plan?.primary_action) onChange({ inferredPlan: plan }); })
				.catch(() => {});
		}, 300);
		return () => { isMounted = false; clearTimeout(timer); };
	}, [formState.foundationMode, formState.activeSessionId, formState.blueprint, formState.blueprintPath, formState.contractPath, formState.ontologyPath, formState.configuredSources.length, formState.unstructuredDocs.length, apiUrl, fetchImpl]);

	const handleFileUpload = (e: ChangeEvent<HTMLInputElement>, cb: (name: string, content: string, parsed: Record<string, unknown> | null) => void) => {
		const file = e.target.files?.[0];
		if (!file) return;
		const reader = new FileReader();
		reader.onload = (event) => {
			const content = (event.target?.result as string) ?? "";
			let parsed: Record<string, unknown> | null = null;
			try { parsed = parseYaml(content) as Record<string, unknown>; } catch {}
			cb(file.name, content, parsed);
		};
		reader.readAsText(file);
	};

	const handleBlueprintFileUpload = (e: ChangeEvent<HTMLInputElement>) =>
		handleFileUpload(e, (name, content, parsed) => {
			const bp = parsed && typeof parsed === "object" ? (parsed as unknown as VaultBlueprintDTO) : null;
			onChange({
				blueprintPath: name,
				blueprintContent: content,
				...(bp ? { blueprint: bp, contract: (bp.contract as Record<string, unknown>) ?? null, domainNamespace: bp.domain_namespace ?? formState.domainNamespace } : {}),
			});
		});

	const handleOntologyFileUpload = (e: ChangeEvent<HTMLInputElement>) =>
		handleFileUpload(e, (ontologyPath, ontologyContent, parsed) =>
			onChange({ ontologyPath, ontologyContent, ontologySource: parsed && typeof parsed === "object" ? parsed : null }),
		);

	const handleContractFileUpload = (e: ChangeEvent<HTMLInputElement>) =>
		handleFileUpload(e, (contractPath, contractContent, parsed) =>
			onChange({ contractPath, contractContent, contract: parsed && typeof parsed === "object" ? parsed : null }),
		);

	const hasExistingContract = Boolean(
		formState.foundationMode === "vault_blueprint" ||
			formState.contract !== null ||
			(formState.contractContent && formState.contractContent.trim().length > 0) ||
			(formState.contractPath && formState.contractPath.trim().length > 0),
	);

	const isZeroData = formState.configuredSources.length === 0 && formState.unstructuredDocs.length === 0;

	const executionPlan = useMemo(() => {
		if (isZeroData && hasExistingContract) {
			return { action: "VERIFIED_NO_OP", title: "Zero-Data: Deterministic Verification Pass (VERIFIED_NO_OP)", color: "#16A34A", bg: "#DCFCE7" };
		}
		if (formState.inferredPlan?.primary_action) {
			const act = formState.inferredPlan.primary_action;
			const isEvol = act === "SCHEMA_DRIFT" || act === "MULTI_SOURCE_EXPANSION";
			const isUnstructured = act === "UNSTRUCTURED_FIRST";
			const isNoOp = act === "VERIFIED_NO_OP";
			return {
				action: act,
				title: formState.inferredPlan.explanation || `${act}: Data-Driven Authoritative Plan`,
				color: isNoOp ? "#16A34A" : isEvol ? "#059669" : isUnstructured ? "#9333EA" : "#0284C7",
				bg: isNoOp ? "#DCFCE7" : isEvol ? "#ECFDF5" : isUnstructured ? "#F3E8FF" : "#E0F2FE",
			};
		}
		if (isZeroData) {
			return hasExistingContract
				? { action: "VERIFIED_NO_OP", title: "Zero-Data: Deterministic Verification Pass (VERIFIED_NO_OP)", color: "#16A34A", bg: "#DCFCE7" }
				: { action: "COLD_START_N1", title: "Zero-Data: Ontology-Only Seed Synthesis (COLD_START_N1)", color: "#0284C7", bg: "#E0F2FE" };
		}
		if (formState.unstructuredDocs.length > 0 && formState.configuredSources.length === 0) {
			return { action: "UNSTRUCTURED_FIRST", title: "Unstructured Materials First (UNSTRUCTURED_FIRST)", color: "#9333EA", bg: "#F3E8FF" };
		}
		if (formState.configuredSources.length > 0) {
			return hasExistingContract
				? { action: "MULTI_SOURCE_EXPANSION", title: "Multi-Source Expansion (MULTI_SOURCE_EXPANSION)", color: "#059669", bg: "#ECFDF5" }
				: { action: "INCREMENTAL_SOURCE", title: "Incremental Tabular Onboarding (INCREMENTAL_SOURCE)", color: "#0284C7", bg: "#E0F2FE" };
		}
		return hasExistingContract
			? { action: "MULTI_SOURCE_EXPANSION", title: "Multi-Source Expansion (MULTI_SOURCE_EXPANSION)", color: "#059669", bg: "#ECFDF5" }
			: { action: "COLD_START_N1", title: "Greenfield Cold Start (COLD_START_N1)", color: "#0284C7", bg: "#E0F2FE" };
	}, [formState.inferredPlan, hasExistingContract, isZeroData, formState.unstructuredDocs.length, formState.configuredSources.length]);

	const MODES: Array<{ mode: FoundationMode; label: string; desc: string }> = [
		{ mode: "studio_session", label: "Active Studio Session", desc: "Ontology Studio session with live concepts" },
		{ mode: "vault_blueprint", label: "Previous Run VaultBlueprint", desc: "Load vault_blueprint.yaml to evolve schema" },
		{ mode: "raw_ontology", label: "Raw Ontology (+ Optional Contract)", desc: "Upload graph_a_ontology.yaml and contract" },
	];

	return (
		<section data-testid="wizard-step-foundation" style={S.sec}>
			<h2 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 8px" }}>Step 1: Choose Foundation</h2>
			<p style={{ fontSize: 13, color: C.inkMuted, marginBottom: 16 }}>
				Start from an active Ontology Studio Session, a previously exported VaultBlueprint, or standalone raw ontology files.
			</p>

			<div data-testid="inferred-mode-status-card" style={{ padding: 12, borderRadius: 6, marginBottom: 16, border: `1px solid ${hasExistingContract ? "#FDE68A" : "#BAE6FD"}`, backgroundColor: hasExistingContract ? "#FEF3C7" : "#F0F9FF" }}>
				<div data-testid="inferred-mode-card-header" style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
					<span>{hasExistingContract ? "🔄" : "✨"}</span>
					<strong style={{ fontSize: 13, color: hasExistingContract ? "#92400E" : "#0369A1" }}>
						{hasExistingContract ? "Existing Schema Update & Evolution" : "Greenfield Future-Proof Design"}
					</strong>
				</div>
				<p style={{ fontSize: 12, margin: "0 0 8px", color: hasExistingContract ? "#78350F" : "#075985" }}>
					{hasExistingContract ? "Additive schema evolution." : "Greenfield schema synthesis from ontology."}
				</p>
				<div data-testid="inferred-execution-plan-card" style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 10px", borderRadius: 4, backgroundColor: "#FFF", border: `1px solid ${C.borderLine}` }}>
					<span data-testid="inferred-plan-badge" style={{ padding: "2px 7px", borderRadius: 4, fontSize: 11, fontFamily: F.mono, fontWeight: 700, color: executionPlan.color, backgroundColor: executionPlan.bg }}>
						{executionPlan.action}
					</span>
					<span style={{ fontSize: 12, color: C.inkPrimary, fontWeight: 500 }}>{executionPlan.title}</span>
				</div>
			</div>

			<div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
				{MODES.map((opt) => (
					<label key={opt.mode} style={S.radio(formState.foundationMode === opt.mode)}>
						<input type="radio" name="foundationMode" value={opt.mode} checked={formState.foundationMode === opt.mode} onChange={() => onChange({ foundationMode: opt.mode })} style={{ marginTop: 3 }} />
						<div style={{ flex: 1 }}>
							<div style={{ fontWeight: 600, fontSize: 13 }}>{opt.label}</div>
							<div style={{ fontSize: 12, color: C.inkMuted, marginTop: 2 }}>{opt.desc}</div>
						</div>
					</label>
				))}
			</div>

			{formState.foundationMode === "studio_session" && (
				<div style={S.box}>
					<label htmlFor="select-studio-session" style={S.lbl}>Select Active Studio Session:</label>
					{isLoading ? (
						<div data-testid="loading-sessions-indicator" style={{ fontSize: 12, color: C.inkMuted }}>Fetching active studio sessions...</div>
					) : (
						<select
							id="select-studio-session"
							data-testid="select-studio-session"
							value={formState.activeSessionId ?? ""}
							onChange={(e) => {
								const sid = e.target.value;
								const match = sessions.find((s) => s.session_id === sid);
								onChange({
									activeSessionId: sid || null,
									blueprint: null,
									contract: null,
									domainNamespace: match?.domain_namespace ?? formState.domainNamespace,
								});
								if (sid) {
									onSelectSession?.(sid);
									loadSessionBlueprint(sid);
								}
							}}
							style={{ ...S.inp, fontFamily: F.sans }}
						>
							<option value="">-- Choose an active session --</option>
							{sessions.map((s) => (
								<option key={s.session_id} value={s.session_id}>
									{s.session_id} ({s.domain_namespace ?? "default"} · {s.concepts_count ?? 0} concepts)
								</option>
							))}
						</select>
					)}

					<div style={{ display: "flex", alignItems: "center", gap: 8 }}>
						<label htmlFor="input-session-id" style={{ fontSize: 11, color: C.inkMuted, whiteSpace: "nowrap" }}>Or manual session ID:</label>
						<input
							id="input-session-id"
							type="text"
							data-testid="input-session-id"
							value={formState.activeSessionId ?? ""}
							onChange={(e) => {
								const val = e.target.value.trim();
								onChange({ activeSessionId: val || null });
								if (val) onSelectSession?.(val);
							}}
							placeholder="e.g. session-am-walkthrough"
							style={{ ...S.inp, padding: "6px 10px", fontSize: 12 }}
						/>
					</div>
					{sessionError && (
						<div data-testid="session-fetch-warning" style={{ fontSize: 11, color: "#B45309" }}>
							Notice: Failed to fetch studio sessions ({sessionError}).
						</div>
					)}
				</div>
			)}

			{formState.foundationMode === "vault_blueprint" && (
				<div style={S.box}>
					<label htmlFor="input-blueprint-path" style={S.lbl}>VaultBlueprint Workspace Path or Upload:</label>
					<div style={S.row}>
						<input
							id="input-blueprint-path"
							type="text"
							data-testid="input-blueprint-path"
							value={formState.blueprintPath}
							onChange={(e) => onChange({ blueprintPath: e.target.value })}
							placeholder="e.g. data/blueprints/vault_blueprint.yaml"
							style={{ ...S.inp, flex: 1 }}
						/>
						<label style={S.btnUp}>
							Upload YAML
							<input type="file" data-testid="input-blueprint-file" accept=".yaml,.yml,.json" onChange={handleBlueprintFileUpload} style={{ display: "none" }} />
						</label>
					</div>

					{formState.blueprint && (
						<div data-testid="blueprint-lineage-card" style={{ padding: "8px 12px", borderRadius: 5, backgroundColor: "#F8FAFC", border: "1px solid #E2E8F0", fontSize: 12 }}>
							Loaded Revision: <span data-testid="blueprint-revision-badge" style={{ fontFamily: F.mono, fontWeight: 700, color: "#2563EB" }}>{formState.blueprint.lineage?.blueprint_revision ?? "v1.0.0"}</span> · Namespace: {formState.blueprint.domain_namespace ?? "default"}
						</div>
					)}
				</div>
			)}

			{formState.foundationMode === "raw_ontology" && (
				<div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
					<div>
						<label htmlFor="input-ontology-path" style={S.lbl}>Graph A Ontology Path or Upload:</label>
						<div style={S.row}>
							<input id="input-ontology-path" type="text" data-testid="input-ontology-path" value={formState.ontologyPath} onChange={(e) => onChange({ ontologyPath: e.target.value })} placeholder="e.g. schemas/ontology.yaml" style={{ ...S.inp, flex: 1 }} />
							<label style={S.btnUp}>Upload Ontology<input type="file" data-testid="input-ontology-file" accept=".yaml,.yml,.json" onChange={handleOntologyFileUpload} style={{ display: "none" }} /></label>
						</div>
					</div>
					<div>
						<div style={S.between}>
							<label htmlFor="input-contract-path" style={{ fontSize: 12, fontWeight: 600 }}>Optional Existing DV 2.0 Contract Path or Upload:</label>
							{hasExistingContract && (
								<button type="button" data-testid="btn-clear-contract" onClick={() => onChange({ contractPath: "", contractContent: "", contract: null })} style={S.clearBtn}>Clear (Switch to Greenfield)</button>
							)}
						</div>
						<div style={S.row}>
							<input id="input-contract-path" type="text" data-testid="input-contract-path" value={formState.contractPath} onChange={(e) => onChange({ contractPath: e.target.value })} placeholder="Leave empty for Greenfield Design, or specify schemas/dv_contract.yaml" style={{ ...S.inp, flex: 1 }} />
							<label style={S.btnUp}>Upload Contract<input type="file" data-testid="input-contract-file" accept=".yaml,.yml,.json" onChange={handleContractFileUpload} style={{ display: "none" }} /></label>
						</div>
					</div>
				</div>
			)}
		</section>
	);
}
