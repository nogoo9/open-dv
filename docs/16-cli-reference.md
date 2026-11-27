# Open-DV V2 CLI Reference

> Generated from `schemas/v2/cli-spec.json` (`cli_version: 2.0.0`).

## Global Options

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--api-url` | `string` | — | Route commands to a running `:8420` server instead of in-process mode. |
| `--json` | `boolean` | `False` | Emit structured `CliEnvelope` JSON payloads. |
| `--env-first` | `boolean` | `False` | Prefer process environment variables ahead of keyring/headless secrets. |

## Commands

### `opendv agents inspect`

Show full agent prompt metadata and workspace trust state.

- **HTTP Route:** `GET /api/v1/agents/{agent_id}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--agent-id` | `argument:string` | yes | — | Agent identifier to inspect. |

### `opendv agents list`

List built-in and workspace agent packs.

- **HTTP Route:** `GET /api/v1/agents`

### `opendv doctor`

Run Layer-4 diagnostic checks across service health, agent harnesses, and MCP servers.

- **HTTP Route:** `GET /api/v1/system/doctor`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--check-agents` | `boolean` | no | `False` | Probe CLI harness binaries, packs, and secrets. |
| `--check-mcp` | `boolean` | no | `False` | Inspect registered external MCP endpoints and secrets. |
| `--probe` | `boolean` | no | `False` | Run live Tier-2 authentication probe on active harness_provider. |

### `opendv mcp list`

List registered external MCP servers.

- **HTTP Route:** `GET /api/v1/mcp/servers`

### `opendv mcp register`

Register or update a bounded external MCP server.

- **HTTP Route:** `POST /api/v1/mcp/servers`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--id` | `string` | yes | — | Unique server identifier. |
| `--transport` | `string` | yes | — | stdio or streamable_http. |
| `--command` | `string` | no | — | JSON argv list for stdio transport. |
| `--url` | `string` | no | — | HTTPS URL for streamable_http transport. |
| `--secret-ref` | `string` | no | — | Secret key referenced by connector or MCP server. |
| `--allow-scout-calls` | `boolean` | no | `False` |  |
| `--allow-document-pages` | `boolean` | no | `False` |  |

### `opendv mcp remove`

Unregister an external MCP server.

- **HTTP Route:** `DELETE /api/v1/mcp/servers/{server_id}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--id` | `string` | yes | — | Server identifier to delete. |

### `opendv mcp serve`

Serve the 17-tool Open-DV lifecycle MCP server over stdio.

- **Execution:** Local / In-process

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--transport` | `string` | no | `stdio` | MCP transport (stdio). |
| `--project` | `string` | no | `` | Project identifier. |

### `opendv model profile`

Statelessly profile sources, compute PK containment, and survey modeling hints.

- **Execution:** Local / In-process

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--source` | `string` | no | — | Repeatable <kind>:<uri> source specification. |
| `--sources-file` | `path` | no | — | Path to JSON or YAML source manifest. |
| `--sample-rows` | `integer` | no | `200` | Max proving rows sampled per table (1..5000). |
| `--no-real-row-proving` | `boolean` | no | `False` | Disable real-row proving capture. |
| `--max-questions` | `integer` | no | `5` | Max clarification survey questions (0 returns all remaining). |
| `--questions-offset` | `integer` | no | `0` | Zero-based pagination offset for questions. |
| `--out` | `path` | no | — | Write ProfileBundle JSON to file. |

### `opendv model run`

Mode-2 stateless managed CEGAR modeling run (with optional --guide and --profile).

- **Execution:** Local / In-process

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--input` | `path` | yes | — | Path to DVModelingRequest JSON. |
| `--profile` | `path` | no | — | Optional ProfileBundle JSON from `opendv model profile`. |
| `--guide` | `path` | no | — | Optional workspace dv2-modeling-guide.md path. |
| `--dry-run` | `boolean` | no | `False` | Validate request, guide trust, and PII only. |
| `--out` | `path` | no | — | Write modeling result JSON to file. |

### `opendv model spec`

Emit annotated DV2.0 schemas, constraints, modeling guide, and preferences.

- **Execution:** Local / In-process

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--part` | `string` | no | `all` | all, schema, guide, or preferences. |

### `opendv model verify`

Mode-1 zero-LLM deterministic verification (L1 + V1-V6 + tiered V6b).

- **Execution:** Local / In-process

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--input` | `path` | yes | — | Path to modeling request JSON. |
| `--draft-ir` | `path` | yes | — | Path to DraftVaultIR JSON. |
| `--profile` | `path` | no | — | Optional ProfileBundle JSON from `opendv model profile`. |
| `--dry-run` | `boolean` | no | `False` | Validate schemas and PII tokenization without proving. |
| `--out` | `path` | no | — | Write verification result JSON to file. |

### `opendv ontology answer`

Submit clarification answers for a Step 4 HOLD_CHECKPOINTED run.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/ontology/runs/{rid}/answers`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | yes | — | Run identifier. |
| `--answers-json` | `string` | yes | — | Clarification answers JSON string or file path. |
| `--wait` | `boolean` | no | `False` | Poll until run finishes or pauses. |

### `opendv ontology commit`

Commit validated Graph A YAML revision with optimistic concurrency guard.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/ontology/commit`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--yaml` | `string` | yes | — | Path to Graph A YAML file. |
| `--base-revision` | `string` | yes | — |  |
| `--rationale` | `string` | yes | — |  |
| `--waive-quarantine` | `boolean` | no | `False` |  |

### `opendv ontology construct`

Start Step 4 ontology construction discovery run.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/ontology/construct`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--discovery-mode` | `string` | no | — | incremental_new, incremental_reverify, or full_rebuild. |
| `--hint` | `string` | no | — | Optional steering hint. |
| `--wait` | `boolean` | no | `False` | Poll until run finishes or pauses. |

### `opendv ontology control`

Send live pause, hint, or raise-budget control signal to a Step 4 run.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/ontology/runs/{rid}/controls`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | yes | — | Run identifier. |
| `--action` | `string` | yes | — | Control action name. |
| `--hint` | `string` | no | — | Optional steering hint. |
| `--extra-turns` | `integer` | no | — |  |
| `--extra-tokens` | `integer` | no | — |  |
| `--extra-timeout` | `integer` | no | — |  |

### `opendv ontology export`

Export active Graph A ontology (optionally including staged candidates).

- **HTTP Route:** `GET /api/v1/projects/{project_id}/ontology/export`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--format` | `string` | no | `yaml` | yaml, json, or ttl. |
| `--include-staged` | `boolean` | no | `False` |  |

### `opendv ontology fibo-qa`

Query vendored FIBO-core reference pack using deterministic `bm25_fibo_v1`.

- **HTTP Route:** `POST /api/v1/ontology/fibo-qa`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--query` | `string` | yes | — | Natural-language or concept query. |
| `--project` | `string` | no | — | Optional project context. |
| `--namespace` | `string` | no | — |  |
| `--top-k` | `integer` | no | `10` |  |

### `opendv ontology validate`

Dry-run ontology YAML validation against invariants and vault evolution locks.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/ontology/validate`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--yaml` | `string` | yes | — | Path to Graph A YAML file. |
| `--base-revision` | `string` | no | — |  |

### `opendv project delete`

Delete a project cleanly.

- **HTTP Route:** `DELETE /api/v1/projects/{project_id}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |

### `opendv project export`

Export a project bundle (`core_project_state` by default).

- **HTTP Route:** `GET /api/v1/projects/{project_id}/bundle`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--out` | `path` | yes | — | Output `.opendv.zip` bundle path. |
| `--full-demo` | `boolean` | no | `False` | Include raw sources in export bundle. |

### `opendv project import`

Import a signed `.opendv.zip` project bundle.

- **HTTP Route:** `POST /api/v1/projects/import`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--bundle-zip` | `argument:path` | yes | — | Path to `.opendv.zip` archive. |
| `--conflict` | `string` | no | `fail` | fail, rename, or replace. |
| `--trust-workspace` | `boolean` | no | `False` | Opt-in to trust embedded workspace agents. |

### `opendv project init`

Initialize a new Open-DV project.

- **HTTP Route:** `POST /api/v1/projects`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--namespace` | `string` | yes | — | Domain namespace URI/slug. |
| `--starter-pack` | `string` | no | — |  |
| `--workspace-seed` | `string` | no | — |  |
| `--baseline` | `string` | no | — | Baseline ontology file. |
| `--baseline-format` | `string` | no | `yaml` |  |

### `opendv project list`

List all projects in the workspace.

- **HTTP Route:** `GET /api/v1/projects`

### `opendv project settings set`

Atomically patch per-project execution settings for future runs.

- **HTTP Route:** `PATCH /api/v1/projects/{project_id}/settings`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--harness-provider` | `choice` | no | — | Default agent harness provider. |
| `--cegar-policy` | `choice` | no | — | Default CEGAR verification policy. |
| `--v7-critic-tier` | `choice` | no | — | Default V7 semantic critic tier. |
| `--dbt-sql-dialect` | `choice` | no | — | Default dbt SQL dialect. |
| `--secret-precedence` | `choice` | no | — | Secret resolution order. |
| `--max-turns` | `integer` | no | — | Max agent turns per run (1..500). |
| `--max-tokens` | `integer` | no | — | Max token budget per run (>= 1). |
| `--timeout-seconds` | `integer` | no | — | Per-turn timeout in seconds (1..7200). |
| `--discovery-mode` | `choice` | no | — | Default Step 4 ontology discovery mode. |
| `--model-override` | `string` | no | — | Set TARGET=MODEL override (repeatable). |
| `--clear-model-override` | `string` | no | — | Clear TARGET model override (repeatable). |
| `--clear` | `choice` | no | — | Clear nullable fields (repeatable). |

### `opendv project settings show`

Show persisted execution settings snapshot for a project.

- **HTTP Route:** `GET /api/v1/projects/{project_id}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |

### `opendv project show`

Show full metadata for one project.

- **HTTP Route:** `GET /api/v1/projects/{project_id}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |

### `opendv secrets delete`

Delete a stored secret key.

- **HTTP Route:** `DELETE /api/v1/secrets/{key}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--key` | `argument:string` | yes | — | Secret key to delete. |

### `opendv secrets list`

List secret presence and backend metadata (never exposes values).

- **HTTP Route:** `GET /api/v1/secrets`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--keys` | `string` | no | — | Optional comma-separated secret keys to probe. |

### `opendv secrets set`

Store a secret value safely without echoing plaintext to stdout or argv.

- **HTTP Route:** `POST /api/v1/secrets`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--key` | `argument:string` | yes | — | Secret key name. |
| `--stdin` | `boolean` | no | `False` | Read secret from stdin. |
| `--prompt` | `boolean` | no | `False` | Prompt interactively with masked input. |

### `opendv serve`

Launch the local `:8420` FastAPI Open-DV Workbench server (fail closed on remote bind).

- **Execution:** Local / In-process

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--host` | `string` | no | `127.0.0.1` | Bind host (loopback by default). |
| `--port` | `integer` | no | `8420` | HTTP listen port. |
| `--allow-remote-unauthenticated` | `boolean` | no | `False` | Explicit opt-in required before binding a non-loopback host. |
| `--trust-workspace` | `path` | no | — | Trust workspace agent overrides before serving. |

### `opendv settings models`

List offline default model suggestions for the active or specified provider.

- **HTTP Route:** `GET /api/v1/agents/readiness`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--provider` | `choice` | no | — | Provider id (defaults to active harness_provider). |

### `opendv settings profile delete`

Delete a saved user-global connection profile.

- **HTTP Route:** `PUT /api/v1/settings`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--name` | `string` | yes | — | Connection profile name to delete. |

### `opendv settings profile list`

List user-global saved connection profiles.

- **HTTP Route:** `GET /api/v1/settings`

### `opendv settings profile set`

Create or replace a named user-global connection profile.

- **HTTP Route:** `PUT /api/v1/settings`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--name` | `string` | yes | — | Unique connection profile name. |
| `--adapter` | `choice` | yes | — | Structured connector adapter identifier. |
| `--display-name` | `string` | no | — | Human-readable label (defaults to --name). |
| `--uri` | `string` | no | — | Source path or host URI (optional for databricks_uc when options.host is set). |
| `--options-json` | `string` | no | — | Connector options JSON object or file path. |
| `--secret-ref` | `string` | no | — | Secret key referenced by connector or MCP server. |
| `--selected-tables` | `string` | no | — | Selected table filter (repeatable). |
| `--timeout-seconds` | `integer` | no | `30` | Profile inspection timeout in seconds. |

### `opendv settings profile test`

Inspect and validate a saved connection profile via `sources/inspect`.

- **HTTP Route:** `POST /api/v1/sources/inspect`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--name` | `string` | yes | — | Connection profile name to test. |

### `opendv settings project-set`

Partially update per-project modeling preferences, hints, and proving settings.

- **HTTP Route:** `PATCH /api/v1/projects/{project_id}/settings`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--pref` | `string` | no | — | Repeatable ModelingPreferences key=value update. |
| `--hints-file` | `path` | no | — | Path to business context hints file. |
| `--hints-text` | `string` | no | — | Inline business context hints text. |
| `--proving-sample-rows` | `integer` | no | — | Proving sample row limit (1..5000). |
| `--real-row-proving` | `boolean` | no | — | Toggle Step 2/3 real-row proving capture. |

### `opendv settings set`

Update workspace-level defaults with strict schema validation.

- **HTTP Route:** `PUT /api/v1/settings`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--harness-provider` | `choice` | no | — | Default agent harness provider. |
| `--cegar-policy` | `choice` | no | — | Default CEGAR verification policy. |
| `--v7-critic-tier` | `choice` | no | — | Default V7 semantic critic tier. |
| `--dbt-sql-dialect` | `choice` | no | — | Default dbt SQL dialect. |
| `--secret-backend` | `choice` | no | — | Secret backend (keyring or user_state_file). |
| `--secret-precedence` | `choice` | no | — | Secret resolution order. |
| `--max-turns` | `integer` | no | — | Max agent turns per run (1..500). |
| `--max-tokens` | `integer` | no | — | Max token budget per run (>= 1). |
| `--timeout-seconds` | `integer` | no | — | Per-turn timeout in seconds (1..7200). |
| `--model-override` | `string` | no | — | Set TARGET=MODEL override (repeatable). |
| `--clear-model-override` | `string` | no | — | Clear TARGET model override (repeatable). |
| `--clear` | `choice` | no | — | Clear nullable fields (repeatable). |

### `opendv settings show`

Show persisted workspace settings.

- **HTTP Route:** `GET /api/v1/settings`

### `opendv settings trust-workspace`

Approve or revoke SHA-256 hashes for `.opendv/agents/*/SKILL.md` overrides.

- **HTTP Route:** `PUT /api/v1/settings`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--path` | `string` | yes | — | Workspace directory path to trust or revoke. |
| `--revoke` | `boolean` | no | `False` | Revoke workspace trust via POST /api/v1/settings/trusted-workspaces/revoke. |

### `opendv source adapters`

List supported data and document source adapters.

- **HTTP Route:** `GET /api/v1/sources/adapters`

### `opendv source approve-scan`

Approve scanned PDF pages for vision/OCR extraction.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/evidence/documents/{did}/approve-scan`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--doc` | `string` | yes | — | Document source identifier. |
| `--pages` | `string` | yes | — | Comma-separated 1-based page numbers. |
| `--rationale` | `string` | yes | — | Audit rationale for approval. |
| `--allow-unredacted-external-ocr` | `boolean` | no | `False` | Explicit opt-in if external vision/OCR is enabled on redacted scans. |

### `opendv source attach`

Attach and profile a data/document source for a project.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/sources`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--kind` | `string` | yes | — | Connector adapter kind. |
| `--uri` | `string` | yes | — | Source URI or local path. |
| `--selected-tables` | `string` | no | — | Comma-separated table subset. |
| `--profile-timeout` | `integer` | no | `30` |  |
| `--options-json` | `string` | no | — | Connector options JSON object or file path. |
| `--secret-ref` | `string` | no | — | Secret key referenced by connector or MCP server. |

### `opendv source detach`

Detach an uncommitted source from a project.

- **HTTP Route:** `DELETE /api/v1/projects/{project_id}/sources/{sid}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--source` | `string` | yes | — | Source identifier. |

### `opendv source inspect`

Preview tables and redaction profile for a candidate source.

- **HTTP Route:** `POST /api/v1/sources/inspect`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--kind` | `string` | yes | — | Connector adapter kind. |
| `--uri` | `string` | yes | — | Source URI or local path. |
| `--options-json` | `string` | no | — | Connector options JSON object or file path. |
| `--secret-ref` | `string` | no | — | Secret key referenced by connector or MCP server. |

### `opendv source list`

List sources attached to a project.

- **HTTP Route:** `GET /api/v1/projects/{project_id}/sources`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |

### `opendv source note`

Attach a domain note document from a local file or inline `--text`.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/sources/upload`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--path` | `path` | no | — | Markdown/text note file. |
| `--text` | `string` | no | — | Inline domain note text. |

### `opendv source reattach`

Reattach or narrow selected tables on an existing source.

- **HTTP Route:** `PATCH /api/v1/projects/{project_id}/sources/{sid}`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--source` | `string` | yes | — | Source identifier. |
| `--uri` | `string` | no | — |  |
| `--selected-tables` | `string` | no | — |  |
| `--options-json` | `string` | no | — | Connector options JSON object or file path. |
| `--secret-ref` | `string` | no | — | Secret key referenced by connector or MCP server. |

### `opendv storage compile`

Compile CertifiedVaultIR into target SQL/dbt/Databricks/Iceberg bundles.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/storage/compile`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--target` | `string` | yes | — | Target id or 'all'. |
| `--dbt-dialect` | `string` | no | `duckdb` |  |
| `--verify-runtime` | `boolean` | no | `False` |  |

### `opendv storage migrate`

Apply certified storage schema DDL and bronze/DV2 load into local DuckDB or Parquet.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/storage/migrate`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--target` | `string` | yes | — | duckdb_local or parquet. |

### `opendv storage query`

Execute a read-only SQL query against local DuckDB/Parquet views.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/storage/query`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--sql` | `string` | yes | — | Read-only SELECT/WITH SQL statement. |
| `--limit` | `integer` | no | `1000` |  |

### `opendv storage regenerate`

Re-verify and regenerate local storage artifacts against CertifiedVaultIR.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/storage/regenerate`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--target` | `string` | yes | — | Target id or 'all'. |

### `opendv vault build`

Launch Step 6 Data Vault 2.0 synthesis (`VaultBuildReq` carries only `policy`).

- **HTTP Route:** `POST /api/v1/projects/{project_id}/runs`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--policy` | `string` | no | — | balanced, strict, fast, or symbolic_only. |
| `--wait` | `boolean` | no | `False` | Poll until run finishes or pauses. |

### `opendv vault control`

Send live pause, hint, accept-best, raise-budget, horizon-review, or revert control.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/runs/{rid}/controls`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | yes | — | Run identifier. |
| `--action` | `string` | yes | — | Control action name. |
| `--hint` | `string` | no | — | Optional steering hint. |
| `--extra-turns` | `integer` | no | — |  |
| `--extra-tokens` | `integer` | no | — |  |
| `--extra-timeout` | `integer` | no | — |  |

### `opendv vault hold`

Submit decision answers on a Step 6 HOLD_CHECKPOINTED run.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/runs/{rid}/decisions`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | yes | — | Run identifier. |
| `--answers-json` | `string` | yes | — | Clarification answers JSON string or file path. |
| `--wait` | `boolean` | no | `False` | Poll until run finishes or pauses. |

### `opendv vault horizon`

Accept, steer, or revert provisional horizon assumptions for a Step 6 run.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/runs/{rid}/horizon`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | yes | — | Run identifier. |
| `--action` | `string` | yes | — | accept, steer, or revert. |
| `--assumptions-json` | `string` | no | — | JSON object (or file) with horizon assumption answers or steering payload. |
| `--notes` | `string` | no | `` |  |
| `--added-fields` | `string` | no | — |  |
| `--wait` | `boolean` | no | `False` | Poll until run finishes or pauses. |

### `opendv vault logs`

Fetch persisted JSONL run observability events.

- **HTTP Route:** `GET /api/v1/projects/{project_id}/runs/{rid}/logs`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | no | — | Run id (defaults to latest). |
| `--since-seq` | `integer` | no | `0` |  |
| `--limit` | `integer` | no | — |  |

### `opendv vault status`

Inspect Step 6 vault governance, active wave, open Holds, and waived rules.

- **HTTP Route:** `GET /api/v1/projects/{project_id}/runs`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--run` | `string` | no | — | Optional Step 6 run identifier. |
| `--holds-only` | `boolean` | no | `False` | Show only open Hold questions. |

### `opendv vault verify`

Verify Draft/Certified Vault IR deterministically (exits 2 on gate failure).

- **HTTP Route:** `POST /api/v1/projects/{project_id}/vault/verify`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |

### `opendv vault waive`

Record a typed anti-pattern waiver rationale.

- **HTTP Route:** `POST /api/v1/projects/{project_id}/vault/waivers`

| Option | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `--project` | `string` | yes | — | Project identifier. |
| `--rule` | `string` | yes | — | Anti-pattern rule identifier. |
| `--entity` | `string` | yes | — | Target DV2 entity name. |
| `--rationale` | `string` | yes | — | Audit justification. |
| `--waiver-id` | `string` | no | — |  |

## Exit Codes

| Code | Name |
| --- | --- |
| `0` | `OK` |
| `1` | `EXECUTION_ERROR` |
| `2` | `VALIDATION_ERROR` |
| `3` | `NOT_FOUND` |
| `4` | `CONFLICT_OR_LOCKED` |
| `5` | `HOLD_OR_UNCERTIFIED` |
| `40` | `REMOTE_BIND_UNAUTHENTICATED` |
| `50` | `EMITTER_VERIFICATION_FAILED` |
