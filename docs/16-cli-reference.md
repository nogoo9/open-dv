# Open-DV CLI Reference (`opendv` & `opendvctl`)

<!-- AUTO-GENERATED FROM schemas/v1/cli-spec.json. DO NOT EDIT DIRECTLY. -->

> Canonical reference documentation for Open-DV command-line interfaces.
> **Specification Version:** `1.0.0` · **Contract Version:** `1.0.0`

---

## 1. Architectural Overview: The Two-CLI Model

Open-DV adheres to a strict **Two-CLI Architecture** (Decisions D6 and D7):

| Characteristic | `opendv` (In-Process Standalone) | `opendvctl` (Platform Client) |
| :--- | :--- | :--- |
| **Implementation** | Pure Python 3.12+ package | Compiled single-file Bun executable |
| **Execution Engine** | `LocalExecutor` directly calling `opendv-service` | `RemoteExecutor` calling Platform REST & SSE API |
| **Target Audience** | Local developers, air-gapped CI/CD, symbolic verification | Platform users, Web UI agents, remote teams |
| **Runtimes Required** | Python 3.12+ with virtualenv | **None** (Self-contained binary; no Node, Bun, or Python) |
| **Network Ports** | Zero network sockets (strictly blocked) | Connects via `OPENDV_API_URL` / `--api-url` |
| **Output Determinism** | RFC 8785 JSON (`--json`) & pre-rendered text | RFC 8785 JSON (`--json`) & pre-rendered text |
| **Hidden Introspection** | `--dump-cli-tree` (Canonical JCS) | `--dump-cli-tree` (Canonical JCS) |

---

## 2. Global Options

The following global options apply across all top-level commands in both CLIs:

| Flag | Short | Type | Default | Choices / Env Var | Description |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `--format` | `-f` | `string` | `toon` | Choices: toon, json, text | Output formatting style (diagnostic TOON, machine RFC 8785 JSON, or human table) |
| `--verbose` | `-v` | `boolean` | `False` | — | Enable verbose diagnostic logs |
| `--version` | — | `boolean` | `False` | — | Show OpenDV CLI version |
| `--json` | — | `boolean` | `False` | — | Format command output as deterministic RFC 8785 canonical JSON |
| `--toon` | — | `boolean` | `False` | — | Print compact diagnostic @toon_v1 trace output |
| `--role` | — | `string` | `INTERNAL_MODELER` | Choices: PUBLIC, INTERNAL_MODELER, DATA_STEWARD, AUDITOR_FINANCE, RESTRICTED_PII_CUSTODIAN; Env: `OPENDV_ROLE` | 5-Tier RBAC role clearance context |
| `--api-url` | — | `string` | — | Env: `OPENDV_API_URL` | Open-DV Platform REST/SSE API base endpoint (`opendvctl` only) |

---

## 3. Command Reference

### `opendv profile` / `opendvctl profile`

**Availability:** *(Both CLIs)*  
**Description:** Profile raw CSV or data files to discover schema, types, candidate keys, and empirical distributions (Graph B)  
**Summary:** Profile Bronze tabular sources to extract empirical Graph B schema

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--source` | — | `string` | No | — | No | Source URI or adapter spec string to profile |
| `--file` | — | `path` | No | — | Yes | Path to data file to profile (backwards compatibility alias for --source) |
| `--source-csv` | `-s` | `path` | No | — | Yes | Path to source CSV file |
| `--graph-b` | `-g` | `path` | No | — | Yes | Path to existing Graph B profile to validate |
| `--project-id` | `-p` | `string` | No | — | No | Associated project identifier |
| `--source-system` | — | `string` | No | — | No | Origin source system identifier |
| `--table-name` | `-t` | `string` | No | — | No | Target physical table name |

- **Request DTO:** `ProfileRequest`
- **Response DTO:** `ProfileResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv profile --help

# Client execution
opendvctl profile --api-url $OPENDV_API_URL --json
```

### `opendv compile` / `opendvctl compile`

**Availability:** *(Both CLIs)*  
**Description:** Compile Data Vault 2.0 contract into Canonical Intermediate Representation (IR) and 4-Zone SQL  
**Summary:** Compile Data Vault contract YAML to 4-Zone SQL bundle

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--contract` | `-c` | `path` | Yes | — | Yes | Path to Data Vault 2.0 YAML contract |
| `--canonical-ir` | `-i` | `path` | No | — | Yes | Optional pre-existing Canonical IR JSON path |
| `--run-id` | `-r` | `string` | No | — | No | Associated pipeline run ID |

- **Request DTO:** `CompileRequest`
- **Response DTO:** `CompileResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv compile --help

# Client execution
opendvctl compile --api-url $OPENDV_API_URL --json
```

### `opendv verify` / `opendvctl verify` (aliases: `axi-dv`)

**Availability:** *(Both CLIs)*  
**Description:** Execute Axi-DV verification gates against Data Vault 2.0 contracts, profiles, and compiled bundles  
**Summary:** Verify Data Vault 2.0 contract against Axi-DV gates

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--contract` | `-c` | `path` | Yes | — | Yes | Path to Data Vault 2.0 contract YAML |
| `--graph-a` | `-a` | `path` | No | — | Yes | Path to Graph A business ontology YAML |
| `--graph-b` | `-b` | `path` | No | — | Yes | Path to Graph B statistical profile JSON |
| `--prior-contract` | — | `path` | No | — | Yes | Path to baseline contract YAML for grain/nullability drift checks |
| `--compiled-sql` | `-s` | `path` | No | — | Yes | Path to compiled 4-Zone SQL bundle for AST validation |
| `--run-id` | `-r` | `string` | No | — | No | Associated pipeline run ID |
| `--apply-codemods` | — | `boolean` | No | `True` | No | Automatically synthesize and apply rule-governed codemod repairs |

- **Request DTO:** `VerifyRequest`
- **Response DTO:** `VerifyResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv verify --help

# Client execution
opendvctl verify --api-url $OPENDV_API_URL --json
```

### `opendv sandbox` / `opendvctl sandbox`

**Availability:** *(Both CLIs)*  
**Description:** Execute DuckDB sandbox assertions, point-in-time joins, and dynamic adversarial batch stress-testing  
**Summary:** Execute DuckDB dynamic validation sandbox queries

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | No | — | No | Target pipeline run ID containing compiled sandbox assets |
| `--contract` | `-c` | `path` | No | — | Yes | Path to Data Vault 2.0 contract YAML |
| `--canonical-ir` | `-i` | `path` | No | — | Yes | Path to Canonical IR JSON |
| `--query-type` | `-q` | `string` | No | `ADVERSARIAL_BATCHES_1_TO_4` | No | Sandbox verification scenario type |
| `--cutoff` | — | `string` | No | `2026-11-15 23:59:59` | No | Point-in-time assertion timestamp cutoff (YYYY-MM-DD HH:MM:SS) |
| `--sql` | `-s` | `string` | No | — | No | Custom ad-hoc SQL query to execute against active sandbox zones |

- **Request DTO:** `SandboxQueryRequest`
- **Response DTO:** `SandboxQueryResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv sandbox --help

# Client execution
opendvctl sandbox --api-url $OPENDV_API_URL --json
```

### `opendv pipeline` / `opendvctl pipeline`

**Availability:** *(Both CLIs)*  
**Description:** Manage and execute end-to-end multi-agent Data Vault 2.0 synthesis pipelines  
**Summary:** Execute or validate end-to-end Data Vault synthesis pipelines

#### Subcommands of `pipeline`

##### `pipeline run` *(Both CLIs)*

Execute end-to-end 8-stage synthesis pipeline run

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--input` | `-i` | `path` | Yes | — | Yes | Path to pipeline input specification YAML |
| `--harness-config` | `-c` | `path` | No | — | No | Path to agent harness configuration YAML |
| `--initial-contract` | — | `path` | No | — | Yes | Path to starting contract YAML |
| `--provider` | `-p` | `string` | No | — | No | Target harness provider override |
| `--hold-policy` | — | `string` | No | — | No | Captain hold intervention policy |
| `--output-dir` | `-o` | `path` | No | — | No | Target directory for generated artifacts and reports |
| `--stream` | `-s` | `boolean` | No | `True` | No | Stream live stage execution progress |
| `--stream-tokens` | — | `boolean` | No | `False` | No | Stream live LLM tokens and thinking deltas |
| `--wave-id` | `-w` | `string` | No | — | No | Target wave identifier (e.g. W0, W1, W2) |

- **Request DTO:** `PipelineRunRequest`
- **Response DTO:** `PipelineRunHandle`

**Example Invocations:**
```bash
# Standalone execution
opendv pipeline run --help

# Client execution
opendvctl pipeline run --api-url $OPENDV_API_URL --json
```

##### `pipeline validate` *(Both CLIs)*

Deterministically validate pipeline input specifications, sources, and harness configuration

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--input` | `-i` | `path` | Yes | — | Yes | Path to input spec file |
| `--harness-config` | `-c` | `path` | No | — | No | Path to harness config file |
| `--initial-contract` | — | `path` | No | — | Yes | Path to initial contract YAML |
| `--provider` | `-p` | `string` | No | — | No | Target harness provider override |
| `--hold-policy` | — | `string` | No | — | No | Steward hold policy |

- **Request DTO:** `PipelineValidateRequest`

**Example Invocations:**
```bash
# Standalone execution
opendv pipeline validate --help

# Client execution
opendvctl pipeline validate --api-url $OPENDV_API_URL --json
```

##### `pipeline report` *(Standalone `opendv` only)*

Inspect or dump a completed pipeline run report

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | No | — | No | Pipeline run ID |
| `--report-path` | `-o` | `path` | No | — | No | Export report path |


**Example Invocations:**
```bash
# Standalone execution
opendv pipeline report --help

# Client execution
opendvctl pipeline report --api-url $OPENDV_API_URL --json
```

### `opendv run` / `opendvctl run`

**Availability:** *(Both CLIs)*  
**Description:** Manage platform runs: list, query status, trigger, abort, or inspect event streams  
**Summary:** Manage pipeline execution runs

#### Subcommands of `run`

##### `run list` *(Both CLIs)*

List runs for a project

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--project-id` | `-p` | `string` | No | — | No | Filter by project ID |


**Example Invocations:**
```bash
# Standalone execution
opendv run list --help

# Client execution
opendvctl run list --api-url $OPENDV_API_URL --json
```

##### `run get` *(Both CLIs)*

Get run details and current lifecycle status

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |

- **Response DTO:** `ProjectRunDTO`

**Example Invocations:**
```bash
# Standalone execution
opendv run get --help

# Client execution
opendvctl run get --api-url $OPENDV_API_URL --json
```

##### `run create` *(Both CLIs)*

Create and initialize a run

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--project-id` | `-p` | `string` | Yes | — | No | Project ID |
| `--harness` | — | `string` | No | — | No | Harness configuration name |
| `--contract` | `-c` | `path` | No | — | Yes | Contract YAML path |
| `--initial-contract-path` | — | `path` | No | — | Yes | Path to initial contract YAML file |
| `--initial-contract` | — | `string` | No | — | No | Inline starting contract YAML or JSON string |
| `--wave-id` | `-w` | `string` | No | — | No | Target wave identifier |

- **Request DTO:** `CreateRunRequest`
- **Response DTO:** `PipelineRunHandle`

**Example Invocations:**
```bash
# Standalone execution
opendv run create --help

# Client execution
opendvctl run create --api-url $OPENDV_API_URL --json
```

##### `run abort` *(Both CLIs)*

Abort an in-flight pipeline run

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |


**Example Invocations:**
```bash
# Standalone execution
opendv run abort --help

# Client execution
opendvctl run abort --api-url $OPENDV_API_URL --json
```

##### `run events` *(Both CLIs)*

Stream or query events for a run

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--last-event-id` | — | `string` | No | — | No | Replay starting after this event ID |
| `--role` | — | `string` | No | `PUBLIC` | No | RBAC role clearance for event redaction |


**Example Invocations:**
```bash
# Standalone execution
opendv run events --help

# Client execution
opendvctl run events --api-url $OPENDV_API_URL --json
```

##### `run explorer` *(Both CLIs)*

Fetch 3-Level Explorer bundle for a completed or active run

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--wave-id` | `-w` | `string` | No | — | No | Target wave identifier |

- **Response DTO:** `ExplorerBundleDTO`

**Example Invocations:**
```bash
# Standalone execution
opendv run explorer --help

# Client execution
opendvctl run explorer --api-url $OPENDV_API_URL --json
```

##### `run artifact` *(Both CLIs)*

Fetch specific run artifact content and metadata

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--artifact-id` | `-a` | `string` | Yes | — | No | Artifact ID to retrieve |

- **Response DTO:** `RunArtifactDTO`

**Example Invocations:**
```bash
# Standalone execution
opendv run artifact --help

# Client execution
opendvctl run artifact --api-url $OPENDV_API_URL --json
```

### `opendv hold` / `opendvctl hold`

**Availability:** *(Both CLIs)*  
**Description:** Inspect and resolve Captain-Hold human-in-the-loop escalations  
**Summary:** Manage Captain-Hold escalations

#### Subcommands of `hold`

##### `hold list` *(Both CLIs)*

List holds for a run

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |


**Example Invocations:**
```bash
# Standalone execution
opendv hold list --help

# Client execution
opendvctl hold list --api-url $OPENDV_API_URL --json
```

##### `hold decide` *(Both CLIs)*

Resolve a Captain-Hold escalation with APPROVE, REJECT, or ANSWER

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--action` | `-a` | `string` | Yes | — | No | Decision action to take |
| `--hold-id` | — | `string` | No | — | No | Specific hold ID |
| `--event-id` | `-e` | `string` | No | — | No | Triggering event ID |
| `--candidate-id` | — | `string` | No | — | No | Selected candidate option ID |
| `--notes` | `-n` | `string` | No | — | No | Steward justification notes |
| `--steward-id` | `-s` | `string` | No | `steward-cli` | No | Acting steward identity |

- **Request DTO:** `CaptainHoldDecisionRequest`
- **Response DTO:** `CaptainHoldDecisionResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv hold decide --help

# Client execution
opendvctl hold decide --api-url $OPENDV_API_URL --json
```

##### `hold approve` *(Both CLIs)*

Approve a Captain-Hold escalation to proceed

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--hold-id` | — | `string` | No | — | No | Specific hold ID |
| `--event-id` | `-e` | `string` | No | — | No | Triggering event ID |
| `--candidate-id` | — | `string` | No | — | No | Selected candidate option ID |
| `--notes` | `-n` | `string` | No | — | No | Steward justification notes |
| `--steward-id` | `-s` | `string` | No | `steward-cli` | No | Acting steward identity |

- **Request DTO:** `CaptainHoldDecisionRequest`
- **Response DTO:** `CaptainHoldDecisionResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv hold approve --help

# Client execution
opendvctl hold approve --api-url $OPENDV_API_URL --json
```

##### `hold reject` *(Both CLIs)*

Reject a Captain-Hold escalation

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--hold-id` | — | `string` | No | — | No | Specific hold ID |
| `--event-id` | `-e` | `string` | No | — | No | Triggering event ID |
| `--notes` | `-n` | `string` | No | — | No | Steward rejection notes |
| `--steward-id` | `-s` | `string` | No | `steward-cli` | No | Acting steward identity |

- **Request DTO:** `CaptainHoldDecisionRequest`
- **Response DTO:** `CaptainHoldDecisionResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv hold reject --help

# Client execution
opendvctl hold reject --api-url $OPENDV_API_URL --json
```

##### `hold answer` *(Both CLIs)*

Answer a Captain-Hold prompt by choosing a candidate option

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--run-id` | `-r` | `string` | Yes | — | No | Target run ID |
| `--hold-id` | — | `string` | No | — | No | Specific hold ID |
| `--event-id` | `-e` | `string` | No | — | No | Triggering event ID |
| `--candidate-id` | — | `string` | No | — | No | Selected candidate option ID |
| `--notes` | `-n` | `string` | No | — | No | Steward response notes |
| `--steward-id` | `-s` | `string` | No | `steward-cli` | No | Acting steward identity |

- **Request DTO:** `CaptainHoldDecisionRequest`
- **Response DTO:** `CaptainHoldDecisionResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv hold answer --help

# Client execution
opendvctl hold answer --api-url $OPENDV_API_URL --json
```

### `opendv ontology` / `opendvctl ontology`

**Availability:** *(Both CLIs)*  
**Description:** Manage and evolve enterprise Business Concept ontologies (Graph A), proposals, and deltas  
**Summary:** Manage Graph A ontology suggestions, deltas, diffs, and decisions

#### Subcommands of `ontology`

##### `ontology suggest` *(Both CLIs)*

Generate an additive ontology evolution proposal for Graph A from source data profiles (IP-1)

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--base` | `-b` | `path` | No | — | Yes | Path to base Graph A ontology YAML/JSON file |
| `--profile` | `-p` | `path` | No | — | Yes | Path to empirical Graph B data profile JSON file |
| `--source` | `-s` | `path` | No | — | Yes | Path to bronze source dataset file |
| `--context-doc` | `-c` | `path` | No | — | Yes | Path to business context document (e.g. glossary, dictionary, policy) |
| `--mode` | `-m` | `string` | No | `DELTA` | No | Recommender operation mode (BOOTSTRAP without base ontology, or DELTA) |
| `--domain-namespace` | — | `string` | No | — | No | Target domain namespace |
| `--recommender-id` | — | `string` | No | — | No | Identifier of specific ontology recommender plugin to use |
| `--output` | `-o` | `path` | No | — | No | Path to write output proposal JSON |

- **Request DTO:** `OntologySuggestionRequest`
- **Response DTO:** `OntologyProposal`

**Example Invocations:**
```bash
# Standalone execution
opendv ontology suggest --help

# Client execution
opendvctl ontology suggest --api-url $OPENDV_API_URL --json
```

##### `ontology validate-delta` *(Both CLIs)*

Deterministically validate an additive ontology delta against deployed contract and base Graph A (ONT-EVO-001..003)

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--delta` | `-d` | `path` | Yes | — | Yes | Path to OntologyDelta JSON/YAML file to validate |
| `--base` | `-b` | `path` | Yes | — | Yes | Path to base Graph A ontology YAML/JSON file |
| `--contract` | `-c` | `path` | No | — | Yes | Path to deployed Data Vault contract YAML file |

- **Request DTO:** `OntologyValidateDeltaRequest`
- **Response DTO:** `OntologyValidationResult`

**Example Invocations:**
```bash
# Standalone execution
opendv ontology validate-delta --help

# Client execution
opendvctl ontology validate-delta --api-url $OPENDV_API_URL --json
```

##### `ontology diff` *(Both CLIs)*

Compute structural diff between two Graph A ontologies (additions, refinements, breaking changes)

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--base` | `-b` | `path` | Yes | — | Yes | Path to base Graph A ontology YAML/JSON file |
| `--target` | `-t` | `path` | Yes | — | Yes | Path to target Graph A ontology YAML/JSON file |

- **Request DTO:** `OntologyDiffRequest`
- **Response DTO:** `OntologyDiff`

**Example Invocations:**
```bash
# Standalone execution
opendv ontology diff --help

# Client execution
opendvctl ontology diff --api-url $OPENDV_API_URL --json
```

##### `ontology accept` *(Both CLIs)*

Apply accepted delta operations to a base ontology, generate Graph A vN+1 with CAS lineage, and record decision

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--delta` | `-d` | `path` | Yes | — | Yes | Path to OntologyDelta JSON/YAML file |
| `--base` | `-b` | `path` | Yes | — | Yes | Path to base Graph A ontology YAML/JSON file |
| `--contract` | `-c` | `path` | No | — | Yes | Path to deployed Data Vault contract YAML file |
| `--op-id` | — | `string` | No | — | No | Specific operation ID to accept (can be repeated or comma-separated) |
| `--decided-by` | — | `string` | No | `steward` | No | Identifier of the decision maker (e.g. 'steward' or policy name) |
| `--notes` | `-n` | `string` | No | `` | No | Steward decision rationale notes |
| `--output` | `-o` | `path` | No | — | No | Path to write resulting Graph A vN+1 YAML file |

- **Request DTO:** `OntologyDecisionRequest`
- **Response DTO:** `OntologyDecisionResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv ontology accept --help

# Client execution
opendvctl ontology accept --api-url $OPENDV_API_URL --json
```

##### `ontology reject` *(Both CLIs)*

Record steward rejection of an ontology delta

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--delta` | `-d` | `path` | No | — | Yes | Path to OntologyDelta JSON/YAML file |
| `--proposal-id` | `-p` | `string` | No | — | No | Proposal identifier being rejected |
| `--base` | `-b` | `path` | No | — | Yes | Path to base Graph A ontology YAML/JSON file |
| `--decided-by` | — | `string` | No | `steward` | No | Identifier of the decision maker |
| `--notes` | `-n` | `string` | No | `` | No | Steward rejection rationale notes |

- **Request DTO:** `OntologyDecisionRequest`
- **Response DTO:** `OntologyDecisionResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv ontology reject --help

# Client execution
opendvctl ontology reject --api-url $OPENDV_API_URL --json
```

---

## 4. Exit Codes & Error Mapping

Both CLIs adhere to normative exit code standards across all failure scenarios:

- `0`: Operation succeeded (`overall_passed: true` or status `COMPLETED`).
- `1`: Domain or verification rejection (`overall_passed: false` or gate failure).
- `2`: Syntax, argument error, configuration error, or schema validation failure.

### Domain Error Mapping Specification

| Error Code | Exit Code | HTTP Status | Problem Title | Problem Type URI |
| :--- | :---: | :---: | :--- | :--- |
| `OK` | `0` | `200` | OK | `urn:opendv:ok` |
| `VERIFICATION_FAILED` | `1` | `200` | Verification Rejection | `urn:opendv:verification-failed` |
| `PATH_CONTAINMENT_ERROR` | `2` | `400` | Path Containment Violation | `urn:opendv:path-containment-error` |
| `INVALID_REQUEST` | `2` | `400` | Bad Request | `urn:opendv:invalid-request` |
| `RESOURCE_NOT_FOUND` | `2` | `404` | Resource Not Found | `urn:opendv:not-found` |
| `RUN_NOT_FOUND` | `2` | `404` | Run Not Found | `urn:opendv:run-not-found` |
| `PROJECT_NOT_FOUND` | `2` | `404` | Project Not Found | `urn:opendv:project-not-found` |
| `HOLD_NOT_FOUND` | `2` | `404` | Hold Not Found | `urn:opendv:hold-not-found` |
| `ARTIFACT_NOT_FOUND` | `2` | `404` | Artifact Not Found | `urn:opendv:artifact-not-found` |
| `PAYLOAD_TOO_LARGE` | `2` | `413` | Payload Too Large | `urn:opendv:payload-too-large` |
| `UNSUPPORTED_FORMAT` | `2` | `415` | Unsupported Format | `urn:opendv:unsupported-format` |
| `INPUT_VALIDATION_ERROR` | `2` | `422` | Input Validation Error | `urn:opendv:input-validation-error` |
| `SCHEMA_VALIDATION_ERROR` | `2` | `422` | Schema Validation Error | `urn:opendv:schema-validation-error` |
| `SOURCE_LOAD_ERROR` | `2` | `422` | Source Ingestion Error | `urn:opendv:source-load-error` |
| `SANDBOX_EXECUTION_ERROR` | `2` | `422` | Sandbox Execution Failure | `urn:opendv:sandbox-error` |
| `PROVIDER_CONFIG_ERROR` | `2` | `422` | Provider Configuration Error | `urn:opendv:provider-config-error` |
| `DETERMINISTIC_PROVIDER_REJECTED` | `2` | `422` | Deterministic Provider Prohibited | `urn:opendv:deterministic-rejected` |
| `CONTRACT_VERSION_MISMATCH` | `2` | `422` | Contract Version Mismatch | `urn:opendv:version-mismatch` |
| `ONTOLOGY_RECOMMENDER_UNAVAILABLE` | `2` | `422` | Ontology Recommender Unavailable | `urn:opendv:ontology-recommender-unavailable` |
| `CAS_INTEGRITY_FAILURE` | `2` | `500` | CAS Digest Integrity Failure | `urn:opendv:cas-integrity-failure` |
| `INTERNAL_ERROR` | `2` | `500` | Internal Server Error | `urn:opendv:internal-error` |
| `API_UNREACHABLE` | `2` | `503` | API Service Unreachable | `urn:opendv:api-unreachable` |
| `PROVIDER_UNAVAILABLE` | `2` | `503` | Harness Provider Unavailable | `urn:opendv:provider-unavailable` |

---

## 5. Output Formats & Canonical Serialization

Both CLIs support three uniform output modes configured via `--format` / `-f`:

1. `--json`: Emits bit-for-bit canonical JSON compliant with **RFC 8785 JSON Canonicalization Scheme (JCS)**.
2. `--toon`: Emits compact diagnostic `@toon_v1` traces pre-rendered by `opendv-service`.
3. `--format text`: Emits human-readable ANSI status tables pre-rendered by `opendv-service`.

