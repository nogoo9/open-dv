# Harness Readiness Diagnostics & CLI Configuration Parity

* **Author:** Eterna2
* **Status:** RFC (Brainstorm Brief)
* **Date:** 2026-11-25

## Overview

Open-DV V2 supports four LLM harnesses (`agy`, `claude-cli`, `claude`, and `gemini`), yet neither Web Settings (`/settings`) nor the `opendv` CLI tells operators or external agents which harnesses are usable before a Step 4 or Step 6 run fails. Several non-UI settings—budgets, `model_overrides`, `connection_profiles`, project settings updates, and workspace trust revocation—also lack CLI parity or stay unwired at runtime, while CLI errors give no structured distinction between fixes an agent can run directly and actions requiring a human in the loop.

This finalized brainstorm brief locks the requirements, security boundaries, runtime fixes, and trade-offs resolved across a 7-lens Goldfish review and author review. Because Open-DV V2 is pre-release, clean-sheet contract and schema changes require no backward-compatibility shims.

## Analysis

Combining codebase audit findings with author decisions establishes six architectural requirements:

1. **User-Global Secrets & Keyring Key Discovery (`src/opendv/service/secrets.py`):**
   - Because Open-DV does not yet support multi-user tenancy, all stored secrets (LLM harness API keys, connector credentials, external MCP tokens) move from workspace-digest namespacing (`opendv:{sha256(OPENDV_HOME)}`) to user-global scope (`opendv`), matching global `settings.json` and `mcp_servers.json` under `<OS_USER_STATE>/opendv/`.
   - Because the product is pre-release, no legacy digest-keyed secret migration path is needed; tests must isolate strictly via mock keyring or temporary `state_dir` paths so test runs never touch real user credentials.
   - Persist a non-secret keyring key-name manifest (`keys` only, zero values, written with `owner_only=True` alongside `secrets.json`) so `opendv secrets list` and Web Settings Tab 2 retain keyring keys across restarts.
   - `SubprocessHarnessRunner` must receive optional API keys resolved from `KeyringSecretStore` / `.env` via `build_isolated_child_env(..., api_key=...)` when present, while continuing to allow interactive CLI logins for `agy` and `claude-cli` when no API key is configured.
2. **Scoped Read-Only `.env` Loading (`env_file` Setting):**
   - `WorkspaceSettings` adds `env_file: str | None = ".env"` (defaults to `.env`; nullable or clearable via `--clear env_file` to disable).
   - **Injection defense:** Never call `load_dotenv()` into `os.environ`. Parse `env_file` into a read-only in-memory dictionary limited strictly to secret keys and model-override keys—blocking executable paths (`OPENDV_*_CLI_BIN`), `OPENDV_ALLOW_REMOTE_UNAUTHENTICATED`, `OPENDV_API_URL`, and SDK proxy/base-URL overrides (`HTTPS_PROXY`, `ANTHROPIC_BASE_URL`, `GOOGLE_GEMINI_BASE_URL`).
   - **Precedence:** Real process environment variables always outrank `env_file` (`os.environ > .env` within the environment layer): under `store_first`, lookup order is `keyring/user_state_file > os.environ > env_file`; under `env_first`, lookup order is `os.environ > env_file > keyring/user_state_file`.
3. **Global Credentials vs. Project-Specific Execution Settings & Dead-Knob Wiring:**
   - Credentials, `secret_backend`, `env_file`, MCP registry, and harness readiness are global to the user; per-project MCP enable/disable toggling is deferred to a future feature request.
   - Execution settings (`harness_provider`, `cegar_policy`, `v7_critic_tier`, `dbt_sql_dialect`, `model_overrides`, `max_turns`, `max_tokens`, `timeout_seconds`, `secret_precedence`, `discovery_mode`) remain project-scoped in `ProjectSettingsSnapshot` (seeded from global `WorkspaceSettings` on `init_project`).
   - **Dead-knob fixes required at runtime:** Add `model_overrides` to `ProjectSettingsSnapshot`, wire Step 4 (`_discovery_run.py`) and Step 6 (`vault_svc.py`) to apply snapshot `model_overrides`, budget limits, and `v7_critic_tier`, add `PATCH /api/v1/projects/{project_id}/settings` + `opendv project settings {show,set}` so existing projects can be updated after creation, and label global `/settings` execution fields clearly as defaults for new projects.
4. **Two-Tier Per-Provider Readiness, Low-Privilege Compatibility & Lease Mitigations:**
   - Persist non-secret per-provider readiness (`last_verified_at`, `last_auth_error_at`, scrubbed `last_auth_error_summary`, `verified_credential_source`, `verified_binary_path`) in `<OS_USER_STATE>/opendv/readiness.json` using standard atomic writes (`owner_only=False`) so low-privilege corporate Windows laptops (extra GPO/EDR SIDs) and shared-group Linux servers never fail strict ACL verification, while keeping strict `owner_only=True` (`0o600`/user-SID DACL) on `secrets.json`.
   - **Tier 1 (`< 50ms`, offline, 0 tokens):** Reuses `resolve_cli_binary`, `load_optional_sdk_module`, and `KeyringSecretStore` + `env_file`. Missing API keys do **not** block `agy` or `claude-cli` when their binaries are present; missing `--extra sdk` (`anthropic`, `google-genai`) or missing matching keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`) mark `claude` / `gemini` as `MISSING_PREREQ`. Valid prerequisites without a verification timestamp yield `CONFIGURED_UNVERIFIED`. Any key mutation, precedence/backend change, `env_file` change, or resolved binary change invalidates `VERIFIED` back to `CONFIGURED_UNVERIFIED` or `MISSING_PREREQ`.
   - **Passive Updates & Long-Turn Lease Heartbeats:** Update `VERIFIED` (`last_verified_at`) only after an `AgentTurnResult` completes (never on `RUNNING` startup), and downgrade immediately to `AUTH_FAILED` on `AGENT_NOT_CONFIGURED` or `401`/`403` errors across stderr, JSON `is_error`, and SDK exceptions. Schedule an async `lease_heartbeat` task calling existing `renew_lease_row` (`_state_leases.py:190`) every `40s` (`ttl=120s`) during Step 4/6 execution so turns longer than 2 minutes keep cross-process probe suppression accurate; suppress manual checks while an active lease exists with `409 PROJECT_JOB_ACTIVE`.
   - **Opt-In Manual Checks (`POST` endpoint + `opendv doctor --probe`):** Never auto-run live checks on `/settings` mount, `GET /health`, or bare `doctor`. Web `/settings` shows `.pill-ok` / `.pill-warn` / `.pill-err` badges, displays an unverified warning only on the selected provider, disables manual `"Test"` during active runs, and sends `{ provider }` (`Content-Type: application/json`). For `claude`/`gemini` SDKs, use zero-token `models.list()` checks; for `agy`/`claude-cli`, use a zero-token auth-status subcommand if supported (`claude auth status --json`), otherwise a bounded single-token minimal prompt (`max_turns=1`, isolated empty cwd, zero tools, `15s` timeout). Scrub credential tokens (`sk-*`, `AIza*`, `dapi*`, `Bearer *`) in `_pii_redact.py` before persisting or displaying errors.
5. **Structured Remediation Contract (`ProblemDetails.remediation`) & `open-dv-workflow` Adoption:**
   - Keep `ErrorCode` locked at `34` codes (`scripts/check_error_codes.py` untouched). Add optional `remediation: ErrorRemediation | None` (`StrictModel` with `summary` and `actions`: `actor: "agent" | "human"`, `kind`, `label`, `command`, `requires_tty`) onto `ProblemDetails` in `models/errors.py`.
   - Update `web/src/api/client.ts` (`createProblemDetails` field whitelist), `schemas/v2/openapi.json`, `web/src/api/types.generated.ts`, `cli/_output.py` (rendering remediation in both `--json CliEnvelope` and plain stderr), and `opendv mcp serve`. Commands suggested for humans use `--prompt` or interactive CLI logins (`requires_tty: true`) with zero secret values in text or argv. Update `.agents/skills/open-dv-workflow/` docs and scripts to use this contract cleanly and fix existing `claude-cli` / `--v7-critic-tier` typos.
6. **Ergonomic Full-Parity CLI Surface & `< 5,000`-Token Split:**
   - Extract settings CLI commands from `src/opendv/cli/commands_core.py` (`4,396` tokens) into `src/opendv/cli/_commands_settings.py` so every module stays well under `5,000` `cl100k_base` tokens.
   - Extend `settings set` with native enum `choices` in `--help`, `--env-file`, `--max-turns`, `--max-tokens`, `--timeout-seconds`, repeatable `--model-override TARGET=MODEL`, repeatable `--clear`, and safe `strict` handling (auto-promoting `v7_critic_tier = FRONTIER_CRITIC` when omitted, while failing closed on explicit conflicting `--v7-critic-tier STANDARD_WORKER`).
   - Add `--revoke` to `trust-workspace`, add `settings profile {list,set,delete,test}` (enforcing non-secret checks on `--options-json`), add `opendv project settings {show,set}`, and update `opendv doctor` (checking `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `--extra sdk`, server-side readiness when `--api-url` is used, and opt-in `--probe`). Regenerate `cli-spec.json`, `openapi.json`, `docs/16-cli-reference.md`, and update doc subcommand counts.

## Goals vs. Non-Goals

* **In-Scope Goals:**
  * User-global secrets (`KeyringSecretStore` + persistent key-name manifest) and scoped in-memory `env_file` loading (`os.environ > .env`, no arbitrary binary/proxy injection).
  * Shared Tier-1 prerequisite checks and workspace/server-safe readiness tracking (`readiness.json`, `owner_only=False`), passive verification after real completed turns, `40s` lease heartbeat renewal on long Step 4/6 runs, active-run probe suppression (`409 PROJECT_JOB_ACTIVE`), and opt-in manual live auth checks (zero-token SDK metadata or single-token bounded CLI probe).
  * Secret-value scrubbing in `_pii_redact.py`, subprocess optional key injection, unified auth failure classification, and structured `ProblemDetails.remediation` separating agent commands from human-in-the-loop actions across REST, CLI (`--json` + plain stderr), MCP, Web UI, and `open-dv-workflow`.
  * Full CLI parity for non-UI settings (`_commands_settings.py`), `project settings {show,set}` + `PATCH /api/v1/projects/{id}/settings`, and Step 4/6 runtime wiring for budgets, `model_overrides`, and `v7_critic_tier`, all `< 5,000` `cl100k_base` tokens per module.
* **Out-of-Scope Non-Goals:**
  * Per-project MCP server enable/disable overrides (deferred to a future feature request; MCP registry stays user-global).
  * Multi-user tenancy, authentication, or legacy V1/pre-release secret migration shims.
  * Auto-running live LLM checks on `/settings` load, `GET /health`, or bare `opendv doctor`.
  * Interactive CLI setup wizards (`opendv setup`) or mutating `os.environ` from `.env` files.
  * Adding new `ErrorCode` enum values beyond the `34` closed V2 codes.
  * Mirroring browser-only UI state (`Guided ⇄ Observatory`, viewport preferences) into CLI commands.

## Alternatives Weighed

1. **User-global secrets vs. workspace-digest secret isolation:** Rejected by author: keeping per-workspace secret namespaces forces operators to re-enter LLM keys across workspaces when single-user mode is active. Moving to user scope eliminates duplicate key setup and cross-workspace readiness bleed (requiring isolated mock/state-dir fixtures in tests).
2. **Strict `owner_only` ACLs on `readiness.json` vs. standard atomic writes:** Rejected by author: strict Win32 SID DACL / `0o600` checks on non-secret `readiness.json` can break locked-down corporate Windows machines (EDR/GPO SIDs) and shared-group Linux/container mounts without protecting any secret data. Keep strict `owner_only=True` exclusively on `secrets.json`.
3. **Mutating `os.environ` via implicit `load_dotenv()` vs. scoped read-only `env_file` map:** Direct `load_dotenv()` allows untrusted repositories to override CLI binary paths (`OPENDV_*_CLI_BIN`), server security flags, or SDK proxy URLs (`HTTPS_PROXY`, `ANTHROPIC_BASE_URL`). Parsing only allowed secret and model keys into a read-only dictionary below real `os.environ` eliminates execution hijack while keeping `.env` convenience.
4. **Persisting readiness in `settings.json` or memory vs. dedicated `<OS_USER_STATE>/opendv/readiness.json`:** `settings.json` is overwritten wholesale by Web UI `PUT /api/v1/settings`, which would clobber timestamps; process memory fails across CLI invocations and restarts.
5. **CLI-only remediation hints vs. shared `ProblemDetails.remediation`:** CLI-only `CliEnvelope` fields omit plain stderr callers, MCP `ToolError`, and Web UI recovery banners; attaching optional `remediation` to `ProblemDetails` keeps the 34-code catalog intact while serving all transports.
6. **Raw JSON strings on `settings set` vs. extracted `_commands_settings.py` subcommands (`settings profile`, `project settings`):** Raw JSON blobs fail quoting across Windows/bash shells, while cramming flags into `commands_core.py` (`4,396` tokens) violates the `5,000`-token ceiling.
7. **Auto-probing on page load or stopping CLI readiness at Tier 1 vs. passive timestamps + opt-in manual zero/single-token probes:** Auto-probing wastes quota and adds startup delay; skipping manual probes leaves expired CLI sessions undetected until Step 4/6 starts.

## Key Decisions & Staging for Next Steps

`/egm-draft` should specify exact contracts, schemas, module splits, and verification gates for these open technical details:

1. **CLI Binary & SDK Probe Behavior Check:** Inspect installed `agy` (`1.5.14`) and `claude` CLI capabilities (`claude auth status --json` verified; inspect `agy models` vs bounded single-token prompt) before locking the exact fallback probe command, and confirm `google-genai` / `anthropic` SDK `models.list()` behavior on valid and invalid keys. Also confirm whether non-`claude` Windows `.cmd` npm shims need generic `.cmd` unwrapping in `unwrap_windows_npm_shim`.
2. **Scoped `env_file` Allowlist & Trust Rule:** Pin the exact allowlist of variable names read from `env_file`, default path resolution (`Path.cwd() / env_file` for relative paths), and error behavior when a user explicitly configures a missing custom `env_file` path vs. absent default `.env`.
3. **Contract & Token Budget Enforcement:** Enumerate exact OpenAPI/CLI spec updates (`schemas/v2/{openapi,cli-spec,baseline-digests}.json`, `docs/16-cli-reference.md`, `web/src/api/types.generated.ts`, `web/src/api/client.ts`), existing test files in `tests/docs/` and `tests/parity/` needing count/scope updates, and file splits keeping every touched `.py` and `.ts/.tsx` module strictly `< 5,000` `cl100k_base` tokens.
