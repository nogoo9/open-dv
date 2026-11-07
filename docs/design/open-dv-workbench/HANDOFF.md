# open-dv Workbench — design handoff

Implementation brief for a coding agent. The design is a minimalist, three-level explorer for the open-dv 8-stage pipeline: **Pipeline → Stage → Artifact**.

## Files in this bundle

| File | What it is |
| --- | --- |
| `HANDOFF.md` | This spec. Source of truth for behaviour. |
| `design/Main.dc.html` | The full interactive prototype. **Reference only**: written in a canvas "Design Component" format (`<x-dc>`, `<sc-for>`, `<sc-if>`, `{{holes}}`, `class Component extends DCLogic`). It will not run standalone. Read it for layout, inline styles (exact px, colours) and the sample data model in the `<script>` block (`A`, `S`, `PHASES`, `CONCEPTS`). |
| `design/StageView.dc.html`, `design/ArtifactView.dc.html` | Thin wrappers that mount `Main` at a different starting state (stage 05 / ontology full view). |

## Target

Build into `apps/opendv-web-workbench` (React 19 + Bun, `:5173`). Read data via `@opendv/protocol-ts` (`packages/opendv-protocol-ts`: `PlatformClient`, `SseClient`, `MockClient`, `pe-demo-bundle.ts`). Don't hardcode sample data. The prototype's inline data is demo content from `examples/pipeline_samples/global_commerce_vault/`.

## Vocabulary: three element kinds

Each kind has a glyph, used everywhere: cards, chips, inspector header and legend.

| Kind | Glyph | Colour | Examples |
| --- | --- | --- | --- |
| Control (step) | 10px diamond (`clip-path: polygon(50% 0,100% 50%,50% 100%,0 50%)`) | `#B45309` | "Pre-flight input validation", "Gate 4 · YAGNI cost & triage" |
| Data / artifact | 10px square, radius 2 | `#1D4ED8` (settings: 1.5px outline, transparent fill) | `graph_b_profile.json`, `final_contract.yaml` |
| Component | 10px circle | `#4B5563` | `GraphBProfiler`, `toon_codec`, `vault-first-mate` |

## Design tokens

- Fonts: IBM Plex Sans 400/500/600 (UI), IBM Plex Mono 400/500 (identifiers, file names, IDs, raw data).
- Colours: ink `#16181D`, body text `#3B414C`, muted `#545A66`, label `#6B7280`, page `#F6F7F8`, surface `#FFFFFF`, line `#E3E5E8`, rail `#D4D7DC`, dashed `#C9CDD3`, selection border `#1D4ED8`.
- Status pills (mono 11px, radius 999). Values are the real `VaultObservabilityEvent.status` enum:
  - `PASS`: `#EEF0F2` / `#16181D`
  - `AUTO_REPAIRED`, `ASK-USER`: `#FFF4E5` / `#8A3B07`
  - `REJECT-WITH-TRACE`: `#FDECEA` / `#8F1D12`
  - `AUTO-FIX`: `#EEF2FF` / `#1E3A8A`
- Radius: cards 8, chips 4, buttons 6. Section headings: 12px uppercase, 0.06em tracking, `#545A66`.
- Touch targets ≥ 32px (icon buttons), chips 28px high, primary buttons 36–40px.

## Global header (56px)

Wordmark `open-dv` · breadcrumb (`Pipeline` / `STAGE_05 · name`) · run pill (display name + `pipeline_input.yaml · N waves`) · spacer · wave segmented control (`All W0 W1 W2 W3`) · run status (`Sealed · 6/6 gates PASS`) · primary `Run pipeline` button.
The wave filter is **not wired in the prototype**. Intended behaviour: it filters inputs, stage counts and outputs to one wave's `waves/wave_<w>/*` artifacts.

## View 1 — Pipeline overview

A flex-wrap row with `gap: 24px` and `padding: 24px`:

1. **Settings + Inputs column** (260–320px).
   - Settings: `harness_config.yaml` (`HarnessSetupConfig`) and run options (CLI flags: `--local`, `--role`, `--stream`, `--output-dir`, `assertion_cutoff_ts`).
   - Inputs: `pipeline_input.yaml`, the Graph A ontology, each relational source, each unstructured doc.
   - Then a dashed placeholder for `initial_contract` when it's absent (cold start).
2. **Stages column** (flex-grow, min 440px). Title "Pipeline · 8 stages" and the kind legend.
   - A vertical list with a 1px rail behind 40px numbered circles, grouped by phase headers:
     - **Discovery** (00, 01, 02): LLM agents · read-only scouts
     - **Modelling** (03, 04): LLM agent · declarative contract only
     - **Verification** (05, 06): deterministic · symbolic gates
     - **Compilation** (07): deterministic · no LLM
   - Each stage card shows: name, `STAGE_xx_KEY` (mono), status pill, one-line blurb, `@persona · tier: reasoning|fast · N in · N out · N steps`, and an optional amber note. Notes: "∥ runs alongside STAGE_02"; "↺ @toon_v1 repair loop → ship-vault · k=1 → k=2".
   - The whole card is a `<button>` that opens View 2.
3. **Outputs column** (260–320px). `final_contract.yaml`, `final_canonical_ir.json`, `final_four_zone.sql`, `pipeline_report.json`, `vault-ledger.jsonl`.
4. **Inspector** (320–380px). Appears only when something is selected; the stage column shrinks to make room.

**Artifact card** (settings, inputs, outputs): name (mono 13), one-line summary, and a 3-line mono preview (`#F6F7F8` box, max-height 48px). For tables the preview is header + 2 rows joined by `|`; otherwise it's the first 3 raw lines. The card body selects the artifact. A separate top-right icon button (aria-label "Open full view") opens View 3 directly.

**Inspector for an artifact** shows:
- glyph + "Data · <group>", and a close button
- name (mono 15) and description
- a definition list: Type, Format, Produced by (stage keys whose outputs include it), Consumed by (stage keys whose inputs include it)
- a "Preview" box (first ~10 lines, or up to 6 table rows)
- "Open full view · <visualisation name>"

## View 2 — Stage drill-down

**Sub-header:**
- "← All stages" button
- numbered circle and stage name (H1 20px)
- `STAGE_KEY · @persona · tier`
- status pill
- prev/next stage arrow buttons
- the stage description underneath (max 880px)

**Body** (flex-wrap):

1. **Components** (240–300px). One button per component: circle glyph, mono name, type label ("Agent persona", "Gate", "Codemod", "Compiler", "Harness adapter"…).
2. **Control & data flow** (flex-grow):
   - "Enters with" strip (dashed box) holding data chips for the stage's inputs.
   - One row per step, as a 3-column grid `1fr 1.3fr 1fr`:
     - left: chips for data the step **reads** (right-aligned)
     - centre: the step card (diamond glyph, step number, name; for `decision`/`loop` steps an amber mono branch line such as `fail → halt run at STAGE_00` or `k=1 → k=2 · re-run gates`)
     - right: chips for data the step **writes**
   - A 14px amber vertical connector between consecutive step cards.
   - "Hands off" strip: the stage's output chips, then `→ <next stage key>` (or "pipeline outputs" for stage 07).
3. **Inspector** (300–360px, always present). Empty state: "Select a step, a data item or a component to see what it does."
   - **Step:** "Control · step i of n", name, description, Kind (action/decision/loop), Branches, Reads, Writes.
   - **Component:** "Component · <type>", name, description, Source (repo path), Stage.
   - **Data chip:** the same artifact inspector as View 1, including "Open full view".

Selection is shown with a `#1D4ED8` border on the selected card or chip, and on every chip for the same artifact.

## View 3 — Artifact full view

A full-bleed overlay on top of whichever view opened it. "Back" returns to that view with its state intact.

**Header:** Back · glyph · name (mono 16) · `type · format · group` · tablist **Visual | Raw | Split** (default **Split**). The artifact description sits below the header.

**Body:** the Visual pane (`flex: 3 1 560px`) and the Raw pane (`flex: 2 1 360px`, `#F6F7F8` `<pre>`, labelled `Raw · FORMAT` with the redaction role on the right). Raw content must already be RBAC-redacted for the current role. PII shows as `‹PII›` in `#8A3B07`.

**Visualisation by artifact type** (`viz` key in the prototype data):

| `viz` | Used for | Rendering |
| --- | --- | --- |
| `ontology` | Graph A ontology | Node-link graph. Concepts are white boxes with a blue outline (name + `→ target_hub`). Relationships are small amber diamonds on edges, labelled `REL_*`; n-ary relationships fan out from one diamond. Side list of concept cards: name, ELM archetype, definition, key authority tiers. |
| `dv` | `dv_model_contract.yaml`, `final_contract.yaml`, `final_canonical_ir.json` | Data Vault model graph. Hubs: solid `#1D4ED8`, white text. Links: `#FFF4E5` pills with an amber outline. Satellites: `#F2F3F5` boxes with a grey outline. RESTRICTED_PII satellites: dashed amber outline. Legend underneath. Use a layout engine (e.g. dagre/elk) in production; the prototype positions are hand-placed. |
| `table` | CSV sources, `graph_b_profile.json`, `drift_report`, `column_triage_ledger`, `cas_manifest`, `pipeline_input.yaml` (waves) | Mono table that scrolls horizontally, with an optional footnote (e.g. the redaction note). |
| `kv` | `harness_config.yaml`, run options, `wave_plan`, `steward_hold` | Two-column definition grid (key on a grey cell, value). |
| `checks` | `gate_verdicts`, `sandbox_report` (DV-DYN-001..015), `pipeline_report.json` | List of rows: ID · description · status pill. |
| `events` | `vault-ledger.jsonl` | Vertical timeline: dot (ink for PASS, amber otherwise) · `#seq` · `stage_id` · message · status pill. Live-append via SSE. |
| `doc` | unstructured docs, `extraction_contract` | Document text with highlighted spans (Tier-U1 `#DBEAFE`, Tier-U3 `#FFEDD5`). Beside it, extraction cards: id, verbatim quote, `span [start, end]`, tier, confidence, resolution decision. Spans come from `char_start`/`char_end`. |
| `code` | `final_four_zone.sql`, `@toon_v1` trace | Outline list (Zone 0–4, or TOON sections) beside a dark `<pre>`. The code pane is the raw view, so Split shows only this. |

## Data model the UI needs

```ts
type ArtifactGroup = 'Setting' | 'Input' | 'Intermediate' | 'Output';
type Viz = 'ontology' | 'dv' | 'table' | 'kv' | 'checks' | 'events' | 'doc' | 'code';

interface ArtifactView {
  id: string; name: string; type: string; fmt: string; group: ArtifactGroup; viz: Viz;
  short: string; desc: string; raw: string;               // raw already RBAC-redacted
  cols?: string[]; rows?: string[][]; foot?: string;      // table
  kv?: [string, string][];                                // kv
  checks?: [id: string, name: string, status: Status][];  // checks
  events?: [stageId: string, msg: string, status: Status][];
  outline?: [label: string, desc: string][];              // code
}

interface StageView {
  key: PipelineStageId; name: string; persona: AgentPersona; tier: 'reasoning' | 'fast';
  tag: Status; phase: 0 | 1 | 2 | 3; blurb: string; desc: string; note?: string;
  ins: string[]; outs: string[];                          // artifact ids
  steps: { name: string; kind?: 'decision' | 'loop'; branch?: string;
           reads: string[]; writes: string[]; desc: string }[];
  comps: [name: string, type: string, desc: string, sourcePath: string][];
}
```

Sources in the repo:
- Stage IDs and personas: `schemas/vault-observability-event.schema.json` and `PipelineStageId` in `packages/opendv-agent-harness/.../pipeline.py`.
- Stage status: the ledger / `AgenticPipelineReport`.
- Artifact paths: the `--output-dir` layout (`final_*`, `waves/wave_<w>/*`, `pipeline_report.json`, `vault-ledger.jsonl`).

Step lists and component descriptions are static metadata per stage. Ship them as a JSON/TS constant (start from `S` in `Main.dc.html`).

## UI state

`{ view: 'pipeline' | 'stage', stageIdx, selType: 'artifact' | 'step' | 'component' | null, selId, fullId | null, fullTab: 'viz' | 'raw' | 'split' }`

- Opening a stage clears the selection.
- Prev/next stage clears the selection.
- Opening the full view keeps the selection.
- Put `view`, `stageIdx` and `fullId` in the URL (`/stages/05`, `?artifact=final_contract`) so views are deep-linkable.

## Accessibility & responsiveness

- Every clickable element is a real `<button>`. Icon-only buttons have an `aria-label`.
- The full view is `role="dialog"` with a `role="tablist"`. Graph SVGs have `role="img"` and an `aria-label`.
- Text contrast is ≥ 4.5:1 (the smallest grey used is `#6B7280` on white).
- Columns use flex-wrap with min widths, so on narrow screens columns stack. Tables scroll horizontally inside their box.

## Not designed yet (open questions)

- What the wave filter actually does.
- Live-run state: running/pending stages, streaming token output.
- Resolving a steward hold from the inspector (approve/reject a candidate), which would map to `opendv hold approve|reject`.
- Diffing artifacts between waves or repair attempts (k=1 vs k=2).
