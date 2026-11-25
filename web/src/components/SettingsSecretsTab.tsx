import { useState } from "react";
import { api, buildSourceInspectReq, parseProblemDetails } from "../api/client";
import type {
	ConnectorConfigPayload,
	SecretSummary,
	WorkspaceSettings,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";

export interface SettingsSecretsTabProps {
	draft: WorkspaceSettings;
	secrets: SecretSummary[];
	onRefreshSecrets: () => Promise<void>;
	onChangeDraft: (next: WorkspaceSettings) => void;
}

export function SettingsSecretsTab({
	draft,
	secrets,
	onRefreshSecrets,
	onChangeDraft,
}: SettingsSecretsTabProps) {
	const setLastProblem = useProjectStore((s) => s.setLastProblem);

	const [secretKey, setSecretKey] = useState("");
	const [secretVal, setSecretVal] = useState("");
	const [profName, setProfName] = useState("");
	const [profAdapter, setProfAdapter] = useState("databricks_uc");
	const [profUri, setProfUri] = useState("");
	const [profSecretRef, setProfSecretRef] = useState("");
	const [testResult, setTestResult] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);

	const saveSecret = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!secretKey.trim() || !secretVal || busy) return;
		setBusy(true);
		try {
			await api.postSecret({ key: secretKey.trim(), value: secretVal });
			setSecretKey("");
			setSecretVal("");
			await onRefreshSecrets();
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	const removeSecret = async (key: string) => {
		if (busy) return;
		setBusy(true);
		try {
			await api.deleteSecret(key);
			await onRefreshSecrets();
		} catch (err) {
			setLastProblem(parseProblemDetails(err));
		} finally {
			setBusy(false);
		}
	};

	const addProfile = (e: React.FormEvent) => {
		e.preventDefault();
		if (!profName.trim() || !profUri.trim()) return;
		const payload: ConnectorConfigPayload = {
			adapter_id: profAdapter,
			display_name: profName.trim(),
			uri_or_path: profUri.trim(),
			options: {},
			secret_refs: profSecretRef
				.split(",")
				.map((s) => s.trim())
				.filter(Boolean),
			profile_timeout_seconds: 30,
		};
		onChangeDraft({
			...draft,
			connection_profiles: {
				...(draft.connection_profiles ?? {}),
				[profName.trim()]: payload,
			},
		});
		setProfName("");
		setProfUri("");
		setProfSecretRef("");
	};

	const testProfile = async (name: string, p: ConnectorConfigPayload) => {
		setBusy(true);
		setTestResult(null);
		try {
			if ((p.secret_refs ?? []).length > 0) {
				const checked = await api.listSecrets(p.secret_refs);
				const missing = checked.filter((s) => !s.present).map((s) => s.key);
				if (missing.length > 0) {
					setTestResult(`Missing secret(s) for ${name}: ${missing.join(", ")}`);
					return;
				}
			}
			const req = buildSourceInspectReq({
				kind: p.adapter_id,
				uri_or_path: p.uri_or_path,
				options: p.options,
				secret_refs: p.secret_refs,
			});
			const prof = await api.inspectSource(req);
			setTestResult(
				`Connected ${name}: ${prof.table_count} table(s) discovered (fingerprint ${prof.schema_fingerprint.slice(0, 10)}).`,
			);
		} catch (err) {
			const pd = parseProblemDetails(err);
			setTestResult(`Connection failed (${pd.error_code}): ${pd.detail}`);
		} finally {
			setBusy(false);
		}
	};

	const profiles = Object.entries(draft.connection_profiles ?? {});

	return (
		<div className="stack-md">
			<div className="banner row-between">
				<div>
					<strong>Secret Backend:</strong>{" "}
					<span className="pill mono">{draft.secret_backend}</span> ·{" "}
					<span>
						Keyring entries are scoped by SHA-256 digest of{" "}
						<code>OPENDV_HOME</code> to prevent cross-workspace bleed.
					</span>
				</div>
				<label className="row text-sm">
					<span>Precedence:</span>
					<select
						className="select"
						style={{ width: "auto" }}
						value={draft.secret_precedence}
						onChange={(e) =>
							onChangeDraft({
								...draft,
								secret_precedence: e.target
									.value as WorkspaceSettings["secret_precedence"],
							})
						}
					>
						<option value="store_first">
							store_first (Project &gt; User &gt; Env)
						</option>
						<option value="env_first">env_first</option>
					</select>
				</label>
			</div>

			{testResult && <output className="banner banner-ok">{testResult}</output>}

			<div className="split-grid">
				<section className="card-subtle stack-sm">
					<h2 className="h2-serif">Write-Only Secrets Store</h2>
					<form className="stack-sm" onSubmit={saveSecret}>
						<div className="grid-2">
							<input
								type="text"
								className="input mono"
								placeholder="Secret key (e.g. DATABRICKS_TOKEN)"
								value={secretKey}
								onChange={(e) => setSecretKey(e.target.value)}
							/>
							<input
								type="password"
								className="input"
								placeholder="Write-only secret value"
								value={secretVal}
								onChange={(e) => setSecretVal(e.target.value)}
							/>
						</div>
						<button
							type="submit"
							className="btn btn-primary btn-sm"
							disabled={busy || !secretKey.trim() || !secretVal}
						>
							Store Secret
						</button>
					</form>

					<div className="stack-sm">
						{secrets.length === 0 ? (
							<p className="text-sm text-muted">No stored secrets listed.</p>
						) : (
							secrets.map((sec) => (
								<div key={sec.key} className="card-cream row-between">
									<div>
										<strong className="mono">{sec.key}</strong>{" "}
										<span className="pill">
											{sec.present ? sec.backend : "unset"}
										</span>
									</div>
									<button
										type="button"
										className="btn btn-danger btn-sm"
										disabled={busy}
										onClick={() => void removeSecret(sec.key)}
									>
										Delete
									</button>
								</div>
							))
						)}
					</div>
				</section>

				<section className="card-subtle stack-sm">
					<h2 className="h2-serif">Connection Profiles ({profiles.length})</h2>
					<form className="stack-sm" onSubmit={addProfile}>
						<div className="grid-2">
							<input
								type="text"
								className="input"
								placeholder="Profile name"
								value={profName}
								onChange={(e) => setProfName(e.target.value)}
							/>
							<select
								className="select"
								value={profAdapter}
								onChange={(e) => setProfAdapter(e.target.value)}
							>
								<option value="databricks_uc">databricks_uc</option>
								<option value="sqlite">sqlite</option>
								<option value="duckdb">duckdb</option>
								<option value="parquet">parquet</option>
								<option value="csv">csv</option>
								<option value="xlsx">xlsx</option>
							</select>
						</div>
						<input
							type="text"
							className="input mono"
							placeholder="URI, Databricks host, or workspace path"
							value={profUri}
							onChange={(e) => setProfUri(e.target.value)}
						/>
						<input
							type="text"
							className="input mono"
							placeholder="Comma-separated secret_refs (e.g. DATABRICKS_TOKEN)"
							value={profSecretRef}
							onChange={(e) => setProfSecretRef(e.target.value)}
						/>
						<button
							type="submit"
							className="btn btn-secondary btn-sm"
							disabled={!profName.trim() || !profUri.trim()}
						>
							Stage Connection Profile
						</button>
					</form>

					{profiles.map(([name, prof]) => (
						<div key={name} className="card-cream row-between">
							<div>
								<div className="font-semibold">
									{name}{" "}
									<span className="mono text-sm">({prof.adapter_id})</span>
								</div>
								<div className="mono text-sm text-muted">
									{prof.uri_or_path}
								</div>
							</div>
							<div className="row">
								<button
									type="button"
									className="btn btn-sm"
									disabled={busy}
									onClick={() => void testProfile(name, prof)}
								>
									Test Connection
								</button>
								<button
									type="button"
									className="btn btn-danger btn-sm"
									onClick={() => {
										const next = { ...(draft.connection_profiles ?? {}) };
										delete next[name];
										onChangeDraft({ ...draft, connection_profiles: next });
									}}
								>
									Remove
								</button>
							</div>
						</div>
					))}
				</section>
			</div>
		</div>
	);
}
