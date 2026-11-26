import { useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import {
	type AgentPackDetail,
	type AgentPackSummary,
	type ConnectorAdapterSpec,
	type ExternalMcpServerSpec,
	validateServerId,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export interface SettingsMcpTabProps {
	adapters: ConnectorAdapterSpec[];
	agents: AgentPackSummary[];
	mcpServers: ExternalMcpServerSpec[];
	onRefreshMcp: () => Promise<void>;
}

export function SettingsMcpTab({
	adapters,
	agents,
	mcpServers,
	onRefreshMcp,
}: SettingsMcpTabProps) {
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const [selectedAgent, setSelectedAgent] = useState<AgentPackDetail | null>(
		null,
	);
	const [serverId, setServerId] = useState("");
	const [transport, setTransport] = useState<"stdio" | "streamable_http">(
		"streamable_http",
	);
	const [commandRaw, setCommandRaw] = useState("");
	const [urlRaw, setUrlRaw] = useState("");
	const [allowScout, setAllowScout] = useState(true);
	const [allowDocs, setAllowDocs] = useState(false);
	const [enabled, setEnabled] = useState(true);
	const [busy, setBusy] = useState(false);

	const serverIdErr = serverId.trim()
		? validateServerId(serverId.trim())
		: null;

	const inspectAgent = async (agentId: string) => {
		try {
			const detail = await api.getAgent(agentId);
			setSelectedAgent(detail);
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		}
	};

	const upsertServer = async (e: React.FormEvent) => {
		e.preventDefault();
		if (busy || !serverId.trim() || serverIdErr) return;
		const cmd = commandRaw
			.split(/\s+/)
			.map((s) => s.trim())
			.filter(Boolean);
		if (transport === "stdio" && cmd.length === 0) return;
		if (transport === "streamable_http" && !urlRaw.trim()) return;
		setBusy(true);
		try {
			await api.upsertMcpServer({
				server_id: serverId.trim(),
				transport,
				command: transport === "stdio" ? cmd : null,
				url: transport === "streamable_http" ? urlRaw.trim() : null,
				allowed_stages: ["step4_discovery"],
				allow_scout_calls: allowScout,
				allow_document_pages: allowDocs,
				enabled,
				timeout_seconds: 30,
				secret_refs: [],
			});
			setServerId("");
			setCommandRaw("");
			setUrlRaw("");
			await onRefreshMcp();
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	const deleteServer = async (id: string) => {
		if (busy) return;
		setBusy(true);
		try {
			await api.deleteMcpServer(id);
			await onRefreshMcp();
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	return (
		<div className="stack-md">
			<div className="split-grid">
				<section className="card-subtle stack-sm">
					<h2 className="h2-serif">
						Structured Source Adapters ({adapters.length})
					</h2>
					{adapters.map((ad) => (
						<div key={ad.adapter_id} className="card-cream row-between">
							<strong>{ad.display_name}</strong>
							<span className="mono text-sm text-muted">
								{ad.adapter_id} · {ad.fields.length} fields
							</span>
						</div>
					))}
				</section>

				<section className="card-subtle stack-sm">
					<h2 className="h2-serif">SKILL.md Agent Packs ({agents.length})</h2>
					{agents.map((ag) => (
						<div key={ag.agent_id} className="card-cream row-between">
							<div>
								<strong className="mono">{ag.agent_id}</strong>{" "}
								<span className="pill">{ag.source}</span>
							</div>
							<button
								type="button"
								className="btn btn-sm"
								onClick={() => void inspectAgent(ag.agent_id)}
							>
								Inspect SKILL.md
							</button>
						</div>
					))}
					{selectedAgent && (
						<pre
							className="card-dark mono text-sm"
							style={{ maxHeight: 200, overflow: "auto", margin: 0 }}
						>
							{selectedAgent.markdown_body}
						</pre>
					)}
				</section>
			</div>

			<section className="card-subtle stack-md">
				<h2 className="h2-serif">
					Step 4 External MCP Servers · User-Global ({mcpServers.length})
				</h2>

				{transport === "stdio" && (
					<div className="banner banner-danger" role="alert">
						<strong>Remote RCE Security Warning:</strong> Local{" "}
						<code>stdio</code> commands execute OS subprocesses under the server
						user. Never expose non-loopback{" "}
						<code>--allow-remote-unauthenticated</code> with untrusted clients.
					</div>
				)}

				<form className="stack-sm" onSubmit={upsertServer}>
					<div className="grid-3">
						<label className="field-col">
							<span className="field-label">Server ID</span>
							<input
								type="text"
								className="input mono"
								placeholder="e.g. sec-edgar-mcp"
								value={serverId}
								onChange={(e) => setServerId(e.target.value)}
							/>
							{serverIdErr && (
								<span
									className="text-sm"
									style={{ color: "var(--danger-ink)" }}
								>
									{serverIdErr}
								</span>
							)}
						</label>
						<label className="field-col">
							<span className="field-label">Transport</span>
							<select
								className="select"
								value={transport}
								onChange={(e) =>
									setTransport(e.target.value as "stdio" | "streamable_http")
								}
							>
								<option value="streamable_http">streamable_http</option>
								<option value="stdio">stdio</option>
							</select>
						</label>
						{transport === "stdio" ? (
							<label className="field-col">
								<span className="field-label">Command Args</span>
								<input
									type="text"
									className="input mono"
									placeholder="uvx mcp-server-..."
									value={commandRaw}
									onChange={(e) => setCommandRaw(e.target.value)}
								/>
							</label>
						) : (
							<label className="field-col">
								<span className="field-label">Streamable HTTP URL</span>
								<input
									type="url"
									className="input mono"
									placeholder="http://127.0.0.1:9001/mcp"
									value={urlRaw}
									onChange={(e) => setUrlRaw(e.target.value)}
								/>
							</label>
						)}
					</div>
					<div className="row">
						<label className="radio-row">
							<input
								type="checkbox"
								checked={allowScout}
								onChange={(e) => setAllowScout(e.target.checked)}
							/>
							<span>Allow Step 4 Scout Calls</span>
						</label>
						<label className="radio-row">
							<input
								type="checkbox"
								checked={allowDocs}
								onChange={(e) => setAllowDocs(e.target.checked)}
							/>
							<span>Allow Document Pages Opt-In</span>
						</label>
						<label className="radio-row">
							<input
								type="checkbox"
								checked={enabled}
								onChange={(e) => setEnabled(e.target.checked)}
							/>
							<span>Enabled</span>
						</label>
						<button
							type="submit"
							className="btn btn-primary btn-sm"
							disabled={busy || !serverId.trim() || Boolean(serverIdErr)}
						>
							Save MCP Server
						</button>
					</div>
				</form>

				{mcpServers.map((srv) => (
					<div key={srv.server_id} className="card-cream row-between">
						<div>
							<strong className="mono">{srv.server_id}</strong>{" "}
							<span className="pill">{srv.transport}</span>{" "}
							<span className="pill">
								{srv.enabled ? "Enabled" : "Disabled"}
							</span>
							<div className="mono text-sm text-muted">
								{srv.transport === "stdio"
									? (srv.command ?? []).join(" ")
									: srv.url}
							</div>
						</div>
						<button
							type="button"
							className="btn btn-danger btn-sm"
							disabled={busy}
							onClick={() => void deleteServer(srv.server_id)}
						>
							Delete
						</button>
					</div>
				))}
			</section>
		</div>
	);
}
