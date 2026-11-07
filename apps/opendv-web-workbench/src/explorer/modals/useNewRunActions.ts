import type {
	PipelineRunHandle,
	PipelineRunRequest,
	PipelineValidateRequest,
} from "@opendv/protocol-ts";
import type { StewardHoldPolicyOption } from "./NewRunFormFields";
import type { PipelineValidationReport } from "./NewRunValidationSection";

export interface ValidateActionParams {
	specPath: string;
	specFileName?: string | undefined;
	specFileContent?: string | undefined;
	inputSpec?: Record<string, unknown> | undefined;
	contractPath: string;
	contractFileContent?: string | undefined;
	waveId: string;
	holdPolicy: StewardHoldPolicyOption;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	validatePipelineFn?:
		| ((req: PipelineValidateRequest) => Promise<PipelineValidationReport>)
		| undefined;
}

function parseJsonSpecIfPossible(
	specFileContent?: string | undefined,
): Record<string, unknown> | undefined {
	if (!specFileContent) return undefined;
	const trimmed = specFileContent.trim();
	if (!trimmed.startsWith("{")) return undefined;
	try {
		const parsed = JSON.parse(trimmed) as unknown;
		if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
			return parsed as Record<string, unknown>;
		}
	} catch {
		// Ignore non-JSON content (e.g., raw YAML)
	}
	return undefined;
}

export async function executeValidate(
	params: ValidateActionParams,
): Promise<PipelineValidationReport> {
	const {
		specPath,
		specFileContent,
		inputSpec,
		contractPath,
		contractFileContent,
		waveId,
		holdPolicy,
		apiUrl,
		fetchImpl,
		validatePipelineFn,
	} = params;

	const parsedSpec = parseJsonSpecIfPossible(specFileContent);
	const req: PipelineValidateRequest = {
		input_spec: inputSpec ?? parsedSpec ?? null,
		input_spec_content: specFileContent || null,
		input_spec_path: specPath.trim() || null,
		initial_contract_path: contractPath.trim() || null,
		initial_contract_content: contractFileContent || null,
		initial_contract: contractFileContent || null,
		wave_id: waveId || null,
		steward_hold_policy: holdPolicy,
	};

	if (validatePipelineFn) {
		return await validatePipelineFn(req);
	}

	const fetchFn =
		fetchImpl ??
		(typeof globalThis.fetch === "function"
			? globalThis.fetch.bind(globalThis)
			: undefined);
	const base = (apiUrl ?? "").replace(/\/+$/, "");

	if (fetchFn) {
		const resp = await fetchFn(`${base}/api/v1/pipelines/validate`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json",
				"X-OpenDV-Role": "INTERNAL_MODELER",
			},
			body: JSON.stringify(req),
		});
		if (resp.ok) {
			const data = (await resp.json()) as PipelineValidationReport;
			return {
				stage: data.stage ?? "INPUT_VALIDATION",
				passed: Boolean(data.passed),
				checked_items_count:
					data.checked_items_count ?? data.diagnostics?.length ?? 0,
				diagnostics: data.diagnostics ?? [],
			};
		}
		const errJson = (await resp.json().catch(() => ({}))) as {
			detail?: string;
			error_code?: string;
		};
		return {
			stage: "INPUT_VALIDATION",
			passed: false,
			checked_items_count: 1,
			diagnostics: [
				{
					code: errJson.error_code ?? "VALIDATION_FAILED",
					severity: "ERROR",
					message: errJson.detail ?? `HTTP ${resp.status} validation failure`,
				},
			],
		};
	}

	return {
		stage: "INPUT_VALIDATION",
		passed: false,
		checked_items_count: 1,
		diagnostics: [
			{
				code: "BACKEND_UNAVAILABLE",
				severity: "ERROR",
				message:
					"Platform API is unavailable: cannot validate pipeline spec offline.",
			},
		],
	};
}

export interface SubmitRunActionParams {
	specPath: string;
	specFileContent?: string | undefined;
	inputSpec?: Record<string, unknown> | undefined;
	contractPath: string;
	contractFileContent?: string | undefined;
	waveId: string;
	holdPolicy: StewardHoldPolicyOption;
	apiUrl?: string | undefined;
	fetchImpl?: typeof fetch | undefined;
	createRunFn?:
		| ((req: PipelineRunRequest) => Promise<PipelineRunHandle>)
		| undefined;
}

export async function executeSubmitRun(
	params: SubmitRunActionParams,
): Promise<PipelineRunHandle> {
	const {
		specPath,
		specFileContent,
		inputSpec,
		contractPath,
		contractFileContent,
		waveId,
		holdPolicy,
		apiUrl,
		fetchImpl,
		createRunFn,
	} = params;

	const parsedSpec = parseJsonSpecIfPossible(specFileContent);
	const req: PipelineRunRequest = {
		dry_run: false,
		input_spec: inputSpec ?? parsedSpec ?? null,
		input_spec_content: specFileContent || null,
		input_spec_path: specPath.trim() || null,
		initial_contract_path: contractPath.trim() || null,
		initial_contract_content: contractFileContent || null,
		initial_contract: contractFileContent || null,
		wave_id: waveId || null,
		steward_hold_policy: holdPolicy,
	};

	if (createRunFn) {
		return await createRunFn(req);
	}

	const fetchFn =
		fetchImpl ??
		(typeof globalThis.fetch === "function"
			? globalThis.fetch.bind(globalThis)
			: undefined);
	const base = (apiUrl ?? "").replace(/\/+$/, "");
	if (!fetchFn) {
		throw new Error("No fetch implementation available to submit run");
	}

	const resp = await fetchFn(`${base}/api/v1/pipelines/runs`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-OpenDV-Role": "INTERNAL_MODELER",
		},
		body: JSON.stringify(req),
	});

	if (resp.status !== 202 && !resp.ok) {
		const errData = (await resp.json().catch(() => ({}))) as {
			detail?: string;
		};
		throw new Error(
			errData.detail ?? `Pipeline run initiation rejected: HTTP ${resp.status}`,
		);
	}

	return (await resp.json()) as PipelineRunHandle;
}
