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

/** Number of hex characters rendered when displaying shortened SHA-256 digests. */
export const SHA256_SHORT_DISPLAY_CHARS = 16;

/** Multiplier converting [0, 1] grounding confidence scores into display percentages. */
export const PERCENTAGE_MULTIPLIER = 100;

/** Default form input string for external mapping confidence scores. */
export const DEFAULT_MAPPING_CONFIDENCE_INPUT = "0.85";

/** Minimum allowed external mapping confidence score. */
export const MIN_MAPPING_CONFIDENCE = 0;

/** Maximum allowed external mapping confidence score. */
export const MAX_MAPPING_CONFIDENCE = 1;

/** Confidence slider/number input step size. */
export const MAPPING_CONFIDENCE_STEP = 0.01;

/** Maximum allowed attribute identifier length in concept & proposal editors. */
export const MAX_ATTRIBUTE_NAME_LENGTH = 64;

/** Maximum allowed synonym string length per entry. */
export const MAX_SYNONYM_LENGTH = 256;

/** Default ELM archetype assigned to newly drafted concepts. */
export const DEFAULT_CONCEPT_ARCHETYPE = "CORE_BUSINESS_CONCEPT";

/** Default reference standard scheme for newly drafted external mappings. */
export const DEFAULT_MAPPING_SCHEME = "FIBO";

/** Default SKOS mapping predicate for newly drafted external mappings. */
export const DEFAULT_MAPPING_PREDICATE = "skos:exactMatch";

/** Decimal precision used when persisting user-entered confidence scores. */
export const CONFIDENCE_DECIMAL_PLACES = 4;

/** Genesis semantic version tag used as fallback when no snapshot exists yet. */
export const GENESIS_SEMVER_VERSION = "0.0.0";

/** Sentinel key used for unsaved brand-new proposal drafts in studio state. */
export const NEW_PROPOSAL_DRAFT_KEY = "__new__";

/** Canonical identifier prefixes for newly authored concepts and relationships. */
export const CONCEPT_URN_PREFIX = "urn:ontology:entity:";
export const TARGET_HUB_PREFIX = "hub_";
export const RELATIONSHIP_URN_PREFIX = "urn:ontology:relationship:";
export const TARGET_LINK_PREFIX = "lnk_";

/** Contract regex patterns for Concept URNs and Data Vault target hubs. */
export const CONCEPT_URN_REGEX =
	/^urn:(?:ontology:(?:provisional:)?entity|opendv:(?:provisional(?::(?:entity|concept))?|concept)):[a-z0-9_]+$/;
export const PARENT_CONCEPT_URN_REGEX =
	/^urn:(ontology:entity|opendv:concept):[a-z0-9_]+$/;
export const TARGET_HUB_REGEX = /^hub_[a-z0-9_]+$/;

/** Supported external mapping schemes for Concept and Proposal editors. */
export const EXTERNAL_MAPPING_SCHEME_OPTIONS = [
	{ value: "fibo", label: "FIBO" },
	{ value: "iso20022", label: "ISO 20022" },
	{ value: "edmcouncil", label: "EDM Council" },
	{ value: "custom", label: "Custom" },
] as const;

/** Supported ELM archetypes for Graph A Canonical Business Concepts. */
export const CONCEPT_ARCHETYPES = [
	"CORE_BUSINESS_CONCEPT",
	"CORE_CONCEPT",
	"REFERENCE_CONCEPT",
	"CONTEXT_CONCEPT",
	"EVENT_CONCEPT",
	"PARTY",
	"AGREEMENT",
	"PRODUCT",
	"ACCOUNT",
	"LOCATION",
] as const;

/** Supported SKOS alignment predicates for external reference ontology mappings. */
export const SKOS_PREDICATES = [
	"skos:exactMatch",
	"skos:closeMatch",
	"skos:broadMatch",
	"skos:narrowMatch",
	"skos:relatedMatch",
] as const;
