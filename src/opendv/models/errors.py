"""Closed 34-code ErrorCode catalog, CliExitCode, RFC 9457 ProblemDetails, and OpenDVError."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import Field

from opendv.models.diagnostics import EmitterDefectReport, StrictModel, VerifierDiagnostic

ErrorCategory = Literal["Syntax", "Preconditions", "Resolution", "Verification", "Execution"]


class CliExitCode(IntEnum):
    """Normative V2 CLI exit codes."""

    OK = 0
    EXECUTION_ERROR = 1
    VALIDATION_ERROR = 2
    NOT_FOUND = 3
    CONFLICT_OR_LOCKED = 4
    HOLD_OR_UNCERTIFIED = 5
    REMOTE_BIND_UNAUTHENTICATED = 40
    EMITTER_VERIFICATION_FAILED = 50


VALID_EXIT_CODES: frozenset[int] = frozenset(int(code) for code in CliExitCode)


class ErrorCode(StrEnum):
    """Closed 34-member V2 ErrorCode enumeration."""

    OK = "OK"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    EXTERNAL_MCP_GATEWAY_ERROR = "EXTERNAL_MCP_GATEWAY_ERROR"
    REFERENCE_PACK_INTEGRITY_FAILURE = "REFERENCE_PACK_INTEGRITY_FAILURE"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    SYNTAX_INVALID_ARGUMENT = "SYNTAX_INVALID_ARGUMENT"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    UNSAFE_WAL_FILESYSTEM = "UNSAFE_WAL_FILESYSTEM"
    DATABRICKS_SSRF_BLOCKED = "DATABRICKS_SSRF_BLOCKED"
    UNTRUSTED_WORKSPACE_OVERRIDE = "UNTRUSTED_WORKSPACE_OVERRIDE"
    LOOPBACK_ORIGIN_FORBIDDEN = "LOOPBACK_ORIGIN_FORBIDDEN"
    EXTERNAL_MCP_DOC_OPT_IN_REQUIRED = "EXTERNAL_MCP_DOC_OPT_IN_REQUIRED"
    DRIVER_MISSING = "DRIVER_MISSING"
    NEEDS_LOCAL_SECRET_BINDING = "NEEDS_LOCAL_SECRET_BINDING"
    AGENT_NOT_CONFIGURED = "AGENT_NOT_CONFIGURED"
    UNREDACTED_SCAN_BLOCKED = "UNREDACTED_SCAN_BLOCKED"
    REDACTED_PLACEHOLDER_LEAK = "REDACTED_PLACEHOLDER_LEAK"
    UNGROUNDED_CANDIDATE_QUARANTINED = "UNGROUNDED_CANDIDATE_QUARANTINED"
    ONT_EVO_CONTRACT_VIOLATION = "ONT_EVO_CONTRACT_VIOLATION"
    VERIFICATION_GATE_FAILED = "VERIFICATION_GATE_FAILED"
    CITATION_SPAN_UNRESOLVED = "CITATION_SPAN_UNRESOLVED"
    ARCHIVE_SECURITY_VIOLATION = "ARCHIVE_SECURITY_VIOLATION"
    BUNDLE_HASH_MISMATCH = "BUNDLE_HASH_MISMATCH"
    ARCHIVE_SIZE_LIMIT_EXCEEDED = "ARCHIVE_SIZE_LIMIT_EXCEEDED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    PROJECT_JOB_ACTIVE = "PROJECT_JOB_ACTIVE"
    SOURCE_COMMITTED_IN_VAULT = "SOURCE_COMMITTED_IN_VAULT"
    PROJECT_ID_CONFLICT = "PROJECT_ID_CONFLICT"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    SOURCE_BINARY_ABSENT = "SOURCE_BINARY_ABSENT"
    HOLD_CHECKPOINTED = "HOLD_CHECKPOINTED"
    UNCERTIFIED_DRAFT = "UNCERTIFIED_DRAFT"
    REMOTE_BIND_UNAUTHENTICATED = "REMOTE_BIND_UNAUTHENTICATED"
    EMITTER_VERIFICATION_FAILED = "EMITTER_VERIFICATION_FAILED"


class ErrorMappingEntry(StrictModel):
    """Authoritative metadata row for a single ErrorCode."""

    error_code: ErrorCode
    category: ErrorCategory
    http_status: int | None
    exit_code: CliExitCode
    problem_title: str
    problem_type_uri: str
    waivable: bool = False
    rationale_required: bool = False
    description: str


def _entry(
    code: ErrorCode,
    category: ErrorCategory,
    http_status: int | None,
    exit_code: CliExitCode,
    title: str,
    description: str,
    *,
    waivable: bool = False,
    rationale_required: bool = False,
) -> ErrorMappingEntry:
    uri = (
        "urn:opendv:ok"
        if code == ErrorCode.OK
        else f"urn:opendv:error:{code.lower().replace('_', '-')}"
    )
    return ErrorMappingEntry(
        error_code=code,
        category=category,
        http_status=http_status,
        exit_code=exit_code,
        problem_title=title,
        problem_type_uri=uri,
        waivable=waivable,
        rationale_required=rationale_required,
        description=description,
    )


_ENTRIES: tuple[ErrorMappingEntry, ...] = (
    _entry(
        ErrorCode.OK,
        "Execution",
        200,
        CliExitCode.OK,
        "OK",
        "Request or command passed all gates.",
    ),
    _entry(
        ErrorCode.AGENT_EXECUTION_FAILED,
        "Execution",
        502,
        CliExitCode.EXECUTION_ERROR,
        "Agent Execution Failed",
        "Host CLI or bare SDK subprocess crashed, timed out, or emitted invalid JSON.",
    ),
    _entry(
        ErrorCode.EXTERNAL_MCP_GATEWAY_ERROR,
        "Execution",
        502,
        CliExitCode.EXECUTION_ERROR,
        "External MCP Gateway Error",
        "ExternalMcpGateway subprocess crash, HTTP error, or timeout expiry.",
    ),
    _entry(
        ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE,
        "Execution",
        500,
        CliExitCode.EXECUTION_ERROR,
        "Reference Pack Integrity Failure",
        "Bundled fibo-core-pack.json failed baseline-digests.json SHA-256 verification.",
    ),
    _entry(
        ErrorCode.INTERNAL_SERVER_ERROR,
        "Execution",
        500,
        CliExitCode.EXECUTION_ERROR,
        "Internal Server Error",
        "Unhandled runtime I/O or subsystem failure inside OpenDVService or FastAPI.",
    ),
    _entry(
        ErrorCode.SYNTAX_INVALID_ARGUMENT,
        "Syntax",
        400,
        CliExitCode.VALIDATION_ERROR,
        "Invalid Argument Syntax",
        "Pydantic extra='forbid' mismatch, invalid regex/SQL query, or server-only delta op.",
    ),
    _entry(
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        "Syntax",
        415,
        CliExitCode.VALIDATION_ERROR,
        "Unsupported Media Type",
        "POST/PUT/PATCH with Content-Length > 0 omitted application/json outside multipart routes.",
    ),
    _entry(
        ErrorCode.UNSAFE_WAL_FILESYSTEM,
        "Preconditions",
        400,
        CliExitCode.VALIDATION_ERROR,
        "Unsafe WAL Filesystem",
        "OPENDV_HOME resolves onto NFS/CIFS/SMB/9p/WSL2 drvfs or Windows remote/UNC mount.",
    ),
    _entry(
        ErrorCode.DATABRICKS_SSRF_BLOCKED,
        "Preconditions",
        400,
        CliExitCode.VALIDATION_ERROR,
        "SSRF Address Blocked",
        "Databricks or HTTP external MCP host resolved to a blocked SSRF IP range.",
    ),
    _entry(
        ErrorCode.UNTRUSTED_WORKSPACE_OVERRIDE,
        "Preconditions",
        403,
        CliExitCode.VALIDATION_ERROR,
        "Untrusted Workspace Override",
        "Workspace .opendv/agents override exists without recorded digest in trusted_workspaces.",
        waivable=True,
    ),
    _entry(
        ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        "Preconditions",
        403,
        CliExitCode.VALIDATION_ERROR,
        "Loopback Origin Forbidden",
        "Non-loopback Host, foreign Origin, or cross-site Sec-Fetch-Site rejected.",
    ),
    _entry(
        ErrorCode.EXTERNAL_MCP_DOC_OPT_IN_REQUIRED,
        "Preconditions",
        403,
        CliExitCode.VALIDATION_ERROR,
        "External MCP Document Opt-In Required",
        "External MCP call attempted with doc_page handle or scout call without server opt-in.",
        waivable=True,
    ),
    _entry(
        ErrorCode.DRIVER_MISSING,
        "Preconditions",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Required Driver or CLI Missing",
        "Optional extra (databricks, ocr, sdk) or host CLI executable is missing.",
    ),
    _entry(
        ErrorCode.NEEDS_LOCAL_SECRET_BINDING,
        "Preconditions",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Local Secret Binding Required",
        "Remote connector or enabled external MCP server lacks a bound secret key.",
    ),
    _entry(
        ErrorCode.AGENT_NOT_CONFIGURED,
        "Preconditions",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Agent Harness Not Configured",
        "Active harness credential or CLI provider is unconfigured during Step 4/6 run.",
    ),
    _entry(
        ErrorCode.UNREDACTED_SCAN_BLOCKED,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Unredacted Scan Page Blocked",
        "Scanned document page lacks approved_scan_pages consent or OCR bounding boxes.",
        waivable=True,
    ),
    _entry(
        ErrorCode.REDACTED_PLACEHOLDER_LEAK,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Redacted Placeholder Leak Detected",
        "Subagent quote or payload contains <MASKED_PII> or [REDACTED_*] placeholder.",
    ),
    _entry(
        ErrorCode.UNGROUNDED_CANDIDATE_QUARANTINED,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Ungrounded Candidate Quarantined",
        "Step 5 commit attempted with Path B2 quarantined concept lacking Steward waiver.",
        waivable=True,
        rationale_required=True,
    ),
    _entry(
        ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Ontology Evolution Contract Violation",
        "Human or agent OntologyDelta violated Graph A, evolution_locks, or _DOps boundary.",
    ),
    _entry(
        ErrorCode.VERIFICATION_GATE_FAILED,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Verification Gate Failed",
        "Step 7 compile blocked on uncertified IR, strict accept-best, or vault verify failure.",
    ),
    _entry(
        ErrorCode.CITATION_SPAN_UNRESOLVED,
        "Resolution",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Citation Span Unresolved",
        "Step 5 commit or V4 found citation occurrence quote unresolved in page text or OCR.",
    ),
    _entry(
        ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Archive Security Violation",
        "Archive contains symlink, traversal, ADS, reserved name, collision, or zip-bomb ratio.",
    ),
    _entry(
        ErrorCode.BUNDLE_HASH_MISMATCH,
        "Verification",
        422,
        CliExitCode.VALIDATION_ERROR,
        "Bundle Hash Mismatch",
        "Imported .opendv.zip manifest.json SHA-256 mismatch against entry bytes.",
    ),
    _entry(
        ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
        "Syntax",
        413,
        CliExitCode.VALIDATION_ERROR,
        "Archive Size Limit Exceeded",
        "Bundle export/import or source upload exceeded 250 MB (262,144,000 bytes).",
    ),
    _entry(
        ErrorCode.RESOURCE_NOT_FOUND,
        "Resolution",
        404,
        CliExitCode.NOT_FOUND,
        "Resource Not Found",
        "Requested project_id, source_id, run_id, target_id, or revision_id does not exist.",
    ),
    _entry(
        ErrorCode.PROJECT_JOB_ACTIVE,
        "Resolution",
        409,
        CliExitCode.CONFLICT_OR_LOCKED,
        "Project Job Active",
        "Leases table holds an unexpired live active_job_lease for this project.",
    ),
    _entry(
        ErrorCode.SOURCE_COMMITTED_IN_VAULT,
        "Resolution",
        409,
        CliExitCode.CONFLICT_OR_LOCKED,
        "Source Committed in Vault",
        "Source delete or lock-breaking reattach blocked because source binds evolution_locks.",
    ),
    _entry(
        ErrorCode.PROJECT_ID_CONFLICT,
        "Resolution",
        409,
        CliExitCode.CONFLICT_OR_LOCKED,
        "Project ID Conflict",
        "Project init or bundle import with conflict='fail' collided with existing project_id.",
    ),
    _entry(
        ErrorCode.REVISION_CONFLICT,
        "Resolution",
        409,
        CliExitCode.CONFLICT_OR_LOCKED,
        "Ontology Revision Conflict",
        "OntologyCommitReq.base_revision_id does not match current active_ontology_rev_id.",
    ),
    _entry(
        ErrorCode.SOURCE_BINARY_ABSENT,
        "Resolution",
        409,
        CliExitCode.CONFLICT_OR_LOCKED,
        "Source Binary Absent",
        "Raw page read, span re-check, or DuckDB load invoked on METADATA_ONLY_BINARY_ABSENT.",
    ),
    _entry(
        ErrorCode.HOLD_CHECKPOINTED,
        "Execution",
        202,
        CliExitCode.HOLD_OR_UNCERTIFIED,
        "Run Checkpointed at Hold",
        "Async run paused cleanly at a Steward Hold or user pause checkpoint.",
    ),
    _entry(
        ErrorCode.UNCERTIFIED_DRAFT,
        "Execution",
        202,
        CliExitCode.HOLD_OR_UNCERTIFIED,
        "Uncertified Draft Checkpointed",
        "Async run stopped at HarnessBudget or K_max ceiling with an uncertified draft.",
    ),
    _entry(
        ErrorCode.REMOTE_BIND_UNAUTHENTICATED,
        "Preconditions",
        None,
        CliExitCode.REMOTE_BIND_UNAUTHENTICATED,
        "Remote Bind Unauthenticated",
        "Non-loopback opendv serve host bound without --allow-remote-unauthenticated.",
    ),
    _entry(
        ErrorCode.EMITTER_VERIFICATION_FAILED,
        "Execution",
        500,
        CliExitCode.EMITTER_VERIFICATION_FAILED,
        "Emitter Verification Failed",
        "Step 7 TargetEvalGate (EVAL-AST/EVAL-RUN/EVAL-FMT) failed with EmitterDefectReport.",
    ),
)

ERROR_MAPPINGS: dict[ErrorCode, ErrorMappingEntry] = {
    entry.error_code: entry for entry in _ENTRIES
}


class ProblemDetails(StrictModel):
    """RFC 9457 error payload returned by HTTP endpoints and CLI envelopes."""

    type: str
    title: str
    status: int | None = None
    detail: str
    error_code: ErrorCode
    category: ErrorCategory
    waivable: bool
    rationale_required: bool
    instance: str | None = None
    run_id: str | None = None
    diagnostics: list[VerifierDiagnostic] = Field(default_factory=list)
    defect_report: EmitterDefectReport | None = None


class CliEnvelope(StrictModel):
    """Normalized JSON envelope emitted by `opendv --json`."""

    ok: bool
    exit_code: int
    error_code: ErrorCode | None = None
    problem: ProblemDetails | None = None
    data: dict[str, Any] | list[Any] | None = None


def get_error_entry(code: ErrorCode) -> ErrorMappingEntry:
    """Look up the authoritative mapping entry for an ErrorCode."""
    return ERROR_MAPPINGS[code]


def get_error_mapping(code: ErrorCode) -> tuple[int, int | None]:
    """Return `(exit_code, http_status)` for the given ErrorCode."""
    entry = ERROR_MAPPINGS[code]
    return (int(entry.exit_code), entry.http_status)


def create_problem_details(
    code: ErrorCode,
    detail: str,
    *,
    instance: str | None = None,
    run_id: str | None = None,
    diagnostics: list[VerifierDiagnostic] | None = None,
    defect_report: EmitterDefectReport | None = None,
) -> ProblemDetails:
    """Build an RFC 9457 ProblemDetails object from a closed ErrorCode."""
    entry = ERROR_MAPPINGS[code]
    return ProblemDetails(
        type=entry.problem_type_uri,
        title=entry.problem_title,
        status=entry.http_status,
        detail=detail,
        error_code=code,
        category=entry.category,
        waivable=entry.waivable,
        rationale_required=entry.rationale_required,
        instance=instance,
        run_id=run_id,
        diagnostics=list(diagnostics or []),
        defect_report=defect_report,
    )


class OpenDVError(Exception):
    """Base domain exception mapped deterministically to ErrorCode and CliExitCode."""

    error_code: ErrorCode = ErrorCode.INTERNAL_SERVER_ERROR
    exit_code: CliExitCode = CliExitCode.EXECUTION_ERROR
    http_status: int | None = 500

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        diagnostics: list[VerifierDiagnostic] | None = None,
        defect_report: EmitterDefectReport | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = message
        if error_code is not None:
            self.error_code = error_code
        entry = get_error_entry(self.error_code)
        self.exit_code = entry.exit_code
        self.http_status = entry.http_status
        self.diagnostics: list[VerifierDiagnostic] = list(diagnostics or [])
        self.defect_report = defect_report
        self.run_id = run_id

    def to_problem_details(self, *, instance: str | None = None) -> ProblemDetails:
        """Convert this exception into an RFC 9457 ProblemDetails model."""
        return create_problem_details(
            self.error_code,
            self.detail,
            instance=instance,
            run_id=self.run_id,
            diagnostics=self.diagnostics,
            defect_report=self.defect_report,
        )


class ReferenceOntologyCorruptError(OpenDVError):
    """Raised when bundled FIBO reference pack digest verification fails."""

    error_code = ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE


def export_error_codes_catalog() -> dict[str, Any]:
    """Export the deterministic V2 error-codes.json catalog dictionary."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "catalog_version": "2.0.0",
        "title": "Open-DV V2 Closed Error Code Catalog",
        "total_error_codes": len(ERROR_MAPPINGS),
        "error_codes": [
            {
                "code": str(entry.error_code),
                "category": entry.category,
                "http_status": entry.http_status,
                "exit_code": int(entry.exit_code),
                "problem_title": entry.problem_title,
                "problem_type_uri": entry.problem_type_uri,
                "waivable": entry.waivable,
                "rationale_required": entry.rationale_required,
                "description": entry.description,
            }
            for entry in ERROR_MAPPINGS.values()
        ],
    }


__all__ = [
    "ERROR_MAPPINGS",
    "VALID_EXIT_CODES",
    "CliEnvelope",
    "CliExitCode",
    "ErrorCategory",
    "ErrorCode",
    "ErrorMappingEntry",
    "OpenDVError",
    "ProblemDetails",
    "ReferenceOntologyCorruptError",
    "create_problem_details",
    "export_error_codes_catalog",
    "get_error_entry",
    "get_error_mapping",
]
