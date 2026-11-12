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
| `--role` | — | `string` | `INTERNAL_MODELER` | Choices: PUBLIC, INTERNAL_MODELER, DATA_STEWARD, AUDITOR_FINANCE, RESTRICTED_PII_CUSTODIAN, ADMIN, EDITOR, VIEWER, AUTOMATED, admin, editor, viewer, automated; Env: `OPENDV_ROLE` | 5-Tier RBAC role clearance context |
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

##### `ontology studio` *(Both CLIs)*

Interactive Ontology Construction Studio (FIBO, LiteParse, dual-mode engine, CAS sessions)

###### Subcommands of `ontology studio`

###### `ontology studio init` *(Standalone `opendv` only)*

Initialize local workbench loopback token and DACL permissions

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--force` | — | `boolean` | No | `False` | No | Force overwrite existing local token if authorized |
| `--show-token` | — | `boolean` | No | `False` | No | Display local authentication token in output |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio init --help
```

###### `ontology studio create` *(Both CLIs)*

Create a new ontology construction studio session

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | No | — | No | Optional explicit studio session identifier |
| `--domain-namespace` | `-n` | `string` | No | `default` | No | Domain namespace matching base Graph A |
| `--enterprise-owner` | `-o` | `string` | No | `Enterprise` | No | Enterprise owner organization or domain |
| `--ontology-id` | — | `string` | No | — | No | Custom ontology identifier |
| `--description` | `-d` | `string` | No | — | No | Session description or business purpose |
| `--from-workspace` | `-w` | `path` | No | — | No | Path to workspace Data Vault project to seed from |
| `--session-label` | `-l` | `string` | No | — | No | Human-readable label for session identification |

- **Request DTO:** `CreateSessionRequest`
- **Response DTO:** `StudioSessionDTO`

**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio create --help

# Client execution
opendvctl ontology studio create --api-url $OPENDV_API_URL --json
```

###### `ontology studio delete` *(Both CLIs)*

Delete an ontology construction studio session and sweep unreferenced CAS

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier to delete |
| `--force` | — | `boolean` | No | `False` | No | Force deletion even if session has active state |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio delete --help

# Client execution
opendvctl ontology studio delete --api-url $OPENDV_API_URL --json
```

###### `ontology studio list` *(Both CLIs)*

List active ontology construction studio sessions

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--limit` | `-l` | `integer` | No | `50` | No | Maximum number of sessions to display |
| `--offset` | — | `integer` | No | `0` | No | Pagination offset |
| `--status` | `-s` | `string` | No | — | No | Filter sessions by status |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio list --help

# Client execution
opendvctl ontology studio list --api-url $OPENDV_API_URL --json
```

###### `ontology studio status` *(Both CLIs)*

Show status, active version, head SHA-256, sources, and jobs for a studio session

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio status --help

# Client execution
opendvctl ontology studio status --api-url $OPENDV_API_URL --json
```

###### `ontology studio upload` *(Both CLIs)*

Upload candidate source documents (PDF, CSV, TSV, Parquet, MD, etc.) to session CAS

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--file` | `-f` | `path` | Yes | — | No | Path to document file to upload |
| `--do-not-send-to-agent` | — | `boolean` | No | `False` | No | Mark source confidential to suppress raw text transmission to external LLMs |
| `--confidential` | — | `boolean` | No | `False` | No | Alias for --do-not-send-to-agent (mark source confidential) |
| `--description` | `-d` | `string` | No | — | No | Optional description of source dataset |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio upload --help

# Client execution
opendvctl ontology studio upload --api-url $OPENDV_API_URL --json
```

###### `ontology studio parse` *(Both CLIs)*

Trigger document ingestion and layout parsing job

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--source-id` | — | `string` | No | — | No | Specific source document ID to parse (defaults to all pending) |
| `--source-ids` | — | `array` | No | — | No | List of source document IDs to parse (repeatable or comma-separated) |
| `--ocr-mode` | — | `string` | No | `auto` | No | OCR routing mode for scanned pages |
| `--stream` | — | `boolean` | No | `False` | No | Follow live SSE event stream until job completes |
| `--wait` | — | `boolean` | No | `False` | No | Block until job completes and return terminal status |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio parse --help

# Client execution
opendvctl ontology studio parse --api-url $OPENDV_API_URL --json
```

###### `ontology studio clarify` *(Both CLIs)*

Generate domain clarification questions from candidate terms

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--focus-concept` | — | `string` | No | — | No | Optional focus concept URI to target clarification |
| `--max-questions` | — | `integer` | No | `10` | No | Maximum number of questions to generate |
| `--mode` | `-m` | `string` | No | `auto` | No | Clarification execution mode |
| `--stream` | — | `boolean` | No | `False` | No | Follow live SSE event stream |
| `--wait` | — | `boolean` | No | `False` | No | Block until clarify job completes |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio clarify --help

# Client execution
opendvctl ontology studio clarify --api-url $OPENDV_API_URL --json
```

###### `ontology studio answer` *(Both CLIs)*

Submit human answers to clarification questions

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--file` | `-f` | `path` | No | — | Yes | Path to JSON file containing clarification answers |
| `--question-id` | `-q` | `string` | No | — | No | Clarification question identifier for single-answer submission |
| `--option` | `-o` | `array` | No | — | No | Selected clarification option key(s) |
| `--text` | `-t` | `string` | No | — | No | Free-text clarification response |
| `--expected-answers-sha256` | — | `string` | No | — | No | Expected SHA-256 digest of current answers state for OCC |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio answer --help

# Client execution
opendvctl ontology studio answer --api-url $OPENDV_API_URL --json
```

###### `ontology studio construct` *(Both CLIs)*

Trigger dual-mode ontology construction pipeline (deterministic or agent)

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--base-version` | — | `string` | No | — | No | Base SemVer version string |
| `--base-sha256` | — | `string` | No | — | No | 64-character hex SHA-256 digest of base ontology |
| `--mode` | `-m` | `string` | No | `auto` | No | Construction execution mode |
| `--auto-commit` | — | `boolean` | No | `False` | No | Automatically commit constructed delta without proposal staging (requires pinned base) |
| `--stream` | — | `boolean` | No | `False` | No | Follow live SSE event stream |
| `--wait` | — | `boolean` | No | `False` | No | Block until construct completes |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio construct --help

# Client execution
opendvctl ontology studio construct --api-url $OPENDV_API_URL --json
```

###### `ontology studio delta` *(Both CLIs)*

Validate or apply human ontology delta operations and steward revisions

###### Subcommands of `ontology studio delta`

###### `ontology studio delta validate` *(Both CLIs)*

Validate ontology delta operations against session head or base

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | No | — | No | Studio session identifier |
| `--file` | `-f` | `path` | No | — | Yes | Path to JSON/YAML file containing delta operations |
| `--delta` | — | `path` | No | — | Yes | Alias path to JSON/YAML file containing delta operations |
| `--base` | `-b` | `path` | No | — | Yes | Path to base Graph A ontology file (when validating outside session) |
| `--base-version` | — | `string` | No | — | No | Expected base SemVer version |
| `--base-sha256` | — | `string` | No | — | No | Expected base SHA-256 digest |
| `--verify-grounding` | — | `boolean` | No | `False` | No | Verify external concept grounding URIs against knowledge pack |
| `--low-confidence-waiver` | — | `boolean` | No | `False` | No | Include admin waiver for low-confidence grounding during validation |
| `--grounding-rationale` | — | `string` | No | — | No | Admin rationale for grounding waiver (min 10 chars) |
| `--waiver-rationale` | — | `string` | No | — | No | Alias for --grounding-rationale |
| `--waiver-key-file` | `-k` | `path` | No | — | No | Path to steward waiver secret key file |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio delta validate --help

# Client execution
opendvctl ontology studio delta validate --api-url $OPENDV_API_URL --json
```

###### `ontology studio delta apply` *(Both CLIs)*

Apply delta operations from file to active session

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--file` | `-f` | `path` | No | — | Yes | Path to JSON file containing delta operations |
| `--delta` | — | `path` | No | — | Yes | Alias path to JSON file containing delta operations |
| `--base-version` | — | `string` | No | — | No | Base SemVer version (optional; defaults to session head version) |
| `--base-sha256` | — | `string` | No | — | No | Base SHA-256 hash (optional on genesis; checked via OCC) |
| `--rationale` | `-r` | `string` | No | `` | No | Business rationale for mutation |
| `--low-confidence-waiver` | — | `boolean` | No | `False` | No | Admin waiver for concepts with low confidence grounding |
| `--grounding-rationale` | — | `string` | No | — | No | Admin rationale for grounding waiver (min 10 chars) |
| `--waiver-rationale` | — | `string` | No | — | No | Alias for --grounding-rationale |
| `--waiver-key-file` | `-k` | `path` | No | — | No | Path to steward waiver secret key file |
| `--ungrounded-concept` | — | `array` | No | — | No | Explicit list of concept URIs covered by grounding waiver |
| `--evidence-grounding-file` | — | `path` | No | — | Yes | Path to admin evidence grounding approval JSON |
| `--authorized-by` | — | `string` | No | — | No | Steward identity authorizing grounding waiver |
| `--authorized-role` | — | `string` | No | — | No | Steward role authorizing grounding waiver (must match admin role) |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio delta apply --help

# Client execution
opendvctl ontology studio delta apply --api-url $OPENDV_API_URL --json
```

###### `ontology studio proposal` *(Both CLIs)*

Manage candidate ontology proposals (list, show, accept, reject, rebase)

###### Subcommands of `ontology studio proposal`

###### `ontology studio proposal list` *(Both CLIs)*

List proposals for a studio session

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio proposal list --help

# Client execution
opendvctl ontology studio proposal list --api-url $OPENDV_API_URL --json
```

###### `ontology studio proposal show` *(Both CLIs)*

Display detailed proposal operations and grounding

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--proposal-id` | `-p` | `string` | Yes | — | No | Proposal identifier |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio proposal show --help

# Client execution
opendvctl ontology studio proposal show --api-url $OPENDV_API_URL --json
```

###### `ontology studio proposal accept` *(Both CLIs)*

Accept pending proposal and apply its delta to roll head forward

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--proposal-id` | `-p` | `string` | Yes | — | No | Proposal identifier |
| `--base-sha256` | — | `string` | Yes | — | No | Expected base SHA-256 for optimistic concurrency check |
| `--allow-self-accepted-proposals` | — | `boolean` | No | `False` | No | Admin override allowing acceptance of own proposal (requires admin role) |
| `--low-confidence-waiver` | — | `boolean` | No | `False` | No | Admin waiver for low-confidence external mappings |
| `--grounding-rationale` | — | `string` | No | — | No | Rationale for grounding waiver (min 10 chars) |
| `--waiver-rationale` | — | `string` | No | — | No | Alias for --grounding-rationale |
| `--waiver-key-file` | `-k` | `path` | No | — | No | Path to steward waiver secret key file |
| `--ungrounded-concept` | — | `array` | No | — | No | Concept URIs covered by waiver |
| `--evidence-grounding-file` | — | `path` | No | — | Yes | Path to admin evidence grounding approval JSON |
| `--authorized-by` | — | `string` | No | — | No | Steward identity authorizing grounding waiver |
| `--authorized-role` | — | `string` | No | — | No | Steward role authorizing grounding waiver (must match admin role) |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio proposal accept --help

# Client execution
opendvctl ontology studio proposal accept --api-url $OPENDV_API_URL --json
```

###### `ontology studio proposal reject` *(Both CLIs)*

Reject a pending proposal

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--proposal-id` | `-p` | `string` | Yes | — | No | Proposal identifier |
| `--reason` | `-r` | `string` | No | — | No | Rejection rationale |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio proposal reject --help

# Client execution
opendvctl ontology studio proposal reject --api-url $OPENDV_API_URL --json
```

###### `ontology studio proposal rebase` *(Both CLIs)*

Rebase proposal operations onto latest active session head

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--proposal-id` | `-p` | `string` | Yes | — | No | Proposal identifier |
| `--base-sha256` | — | `string` | No | — | No | Target base SHA-256 for rebase (defaults to current session head) |
| `--head-sha256` | — | `string` | No | — | No | Alias for target session head SHA-256 |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio proposal rebase --help

# Client execution
opendvctl ontology studio proposal rebase --api-url $OPENDV_API_URL --json
```

###### `ontology studio export` *(Both CLIs)*

Export constructed Graph A ontology to YAML, JSON, OWL 2/Turtle, or JSON-LD

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--export-format` | `-e` | `string` | No | `yaml` | No | Serialization format |
| `--version` | `-v` | `string` | No | — | No | Target ontology version (defaults to active head) |
| `--out` | `-o` | `path` | No | — | No | Output file path (client_file_download) |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio export --help

# Client execution
opendvctl ontology studio export --api-url $OPENDV_API_URL --json
```

###### `ontology studio promote` *(Both CLIs)*

Promote active session ontology to production workspace pipeline under graph_a.lock

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--workspace` | `-w` | `path` | No | — | No | Destination workspace Data Vault project path |
| `--workspace-sha256` | — | `string` | Yes | — | No | Expected current workspace SHA-256 (64 zeros for greenfield) |
| `--head-sha256` | — | `string` | Yes | — | No | Expected session head SHA-256 digest |
| `--rationale` | `-r` | `string` | Yes | — | No | Required promotion rationale (min 10 characters) |
| `--acknowledge-historical-contract-warning` | — | `boolean` | No | `False` | No | Acknowledge historical deployed contract warning |
| `--historical-contract-rationale` | — | `string` | No | — | No | Rationale acknowledging historical contract warning |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio promote --help

# Client execution
opendvctl ontology studio promote --api-url $OPENDV_API_URL --json
```

###### `ontology studio bundle` *(Both CLIs)*

Manage portable session archives (.odvstudio.zip export, import, clone)

###### Subcommands of `ontology studio bundle`

###### `ontology studio bundle export` *(Both CLIs)*

Export session into portable .odvstudio.zip archive

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Studio session identifier |
| `--out` | `-o` | `path` | Yes | — | No | Destination file path (.odvstudio.zip) |
| `--acknowledge-confidential-source-export` | — | `boolean` | No | `False` | No | Acknowledge export of confidential/tainted sources |
| `--export-rationale` | — | `string` | No | — | No | Rationale for exporting confidential session archive |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio bundle export --help

# Client execution
opendvctl ontology studio bundle export --api-url $OPENDV_API_URL --json
```

###### `ontology studio bundle import` *(Both CLIs)*

Import and re-validate portable .odvstudio.zip archive

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--file` | `-f` | `path` | Yes | — | No | Path to .odvstudio.zip archive to import |
| `--new-session-id` | — | `string` | No | — | No | Optional explicit session identifier for imported session |
| `--force` | — | `boolean` | No | `False` | No | Force overwrite if target session identifier already exists |
| `--acknowledge-unverified-import-attribution` | — | `boolean` | No | `False` | No | Admin acknowledgement for unverified imported attribution or waivers |
| `--acknowledgement-rationale` | — | `string` | No | — | No | Rationale acknowledging unverified import attribution |
| `--acknowledge-historical-contract-warning` | — | `boolean` | No | `False` | No | Acknowledge historical contract warning in imported session |
| `--historical-contract-rationale` | — | `string` | No | — | No | Rationale acknowledging historical contract warning |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio bundle import --help

# Client execution
opendvctl ontology studio bundle import --api-url $OPENDV_API_URL --json
```

###### `ontology studio bundle clone` *(Both CLIs)*

Clone an existing studio session into a new isolated session identifier

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--session` | `-s` | `string` | Yes | — | No | Source studio session identifier to clone |
| `--session-label` | `-l` | `string` | No | — | No | Label for the newly cloned session |
| `--new-session-id` | — | `string` | No | — | No | Explicit target session identifier for the newly cloned session |


**Example Invocations:**
```bash
# Standalone execution
opendv ontology studio bundle clone --help

# Client execution
opendvctl ontology studio bundle clone --api-url $OPENDV_API_URL --json
```

### `opendv config` / `opendvctl config`

**Availability:** *(Both CLIs)*  
**Description:** Inspect, update, and validate Open-DV platform and agnostic agent harness configuration  
**Summary:** Manage platform and agent harness configuration

#### Subcommands of `config`

##### `config show` *(Both CLIs)*

Display active platform settings, default agent harness configuration, resolved persona models, and provider readiness

- **Response DTO:** `PlatformConfigResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv config show --help

# Client execution
opendvctl config show --api-url $OPENDV_API_URL --json
```

##### `config set` *(Both CLIs)*

Update and persist platform default agent harness provider, model routing, budgets, and governance policies

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--provider` | `-p` | `string` | No | — | No | Default agent harness provider |
| `--reasoning-model` | — | `string` | No | — | No | Model override for HIGH_REASONING tier ('reasoning') |
| `--fast-model` | — | `string` | No | — | No | Model override for FAST_TIER ('fast') |
| `--auth-mode` | — | `string` | No | — | No | Authentication mode for the harness provider |
| `--api-key-env-var` | — | `string` | No | — | No | Environment variable name storing the provider API key |
| `--hold-policy` | — | `string` | No | — | No | Default Steward Hold policy |
| `--ontology-policy` | — | `string` | No | — | No | Default ontology evolution policy (off, recommend, auto_accept) |
| `--stage-output-policy` | — | `string` | No | — | No | Default stage output synthesis policy |
| `--max-turns` | — | `integer` | No | — | No | Maximum agent turns per task |
| `--timeout-seconds` | — | `integer` | No | — | No | Task execution timeout in seconds |
| `--harness-config` | `-c` | `path` | No | — | Yes | Optional path to a full harness_config.yaml file to import |

- **Request DTO:** `PlatformConfigUpdateRequest`
- **Response DTO:** `PlatformConfigResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv config set --help

# Client execution
opendvctl config set --api-url $OPENDV_API_URL --json
```

##### `config validate` *(Both CLIs)*

Deterministically validate platform and agent harness configuration and probe provider readiness

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--provider` | `-p` | `string` | No | — | No | Candidate harness provider to validate |
| `--harness-config` | `-c` | `path` | No | — | Yes | Optional path to a candidate harness_config.yaml file to validate |

- **Request DTO:** `PlatformConfigValidateRequest`
- **Response DTO:** `PlatformConfigValidationResponse`

**Example Invocations:**
```bash
# Standalone execution
opendv config validate --help

# Client execution
opendvctl config validate --api-url $OPENDV_API_URL --json
```

### `opendv serve`

**Availability:** *(Standalone `opendv` only)*  
**Description:** Launch the unified Open-DV Platform Server serving both the REST/SSE API and the embedded static Web Explorer UI  
**Summary:** Start the unified Open-DV Platform & Web Explorer server

| Option | Short | Type | Required | Default | Inline Upload | Description |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `--host` | — | `string` | No | `127.0.0.1` | No | Bind host address for the platform server |
| `--port` | `-p` | `integer` | No | `8420` | No | Bind port for the platform server and Web Explorer UI |
| `--config` | `-c` | `path` | No | — | No | Path to opendv.config.yaml configuration file |
| `--workspace` | `-w` | `path` | No | — | No | Workspace root directory for path containment and state storage |
| `--open` | — | `boolean` | No | `False` | No | Automatically open the Web Explorer UI in the default browser on startup |


**Example Invocations:**
```bash
# Standalone execution
opendv serve --help
```

---

## 4. Exit Codes & Error Mapping

Both CLIs adhere to normative exit code standards across all failure scenarios:

- `0`: Operation succeeded (`overall_passed: true` or status `COMPLETED`).
- `1`: Domain, verification, or runtime failure (`overall_passed: false`, gate failure, or internal error).
- `2`: Syntax, invalid CLI argument, configuration error, or missing precondition.
- `3`: Resource not found (session, proposal, job, source, or input file not found).
- `4`: Governance, contract, or semantic validation violation (`ONT-EVO-001..003`, grounding, or taint policy).
- `5`: Optimistic concurrency or stale base precondition conflict (`If-Match` / `base_sha256` mismatch).
- `6`: Resource state conflict (session already exists, proposal already resolved, or active job conflict).
- `7`: Authentication, RBAC role clearance, steward waiver, or transport security forbidden.

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
| `UNSUPPORTED_FORMAT` | `2` | `400` | Unsupported Format | `urn:opendv:error:unsupported-format` |
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
| `AGENT_UNAVAILABLE` | `1` | `503` | LLM Agent Service Unavailable | `urn:opendv:error:agent-unavailable` |
| `CONFLICTING_WORKSPACE_SEED_PARAMS` | `1` | `400` | Conflicting Workspace Seed Parameters | `urn:opendv:error:conflicting-workspace-seed-params` |
| `CORRUPT_SESSION_RECORD` | `1` | `500` | Corrupt Session Record | `urn:opendv:error:corrupt-session-record` |
| `EXPORT_RATIONALE_REQUIRED` | `1` | `400` | Export Rationale Required | `urn:opendv:error:export-rationale-required` |
| `HISTORICAL_CONTRACT_RATIONALE_REQUIRED` | `1` | `400` | Historical Contract Rationale Required | `urn:opendv:error:historical-contract-rationale-required` |
| `IMPORT_GROUNDING_RATIONALE_REQUIRED` | `1` | `400` | Import Grounding Rationale Required | `urn:opendv:error:import-grounding-rationale-required` |
| `INSECURE_STUDIO_ROOT_PERMISSIONS` | `1` | `500` | Insecure Studio Root Permissions | `urn:opendv:error:insecure-studio-root-permissions` |
| `JOB_ABANDONED_SERVER_CRASH` | `1` | `500` | Background Job Abandoned Due to Server Crash | `urn:opendv:error:job-abandoned-server-crash` |
| `MATCHER_UNIDATA_VERSION_MISMATCH` | `1` | `500` | Matcher Unicode Character Tables Version Mismatch | `urn:opendv:error:matcher-unidata-version-mismatch` |
| `NO_OP_DELTA` | `1` | `400` | No-Op Delta | `urn:opendv:error:no-op-delta` |
| `OCR_UNAVAILABLE` | `1` | `501` | OCR Engine Unavailable | `urn:opendv:error:ocr-unavailable` |
| `OFFICE_CONVERSION_UNAVAILABLE` | `1` | `501` | Office Document Conversion Unavailable | `urn:opendv:error:office-conversion-unavailable` |
| `PROMOTION_RATIONALE_REQUIRED` | `1` | `400` | Promotion Rationale Required | `urn:opendv:error:promotion-rationale-required` |
| `REFERENCE_PACK_INTEGRITY_FAILURE` | `1` | `500` | Reference Pack Integrity Failure | `urn:opendv:error:reference-pack-integrity-failure` |
| `VERSION_COMMIT_IO_FAILURE` | `1` | `500` | Version Commit IO Failure | `urn:opendv:error:version-commit-io-failure` |
| `INVALID_EVIDENCE_OFFSETS` | `2` | `400` | Invalid Evidence Offsets | `urn:opendv:error:invalid-evidence-offsets` |
| `INVALID_PRECONDITION_HEADER` | `2` | `400` | Invalid Precondition Header | `urn:opendv:error:invalid-precondition-header` |
| `INVALID_SESSION_IDENTIFIER` | `2` | `400` | Invalid Session Identifier | `urn:opendv:error:invalid-session-identifier` |
| `INVALID_WORKSPACE_PATH` | `2` | `400` | Invalid Workspace Path | `urn:opendv:error:invalid-workspace-path` |
| `MISSING_AUTO_COMMIT_PRECONDITION` | `2` | `400` | Missing Auto-Commit Precondition | `urn:opendv:error:missing-auto-commit-precondition` |
| `MISSING_PRECONDITION_HEADER` | `2` | `400` | Missing Precondition Header | `urn:opendv:error:missing-precondition-header` |
| `PRECONDITION_BASE_SHA_MISMATCH` | `2` | `400` | Precondition Base SHA Mismatch | `urn:opendv:error:precondition-base-sha-mismatch` |
| `REMOTE_SERVER_PATH_FORBIDDEN` | `2` | `400` | Remote Server Path Forbidden | `urn:opendv:error:remote-server-path-forbidden` |
| `SYNTAX_INVALID_ARGUMENT` | `2` | `400` | Invalid Argument Syntax | `urn:opendv:error:syntax-invalid-argument` |
| `VALIDATION_AMBIGUOUS_TARGET` | `2` | `400` | Ambiguous Target Specifier | `urn:opendv:error:validation-ambiguous-target` |
| `JOB_NOT_FOUND` | `3` | `404` | Job Not Found | `urn:opendv:error:job-not-found` |
| `PROPOSAL_NOT_FOUND` | `3` | `404` | Proposal Not Found | `urn:opendv:error:proposal-not-found` |
| `SESSION_NOT_FOUND` | `3` | `404` | Session Not Found | `urn:opendv:error:session-not-found` |
| `SOURCE_NOT_FOUND` | `3` | `404` | Source Document Not Found | `urn:opendv:error:source-not-found` |
| `VERSION_NOT_FOUND` | `3` | `404` | Version Not Found | `urn:opendv:error:version-not-found` |
| `AGENT_UNRESOLVED_PSEUDONYM` | `4` | `422` | Agent Unresolved Pseudonym | `urn:opendv:error:agent-unresolved-pseudonym` |
| `AMBIGUOUS_EXTERNAL_MAPPING` | `4` | `422` | Ambiguous External Mapping | `urn:opendv:error:ambiguous-external-mapping` |
| `ARCHIVE_SECURITY_VIOLATION` | `4` | `422` | Archive Security Violation | `urn:opendv:error:archive-security-violation` |
| `ATTRIBUTE_NOT_FOUND` | `4` | `422` | Attribute Not Found | `urn:opendv:error:attribute-not-found` |
| `AUTOMATED_ATTRIBUTION_INVALID` | `4` | `422` | Invalid Automated Attribution | `urn:opendv:error:automated-attribution-invalid` |
| `CANNOT_DELETE_PROMOTED_SESSION` | `6` | `409` | Cannot Delete Promoted Session | `urn:opendv:error:cannot-delete-promoted-session` |
| `CANNOT_PRUNE_PROMOTED_SESSION` | `4` | `422` | Cannot Prune Promoted Session | `urn:opendv:error:cannot-prune-promoted-session` |
| `CONCEPT_STILL_REFERENCED` | `4` | `422` | Concept Still Referenced | `urn:opendv:error:concept-still-referenced` |
| `CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED` | `4` | `422` | Confidential Source Export Unacknowledged | `urn:opendv:error:confidential-source-export-unacknowledged` |
| `DANGLING_ENUM_CODE_DESCRIPTION` | `4` | `422` | Dangling Enumeration Code Description | `urn:opendv:error:dangling-enum-code-description` |
| `DESCRIPTION_ALREADY_EXISTS` | `4` | `422` | Description Already Exists | `urn:opendv:error:description-already-exists` |
| `DUPLICATE_ATTRIBUTE_NAME` | `4` | `422` | Duplicate Attribute Name | `urn:opendv:error:duplicate-attribute-name` |
| `DUPLICATE_ENUM_CODE` | `4` | `422` | Duplicate Enumeration Code | `urn:opendv:error:duplicate-enum-code` |
| `DUPLICATE_RELATIONSHIP_NAME` | `4` | `422` | Duplicate Relationship Name | `urn:opendv:error:duplicate-relationship-name` |
| `DUPLICATE_RELATIONSHIP_URI` | `4` | `422` | Duplicate Relationship URI | `urn:opendv:error:duplicate-relationship-uri` |
| `DUPLICATE_SYNONYM` | `4` | `422` | Duplicate Synonym | `urn:opendv:error:duplicate-synonym` |
| `ENUM_CODE_NOT_FOUND` | `4` | `422` | Enumeration Code Not Found | `urn:opendv:error:enum-code-not-found` |
| `EVIDENCE_OFFSET_OUT_OF_RANGE` | `4` | `422` | Evidence Offset Out of Range | `urn:opendv:error:evidence-offset-out-of-range` |
| `GENESIS_REQUIRES_CONCEPT` | `4` | `422` | Genesis Version Requires Concept | `urn:opendv:error:genesis-requires-concept` |
| `HIERARCHICAL_CYCLE_DETECTED` | `4` | `422` | Hierarchical Cycle Detected | `urn:opendv:error:hierarchical-cycle-detected` |
| `HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED` | `4` | `422` | Historical Contract Warning Unacknowledged | `urn:opendv:error:historical-contract-warning-unacknowledged` |
| `HUB_BIJECTION_VIOLATION` | `4` | `422` | Hub Bijection Violation | `urn:opendv:error:hub-bijection-violation` |
| `IMPORT_ADMIN_OP_FORBIDDEN` | `4` | `422` | Import Admin Operation Forbidden | `urn:opendv:error:import-admin-op-forbidden` |
| `IMPORT_AGENT_DESTRUCTIVE_OP_FORBIDDEN` | `4` | `422` | Import Agent Destructive Operation Forbidden | `urn:opendv:error:import-agent-destructive-op-forbidden` |
| `IMPORT_AUTOMATED_ATTRIBUTION_INVALID` | `4` | `422` | Import Invalid Automated Attribution | `urn:opendv:error:import-automated-attribution-invalid` |
| `IMPORT_CONFIDENCE_MISMATCH` | `4` | `422` | Import Confidence Mismatch | `urn:opendv:error:import-confidence-mismatch` |
| `IMPORT_DELTA_REPLAY_HASH_MISMATCH` | `4` | `422` | Import Delta Replay Hash Mismatch | `urn:opendv:error:import-delta-replay-hash-mismatch` |
| `IMPORT_ENVELOPE_VIOLATION` | `4` | `422` | Import Envelope Violation | `urn:opendv:error:import-envelope-violation` |
| `IMPORT_GROUNDING_VIOLATION` | `4` | `422` | Import Grounding Violation | `urn:opendv:error:import-grounding-violation` |
| `IMPORT_LINEAGE_BROKEN` | `4` | `422` | Import Lineage Broken | `urn:opendv:error:import-lineage-broken` |
| `IMPORT_MATCHER_VERSION_MISMATCH` | `4` | `422` | Import Matcher Version Mismatch | `urn:opendv:error:import-matcher-version-mismatch` |
| `IMPORT_PROVISIONAL_URI_FORBIDDEN` | `4` | `422` | Import Provisional URI Forbidden | `urn:opendv:error:import-provisional-uri-forbidden` |
| `IMPORT_SELF_ACCEPTANCE_UNACKNOWLEDGED` | `4` | `422` | Import Self-Acceptance Unacknowledged | `urn:opendv:error:import-self-acceptance-unacknowledged` |
| `IMPORT_SEMVER_BUMP_MISMATCH` | `4` | `422` | Import SemVer Bump Mismatch | `urn:opendv:error:import-semver-bump-mismatch` |
| `IMPORT_UNKNOWN_REFERENCE_PACK` | `4` | `422` | Import Unknown Reference Pack | `urn:opendv:error:import-unknown-reference-pack` |
| `INVALID_EVIDENCE_GROUNDING` | `4` | `422` | Invalid Evidence Grounding | `urn:opendv:error:invalid-evidence-grounding` |
| `INVALID_GROUNDING_WAIVER` | `4` | `422` | Invalid Grounding Waiver | `urn:opendv:error:invalid-grounding-waiver` |
| `INVALID_PHYSICAL_TABLE_NAME` | `4` | `422` | Invalid Physical Table Name | `urn:opendv:error:invalid-physical-table-name` |
| `INVALID_PROPOSAL_STATE` | `4` | `422` | Invalid Proposal State | `urn:opendv:error:invalid-proposal-state` |
| `INVALID_WORKSPACE_ONTOLOGY` | `4` | `422` | Invalid Workspace Ontology | `urn:opendv:error:invalid-workspace-ontology` |
| `MAPPING_NOT_FOUND` | `4` | `422` | External Mapping Not Found | `urn:opendv:error:mapping-not-found` |
| `ONTOLOGY_REQUIRES_CONCEPT` | `4` | `422` | Ontology Requires Concept | `urn:opendv:error:ontology-requires-concept` |
| `ONT_EVO_CONTRACT_VIOLATION` | `4` | `422` | ONT-EVO Contract Violation | `urn:opendv:error:ont-evo-contract-violation` |
| `ONT_EVO_ENVELOPE_VIOLATION` | `4` | `422` | ONT-EVO Envelope Violation | `urn:opendv:error:ont-evo-envelope-violation` |
| `ONT_EVO_FORGED_PROVENANCE` | `4` | `422` | ONT-EVO Forged Provenance | `urn:opendv:error:ont-evo-forged-provenance` |
| `ONT_EVO_GROUNDING_VIOLATION` | `4` | `422` | ONT-EVO Grounding Violation | `urn:opendv:error:ont-evo-grounding-violation` |
| `ONT_EVO_LOW_CONFIDENCE_GROUNDING` | `4` | `422` | ONT-EVO Low Confidence Grounding | `urn:opendv:error:ont-evo-low-confidence-grounding` |
| `ONT_EVO_PSEUDONYM_LEAK` | `4` | `422` | ONT-EVO Pseudonym Leak | `urn:opendv:error:ont-evo-pseudonym-leak` |
| `ONT_EVO_UNGROUNDED_CONCEPT` | `4` | `422` | ONT-EVO Ungrounded Concept | `urn:opendv:error:ont-evo-ungrounded-concept` |
| `PARENT_ALREADY_EXISTS` | `4` | `422` | Parent Already Exists | `urn:opendv:error:parent-already-exists` |
| `PARENT_NOT_FOUND` | `4` | `422` | Parent Concept Not Found | `urn:opendv:error:parent-not-found` |
| `PHYSICAL_TABLE_NAME_COLLISION` | `4` | `422` | Physical Table Name Collision | `urn:opendv:error:physical-table-name-collision` |
| `REDACTED_PLACEHOLDER_LEAK` | `4` | `422` | Redacted Placeholder Leak | `urn:opendv:error:redacted-placeholder-leak` |
| `REFERENCED_CONCEPT_NOT_FOUND` | `4` | `422` | Referenced Concept Not Found | `urn:opendv:error:referenced-concept-not-found` |
| `RELATIONSHIP_NOT_FOUND` | `4` | `422` | Relationship Not Found | `urn:opendv:error:relationship-not-found` |
| `SOURCE_REFERENCED_BY_COMMITTED_VERSION` | `4` | `422` | Source Referenced by Committed Version | `urn:opendv:error:source-referenced-by-committed-version` |
| `SOURCE_REFERENCED_BY_PENDING_PROPOSAL` | `4` | `422` | Source Referenced by Pending Proposal | `urn:opendv:error:source-referenced-by-pending-proposal` |
| `UNDEFINED_PHYSICAL_TABLE_MAPPING` | `4` | `422` | Undefined Physical Table Mapping | `urn:opendv:error:undefined-physical-table-mapping` |
| `UNKNOWN_REFERENCE_PACK` | `4` | `422` | Unknown Reference Pack | `urn:opendv:error:unknown-reference-pack` |
| `VERSION_CONSISTENCY_MISMATCH` | `4` | `422` | Version Consistency Mismatch | `urn:opendv:error:version-consistency-mismatch` |
| `LINEAGE_ROOT_MISMATCH` | `5` | `412` | Lineage Root Mismatch | `urn:opendv:error:lineage-root-mismatch` |
| `ONT_EVO_CONCURRENT_MODIFICATION` | `5` | `412` | ONT-EVO Concurrent Modification | `urn:opendv:error:ont-evo-concurrent-modification` |
| `JOB_COMMIT_IN_PROGRESS` | `6` | `409` | Job Commit In Progress | `urn:opendv:error:job-commit-in-progress` |
| `LOCK_TIMEOUT` | `6` | `409` | Lock Acquisition Timeout | `urn:opendv:error:lock-timeout` |
| `PRINCIPAL_ALREADY_EXISTS` | `6` | `409` | Principal Already Exists | `urn:opendv:error:principal-already-exists` |
| `PRINCIPAL_RESERVED` | `6` | `409` | Principal Identifier Reserved | `urn:opendv:error:principal-reserved` |
| `PROPOSAL_ALREADY_RESOLVED` | `6` | `409` | Proposal Already Resolved | `urn:opendv:error:proposal-already-resolved` |
| `STUDIO_ALREADY_INITIALIZED` | `6` | `409` | Studio Already Initialized | `urn:opendv:error:studio-already-initialized` |
| `AGENT_ADMIN_OP_FORBIDDEN` | `7` | `403` | Agent Admin Operation Forbidden | `urn:opendv:error:agent-admin-op-forbidden` |
| `AGENT_DESTRUCTIVE_OP_FORBIDDEN` | `7` | `403` | Agent Destructive Operation Forbidden | `urn:opendv:error:agent-destructive-op-forbidden` |
| `AGENT_METADATA_OVERWRITE_FORBIDDEN` | `7` | `403` | Agent Metadata Overwrite Forbidden | `urn:opendv:error:agent-metadata-overwrite-forbidden` |
| `AGENT_OPERATION_FORBIDDEN` | `7` | `403` | Agent Operation Forbidden | `urn:opendv:error:agent-operation-forbidden` |
| `AGENT_PROPOSAL_ACCEPT_FORBIDDEN` | `7` | `403` | Agent Proposal Accept Forbidden | `urn:opendv:error:agent-proposal-accept-forbidden` |
| `CSRF_ORIGIN_MISMATCH` | `7` | `403` | CSRF Origin Mismatch | `urn:opendv:error:csrf-origin-mismatch` |
| `FORBIDDEN_ADMIN_REQUIRED` | `7` | `403` | Forbidden: Admin Role Required | `urn:opendv:error:forbidden-admin-required` |
| `LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE` | `7` | `403` | Loopback Privilege Disabled in Remote Mode | `urn:opendv:error:loopback-privilege-disabled-in-remote-mode` |
| `PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN` | `7` | `403` | Proposal Self-Acceptance Forbidden | `urn:opendv:error:proposal-self-acceptance-forbidden` |
| `SERVER_OPERATION_FORBIDDEN` | `7` | `403` | Server Operation Forbidden | `urn:opendv:error:server-operation-forbidden` |
| `UNAUTHENTICATED` | `7` | `401` | Unauthenticated Request | `urn:opendv:error:unauthenticated` |
| `SESSION_ALREADY_EXISTS` | `6` | `409` | Session Already Exists | `urn:opendv:error:session-already-exists` |
| `CORRUPT_SOURCE_BLOB` | `4` | `422` | Corrupt Source Blob | `urn:opendv:error:corrupt-source-blob` |
| `LAUNCH_NONCE_EXPIRED` | `7` | `401` | Launch Nonce Expired | `urn:opendv:error:launch-nonce-expired` |
| `AUTHENTICATION_CONFIG_ERROR` | `1` | `500` | Authentication Configuration Error | `urn:opendv:error:authentication-config-error` |
| `EXECUTION_FAILURE` | `1` | `500` | Execution Failure | `urn:opendv:error:execution-failure` |
| `JOB_ALREADY_TERMINAL` | `6` | `409` | Job Already Terminal | `urn:opendv:error:job-already-terminal` |
| `STATIC_TOKEN_CONFIGURED` | `1` | `500` | Static Token Configured | `urn:opendv:error:static-token-configured` |
| `FILE_NOT_FOUND` | `3` | `404` | File Not Found | `urn:opendv:error:file-not-found` |
| `INSECURE_TRANSPORT_REFUSED` | `7` | `403` | Insecure Transport Refused | `urn:opendv:error:insecure-transport-refused` |
| `CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN` | `7` | `403` | Confidential Source Agent Access Forbidden | `urn:opendv:error:confidential-source-agent-access-forbidden` |
| `REFERENCE_ONTOLOGY_CORRUPT` | `1` | `500` | Reference Ontology Pack Corrupt | `urn:opendv:error:reference-ontology-corrupt` |
| `UNGROUNDED_SEED_MAPPING` | `4` | `422` | Ungrounded Seed Mapping | `urn:opendv:error:ungrounded-seed-mapping` |
| `TAXONOMY_CYCLE_DETECTED` | `4` | `422` | Taxonomy Cycle Detected | `urn:opendv:error:taxonomy-cycle-detected` |
| `QUESTION_NOT_FOUND` | `3` | `404` | Clarification Question Not Found | `urn:opendv:error:question-not-found` |
| `TAMPER_EVIDENT_CHAIN_CORRUPT` | `4` | `422` | Tamper-Evident Audit Chain Corrupt | `urn:opendv:error:tamper-evident-chain-corrupt` |
| `CSRF_ORIGIN_DENIED` | `7` | `403` | CSRF Origin Denied | `urn:opendv:error:csrf-origin-denied` |
| `TOKEN_EXPIRED` | `7` | `401` | Authentication Token Expired | `urn:opendv:error:token-expired` |
| `JOB_CANNOT_BE_CANCELLED` | `6` | `409` | Job Cannot Be Cancelled | `urn:opendv:error:job-cannot-be-cancelled` |
| `CORRUPT_SESSION_STATE` | `1` | `500` | Corrupt Session State | `urn:opendv:error:corrupt-session-state` |
| `CONCURRENCY_CONFLICT` | `5` | `412` | Concurrency Conflict | `urn:opendv:error:concurrency-conflict` |
| `LOW_CONFIDENCE_GROUNDING` | `4` | `422` | Low Confidence Grounding | `urn:opendv:error:low-confidence-grounding` |
| `PROPOSAL_STALE_REBASE_REQUIRED` | `6` | `409` | Proposal Stale Rebase Required | `urn:opendv:error:proposal-stale-rebase-required` |
| `INTERNAL_CLONE_ERROR` | `1` | `500` | Internal Clone Error | `urn:opendv:error:internal-clone-error` |
| `AGENT_LAUNCH_NONCE_FORBIDDEN` | `7` | `403` | Agent Launch Nonce Forbidden | `urn:opendv:error:agent-launch-nonce-forbidden` |
| `HARNESS_CONFIG_INVALID` | `4` | `422` | Harness Configuration Invalid | `urn:opendv:error:harness-config-invalid` |

---

## 5. Output Formats & Canonical Serialization

Both CLIs support three uniform output modes configured via `--format` / `-f`:

1. `--json`: Emits bit-for-bit canonical JSON compliant with **RFC 8785 JSON Canonicalization Scheme (JCS)**.
2. `--toon`: Emits compact diagnostic `@toon_v1` traces pre-rendered by `opendv-service`.
3. `--format text`: Emits human-readable ANSI status tables pre-rendered by `opendv-service`.

