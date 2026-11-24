"""Plain-language UX translations for all 34 closed V2 ErrorCodes & Guided templates."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import (
    ErrorCategory,
    ErrorCode,
    ProblemDetails,
    get_error_entry,
)

FORBIDDEN_GUIDED_JARGON: re.Pattern[str] = re.compile(
    r"(?i)(?<![A-Za-z0-9])("
    r"DV-(?:STR|DYN|COL|AST|EVO|AP|G)-\d*"
    r"|ONT-(?:AP|EVO)-\d+"
    r"|ALG\.?\s*A\.[1-9][ab]?"
    r"|EVAL-(?:AST|RUN|FMT)-\d+"
    r"|(?:TRAP|BRK|KEY|TMP|INV|EVO)-\d+"
    r"|BKCC|CDK|CEGAR|SQLGlot|Hash[\s_]*Diff|TIER_U3"
    r")(?![A-Za-z0-9])"
)

GUIDED_MILESTONES: tuple[str, str, str, str] = (
    "Scope & Map",
    "Discovery & Ontology",
    "Vault Synthesis & Proof",
    "Target Build",
)

TemplateKey = Literal[
    "step4_hold",
    "step6_hold",
    "step6_v6b_waiver",
    "step6_v8_horizon",
    "step6_readiness",
]

FALLBACK_TEMPLATES: dict[TemplateKey, dict[str, str]] = {
    "step4_hold": {
        "title": "Business meaning needed for {entity_label}",
        "prompt": (
            "Please confirm how {entity_label} identifies unique records across your "
            "source files so discovery can continue safely."
        ),
        "impact": "Your choice locks how this concept is identified across future data updates.",
    },
    "step6_hold": {
        "title": "Confirm business key choice for {entity_label}",
        "summary": (
            "Automated checks found two ways to track {entity_label}. Choose whether to lock "
            "in a single shared identifier or keep it flexible per source."
        ),
        "why_it_matters": (
            "Choosing how records are matched prevents accidental merges or split histories "
            "in future waves."
        ),
    },
    "step6_v6b_waiver": {
        "title": "Review design warning for {entity_label}",
        "summary": (
            "A modeling quality check flagged {entity_label}. You can ask the assistant to "
            "redesign it automatically or record a business waiver explaining why the current "
            "layout is intentional."
        ),
        "why_it_matters": (
            "Resolving or documenting design warnings keeps the data warehouse maintainable "
            "as new sources arrive."
        ),
    },
    "step6_v8_horizon": {
        "title": "Future-readiness review for {entity_label}",
        "summary": (
            "A forward-looking resilience check highlighted a potential schema change impact on "
            "{entity_label}. Accept the current layout or guide a safer design before certifying."
        ),
        "why_it_matters": (
            "Hardening this choice now avoids disruptive changes when new feeds "
            "attach in later waves."
        ),
    },
    "step6_readiness": {
        "CERTIFIED": (
            "All structural and runtime verification checks passed for Wave {wave_number}. "
            "Your data warehouse blueprint is ready for target build."
        ),
        "CERTIFIED_FAST": (
            "Core structural and runtime checks passed for Wave {wave_number} under Quick policy. "
            "Run Horizon Review anytime for full future-wave sign-off."
        ),
        "UNCERTIFIED": (
            "Wave {wave_number} is paused with a draft blueprint because verification checks or "
            "budget limits prevented full certification."
        ),
    },
}


def contains_forbidden_jargon(text: str) -> bool:
    """Return True if text matches FORBIDDEN_GUIDED_JARGON."""
    return bool(FORBIDDEN_GUIDED_JARGON.search(text))


def filter_jargon_free_rows(rows: Sequence[str], *, limit: int | None = None) -> list[str]:
    """Return non-empty sample rows free of FORBIDDEN_GUIDED_JARGON."""
    clean = [r for r in rows if r.strip() and not contains_forbidden_jargon(r)]
    return clean[:limit] if limit is not None else clean


def safe_entity_label(raw_name: str, fallback: str = "this table") -> str:
    """Convert internal hub/link/sat names into clean title-cased business labels."""
    cleaned = raw_name.strip()
    for prefix in ("hub_", "t_lnk_", "lnk_", "sal_", "mas_", "esat_", "sat_"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    label = cleaned.replace("_", " ").strip()
    if not label or contains_forbidden_jargon(label):
        return fallback
    return label.title()


def sanitize_or_fallback(
    text: str,
    template_key: TemplateKey,
    *,
    field: str = "summary",
    entity_label: str = "this table",
    wave_number: int = 1,
) -> str:
    """Return stripped text when jargon-free, otherwise render plain-English template."""
    stripped = text.strip()
    if stripped and not contains_forbidden_jargon(stripped):
        return stripped
    safe_label = (
        entity_label.strip()
        if entity_label.strip() and not contains_forbidden_jargon(entity_label)
        else "this table"
    )
    template = FALLBACK_TEMPLATES[template_key].get(field)
    if template is None:
        template = next(iter(FALLBACK_TEMPLATES[template_key].values()))
    return template.format(entity_label=safe_label, wave_number=wave_number)


class GuidedErrorTranslation(StrictModel):
    """Plain-language Guided UX translation for a single closed V2 ErrorCode."""

    error_code: ErrorCode
    category: ErrorCategory
    http_status: int | None = None
    exit_code: int
    waivable: bool = False
    rationale_required: bool = False
    plain_title: str
    plain_summary: str
    next_step: str
    can_retry: bool


_RAW_ERROR_COPY: dict[ErrorCode, tuple[str, str, str, bool]] = {
    ErrorCode.OK: (
        "Completed Successfully",
        "Your request completed and passed all required checks.",
        "Continue to the next step.",
        False,
    ),
    ErrorCode.AGENT_EXECUTION_FAILED: (
        "AI Assistant Run Interrupted",
        "The configured AI assistant stopped unexpectedly, timed out, or returned "
        "an unreadable response.",
        "Inspect the run log in Run Explorer and retry or switch providers in Settings.",
        True,
    ),
    ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR: (
        "External Tool Connection Failed",
        "An external tool server timed out or returned an error during document or "
        "metadata lookup.",
        "Check the external tool server configuration in Settings and retry.",
        True,
    ),
    ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE: (
        "Reference Ontology File Corrupted",
        "The built-in financial reference pack failed its integrity check and cannot be trusted.",
        "Reinstall or restore the application package before continuing.",
        False,
    ),
    ErrorCode.INTERNAL_SERVER_ERROR: (
        "Unexpected Service Error",
        "An internal file or runtime error prevented the request from finishing.",
        "Check Run Explorer logs and retry your action.",
        True,
    ),
    ErrorCode.SYNTAX_INVALID_ARGUMENT: (
        "Invalid Input Provided",
        "One or more fields, query expressions, or requested changes did not match "
        "the expected format.",
        "Review the highlighted input values and try again.",
        False,
    ),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: (
        "Unsupported Request Format",
        "The request body was not sent in the expected structured data format.",
        "Send structured requests as JSON or use the file upload form.",
        False,
    ),
    ErrorCode.UNSAFE_WAL_FILESYSTEM: (
        "Unsupported Network Storage Location",
        "The local state folder points to a network or remote drive that cannot safely "
        "lock the project database.",
        "Move your application state folder to a local hard drive or solid-state drive.",
        False,
    ),
    ErrorCode.DATABRICKS_SSRF_BLOCKED: (
        "Blocked Private Network Address",
        "The remote warehouse or tool web address resolved to a restricted private or "
        "internal network range.",
        "Enter an approved public or enterprise hostname and test the connection again.",
        False,
    ),
    ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE: (
        "Workspace Agent Instructions Need Approval",
        "This folder contains custom AI instructions that have not been approved yet.",
        "Approve the workspace instructions in Settings if you trust this project folder.",
        True,
    ),
    ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN: (
        "Browser Origin Not Allowed",
        "The request came from an untrusted web address rather than the local workbench.",
        "Open the workbench directly from the local server address shown at startup.",
        False,
    ),
    ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED: (
        "External Document Access Not Enabled",
        "An assistant tried to send a document page to an external tool without your permission.",
        "Enable external document access for that tool in Settings if you want to allow it.",
        True,
    ),
    ErrorCode.DRIVER_MISSING: (
        "Required Connector or Tool Missing",
        "An optional database driver, document scanner, or assistant command-line tool "
        "is not installed.",
        "Install the missing extension or choose a different provider in Settings.",
        False,
    ),
    ErrorCode.NEEDS_LOCAL_SECRET_BINDING: (
        "Credential Key Needed",
        "A remote data source or external tool is missing its saved access token on this machine.",
        "Enter the missing token in Credentials Settings to reconnect without losing your setup.",
        True,
    ),
    ErrorCode.AGENT_NOT_CONFIGURED: (
        "AI Provider Not Configured",
        "No active AI assistant key or login session was found for this run.",
        "Configure your AI provider and credentials in Settings, then start the run again.",
        True,
    ),
    ErrorCode.UNREDACTED_SCAN_BLOCKED: (
        "Scanned Page Requires Approval",
        "A scanned document page cannot be viewed by the assistant until you approve it "
        "and privacy boxes are checked.",
        "Review and approve the scanned page in Step 3 Document Outlines.",
        True,
    ),
    ErrorCode.REDACTED_PLACEHOLDER_LEAK: (
        "Masked Privacy Token Found in Quote",
        "A submitted quote or payload echoed a privacy mask placeholder instead of real "
        "non-sensitive text.",
        "Remove the masked placeholder text and use an unmasked non-sensitive quote.",
        False,
    ),
    ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED: (
        "Unverified Concept Held in Quarantine",
        "A proposed business concept lacks supporting reference or source evidence and "
        "was held before commit.",
        "Attach supporting table or document evidence, or sign a waiver with at least "
        "20 characters.",
        True,
    ),
    ErrorCode.ONT_EVO_CONTRACT_VIOLATION: (
        "Protected Model Contract Violated",
        "The requested ontology edit would break a deployed business key or restricted "
        "editing boundary.",
        "Keep deployed business keys intact or use an additive change instead.",
        False,
    ),
    ErrorCode.VERIFICATION_GATE_FAILED: (
        "Quality Checks Must Pass First",
        "Target generation or final sign-off is blocked because required model checks "
        "did not pass.",
        "Resolve the listed issues or run Verify Vault in Step 6 before building targets.",
        True,
    ),
    ErrorCode.CITATION_SPAN_UNRESOLVED: (
        "Document Quote Not Found",
        "A cited quote could not be matched cleanly inside the normalized text of the "
        "linked document page.",
        "Verify the page number, quote wording, and occurrence count in Step 4.",
        False,
    ),
    ErrorCode.ARCHIVE_SECURITY_VIOLATION: (
        "Unsafe Project Archive Rejected",
        "The uploaded bundle contains unsafe file paths, links, or compression ratios and "
        "no files were extracted.",
        "Export a fresh project bundle from a trusted workspace and retry import.",
        False,
    ),
    ErrorCode.BUNDLE_HASH_MISMATCH: (
        "Project Archive Integrity Mismatch",
        "One or more files inside the project bundle did not match the manifest checksum.",
        "Re-export the project bundle and verify the file was not modified in transit.",
        True,
    ),
    ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED: (
        "File Exceeds 250 MB Limit",
        "The uploaded source file or project archive is larger than the 250 MB limit.",
        "Reduce the file size or export a lightweight project-state bundle without raw data files.",
        False,
    ),
    ErrorCode.RESOURCE_NOT_FOUND: (
        "Item Not Found",
        "The requested project, source, run, revision, or target could not be found.",
        "Return to the project list or refresh the page to load current items.",
        False,
    ),
    ErrorCode.PROJECT_JOB_ACTIVE: (
        "Another Run Is Already Active",
        "This project already has a discovery or build job running right now.",
        "Wait for the active run to finish or pause it in Run Explorer before starting a new one.",
        True,
    ),
    ErrorCode.SOURCE_COMMITTED_IN_VAULT: (
        "Source Locked by Certified Model",
        "This data source cannot be deleted or reshaped because a certified wave depends "
        "on its columns.",
        "Keep this source attached or re-attach a replacement file with matching locked columns.",
        False,
    ),
    ErrorCode.PROJECT_ID_CONFLICT: (
        "Project Name Already Exists",
        "A project with this identifier already exists in the workspace.",
        "Choose a different project identifier or select Rename or Replace during bundle import.",
        False,
    ),
    ErrorCode.REVISION_CONFLICT: (
        "Ontology Updated Elsewhere",
        "Someone else or a background step committed a newer ontology revision since you "
        "opened this draft.",
        "Review the latest server revision, validate your merged changes, and commit again.",
        True,
    ),
    ErrorCode.SOURCE_BINARY_ABSENT: (
        "Local Source File Not Attached",
        "This project was imported with metadata only, so raw data rows and document pages "
        "are not present locally.",
        "Re-attach the original data or document file in Step 2 to inspect rows or load "
        "local data.",
        True,
    ),
    ErrorCode.HOLD_CHECKPOINTED: (
        "Paused for Steward Review",
        "The run paused cleanly at a checkpoint and saved all progress so far.",
        "Answer any open questions or click Resume to continue from this checkpoint.",
        True,
    ),
    ErrorCode.UNCERTIFIED_DRAFT: (
        "Stopped With Uncertified Draft",
        "The run reached its turn, time, or repair limit before achieving full certification.",
        "Increase the run budget to continue refining, or accept the best draft if core "
        "checks pass.",
        True,
    ),
    ErrorCode.REMOTE_BIND_UNAUTHENTICATED: (
        "Remote Server Binding Blocked",
        "Starting the server on a non-local network address requires explicit acknowledgement.",
        "Bind to the local machine address or pass the explicit remote-bind flag when "
        "starting the server.",
        False,
    ),
    ErrorCode.EMITTER_VERIFICATION_FAILED: (
        "Target Output Verification Failed",
        "Generated database scripts or target artifacts failed safety or parity checks "
        "before writing to disk.",
        "Inspect the failing target report in Step 7 and compile unaffected targets individually.",
        True,
    ),
}


def _build_catalog() -> dict[ErrorCode, GuidedErrorTranslation]:
    catalog: dict[ErrorCode, GuidedErrorTranslation] = {}
    for code in ErrorCode:
        entry = get_error_entry(code)
        title, summary, next_step, can_retry = _RAW_ERROR_COPY[code]
        catalog[code] = GuidedErrorTranslation(
            error_code=code,
            category=entry.category,
            http_status=entry.http_status,
            exit_code=int(entry.exit_code),
            waivable=entry.waivable,
            rationale_required=entry.rationale_required,
            plain_title=title,
            plain_summary=summary,
            next_step=next_step,
            can_retry=can_retry,
        )
    return catalog


ERROR_UX_TRANSLATIONS: dict[ErrorCode, GuidedErrorTranslation] = _build_catalog()


def translate_error_code(code: ErrorCode, *, detail: str | None = None) -> GuidedErrorTranslation:
    """Return the plain-language Guided UX translation for a closed V2 ErrorCode."""
    base = ERROR_UX_TRANSLATIONS[code]
    if detail and detail.strip() and not contains_forbidden_jargon(detail):
        return base.model_copy(
            update={"plain_summary": f"{base.plain_summary} ({detail.strip()})"}
        )
    return base


def translate_problem_details(problem: ProblemDetails) -> GuidedErrorTranslation:
    """Translate an RFC 9457 ProblemDetails payload into plain-language Guided UX."""
    return ERROR_UX_TRANSLATIONS[problem.error_code]


__all__ = [
    "ERROR_UX_TRANSLATIONS",
    "FALLBACK_TEMPLATES",
    "FORBIDDEN_GUIDED_JARGON",
    "GUIDED_MILESTONES",
    "GuidedErrorTranslation",
    "TemplateKey",
    "contains_forbidden_jargon",
    "filter_jargon_free_rows",
    "safe_entity_label",
    "sanitize_or_fallback",
    "translate_error_code",
    "translate_problem_details",
]
