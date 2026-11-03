# 12 — Agentic Pipeline Inputs, Harness Configuration & Authentication Reference Guide (`Claude` vs `Antigravity`)

This document is the authoritative, field-by-field reference guide for configuring and executing the **Open-DV 8-Stage Neuro-Symbolic Agentic Data Vault 2.0 Pipeline** (`packages/opendv-agent-harness`). It covers:

1. **Agent Harness Configuration (`HarnessSetupConfig` / `harness_config.yaml`)**: How to configure **Google Antigravity** (`AgyCliHarnessAdapter` / `AntigravityHarnessAdapter`) vs **Anthropic Claude** (`ClaudeCliHarnessAdapter` / `ClaudeAgentHarnessAdapter`), semantic model-tier routing (`reasoning` vs `fast`), fine-grained per-agent model overrides (`agent_models`), and **API / Cloud Authentication mechanisms** (`HarnessAuthConfig`: API keys, Google Cloud Vertex AI ADC, AWS Bedrock IAM/SSO, and CLI sessions).
2. **Pipeline Input Specification (`PipelineInputSpec` / `pipeline_input.yaml`)**: Every field of the root pipeline spec, multi-wave execution schedule (`PipelineWaveInput`), relational Bronze source feeds (`PipelineSourceFeedInput`), and span-grounded unstructured documents (`PipelineUnstructuredDocInput` & `PipelineUnstructuredCandidateInput`).
3. **Enterprise Graph A Ontology (`GraphAOntology`) & Seed Contract (`initial_contract_path`)**: Complete schema reference for Core Business Concepts (CBCs), 3-Tier Business Key Authority (`BKCC`), governance constraints, canonical relationships, and baseline `NoMistakeDataVault2ContractV5` contracts.
4. **Deterministic Pre-Flight Input & Post-Flight Output Validation**: Every validation check and diagnostic error code emitted by `validate_pipeline_inputs()` and `validate_pipeline_outputs()`.
5. **Pipeline Output Report (`AgenticPipelineReport`) & Artifacts Catalog**: Field-by-field specification of `pipeline_report.json`, `pipeline_report.md`, `wave_reports`, `step_reports`, `intermediate_artifacts`, and `final_artifacts`.

---

## 1. Agent Harness Configuration (`HarnessSetupConfig` — `harness_config.yaml`)

The Open-DV pipeline decouples its 8-stage neuro-symbolic workflow from any single LLM vendor via the hexagonal `VaultAgentHarness` protocol (`packages/opendv-agent-harness`). You configure the harness declaratively via a YAML/JSON file (`HarnessSetupConfig`) or programmatic overrides.

### 1.1 Top-Level `HarnessSetupConfig` Fields

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `provider` | `"agy" \| "agy-cli" \| "antigravity" \| "claude" \| "claude-cli" \| "claude-code"` | No | `"agy"` | Selects the concrete `VaultAgentHarness` adapter:<ul><li>`"agy"` / `"agy-cli"`: `AgyCliHarnessAdapter` backed by the local logged-in Antigravity Coding CLI (`agy -p --output-format stream-json --json-schema <path>`).</li><li>`"antigravity"`: `AntigravityHarnessAdapter` backed by the Google Antigravity Python SDK (`google.antigravity`) or Gemini REST API (`GEMINI_API_KEY`).</li><li>`"claude"`: `ClaudeAgentHarnessAdapter` backed by the Anthropic Claude Agent SDK (`claude_agent_sdk`) or Anthropic Messages API (`ANTHROPIC_API_KEY`).</li><li>`"claude-cli"` / `"claude-code"`: `ClaudeCliHarnessAdapter` (`ClaudeCodeCliAdapter`) backed by the local logged-in Claude Code CLI (`claude -p --model <id> --output-format stream-json --json-schema <schema> --agents <subagents_json>`).</li></ul>*(Note: `DeterministicHarnessAdapter` and SDK shims exist strictly inside internal `pytest` suites for unit/integration mocking and are rejected by the CLI and API.)* |
| `model_overrides` | `dict[str, str]` | No | `{"reasoning": "gemini-4-argon-high", "fast": "gemini-3.8-flash-high"}` (or `{"reasoning": "claude-opus-4-6", "fast": "claude-haiku-4-5"}` when `provider` is `"claude-cli"` / `"claude-code"`) | Maps semantic model tiers (`reasoning` for `ModelTier.HIGH_REASONING` and `fast` for `ModelTier.FAST_TIER`) to concrete provider model IDs. Legacy keys `argon` and `flash` are rejected during schema validation (`HARNESS_SETUP_INVALID`). |
| `agent_models` | `dict[str, str]` | No | `{}` | Optional fine-grained per-agent model overrides keyed by canonical persona identifier (`"vault-first-mate"`, `"scout-source"`, `"scout-unstructured"`, `"ship-vault"`). Takes highest precedence over `model_overrides[tier]` and provider defaults for both top-level agent tasks and delegated subagents. |
| `auth` | `HarnessAuthConfig` | No | `HarnessAuthConfig(auth_mode="none")` (`auth_mode="cli_session"` when `provider` is `"claude-cli"` / `"claude-code"`) | Declarative authentication and cloud provider routing configuration (API key environment variable lookup, Vertex AI ADC, AWS Bedrock, or CLI session). Secrets are resolved from environment variables at runtime so raw credentials are never committed to YAML files. |
| `budget` | `HarnessBudget` | No | `HarnessBudget()` | Per-task execution guardrails for turns, model calls, tool invocations, input/output tokens, USD cost ceiling, wall-clock timeout, and `@toon_v1` self-healing retries. |
| `sandbox` | `SandboxPolicy` | No | `SandboxPolicy()` | Workspace read/write isolation, allowed directories, shell execution toggle, blocked command substrings, and built-in tool allow/deny lists. |
| `steward_hold_policy` | `"auto_approve_top_candidate" \| "pause" \| "reject_on_hold"` | No | `"auto_approve_top_candidate"` | Controls how `Tier-U3` (`ASK-USER` / `STEWARD_HOLD`) ambiguous entity resolution holds are handled during autonomous pipeline runs:<ul><li>`"auto_approve_top_candidate"`: Automatically approves the highest-scoring candidate Hub match and records an `AUTO_REPAIRED` audit event.</li><li>`"pause"`: Halts the pipeline with `status="PAUSED_HOLD"`, persists a `PENDING` `StewardHoldRecord`, and awaits human Steward resolution via CLI/API/Workbench.</li><li>`"reject_on_hold"`: Rejects the hold immediately (`REJECT-WITH-TRACE`) and fails the wave.</li></ul> |
| `inject_adversarial_fault_on_attempt_1` | `bool` | No | `false` | When `true`, injects a deterministic Attempt $k=1$ Pass 1 column triage omission on Wave 0 so `STAGE_05_AXI_DV_STATIC_GATES` exercises closed-loop `@toon_v1` `REJECT-WITH-TRACE` $\to$ `@ship-vault` self-healing convergence on Attempt $k=2$. Defaults to `false` so production runs execute clean single-pass synthesis unless a real static gate violation occurs. |
| `env` | `dict[str, str]` | No | `{}` | Optional additional environment variables injected into the adapter runtime (`LocalAgentConfig.env` for Antigravity or `ClaudeAgentOptions.env` for Claude). Any keys present in `env` are automatically redacted to `"[REDACTED]"` in `AgenticPipelineReport.harness_setup`. |
| `extra_provider_options` | `dict[str, Any]` | No | `{}` | Optional vendor-specific adapter options forwarded to `HarnessConfig.extra_provider_options`. |

---

### 1.2 Antigravity vs Claude Adapter Comparison, Semantic Model-Tier Routing (`reasoning` & `fast`), & Per-Agent Model Routing (`agent_models`)

Open-DV routes agent personas to two semantic model tiers (`ModelTier`) rather than hardcoding vendor model names in pipeline stages, and supports optional fine-grained per-agent overrides (`agent_models`):

- **`reasoning` (`ModelTier.HIGH_REASONING`):** Used for complex multi-pass architectural reasoning, Graph A domain ontology orchestration (`STAGE_00_ORCHESTRATION` / `vault-first-mate`), 3-Pass Chain-of-Thought Data Vault 2.0 contract modeling (`STAGE_03_SHIP_VAULT_PASS1` & `STAGE_04_SHIP_VAULT_PASS2` / `ship-vault`), and closed-loop `@toon_v1` self-healing repair (`STAGE_05_AXI_DV_STATIC_GATES` / `ship-vault`).
- **`fast` (`ModelTier.FAST_TIER`):** Used for high-throughput relational Bronze statistical profiling & schema-drift triage (`STAGE_01_SCOUT_SOURCE` / `scout-source`) and unstructured document span extraction & 3-Tier entity resolution triage (`STAGE_02_SCOUT_UNSTRUCTURED` / `scout-unstructured`).

| Semantic Tier (`ModelTier`) | YAML Key (`model_overrides`) | Assigned Agent Personas & Stages | Antigravity (`provider: agy` / `antigravity`) Recommended Override / Default | Claude (`provider: claude` / `claude-cli` / `claude-code`) Recommended Override / Default |
| :--- | :--- | :--- | :--- | :--- |
| **`ModelTier.HIGH_REASONING`** | `reasoning` | <ul><li>`@vault-first-mate` (`STAGE_00_ORCHESTRATION` Graph A domain ontology orchestration)</li><li>`@ship-vault` (`STAGE_03_SHIP_VAULT_PASS1` & `STAGE_04_SHIP_VAULT_PASS2` 3-Pass Chain-of-Thought DV 2.0 contract modeling, plus `STAGE_05_AXI_DV_STATIC_GATES` closed-loop `@toon_v1` self-healing repair)</li></ul> | <ul><li>Sample YAML override (`harness_config.yaml` / `harness_config_agy.yaml` / `harness_config_antigravity.yaml`): `gemini-4-argon-high`</li><li>SDK fallback (`DEFAULT_AGY_MODEL_MAP` / `$OPENDV_AGY_HIGH_MODEL`): `gemini-4-argon-high`</li></ul> | <ul><li>Sample YAML override (`harness_config_claude.yaml` / `harness_config_claude_cli.yaml`): `claude-opus-4-6`</li><li>SDK fallback (`DEFAULT_CLAUDE_MODEL_MAP` / `$OPENDV_CLAUDE_HIGH_MODEL`): `claude-opus-5-5-high`</li></ul> |
| **`ModelTier.FAST_TIER`** | `fast` | <ul><li>`@scout-source` (`STAGE_01_SCOUT_SOURCE` relational Bronze statistical profiling & schema-drift triage)</li><li>`@scout-unstructured` (`STAGE_02_SCOUT_UNSTRUCTURED` unstructured document span extraction & 3-Tier entity resolution triage)</li><li>`@axi-dv` (`STAGE_05_AXI_DV_STATIC_GATES` & `STAGE_06_AXI_DV_DYNAMIC_SANDBOX`)</li><li>`@deterministic-compiler` (`STAGE_07_DETERMINISTIC_COMPILER`)</li></ul> | <ul><li>Sample YAML override (`harness_config.yaml` / `harness_config_agy.yaml` / `harness_config_antigravity.yaml`): `gemini-3.8-flash-high`</li><li>SDK fallback (`DEFAULT_AGY_MODEL_MAP` / `$OPENDV_AGY_FAST_MODEL`): `gemini-3.8-flash-high`</li></ul> | <ul><li>Sample YAML override (`harness_config_claude.yaml` / `harness_config_claude_cli.yaml`): `claude-haiku-4-5` (or `claude-sonnet-4-6`)</li><li>SDK fallback (`DEFAULT_CLAUDE_MODEL_MAP` / `$OPENDV_CLAUDE_FAST_MODEL`): `claude-sonnet-5-5-medium`</li></ul> |

#### Fine-Grained Per-Agent Model Routing (`agent_models`) & 3-Level Resolution Precedence

In addition to tier-level `model_overrides` (`reasoning` and `fast`), `HarnessSetupConfig` supports an optional `agent_models` dictionary keyed by the 4 agent personas (`vault-first-mate`, `scout-source`, `scout-unstructured`, `ship-vault`). When resolving the concrete model ID for any agent task execution, the harness applies a strict **3-level resolution precedence**:

1. **Fine-grained per-agent override (`agent_models[persona]`):** If `agent_models` specifies a model ID for the active persona (`"vault-first-mate"`, `"scout-source"`, `"scout-unstructured"`, or `"ship-vault"`), that exact model ID is used.
2. **Semantic tier override (`model_overrides[tier]`):** Otherwise, if `model_overrides` specifies a model ID for the task's semantic tier (`"reasoning"` for `ModelTier.HIGH_REASONING` or `"fast"` for `ModelTier.FAST_TIER`), that tier override is used.
3. **Provider default model map (`DEFAULT_AGY_MODEL_MAP` / `DEFAULT_CLAUDE_MODEL_MAP`):** Otherwise, the harness falls back to the provider's default model map (`DEFAULT_AGY_MODEL_MAP` for `agy` / `agy-cli` / `antigravity`, or `DEFAULT_CLAUDE_MODEL_MAP` for `claude` / `claude-cli` / `claude-code`).

Every executed step's `PipelineStepReport` records both `model_tier` (`"reasoning"` or `"fast"`) and `model_used` (the resolved concrete model ID).

#### Architectural Differences Between `AntigravityHarnessAdapter` and `ClaudeAgentHarnessAdapter`

| Capability | Google Antigravity (`provider: antigravity`) | Anthropic Claude (`provider: claude`) |
| :--- | :--- | :--- |
| **Underlying Python SDK** | `google.antigravity` (`agy.Agent`, `agy.LocalAgentConfig`), with Gemini REST API fallback when `GEMINI_API_KEY` / `GOOGLE_API_KEY` is configured | `claude_agent_sdk` (`ClaudeSDKClient`, `ClaudeAgentOptions`), with Anthropic Messages REST API fallback when `ANTHROPIC_API_KEY` is configured |
| **Domain Tool Bridge** | Synthesizes typed async Python callables with Google-style docstrings via `to_antigravity_tool_callable()` | Packages domain tools into an in-process MCP server (`mcp__opendv__<tool_name>`) via `build_claude_sdk_mcp_bundle()` |
| **Subagent Delegation** | `agy.types.SubagentConfig` + `CapabilitiesConfig(enable_subagents=True, max_subagent_depth=3)` | `claude_agent_sdk.AgentDefinition` dictionary + `"Agent"` built-in tool enablement |
| **Workspace & Shell Sandboxing** | 9-tier `agy.hooks.policy` rules (`deny_all()`, `workspace_only()`, conditional `deny("run_command")` lambda) | `allowed_tools` / `disallowed_tools` (`Read`, `Glob`, `Grep`, `Write`, `Edit`, `Bash`) + `PreToolUse` `HookMatcher(matcher="Bash")` |
| **Structured Output Enforcement** | `LocalAgentConfig(response_schema=spec.output_schema)` + `await response.structured_output()` | `ClaudeAgentOptions(output_format={"type": "json_schema", "schema": ...})` |
| **Session Resumption (`STEWARD_HOLD`)** | `LocalAgentConfig(conversation_id=spec.resume_session_id)` | `ClaudeAgentOptions(resume=spec.resume_session_id)` |

---

### 1.3 API & Authentication Mechanisms (`auth: HarnessAuthConfig`)

`HarnessAuthConfig` (`harness_setup.auth`) provides a unified, declarative way to configure API keys, cloud identity providers (Google Cloud Vertex AI and AWS Bedrock), custom API gateways, and local CLI sessions without ever hardcoding secret tokens in configuration files.

#### `HarnessAuthConfig` Field Reference

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `auth_mode` (or `mode`) | `"none" \| "api_key_env" \| "api_key" \| "oauth_token" \| "vertex_adc" \| "vertex" \| "aws_bedrock" \| "cli_session" \| "ambient_cli" \| "foundry" \| "auto"` | No | `"none"` (`"cli_session"` for `claude-cli` / `claude-code`) | Selects the authentication mechanism (`mode` and `auth_mode` are synchronized aliases):<ul><li>`"none"`: No explicit auth injection (used when the environment is pre-configured externally).</li><li>`"api_key_env"` / `"api_key"`: Resolves a bearer API key from the environment variable named in `api_key_env_var`.</li><li>`"oauth_token"` / `"foundry"` / `"auto"`: Uses ambient OAuth/Foundry/auto CLI authentication (supported for `claude-cli` / `claude-code` and SDK providers).</li><li>`"vertex_adc"` / `"vertex"`: Configures Google Cloud Vertex AI Application Default Credentials (supported by `antigravity` and `claude` / `claude-cli` / `claude-code`).</li><li>`"aws_bedrock"`: Configures AWS Bedrock IAM/SSO authentication (supported by `claude` / `claude-cli` / `claude-code`; rejected with `HARNESS_AUTH_INCOMPATIBLE` on non-Claude providers).</li><li>`"cli_session"` / `"ambient_cli"`: Uses an existing local CLI login session (`agy` CLI session for `agy` / `antigravity` or `claude` CLI login for `claude-cli` / `claude-code` / `claude`).</li></ul> |
| `api_key_env_var` | `str \| null` | No | `null` | Name of the environment variable holding the API key when `auth_mode` is `api_key_env` / `api_key`. Defaults to `"GEMINI_API_KEY"` for `antigravity` and `"ANTHROPIC_API_KEY"` for `claude` / `claude-cli` / `claude-code` when omitted. **Important:** Pass the *name* of the environment variable (e.g., `"GEMINI_API_KEY"`), never the raw secret key itself. |
| `api_base_url` | `str \| null` | No | `null` | Optional custom API gateway or enterprise proxy URL. Mapped to `GOOGLE_GEMINI_BASE_URL` for `antigravity` and `ANTHROPIC_BASE_URL` for `claude` / `claude-cli` / `claude-code`. |
| `gcp_project` | `str \| null` | No | `null` | Google Cloud project ID for `auth_mode` `vertex_adc` / `vertex`. Mapped to `GOOGLE_CLOUD_PROJECT` for `antigravity` and `ANTHROPIC_VERTEX_PROJECT_ID` for `claude` / `claude-cli` / `claude-code` (falling back to `GOOGLE_CLOUD_PROJECT` / `ANTHROPIC_VERTEX_PROJECT_ID` in `setup.env` or `os.environ` when omitted). |
| `gcp_location` | `str \| null` | No | `null` | Google Cloud region for `auth_mode` `vertex_adc` / `vertex` (e.g., `"us-central1"`, `"us-east5"`, `"global"`). Mapped to `GOOGLE_CLOUD_LOCATION` for `antigravity` and `CLOUD_ML_REGION` for `claude` / `claude-cli` / `claude-code` (falling back to `GOOGLE_CLOUD_LOCATION` / `CLOUD_ML_REGION` in `setup.env` or `os.environ` when omitted). |
| `gcp_credentials_path` | `str \| null` | No | `null` | Optional filesystem path to a Google Cloud Service Account JSON key file for `auth_mode` `vertex_adc` / `vertex`. Mapped to `GOOGLE_APPLICATION_CREDENTIALS` (falling back to `GOOGLE_APPLICATION_CREDENTIALS` in `setup.env` or `os.environ` when omitted; otherwise standard `gcloud auth application-default login` or workload identity ADC is used). |
| `aws_region` | `str \| null` | No | `null` | AWS region for `auth_mode: aws_bedrock` on `claude` / `claude-cli` / `claude-code` (e.g., `"us-east-1"`, `"us-west-2"`). Mapped to `AWS_REGION` (falling back to `AWS_REGION` / `AWS_DEFAULT_REGION` in `setup.env` or `os.environ` when omitted). |
| `aws_profile` | `str \| null` | No | `null` | Optional named AWS CLI/SSO profile in `~/.aws/config` for `auth_mode: aws_bedrock` on `claude` / `claude-cli` / `claude-code`. Mapped to `AWS_PROFILE` (falling back to `AWS_PROFILE` in `setup.env` or `os.environ` when omitted). |
| `require_credentials_present` (or `require_preflight`) | `bool` | No | `false` | When `true` (`require_preflight` and `require_credentials_present` are synchronized aliases), pre-flight `validate_pipeline_inputs()` deterministically checks that the required CLI executable (`agy` or `claude`) or credential (`api_key_env_var` in `os.environ`/`env` for `api_key_env`, `gcp_project` for `vertex_adc`, or `aws_region` for `aws_bedrock`) is present, emitting `HARNESS_PROVIDER_UNAVAILABLE` or `HARNESS_AUTH_MISSING` if absent. |

#### Provider $\times$ `auth_mode` Environment Variable Resolution Matrix

When `HarnessSetupConfig.to_harness_config()` runs, it constructs the resolved environment dictionary (`HarnessConfig.env`) passed into the underlying SDK adapter as follows:

| Provider | `auth_mode` / `mode` | Resolved Environment Variables Set on `HarnessConfig.env` | Pre-Flight Check (`require_credentials_present: true` / `require_preflight: true`) |
| :--- | :--- | :--- | :--- |
| **`agy` / `agy-cli`** | `cli_session` / `ambient_cli` | Uses active `~/.gemini` session credentials for the local `agy` CLI (see §1.1 `provider`), plus `GOOGLE_GEMINI_BASE_URL` if `api_base_url` is set. | Verifies the `agy` CLI executable is available on `PATH` or in `OPENDV_AGY_CLI_BIN` (always checked when `auth_mode: ambient_cli` or `require_credentials_present: true`). |
| **`agy` / `agy-cli`** | `aws_bedrock` | **Invalid combination** — rejected during input validation. | Emits `HARNESS_AUTH_INCOMPATIBLE` (even when `require_credentials_present: false`). |
| **`claude-cli` / `claude-code`** | `cli_session` / `ambient_cli` / `oauth_token` / `foundry` / `auto` | Uses active `~/.claude` session credentials for the local `claude` CLI (see §1.1 `provider`), plus `ANTHROPIC_BASE_URL` if `api_base_url` is set. | Verifies the `claude` CLI executable is available on `PATH`, in `OPENDV_CLAUDE_CLI_BIN`, or in `extra_provider_options["claude_binary"]` (always checked when `auth_mode: ambient_cli` or `require_preflight: true` / `require_credentials_present: true`; emits `HARNESS_PROVIDER_UNAVAILABLE` if missing). |
| **`claude-cli` / `claude-code`** | `api_key` / `api_key_env` / `vertex` / `vertex_adc` / `aws_bedrock` | Populates `ANTHROPIC_API_KEY`, `CLAUDE_CODE_USE_VERTEX="1"` (`ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`), or `CLAUDE_CODE_USE_BEDROCK="1"` (`AWS_REGION`, `AWS_PROFILE`) in the subprocess environment passed to `claude -p`. | Verifies the `claude` CLI executable is available (plus `gcp_project` for `vertex_adc` or `aws_region` for `aws_bedrock` when `require_preflight: true`). |
| **`antigravity`** | `api_key_env` | Reads `$GEMINI_API_KEY` (or custom `api_key_env_var`) from `setup.env` / `os.environ` and populates both `GEMINI_API_KEY` and `GOOGLE_API_KEY` (plus `GOOGLE_GEMINI_BASE_URL` if `api_base_url` is set). | Verifies `api_key_env_var` (default `GEMINI_API_KEY`) is non-empty in `setup.env` or `os.environ`. |
| **`antigravity`** | `vertex_adc` | Sets `GOOGLE_GENAI_USE_VERTEXAI="true"`, `GOOGLE_CLOUD_PROJECT=<gcp_project>`, `GOOGLE_CLOUD_LOCATION=<gcp_location>`, and `GOOGLE_APPLICATION_CREDENTIALS=<gcp_credentials_path>` (resolving `auth.*` $\to$ `setup.env` $\to$ `os.environ`). | Verifies `gcp_project` or `$GOOGLE_CLOUD_PROJECT` / `$ANTHROPIC_VERTEX_PROJECT_ID` is non-empty. |
| **`antigravity`** | `cli_session` | Uses active `agy` CLI credentials in `~/.gemini` / application data directory. | Verifies `google.antigravity` SDK package is installed. |
| **`antigravity`** | `aws_bedrock` | **Invalid combination** — rejected during input validation. | Emits `HARNESS_AUTH_INCOMPATIBLE` (even when `require_credentials_present: false`). |
| **`claude`** | `api_key_env` | Reads `$ANTHROPIC_API_KEY` (or custom `api_key_env_var`) from `setup.env` / `os.environ` and populates `ANTHROPIC_API_KEY` (plus `ANTHROPIC_BASE_URL` if `api_base_url` is set). | Verifies `api_key_env_var` (default `ANTHROPIC_API_KEY`) is non-empty in `setup.env` or `os.environ`. |
| **`claude`** | `aws_bedrock` | Sets `CLAUDE_CODE_USE_BEDROCK="1"`, `AWS_REGION=<aws_region>`, and `AWS_PROFILE=<aws_profile>` (resolving `auth.*` $\to$ `setup.env` $\to$ `os.environ`, including `$AWS_DEFAULT_REGION`). Uses standard AWS IAM env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) or AWS SSO profile. | Verifies `aws_region` or `$AWS_REGION` / `$AWS_DEFAULT_REGION` is non-empty. |
| **`claude`** | `vertex_adc` | Sets `CLAUDE_CODE_USE_VERTEX="1"`, `ANTHROPIC_VERTEX_PROJECT_ID=<gcp_project>`, `CLOUD_ML_REGION=<gcp_location>`, and `GOOGLE_APPLICATION_CREDENTIALS=<gcp_credentials_path>` (resolving `auth.*` $\to$ `setup.env` $\to$ `os.environ`). | Verifies `gcp_project` or `$ANTHROPIC_VERTEX_PROJECT_ID` / `$GOOGLE_CLOUD_PROJECT` is non-empty. |
| **`claude`** | `cli_session` | Uses active `claude` CLI session credentials (`~/.claude`). | Verifies `claude_agent_sdk` package is installed. |

---

### 1.4 Execution Budget (`budget: HarnessBudget`) Reference

`HarnessBudget` (`packages/opendv-agent-harness/src/opendv_agent_harness/models.py`) enforces strict per-task resource and retry ceilings across all providers:

| Field | Type | Default | Constraints | Description |
| :--- | :--- | :---: | :---: | :--- |
| `max_turns` | `int` | `12` | `1 <= x <= 100` | Maximum conversational turns per `run_task()` / `stream_task()` invocation before halting with `HarnessStopReason.MAX_TURNS_EXCEEDED`. |
| `max_model_calls` | `int` | `20` | `1 <= x <= 200` | Maximum LLM generation calls per task before halting with `HarnessStopReason.MAX_MODEL_CALLS_EXCEEDED`. |
| `max_tool_calls` | `int` | `40` | `1 <= x <= 500` | Maximum domain or built-in tool invocations per task before halting with `HarnessStopReason.MAX_TOOL_CALLS_EXCEEDED`. |
| `max_input_tokens` | `int` | `200000` | `>= 1000` | Maximum cumulative input/prompt tokens per task before halting with `HarnessStopReason.TOKEN_BUDGET_EXCEEDED`. |
| `max_output_tokens` | `int` | `32000` | `>= 512` | Maximum cumulative output/completion tokens per task before halting with `HarnessStopReason.TOKEN_BUDGET_EXCEEDED`. |
| `max_total_tokens` | `int` | `232000` | `>= 1512` | Maximum combined (`input + output`) tokens per task before halting with `HarnessStopReason.TOKEN_BUDGET_EXCEEDED`. |
| `max_cost_usd` | `float` | `5.0` | `> 0.0` | Maximum estimated USD spend per task before halting with `HarnessStopReason.COST_BUDGET_EXCEEDED`. Forwarded to `BudgetConfig.max_cost_usd` (Antigravity) and `ClaudeAgentOptions.max_budget_usd` (Claude). |
| `timeout_seconds` | `float` | `300.0` | `> 1.0` | Wall-clock timeout in seconds per agent task execution. |
| `max_schema_validation_retries` | `int` | `3` | `0 <= x <= 10` | Maximum closed-loop `@toon_v1` self-healing attempts in `STAGE_05_AXI_DV_STATIC_GATES` when a candidate contract fails static verification gates. |

---

### 1.5 Sandbox Policy (`sandbox: SandboxPolicy`) Reference

`SandboxPolicy` (`packages/opendv-agent-harness/src/opendv_agent_harness/models.py`) enforces defense-in-depth filesystem and shell isolation:

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `read_only_workspace` | `bool` | `true` | When `true`, blocks file creation and modification tools (`create_file` / `edit_file` in Antigravity; `Write` / `Edit` in Claude). Scouting personas (`@vault-first-mate`, `@scout-source`, `@scout-unstructured`) always run in read-only mode. |
| `allowed_workspaces` | `list[str]` | `[]` | Additional workspace directory paths permitted alongside the primary `workspace_dir`. |
| `allow_shell_execution` | `bool` | `false` | When `false` (default), completely disables `run_command` (Antigravity) and `Bash` (Claude). When `true`, shell commands are filtered against `blocked_shell_substrings`. |
| `blocked_shell_substrings` | `list[str]` | `["rm -rf", "git push", "git worktree", "treehouse", "no-mistakes daemon"]` | Substrings that are unconditionally blocked by the Antigravity `deny_dangerous_shell_commands` policy and Claude `PreToolUse` `HookMatcher("Bash")` guardrail even when `allow_shell_execution: true`. |
| `allowed_builtin_tools` | `list[str]` | `["view_file", "list_directory", "find_file", "search_directory"]` | Built-in read-only filesystem discovery tools enabled for the agent. |
| `denied_builtin_tools` | `list[str]` | `["run_command", "create_file", "edit_file"]` | Built-in mutation/execution tools explicitly denied by default. |

---

### 1.6 Copy-Paste YAML Examples for Antigravity, Claude, and Local `agy` & `claude` CLI Harnesses

#### Example A: Google Antigravity Harness (`examples/pipeline_samples/global_commerce_vault/harness_config_antigravity.yaml`)

```yaml
provider: antigravity

model_overrides:
  reasoning: gemini-4-argon-high
  fast: gemini-3.8-flash-high

# Optional fine-grained per-agent model overrides (take precedence over model_overrides):
# agent_models:
#   vault-first-mate: gemini-4-argon-high
#   scout-source: gemini-3.8-flash-high
#   scout-unstructured: gemini-3.8-flash-high
#   ship-vault: gemini-4-argon-high

auth:
  # Direct Gemini API Key resolved from $GEMINI_API_KEY at runtime
  auth_mode: api_key_env
  api_key_env_var: GEMINI_API_KEY
  require_credentials_present: false

  # Alternative: Google Cloud Vertex AI Application Default Credentials (ADC)
  # auth_mode: vertex_adc
  # gcp_project: "enterprise-dv-prod"
  # gcp_location: "us-central1"
  # gcp_credentials_path: "/etc/gcp/sa-vertex-ai.json"
  # require_credentials_present: true

budget:
  max_turns: 12
  max_model_calls: 20
  max_tool_calls: 40
  max_input_tokens: 200000
  max_output_tokens: 32000
  max_total_tokens: 232000
  max_cost_usd: 5.0
  timeout_seconds: 300.0
  max_schema_validation_retries: 3

sandbox:
  read_only_workspace: true
  allow_shell_execution: false

steward_hold_policy: auto_approve_top_candidate
inject_adversarial_fault_on_attempt_1: true
```

#### Example B: Anthropic Claude Harness (`examples/pipeline_samples/global_commerce_vault/harness_config_claude.yaml`)

```yaml
provider: claude

model_overrides:
  reasoning: claude-opus-4-6
  fast: claude-haiku-4-5

# Optional fine-grained per-agent model overrides (take precedence over model_overrides):
# agent_models:
#   vault-first-mate: claude-opus-4-6
#   scout-source: claude-haiku-4-5
#   scout-unstructured: claude-haiku-4-5
#   ship-vault: claude-opus-4-6

auth:
  # Direct Anthropic API Key resolved from $ANTHROPIC_API_KEY at runtime
  auth_mode: api_key_env
  api_key_env_var: ANTHROPIC_API_KEY
  require_credentials_present: false

  # Alternative 1: AWS Bedrock IAM / SSO
  # auth_mode: aws_bedrock
  # aws_region: "us-east-1"
  # aws_profile: "enterprise-bedrock-sso"
  # require_credentials_present: true

  # Alternative 2: Google Cloud Vertex AI Partner Endpoint
  # auth_mode: vertex_adc
  # gcp_project: "enterprise-dv-prod"
  # gcp_location: "us-east5"
  # require_credentials_present: true

budget:
  max_turns: 12
  max_model_calls: 20
  max_tool_calls: 40
  max_input_tokens: 200000
  max_output_tokens: 32000
  max_total_tokens: 232000
  max_cost_usd: 5.0
  timeout_seconds: 300.0
  max_schema_validation_retries: 3

sandbox:
  read_only_workspace: true
  allow_shell_execution: false

steward_hold_policy: auto_approve_top_candidate
inject_adversarial_fault_on_attempt_1: true
```

#### Example C: Local Antigravity Coding CLI Harness (`examples/pipeline_samples/global_commerce_vault/harness_config.yaml` & `harness_config_agy.yaml`)

```yaml
provider: agy
model_overrides:
  reasoning: gemini-4-argon-high
  fast: gemini-3.8-flash-high
# Optional fine-grained per-agent model overrides (take precedence over model_overrides):
# agent_models:
#   vault-first-mate: gemini-4-argon-high
#   scout-source: gemini-3.8-flash-high
#   scout-unstructured: gemini-3.8-flash-high
#   ship-vault: gemini-4-argon-high
auth:
  auth_mode: cli_session
  require_credentials_present: false
budget:
  max_turns: 12
  max_model_calls: 20
  max_tool_calls: 40
  max_input_tokens: 200000
  max_output_tokens: 32000
  max_total_tokens: 232000
  max_cost_usd: 5.0
  timeout_seconds: 300.0
  max_schema_validation_retries: 3
sandbox:
  read_only_workspace: true
  allow_shell_execution: false
steward_hold_policy: auto_approve_top_candidate
inject_adversarial_fault_on_attempt_1: true
```

#### Example D: Local Claude Code CLI Harness (`examples/pipeline_samples/global_commerce_vault/harness_config_claude_cli.yaml`)

```yaml
provider: claude-cli
model_overrides:
  reasoning: claude-opus-4-6
  fast: claude-haiku-4-5
# Optional fine-grained per-agent model overrides (take precedence over model_overrides):
# agent_models:
#   vault-first-mate: claude-opus-4-6
#   scout-source: claude-haiku-4-5
#   scout-unstructured: claude-haiku-4-5
#   ship-vault: claude-opus-4-6
auth:
  mode: cli_session
  require_preflight: false
budget:
  max_turns: 12
  max_model_calls: 20
  max_tool_calls: 40
  max_input_tokens: 200000
  max_output_tokens: 32000
  max_total_tokens: 232000
  max_cost_usd: 5.0
  timeout_seconds: 300.0
  max_schema_validation_retries: 3
sandbox:
  read_only_workspace: true
  allow_shell_execution: false
steward_hold_policy: auto_approve_top_candidate
inject_adversarial_fault_on_attempt_1: true
```

---

## 2. Pipeline Input Specification (`PipelineInputSpec` — `pipeline_input.yaml` & Per-Wave Specs)

The pipeline input file (`pipeline_input.yaml`, `wave0_cold_start.yaml`..`wave3_unstructured_msa.yaml`, or `.json`) defines the enterprise domain, reference ontology, bi-temporal cutoff timestamp, optional seed contract (`initial_contract_path`), and ordered sequence of onboarding/evolution waves. All models enforce `ConfigDict(extra="forbid")`, so unknown or misspelled YAML keys are immediately flagged during pre-flight validation.

### 2.1 Top-Level `PipelineInputSpec` Fields

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `pipeline_id` | `str` | **Yes** | — | Unique identifier for the pipeline definition (e.g., `"pipe-global-commerce-vault-v1"`). |
| `project_id` | `str` | **Yes** | — | Platform project identifier owning the run (e.g., `"proj-global-commerce-001"`). |
| `run_id` | `str \| null` | No | Auto-generated (`run-<10-hex>`) | Optional explicit run identifier for deterministic replay and idempotent ledger correlation. |
| `display_name` | `str` | No | `"Open-DV Agentic Pipeline Run"` | Human-readable title rendered at the top of `pipeline_report.md` and in the `WB-00` Web Workbench Command Center. |
| `domain_namespace` | `str` | No | `"general"` | Enterprise governance domain namespace (normalized to lowercase `snake_case`, e.g., `"global_commerce"`). Must match or align with the ontology namespace. |
| `graph_a_ontology_path` | `str` | **Yes** | — | Path to the Enterprise Core Business Concept (CBC) `GraphAOntology` YAML/JSON file, resolved relative to `base_dir` (the directory containing `pipeline_input.yaml`) or the workspace root. Path traversal (`..`) is strictly forbidden. |
| `initial_contract_path` | `str \| null` | No | `null` | Optional path to an existing production `NoMistakeDataVault2ContractV5` YAML/JSON file (e.g., the previous CLI run's `final_contract.yaml`), which can also be supplied via the `--initial-contract` CLI flag or REST API `initial_contract_path` payload field. When provided, the pipeline seeds its baseline `NoMistakeDataVault2ContractV5` and `GraphBProfile` from this deployed contract, transitions Wave 0 to `lifecycle_stage: "MULTI_SOURCE_EXPANSION"`, resolves cross-call foreign keys against existing Hubs, detects intra-source schema drift (`Algorithm A.9a`), and enforces `Algorithm A.7` additive evolution locks against it (see §3.2–§3.3). |
| `assertion_cutoff_ts` | `str` | No | `"2026-12-31 23:59:59"` | Bi-temporal assertion cutoff timestamp (`YYYY-MM-DD HH:MM:SS`) used for Point-in-Time (PIT) audit queries (`Query D.10` & `Query D.10b`). |
| `output_dir` | `str \| null` | No | Ephemeral temp dir (`opendv_pipeline_out_*`) | Directory where all intermediate wave artifacts, final artifacts (`final_contract.yaml`, `final_canonical_ir.json`, `final_four_zone.sql`), `vault-ledger.jsonl`, `pipeline_report.json`, and `pipeline_report.md` are materialized. Can be overridden via `--output-dir` CLI flag or `output_dir` API parameter. |
| `waves` | `list[PipelineWaveInput]` | **Yes** (`min_length=1`) | — | Ordered list of one or more onboarding or evolution waves (`Wave 0 .. Wave N-1`). |

---

### 2.2 Wave Specification (`PipelineWaveInput` — `waves[]`)

Each entry in `waves:` represents a discrete lifecycle transition in the Data Vault warehouse:

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `wave_index` | `int` | **Yes** (`>= 0`) | — | Zero-based sequential wave number (`0`, `1`, `2`, ...). Wave `0` uses `lifecycle_stage: "COLD_START_N1"`; subsequent waves (`wave_index >= 1`) transition to `"MULTI_SOURCE_EXPANSION"` and enforce `Algorithm A.7` additive evolution invariants against the prior wave's verified contract. |
| `wave_name` | `str` | **Yes** | — | Human-readable label for the wave (e.g., `"Wave 0 — Cold Start (CRM Customers & ERP Orders)"`). |
| `wave_type` | `"COLD_START" \| "INCREMENTAL_SOURCE" \| "SCHEMA_DRIFT" \| "UNSTRUCTURED_ONBOARDING"` | No | `"COLD_START"` | Semantic classification of the wave:<ul><li>`"COLD_START"`: Day-1 ($N=1$) relational source onboarding, 3-axis satellite splitting (`Alg A.1`), and nullable-FK link splitting (`Alg A.4`).</li><li>`"INCREMENTAL_SOURCE"`: $N \to N+1$ multi-source expansion with `BKCC` collision isolation and `Alg A.7` additive evolution lock.</li><li>`"SCHEMA_DRIFT"`: Intra-source $S_k(t) \to S_k(t+1)$ schema drift detection (`baseline_data_path`) and `Alg A.9a` companion satellite synthesis (`sat_*_ext_v2`).</li><li>`"UNSTRUCTURED_ONBOARDING"`: Span-grounded unstructured document extraction (`Alg A.9b`) and 3-Tier Entity Resolution (`Tier-U1..U3`).</li></ul> |
| `description` | `str` | No | `""` | Detailed architectural description of the wave's modelling objectives. |
| `sources` | `list[PipelineSourceFeedInput]` | No | `[]` | Relational Bronze table feeds onboarded or evolved in this wave. Note: Every wave must contain at least one entry in `sources` or `unstructured_documents` (`EMPTY_WAVE_INPUT`). |
| `unstructured_documents` | `list[PipelineUnstructuredDocInput]` | No | `[]` | Unstructured text/PDF documents onboarded in this wave. Note: At least one relational source must be onboarded in the current or an earlier wave—or provided via a seed `initial_contract_path` (`--initial-contract`) containing deployed Hubs—before unstructured documents can be linked (`UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE`). |

---

### 2.3 Relational Bronze Source Feed Specification (`PipelineSourceFeedInput` — `waves[].sources[]`)

Each entry in `waves[].sources` declares a relational Bronze table feed (CSV, Parquet, JSON, or JSONL/NDJSON) profiled by `GraphBProfiler` in `STAGE_01_SCOUT_SOURCE`:

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `feed_id` | `str \| null` | No | Auto-derived (`<source_system>_<table_name>`) | Optional explicit feed identifier within the wave. |
| `source_system` | `str` | **Yes** | — | Logical identifier of the upstream operational system (e.g., `"crm_salesforce"`, `"erp_netsuite"`, `"billing_stripe"`). Combined with `table_name` to form `<source_system>.<table_name>`. |
| `table_name` | `str` | **Yes** | — | Physical or logical Bronze table name (e.g., `"crm_customers"`, `"erp_orders"`). |
| `data_path` (or `file_path`) | `str` | **Yes** | — | Path to the relational data file (`.csv`, `.parquet`, `.json`, `.jsonl`, `.ndjson`), resolved relative to `base_dir` or workspace root. Must contain $\ge 1$ row and $\ge 1$ column (`SOURCE_TABLE_EMPTY`). |
| `key_authority_tier` | `"TIER_1_GLOBAL_STANDARD" \| "TIER_2_ENTERPRISE_SOR" \| "TIER_3_LOCAL_SCOPED" \| null` | No | `null` (Auto-discovered) | 3-Tier Business Key Authority classification (`docs/02` §5). Auto-discovered by `@scout-source` from `GraphBProfiler` and `GraphAOntology` when omitted:<ul><li>`"TIER_1_GLOBAL_STANDARD"`: Globally unique ISO/tax/LEI standard key (`BKCC='GLOBAL'`, `require_src_qualifier=False`). Enables passive cross-source Hub integration.</li><li>`"TIER_2_ENTERPRISE_SOR"`: Enterprise System-of-Record master key (`BKCC='GLOBAL'`, `require_src_qualifier=False`).</li><li>`"TIER_3_LOCAL_SCOPED"`: System-local or regional sequence/code prone to cross-source collision (`require_src_qualifier=True`, requires non-global `bkcc_namespace` prefix and `system_scoped_bk_justification`).</li></ul> |
| `bkcc_namespace` | `str \| null` | No | `null` (Auto-discovered) | Business Key Collision Code (`BKCC`) namespace prefix hashed into Hub/Link keys (`Algorithm A.5`). Auto-discovered by `@scout-source` when omitted (`"GLOBAL"` for Tier-1/Tier-2 keys and a system-specific namespace such as `"STRIPE_EU"` for `TIER_3_LOCAL_SCOPED` keys). |
| `record_source_code` | `str \| null` | No | Auto-inferred from `source_system` | Audit record source identifier (`rec_src`) written to Hubs, Links, and Satellites (e.g., `"CRM_SFDC"`, `"ERP_NS"`). Auto-derived from `source_system` when omitted. |
| `target_hub_name` | `str \| null` | No | `null` (Auto-discovered) | Optional explicit target Hub name override (e.g., `"hub_customer"`). When omitted, `@scout-source` resolves the target Hub from `GraphAOntology` and empirical column profiles. |
| `business_key_columns` | `list[str]` | No | `[]` (Auto-discovered) | Optional explicit override of column names in `data_path` that form the natural Business Key for the table's primary Hub. When omitted (`[]`), `@scout-source` discovers Business Key columns automatically from `GraphBProfiler` empirical uniqueness/entropy metrics and `GraphAOntology` key authority patterns. Explicit blank column strings (e.g. `[""]`) are rejected with `MISSING_BUSINESS_KEY_COLUMNS`. |
| `surrogate_key_columns` | `list[str]` | No | `[]` (Auto-discovered) | Upstream system surrogate/auto-increment columns (e.g., `["customer_row_seq"]`) to be quarantined and dropped (`SYSTEM_SURROGATE_DROPPED` $\to$ `DROPPED_SURROGATE`) in the Pass 1 Column Triage Ledger (`Trap 2` prevention). |
| `timestamp_column` | `str \| null` | No | `null` (Auto-discovered) | Optional load/event timestamp column name override. |
| `foreign_key_mappings` | `dict[str, str] \| list[PipelineForeignKeyMapping]` | No | `{}` (Auto-discovered) | Mapping of `{source_fk_column: target_hub_or_cbc_hint}` (e.g., `{"customer_tax_id": "hub_customer", "promo_campaign_code": "hub_promotion"}`). `GraphBProfiler` measures non-null uniqueness $U_{\text{nn}}$ and null rate $N_{\text{null}}$ on each FK column; optional/sparse FKs ($U_{\text{nn}} < 0.95$ or $N_{\text{null}} > 0.05$) are automatically split into dedicated `WHERE <fk> IS NOT NULL` Links by `Algorithm A.4`. |
| `cbc_mapping_hints` | `dict[str, str]` | No | `{}` (Auto-discovered) | Mapping of `{table_or_column_name: cbc_concept_name_or_uri}` (e.g., `{"crm_customers": "CBC_CUSTOMER"}`) guiding `@ship-vault` Hub alignment against `GraphAOntology`. |
| `pii_columns` | `list[str]` | No | `[]` (Auto-discovered) | Columns containing `RESTRICTED_PII` / `PII_HIGH` attributes (e.g., `["contact_email", "tax_ssn_tin"]`). `Algorithm A.1` isolates these columns into a dedicated `sat_<entity>_pii` Satellite with `security_classification: "PII_HIGH"`. |
| `baseline_data_path` (or `baseline_file_path`) | `str \| null` | No | `null` | Optional path to the prior version $S_k(t)$ of this table (CSV/Parquet/JSON). When provided (typically in a `SCHEMA_DRIFT` wave), `STAGE_01_SCOUT_SOURCE` compares `baseline_data_path` ($t_0$) against `data_path` ($t_1$) via `GraphBProfiler.detect_schema_drift()` and triggers `Algorithm A.9a` additive companion satellite evolution (`sat_<entity>_core_ext_v2`). |

> [!IMPORTANT]
> Every column name listed in `business_key_columns`, `surrogate_key_columns`, `pii_columns`, and `foreign_key_mappings.keys()` must physically exist in the file at `data_path`, or pre-flight validation fails with `DECLARED_COLUMNS_MISSING`.

---

### 2.4 Unstructured Document & Candidate Span Specification (`PipelineUnstructuredDocInput` & `PipelineUnstructuredCandidateInput`)

#### `PipelineUnstructuredDocInput` (`waves[].unstructured_documents[]`)

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `doc_id` | `str` | **Yes** | — | Unique document business key / URI (e.g., `"DOC-MSA-2026-089"`) anchored in `hub_document`. |
| `document_path` (or `file_path`) | `str` | **Yes** | — | Path to the unstructured text document, resolved relative to `base_dir` or workspace root. Must exist (`DOCUMENT_FILE_MISSING`) and be non-empty (`DOCUMENT_EMPTY`). |
| `record_source` (or `record_source_code`) | `str` | No | `"UNSTRUCTURED_DOC"` | Audit record source code for the document repository (e.g., `"LEGAL_DOC_VAULT"`, `"OBJECT_STORE_S3"`). |
| `target_hub_name` | `str \| null` | No | `null` (Auto-discovered) | Optional explicit target Hub override for document linking. |
| `link_name` | `str \| null` | No | `null` (Auto-discovered) | Optional explicit Link table name override (`lnk_document_<cbc>`). |
| `satellite_name` | `str \| null` | No | `null` (Auto-discovered) | Optional explicit Extraction Satellite table name override. |
| `extracted_candidates` | `list[PipelineUnstructuredCandidateInput]` | No | `[]` (Auto-discovered) | Optional explicit span-grounded entity and attribute extractions. When omitted (`[]`), `@scout-unstructured` discovers candidate mentions, verbatim quotes, character offsets, and attributes from `document_path` and `GraphAOntology`, and routes them via `Algorithm A.9b`. |

#### `PipelineUnstructuredCandidateInput` (`waves[].unstructured_documents[].extracted_candidates[]`)

| Field | Type | Required | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `extraction_id` (or `candidate_id`) | `str` | No | Auto-derived (`ext_<slug>`) | Unique identifier for the extraction candidate within the document (e.g., `"ext_msa_counterparty_u1"`). |
| `target_hub` | `str` | No | `"hub_customer"` | Target Data Vault Hub or CBC concept hint to link the document extraction against (e.g., `"hub_customer"`). |
| `extracted_business_key` | `str` | **Yes** | — | Resolved business key value for the target Hub (e.g., `"TAX-US-900101"`). |
| `verbatim_quote` | `str` | **Yes** | — | Exact substring from the document proving provenance (`Rule U3`). Pre-flight validation enforces `document_text[char_start:char_end] == verbatim_quote` (`SPAN_QUOTE_MISMATCH`). |
| `char_start` | `int \| null` | No (`>= 0`) | Auto-resolved from `verbatim_quote` | Zero-based inclusive starting character offset in `document_path` (automatically resolved by matching `verbatim_quote` against `document_path` when omitted). |
| `char_end` | `int \| null` | No (`> char_start`) | Auto-resolved from `verbatim_quote` | Zero-based exclusive ending character offset in `document_path` (`char_end <= len(document_text)`, enforced by `SPAN_OUT_OF_BOUNDS`). |
| `page_number` | `int` | No | `1` | Source page number (1-indexed) for multi-page document provenance. |
| `confidence_score` | `float` | No (`0.0 <= x <= 1.0`) | `0.95` | Extraction/resolution confidence score $c \in [0.0, 1.0]$. Drives 3-Tier Entity Resolution (`Tier-U1..U3`):<ul><li>**`Tier-U1` (`TIER_U1_DETERMINISTIC`)**: $c \ge 0.92 \implies$ `PASS` (auto-linked).</li><li>**`Tier-U2` (`TIER_U2_HYBRID_EXACT`)**: $0.75 \le c < 0.92$ and top-2 score margin $\Delta \ge 0.15 \implies$ `PASS`.</li><li>**`Tier-U3` (`TIER_U3_CAPTAIN_HOLD`)**: $c < 0.75$ or $\Delta < 0.15 \implies$ triggers a `StewardHoldRecord` governed by `steward_hold_policy`.</li></ul> |
| `er_tier` | `"TIER_U1_DETERMINISTIC" \| "TIER_U2_HYBRID_EXACT" \| "TIER_U3_CAPTAIN_HOLD" \| null` | No | `null` (Auto-classified) | Optional explicit 3-Tier Entity Resolution classification override. |
| `top2_margin` | `float` | No (`0.0 <= x <= 1.0`) | `0.25` | Top-2 candidate similarity score margin $\Delta = s_1 - s_2$ when `candidate_hub_matches` has fewer than 2 entries. |
| `candidate_hub_matches` | `list[dict[str, Any]]` | No | `[]` | Ranked candidate Hub matches from the vault (each entry typically containing `candidate_key`, `legal_name`, and `score` / `similarity`). Used to compute the top-2 confidence margin $\Delta = s_1 - s_2$ and populate Steward Hold decision options. |
| `attributes` | `dict[str, Any]` | No | `{}` | Extracted structured key-value payload attributes associated with the span (e.g., `{"payment_window_days": 90, "volume_rebate_pct": 4.50}`). |

---

## 3. Enterprise Graph A Ontology (`GraphAOntology`) & Seed Contract (`initial_contract_path`)

### 3.1 Enterprise Graph A Ontology (`GraphAOntology` — `graph_a_ontology_path`)

The Graph A ontology file (`packages/opendv-core/src/opendv_core/models/graph_a.py`) anchors all Data Vault Hubs, Links, and Reference Tables to enterprise Core Business Concepts (CBCs) so agents never invent source-system-specific silo Hubs (`Trap 1`).

#### Root `GraphAOntology` Fields

| Field | Type | Required | Pattern / Constraint | Description |
| :--- | :--- | :---: | :--- | :--- |
| `ontology_version` | `str` | No (default `"1.0.0"`) | — | Semantic version of the ontology document. |
| `domain_namespace` | `str` | **Yes** | `^[a-z][a-z0-9_]*$` | Lowercase `snake_case` domain identifier (e.g., `"global_commerce"`, `"private_equity"`). |
| `enterprise_owner` | `str` | **Yes** | `min_length=2` | Governance council or data ownership body (e.g., `"Global Commerce Architecture Council"`). |
| `standards_references` | `list[str]` | No | Default `[]` | Industry standards referenced by the ontology (e.g., `["ISO-3166-1-Alpha-2", "ISO-4217-Currency"]`). |
| `canonical_business_concepts` | `list[CanonicalBusinessConcept]` | **Yes** | `min_length=1` | Enterprise Core Business Concept (CBC) nodes that map 1-to-1 to Data Vault 2.0 Hubs (`hub_*`). |
| `canonical_relationships` | `list[CanonicalRelationship]` | No | Default `[]` | Unit-of-Work relationships and associative events that map to Data Vault 2.0 Links (`lnk_*`, `t_lnk_*`, `sal_*`, `hal_*`). |
| `reference_enumerations` | `list[ReferenceEnumeration]` | No | Default `[]` | Controlled vocabulary enumerations that map to Data Vault 2.0 Reference Tables (`ref_*`). |

#### `CanonicalBusinessConcept` (`canonical_business_concepts[]`)

| Field | Type | Required | Pattern / Constraint | Description |
| :--- | :--- | :---: | :--- | :--- |
| `concept_uri` | `str` | **Yes** | `^urn:ontology:(provisional:)?entity:[a-z0-9_]+$` | Canonical URN for the concept (e.g., `"urn:ontology:entity:customer"`). |
| `concept_name` | `str` | **Yes** | `min_length=2` | Uppercase concept code (e.g., `"CBC_CUSTOMER"`, `"CBC_ORDER"`). |
| `elm_archetype` | `str` | **Yes** | `min_length=2` | Ensemble Logical Modeling (ELM) classification (e.g., `"WHO_PARTY"`, `"WHAT_EVENT_ANCHOR"`, `"WHAT_FINANCIAL_INSTRUMENT"`, `"WHAT_UNSTRUCTURED_ARTIFACT"`). |
| `definition` | `str` | **Yes** | `min_length=5` | Formal business definition of the concept. |
| `target_hub` | `str \| null` | No | `^hub_[a-z0-9_]+$` | Target Hub table name (e.g., `"hub_customer"`). |
| `governance_constraints` | `GovernanceConstraints \| null` | No | — | Security and satellite split rules:<ul><li>`pii_confidential_attributes`: `list[str] \| null`</li><li>`mnpi_confidential_attributes`: `list[str] \| null`</li><li>`required_satellite_split`: `str \| null` (e.g., `"sat_customer_pii"`)</li><li>`required_extraction_satellite_split`: `str \| null`</li></ul> |
| `key_authority_tiers` | `KeyAuthorityTiers \| null` | No | — | 3-Tier Business Key Authority rules (`tier_1_global_standard`, `tier_2_enterprise_sor`, `tier_3_local_scoped`), where each `KeyAuthorityTierSpec` defines:<ul><li>`authority_name`: `str`</li><li>`column_patterns`: `list[str] \| null`</li><li>`regex_pattern`: `str \| null`</li><li>`bkcc_policy`: `str` (e.g., `"GLOBAL"` or `"STRIPE_EU"`)</li><li>`require_src_qualifier`: `bool`</li><li>`cross_source_resolution_target`: `str \| null`</li></ul> |

#### `CanonicalRelationship` (`canonical_relationships[]`) & `ReferenceEnumeration` (`reference_enumerations[]`)

| Model | Field | Type | Required | Pattern / Constraint | Description |
| :--- | :--- | :--- | :---: | :--- | :--- |
| `CanonicalRelationship` | `relationship_uri` | `str` | **Yes** | `^urn:ontology:(provisional:)?(event\|relationship):[a-z0-9_]+$` | Canonical URN for the relationship (e.g., `"urn:ontology:relationship:order_customer"`). |
| `CanonicalRelationship` | `relationship_name` | `str` | **Yes** | `min_length=2` | Relationship identifier (e.g., `"REL_ORDER_CUSTOMER"`). |
| `CanonicalRelationship` | `target_link` | `str` | **Yes** | `^(lnk\|t_lnk\|nh_lnk\|sal\|hal\|x_lnk)_[a-z0-9_]+$` | Target Link table name (e.g., `"lnk_order_customer"`). |
| `CanonicalRelationship` | `endpoints` | `list[str]` | **Yes** | `min_length=2` | List of participating `concept_uri` or Hub names. |
| `CanonicalRelationship` | `grain_semantics` | `str` | **Yes** | `min_length=5` | Mandatory Unit-of-Work grain statement for Gate 1 (`gate1_contract_grain`). |
| `CanonicalRelationship` | `yagni_cost_guard_policy` | `YagniCostGuardPolicy \| null` | No | `max_general_columns_threshold >= 1`, `min_write_amplification_mb_per_day >= 0.0` | Optional Gate 4 YAGNI cost-guard threshold overrides. |
| `ReferenceEnumeration` | `enum_uri` | `str` | **Yes** | `^urn:ontology:enum:[a-z0-9_]+$` | Canonical URN for the reference vocabulary enumeration. |
| `ReferenceEnumeration` | `target_reference_table` | `str` | **Yes** | `^ref_[a-z0-9_]+$` | Target `ref_*` table name (demoted from low-cardinality code columns by `Algorithm A.2`). |
| `ReferenceEnumeration` | `allowed_codes` | `list[str]` | **Yes** | `min_length=1` | Permitted enumeration code values. |

---

### 3.2 Optional Seed Contract Input (`NoMistakeDataVault2ContractV5` — `initial_contract_path` / `--initial-contract`)

When `initial_contract_path` is set in `pipeline_input.yaml` (or passed via the `--initial-contract <prev_final_contract.yaml>` CLI flag / REST API `initial_contract_path` parameter), the file must conform to `schemas/no-mistake-dv2-contract-v5.schema.json` (`NoMistakeDataVault2ContractV5` in `packages/opendv-core/src/opendv_core/models/contract_v5.py`):

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `contract_version` | `"5.0"` | **Yes** | Must be `"5.0"`. |
| `domain_namespace` | `str` (`^[a-z][a-z0-9_]*$`) | **Yes** | Domain namespace of the deployed baseline vault. |
| `lifecycle_stage` | `"COLD_START_N1" \| "MULTI_SOURCE_EXPANSION"` | **Yes** | Lifecycle stage of the baseline contract. |
| `column_triage_ledger` | `list[ColumnTriageEntry]` (`min_length=1`) | **Yes** | 100% column triage accounting ledger (`bronze_table`, `column_name`, `elm_classification`, `target_dv_entity`). |
| `hubs` | `list[ContractHubSpec]` | **Yes** | Deployed Hubs (`name` matching `^hub_[a-z0-9_]+$`, `ontology_concept_uri`, `business_key_sources`). Protected by `Algorithm A.7` against deletion or grain mutation in subsequent waves. |
| `links` | `list[ContractLinkSpec]` | **Yes** | Deployed Links (`name`, `link_type`, `ontology_concept_uri`, `source_bronze_table`, `where_filter_condition`, `hub_references` with `min_length=2`). |
| `satellites` | `list[ContractSatelliteSpec]` | **Yes** | Deployed Satellites (`name`, `satellite_type`, `parent_entity`, `source_bronze_table`, `security_classification`, `volatility_tier`, `payload_columns`, `soft_deprecated_columns`). |
| `reference_tables` | `list[ContractReferenceTableSpec]` | **Yes** | Deployed `ref_*` tables (`[]` if none). |
| `unmapped_escalations` | `list[UnmappedEscalation]` | **Yes** | Escalated ambiguous columns (`[]` in verified contracts). |

---

### 3.3 Subsequent-CLI Incremental Schema Evolution Workflow (`--initial-contract`)

In production Data Vault 2.0 environments, enterprise schemas are **not** built in a single 1-pass batch run. Instead, Day-1 cold start ($N=1$), new source system onboarding ($N \to N+1$), intra-source operational schema drift ($S_k(t) \to S_k(t+1)$), and unstructured PDF/MSA document ingestion occur asynchronously across independent deployment releases. Each release is executed as a **subsequent CLI call** (`opendv pipeline run --input <wave_spec.yaml> --initial-contract <prev_final_contract.yaml>`) that takes the prior run's verified `final_contract.yaml` as its immutable baseline.

#### Stage-by-Stage Mechanics When `--initial-contract` / `initial_contract_path` Is Provided

1. **Baseline Contract & `GraphBProfile` Seeding (`STAGE_00_ORCHESTRATION`):**
   - Pre-flight `validate_pipeline_inputs()` verifies that `initial_contract_path` exists (`INITIAL_CONTRACT_MISSING`) and conforms to `NoMistakeDataVault2ContractV5` (`INITIAL_CONTRACT_INVALID`), and satisfies `UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE` when an unstructured-only wave builds on an existing relational contract.
   - `AgenticVaultPipeline` loads the baseline `NoMistakeDataVault2ContractV5` (`Hubs`, `Links`, `Satellites`, `reference_tables`, and `column_triage_ledger`) as `deployed_contract` and automatically transitions `lifecycle_stage` to `"MULTI_SOURCE_EXPANSION"`.
   - It synthesizes baseline `GraphBProfile` table/column metadata from `deployed_contract` so downstream discovery and verification stages have full visibility into already-deployed Hubs, business key sources, and satellite payload columns even when prior Bronze CSV files are not re-supplied in the current wave.
2. **Cross-Call Foreign Key Resolution & Drift Detection (`STAGE_01_SCOUT_SOURCE` & `STAGE_02_SCOUT_UNSTRUCTURED`):**
   - `STAGE_01_SCOUT_SOURCE` resolves foreign keys in new relational feeds (e.g., `stripe_invoices.csv` in Wave 1) against existing Hubs in `deployed_contract` (`hub_customer`, `hub_order` / `hub_sales_order`) and appends new `TIER_3_LOCAL_SCOPED` `BKCC` sources (e.g., `STRIPE_EU`) to existing Hubs without duplicating Hub tables.
   - When an existing source table evolves (e.g., `crm_customers_v2.csv` in Wave 2), `STAGE_01_SCOUT_SOURCE` compares the incoming columns against `deployed_contract` (and optional `baseline_data_path`) to detect `ADD_COLUMN` intra-source schema drift (`loyalty_tier_code`, `annual_spend_band`).
   - `STAGE_02_SCOUT_UNSTRUCTURED` links extracted document entities (`hub_document` $\to$ `lnk_document_customer`) directly against existing Hubs in `deployed_contract`.
3. **Additive Evolution Lock & Companion Satellite Synthesis (`STAGE_05_AXI_DV_STATIC_GATES`):**
   - `STAGE_05_AXI_DV_STATIC_GATES` executes **`Algorithm A.7` (`verify_additive_evolution_invariants`, `DV-EVO-001`–`007`)** against `deployed_contract`, guaranteeing zero destructive changes: no existing Hub, Link, or Satellite can be dropped, no Hub business key grain or `BKCC` prefix can be mutated, and no existing Satellite's `payload_columns` (`hash_diff` calculation) can be altered.
   - Simultaneously, **`Algorithm A.9a` (`codemod_intra_source_schema_drift`)** routes newly added drift columns (`loyalty_tier_code`, `annual_spend_band`) into an additive companion satellite (`sat_customer_core_ext_v2`) while leaving the historical `sat_customer_core` table untouched.

#### Schema Evolution Summary Across the 4 Sequential CLI Calls

See [`README.md` — Recommended Production Workflow: Multi-Call Incremental Schema Evolution (`--initial-contract`)](../README.md#1-trigger-via-unified-cli-opendv-pipeline) for the copy-pasteable 4-call `opendv pipeline run` CLI sequence.

| CLI Call | Per-Wave Input Spec | `--initial-contract` Input | Output Contract & `lifecycle_stage` | Key Algorithms & Gates Executed | Schema Changes & Cumulative Vault Topology |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Call 1** | `wave0_cold_start.yaml` | `None` *(Day-1 Cold Start)* | `wave0/final_contract.yaml`<br>(`COLD_START_N1`) | `Alg A.1` (3-Axis PII Split & Gate 4 YAGNI), `Alg A.4` (Nullable-FK Link Splitter) | **3 Hubs:** `hub_customer`, `hub_order`, `hub_promotion`<br>**2 Links:** `lnk_order_customer`, `lnk_order_promotion` (`WHERE promo_campaign_code IS NOT NULL`)<br>**4 Satellites:** `sat_customer_core`, `sat_customer_pii` (`PII_HIGH`), `sat_order_core`, `sat_promotion_core`<br>**Quarantined:** `customer_row_seq` (`DROPPED_SURROGATE`) |
| **Call 2** | `wave1_incremental_billing.yaml` | `wave0/final_contract.yaml` | `wave1/final_contract.yaml`<br>(`MULTI_SOURCE_EXPANSION`) | `Alg A.4` (3-Tier `BKCC` Shield), `Alg A.7` (Additive Evolution Lock `DV-EVO-001`–`007`) | **Hubs:** Appends `STRIPE_EU` (`TIER_3_LOCAL_SCOPED`) business key source to `hub_customer`<br>**Links Added:** `lnk_customer_sales_order` (billing-to-customer/order association)<br>**Satellites Added/Expanded:** Preserves all Wave 0 entities (`sat_customer_core`, `sat_customer_pii`, `sat_order_core`) and adds Stripe EU invoice satellite coverage |
| **Call 3** | `wave2_schema_drift.yaml` | `wave1/final_contract.yaml` | `wave2/final_contract.yaml`<br>(`MULTI_SOURCE_EXPANSION`) | `Alg A.9a` (Intra-Source Drift Codemod), `Alg A.7` (Additive Evolution Lock) | **Drift Detected:** `ADD_COLUMN` on `crm_customers` (`loyalty_tier_code`, `annual_spend_band`)<br>**Companion Satellite Added:** `sat_customer_core_ext_v2` (`sat_customer_core` `hash_diff` remains 100% immutable) |
| **Call 4** | `wave3_unstructured_msa.yaml` | `wave2/final_contract.yaml` | `wave3/final_contract.yaml`<br>(`MULTI_SOURCE_EXPANSION`) | `Alg A.9b` (Verbatim Span Grounding & `Tier-U1..U3` ER), `Alg A.7` (Additive Evolution Lock) | **Hubs Added:** `hub_document` (`DOC-MSA-2026-089`)<br>**Links Added:** `lnk_document_customer` (`UNSTRUCTURED_EXTRACTION_LINK`)<br>**Satellites Added:** `sat_document_customer_ai_provenance` and `mas_document_extractions` (`Tier-U1` `0.98` + `Tier-U3` `0.68` Steward Hold) |

---

## 4. Deterministic Input & Output Validation Rules & Diagnostic Codes

### 4.1 Pre-Flight Input Validation (`validate_pipeline_inputs`)

Before any agent task or DuckDB compilation runs, `STAGE_00_ORCHESTRATION` executes `validate_pipeline_inputs(input_spec, harness_setup, base_dir=...)` and returns a `DeterministicValidationReport(stage="INPUT_VALIDATION", ...)`. If any check fails, the pipeline halts immediately with `status="FAILED"` and emits one or more structured diagnostic entries:

| Diagnostic Code | Target | Trigger Condition & Remediation |
| :--- | :--- | :--- |
| `INPUT_SPEC_SCHEMA_INVALID` | `input_spec` | `pipeline_input.yaml` fails `PipelineInputSpec` Pydantic v2 validation (e.g., missing required field, unknown extra field, or `waves` is empty). |
| `HARNESS_SETUP_INVALID` | `harness_setup` | `harness_config.yaml` fails `HarnessSetupConfig` / `HarnessAuthConfig` / `HarnessBudget` / `SandboxPolicy` Pydantic validation (including legacy `argon`/`flash` or unknown `model_overrides` keys and invalid `agent_models` persona keys). |
| `UNKNOWN_HARNESS_PROVIDER` | `harness_setup.provider` | `provider` is not a supported harness provider (`"agy"`, `"agy-cli"`, `"antigravity"`, `"claude"`, `"claude-cli"`, `"claude-code"`). |
| `HARNESS_INIT_FAILED` | `harness_setup` | Adapter instantiation via `get_harness_adapter()` raised an error (e.g., non-absolute path in `HarnessConfig`). |
| `HARNESS_AUTH_INVALID` | `harness_setup.auth.mode` / `harness_setup.auth.auth_mode` | `auth.mode` / `auth.auth_mode` is not a supported authentication mode for the selected provider (e.g., `"adc"` instead of `"cli_session"`, `"api_key"`, `"oauth_token"`, `"vertex"`, `"aws_bedrock"`, `"foundry"`, `"auto"`). |
| `HARNESS_AUTH_INCOMPATIBLE` | `harness_setup.auth.auth_mode` | Incompatible `(provider, auth_mode)` pair—specifically using `auth_mode: aws_bedrock` with `provider: agy`, `antigravity`, or any non-Claude provider. Switch `provider` to `claude` / `claude-cli` / `claude-code` or `auth_mode` to `vertex_adc` / `api_key_env` / `cli_session`. |
| `HARNESS_PROVIDER_UNAVAILABLE` | `harness_setup.provider` | Emitted when `provider` is `"claude-cli"` / `"claude-code"` with `require_preflight: true` (or `require_credentials_present: true` / `ambient_cli`) and the `claude` CLI executable cannot be resolved on `PATH`, in `OPENDV_CLAUDE_CLI_BIN`, or via `extra_provider_options["claude_binary"]`. |
| `HARNESS_AUTH_MISSING` | `harness_setup.auth.*` | Emitted when: (1) `provider: agy` / `agy-cli` with `auth_mode: cli_session` (when `require_credentials_present: true`) or `auth_mode: ambient_cli` does not find the `agy` CLI executable on `PATH` or in `OPENDV_AGY_CLI_BIN`; (2) `auth_mode: cli_session` / `ambient_cli` on `antigravity` or `claude` does not have the required SDK package (`google.antigravity` or `claude_agent_sdk`) installed; or (3) `auth.require_credentials_present` / `auth.require_preflight` is `true` and the required credential (`api_key_env_var` for `api_key_env`, `gcp_project` / `$GOOGLE_CLOUD_PROJECT` / `$ANTHROPIC_VERTEX_PROJECT_ID` for `vertex_adc`, `aws_region` / `$AWS_REGION` / `$AWS_DEFAULT_REGION` for `aws_bedrock`, or SDK package / API key for `auth_mode: none` on `antigravity` / `claude`) is missing or empty. |
| `PATH_TRAVERSAL_REJECTED` | Path field name | Any file/directory path in `PipelineInputSpec` contains `..` segments or resolves outside `base_dir` and `workspace_root`. |
| `ONTOLOGY_FILE_MISSING` | Resolved ontology path | The file specified by `graph_a_ontology_path` does not exist on disk. |
| `ONTOLOGY_SCHEMA_INVALID` | Resolved ontology path | The ontology file fails `validate_graph_a()` or `GraphAOntology` Pydantic validation. |
| `INITIAL_CONTRACT_MISSING` | Resolved contract path | `initial_contract_path` was provided but the file does not exist on disk. |
| `INITIAL_CONTRACT_INVALID` | Resolved contract path | `initial_contract_path` fails `validate_contract_v5()` Draft 2020-12 schema or `NoMistakeDataVault2ContractV5` validation. |
| `OUTPUT_DIR_NOT_A_DIRECTORY` | Resolved `output_dir` | `output_dir` already exists on disk as a regular file rather than a directory. |
| `EMPTY_WAVE_INPUT` | `wave_<index>` | A wave has neither `sources` nor `unstructured_documents`. |
| `MISSING_BUSINESS_KEY_COLUMNS` | `wave[<i>].sources[<s>].business_key_columns` | An explicitly provided `business_key_columns` list contains a blank or whitespace-only column name (note: an omitted or empty `[]` list is valid and triggers `@scout-source` auto-discovery). |
| `SOURCE_FILE_MISSING` | Resolved `data_path` | The relational source data file does not exist on disk. |
| `SOURCE_TABLE_EMPTY` | Resolved `data_path` | The relational source file loaded into DuckDB has $0$ rows or $0$ columns. |
| `DECLARED_COLUMNS_MISSING` | `<source_system>.<table_name>` | One or more columns listed in `business_key_columns`, `surrogate_key_columns`, `pii_columns`, or `foreign_key_mappings` do not exist in the physical CSV/Parquet/JSON file. |
| `SOURCE_LOAD_ERROR` | Resolved `data_path` | DuckDB failed to parse the source file (`read_csv_auto`, `read_parquet`, or `read_json_auto`). |
| `BASELINE_FILE_MISSING` | Resolved `baseline_data_path` | `baseline_data_path` was specified on a source feed but does not exist on disk. |
| `BASELINE_TABLE_EMPTY` | Resolved `baseline_data_path` | The baseline table has $0$ rows or $0$ columns. |
| `BASELINE_LOAD_ERROR` | Resolved `baseline_data_path` | DuckDB failed to parse the `baseline_data_path` file. |
| `UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE` | `wave_<index>` | A wave attempts to onboard `unstructured_documents` before any relational source has been onboarded in the current or prior waves (and without a seed `initial_contract_path` containing deployed Hubs). |
| `DOCUMENT_FILE_MISSING` | Resolved `document_path` | An unstructured document file does not exist on disk. |
| `DOCUMENT_EMPTY` | Resolved `document_path` | An unstructured document file is empty or whitespace-only. |
| `SPAN_OUT_OF_BOUNDS` | `<doc_id>:<extraction_id>` | Candidate span `[char_start, char_end]` violates `0 <= char_start < char_end <= len(document_text)`. |
| `SPAN_QUOTE_MISMATCH` | `<doc_id>:<extraction_id>` | Candidate `verbatim_quote` does not exactly match `document_text[char_start:char_end]`. |

---

### 4.2 Post-Flight Output Validation (`validate_pipeline_outputs`)

After all waves complete and `STAGE_07_DETERMINISTIC_COMPILER` materializes the final artifacts, `validate_pipeline_outputs(report, cas_store=...)` executes a second deterministic verification pass (`DeterministicValidationReport(stage="OUTPUT_VALIDATION", ...)`):

| Diagnostic Code | Target | Verification Rule |
| :--- | :--- | :--- |
| `CAS_INTEGRITY_FAILURE` | `ref.name` | `cas_store.verify(ref.sha256)` returned `False` (the content-addressable blob is missing or corrupted). |
| `CAS_LOOKUP_ERROR` | `ref.name` | Exception raised while verifying the SHA-256 digest in `ContentAddressableStore`. |
| `ARTIFACT_FILE_MISSING` | `ref.name` | The materialized file at `ref.file_path` is missing on disk. |
| `ARTIFACT_SHA256_MISMATCH` | `ref.name` | The on-disk file's SHA-256 digest does not match `ref.sha256` recorded in the report (detects post-compilation tampering). |
| `GRAPH_B_PROFILE_INVALID` | `wave_<w>_graph_b_profile.json` | A wave's Graph B profile JSON fails `validate_graph_b()` Draft 2020-12 schema or `GraphBProfile` Pydantic validation. |
| `FINAL_CONTRACT_SCHEMA_INVALID` | `final_contract` | `final_contract.yaml` fails `validate_contract_v5()` Draft 2020-12 schema or `NoMistakeDataVault2ContractV5` validation. |
| `FINAL_CANONICAL_IR_INVALID` | `final_canonical_ir` | `final_canonical_ir.json` fails `validate_canonical_ir()` Draft 2020-12 schema or `CanonicalIR` validation. |
| `OBSERVABILITY_EVENT_INVALID` | `observability_events[<i>]` | An emitted telemetry event fails `validate_observability_event()` (`schemas/vault-observability-event.schema.json`) or fails `validate_no_pii_leaks()` after `VIEWER` RBAC redaction. |
| `WAVE_EXECUTION_REJECTED` | `wave_<w>` | A wave finished with terminal status `"REJECT-WITH-TRACE"` or `"ERROR"`. |
| `STEP_EXECUTION_REJECTED` | `step_id` | A pipeline stage step finished with terminal status `"REJECT-WITH-TRACE"` or `"ERROR"`. |
| `SKIPPED_DUE_TO_INPUT_FAILURE` | `pipeline` | Output validation was skipped because pre-flight input validation failed at `STAGE_00_ORCHESTRATION`. |

---

## 5. Pipeline Output Report (`AgenticPipelineReport`) & Intermediate Artifacts Catalog

### 5.1 Top-Level `AgenticPipelineReport` (`pipeline_report.json`)

| Field | Type | Description |
| :--- | :--- | :--- |
| `run_id` | `str` | Unique identifier for the pipeline execution run. |
| `pipeline_id` | `str` | Pipeline identifier from `PipelineInputSpec.pipeline_id`. |
| `project_id` | `str` | Project identifier from `PipelineInputSpec.project_id`. |
| `domain_namespace` | `str` | Enterprise domain namespace. |
| `status` | `"COMPLETED" \| "PAUSED_HOLD" \| "FAILED"` | Terminal pipeline run status:<ul><li>`"COMPLETED"`: All waves and stages passed (`PASS` or `AUTO_REPAIRED`).</li><li>`"PAUSED_HOLD"`: Execution paused cleanly at a `Tier-U3` / `ASK-USER` Steward Hold (`steward_hold_policy: pause`).</li><li>`"FAILED"`: Input validation, gate verification, or output validation failed.</li></ul> |
| `overall_passed` | `bool` | `true` iff `input_validation.passed and output_validation.passed and status == "COMPLETED"`. |
| `started_at` | `str` | ISO-8601 UTC start timestamp (`YYYY-MM-DDTHH:MM:SZ`). |
| `completed_at` | `str` | ISO-8601 UTC completion timestamp (`YYYY-MM-DDTHH:MM:SZ`). |
| `total_duration_ms` | `float` | End-to-end wall-clock duration in milliseconds. |
| `harness_setup` | `dict[str, Any]` | Redacted summary of `HarnessSetupConfig` including top-level `provider`, `auth_mode`, `api_key_env_var`, `auth`, `model_overrides`, `agent_models`, `budget`, `sandbox`, `steward_hold_policy`, and redacted `env` (`{"<KEY>": "[REDACTED]"}`). Never contains raw secret values. |
| `input_validation` | `DeterministicValidationReport` | Pre-flight validation report (`stage="INPUT_VALIDATION"`, `passed`, `checked_items_count`, `diagnostics`). |
| `output_validation` | `DeterministicValidationReport` | Post-flight validation report (`stage="OUTPUT_VALIDATION"`, `passed`, `checked_items_count`, `verified_artifacts_count`, `verified_events_count`, `pii_leak_check_passed`, `cas_integrity_verified`, `diagnostics`). |
| `wave_reports` | `list[PipelineWaveReport]` | Per-wave execution summaries (`wave_index`, `wave_name`, `wave_type`, `status`, `sources_profiled`, `columns_profiled`, `schema_drift_events`, `unstructured_tier_counts`, `self_healing_attempts`, `applied_codemods`, `gate_scorecard`, `sandbox_summary`, `contract_sha256`, `canonical_ir_sha256`, `sql_bundle_sha256`, `step_ids`). |
| `step_reports` | `list[PipelineStepReport]` | Ordered ledger of every stage execution across all waves (`step_id`, `wave_index`, `wave_name`, `stage_id`, `persona`, `model_tier` (`"reasoning"` or `"fast"`), `model_used` (the resolved concrete model ID), `status`, `attempt_count`, `duration_ms`, `summary`, `tool_invocations`, `input_artifact_sha256s`, `output_artifact_sha256s`, `metrics`, `diagnostics`). |
| `intermediate_artifacts` | `list[PipelineArtifactRef]` | Content-addressable metadata (`artifact_id`, `name`, `stage_id`, `wave_index`, `media_type`, `sha256`, `byte_length`, `file_path`, `description`) for every intermediate artifact generated during `STAGE_00` through `STAGE_06`. |
| `final_artifacts` | `dict[str, PipelineArtifactRef]` | Keyed dictionary of final sealed deliverables (`final_contract`, `final_canonical_ir`, `final_four_zone_sql`, `vault_ledger_jsonl`, `pipeline_report_md`, `pipeline_report_json`). |
| `active_holds` | `list[dict[str, Any]]` | Currently pending `StewardHoldRecord` items (non-empty when `status == "PAUSED_HOLD"`). |
| `resolved_holds` | `list[dict[str, Any]]` | Resolved `StewardHoldRecord` items (e.g., `Tier-U3` holds auto-approved or manually resolved). |
| `observability_events` | `list[dict[str, Any]]` | Redacted, schema-valid `VaultObservabilityEvent` records emitted across Workbench views `WB-00` through `WB-11`. |
| `markdown_summary` | `str` | Complete human-readable Markdown report (also written to `<output_dir>/pipeline_report.md`). |

---

### 5.2 On-Disk Output Directory & Intermediate Artifacts Catalog

Every pipeline run materializes a self-contained, cryptographically verifiable directory tree under `<output_dir>`:

| Relative Path in `<output_dir>` | Artifact ID / Key | Emitted Stage | Media Type | Description |
| :--- | :--- | :--- | :--- | :--- |
| `stage_00/graph_a_ontology.yaml` | `art-stage00-graph-a-ontology` | `STAGE_00_ORCHESTRATION` | `application/x-yaml` | Verified Enterprise Core Business Concept (`Graph A`) ontology snapshot. |
| `waves/wave_<w>/wave_<w>_graph_b_profile.json` | `art-w<w>-stage01-graph-b-profile` | `STAGE_01_SCOUT_SOURCE` | `application/json` | Cumulative `GraphBProfile` statistical metrics ($U(C), H(C), N_{\text{null}}(C), W_{\text{amp}}$) for Wave `<w>`. |
| `waves/wave_<w>/wave_<w>_schema_drift_report.json` | `art-w<w>-stage01-schema-drift` | `STAGE_01_SCOUT_SOURCE` | `application/json` | Emitted when `baseline_data_path` detects $S_k(t) \to S_k(t+1)$ schema drift events (`ADD_COLUMN`, `DROP_COLUMN`, etc.). |
| `waves/wave_<w>/wave_<w>_unstructured_grounding.json` | `art-w<w>-stage02-unstructured-grounding` | `STAGE_02_SCOUT_UNSTRUCTURED` | `application/json` | Character-offset span verification results and `Tier-U1..U3` Entity Resolution outcomes for Wave `<w>`. |
| `waves/wave_<w>/wave_<w>_pass1_column_triage.json` | `art-w<w>-stage03-pass1-triage` | `STAGE_03_SHIP_VAULT_PASS1` | `application/json` | Pass 1 100% Column Triage Ledger accounting for every Bronze column (`BUSINESS_KEY`, `FOREIGN_KEY_REF`, `OPTIONAL_FOREIGN_KEY_REF`, `DESCRIPTIVE_PAYLOAD`, `SYSTEM_SURROGATE_DROPPED`, `TECHNICAL_METADATA`). |
| `waves/wave_<w>/wave_<w>_contract_attempt_1.yaml` | `art-w<w>-stage04-contract-attempt1` | `STAGE_04_SHIP_VAULT_PASS2` | `application/x-yaml` | Initial candidate `NoMistakeDataVault2ContractV5` YAML before static gate verification and AST codemods. |
| `waves/wave_<w>/wave_<w>_axi_trace_attempt_<k>.toon` | `art-w<w>-stage05-toon-attempt<k>` | `STAGE_05_AXI_DV_STATIC_GATES` | `text/plain` | Token-Oriented Object Notation (`@toon_v1`) diagnostic trace for Attempt `<k>` across Gates `G0`–`G5`. |
| `waves/wave_<w>/wave_<w>_contract_verified.yaml` | `art-w<w>-stage05-contract-verified` | `STAGE_05_AXI_DV_STATIC_GATES` | `application/x-yaml` | Converged, 6-Gate verified `NoMistakeDataVault2ContractV5` YAML after deterministic codemods (`A.1`, `A.4`, `A.9a`) and `@toon_v1` self-healing. |
| `waves/wave_<w>/wave_<w>_canonical_ir.json` | `art-w<w>-stage06-canonical-ir` | `STAGE_06_AXI_DV_DYNAMIC_SANDBOX` | `application/json` | Lowered `CanonicalIR` (`v1.0.0`) with length-prefixed SHA-256 hash expressions and topological load wave DAG. |
| `waves/wave_<w>/wave_<w>_four_zone.sql` | `art-w<w>-stage06-four-zone-sql` | `STAGE_06_AXI_DV_DYNAMIC_SANDBOX` | `application/sql` | Compiled 4-Zone DuckDB SQL bundle (`Zones 0–4`: Staging views, Raw Vault DDL, Zero Keys & Ghost Records, idempotent DML loaders, and PIT audit queries). |
| `final_contract.yaml` | `final_artifacts["final_contract"]` | `STAGE_07_DETERMINISTIC_COMPILER` | `application/x-yaml` | Final verified cumulative Data Vault 2.0 Model Contract (`v5.0`). |
| `final_canonical_ir.json` | `final_artifacts["final_canonical_ir"]` | `STAGE_07_DETERMINISTIC_COMPILER` | `application/json` | Final lowered Canonical IR (`v1.0.0`). |
| `final_four_zone.sql` | `final_artifacts["final_four_zone_sql"]` | `STAGE_07_DETERMINISTIC_COMPILER` | `application/sql` | Final compiled 4-Zone DuckDB SQL bundle. |
| `vault-ledger.jsonl` | `final_artifacts["vault_ledger_jsonl"]` | `STAGE_07_DETERMINISTIC_COMPILER` | `application/x-ndjson` | Append-only JSONL ledger of RBAC-redacted `VaultObservabilityEvent` records (`WB-00..WB-11`). |
| `pipeline_report.md` | `final_artifacts["pipeline_report_md"]` | `STAGE_07_DETERMINISTIC_COMPILER` | `text/markdown` | Human-readable Markdown summary report with validation tables, wave progression, step ledger, and SHA-256 artifact manifest. |
| `pipeline_report.json` | `final_artifacts["pipeline_report_json"]` | `STAGE_07_DETERMINISTIC_COMPILER` | `application/json` | Full serialized `AgenticPipelineReport` JSON document. |
| `cas/<sha256>` | Content-Addressable Store | All stages | Binary / UTF-8 | Immutable SHA-256 content-addressable blobs backing every intermediate and final artifact. |

---

### 5.3 Programmatic Python Usage Example

```python
from pathlib import Path

from opendv_agent_harness import (
    HarnessAuthConfig,
    HarnessBudget,
    HarnessSetupConfig,
    PipelineInputSpec,
    validate_pipeline_inputs,
)
from opendv_core.schema_registry import find_workspace_root

ws_root = find_workspace_root()
sample_dir = ws_root / "examples" / "pipeline_samples" / "global_commerce_vault"

# 1. Load sample pipeline input spec and Antigravity / Claude harness configs
input_spec = PipelineInputSpec.from_file(sample_dir / "pipeline_input.yaml")
agy_setup = HarnessSetupConfig.from_file(sample_dir / "harness_config_antigravity.yaml")
claude_setup = HarnessSetupConfig.from_file(sample_dir / "harness_config_claude.yaml")

assert agy_setup.provider == "antigravity"
assert agy_setup.model_overrides == {
    "reasoning": "gemini-4-argon-high",
    "fast": "gemini-3.8-flash-high",
}
assert agy_setup.auth.auth_mode == "api_key_env"
assert agy_setup.auth.api_key_env_var == "GEMINI_API_KEY"

assert claude_setup.provider == "claude"
assert claude_setup.auth.auth_mode == "api_key_env"
assert claude_setup.auth.api_key_env_var == "ANTHROPIC_API_KEY"

# 2. Construct a Vertex AI ADC config programmatically with semantic tiers & per-agent overrides
vertex_setup = HarnessSetupConfig(
    provider="antigravity",
    model_overrides={"reasoning": "gemini-4-argon-high", "fast": "gemini-3.8-flash-high"},
    agent_models={"ship-vault": "gemini-4-argon-high"},
    auth=HarnessAuthConfig(
        auth_mode="vertex_adc",
        gcp_project="enterprise-dv-prod",
        gcp_location="us-central1",
        require_credentials_present=True,
    ),
    budget=HarnessBudget(max_turns=12, max_cost_usd=5.0),
)
runtime_cfg = vertex_setup.to_harness_config(workspace_dir=Path("/tmp"))
assert runtime_cfg.env["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
assert runtime_cfg.env["GOOGLE_CLOUD_PROJECT"] == "enterprise-dv-prod"

# 3. Run deterministic pre-flight validation
val_report = validate_pipeline_inputs(input_spec, vertex_setup, base_dir=sample_dir)
assert val_report.passed is True
```
