/**
 * Shared Ontology Studio constants synchronized with platform API contracts.
 */

/** Maximum source file upload size accepted by Platform API (100 MiB). */
export const STUDIO_MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

/** Maximum synonyms allowed per concept in the Visual Editor. */
export const MAX_SYNONYMS_PER_CONCEPT = 50;

/** Minimum external grounding confidence threshold required by ONT-EVO-002 Path A. */
export const GROUNDING_MIN_CONFIDENCE = 0.65;

/** Transient reset delay (ms) after copying exported ontology text to clipboard. */
export const CLIPBOARD_FEEDBACK_RESET_MS = 2000;

/** Transient reset delay (ms) after saving concept/edge changes in Visual Editor. */
export const SAVE_FEEDBACK_RESET_MS = 2500;

/** Default ELK layout timeout (ms) before falling back to deterministic layered layout. */
export const ELK_LAYOUT_DEFAULT_TIMEOUT_MS = 250;

/** Default ELK/layered auto-layout node box dimensions and inter-node spacing (px). */
export const ELK_NODE_WIDTH = 280;
export const ELK_NODE_HEIGHT = 160;
export const ELK_NODE_SPACING = 80;
export const ELK_LAYER_SPACING = 140;

/** Deterministic grid fallback geometry (px / columns) exceeding max card width (300px). */
export const GRID_LAYOUT_ORIGIN = 100;
export const GRID_LAYOUT_X_STRIDE = 340;
export const GRID_LAYOUT_Y_STRIDE = 220;
export const GRID_LAYOUT_COLS = 4;
export const CANVAS_FALLBACK_GRID_COLS = 5;

/** Minimum character length required for governance waiver and promote rationales. */
export const MIN_RATIONALE_LENGTH = 10;

/** Canonical 64-hex-zero SHA-256 baseline representing a greenfield workspace promotion. */
export const GREENFIELD_ZERO_HASH_SHA256 = "0".repeat(64);

/** Relative filesystem location where `opendv serve` persists the local studio token. */
export const LOCAL_TOKEN_PATH_HINT = ".opendv/ontology_studio/.local_token";
