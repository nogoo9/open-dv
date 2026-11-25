import { useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import {
	type AgentPackSummary,
	CEGAR_POLICY_DEFAULTS,
	type CegarPolicy,
	type DbtSqlDialect,
	type HarnessProvider,
	type WorkspaceSettings,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

const PROVIDERS: readonly HarnessProvider[] = [
	"agy",
	"claude-cli",
	"claude",
	"gemini",
];
const POLICIES: readonly CegarPolicy[] = [
	"symbolic_only",
	"fast",
	"balanced",
	"strict",
];
const DIALECTS: readonly DbtSqlDialect[] = [
	"duckdb",
	"databricks",
	"snowflake",
	"postgres",
	"bigquery",
	"tsql",
	"redshift",
	"clickhouse",
];

export interface SettingsProviderTabProps {
	draft: WorkspaceSettings;
	agents: AgentPackSummary[];
	onChangeDraft: (next: WorkspaceSettings) => void;
}

export function SettingsProviderTab({
	draft,
	agents,
	onChangeDraft,
}: SettingsProviderTabProps) {
	const settings = useProjectStore((s) => s.settings);
	const setSettings = useProjectStore((s) => s.setSettings);
	const setLastProblem = useProjectStore((s) => s.setLastProblem);
	const announce = useProjectStore((s) => s.announce);

	const [providerSwitchPrompt, setProviderSwitchPrompt] =
		useState<HarnessProvider | null>(null);
	const [revokingRoot, setRevokingRoot] = useState<string | null>(null);

	const selectProvider = (nextProv: HarnessProvider) => {
		if (nextProv === draft.harness_provider) return;
		if (Object.keys(draft.model_overrides ?? {}).length > 0) {
			setProviderSwitchPrompt(nextProv);
		} else {
			onChangeDraft({ ...draft, harness_provider: nextProv });
		}
	};

	const setPolicy = (pol: CegarPolicy) => {
		onChangeDraft({
			...draft,
			cegar_policy: pol,
			v7_critic_tier:
				pol === "strict" ? "FRONTIER_CRITIC" : draft.v7_critic_tier,
		});
	};

	const revokeTrustedWorkspace = async (workspaceRoot: string) => {
		if (!settings) return;
		setRevokingRoot(workspaceRoot);
		try {
			const nextTrusted = (settings.trusted_workspaces ?? []).filter(
				(tw) => tw.workspace_root !== workspaceRoot,
			);
			const saved = await api.putSettings({
				...settings,
				trusted_workspaces: nextTrusted,
			});
			setSettings(saved);
			announce("polite", `Revoked trusted workspace ${workspaceRoot}`);
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setRevokingRoot(null);
		}
	};

	const numOrNull = (val: string) => {
		const trimmed = val.trim();
		if (!trimmed) return null;
		const parsed = Number.parseInt(trimmed, 10);
		return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
	};

	const trusted = settings?.trusted_workspaces ?? [];

	return (
		<div className="stack-md">
			{providerSwitchPrompt && (
				<div className="callout-amber row-between" role="alert">
					<span>
						Switching provider to <strong>{providerSwitchPrompt}</strong> may
						invalidate existing <code>model_overrides</code>. Clear overrides?
					</span>
					<div className="row">
						<button
							type="button"
							className="btn btn-primary btn-sm"
							onClick={() => {
								onChangeDraft({
									...draft,
									harness_provider: providerSwitchPrompt,
									model_overrides: {},
								});
								setProviderSwitchPrompt(null);
							}}
						>
							Switch &amp; Clear Overrides
						</button>
						<button
							type="button"
							className="btn btn-secondary btn-sm"
							onClick={() => {
								onChangeDraft({
									...draft,
									harness_provider: providerSwitchPrompt,
								});
								setProviderSwitchPrompt(null);
							}}
						>
							Keep Overrides
						</button>
					</div>
				</div>
			)}

			<div className="split-grid">
				<section className="card-subtle stack-sm">
					<h2 className="h2-serif">LLM Harness &amp; CEGAR Policy</h2>
					<label className="field-col">
						<span className="field-label">Harness Provider</span>
						<select
							className="select"
							value={draft.harness_provider}
							onChange={(e) =>
								selectProvider(e.target.value as HarnessProvider)
							}
						>
							{PROVIDERS.map((p) => (
								<option key={p} value={p}>
									{p}
								</option>
							))}
						</select>
					</label>

					<fieldset className="radio-group">
						<legend className="field-label">Default CEGAR Policy</legend>
						{POLICIES.map((pol) => (
							<label key={pol} className="radio-row">
								<input
									type="radio"
									name="settings_cegar_policy"
									value={pol}
									checked={draft.cegar_policy === pol}
									onChange={() => setPolicy(pol)}
								/>
								<span>
									{CEGAR_POLICY_DEFAULTS[pol].label} (<code>{pol}</code>)
								</span>
							</label>
						))}
					</fieldset>

					<label className="field-col">
						<span className="field-label">
							V7 Semantic Critic Tier{" "}
							{draft.cegar_policy === "strict" && "(Locked by Strict Policy)"}
						</span>
						<select
							className="select"
							disabled={draft.cegar_policy === "strict"}
							value={
								draft.cegar_policy === "strict"
									? "FRONTIER_CRITIC"
									: draft.v7_critic_tier
							}
							onChange={(e) =>
								onChangeDraft({
									...draft,
									v7_critic_tier: e.target
										.value as WorkspaceSettings["v7_critic_tier"],
								})
							}
						>
							<option value="FRONTIER_CRITIC">FRONTIER_CRITIC</option>
							<option value="STANDARD_WORKER">STANDARD_WORKER</option>
						</select>
					</label>

					<label className="field-col">
						<span className="field-label">Default dbt SQL Dialect</span>
						<select
							className="select"
							value={draft.dbt_sql_dialect}
							onChange={(e) =>
								onChangeDraft({
									...draft,
									dbt_sql_dialect: e.target.value as DbtSqlDialect,
								})
							}
						>
							{DIALECTS.map((d) => (
								<option key={d} value={d}>
									{d}
								</option>
							))}
						</select>
					</label>
				</section>

				<section className="card-subtle stack-sm">
					<h2 className="h2-serif">HarnessBudget Defaults</h2>
					<label className="field-col">
						<span className="field-label">Max Turns</span>
						<input
							type="number"
							min={1}
							className="input"
							placeholder="Inherit Policy Default (null)"
							value={draft.max_turns ?? ""}
							onChange={(e) =>
								onChangeDraft({
									...draft,
									max_turns: numOrNull(e.target.value),
								})
							}
						/>
					</label>
					<label className="field-col">
						<span className="field-label">Max Tokens</span>
						<input
							type="number"
							min={1000}
							step={10000}
							className="input"
							placeholder="Inherit Policy Default (null)"
							value={draft.max_tokens ?? ""}
							onChange={(e) =>
								onChangeDraft({
									...draft,
									max_tokens: numOrNull(e.target.value),
								})
							}
						/>
					</label>
					<label className="field-col">
						<span className="field-label">Timeout Seconds</span>
						<input
							type="number"
							min={10}
							step={30}
							className="input"
							placeholder="Inherit Policy Default (null)"
							value={draft.timeout_seconds ?? ""}
							onChange={(e) =>
								onChangeDraft({
									...draft,
									timeout_seconds: numOrNull(e.target.value),
								})
							}
						/>
					</label>
				</section>
			</div>

			<section className="card-subtle stack-sm">
				<h2 className="h2-serif">Agent Model Matrix</h2>
				<div className="table-scroll-region">
					<table className="responsive-table">
						<thead>
							<tr>
								<th scope="col">Agent</th>
								<th scope="col">Stage</th>
								<th scope="col">Model Tier</th>
								<th scope="col">Model Override</th>
							</tr>
						</thead>
						<tbody>
							{agents.map((ag) => {
								const isHorizon = ag.agent_id === "dv-horizon-critic";
								const isCritic = ag.agent_id === "dv-semantic-critic";
								const tierLabel = isHorizon
									? "FRONTIER_CRITIC (locked)"
									: isCritic
										? `${draft.cegar_policy === "strict" ? "FRONTIER_CRITIC" : draft.v7_critic_tier}`
										: "STANDARD_WORKER (fixed)";
								return (
									<tr key={ag.agent_id}>
										<th scope="row">
											<div className="mono">{ag.agent_id}</div>
											<div className="text-sm text-muted">{ag.role}</div>
										</th>
										<td data-label="Stage" className="mono">
											{ag.stage}
										</td>
										<td data-label="Tier">
											<span className="pill">{tierLabel}</span>
										</td>
										<td data-label="Model Override">
											<input
												type="text"
												className="input mono"
												aria-label={`Model override for ${ag.agent_id}`}
												placeholder="Default provider model"
												value={draft.model_overrides?.[ag.agent_id] ?? ""}
												onChange={(e) => {
													const next = { ...(draft.model_overrides ?? {}) };
													if (e.target.value.trim()) {
														next[ag.agent_id] = e.target.value.trim();
													} else {
														delete next[ag.agent_id];
													}
													onChangeDraft({ ...draft, model_overrides: next });
												}}
											/>
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</div>
			</section>

			<section className="card-subtle stack-sm">
				<div className="row-between">
					<h2 className="h2-serif">
						Trusted Workspaces (Server-Owned Allowlist)
					</h2>
					<code className="text-sm">
						opendv settings trust-workspace --path &lt;dir&gt;
					</code>
				</div>
				{trusted.length === 0 ? (
					<p className="text-sm text-muted">
						No workspace override packs trusted yet.
					</p>
				) : (
					trusted.map((tw) => (
						<div key={tw.workspace_root} className="card-cream row-between">
							<div className="stack-sm">
								<span className="mono font-semibold">{tw.workspace_root}</span>
								<span className="mono text-sm text-muted">
									sha256: {tw.agent_pack_sha256} · trusted {tw.trusted_at}
								</span>
							</div>
							<button
								type="button"
								className="btn btn-danger btn-sm"
								disabled={revokingRoot === tw.workspace_root}
								onClick={() => void revokeTrustedWorkspace(tw.workspace_root)}
							>
								Revoke
							</button>
						</div>
					))
				)}
			</section>
		</div>
	);
}
