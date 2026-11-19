---
version: "1.0.0"
name: open-dv-claude-design-system
description: >
  Canonical Claude-aligned editorial design system for open-dv across both the
  Web Workbench (apps/opendv-web-workbench: 54px 4-tab topbar, 6-step Unified
  Wizard, Multi-View DV Schema Explorer, Session Logs Inspector) and the
  static documentation/marketing site (apps/opendv-website: 64px topbar, 96px
  editorial section rhythm). Anchored on warm cream surfaces (#faf9f5), warm
  coral brand CTAs (#cc785c), dark obsidian/navy product chrome (#181715), and
  bundled Instrument Serif (weight 400), DM Sans, and JetBrains Mono typefaces.

colors:
  primary: "#cc785c"
  primary-active: "#a9583e"
  primary-disabled: "#e6dfd8"
  ink: "#141413"
  body: "#3d3d3a"
  body-strong: "#252523"
  muted: "#6c6a64"
  muted-soft: "#8e8b82"
  hairline: "#e6dfd8"
  hairline-soft: "#ebe6df"
  hairline-strong: "#d5cdc2"
  canvas: "#faf9f5"
  surface-soft: "#f5f0e8"
  surface-card: "#efe9de"
  surface-cream-strong: "#e8e0d2"
  surface-dark: "#181715"
  surface-dark-elevated: "#252320"
  surface-dark-soft: "#1f1e1b"
  on-primary: "#ffffff"
  on-dark: "#faf9f5"
  on-dark-soft: "#a09d96"
  accent-teal: "#5db8a6"
  accent-amber: "#e8a55a"
  success: "#5db872"
  warning: "#d4a017"
  error: "#c64545"

typography:
  display-xl:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 64px
    fontWeight: 400
    lineHeight: 1.05
    letterSpacing: -1.5px
  display-lg:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 48px
    fontWeight: 400
    lineHeight: 1.1
    letterSpacing: -1px
  display-md:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 36px
    fontWeight: 400
    lineHeight: 1.15
    letterSpacing: -0.5px
  display-sm:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 28px
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: -0.3px
  title-lg:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 22px
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: 0
  title-md:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 18px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0
  title-sm:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 16px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0
  body-md:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: 0
  body-sm:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: 0
  caption:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0
  caption-uppercase:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 1.5px
  code:
    fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: 0
  button:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1
    letterSpacing: 0
  nav-link:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0
  workbench-h1:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 26px
    fontWeight: 400
    lineHeight: 1.08
    letterSpacing: -0.45px
  workbench-h2:
    fontFamily: "'Instrument Serif', Georgia, 'Times New Roman', serif"
    fontSize: 20px
    fontWeight: 400
    lineHeight: 1.15
    letterSpacing: -0.3px
  workbench-body:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: 0
  workbench-sub:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 12.5px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: 0
    color: "{colors.muted}"
  workbench-th:
    fontFamily: "'DM Sans', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: 10.5px
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: 0.04em
    textTransform: uppercase

rounded:
  xs: 4px
  sm: 6px
  md: 8px
  card-sm: 10px
  lg: 12px
  xl: 16px
  link-node: 20px
  pill: 9999px
  full: 9999px

spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  xxl: 48px
  section: 96px

components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 12px 20px
    height: 40px
  button-primary-active:
    backgroundColor: "{colors.primary-active}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
  button-primary-disabled:
    backgroundColor: "{colors.primary-disabled}"
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderColor: "{colors.hairline-strong}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 12px 20px
    height: 40px
  button-secondary-on-dark:
    backgroundColor: "{colors.surface-dark-elevated}"
    textColor: "{colors.on-dark}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 12px 20px
  button-text-link:
    backgroundColor: transparent
    textColor: "{colors.ink}"
    typography: "{typography.button}"
  button-icon-circular:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.full}"
    size: 36px
  text-link:
    backgroundColor: transparent
    textColor: "{colors.primary}"
    typography: "{typography.body-md}"
  btn-cite:
    backgroundColor: "{colors.canvas}"
    borderColor: "{colors.primary}"
    textColor: "{colors.primary-active}"
    typography: "{typography.caption}"
    rounded: "{rounded.sm}"
    padding: 3px 9px
  top-nav:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.nav-link}"
    height: 64px
  workbench-topbar:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderBottomColor: "{colors.hairline}"
    typography: "{typography.workbench-body}"
    height: 54px
    padding: 0 24px
  workbench-stepper:
    backgroundColor: "{colors.surface-soft}"
    borderBottomColor: "{colors.hairline}"
    height: 44px
    explorerSubBarHeight: 46px
    padding: 0 24px
  workbench-footer:
    backgroundColor: "{colors.canvas}"
    borderTopColor: "{colors.hairline}"
    height: 52px
    padding: 0 24px
  workbench-drawer:
    backgroundColor: "{colors.canvas}"
    borderLeftColor: "{colors.hairline}"
    width: 716px
    height: 920px
    backdrop: "rgba(20, 20, 19, 0.34)"
    boxShadow: "-12px 0 32px rgba(20, 20, 19, 0.14)"
  hero-band:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.display-xl}"
    padding: 96px
  hero-illustration-card:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.xl}"
  feature-card:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.ink}"
    typography: "{typography.title-md}"
    rounded: "{rounded.lg}"
    padding: 32px
  card-subtle:
    backgroundColor: "{colors.canvas}"
    borderColor: "{colors.hairline}"
    rounded: "{rounded.card-sm}"
    padding: 9px 12px
  card-selected:
    backgroundColor: "{colors.surface-cream-strong}"
    borderColor: "{colors.primary}"
    borderWidth: 1.5px
    rounded: "{rounded.card-sm}"
    padding: 9px 12px
  product-mockup-card-dark:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.on-dark}"
    typography: "{typography.title-md}"
    rounded: "{rounded.lg}"
    padding: 32px
  code-window-card:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.on-dark}"
    typography: "{typography.code}"
    rounded: "{rounded.lg}"
    padding: 24px
  model-comparison-card:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.title-md}"
    rounded: "{rounded.lg}"
    padding: 32px
  pricing-tier-card:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.title-lg}"
    rounded: "{rounded.lg}"
    padding: 32px
  pricing-tier-card-featured:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.on-dark}"
    typography: "{typography.title-lg}"
    rounded: "{rounded.lg}"
    padding: 32px
  callout-card-coral:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.title-md}"
    rounded: "{rounded.lg}"
    padding: 32px
  connector-tile:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.title-sm}"
    rounded: "{rounded.lg}"
    padding: 20px
  text-input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    padding: 10px 14px
    height: 40px
  text-input-focused:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderColor: "{colors.primary}"
    boxShadow: "0 0 0 3px rgba(204, 120, 92, 0.15)"
    rounded: "{rounded.md}"
  cookie-consent-card:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.on-dark}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.lg}"
    padding: 24px
  category-tab:
    backgroundColor: transparent
    textColor: "{colors.muted}"
    typography: "{typography.nav-link}"
    padding: 8px 14px
    rounded: "{rounded.md}"
  category-tab-active:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.ink}"
    typography: "{typography.nav-link}"
    rounded: "{rounded.md}"
  badge-pill:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.ink}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 4px 12px
  badge-coral:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.caption-uppercase}"
    rounded: "{rounded.pill}"
    padding: 4px 12px
  cta-band-coral:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.display-sm}"
    rounded: "{rounded.lg}"
    padding: 64px
  cta-band-dark:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.on-dark}"
    typography: "{typography.display-sm}"
    rounded: "{rounded.lg}"
    padding: 64px
  footer:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.on-dark-soft}"
    typography: "{typography.body-sm}"
    padding: 64px
---

# `open-dv` Design System (`DESIGN.md`)

> Adapted from `VoltAgent/awesome-design-md/design-md/claude/DESIGN.md` and calibrated against the 12 approved `open-dv` Unified Wizard, DV Schema Explorer, and Session Logs mockups (`docs/assets/unified-wizard/studio_mockup_*.png`).

---

## Overview

`open-dv` adopts a warm-canvas, editorial interface grounded in Claude's cream, coral, and dark-chrome design language. Rather than using cold SaaS grays or saturated blues, the entire application rests on a **tinted cream canvas** (`{colors.canvas}` — `#faf9f5`) paired with **Instrument Serif** display headlines at regular weight (`400`), **DM Sans** body/UI copy, and **JetBrains Mono** for hashes, SQL, IR, and logs.

Brand voltage comes from the **cream + coral pairing** (`{colors.primary}` — `#cc785c`) reserved for primary CTAs, active step numbers, citation actions, and full-bleed alert/setup callouts, contrasted against **dark obsidian/navy product surfaces** (`{colors.surface-dark}` — `#181715`) for SQL code windows, the 7-stage pipeline console, Data Vault Hub nodes, session digest badges, and LLM prompt/response trace panels.

Two surface densities share the exact same color, font, and border grammar:
1. **Web Workbench (`apps/opendv-web-workbench`)**: High-density 4-tab application chrome (`54px` `{component.workbench-topbar}`, `44px` step track / `46px` explorer sub-bar, `52px` `{component.workbench-footer}`, `716px × 920px` right inspector drawers).
2. **GitHub Pages Documentation & Marketing Site (`apps/opendv-website`)**: Editorial long-form publication rhythm (`64px` `{component.top-nav}`, `{spacing.section}` `96px` vertical band pacing, `1200px` centered container).

### Canonical 5-Zone (`Z0–Z4`) SQL & Schema Standard

All Web Workbench UI views, DV Schema Explorer filters, documentation, and website tours standardize on the **5-Zone (`Z0–Z4`)** taxonomy and **`information_mart`** schema name:
- **`Z0` — Target Schemas, Bronze Seeds, DDL & Zero/Ghost Keys** (`bronze`, `ref_*`, Dual Zero Keys `-1 -> '0'*64` / `-2 -> 'f'*64`, Ghost Record `'0'*64`).
- **`Z1` — Hashed Staging Views** (`staging.stg_*` with `Algorithm A.5` length-prefixed SHA-256, 3-tier `BKCC`, `EVO-03` rename aliases, `EVO-02` `CAST(NULL AS <type>)`).
- **`Z2` — Insert-Only Raw Vault Loads** (`raw_vault.hub_*`, `lnk_*`, `t_lnk_*`, `sat_*`, `sat_*_ext_v*`, `sat_*_pii`).
- **`Z3` — Business Vault PITs, Bridges & SALs** (`business_vault.sal_*`, `esat_sal_*`, `pit_*`, `brg_*`).
- **`Z4` — Information Mart Current Views & Bi-Temporal Audit Queries** (`information_mart.vw_*_current`, `Queries D.10 / D.10b`).

> **Parity Compatibility Note:** Early mockups `06` and `06b` carried legacy `"4-Zone"` / `marts` labels, while `06c` and normative `FR-DVM-14` use **5-Zone (`Z0–Z4`)** and `information_mart`. All UI, website, and new docs must use **5-Zone (`Z0–Z4`)** and `information_mart`; the internal Python module path `compiler_4zone` and CLI parity report header `"Open-DV 4-Zone SQL Compilation Report"` stay intact to preserve `tests/parity/` compatibility.

**Key Characteristics:**
- Warm cream canvas (`{colors.canvas}` — `#faf9f5`) with dark warm-ink typography (`{colors.ink}` — `#141413`).
- Coral primary CTA (`{colors.primary}` — `#cc785c`), active darken (`#a9583e`), and coral citation chip (`{component.btn-cite}`).
- Regular-weight (`400`) `Instrument Serif` display titles with negative tracking (`-0.3px` to `-1.5px`) paired with `DM Sans` (`400`/`500`/`600`) UI controls and `JetBrains Mono` technical metadata.
- Three alternating surface tiers: cream canvas (`#faf9f5`) ➔ soft/cream cards (`#f5f0e8` / `#efe9de` / `#e8e0d2`) ➔ dark product chrome (`#181715` / `#1f1e1b` / `#252320`).
- **4-Spoke Radial Spike Mark (`SPIKE_MARK_SVG`)**: `16×16` inline SVG (`viewBox="0 0 24 24"`, `#141413`, `stroke-width="2.1"`, `stroke-linecap="round"`) anchoring the `open-dv` brand prefix on cream topbars (`{colors.on-dark}` on dark footers where needed for `>= 4.5:1` contrast, never inverted inside light wordmarks).

---

## Colors

### Brand & Accent
- **Primary / Coral** (`{colors.primary}` — `#cc785c`): Primary CTAs (`Verify & Save`, `Run Agent Discovery`, `Run 7-Stage Pipeline`), active step badge fills, selected card borders (`1.5px`), and Step 4a setup callout bands.
- **Primary Active** (`{colors.primary-active}` — `#a9583e`): Hover/pressed CTA fill and `{component.btn-cite}` citation button text.
- **Primary Disabled** (`{colors.primary-disabled}` — `#e6dfd8`): Disabled button background paired with `{colors.muted}` text (also shared with hairline borders).
- **Accent Teal** (`{colors.accent-teal}` — `#5db8a6`): Custom Beyond-FIBO entities (`+ Custom Entity`), `broadMatch` tags, `Z3 PIT & Bridges`, and active terminal/connector dots.
- **Accent Amber** (`{colors.accent-amber}` — `#e8a55a`): Document-observed concepts, `MNPI` / PII-masked row hints, and citation `<mark>` spans (`rgba(232, 165, 90, 0.35)`).

### Surfaces & Borders
- **Canvas** (`{colors.canvas}` — `#faf9f5`): Default page floor, workbench topbar (`54px`), footer (`52px`), inspector drawers, and default card/input backgrounds.
- **Surface Soft** (`{colors.surface-soft}` — `#f5f0e8`): Wizard stepper strip (`44px`), Explorer/Logs sub-bars (`46px`), table `<th>` headers, and DAG dot-grid canvas base (`radial-gradient(#d5cdc2 1px, #f5f0e8 1px)` at `18px 18px`).
- **Surface Card** (`{colors.surface-card}` — `#efe9de`): Cream grouping cards, active top-nav tab pill, and default badge pills.
- **Surface Cream Strong** (`{colors.surface-cream-strong}` — `#e8e0d2`): Active stepper pill, selected pack/session card fill (`{component.card-selected}`), and emphasized status badges.
- **Surface Dark** (`{colors.surface-dark}` — `#181715`): 7-Stage Pipeline Console, Zone `0–4` SQL code window, session digest pill (`pill-dark`), Data Vault `Hub` nodes in DAGs, LLM Prompt/Response trace window, featured pricing cards, and website footer.
- **Surface Dark Soft** (`{colors.surface-dark-soft}` — `#1f1e1b`): Inner code blocks, SQL statement frames, and log detail panes nested inside `{colors.surface-dark}` cards.
- **Surface Dark Elevated** (`{colors.surface-dark-elevated}` — `#252320`): Dark-surface secondary buttons, dark badges (`b-dark`), and elevated controls inside dark panels.
- **Hairline** (`{colors.hairline}` — `#e6dfd8`): Primary `1px` structural divider (topbar bottom, stepper bottom, footer top, card borders, `<th>` bottom border).
- **Hairline Soft** (`{colors.hairline-soft}` — `#ebe6df`): Inner table row `<td>` dividers and subtle horizontal splits inside cards.
- **Hairline Strong** (`{colors.hairline-strong}` — `#d5cdc2`): Secondary button borders (`.btn`), topbar wordmark/tab separator (`.sep`), active stepper border, dashed file dropzones, and DAG dot-grid dots.

### Text
- **Ink** (`{colors.ink}` — `#141413`): All serif headlines, card titles, primary labels, active nav/step text, and dark-on-light contrast anchor.
- **Body Strong** (`{colors.body-strong}` — `#252523`): Table cell values (`<td>`), emphasized option labels, and primary list items.
- **Body** (`{colors.body}` — `#3d3d3a`): Default running UI and editorial prose.
- **Muted** (`{colors.muted}` — `#6c6a64`): Subtitles (`.sub`), table headers (`<th>`), inactive nav tabs, form labels, and secondary metadata.
- **Muted Soft** (`{colors.muted-soft}` — `#8e8b82`): Step arrows (`➔`), disabled button text, fine-print captions, and SQL line numbers.
- **On Primary** (`{colors.on-primary}` — `#ffffff`): Text/icons on coral `#cc785c` buttons, badges, and callout cards.
- **On Dark** (`{colors.on-dark}` — `#faf9f5`): Primary text inside `#181715` / `#1f1e1b` / `#252320` dark chrome surfaces.
- **On Dark Soft** (`{colors.on-dark-soft}` — `#a09d96`): Secondary labels, timestamps, line numbers, and footer links on dark surfaces.

### Semantic & Utility Values
- **Success** (`{colors.success}` — `#5db872`): Agent Ready dot, completed step badges (`✓`), Gate `0–6` PASS indicators, and `MINOR` additive checks.
- **Warning** (`{colors.warning}` — `#d4a017`): Pending/Hold indicators (`StewardHold`), unconfigured Agent warning dot, and soft-deprecation hints.
- **Error** (`{colors.error}` — `#c64545`): `422 AGENT_NOT_CONFIGURED` errors, `MAJOR` Opt-In breaking cards, `DROP`/`DELETE` blocked SQL badges, and `b-error` pills.
- **Derived Utility Values**:
  - Dark-card inner divider: `#33312d` (used inside `#181715` dark log/lineage cards in `06c` and `07`).
  - Citation span `<mark>` highlight: `rgba(232, 165, 90, 0.35)` with `#141413` ink.
  - Focused input ring: `0 0 0 3px rgba(204, 120, 92, 0.15)` paired with `1px solid #cc785c`.
  - Drawer backdrop & shadow: `rgba(20, 20, 19, 0.34)` backdrop; `-12px 0 32px rgba(20, 20, 19, 0.14)` left elevation shadow on `drawer-panel`.

---

## Typography

### Bundled Font Families
`open-dv` bundles 4 `.woff2` webfont assets in `apps/opendv-website/public/fonts/` (and reuses the exact same families in `apps/opendv-web-workbench` static builds with zero external CDN runtime dependency):
- **Display Serif**: `'Instrument Serif'` (`instrument-serif-latin.woff2` normal & `instrument-serif-italic-latin.woff2` italic, weight `400`, fallback `Georgia, 'Times New Roman', serif`).
- **UI & Editorial Sans**: `'DM Sans'` (`dm-sans-latin.woff2`, variable weights `400 700`, fallback `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`).
- **Technical Monospace**: `'JetBrains Mono'` (`jetbrains-mono-latin.woff2`, variable weights `400 700`, fallback `ui-monospace, SFMono-Regular, Menlo, monospace`).

### Scale Hierarchy

#### Website / Editorial Scale (`apps/opendv-website`)
| Token | Size | Weight | Line Height | Letter Spacing | Use |
|---|---|---|---|---|---|
| `{typography.display-xl}` | `64px` | `400` | `1.05` | `-1.5px` | Hero headline (`Instrument Serif`) |
| `{typography.display-lg}` | `48px` | `400` | `1.1` | `-1px` | Major section heads (`Instrument Serif`) |
| `{typography.display-md}` | `36px` | `400` | `1.15` | `-0.5px` | Sub-section / tour step titles (`Instrument Serif`) |
| `{typography.display-sm}` | `28px` | `400` | `1.2` | `-0.3px` | CTA bands & showcase heads (`Instrument Serif`) |
| `{typography.title-lg}` | `22px` | `500` | `1.3` | `0` | Tier & architecture card titles (`DM Sans`) |
| `{typography.title-md}` | `18px` | `500` | `1.4` | `0` | Feature card titles (`DM Sans`) |
| `{typography.title-sm}` | `16px` | `500` | `1.4` | `0` | Connector tile labels (`DM Sans`) |
| `{typography.body-md}` | `16px` | `400` | `1.55` | `0` | Editorial running prose (`DM Sans`) |
| `{typography.body-sm}` | `14px` | `400` | `1.55` | `0` | Footer copy & card details (`DM Sans`) |
| `{typography.caption}` | `13px` | `500` | `1.4` | `0` | Badges & UI captions (`DM Sans`) |
| `{typography.caption-uppercase}` | `12px` | `500` | `1.4` | `1.5px` | Uppercase tags (`DM Sans`) |
| `{typography.code}` | `14px` | `400` | `1.6` | `0` | Code windows & CLI examples (`JetBrains Mono`) |
| `{typography.button}` | `14px` | `500` | `1.0` | `0` | Website CTA labels (`DM Sans`) |
| `{typography.nav-link}` | `14px` | `500` | `1.4` | `0` | Website `64px` top-nav links (`DM Sans`) |

#### Web Workbench Density Scale (`apps/opendv-web-workbench`)
| Token | Size | Weight | Line Height | Letter Spacing | Use |
|---|---|---|---|---|---|
| `{typography.workbench-h1}` | `26px` | `400` | `1.08` | `-0.45px` | Wizard Step & Explorer page headings (`Instrument Serif`) |
| `{typography.workbench-h2}` | `20px` | `400` | `1.15` | `-0.3px` | Drawer & sub-panel titles (`Instrument Serif`) |
| `{typography.workbench-body}` | `13px` | `400` | `1.45` | `0` | Workbench base text & nav tabs (`DM Sans`) |
| `{typography.workbench-sub}` | `12.5px` | `400` | `1.45` | `0` | Step subtitles in `{colors.muted}` (`DM Sans`) |
| `{typography.workbench-th}` | `10.5px` | `500` | `1.3` | `0.04em` | Uppercase table headers on `#f5f0e8` (`DM Sans`) |

### Principles
- **Display Serif at Weight 400 Only:** Never bold `Instrument Serif` (`fontWeight: 700` distorts letterforms); keep weight at `400` with negative tracking (`-0.3px` to `-1.5px`).
- **Humanist UI Sans + Tabular Mono:** `DM Sans` stays at `400` for copy and `500`/`600` for controls/badges. All SHA-256 hashes, SemVer tags, SQL columns, BKCC tiers, token counts, and log timestamps use `JetBrains Mono` (`10px`–`12.5px` in workbench, `14px` in website code windows).

---

## Layout

### Spacing System
- **Base unit:** `4px`.
- **Tokens:** `{spacing.xxs}` `4px` · `{spacing.xs}` `8px` · `{spacing.sm}` `12px` · `{spacing.md}` `16px` · `{spacing.lg}` `24px` · `{spacing.xl}` `32px` · `{spacing.xxl}` `48px` · `{spacing.section}` `96px`.

### Workbench Frame vs. Website Container
- **Web Workbench Frame (`1440×920` design viewport, full-height app shell):**
  - Top Bar: `54px` (`padding: 0 24px`, `1px solid #e6dfd8` bottom). Left: `16×16` `SPIKE_MARK_SVG` + `open-dv` + `#d5cdc2` separator + 4 tabs (**`Ontology & Pipeline` | `DV Schema Explorer` | `Run Explorer` | `Settings`**). Right: live Agent Readiness pill (`● Agent Ready` / warning dot), live Cost Meter pill (session cumulative tokens + USD), `Logs & Traces (<count>)` button, and dark session digest pill (`pill-dark`).
  - Sub-Bar / Stepper: `44px` on Steps 1–6 (`#f5f0e8`, 6 numbered step pills joined by `➔`), `46px` on DV Schema Explorer & Session Logs sub-bars.
  - Main Workspace: `flex: 1`, `padding: 14px 24px`, `gap: 11px`.
  - Wizard Footer: `52px` (`padding: 0 24px`, `#faf9f5`, `1px solid #e6dfd8` top) with Back, secondary actions (`Fast-Forward to Step 6`, `Export Ontology Only`, `View Stage Logs ↗`), and primary Coral CTA.
- **Website Container (`apps/opendv-website`):**
  - Top Nav: `64px` (`{component.top-nav}`), centered `1200px` max content width, `{spacing.section}` (`96px`) vertical rhythm, `32px` card padding, `3-up` / `2-up` / `1-up` feature grids and `4-up` connector grids.

---

## Elevation & Depth

| Level | Treatment | Use |
|---|---|---|
| Flat | No shadow, hairline divider only | Workbench topbar (`54px`), stepper (`44px`), footer (`52px`), website top nav (`64px`) |
| Soft Hairline Card | `1px solid #e6dfd8` on `#faf9f5` | `{component.card-subtle}`, data tables, form containers |
| Cream Grouping Card | `#efe9de` fill + `1px solid #e6dfd8` | `{component.feature-card}`, column panels in Step 4b/Step 5/Step 6 |
| Selected Card | `#e8e0d2` fill + `1.5px solid #cc785c` | `{component.card-selected}` active pack/baseline/session choice |
| Dark Product Chrome | `#181715` fill + `1px solid #252320` | `{component.product-mockup-card-dark}`, `{component.code-window-card}`, Step 6 console, Hub nodes, `SessionLogsInspector` trace pane |
| Slide-Over Drawer | `backdrop: rgba(20,20,19,0.34)` + `-12px 0 32px rgba(20,20,19,0.14)` | `{component.workbench-drawer}` (`716×920`, e.g., `CitationInspectorDrawer`, `VisualEditorDrawer`, `YamlDrawer`) |
| Interactive Focus | `0 0 0 3px rgba(204,120,92,0.15)` + `#cc785c` border | `{component.text-input-focused}` |

### React Flow & Accessibility Depth Rules
From `.agents/rules/ontology-studio-learnings.md §7`:
- **DAG Canvas Background:** `radial-gradient(#d5cdc2 1px, #f5f0e8 1px)` at `18px 18px` inside `.card-subtle` / `.card-cream` wrappers.
- **React Flow Controls & Minimap Contrast:** Explicitly override `--xy-controls-button-*` and `--xy-minimap-*` CSS variables and `.react-flow__controls button svg { fill: currentColor; }` across both light cream and dark chrome canvases so every zoom, fit-view, and minimap element maintains **`>= 4.5:1` WCAG AA** contrast.
- **Native Top-Layer Semantics:** All modals and collapsible sections prefer native HTML `<dialog>` (`overscroll-behavior: contain`) and `<details>` with full keyboard focus trapping.
- **Bipartite Topology Visual Grammar:** `Hub` nodes render in dark obsidian (`#181715` fill, `#faf9f5` text, `rounded.md` `8px`); `Link` / `SAL` nodes render as warm cream/amber pill ellipses (`border-radius: 18px–22px`, `1.5px` border, zero Hub➔Hub or Link➔Link edges); `Satellite` / `PIT` nodes attach single-parent off Hubs/Links on `#faf9f5` / `#efe9de` cards (`#5db8a6` teal accent for `PIT`/`Bridge`, `#cc785c` coral border for `sat_*_pii`).

---

## Shapes

| Token | Value | Use |
|---|---|---|
| `{rounded.xs}` | `4px` | Citation `<mark>` spans, tiny status chips |
| `{rounded.sm}` | `6px` | Compact `.btn-sm` buttons, `{component.btn-cite}`, dropdown items |
| `{rounded.md}` | `8px` | Standard buttons, text inputs, nav tabs, stepper items, `Hub` DAG nodes |
| `{rounded.card-sm}` | `10px` | Workbench sub-cards (`{component.card-subtle}`, `{component.card-selected}`) |
| `{rounded.lg}` | `12px` | Primary cards (`feature-card`, `card-cream`, `card-dark`, `code-window-card`, `callout-coral`) |
| `{rounded.xl}` | `16px` | Website hero illustration & marquee containers |
| `{rounded.link-node}` | `18px–22px` | Bipartite `lnk_*`, `t_lnk_*`, and `sal_*` pills in DAG & DV Schema Explorer |
| `{rounded.pill}` | `9999px` | Status badges, step numbers, header status/digest pills |
| `{rounded.full}` | `9999px` / `50%` | `7px` status dots, circular icon buttons |

---

## Components

Every component defined in `VoltAgent/claude/DESIGN.md` plus `open-dv` workbench chrome maps cleanly to shared tokens:

1. **Navigation & Chrome**: `top-nav` (`64px` website), `workbench-topbar` (`54px`, 4 tabs + `SPIKE_MARK_SVG` + live Agent Ready pill + live Cost Meter + `Logs & Traces` + `pill-dark` session digest), `workbench-stepper` (`44px`/`46px`), `workbench-footer` (`52px`), `workbench-drawer` (`716×920` slide-over), `footer` (`#181715` 4-column dark footer).
2. **Buttons & Links**: `button-primary` (`#cc785c`), `button-primary-active` (`#a9583e`), `button-primary-disabled` (`#e6dfd8`), `button-secondary` (`#faf9f5` + `#d5cdc2` border), `button-secondary-on-dark` (`#252320` + `#3d3d3a` border), `button-text-link`, `button-icon-circular` (`36px`), `text-link` (`#cc785c` underlined), `btn-cite` (`#cc785c` border / `#a9583e` text).
3. **Cards & Product Surfaces**: `hero-band`, `hero-illustration-card`, `feature-card` (`#efe9de`), `card-subtle` (`#faf9f5` `10px`), `card-selected` (`#e8e0d2` + `1.5px #cc785c`), `product-mockup-card-dark` (`#181715`), `code-window-card` (`#181715` outer + `#1f1e1b` inner `JetBrains Mono` block for 5-Zone SQL, YAML, Canonical IR, and prompt/response logs), `model-comparison-card`, `pricing-tier-card`, `pricing-tier-card-featured`, `callout-card-coral` (used in Step 4a `AGENT_NOT_CONFIGURED` / remote-unauthenticated warning banners), `connector-tile` (Step 2 SQLite/DuckDB/Databricks UC/CSV/Parquet/PDF cards).
4. **Inputs, Tabs & Badges**: `text-input`, `text-input-focused` (`3px rgba(204,120,92,0.15)`), `cookie-consent-card`, `category-tab`, `category-tab-active` (Zone filters `All Zones`, `Z0 Refs`, `Z1 Staging`, `Z2 Hubs`, `Z2 Links & SAL`, `Z2 Satellites`, `Z3 PIT & Bridges`, `PII Split Only`, `+ Wave Delta`, and `SessionLogsInspector` deep tabs), `badge-pill`, `badge-coral`, `cta-band-coral`, `cta-band-dark`.
5. **Workbench Domain Components**:
   - `<AgentHarnessConfigCard />`: Shared provider/model/budget configurator rendered identically in `Settings` and Step 4a `AgentSetupGate`.
   - `StewardHoldModal` / Inline Hold Card: Surfaces `PAUSED_HOLD` (`Tier-U2` span review, `S3` Firewall, `TOKEN_BUDGET_EXCEEDED` / `COST_BUDGET_EXCEEDED` `D21`) directly inside Step 6 (`Approve / Reject & Resume`, `Raise budget & resume`, `Abort`, `↺ Sync Drift to Step 4`).
   - `SessionLogsInspector`: Live `EventSource` log timeline (`● Follow Live Tail` / `⏸ Pause Auto-Scroll`) with 4 deep tabs (`Masked Prompt & Context`, `Streaming / Structured LLM Response`, `Tool Calls & @toon_v1 Traces`, `Raw JSON Event`).

---

## Do's and Don'ts

### Do
- Anchor every page and workbench pane on warm cream `{colors.canvas}` (`#faf9f5`), layering `#f5f0e8` ➔ `#efe9de` ➔ `#e8e0d2` for elevation.
- Render every display heading (`h1`, `h2`) in weight-`400` `Instrument Serif` with negative tracking (`-0.3px` to `-1.5px`).
- Reserve `{colors.primary}` (`#cc785c`) strictly for primary CTAs, active step numbers, citation chips (`btn-cite`), selected card borders, and critical callout bands (`422 AGENT_NOT_CONFIGURED`, `--allow-remote-unauthenticated`).
- Use `{colors.surface-dark}` (`#181715`) and `{colors.surface-dark-soft}` (`#1f1e1b`) for genuine technical chrome: 7-Stage Pipeline Console, 5-Zone (`Z0–Z4`) SQL previews, `Hub` nodes, session digest pills, and live LLM prompt/response traces.
- Keep all terminal IRI fragments readable via `.split(/[\/#:]/).filter(Boolean).pop()` with full IRI tooltips and ellipsis overflow protection inside DAG nodes.
- Maintain `>= 4.5:1` WCAG AA contrast across all light and dark surfaces, including React Flow controls and minimap.

### Don't
- Don't use cool gray/blue/slate backgrounds (`#F6F7F8`, `#0b0f19`, `#1D4ED8`) or pure `#ffffff` page canvases.
- Don't set `font-weight: 700` on `Instrument Serif` display headings; weight stays `400`.
- Don't add deterministic mode toggles anywhere in the Web Workbench UI or Settings (`D28`: UI is 100% Agent-first; `--mode deterministic` belongs only to headless CLI/API tests).
- Don't label Step 6 or DV Schema Explorer SQL views as `"4-Zone"` or `marts` in new UI/website copy—use **5-Zone (`Z0–Z4`)** and **`information_mart`**.
- Don't duplicate `<AgentHarnessConfigCard />`, connector inspection UI, or `GraphCanvas` logic; compose the single canonical components (`P1`–`P3`).

---

## Responsive Behavior (`D26`)

| Breakpoint | Width | Workbench Behavior | Website Behavior |
|---|---|---|---|
| **Wide** | `> 1440px` | Centered/fluid workbench workspace up to `1680px`; side-by-side panels and `716px` drawers preserve full density | `1200px` centered max container with outer cream breathing room |
| **Desktop (Primary)** | `1024px – 1440px` (`1440×920` baseline, `1024×768` compact) | Full 4-tab `54px` topbar, horizontal 6-step stepper (`44px`), multi-column Step 4b/Step 5/Step 6 layouts, interactive `elkjs` DAGs, and live Safe SQL Runner | Full `64px` top-nav, 6/6 hero grid, 3-up feature grids, 4-up/6-up connector grids |
| **Tablet** | `768px – 1024px` | Compact horizontal nav/stepper with wrapping badges; 2-column split views; slide-over drawers cap at `min(716px, 92vw)` | Tightened top-nav; 2-up feature cards; 3-up connector tiles |
| **Mobile / Narrow (`D26`)** | `< 768px` (verified at `390×844`) | **Readable & navigable single-column stacked layout** (`scrollWidth <= clientWidth`, zero horizontal overflow); collapsible nav & stepper menu; **graph editing (`VisualEditorDrawer`) and ad-hoc SQL runner downgrade cleanly to view-only mode** | Hamburger nav drawer on `#faf9f5`; `64px -> 32px` hero h1; 1-up stacked feature/connector grids; code windows scroll horizontally (`overflow-x: auto`) |

### Touch Targets
- Primary & secondary buttons: minimum `40px` height on website (`32px–36px` compact buttons on desktop workbench, expanding to `>= 40px` below `768px`).
- Circular icon buttons: `36×36px`; text inputs: `40px` (`34px–38px` inside dense workbench forms); connector tiles: full-card click/tap target.

---

## Iteration Guide

1. Reference tokens via semantic YAML keys (`{colors.canvas}`, `{colors.primary}`, `{typography.workbench-h1}`, `{component.code-window-card}`) rather than scattering raw hex literals across TSX/CSS files (`apps/opendv-web-workbench/src/explorer/design-tokens.ts` and `apps/opendv-website/src/styles/tokens.css` are the single token emitters).
2. Keep the three-tone polarity intact: cream (`#faf9f5` / `#efe9de`) + coral (`#cc785c`) + dark obsidian (`#181715`). Never introduce arbitrary purple/blue surface themes.
3. Every new modal/drawer uses native `<dialog>` or `workbench-drawer` with `overscroll-behavior: contain` and `>= 4.5:1` contrast.
4. Keep every `.ts`/`.tsx` UI module strictly `< 5,000` `cl100k_base` tokens (`NFR-MOD-01`).

---

## Known Gaps

- `Copernicus` and `StyreneB` from Anthropic's original spec are proprietary; `open-dv` standardizes on bundled open-source **`Instrument Serif`** (weight `400`) + **`DM Sans`** + **`JetBrains Mono`** across both workbench and website so there is zero font licensing friction or external CDN dependency.
- `SPIKE_MARK_SVG` is an inline `16×16` SVG asset rather than a CSS token; keep it unified in a single shared TSX/Astro partial.
- Legacy `"4-Zone"` naming inside `packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/compiler_4zone/` and the CLI parity header `"Open-DV 4-Zone SQL Compilation Report"` stays frozen for backward compatibility with `tests/parity/`, while all UI and user-facing docs present **5-Zone (`Z0–Z4`)**.
- Phase 3 (`apps/opendv-web-workbench`) and Phase 5 (`apps/opendv-website`) execute the concrete CSS/TSX migration to these tokens; Phase 0 freezes the contract.
