from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from opendv_contracts.dtos.common import BaseContractModel


class ErrorCode(StrEnum):
    """Canonical error classification across CLI exit codes and HTTP status codes.

    Normative Exit Code Taxonomy (conforming to schemas/v1/error-codes.json):
    - Exit 0: Success (HTTP 200, 201, 202)
    - Exit 1: Capability & Subsystem Failure / Missing Rationale / Verification Rejection
      (HTTP 200, 400, 500, 503)
    - Exit 2: Command Syntax, Precondition, Payload, or Legacy Subprocess Failure
      (HTTP 400, 413, 415, 500, 503)
    - Exit 3: Resource Not Found (HTTP 404)
    - Exit 4: Domain, Referential Integrity Violation & ONT-EVO Governance (HTTP 422)
    - Exit 5: Optimistic Concurrency Control (OCC) Precondition Failure (HTTP 412)
    - Exit 6: Lock & In-Progress State Conflict (HTTP 409)
    - Exit 7: Authentication & RBAC Authorization Violation (HTTP 401, 403)

    Historical Parity Note:
    Legacy Wave 1 error codes (e.g. API_UNREACHABLE, PROVIDER_UNAVAILABLE) retain normative (2, 503)
    mappings per frozen contract baseline in schemas/cli-spec.json, while forward Studio
    capabilities (e.g. AGENT_UNAVAILABLE) map to (1, 503).
    """

    # 0 Exit / 200 HTTP
    OK = "OK"

    # 1 Exit / 200 HTTP (Domain verification result)
    VERIFICATION_FAILED = "VERIFICATION_FAILED"

    # 2 Exit / 400 Bad Request
    PATH_CONTAINMENT_ERROR = "PATH_CONTAINMENT_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"

    # 2 Exit / 404 Not Found
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    HOLD_NOT_FOUND = "HOLD_NOT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"

    # 2 Exit / 413 Payload Too Large
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

    # 2 Exit / 415 Unsupported Media Type
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"

    # 2 Exit / 422 Unprocessable Entity
    INPUT_VALIDATION_ERROR = "INPUT_VALIDATION_ERROR"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    SOURCE_LOAD_ERROR = "SOURCE_LOAD_ERROR"
    SANDBOX_EXECUTION_ERROR = "SANDBOX_EXECUTION_ERROR"
    PROVIDER_CONFIG_ERROR = "PROVIDER_CONFIG_ERROR"
    DETERMINISTIC_PROVIDER_REJECTED = "DETERMINISTIC_PROVIDER_REJECTED"
    CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"
    ONTOLOGY_RECOMMENDER_UNAVAILABLE = "ONTOLOGY_RECOMMENDER_UNAVAILABLE"

    # 2 Exit / 500 Internal Error
    CAS_INTEGRITY_FAILURE = "CAS_INTEGRITY_FAILURE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # 2 Exit / 503 Service Unavailable
    API_UNREACHABLE = "API_UNREACHABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    # Studio & Extended Error Taxonomy Codes (Phase 6 Track 6E)
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    CONFLICTING_WORKSPACE_SEED_PARAMS = "CONFLICTING_WORKSPACE_SEED_PARAMS"
    CORRUPT_SESSION_RECORD = "CORRUPT_SESSION_RECORD"
    EXPORT_RATIONALE_REQUIRED = "EXPORT_RATIONALE_REQUIRED"
    HISTORICAL_CONTRACT_RATIONALE_REQUIRED = "HISTORICAL_CONTRACT_RATIONALE_REQUIRED"
    IMPORT_GROUNDING_RATIONALE_REQUIRED = "IMPORT_GROUNDING_RATIONALE_REQUIRED"
    INSECURE_STUDIO_ROOT_PERMISSIONS = "INSECURE_STUDIO_ROOT_PERMISSIONS"
    JOB_ABANDONED_SERVER_CRASH = "JOB_ABANDONED_SERVER_CRASH"
    MATCHER_UNIDATA_VERSION_MISMATCH = "MATCHER_UNIDATA_VERSION_MISMATCH"
    NO_OP_DELTA = "NO_OP_DELTA"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    OFFICE_CONVERSION_UNAVAILABLE = "OFFICE_CONVERSION_UNAVAILABLE"
    PROMOTION_RATIONALE_REQUIRED = "PROMOTION_RATIONALE_REQUIRED"
    REFERENCE_PACK_INTEGRITY_FAILURE = "REFERENCE_PACK_INTEGRITY_FAILURE"
    VERSION_COMMIT_IO_FAILURE = "VERSION_COMMIT_IO_FAILURE"
    INVALID_EVIDENCE_OFFSETS = "INVALID_EVIDENCE_OFFSETS"
    INVALID_PRECONDITION_HEADER = "INVALID_PRECONDITION_HEADER"
    INVALID_SESSION_IDENTIFIER = "INVALID_SESSION_IDENTIFIER"
    INVALID_WORKSPACE_PATH = "INVALID_WORKSPACE_PATH"
    MISSING_AUTO_COMMIT_PRECONDITION = "MISSING_AUTO_COMMIT_PRECONDITION"
    MISSING_PRECONDITION_HEADER = "MISSING_PRECONDITION_HEADER"
    PRECONDITION_BASE_SHA_MISMATCH = "PRECONDITION_BASE_SHA_MISMATCH"
    REMOTE_SERVER_PATH_FORBIDDEN = "REMOTE_SERVER_PATH_FORBIDDEN"
    SYNTAX_INVALID_ARGUMENT = "SYNTAX_INVALID_ARGUMENT"
    VALIDATION_AMBIGUOUS_TARGET = "VALIDATION_AMBIGUOUS_TARGET"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    PROPOSAL_NOT_FOUND = "PROPOSAL_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
    AGENT_UNRESOLVED_PSEUDONYM = "AGENT_UNRESOLVED_PSEUDONYM"
    AMBIGUOUS_EXTERNAL_MAPPING = "AMBIGUOUS_EXTERNAL_MAPPING"
    ARCHIVE_SECURITY_VIOLATION = "ARCHIVE_SECURITY_VIOLATION"
    ATTRIBUTE_NOT_FOUND = "ATTRIBUTE_NOT_FOUND"
    AUTOMATED_ATTRIBUTION_INVALID = "AUTOMATED_ATTRIBUTION_INVALID"
    CANNOT_DELETE_PROMOTED_SESSION = "CANNOT_DELETE_PROMOTED_SESSION"
    CANNOT_PRUNE_PROMOTED_SESSION = "CANNOT_PRUNE_PROMOTED_SESSION"
    CONCEPT_STILL_REFERENCED = "CONCEPT_STILL_REFERENCED"
    CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED = "CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED"
    DANGLING_ENUM_CODE_DESCRIPTION = "DANGLING_ENUM_CODE_DESCRIPTION"
    DESCRIPTION_ALREADY_EXISTS = "DESCRIPTION_ALREADY_EXISTS"
    DUPLICATE_ATTRIBUTE_NAME = "DUPLICATE_ATTRIBUTE_NAME"
    DUPLICATE_ENUM_CODE = "DUPLICATE_ENUM_CODE"
    DUPLICATE_RELATIONSHIP_NAME = "DUPLICATE_RELATIONSHIP_NAME"
    DUPLICATE_RELATIONSHIP_URI = "DUPLICATE_RELATIONSHIP_URI"
    DUPLICATE_SYNONYM = "DUPLICATE_SYNONYM"
    ENUM_CODE_NOT_FOUND = "ENUM_CODE_NOT_FOUND"
    EVIDENCE_OFFSET_OUT_OF_RANGE = "EVIDENCE_OFFSET_OUT_OF_RANGE"
    GENESIS_REQUIRES_CONCEPT = "GENESIS_REQUIRES_CONCEPT"
    HIERARCHICAL_CYCLE_DETECTED = "HIERARCHICAL_CYCLE_DETECTED"
    HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED = "HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED"
    HUB_BIJECTION_VIOLATION = "HUB_BIJECTION_VIOLATION"
    IMPORT_ADMIN_OP_FORBIDDEN = "IMPORT_ADMIN_OP_FORBIDDEN"
    IMPORT_AGENT_DESTRUCTIVE_OP_FORBIDDEN = "IMPORT_AGENT_DESTRUCTIVE_OP_FORBIDDEN"
    IMPORT_AUTOMATED_ATTRIBUTION_INVALID = "IMPORT_AUTOMATED_ATTRIBUTION_INVALID"
    IMPORT_CONFIDENCE_MISMATCH = "IMPORT_CONFIDENCE_MISMATCH"
    IMPORT_DELTA_REPLAY_HASH_MISMATCH = "IMPORT_DELTA_REPLAY_HASH_MISMATCH"
    IMPORT_ENVELOPE_VIOLATION = "IMPORT_ENVELOPE_VIOLATION"
    IMPORT_GROUNDING_VIOLATION = "IMPORT_GROUNDING_VIOLATION"
    IMPORT_LINEAGE_BROKEN = "IMPORT_LINEAGE_BROKEN"
    IMPORT_MATCHER_VERSION_MISMATCH = "IMPORT_MATCHER_VERSION_MISMATCH"
    IMPORT_PROVISIONAL_URI_FORBIDDEN = "IMPORT_PROVISIONAL_URI_FORBIDDEN"
    IMPORT_SELF_ACCEPTANCE_UNACKNOWLEDGED = "IMPORT_SELF_ACCEPTANCE_UNACKNOWLEDGED"
    IMPORT_SEMVER_BUMP_MISMATCH = "IMPORT_SEMVER_BUMP_MISMATCH"
    IMPORT_UNKNOWN_REFERENCE_PACK = "IMPORT_UNKNOWN_REFERENCE_PACK"
    INVALID_EVIDENCE_GROUNDING = "INVALID_EVIDENCE_GROUNDING"
    INVALID_GROUNDING_WAIVER = "INVALID_GROUNDING_WAIVER"
    INVALID_PHYSICAL_TABLE_NAME = "INVALID_PHYSICAL_TABLE_NAME"
    INVALID_PROPOSAL_STATE = "INVALID_PROPOSAL_STATE"
    INVALID_WORKSPACE_ONTOLOGY = "INVALID_WORKSPACE_ONTOLOGY"
    MAPPING_NOT_FOUND = "MAPPING_NOT_FOUND"
    ONTOLOGY_REQUIRES_CONCEPT = "ONTOLOGY_REQUIRES_CONCEPT"
    ONT_EVO_CONTRACT_VIOLATION = "ONT_EVO_CONTRACT_VIOLATION"
    ONT_EVO_ENVELOPE_VIOLATION = "ONT_EVO_ENVELOPE_VIOLATION"
    ONT_EVO_FORGED_PROVENANCE = "ONT_EVO_FORGED_PROVENANCE"
    ONT_EVO_GROUNDING_VIOLATION = "ONT_EVO_GROUNDING_VIOLATION"
    ONT_EVO_LOW_CONFIDENCE_GROUNDING = "ONT_EVO_LOW_CONFIDENCE_GROUNDING"
    ONT_EVO_PSEUDONYM_LEAK = "ONT_EVO_PSEUDONYM_LEAK"
    ONT_EVO_UNGROUNDED_CONCEPT = "ONT_EVO_UNGROUNDED_CONCEPT"
    PARENT_ALREADY_EXISTS = "PARENT_ALREADY_EXISTS"
    PARENT_NOT_FOUND = "PARENT_NOT_FOUND"
    PHYSICAL_TABLE_NAME_COLLISION = "PHYSICAL_TABLE_NAME_COLLISION"
    REDACTED_PLACEHOLDER_LEAK = "REDACTED_PLACEHOLDER_LEAK"
    REFERENCED_CONCEPT_NOT_FOUND = "REFERENCED_CONCEPT_NOT_FOUND"
    RELATIONSHIP_NOT_FOUND = "RELATIONSHIP_NOT_FOUND"
    SOURCE_REFERENCED_BY_COMMITTED_VERSION = "SOURCE_REFERENCED_BY_COMMITTED_VERSION"
    SOURCE_REFERENCED_BY_PENDING_PROPOSAL = "SOURCE_REFERENCED_BY_PENDING_PROPOSAL"
    UNDEFINED_PHYSICAL_TABLE_MAPPING = "UNDEFINED_PHYSICAL_TABLE_MAPPING"
    UNKNOWN_REFERENCE_PACK = "UNKNOWN_REFERENCE_PACK"
    VERSION_CONSISTENCY_MISMATCH = "VERSION_CONSISTENCY_MISMATCH"
    LINEAGE_ROOT_MISMATCH = "LINEAGE_ROOT_MISMATCH"
    ONT_EVO_CONCURRENT_MODIFICATION = "ONT_EVO_CONCURRENT_MODIFICATION"
    JOB_COMMIT_IN_PROGRESS = "JOB_COMMIT_IN_PROGRESS"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"
    PRINCIPAL_ALREADY_EXISTS = "PRINCIPAL_ALREADY_EXISTS"
    PRINCIPAL_RESERVED = "PRINCIPAL_RESERVED"
    PROPOSAL_ALREADY_RESOLVED = "PROPOSAL_ALREADY_RESOLVED"
    STUDIO_ALREADY_INITIALIZED = "STUDIO_ALREADY_INITIALIZED"
    AGENT_ADMIN_OP_FORBIDDEN = "AGENT_ADMIN_OP_FORBIDDEN"
    AGENT_DESTRUCTIVE_OP_FORBIDDEN = "AGENT_DESTRUCTIVE_OP_FORBIDDEN"
    AGENT_METADATA_OVERWRITE_FORBIDDEN = "AGENT_METADATA_OVERWRITE_FORBIDDEN"
    AGENT_OPERATION_FORBIDDEN = "AGENT_OPERATION_FORBIDDEN"
    AGENT_PROPOSAL_ACCEPT_FORBIDDEN = "AGENT_PROPOSAL_ACCEPT_FORBIDDEN"
    CSRF_ORIGIN_MISMATCH = "CSRF_ORIGIN_MISMATCH"
    FORBIDDEN_ADMIN_REQUIRED = "FORBIDDEN_ADMIN_REQUIRED"
    LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE = "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"
    PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN = "PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN"
    SERVER_OPERATION_FORBIDDEN = "SERVER_OPERATION_FORBIDDEN"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    SESSION_ALREADY_EXISTS = "SESSION_ALREADY_EXISTS"
    CORRUPT_SOURCE_BLOB = "CORRUPT_SOURCE_BLOB"
    LAUNCH_NONCE_EXPIRED = "LAUNCH_NONCE_EXPIRED"
    AUTHENTICATION_CONFIG_ERROR = "AUTHENTICATION_CONFIG_ERROR"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    JOB_ALREADY_TERMINAL = "JOB_ALREADY_TERMINAL"
    STATIC_TOKEN_CONFIGURED = "STATIC_TOKEN_CONFIGURED"


class ErrorDetail(BaseContractModel):
    """Specific field or parameter validation error."""

    field: str = Field(..., description="Field path or parameter name")
    message: str = Field(..., description="Validation failure explanation")
    error_type: str = Field(default="value_error", description="Validator code or type")


class ProblemDetail(BaseContractModel):
    """RFC 9457 compliant error document (application/problem+json)."""

    type: str = Field(default="about:blank", description="URI identifying problem category")
    title: str = Field(..., description="Short, human-readable summary of problem type")
    status: int = Field(..., ge=200, le=599, description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation specific to this occurrence")
    instance: str | None = Field(
        default=None, description="URI reference identifying specific occurrence"
    )
    error_code: ErrorCode = Field(..., description="Machine-readable OpenDV ErrorCode")
    invalid_params: list[ErrorDetail] = Field(
        default_factory=list, description="Granular parameter failures"
    )
    extensions: dict[str, Any] = Field(
        default_factory=dict, description="Supplementary debugging context"
    )


class ErrorMappingEntry(BaseContractModel):
    """Entry describing the normative mapping from ErrorCode to exit code and HTTP status."""

    error_code: ErrorCode
    exit_code: int
    http_status: int
    problem_title: str
    problem_type_uri: str


ERROR_MAPPINGS: dict[ErrorCode, ErrorMappingEntry] = {
    ErrorCode.OK: ErrorMappingEntry(
        error_code=ErrorCode.OK,
        exit_code=0,
        http_status=200,
        problem_title="OK",
        problem_type_uri="urn:opendv:ok",
    ),
    ErrorCode.VERIFICATION_FAILED: ErrorMappingEntry(
        error_code=ErrorCode.VERIFICATION_FAILED,
        exit_code=1,
        http_status=200,
        problem_title="Verification Rejection",
        problem_type_uri="urn:opendv:verification-failed",
    ),
    ErrorCode.PATH_CONTAINMENT_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.PATH_CONTAINMENT_ERROR,
        exit_code=2,
        http_status=400,
        problem_title="Path Containment Violation",
        problem_type_uri="urn:opendv:path-containment-error",
    ),
    ErrorCode.INVALID_REQUEST: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_REQUEST,
        exit_code=2,
        http_status=400,
        problem_title="Bad Request",
        problem_type_uri="urn:opendv:invalid-request",
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.RESOURCE_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Resource Not Found",
        problem_type_uri="urn:opendv:not-found",
    ),
    ErrorCode.RUN_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.RUN_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Run Not Found",
        problem_type_uri="urn:opendv:run-not-found",
    ),
    ErrorCode.PROJECT_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.PROJECT_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Project Not Found",
        problem_type_uri="urn:opendv:project-not-found",
    ),
    ErrorCode.HOLD_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.HOLD_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Hold Not Found",
        problem_type_uri="urn:opendv:hold-not-found",
    ),
    ErrorCode.ARTIFACT_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.ARTIFACT_NOT_FOUND,
        exit_code=2,
        http_status=404,
        problem_title="Artifact Not Found",
        problem_type_uri="urn:opendv:artifact-not-found",
    ),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorMappingEntry(
        error_code=ErrorCode.PAYLOAD_TOO_LARGE,
        exit_code=2,
        http_status=413,
        problem_title="Payload Too Large",
        problem_type_uri="urn:opendv:payload-too-large",
    ),
    ErrorCode.UNSUPPORTED_FORMAT: ErrorMappingEntry(
        error_code=ErrorCode.UNSUPPORTED_FORMAT,
        exit_code=2,
        http_status=415,
        problem_title="Unsupported Format",
        problem_type_uri="urn:opendv:unsupported-format",
    ),
    ErrorCode.INPUT_VALIDATION_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.INPUT_VALIDATION_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Input Validation Error",
        problem_type_uri="urn:opendv:input-validation-error",
    ),
    ErrorCode.SCHEMA_VALIDATION_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.SCHEMA_VALIDATION_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Schema Validation Error",
        problem_type_uri="urn:opendv:schema-validation-error",
    ),
    ErrorCode.SOURCE_LOAD_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.SOURCE_LOAD_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Source Ingestion Error",
        problem_type_uri="urn:opendv:source-load-error",
    ),
    ErrorCode.SANDBOX_EXECUTION_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.SANDBOX_EXECUTION_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Sandbox Execution Failure",
        problem_type_uri="urn:opendv:sandbox-error",
    ),
    ErrorCode.PROVIDER_CONFIG_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.PROVIDER_CONFIG_ERROR,
        exit_code=2,
        http_status=422,
        problem_title="Provider Configuration Error",
        problem_type_uri="urn:opendv:provider-config-error",
    ),
    ErrorCode.DETERMINISTIC_PROVIDER_REJECTED: ErrorMappingEntry(
        error_code=ErrorCode.DETERMINISTIC_PROVIDER_REJECTED,
        exit_code=2,
        http_status=422,
        problem_title="Deterministic Provider Prohibited",
        problem_type_uri="urn:opendv:deterministic-rejected",
    ),
    ErrorCode.CONTRACT_VERSION_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.CONTRACT_VERSION_MISMATCH,
        exit_code=2,
        http_status=422,
        problem_title="Contract Version Mismatch",
        problem_type_uri="urn:opendv:version-mismatch",
    ),
    ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE,
        exit_code=2,
        http_status=422,
        problem_title="Ontology Recommender Unavailable",
        problem_type_uri="urn:opendv:ontology-recommender-unavailable",
    ),
    ErrorCode.CAS_INTEGRITY_FAILURE: ErrorMappingEntry(
        error_code=ErrorCode.CAS_INTEGRITY_FAILURE,
        exit_code=2,
        http_status=500,
        problem_title="CAS Digest Integrity Failure",
        problem_type_uri="urn:opendv:cas-integrity-failure",
    ),
    ErrorCode.INTERNAL_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.INTERNAL_ERROR,
        exit_code=2,
        http_status=500,
        problem_title="Internal Server Error",
        problem_type_uri="urn:opendv:internal-error",
    ),
    ErrorCode.API_UNREACHABLE: ErrorMappingEntry(
        error_code=ErrorCode.API_UNREACHABLE,
        exit_code=2,
        http_status=503,
        problem_title="API Service Unreachable",
        problem_type_uri="urn:opendv:api-unreachable",
    ),
    ErrorCode.PROVIDER_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.PROVIDER_UNAVAILABLE,
        exit_code=2,
        http_status=503,
        problem_title="Harness Provider Unavailable",
        problem_type_uri="urn:opendv:provider-unavailable",
    ),
    # Studio & Extended Error Taxonomy Entries (Phase 6 Track 6E)
    ErrorCode.AGENT_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_UNAVAILABLE,
        exit_code=1,
        http_status=503,
        problem_title="LLM Agent Service Unavailable",
        problem_type_uri="urn:opendv:error:agent-unavailable",
    ),
    ErrorCode.CONFLICTING_WORKSPACE_SEED_PARAMS: ErrorMappingEntry(
        error_code=ErrorCode.CONFLICTING_WORKSPACE_SEED_PARAMS,
        exit_code=1,
        http_status=400,
        problem_title="Conflicting Workspace Seed Parameters",
        problem_type_uri="urn:opendv:error:conflicting-workspace-seed-params",
    ),
    ErrorCode.CORRUPT_SESSION_RECORD: ErrorMappingEntry(
        error_code=ErrorCode.CORRUPT_SESSION_RECORD,
        exit_code=1,
        http_status=500,
        problem_title="Corrupt Session Record",
        problem_type_uri="urn:opendv:error:corrupt-session-record",
    ),
    ErrorCode.EXPORT_RATIONALE_REQUIRED: ErrorMappingEntry(
        error_code=ErrorCode.EXPORT_RATIONALE_REQUIRED,
        exit_code=1,
        http_status=400,
        problem_title="Export Rationale Required",
        problem_type_uri="urn:opendv:error:export-rationale-required",
    ),
    ErrorCode.HISTORICAL_CONTRACT_RATIONALE_REQUIRED: ErrorMappingEntry(
        error_code=ErrorCode.HISTORICAL_CONTRACT_RATIONALE_REQUIRED,
        exit_code=1,
        http_status=400,
        problem_title="Historical Contract Rationale Required",
        problem_type_uri="urn:opendv:error:historical-contract-rationale-required",
    ),
    ErrorCode.IMPORT_GROUNDING_RATIONALE_REQUIRED: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_GROUNDING_RATIONALE_REQUIRED,
        exit_code=1,
        http_status=400,
        problem_title="Import Grounding Rationale Required",
        problem_type_uri="urn:opendv:error:import-grounding-rationale-required",
    ),
    ErrorCode.INSECURE_STUDIO_ROOT_PERMISSIONS: ErrorMappingEntry(
        error_code=ErrorCode.INSECURE_STUDIO_ROOT_PERMISSIONS,
        exit_code=1,
        http_status=500,
        problem_title="Insecure Studio Root Permissions",
        problem_type_uri="urn:opendv:error:insecure-studio-root-permissions",
    ),
    ErrorCode.JOB_ABANDONED_SERVER_CRASH: ErrorMappingEntry(
        error_code=ErrorCode.JOB_ABANDONED_SERVER_CRASH,
        exit_code=1,
        http_status=500,
        problem_title="Background Job Abandoned Due to Server Crash",
        problem_type_uri="urn:opendv:error:job-abandoned-server-crash",
    ),
    ErrorCode.MATCHER_UNIDATA_VERSION_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.MATCHER_UNIDATA_VERSION_MISMATCH,
        exit_code=1,
        http_status=500,
        problem_title="Matcher Unicode Character Tables Version Mismatch",
        problem_type_uri="urn:opendv:error:matcher-unidata-version-mismatch",
    ),
    ErrorCode.NO_OP_DELTA: ErrorMappingEntry(
        error_code=ErrorCode.NO_OP_DELTA,
        exit_code=1,
        http_status=400,
        problem_title="No-Op Delta",
        problem_type_uri="urn:opendv:error:no-op-delta",
    ),
    ErrorCode.OCR_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.OCR_UNAVAILABLE,
        exit_code=1,
        http_status=503,
        problem_title="OCR Engine Unavailable",
        problem_type_uri="urn:opendv:error:ocr-unavailable",
    ),
    ErrorCode.OFFICE_CONVERSION_UNAVAILABLE: ErrorMappingEntry(
        error_code=ErrorCode.OFFICE_CONVERSION_UNAVAILABLE,
        exit_code=1,
        http_status=503,
        problem_title="Office Document Conversion Unavailable",
        problem_type_uri="urn:opendv:error:office-conversion-unavailable",
    ),
    ErrorCode.PROMOTION_RATIONALE_REQUIRED: ErrorMappingEntry(
        error_code=ErrorCode.PROMOTION_RATIONALE_REQUIRED,
        exit_code=1,
        http_status=400,
        problem_title="Promotion Rationale Required",
        problem_type_uri="urn:opendv:error:promotion-rationale-required",
    ),
    ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE: ErrorMappingEntry(
        error_code=ErrorCode.REFERENCE_PACK_INTEGRITY_FAILURE,
        exit_code=1,
        http_status=500,
        problem_title="Reference Pack Integrity Failure",
        problem_type_uri="urn:opendv:error:reference-pack-integrity-failure",
    ),
    ErrorCode.VERSION_COMMIT_IO_FAILURE: ErrorMappingEntry(
        error_code=ErrorCode.VERSION_COMMIT_IO_FAILURE,
        exit_code=1,
        http_status=500,
        problem_title="Version Commit IO Failure",
        problem_type_uri="urn:opendv:error:version-commit-io-failure",
    ),
    ErrorCode.INVALID_EVIDENCE_OFFSETS: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_EVIDENCE_OFFSETS,
        exit_code=2,
        http_status=400,
        problem_title="Invalid Evidence Offsets",
        problem_type_uri="urn:opendv:error:invalid-evidence-offsets",
    ),
    ErrorCode.INVALID_PRECONDITION_HEADER: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_PRECONDITION_HEADER,
        exit_code=2,
        http_status=400,
        problem_title="Invalid Precondition Header",
        problem_type_uri="urn:opendv:error:invalid-precondition-header",
    ),
    ErrorCode.INVALID_SESSION_IDENTIFIER: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_SESSION_IDENTIFIER,
        exit_code=2,
        http_status=400,
        problem_title="Invalid Session Identifier",
        problem_type_uri="urn:opendv:error:invalid-session-identifier",
    ),
    ErrorCode.INVALID_WORKSPACE_PATH: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_WORKSPACE_PATH,
        exit_code=2,
        http_status=400,
        problem_title="Invalid Workspace Path",
        problem_type_uri="urn:opendv:error:invalid-workspace-path",
    ),
    ErrorCode.MISSING_AUTO_COMMIT_PRECONDITION: ErrorMappingEntry(
        error_code=ErrorCode.MISSING_AUTO_COMMIT_PRECONDITION,
        exit_code=2,
        http_status=400,
        problem_title="Missing Auto-Commit Precondition",
        problem_type_uri="urn:opendv:error:missing-auto-commit-precondition",
    ),
    ErrorCode.MISSING_PRECONDITION_HEADER: ErrorMappingEntry(
        error_code=ErrorCode.MISSING_PRECONDITION_HEADER,
        exit_code=2,
        http_status=400,
        problem_title="Missing Precondition Header",
        problem_type_uri="urn:opendv:error:missing-precondition-header",
    ),
    ErrorCode.PRECONDITION_BASE_SHA_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.PRECONDITION_BASE_SHA_MISMATCH,
        exit_code=2,
        http_status=400,
        problem_title="Precondition Base SHA Mismatch",
        problem_type_uri="urn:opendv:error:precondition-base-sha-mismatch",
    ),
    ErrorCode.REMOTE_SERVER_PATH_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.REMOTE_SERVER_PATH_FORBIDDEN,
        exit_code=2,
        http_status=400,
        problem_title="Remote Server Path Forbidden",
        problem_type_uri="urn:opendv:error:remote-server-path-forbidden",
    ),
    ErrorCode.SYNTAX_INVALID_ARGUMENT: ErrorMappingEntry(
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        exit_code=2,
        http_status=400,
        problem_title="Invalid Argument Syntax",
        problem_type_uri="urn:opendv:error:syntax-invalid-argument",
    ),
    ErrorCode.VALIDATION_AMBIGUOUS_TARGET: ErrorMappingEntry(
        error_code=ErrorCode.VALIDATION_AMBIGUOUS_TARGET,
        exit_code=2,
        http_status=400,
        problem_title="Ambiguous Target Specifier",
        problem_type_uri="urn:opendv:error:validation-ambiguous-target",
    ),
    ErrorCode.JOB_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.JOB_NOT_FOUND,
        exit_code=3,
        http_status=404,
        problem_title="Job Not Found",
        problem_type_uri="urn:opendv:error:job-not-found",
    ),
    ErrorCode.PROPOSAL_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.PROPOSAL_NOT_FOUND,
        exit_code=3,
        http_status=404,
        problem_title="Proposal Not Found",
        problem_type_uri="urn:opendv:error:proposal-not-found",
    ),
    ErrorCode.SESSION_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.SESSION_NOT_FOUND,
        exit_code=3,
        http_status=404,
        problem_title="Session Not Found",
        problem_type_uri="urn:opendv:error:session-not-found",
    ),
    ErrorCode.SOURCE_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.SOURCE_NOT_FOUND,
        exit_code=3,
        http_status=404,
        problem_title="Source Document Not Found",
        problem_type_uri="urn:opendv:error:source-not-found",
    ),
    ErrorCode.VERSION_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.VERSION_NOT_FOUND,
        exit_code=3,
        http_status=404,
        problem_title="Version Not Found",
        problem_type_uri="urn:opendv:error:version-not-found",
    ),
    ErrorCode.AGENT_UNRESOLVED_PSEUDONYM: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_UNRESOLVED_PSEUDONYM,
        exit_code=4,
        http_status=422,
        problem_title="Agent Unresolved Pseudonym",
        problem_type_uri="urn:opendv:error:agent-unresolved-pseudonym",
    ),
    ErrorCode.AMBIGUOUS_EXTERNAL_MAPPING: ErrorMappingEntry(
        error_code=ErrorCode.AMBIGUOUS_EXTERNAL_MAPPING,
        exit_code=4,
        http_status=422,
        problem_title="Ambiguous External Mapping",
        problem_type_uri="urn:opendv:error:ambiguous-external-mapping",
    ),
    ErrorCode.ARCHIVE_SECURITY_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.ARCHIVE_SECURITY_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="Archive Security Violation",
        problem_type_uri="urn:opendv:error:archive-security-violation",
    ),
    ErrorCode.ATTRIBUTE_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.ATTRIBUTE_NOT_FOUND,
        exit_code=4,
        http_status=422,
        problem_title="Attribute Not Found",
        problem_type_uri="urn:opendv:error:attribute-not-found",
    ),
    ErrorCode.AUTOMATED_ATTRIBUTION_INVALID: ErrorMappingEntry(
        error_code=ErrorCode.AUTOMATED_ATTRIBUTION_INVALID,
        exit_code=4,
        http_status=422,
        problem_title="Invalid Automated Attribution",
        problem_type_uri="urn:opendv:error:automated-attribution-invalid",
    ),
    ErrorCode.CANNOT_DELETE_PROMOTED_SESSION: ErrorMappingEntry(
        error_code=ErrorCode.CANNOT_DELETE_PROMOTED_SESSION,
        exit_code=4,
        http_status=422,
        problem_title="Cannot Delete Promoted Session",
        problem_type_uri="urn:opendv:error:cannot-delete-promoted-session",
    ),
    ErrorCode.CANNOT_PRUNE_PROMOTED_SESSION: ErrorMappingEntry(
        error_code=ErrorCode.CANNOT_PRUNE_PROMOTED_SESSION,
        exit_code=4,
        http_status=422,
        problem_title="Cannot Prune Promoted Session",
        problem_type_uri="urn:opendv:error:cannot-prune-promoted-session",
    ),
    ErrorCode.CONCEPT_STILL_REFERENCED: ErrorMappingEntry(
        error_code=ErrorCode.CONCEPT_STILL_REFERENCED,
        exit_code=4,
        http_status=422,
        problem_title="Concept Still Referenced",
        problem_type_uri="urn:opendv:error:concept-still-referenced",
    ),
    ErrorCode.CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED: ErrorMappingEntry(
        error_code=ErrorCode.CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED,
        exit_code=4,
        http_status=422,
        problem_title="Confidential Source Export Unacknowledged",
        problem_type_uri="urn:opendv:error:confidential-source-export-unacknowledged",
    ),
    ErrorCode.DANGLING_ENUM_CODE_DESCRIPTION: ErrorMappingEntry(
        error_code=ErrorCode.DANGLING_ENUM_CODE_DESCRIPTION,
        exit_code=4,
        http_status=422,
        problem_title="Dangling Enumeration Code Description",
        problem_type_uri="urn:opendv:error:dangling-enum-code-description",
    ),
    ErrorCode.DESCRIPTION_ALREADY_EXISTS: ErrorMappingEntry(
        error_code=ErrorCode.DESCRIPTION_ALREADY_EXISTS,
        exit_code=4,
        http_status=422,
        problem_title="Description Already Exists",
        problem_type_uri="urn:opendv:error:description-already-exists",
    ),
    ErrorCode.DUPLICATE_ATTRIBUTE_NAME: ErrorMappingEntry(
        error_code=ErrorCode.DUPLICATE_ATTRIBUTE_NAME,
        exit_code=4,
        http_status=422,
        problem_title="Duplicate Attribute Name",
        problem_type_uri="urn:opendv:error:duplicate-attribute-name",
    ),
    ErrorCode.DUPLICATE_ENUM_CODE: ErrorMappingEntry(
        error_code=ErrorCode.DUPLICATE_ENUM_CODE,
        exit_code=4,
        http_status=422,
        problem_title="Duplicate Enumeration Code",
        problem_type_uri="urn:opendv:error:duplicate-enum-code",
    ),
    ErrorCode.DUPLICATE_RELATIONSHIP_NAME: ErrorMappingEntry(
        error_code=ErrorCode.DUPLICATE_RELATIONSHIP_NAME,
        exit_code=4,
        http_status=422,
        problem_title="Duplicate Relationship Name",
        problem_type_uri="urn:opendv:error:duplicate-relationship-name",
    ),
    ErrorCode.DUPLICATE_RELATIONSHIP_URI: ErrorMappingEntry(
        error_code=ErrorCode.DUPLICATE_RELATIONSHIP_URI,
        exit_code=4,
        http_status=422,
        problem_title="Duplicate Relationship URI",
        problem_type_uri="urn:opendv:error:duplicate-relationship-uri",
    ),
    ErrorCode.DUPLICATE_SYNONYM: ErrorMappingEntry(
        error_code=ErrorCode.DUPLICATE_SYNONYM,
        exit_code=4,
        http_status=422,
        problem_title="Duplicate Synonym",
        problem_type_uri="urn:opendv:error:duplicate-synonym",
    ),
    ErrorCode.ENUM_CODE_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.ENUM_CODE_NOT_FOUND,
        exit_code=4,
        http_status=422,
        problem_title="Enumeration Code Not Found",
        problem_type_uri="urn:opendv:error:enum-code-not-found",
    ),
    ErrorCode.EVIDENCE_OFFSET_OUT_OF_RANGE: ErrorMappingEntry(
        error_code=ErrorCode.EVIDENCE_OFFSET_OUT_OF_RANGE,
        exit_code=4,
        http_status=422,
        problem_title="Evidence Offset Out of Range",
        problem_type_uri="urn:opendv:error:evidence-offset-out-of-range",
    ),
    ErrorCode.GENESIS_REQUIRES_CONCEPT: ErrorMappingEntry(
        error_code=ErrorCode.GENESIS_REQUIRES_CONCEPT,
        exit_code=4,
        http_status=422,
        problem_title="Genesis Version Requires Concept",
        problem_type_uri="urn:opendv:error:genesis-requires-concept",
    ),
    ErrorCode.HIERARCHICAL_CYCLE_DETECTED: ErrorMappingEntry(
        error_code=ErrorCode.HIERARCHICAL_CYCLE_DETECTED,
        exit_code=4,
        http_status=422,
        problem_title="Hierarchical Cycle Detected",
        problem_type_uri="urn:opendv:error:hierarchical-cycle-detected",
    ),
    ErrorCode.HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED: ErrorMappingEntry(
        error_code=ErrorCode.HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED,
        exit_code=4,
        http_status=422,
        problem_title="Historical Contract Warning Unacknowledged",
        problem_type_uri="urn:opendv:error:historical-contract-warning-unacknowledged",
    ),
    ErrorCode.HUB_BIJECTION_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.HUB_BIJECTION_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="Hub Bijection Violation",
        problem_type_uri="urn:opendv:error:hub-bijection-violation",
    ),
    ErrorCode.IMPORT_ADMIN_OP_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_ADMIN_OP_FORBIDDEN,
        exit_code=4,
        http_status=422,
        problem_title="Import Admin Operation Forbidden",
        problem_type_uri="urn:opendv:error:import-admin-op-forbidden",
    ),
    ErrorCode.IMPORT_AGENT_DESTRUCTIVE_OP_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_AGENT_DESTRUCTIVE_OP_FORBIDDEN,
        exit_code=4,
        http_status=422,
        problem_title="Import Agent Destructive Operation Forbidden",
        problem_type_uri="urn:opendv:error:import-agent-destructive-op-forbidden",
    ),
    ErrorCode.IMPORT_AUTOMATED_ATTRIBUTION_INVALID: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_AUTOMATED_ATTRIBUTION_INVALID,
        exit_code=4,
        http_status=422,
        problem_title="Import Invalid Automated Attribution",
        problem_type_uri="urn:opendv:error:import-automated-attribution-invalid",
    ),
    ErrorCode.IMPORT_CONFIDENCE_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_CONFIDENCE_MISMATCH,
        exit_code=4,
        http_status=422,
        problem_title="Import Confidence Mismatch",
        problem_type_uri="urn:opendv:error:import-confidence-mismatch",
    ),
    ErrorCode.IMPORT_DELTA_REPLAY_HASH_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_DELTA_REPLAY_HASH_MISMATCH,
        exit_code=4,
        http_status=422,
        problem_title="Import Delta Replay Hash Mismatch",
        problem_type_uri="urn:opendv:error:import-delta-replay-hash-mismatch",
    ),
    ErrorCode.IMPORT_ENVELOPE_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_ENVELOPE_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="Import Envelope Violation",
        problem_type_uri="urn:opendv:error:import-envelope-violation",
    ),
    ErrorCode.IMPORT_GROUNDING_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_GROUNDING_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="Import Grounding Violation",
        problem_type_uri="urn:opendv:error:import-grounding-violation",
    ),
    ErrorCode.IMPORT_LINEAGE_BROKEN: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_LINEAGE_BROKEN,
        exit_code=4,
        http_status=422,
        problem_title="Import Lineage Broken",
        problem_type_uri="urn:opendv:error:import-lineage-broken",
    ),
    ErrorCode.IMPORT_MATCHER_VERSION_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_MATCHER_VERSION_MISMATCH,
        exit_code=4,
        http_status=422,
        problem_title="Import Matcher Version Mismatch",
        problem_type_uri="urn:opendv:error:import-matcher-version-mismatch",
    ),
    ErrorCode.IMPORT_PROVISIONAL_URI_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_PROVISIONAL_URI_FORBIDDEN,
        exit_code=4,
        http_status=422,
        problem_title="Import Provisional URI Forbidden",
        problem_type_uri="urn:opendv:error:import-provisional-uri-forbidden",
    ),
    ErrorCode.IMPORT_SELF_ACCEPTANCE_UNACKNOWLEDGED: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_SELF_ACCEPTANCE_UNACKNOWLEDGED,
        exit_code=4,
        http_status=422,
        problem_title="Import Self-Acceptance Unacknowledged",
        problem_type_uri="urn:opendv:error:import-self-acceptance-unacknowledged",
    ),
    ErrorCode.IMPORT_SEMVER_BUMP_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_SEMVER_BUMP_MISMATCH,
        exit_code=4,
        http_status=422,
        problem_title="Import SemVer Bump Mismatch",
        problem_type_uri="urn:opendv:error:import-semver-bump-mismatch",
    ),
    ErrorCode.IMPORT_UNKNOWN_REFERENCE_PACK: ErrorMappingEntry(
        error_code=ErrorCode.IMPORT_UNKNOWN_REFERENCE_PACK,
        exit_code=4,
        http_status=422,
        problem_title="Import Unknown Reference Pack",
        problem_type_uri="urn:opendv:error:import-unknown-reference-pack",
    ),
    ErrorCode.INVALID_EVIDENCE_GROUNDING: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_EVIDENCE_GROUNDING,
        exit_code=4,
        http_status=422,
        problem_title="Invalid Evidence Grounding",
        problem_type_uri="urn:opendv:error:invalid-evidence-grounding",
    ),
    ErrorCode.INVALID_GROUNDING_WAIVER: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_GROUNDING_WAIVER,
        exit_code=4,
        http_status=422,
        problem_title="Invalid Grounding Waiver",
        problem_type_uri="urn:opendv:error:invalid-grounding-waiver",
    ),
    ErrorCode.INVALID_PHYSICAL_TABLE_NAME: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_PHYSICAL_TABLE_NAME,
        exit_code=4,
        http_status=422,
        problem_title="Invalid Physical Table Name",
        problem_type_uri="urn:opendv:error:invalid-physical-table-name",
    ),
    ErrorCode.INVALID_PROPOSAL_STATE: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_PROPOSAL_STATE,
        exit_code=4,
        http_status=422,
        problem_title="Invalid Proposal State",
        problem_type_uri="urn:opendv:error:invalid-proposal-state",
    ),
    ErrorCode.INVALID_WORKSPACE_ONTOLOGY: ErrorMappingEntry(
        error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
        exit_code=4,
        http_status=422,
        problem_title="Invalid Workspace Ontology",
        problem_type_uri="urn:opendv:error:invalid-workspace-ontology",
    ),
    ErrorCode.MAPPING_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.MAPPING_NOT_FOUND,
        exit_code=4,
        http_status=422,
        problem_title="External Mapping Not Found",
        problem_type_uri="urn:opendv:error:mapping-not-found",
    ),
    ErrorCode.ONTOLOGY_REQUIRES_CONCEPT: ErrorMappingEntry(
        error_code=ErrorCode.ONTOLOGY_REQUIRES_CONCEPT,
        exit_code=4,
        http_status=422,
        problem_title="Ontology Requires Concept",
        problem_type_uri="urn:opendv:error:ontology-requires-concept",
    ),
    ErrorCode.ONT_EVO_CONTRACT_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_CONTRACT_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Contract Violation",
        problem_type_uri="urn:opendv:error:ont-evo-contract-violation",
    ),
    ErrorCode.ONT_EVO_ENVELOPE_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_ENVELOPE_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Envelope Violation",
        problem_type_uri="urn:opendv:error:ont-evo-envelope-violation",
    ),
    ErrorCode.ONT_EVO_FORGED_PROVENANCE: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_FORGED_PROVENANCE,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Forged Provenance",
        problem_type_uri="urn:opendv:error:ont-evo-forged-provenance",
    ),
    ErrorCode.ONT_EVO_GROUNDING_VIOLATION: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_GROUNDING_VIOLATION,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Grounding Violation",
        problem_type_uri="urn:opendv:error:ont-evo-grounding-violation",
    ),
    ErrorCode.ONT_EVO_LOW_CONFIDENCE_GROUNDING: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_LOW_CONFIDENCE_GROUNDING,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Low Confidence Grounding",
        problem_type_uri="urn:opendv:error:ont-evo-low-confidence-grounding",
    ),
    ErrorCode.ONT_EVO_PSEUDONYM_LEAK: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_PSEUDONYM_LEAK,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Pseudonym Leak",
        problem_type_uri="urn:opendv:error:ont-evo-pseudonym-leak",
    ),
    ErrorCode.ONT_EVO_UNGROUNDED_CONCEPT: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_UNGROUNDED_CONCEPT,
        exit_code=4,
        http_status=422,
        problem_title="ONT-EVO Ungrounded Concept",
        problem_type_uri="urn:opendv:error:ont-evo-ungrounded-concept",
    ),
    ErrorCode.PARENT_ALREADY_EXISTS: ErrorMappingEntry(
        error_code=ErrorCode.PARENT_ALREADY_EXISTS,
        exit_code=4,
        http_status=422,
        problem_title="Parent Already Exists",
        problem_type_uri="urn:opendv:error:parent-already-exists",
    ),
    ErrorCode.PARENT_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.PARENT_NOT_FOUND,
        exit_code=4,
        http_status=422,
        problem_title="Parent Concept Not Found",
        problem_type_uri="urn:opendv:error:parent-not-found",
    ),
    ErrorCode.PHYSICAL_TABLE_NAME_COLLISION: ErrorMappingEntry(
        error_code=ErrorCode.PHYSICAL_TABLE_NAME_COLLISION,
        exit_code=4,
        http_status=422,
        problem_title="Physical Table Name Collision",
        problem_type_uri="urn:opendv:error:physical-table-name-collision",
    ),
    ErrorCode.REDACTED_PLACEHOLDER_LEAK: ErrorMappingEntry(
        error_code=ErrorCode.REDACTED_PLACEHOLDER_LEAK,
        exit_code=4,
        http_status=422,
        problem_title="Redacted Placeholder Leak",
        problem_type_uri="urn:opendv:error:redacted-placeholder-leak",
    ),
    ErrorCode.REFERENCED_CONCEPT_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.REFERENCED_CONCEPT_NOT_FOUND,
        exit_code=4,
        http_status=422,
        problem_title="Referenced Concept Not Found",
        problem_type_uri="urn:opendv:error:referenced-concept-not-found",
    ),
    ErrorCode.RELATIONSHIP_NOT_FOUND: ErrorMappingEntry(
        error_code=ErrorCode.RELATIONSHIP_NOT_FOUND,
        exit_code=4,
        http_status=422,
        problem_title="Relationship Not Found",
        problem_type_uri="urn:opendv:error:relationship-not-found",
    ),
    ErrorCode.SOURCE_REFERENCED_BY_COMMITTED_VERSION: ErrorMappingEntry(
        error_code=ErrorCode.SOURCE_REFERENCED_BY_COMMITTED_VERSION,
        exit_code=4,
        http_status=422,
        problem_title="Source Referenced by Committed Version",
        problem_type_uri="urn:opendv:error:source-referenced-by-committed-version",
    ),
    ErrorCode.SOURCE_REFERENCED_BY_PENDING_PROPOSAL: ErrorMappingEntry(
        error_code=ErrorCode.SOURCE_REFERENCED_BY_PENDING_PROPOSAL,
        exit_code=4,
        http_status=422,
        problem_title="Source Referenced by Pending Proposal",
        problem_type_uri="urn:opendv:error:source-referenced-by-pending-proposal",
    ),
    ErrorCode.UNDEFINED_PHYSICAL_TABLE_MAPPING: ErrorMappingEntry(
        error_code=ErrorCode.UNDEFINED_PHYSICAL_TABLE_MAPPING,
        exit_code=4,
        http_status=422,
        problem_title="Undefined Physical Table Mapping",
        problem_type_uri="urn:opendv:error:undefined-physical-table-mapping",
    ),
    ErrorCode.UNKNOWN_REFERENCE_PACK: ErrorMappingEntry(
        error_code=ErrorCode.UNKNOWN_REFERENCE_PACK,
        exit_code=4,
        http_status=422,
        problem_title="Unknown Reference Pack",
        problem_type_uri="urn:opendv:error:unknown-reference-pack",
    ),
    ErrorCode.VERSION_CONSISTENCY_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.VERSION_CONSISTENCY_MISMATCH,
        exit_code=4,
        http_status=422,
        problem_title="Version Consistency Mismatch",
        problem_type_uri="urn:opendv:error:version-consistency-mismatch",
    ),
    ErrorCode.LINEAGE_ROOT_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.LINEAGE_ROOT_MISMATCH,
        exit_code=5,
        http_status=412,
        problem_title="Lineage Root Mismatch",
        problem_type_uri="urn:opendv:error:lineage-root-mismatch",
    ),
    ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION: ErrorMappingEntry(
        error_code=ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION,
        exit_code=5,
        http_status=412,
        problem_title="ONT-EVO Concurrent Modification",
        problem_type_uri="urn:opendv:error:ont-evo-concurrent-modification",
    ),
    ErrorCode.JOB_COMMIT_IN_PROGRESS: ErrorMappingEntry(
        error_code=ErrorCode.JOB_COMMIT_IN_PROGRESS,
        exit_code=6,
        http_status=409,
        problem_title="Job Commit In Progress",
        problem_type_uri="urn:opendv:error:job-commit-in-progress",
    ),
    ErrorCode.LOCK_TIMEOUT: ErrorMappingEntry(
        error_code=ErrorCode.LOCK_TIMEOUT,
        exit_code=6,
        http_status=409,
        problem_title="Lock Acquisition Timeout",
        problem_type_uri="urn:opendv:error:lock-timeout",
    ),
    ErrorCode.PRINCIPAL_ALREADY_EXISTS: ErrorMappingEntry(
        error_code=ErrorCode.PRINCIPAL_ALREADY_EXISTS,
        exit_code=6,
        http_status=409,
        problem_title="Principal Already Exists",
        problem_type_uri="urn:opendv:error:principal-already-exists",
    ),
    ErrorCode.PRINCIPAL_RESERVED: ErrorMappingEntry(
        error_code=ErrorCode.PRINCIPAL_RESERVED,
        exit_code=6,
        http_status=409,
        problem_title="Principal Identifier Reserved",
        problem_type_uri="urn:opendv:error:principal-reserved",
    ),
    ErrorCode.PROPOSAL_ALREADY_RESOLVED: ErrorMappingEntry(
        error_code=ErrorCode.PROPOSAL_ALREADY_RESOLVED,
        exit_code=6,
        http_status=409,
        problem_title="Proposal Already Resolved",
        problem_type_uri="urn:opendv:error:proposal-already-resolved",
    ),
    ErrorCode.STUDIO_ALREADY_INITIALIZED: ErrorMappingEntry(
        error_code=ErrorCode.STUDIO_ALREADY_INITIALIZED,
        exit_code=6,
        http_status=409,
        problem_title="Studio Already Initialized",
        problem_type_uri="urn:opendv:error:studio-already-initialized",
    ),
    ErrorCode.AGENT_ADMIN_OP_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_ADMIN_OP_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Agent Admin Operation Forbidden",
        problem_type_uri="urn:opendv:error:agent-admin-op-forbidden",
    ),
    ErrorCode.AGENT_DESTRUCTIVE_OP_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_DESTRUCTIVE_OP_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Agent Destructive Operation Forbidden",
        problem_type_uri="urn:opendv:error:agent-destructive-op-forbidden",
    ),
    ErrorCode.AGENT_METADATA_OVERWRITE_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_METADATA_OVERWRITE_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Agent Metadata Overwrite Forbidden",
        problem_type_uri="urn:opendv:error:agent-metadata-overwrite-forbidden",
    ),
    ErrorCode.AGENT_OPERATION_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_OPERATION_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Agent Operation Forbidden",
        problem_type_uri="urn:opendv:error:agent-operation-forbidden",
    ),
    ErrorCode.AGENT_PROPOSAL_ACCEPT_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.AGENT_PROPOSAL_ACCEPT_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Agent Proposal Accept Forbidden",
        problem_type_uri="urn:opendv:error:agent-proposal-accept-forbidden",
    ),
    ErrorCode.CSRF_ORIGIN_MISMATCH: ErrorMappingEntry(
        error_code=ErrorCode.CSRF_ORIGIN_MISMATCH,
        exit_code=7,
        http_status=403,
        problem_title="CSRF Origin Mismatch",
        problem_type_uri="urn:opendv:error:csrf-origin-mismatch",
    ),
    ErrorCode.FORBIDDEN_ADMIN_REQUIRED: ErrorMappingEntry(
        error_code=ErrorCode.FORBIDDEN_ADMIN_REQUIRED,
        exit_code=7,
        http_status=403,
        problem_title="Forbidden: Admin Role Required",
        problem_type_uri="urn:opendv:error:forbidden-admin-required",
    ),
    ErrorCode.LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE: ErrorMappingEntry(
        error_code=ErrorCode.LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE,
        exit_code=7,
        http_status=403,
        problem_title="Loopback Privilege Disabled in Remote Mode",
        problem_type_uri="urn:opendv:error:loopback-privilege-disabled-in-remote-mode",
    ),
    ErrorCode.PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Proposal Self-Acceptance Forbidden",
        problem_type_uri="urn:opendv:error:proposal-self-acceptance-forbidden",
    ),
    ErrorCode.SERVER_OPERATION_FORBIDDEN: ErrorMappingEntry(
        error_code=ErrorCode.SERVER_OPERATION_FORBIDDEN,
        exit_code=7,
        http_status=403,
        problem_title="Server Operation Forbidden",
        problem_type_uri="urn:opendv:error:server-operation-forbidden",
    ),
    ErrorCode.UNAUTHENTICATED: ErrorMappingEntry(
        error_code=ErrorCode.UNAUTHENTICATED,
        exit_code=7,
        http_status=401,
        problem_title="Unauthenticated Request",
        problem_type_uri="urn:opendv:error:unauthenticated",
    ),
    ErrorCode.SESSION_ALREADY_EXISTS: ErrorMappingEntry(
        error_code=ErrorCode.SESSION_ALREADY_EXISTS,
        exit_code=6,
        http_status=409,
        problem_title="Session Already Exists",
        problem_type_uri="urn:opendv:error:session-already-exists",
    ),
    ErrorCode.CORRUPT_SOURCE_BLOB: ErrorMappingEntry(
        error_code=ErrorCode.CORRUPT_SOURCE_BLOB,
        exit_code=4,
        http_status=422,
        problem_title="Corrupt Source Blob",
        problem_type_uri="urn:opendv:error:corrupt-source-blob",
    ),
    ErrorCode.LAUNCH_NONCE_EXPIRED: ErrorMappingEntry(
        error_code=ErrorCode.LAUNCH_NONCE_EXPIRED,
        exit_code=7,
        http_status=401,
        problem_title="Launch Nonce Expired",
        problem_type_uri="urn:opendv:error:launch-nonce-expired",
    ),
    ErrorCode.AUTHENTICATION_CONFIG_ERROR: ErrorMappingEntry(
        error_code=ErrorCode.AUTHENTICATION_CONFIG_ERROR,
        exit_code=1,
        http_status=500,
        problem_title="Authentication Configuration Error",
        problem_type_uri="urn:opendv:error:authentication-config-error",
    ),
    ErrorCode.EXECUTION_FAILURE: ErrorMappingEntry(
        error_code=ErrorCode.EXECUTION_FAILURE,
        exit_code=1,
        http_status=500,
        problem_title="Execution Failure",
        problem_type_uri="urn:opendv:error:execution-failure",
    ),
    ErrorCode.JOB_ALREADY_TERMINAL: ErrorMappingEntry(
        error_code=ErrorCode.JOB_ALREADY_TERMINAL,
        exit_code=6,
        http_status=409,
        problem_title="Job Already Terminal",
        problem_type_uri="urn:opendv:error:job-already-terminal",
    ),
    ErrorCode.STATIC_TOKEN_CONFIGURED: ErrorMappingEntry(
        error_code=ErrorCode.STATIC_TOKEN_CONFIGURED,
        exit_code=1,
        http_status=500,
        problem_title="Static Token Configured",
        problem_type_uri="urn:opendv:error:static-token-configured",
    ),
}

ERROR_MAP = ERROR_MAPPINGS

VALID_EXIT_CODES: frozenset[int] = frozenset(range(8))


def get_error_mapping(code: ErrorCode) -> tuple[int, int]:
    """Returns (exit_code, http_status) for the given ErrorCode."""
    entry = ERROR_MAPPINGS.get(code)
    if entry is None:
        return (2, 500)
    return (entry.exit_code, entry.http_status)


def get_error_entry(code: ErrorCode) -> ErrorMappingEntry:
    """Returns the full ErrorMappingEntry for the given ErrorCode."""
    if code in ERROR_MAPPINGS:
        return ERROR_MAPPINGS[code]
    return ErrorMappingEntry(
        error_code=code,
        exit_code=2,
        http_status=500,
        problem_title="Internal Server Error",
        problem_type_uri="urn:opendv:internal-error",
    )


def create_problem_detail(
    code: ErrorCode,
    detail: str,
    *,
    instance: str | None = None,
    invalid_params: list[ErrorDetail] | None = None,
    extensions: dict[str, Any] | None = None,
) -> ProblemDetail:
    """Helper to construct an RFC 9457 ProblemDetail from an ErrorCode."""
    entry = get_error_entry(code)
    return ProblemDetail(
        type=entry.problem_type_uri,
        title=entry.problem_title,
        status=entry.http_status,
        detail=detail,
        instance=instance,
        error_code=code,
        invalid_params=invalid_params or [],
        extensions=extensions or {},
    )


class OpenDVError(Exception):
    """Base exception for all domain errors within OpenDV."""

    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    exit_code: int = 2
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        self.exit_code, self.http_status = get_error_mapping(self.error_code)
        self.details = details or {}

    def to_problem_detail(self, *, instance: str | None = None) -> ProblemDetail:
        """Convert exception to an RFC 9457 ProblemDetail DTO."""
        return create_problem_detail(
            code=self.error_code,
            detail=self.message,
            instance=instance,
            extensions=self.details,
        )


class InputValidationError(OpenDVError):
    """Raised when client input fails syntactic, structural, or ontology validation."""

    error_code = ErrorCode.INPUT_VALIDATION_ERROR


class NotFoundError(OpenDVError):
    """Raised when a requested resource (project, run, hold, artifact) does not exist."""

    error_code = ErrorCode.RESOURCE_NOT_FOUND


class HoldNotFoundError(NotFoundError):
    """Raised when a requested steward hold does not exist."""

    error_code = ErrorCode.HOLD_NOT_FOUND


class OntologyRecommenderUnavailableError(OpenDVError):
    """Raised when an ontology recommendation policy is active but no
    recommender module is installed.
    """

    error_code = ErrorCode.ONTOLOGY_RECOMMENDER_UNAVAILABLE


DEFAULT_MAX_INLINE_BYTES: int = 50 * 1024 * 1024


class PayloadTooLargeError(OpenDVError):
    """Raised when an uploaded payload or request body exceeds the configured maximum byte size."""

    error_code = ErrorCode.PAYLOAD_TOO_LARGE
    exit_code = 2
    http_status = 413


__all__ = [
    "DEFAULT_MAX_INLINE_BYTES",
    "ERROR_MAP",
    "ERROR_MAPPINGS",
    "VALID_EXIT_CODES",
    "ErrorCode",
    "ErrorDetail",
    "ErrorMappingEntry",
    "HoldNotFoundError",
    "InputValidationError",
    "NotFoundError",
    "OntologyRecommenderUnavailableError",
    "OpenDVError",
    "PayloadTooLargeError",
    "ProblemDetail",
    "create_problem_detail",
    "get_error_entry",
    "get_error_mapping",
]
