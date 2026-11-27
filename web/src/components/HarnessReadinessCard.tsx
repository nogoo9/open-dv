import { useState } from "react";
import { api, parseProblemDetails } from "../api/client";
import type {
	HarnessProvider,
	ProblemDetails,
	ProviderReadinessRecord,
	ReadinessResponse,
	WorkspaceSettings,
} from "../api/openapi";
import { useProjectStore } from "../store/projectStore";
import { RemediationList } from "./RemediationList";

const PROVIDERS: readonly HarnessProvider[] = [
	"agy",
	"claude-cli",
	"claude",
	"gemini",
];

const NATIVE_CMD: Record<HarnessProvider, string> = {
	agy: "agy",
	"claude-cli": "claude auth login",
	claude: "uv run opendv secrets set ANTHROPIC_API_KEY --prompt",
	gemini: "uv run opendv secrets set GEMINI_API_KEY --prompt",
};

const pillClass = (status?: ProviderReadinessRecord["status"]) =>
	status === "VERIFIED"
		? "pill-ok"
		: status === "CONFIGURED_UNVERIFIED"
			? "pill-warn"
			: status === "MISSING_PREREQ" || status === "AUTH_FAILED"
				? "pill-err"
				: "";

export interface HarnessReadinessCardProps {
	draft: WorkspaceSettings;
	settings: WorkspaceSettings | null;
	readiness: ReadinessResponse | null;
	onRefreshSecrets: () => Promise<void>;
	onSelectProvider: (next: HarnessProvider) => void;
}

export function HarnessReadinessCard({
	draft,
	settings,
	readiness,
	onRefreshSecrets,
	onSelectProvider,
}: HarnessReadinessCardProps) {
	const fetchReadiness = useProjectStore((s) => s.fetchReadiness);
	const announce = useProjectStore((s) => s.announce);

	const [testing, setTesting] = useState(false);
	const [binding, setBinding] = useState(false);
	const [keyVal, setKeyVal] = useState("");
	const [testProblem, setTestProblem] = useState<ProblemDetails | null>(null);
	const [probed, setProbed] = useState<{
		provider: HarnessProvider;
		record: ProviderReadinessRecord;
		readinessRef: ReadinessResponse | null;
	} | null>(null);

	const byProvider = new Map(
		(readiness?.providers ?? []).map((p) => [p.provider, p]),
	);
	const selectedRecord = byProvider.get(draft.harness_provider) ?? null;
	const activeRecord =
		probed &&
		probed.provider === draft.harness_provider &&
		probed.readinessRef === readiness
			? probed.record
			: selectedRecord;

	const bindKeyName =
		draft.harness_provider === "gemini"
			? "GEMINI_API_KEY"
			: draft.harness_provider === "claude" ||
					draft.harness_provider === "claude-cli"
				? "ANTHROPIC_API_KEY"
				: null;

	const unsavedSecretPolicy = Boolean(
		settings &&
			(draft.secret_precedence !== settings.secret_precedence ||
				draft.secret_backend !== settings.secret_backend),
	);

	const runProbe = async () => {
		if (testing || activeRecord?.active_run) return;
		setTesting(true);
		setTestProblem(null);
		setProbed(null);
		let rec: ProviderReadinessRecord | null = null;
		try {
			rec = await api.testAgentReadiness({ provider: draft.harness_provider });
			announce("polite", `Tested ${draft.harness_provider}: ${rec.status}`);
		} catch (err) {
			setTestProblem(parseProblemDetails(err));
		} finally {
			await fetchReadiness();
			if (rec) {
				setProbed({
					provider: rec.provider,
					record: rec,
					readinessRef: useProjectStore.getState().readiness,
				});
			}
			setTesting(false);
		}
	};

	const bindKey = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!bindKeyName || !keyVal || binding) return;
		setBinding(true);
		setTestProblem(null);
		try {
			await api.postSecret({ key: bindKeyName, value: keyVal });
			setKeyVal("");
			await onRefreshSecrets();
			announce("polite", `Bound ${bindKeyName}`);
		} catch (err) {
			setTestProblem(parseProblemDetails(err));
		} finally {
			setBinding(false);
		}
	};

	const copyNativeCmd = async () => {
		const cmd = NATIVE_CMD[draft.harness_provider];
		try {
			if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
				await navigator.clipboard.writeText(cmd);
			}
			announce("polite", `Copied command: ${cmd}`);
		} catch {
			announce("polite", `Copy failed for command: ${cmd}`);
		}
	};

	return (
		<section className="card-subtle stack-md">
			<div className="row-between">
				<div>
					<h2 className="h2-serif">Harness Readiness Diagnostics</h2>
					<p className="text-sm text-muted">
						Offline Tier-1 status across all 4 providers; explicit Test runs a
						live Tier-2 auth check for the selected provider.
					</p>
				</div>
				<div className="row">
					{draft.harness_provider === "agy" &&
						(activeRecord?.status === "MISSING_PREREQ" ||
							activeRecord?.status === "AUTH_FAILED") && (
							<button
								type="button"
								className="btn btn-secondary btn-sm"
								onClick={() => onSelectProvider("gemini")}
							>
								Switch to gemini
							</button>
						)}
					<button
						type="button"
						className="btn btn-primary btn-sm"
						disabled={testing || Boolean(activeRecord?.active_run)}
						onClick={() => void runProbe()}
					>
						{testing ? "Testing..." : "Test"}
					</button>
				</div>
			</div>

			<div className="grid-2">
				{PROVIDERS.map((prov) => {
					const rec =
						probed &&
						probed.provider === prov &&
						probed.readinessRef === readiness
							? probed.record
							: byProvider.get(prov);
					const missing = rec?.missing_prereqs ?? [];
					return (
						<div key={prov} className="card-cream stack-sm">
							<div className="row-between">
								<div className="row">
									<strong className="mono">{prov}</strong>
									{prov === draft.harness_provider && (
										<span className="pill pill-sal">Selected</span>
									)}
								</div>
								<span className={`pill ${pillClass(rec?.status)}`}>
									{rec?.status ?? "UNLOADED"}
								</span>
							</div>
							{missing.length > 0 && (
								<div className="text-sm text-muted">
									Missing: <span className="mono">{missing.join(", ")}</span>
								</div>
							)}
						</div>
					);
				})}
			</div>

			{unsavedSecretPolicy && (
				<div className="banner banner-warn">
					Secret precedence or backend has unsaved draft changes. Save Settings
					before running a server Test probe.
				</div>
			)}

			{activeRecord?.active_run && (
				<div className="banner banner-warn">
					Active run{" "}
					<span className="mono">{activeRecord.active_run_id ?? "active"}</span>{" "}
					is running on {draft.harness_provider}; Test probes are locked until
					it finishes.
				</div>
			)}

			{activeRecord?.status === "CONFIGURED_UNVERIFIED" && (
				<div className="banner banner-warn">
					<strong className="mono">{draft.harness_provider}</strong>{" "}
					prerequisites are configured, but authentication has not been verified
					yet. Click <strong>Test</strong> to verify credentials.
				</div>
			)}

			{activeRecord && (
				<div className="card-cream stack-sm">
					<div className="row">
						<span className="pill">
							Source: {activeRecord.credential_source}
						</span>
						{activeRecord.binary_path && (
							<span className="pill mono">{activeRecord.binary_path}</span>
						)}
						{activeRecord.sdk_installed !== null &&
							activeRecord.sdk_installed !== undefined && (
								<span className="pill">
									SDK:{" "}
									{activeRecord.sdk_installed ? "installed" : "not installed"}
								</span>
							)}
						{activeRecord.last_verified_at && (
							<span className="pill">
								Verified: {activeRecord.last_verified_at}
							</span>
						)}
						{activeRecord.last_auth_error_at && (
							<span className="pill pill-err">
								{activeRecord.status === "AUTH_FAILED"
									? "Auth error:"
									: "Probe error:"}{" "}
								{activeRecord.last_auth_error_at}
							</span>
						)}
					</div>
					{activeRecord.last_auth_error_summary && (
						<div className="text-sm text-muted">
							{activeRecord.status === "AUTH_FAILED"
								? "Last auth error:"
								: "Last probe error:"}{" "}
							<span className="mono">
								{activeRecord.last_auth_error_summary}
							</span>
						</div>
					)}
					<div className="row-between">
						<span className="text-sm">
							Native CLI:{" "}
							<code
								className="mono"
								style={{ overflowWrap: "anywhere", wordBreak: "break-word" }}
							>
								{NATIVE_CMD[draft.harness_provider]}
							</code>
						</span>
						<button
							type="button"
							className="btn btn-sm"
							onClick={() => void copyNativeCmd()}
						>
							Copy CLI
						</button>
					</div>
					<RemediationList remediation={activeRecord.remediation} />
				</div>
			)}

			{bindKeyName && (
				<form className="row" onSubmit={(e) => void bindKey(e)}>
					<input
						type="password"
						className="input mono"
						style={{ flex: "1 1 220px" }}
						aria-label={`API key for ${bindKeyName}`}
						placeholder={`Paste ${bindKeyName} (write-only)`}
						value={keyVal}
						onChange={(e) => setKeyVal(e.target.value)}
					/>
					<button
						type="submit"
						className="btn btn-secondary btn-sm"
						disabled={binding || !keyVal}
					>
						{binding ? "Binding..." : `Bind ${bindKeyName}`}
					</button>
				</form>
			)}

			{testProblem && (
				<div className="banner banner-danger stack-sm" role="alert">
					<div>
						<strong>{testProblem.title}</strong> ({testProblem.error_code}):{" "}
						{testProblem.detail}
					</div>
					<RemediationList problem={testProblem} />
				</div>
			)}
		</section>
	);
}
