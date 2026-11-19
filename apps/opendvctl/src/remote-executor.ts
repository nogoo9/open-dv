/**
 * REST & SSE Execution Coordinator (`RemoteExecutor`).
 *
 * Implements:
 * - Mandatory API endpoint verification (REQ-CFG-01).
 * - Contract version guard against `GET /api/v1/health` (REQ-FAIL-01).
 * - Decision D1 inline uploads attached to text contract requests (skipped for binary Studio uploads).
 * - Decision D2 async pipeline runs & Studio jobs (`--wait` polling / `--stream` SSE).
 * - Command dispatch across all platform API endpoints.
 */

import { CliDomainError, errorHandler } from "./error-handler";
import { processInlineUploads } from "./inline-uploader";
import { isClientUploadableFile, isLocalSqliteFile, stageCasBlob } from "./remote/cas";
import { RemoteHttpClient } from "./remote/http-client";
import { handleOntology } from "./remote/ontology";
import {
	handleCompile,
	handleHold,
	handlePipeline,
	handleProfile,
	handleRun,
	handleSandbox,
	handleVerify,
} from "./remote/pipeline";
import { handleConfig, handleSource } from "./remote/source";
import { handleStudioDeltas } from "./remote/studio-deltas";
import { handleStudioProposalsAndBundles } from "./remote/studio-proposals";
import { handleStudioSessions } from "./remote/studio-sessions";
import { handleStudioWizard, withWizardExitCode } from "./remote/studio-wizard";
import type { RemoteExecutorConfig } from "./remote/types";
import {
	type CliCommand,
	type CliOption,
	type CliSubcommand,
	loadCliSpec,
} from "./spec";

export {
	ADMIN_ROLE_NAMES,
	DESTRUCTIVE_OP_TYPES,
	MIN_RATIONALE_LENGTH,
	type RemoteExecutorConfig,
	computeCanonicalAnswersSha256,
	isDestructiveDeltaOperation,
	normalizeDeltaOperations,
	normalizeOpType,
	normalizeStringArrayOption,
} from "./remote/types";

export class RemoteExecutor extends RemoteHttpClient {
	constructor(config: RemoteExecutorConfig = {}) {
		super(config);
	}

	isClientUploadableFile(uriOrPath: string): boolean {
		return isClientUploadableFile(uriOrPath);
	}

	isLocalSqliteFile(uriOrPath: string): boolean {
		return isLocalSqliteFile(uriOrPath);
	}

	async stageCasBlob(filePathOrUri: string): Promise<string> {
		return stageCasBlob(this, filePathOrUri);
	}

	async execute(
		command: string,
		subcommand: string | undefined,
		rawOptions: Record<string, unknown>,
	): Promise<{ exitCode: number; payload: unknown }> {
		// 1. Contract version guard
		await this.verifyContractVersion();

		// 2. Skip text `processInlineUploads` on `ontology studio` commands (`MAJ-04`)
		// so binary PDFs, Parquet, Office, and .odvstudio.zip archives are never UTF-8 decoded.
		const isStudioCommand =
			command === "ontology" &&
			Boolean(subcommand && subcommand.toLowerCase().startsWith("studio"));

		let enrichedOptions: Record<string, unknown>;
		if (isStudioCommand) {
			enrichedOptions = { ...rawOptions };
		} else {
			const spec = loadCliSpec();
			const cmdDef = spec.commands.find((c) => c.name === command);
			const specOptions: CliOption[] = [...spec.global_options];
			if (cmdDef?.options) {
				specOptions.push(...cmdDef.options);
			}
			if (subcommand && cmdDef?.subcommands) {
				const subTokens = subcommand.split(" ");
				let currentCmd: CliCommand | CliSubcommand | undefined = cmdDef;
				for (const tok of subTokens) {
					if (currentCmd?.subcommands) {
						const found: CliSubcommand | undefined =
							currentCmd.subcommands.find(
								(s) => s.name === tok || (s.aliases && s.aliases.includes(tok)),
							);
						if (found) {
							if (found.options) {
								specOptions.push(...found.options);
							}
							currentCmd = found;
						} else {
							break;
						}
					}
				}
			}
			enrichedOptions = await processInlineUploads(rawOptions, specOptions);
		}

		let payload: unknown;

		switch (command) {
			case "profile":
				payload = await handleProfile(this, enrichedOptions);
				break;
			case "compile":
				payload = await handleCompile(this, enrichedOptions);
				break;
			case "verify":
				payload = await handleVerify(this, enrichedOptions);
				break;
			case "sandbox":
				payload = await handleSandbox(this, enrichedOptions);
				break;
			case "pipeline":
				payload = await handlePipeline(this, subcommand, enrichedOptions);
				break;
			case "run":
				if (subcommand === "dv-schema") {
					try {
						payload = await handleRun(this, subcommand, enrichedOptions);
					} catch (err) {
						withWizardExitCode(err);
					}
				} else {
					payload = await handleRun(this, subcommand, enrichedOptions);
				}
				break;
			case "hold":
				payload = await handleHold(this, subcommand, enrichedOptions);
				break;
			case "ontology": {
				if (!subcommand) {
					throw new CliDomainError(
						"INVALID_REQUEST",
						"Missing ontology subcommand (suggest, validate-delta, diff, accept, reject, studio)",
						400,
					);
				}
				const sub = subcommand.toLowerCase();
				if (sub.startsWith("studio")) {
					const studioSub = sub.replace(/^studio\s*/, "").trim();
					if (
						studioSub === "attach-source" ||
						studioSub === "fibo-qa" ||
						studioSub === "logs"
					) {
						try {
							const wizRes = await handleStudioWizard(
								this,
								studioSub,
								enrichedOptions,
							);
							payload = wizRes.payload;
						} catch (err) {
							withWizardExitCode(err);
						}
					} else {
						const sessRes = await handleStudioSessions(
							this,
							studioSub,
							enrichedOptions,
						);
						if (sessRes.handled) {
							payload = sessRes.payload;
						} else {
							const deltaRes = await handleStudioDeltas(
								this,
								studioSub,
								enrichedOptions,
							);
							if (deltaRes.handled) {
								payload = deltaRes.payload;
							} else {
								const propRes = await handleStudioProposalsAndBundles(
									this,
									studioSub,
									enrichedOptions,
								);
								payload = propRes.payload;
							}
						}
					}
				} else {
					payload = await handleOntology(this, subcommand, enrichedOptions);
				}
				break;
			}
			case "source":
				payload = await handleSource(this, subcommand, enrichedOptions);
				break;
			case "config":
				return handleConfig(this, subcommand, enrichedOptions);
			default:
				throw new CliDomainError(
					"INVALID_REQUEST",
					`Unrecognized command: ${command}`,
					400,
				);
		}

		let exitCode = errorHandler.resolvePayloadExitCode(payload);
		if (
			exitCode === 0 &&
			typeof payload === "object" &&
			payload !== null &&
			(payload as Record<string, unknown>).passed === false
		) {
			exitCode = 1;
		}
		return { exitCode, payload };
	}
}
