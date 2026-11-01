/**
 * Typed HTTP REST Client (`OpenDVPlatformClient`) for the Headless Open-DV Platform API (`/api/v1/*`).
 */

import type {
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	CreateProjectRequest,
	CreateRunRequest,
	GateScorecardSummaryDTO,
	GateStatus,
	HealthResponseDTO,
	OpenDVClientOptions,
	ProjectDTO,
	ProjectRunDTO,
	SandboxExecutionResponse,
	SandboxQueryRequest,
	WorkbenchViewProjectionDTO,
} from "../types/api-dtos";
import type { WorkbenchViewId } from "../types/observability-event";

export type {
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	CreateProjectRequest,
	CreateRunRequest,
	GateScorecardSummaryDTO,
	GateStatus,
	HealthResponseDTO,
	OpenDVClientOptions,
	ProjectDTO,
	ProjectRunDTO,
	SandboxExecutionResponse,
	SandboxQueryRequest,
	WorkbenchViewProjectionDTO,
};

export class OpenDVPlatformClient {
	private readonly baseUrl: string;
	private readonly headers: Record<string, string>;
	private readonly fetchImpl: typeof fetch;

	constructor(options: OpenDVClientOptions) {
		this.baseUrl = options.baseUrl.replace(/\/+$/, "");
		this.headers = {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-OpenDV-Role": options.rbacRole ?? "INTERNAL_MODELER",
			...options.headers,
		};
		this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
	}

	getBaseUrl(): string {
		return this.baseUrl;
	}

	getHeaders(): Readonly<Record<string, string>> {
		return this.headers;
	}

	async getHealth(): Promise<HealthResponseDTO> {
		const res = await this.fetchImpl(`${this.baseUrl}/api/v1/health`, {
			headers: this.headers,
		});
		if (!res.ok) {
			throw new Error(`Health check failed: HTTP ${res.status}`);
		}
		return (await res.json()) as HealthResponseDTO;
	}

	async listProjects(): Promise<ProjectDTO[]> {
		const res = await this.fetchImpl(`${this.baseUrl}/api/v1/projects`, {
			headers: this.headers,
		});
		if (!res.ok) {
			throw new Error(`Failed to list projects: HTTP ${res.status}`);
		}
		return (await res.json()) as ProjectDTO[];
	}

	async getProject(projectId: string): Promise<ProjectDTO> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/projects/${encodeURIComponent(projectId)}`,
			{ headers: this.headers },
		);
		if (!res.ok) {
			throw new Error(
				`Failed to fetch project ${projectId}: HTTP ${res.status}`,
			);
		}
		return (await res.json()) as ProjectDTO;
	}

	async createProject(request: CreateProjectRequest): Promise<ProjectDTO> {
		const res = await this.fetchImpl(`${this.baseUrl}/api/v1/projects`, {
			method: "POST",
			headers: this.headers,
			body: JSON.stringify(request),
		});
		if (!res.ok) {
			throw new Error(`Failed to create project: HTTP ${res.status}`);
		}
		return (await res.json()) as ProjectDTO;
	}

	async listRuns(): Promise<ProjectRunDTO[]> {
		const res = await this.fetchImpl(`${this.baseUrl}/api/v1/runs`, {
			headers: this.headers,
		});
		if (!res.ok) {
			throw new Error(`Failed to list runs: HTTP ${res.status}`);
		}
		return (await res.json()) as ProjectRunDTO[];
	}

	async createRun(request: CreateRunRequest): Promise<ProjectRunDTO> {
		const res = await this.fetchImpl(`${this.baseUrl}/api/v1/runs`, {
			method: "POST",
			headers: this.headers,
			body: JSON.stringify(request),
		});
		if (!res.ok) {
			throw new Error(`Failed to create run: HTTP ${res.status}`);
		}
		return (await res.json()) as ProjectRunDTO;
	}

	async getRun(runId: string): Promise<ProjectRunDTO> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/runs/${encodeURIComponent(runId)}`,
			{
				headers: this.headers,
			},
		);
		if (!res.ok) {
			throw new Error(`Failed to fetch run ${runId}: HTTP ${res.status}`);
		}
		return (await res.json()) as ProjectRunDTO;
	}

	async getRunGates(runId: string): Promise<Record<string, unknown>> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/runs/${encodeURIComponent(runId)}/gates`,
			{
				headers: this.headers,
			},
		);
		if (!res.ok) {
			throw new Error(`Failed to fetch gates for ${runId}: HTTP ${res.status}`);
		}
		return (await res.json()) as Record<string, unknown>;
	}

	async getRunSandbox(runId: string): Promise<Record<string, unknown>> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/runs/${encodeURIComponent(runId)}/sandbox`,
			{
				headers: this.headers,
			},
		);
		if (!res.ok) {
			throw new Error(
				`Failed to fetch sandbox for ${runId}: HTTP ${res.status}`,
			);
		}
		return (await res.json()) as Record<string, unknown>;
	}

	async getWorkbenchView(
		runId: string,
		viewId: WorkbenchViewId,
	): Promise<WorkbenchViewProjectionDTO> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/runs/${encodeURIComponent(runId)}/workbench/${encodeURIComponent(viewId)}`,
			{
				headers: this.headers,
			},
		);
		if (!res.ok) {
			throw new Error(
				`Failed to fetch workbench view ${viewId} for run ${runId}: HTTP ${res.status}`,
			);
		}
		return (await res.json()) as WorkbenchViewProjectionDTO;
	}

	async submitDecision(
		runId: string,
		decision: CaptainHoldDecisionRequest,
	): Promise<CaptainHoldDecisionResponse> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/runs/${encodeURIComponent(runId)}/decisions`,
			{
				method: "POST",
				headers: this.headers,
				body: JSON.stringify(decision),
			},
		);
		if (!res.ok) {
			throw new Error(`Failed to submit decision: HTTP ${res.status}`);
		}
		return (await res.json()) as CaptainHoldDecisionResponse;
	}

	async getArtifact(sha256Hex: string): Promise<string> {
		const res = await this.fetchImpl(
			`${this.baseUrl}/api/v1/artifacts/${encodeURIComponent(sha256Hex)}`,
			{
				headers: this.headers,
			},
		);
		if (!res.ok) {
			throw new Error(
				`Failed to fetch artifact ${sha256Hex}: HTTP ${res.status}`,
			);
		}
		return res.text();
	}
}
