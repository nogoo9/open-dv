import { useCallback, useEffect, useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type {
	AgentPackSummary,
	ConnectorAdapterSpec,
	ExternalMcpServerSpec,
	SecretSummary,
	WorkspaceSettings,
} from "../api/openapi";
import { SettingsMcpTab } from "../components/SettingsMcpTab";
import { SettingsProviderTab } from "../components/SettingsProviderTab";
import { SettingsSecretsTab } from "../components/SettingsSecretsTab";
import { useProjectStore } from "../store/projectStore";

const TABS = [
	["provider", "1. Agent Harness, Model Matrix & Budgets"],
	["secrets", "2. Profiles & KeyringSecretStore"],
	["mcp", "3. Adapters, SKILL.md & External MCP"],
] as const;

type SettingsTabId = (typeof TABS)[number][0];

export function SettingsHub() {
	const currentUrl = useProjectStore((s) => s.currentUrl);
	const healthOk = useProjectStore((s) => s.healthOk);
	const healthVersion = useProjectStore((s) => s.healthVersion);
	const settings = useProjectStore((s) => s.settings);
	const settingsDraft = useProjectStore((s) => s.settingsDraft);
	const readiness = useProjectStore((s) => s.readiness);
	const dirtySettings = useProjectStore((s) => s.dirtyFlags.settings);
	const navigate = useProjectStore((s) => s.navigate);
	const setSettings = useProjectStore((s) => s.setSettings);
	const setSettingsDraft = useProjectStore((s) => s.setSettingsDraft);
	const fetchReadiness = useProjectStore((s) => s.fetchReadiness);
	const applyDiscardReset = useProjectStore((s) => s.applyDiscardReset);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);
	const announce = useProjectStore((s) => s.announce);

	const parsed = new URL(currentUrl, "http://localhost");
	const rawTab = parsed.searchParams.get("tab") ?? "provider";
	const activeTab: SettingsTabId =
		rawTab === "secrets" || rawTab === "mcp" ? rawTab : "provider";

	const [adapters, setAdapters] = useState<ConnectorAdapterSpec[]>([]);
	const [agents, setAgents] = useState<AgentPackSummary[]>([]);
	const [mcpServers, setMcpServers] = useState<ExternalMcpServerSpec[]>([]);
	const [secrets, setSecrets] = useState<SecretSummary[]>([]);
	const [busy, setBusy] = useState(false);

	const refreshSecrets = useCallback(async () => {
		const [list] = await Promise.all([api.listSecrets(), fetchReadiness()]);
		setSecrets(list);
	}, [fetchReadiness]);

	const refreshMcp = useCallback(async () => {
		const list = await api.listMcpServers();
		setMcpServers(list);
	}, []);

	useEffect(() => {
		const ac = new AbortController();
		const load = async () => {
			const [ws, ad, ag, mcp, sec] = await Promise.all([
				api.getSettings(ac.signal),
				api.listSourceAdapters(ac.signal),
				api.listAgents(ac.signal),
				api.listMcpServers(ac.signal),
				api.listSecrets(undefined, ac.signal),
				fetchReadiness(),
			]);
			if (ac.signal.aborted) return;
			setSettings(ws);
			if (!useProjectStore.getState().dirtyFlags.settings) {
				setSettingsDraft(structuredClone(ws), false);
			}
			setAdapters(ad);
			setAgents(ag);
			setMcpServers(mcp);
			setSecrets(sec);
		};
		void load().catch((err) => {
			if (!ac.signal.aborted) setLastProblem(parseProblemDetails(err));
		});
		return () => ac.abort();
	}, [setSettings, setSettingsDraft, setLastProblem, fetchReadiness]);

	const saveSettings = async () => {
		if (!settingsDraft || busy) return;
		setBusy(true);
		try {
			const merged: WorkspaceSettings = {
				...settingsDraft,
				trusted_workspaces: settings?.trusted_workspaces ?? [],
			};
			const saved = await api.putSettings(merged);
			setSettings(saved);
			setSettingsDraft(structuredClone(saved), false);
			await fetchReadiness();
			announce("polite", "Settings saved");
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	const onTabKeyDown = (e: React.KeyboardEvent, idx: number) => {
		const next =
			e.key === "ArrowRight"
				? (idx + 1) % TABS.length
				: e.key === "ArrowLeft"
					? (idx - 1 + TABS.length) % TABS.length
					: e.key === "Home"
						? 0
						: e.key === "End"
							? TABS.length - 1
							: -1;
		if (next >= 0) {
			e.preventDefault();
			const target = TABS[next];
			if (target) navigate(`/settings?tab=${target[0]}`);
		}
	};

	const draft = settingsDraft ?? settings;

	return (
		<div className="stack-md">
			<div className="row-between">
				<div>
					<h1 className="h1-serif">Workspace &amp; User State Settings</h1>
					<p className="wb-sub">
						Stored out-of-repo under <code>&lt;OS_USER_STATE&gt;/opendv/</code>{" "}
						(never committed to Git).
					</p>
				</div>
				<div className="row">
					<span className={`pill ${healthOk ? "pill-ok" : "pill-err"}`}>
						{healthOk
							? `Server Healthy${healthVersion ? ` · v${healthVersion}` : ""}`
							: "Server Offline"}
					</span>
					{dirtySettings && (
						<button
							type="button"
							className="btn btn-secondary btn-sm"
							onClick={() => applyDiscardReset()}
						>
							Discard Draft
						</button>
					)}
					<button
						type="button"
						className="btn btn-primary btn-sm"
						disabled={busy || !draft}
						onClick={() => void saveSettings()}
					>
						Save Settings
					</button>
				</div>
			</div>

			<div className="tab-strip" role="tablist" aria-label="Settings Sections">
				{TABS.map(([id, label], idx) => (
					<button
						key={id}
						type="button"
						role="tab"
						id={`set-tab-${id}`}
						aria-selected={activeTab === id}
						aria-controls={`set-panel-${id}`}
						tabIndex={activeTab === id ? 0 : -1}
						className="tab-btn"
						onClick={() => navigate(`/settings?tab=${id}`)}
						onKeyDown={(e) => onTabKeyDown(e, idx)}
					>
						{label}
					</button>
				))}
			</div>

			<div
				role="tabpanel"
				id={`set-panel-${activeTab}`}
				aria-labelledby={`set-tab-${activeTab}`}
				className="stack-md"
			>
				{!draft ? (
					<div className="card-subtle text-sm text-muted">
						Loading settings...
					</div>
				) : activeTab === "provider" ? (
					<SettingsProviderTab
						draft={draft}
						agents={agents}
						readiness={readiness}
						onRefreshSecrets={refreshSecrets}
						onChangeDraft={(next) => setSettingsDraft(next, true)}
					/>
				) : activeTab === "secrets" ? (
					<SettingsSecretsTab
						draft={draft}
						secrets={secrets}
						onRefreshSecrets={refreshSecrets}
						onChangeDraft={(next) => setSettingsDraft(next, true)}
					/>
				) : (
					<SettingsMcpTab
						adapters={adapters}
						agents={agents}
						mcpServers={mcpServers}
						onRefreshMcp={refreshMcp}
					/>
				)}
			</div>

			<div className="card-subtle row-between text-sm text-muted">
				<span>CLI Diagnostic Command:</span>
				<code className="mono">opendv doctor --check-agents --check-mcp</code>
			</div>
		</div>
	);
}
