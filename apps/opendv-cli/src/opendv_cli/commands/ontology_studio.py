"""`opendv ontology studio` command handlers (Track 5C / Task 5C.1 / Issue #219).

In-process local executor for the 14 Ontology Construction Studio subcommand groups
(21 leaf commands) operating directly against `opendv-service` with zero network overhead.
Adheres strictly to AST isolation: zero imports from `opendv_platform_api` or web frameworks.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import TypeAdapter, ValidationError

from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.ontology import (
    BaseOntologyDeltaOp,
    OntologyDelta,
    OntologyOperation,
    StewardWaiverRecord,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.construction.clarification_harness import ClarificationHarness
from opendv_service.construction.deterministic_pipeline import (
    DeterministicConstructionPipeline,
)
from opendv_service.construction.models import ConstructOptions
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.ontology.applier import compute_next_semver, parse_semver
from opendv_service.ontology.validator import SemVerCalculusError
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
)
from opendv_service.studio.bundle import (
    ArchiveSecurityViolationError,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.locks import (
    L4_WORKSPACE,
    L5_SESSION,
    StudioLock,
    multi_studio_lock,
    studio_lock,
)
from opendv_service.studio.session_manager import (
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    StudioSessionManager,
    set_owner_only_permissions,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    DEFAULT_STUDIO_DIR,
    WorkspaceSecurityManager,
)
from opendv_sources.adapters.multiformat_adapter import (
    DelimitedTabularAdapter,
    MarkdownDocumentAdapter,
    MarkupDocumentAdapter,
    PyArrowTabularAdapter,
    StructuredDataDocumentAdapter,
)
from opendv_sources.ingestion import PlainTextDocumentAdapter
from opendv_sources.ingestion.service import IngestionService

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


_PROPOSAL_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$"
)
_OP_TYPE_ADAPTER: TypeAdapter[OntologyOperation] = TypeAdapter(OntologyOperation)


def _get_studio_root(executor: LocalExecutor | None = None) -> Path:
    """Resolve active studio CAS root path."""
    env_root = os.environ.get("OPENDV_STUDIO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    base_ws = executor.workspace_root if executor else Path.cwd().resolve()
    return (base_ws / DEFAULT_STUDIO_DIR).resolve()


def _get_session_manager(executor: LocalExecutor | None = None) -> StudioSessionManager:
    """Resolve StudioSessionManager for local execution."""
    root = _get_studio_root(executor)
    return StudioSessionManager(studio_root=root)


def _validate_session(sid: str | None) -> tuple[int, Any] | None:
    """Validate session identifier presence and structure."""
    if not sid:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --session flag")
    try:
        validate_session_id(sid)
    except InvalidSessionIdentifierError as exc:
        return _err(2, "INVALID_SESSION_IDENTIFIER", str(exc))
    return None


def _err(code: int, error_code: str, detail: str) -> tuple[int, dict[str, str]]:
    """Construct standard error envelope tuple (SUG-01)."""
    return code, {
        "status": "ERROR",
        "error_code": error_code,
        "detail": detail,
    }


def _resolve_proposal_path(sdir: Path, proposal_id: str | None) -> tuple[int, Any] | Path:
    """Validate proposal ID and return resolved file path strictly under proposals dir."""
    if not proposal_id or not _PROPOSAL_ID_PATTERN.match(str(proposal_id)):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid proposal identifier: '{proposal_id}'")
    props_dir = (sdir / "proposals").resolve()
    target_file = (props_dir / f"{proposal_id}.json").resolve()
    if not target_file.is_relative_to(props_dir):
        return _err(
            2,
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in proposal identifier: '{proposal_id}'",
        )
    return target_file


def _atomic_write_json(target_path: Path, data: Any, indent: int = 2) -> None:
    """Atomically write JSON data with 0600 permissions using temp-file and replace (SUG-01)."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".{target_path.name}.{secrets.token_hex(4)}.tmp"
    tmp_path.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    set_owner_only_permissions(tmp_path)
    os.replace(tmp_path, target_path)
    set_owner_only_permissions(target_path)


def _require_session(session_mgr: StudioSessionManager, sid: str) -> tuple[int, Any] | Any:
    """Retrieve session metadata or return exit 3 error tuple (SUG-01)."""
    try:
        return session_mgr.get_session(sid)
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")


def _require_session_context(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, dict[str, str]] | tuple[StudioSessionManager, Any, Path, str]:
    """Validate session ID and return (session_mgr, meta, sdir, sid) or error tuple."""
    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err
    session_mgr = _get_session_manager(executor)
    try:
        meta = session_mgr.get_session(sid)
        sdir = session_mgr.get_session_dir(sid)
        return session_mgr, meta, sdir, str(sid)
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")


def _normalize_external_mapping(m: dict[str, Any] | Any) -> Any:
    """Normalize legacy or heterogeneous external mapping dicts into ExternalMapping schema."""
    if not isinstance(m, dict):
        return m
    norm = dict(m)
    scheme = (
        norm.get("scheme")
        or norm.pop("source_system", None)
        or norm.pop("source_standard", None)
        or norm.pop("standard_name", None)
    )
    if scheme:
        norm["scheme"] = str(scheme)
    target = (
        norm.get("target_uri")
        or norm.pop("standard_uri", None)
        or norm.pop("external_id", None)
        or norm.pop("external_term", None)
    )
    if target:
        norm["target_uri"] = str(target)

    conf = norm.get("confidence")
    if conf is None:
        conf = norm.pop("confidence_score", None)
    if conf is None:
        conf = norm.get("mapping_confidence")
    if conf is not None:
        with contextlib.suppress(ValueError, TypeError):
            norm["confidence"] = float(conf)

    pred = norm.get("predicate") or norm.pop("mapping_type", None)
    if pred:
        norm["predicate"] = str(pred)
    return norm


def _normalize_delta_operation(op: dict[str, Any] | Any) -> Any:
    """Instantiate typed delta operation from JSON dictionary (MIN-08, MAJ-01)."""
    if isinstance(op, BaseOntologyDeltaOp):
        return op
    if not isinstance(op, dict):
        return op
    op_copy = dict(op)
    raw_type = (
        op_copy.pop("op_type", None) or op_copy.pop("op", None) or op_copy.pop("operation", None)
    )
    if not raw_type:
        raise ValueError("Delta operation missing required 'op_type'")

    op_copy.pop("op", None)
    op_copy.pop("operation", None)

    op_type = str(raw_type)
    if op_type.endswith("Op") and len(op_type) > 2:
        op_type = op_type[:-2]
    op_type = re.sub(r"(?<!^)(?=[A-Z])", "_", op_type).lower()

    if not op_copy.get("op_id"):
        op_bytes = json.dumps(op_copy, sort_keys=True).encode("utf-8")
        op_copy["op_id"] = f"op-{hashlib.sha256(op_bytes).hexdigest()[:12]}"

    if "external_mappings" in op_copy and isinstance(op_copy["external_mappings"], list):
        op_copy["external_mappings"] = [
            _normalize_external_mapping(m) for m in op_copy["external_mappings"]
        ]

    if op_type == "add_concept":
        op_copy["op_type"] = "add_concept"
        if "concept" in op_copy and isinstance(op_copy["concept"], dict):
            c_dict = dict(op_copy.pop("concept"))
            if "external_mappings" in c_dict and isinstance(c_dict["external_mappings"], list):
                c_dict["external_mappings"] = [
                    _normalize_external_mapping(m) for m in c_dict["external_mappings"]
                ]
            norm: dict[str, Any] = {
                "op_type": "add_concept",
                "op_id": op_copy["op_id"],
            }
            norm.update(c_dict)
            norm.update(op_copy)
            norm["op_type"] = "add_concept"
            return _OP_TYPE_ADAPTER.validate_python(norm)
        return _OP_TYPE_ADAPTER.validate_python(op_copy)

    op_copy["op_type"] = op_type
    try:
        return _OP_TYPE_ADAPTER.validate_python(op_copy)
    except (ValidationError, ValueError, TypeError):
        return op_copy


def _extract_mapping_confidence(m: Any) -> float | None:
    """Extract numeric confidence score from typed mapping or dictionary, or None (MAJ-01)."""
    val = getattr(m, "confidence", None)
    if val is None and isinstance(m, dict):
        val = m.get("confidence")
        if val is None:
            val = m.get("confidence_score")
        if val is None:
            val = m.get("mapping_confidence")
    elif val is None and hasattr(m, "mapping_confidence"):
        val = m.mapping_confidence
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _is_mapping_low_confidence(m: Any, threshold: float = 0.70) -> bool:
    """A mapping is low-confidence if score is missing (None) or strictly below threshold."""
    conf = _extract_mapping_confidence(m)
    return conf is None or conf < threshold


def _collect_ungrounded_concepts(ops: list[Any], threshold: float = 0.70) -> set[str]:
    """Identify concept URIs across operations that have missing or low-confidence mappings."""
    ungrounded: set[str] = set()
    for op in ops:
        uri = getattr(op, "concept_uri", None) or (
            op.get("concept_uri") if isinstance(op, dict) else None
        )
        op_type = getattr(op, "op_type", None) or (
            op.get("op_type") if isinstance(op, dict) else None
        )
        if op_type in ("add_concept", "AddConcept", "AddConceptOp"):
            ext_mappings = getattr(op, "external_mappings", None) or (
                op.get("external_mappings") if isinstance(op, dict) else None
            )
            if not ext_mappings:
                if uri:
                    ungrounded.add(str(uri))
            else:
                for m in ext_mappings:
                    if _is_mapping_low_confidence(m, threshold=threshold):
                        if uri:
                            ungrounded.add(str(uri))
                        break
        elif (
            op_type in ("add_external_mapping", "rescore_external_mapping")
            and _is_mapping_low_confidence(op, threshold=threshold)
            and uri
        ):
            ungrounded.add(str(uri))
    return ungrounded


def _load_and_verify_waiver_credentials(
    args: argparse.Namespace, executor: LocalExecutor | None, studio_root: Path
) -> tuple[int, Any] | str:
    """Load waiver key from --waiver-key-file or OPENDV_WAIVER_KEY and verify key."""
    waiver_key = ""
    waiver_key_file = getattr(args, "waiver_key_file", None)
    waiver_key_env = os.environ.get("OPENDV_WAIVER_KEY")
    if waiver_key_file:
        key_path = Path(waiver_key_file)
        if not key_path.is_absolute() and executor:
            key_path = (executor.workspace_root / key_path).resolve()
        if not key_path.is_file():
            return _err(
                7, "FORBIDDEN_ADMIN_REQUIRED", f"Waiver key file not found: {waiver_key_file}"
            )
        try:
            waiver_key = key_path.read_text(encoding="utf-8").strip()
        except OSError:
            return _err(
                7, "FORBIDDEN_ADMIN_REQUIRED", f"Cannot read waiver key file: {waiver_key_file}"
            )
    elif waiver_key_env:
        waiver_key = waiver_key_env.strip()

    if not waiver_key:
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Administrative waivers strictly require authenticated waiver credentials.",
        )

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    expected_waiver_bytes = sec_mgr.get_waiver_key()
    expected_waiver_hex = expected_waiver_bytes.hex().lower()
    key_clean = waiver_key.strip().lower()

    try:
        key_bytes = bytes.fromhex(key_clean)
    except ValueError:
        key_bytes = waiver_key.strip().encode("utf-8")

    is_valid_key = hmac.compare_digest(key_clean, expected_waiver_hex) or hmac.compare_digest(
        key_bytes, expected_waiver_bytes
    )
    if not is_valid_key:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Invalid steward waiver credentials.")
    return waiver_key


def _verify_grounding_and_waiver(
    norm_ops: list[Any],
    args: argparse.Namespace,
    head_sha256: str,
    proposal_id: str | None,
    executor: LocalExecutor | None,
    studio_root: Path,
) -> tuple[int, Any] | StewardWaiverRecord | None:
    """Validate ONT-EVO-002 grounding gate and waiver authorization across operations."""
    ungrounded_in_ops = _collect_ungrounded_concepts(norm_ops)
    low_conf_waiver = bool(getattr(args, "low_confidence_waiver", False))

    if ungrounded_in_ops and not low_conf_waiver:
        return _err(
            4,
            "ONT_EVO_LOW_CONFIDENCE_GROUNDING",
            f"Operations contain low-confidence mappings for concepts: {sorted(ungrounded_in_ops)} "
            "and require an administrative waiver.",
        )

    if not low_conf_waiver:
        return None

    # Low-confidence waiver requested: validate rationale and credentials
    waiver_rationale = getattr(args, "waiver_rationale", None) or getattr(
        args, "grounding_rationale", None
    )
    if not waiver_rationale or len(waiver_rationale.strip()) < 10:
        return _err(
            4,
            "INVALID_GROUNDING_WAIVER",
            "Administrative grounding waiver requires a rationale of at least 10 characters.",
        )

    key_res = _load_and_verify_waiver_credentials(args, executor, studio_root)
    if isinstance(key_res, tuple):
        return key_res
    waiver_key = key_res

    # Enforce waiver scope: if user specifies --ungrounded-concept, verify all ungrounded ops
    specified_scope = getattr(args, "ungrounded_concept", None) or []
    if specified_scope:
        waived_set = set(specified_scope)
        uncovered = ungrounded_in_ops - waived_set
        if uncovered:
            return _err(
                4,
                "ONT_EVO_UNGROUNDED_CONCEPT",
                "Low-confidence waiver scope does not cover ungrounded concepts: "
                f"{sorted(uncovered)}",
            )
        target_concept_uris = sorted(waived_set)
    else:
        # Default to ungrounded concept URIs, or concept URIs in operations if none ungrounded
        if ungrounded_in_ops:
            target_concept_uris = sorted(ungrounded_in_ops)
        else:
            all_uris = [
                getattr(op, "concept_uri", None)
                or (op.get("concept_uri") if isinstance(op, dict) else None)
                for op in norm_ops
            ]
            target_concept_uris = sorted({str(u) for u in all_uris if u}) or [
                "urn:ontology:entity:exempted_concept"
            ]

    if proposal_id:
        try:
            p_uuid = uuid.UUID(str(proposal_id))
            prop_uuid = (
                p_uuid
                if p_uuid.version == 4
                else uuid.UUID(
                    bytes=hashlib.sha256(str(proposal_id).encode("utf-8")).digest()[:16], version=4
                )
            )
        except (ValueError, TypeError):
            prop_uuid = uuid.UUID(
                bytes=hashlib.sha256(str(proposal_id).encode("utf-8")).digest()[:16], version=4
            )
    else:
        prop_uuid = uuid.uuid4()

    delta_canon_bytes = dumps_jcs_bytes(
        [
            (
                op.model_dump(mode="json", exclude_none=True)
                if hasattr(op, "model_dump")
                else dict(op)
                if isinstance(op, dict)
                else op
            )
            for op in norm_ops
        ]
    )
    delta_hash = hashlib.sha256(delta_canon_bytes).hexdigest()

    auth_by = getattr(args, "authorized_by", None) or "data-steward"
    auth_role = getattr(args, "authorized_role", None) or "steward"
    auth_role_tier = "admin" if str(auth_role).lower() == "admin" else "steward"

    waiver_id = uuid.uuid4()
    sig_payload = (
        f"{waiver_id}:{prop_uuid}:{delta_hash}:"
        f"{head_sha256}:{','.join(sorted(target_concept_uris))}:"
        f"{waiver_rationale.strip()}"
    )
    if isinstance(waiver_key, bytes):
        key_bytes = waiver_key
    else:
        clean_key = str(waiver_key).strip()
        try:
            key_bytes = bytes.fromhex(clean_key)
        except ValueError:
            key_bytes = clean_key.encode("utf-8")
    sig = hmac.new(key_bytes, sig_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    waiver_rec = StewardWaiverRecord(
        waiver_id=waiver_id,
        proposal_id=prop_uuid,
        proposal_delta_sha256=delta_hash,
        target_concept_uris=target_concept_uris,
        base_sha256=head_sha256,
        rationale=waiver_rationale.strip(),
        authorized_by=str(auth_by),
        authorized_role=auth_role_tier,
        signature_or_token=sig,
        created_at=datetime.now(UTC),
    )

    return waiver_rec


def _load_session_base_ontology(sdir: Path, head_version: str | None) -> GraphAOntology | None:
    """Load base GraphAOntology artifact for a session's head version if not genesis."""
    if not head_version or head_version in ("0.0.0", ""):
        return None
    hver = head_version.lstrip("v")
    vdir = sdir / "versions" / f"v{hver}"
    ont_f = vdir / "ontology.json"
    if not ont_f.is_file():
        ont_f = vdir / "ontology.yaml"
    if not ont_f.is_file():
        return None
    try:
        raw_text = ont_f.read_text(encoding="utf-8")
        if ont_f.suffix == ".json":
            return GraphAOntology.model_validate_json(raw_text)
        return GraphAOntology.model_validate(yaml.safe_load(raw_text))
    except (OSError, ValueError, TypeError, yaml.YAMLError, ValidationError):
        return None


# ---------------------------------------------------------------------------
# Command Handlers (14 subcommand groups / 21 leaf commands)
# ---------------------------------------------------------------------------


def handle_init(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """1. `opendv ontology studio init [--force]`."""
    studio_root = _get_studio_root(executor)
    secret_file = studio_root / ".odv_workspace_secret"
    token_file = studio_root / ".local_token"
    force = bool(getattr(args, "force", False))

    if secret_file.exists() and not force:
        return _err(
            6,
            "STUDIO_ALREADY_INITIALIZED",
            "Studio workspace already initialized. Use --force to rotate.",
        )

    studio_root.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(studio_root)

    if force:
        with contextlib.suppress(OSError):
            if secret_file.exists():
                secret_file.unlink()

    WorkspaceSecurityManager(studio_root=studio_root)

    # Initialize local loopback token with 0600 mode from start (MIN-01)
    token = secrets.token_hex(32)
    tmp_token = studio_root / f".token.{uuid.uuid4().hex[:8]}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp_token), flags, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    set_owner_only_permissions(tmp_token)
    os.replace(tmp_token, token_file)
    set_owner_only_permissions(token_file)

    res_payload: dict[str, Any] = {
        "status": "initialized",
        "token_path": str(token_file),
        "principal": "human:local_admin",
        "studio_root": str(studio_root),
    }
    if getattr(args, "show_token", False):
        res_payload["local_token"] = token
    return 0, res_payload


def handle_create(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """2. `opendv ontology studio create`."""
    sid = getattr(args, "session", None)
    if sid:
        err = _validate_session(sid)
        if err:
            return err

    from_ws = getattr(args, "from_workspace", None)
    initial_ontology: GraphAOntology | None = None

    if from_ws:
        ws_path = Path(from_ws)
        if not ws_path.is_absolute():
            base = executor.workspace_root if executor else Path.cwd()
            ws_path = (base / ws_path).resolve()
        if not ws_path.exists():
            return _err(2, "INVALID_WORKSPACE_PATH", f"Workspace directory not found: {from_ws}")

        # Search for Graph A ontology file in workspace (MIN-13)
        found_yaml: Path | None = None
        for candidate in [
            ws_path / "pe_graph_a_ontology.yaml",
            ws_path / "graph_a_ontology.yaml",
            ws_path / "ontology.yaml",
        ]:
            if candidate.is_file():
                found_yaml = candidate
                break

        if not found_yaml:
            return _err(
                4,
                "INVALID_WORKSPACE_ONTOLOGY",
                f"No Graph A ontology contract found in '{from_ws}' "
                "(expected graph_a_ontology.yaml or pe_graph_a_ontology.yaml)",
            )

        try:
            raw_ont = yaml.safe_load(found_yaml.read_text(encoding="utf-8"))
            initial_ontology = GraphAOntology.model_validate(raw_ont)
        except (yaml.YAMLError, ValidationError, ValueError, OSError) as err_parse:
            return _err(
                4,
                "INVALID_WORKSPACE_ONTOLOGY",
                f"Failed to parse ontology in workspace: {err_parse}",
            )

    session_mgr = _get_session_manager(executor)
    domain_ns = getattr(args, "domain_namespace", None) or "default"
    owner = getattr(args, "enterprise_owner", None) or "Enterprise"

    try:
        meta = session_mgr.create_session(
            session_id=sid,
            initial_ontology=initial_ontology,
            domain_namespace=domain_ns,
            enterprise_owner=owner,
        )
    except SessionAlreadyExistsError as err_exists:
        return _err(6, "SESSION_ALREADY_EXISTS", str(err_exists))
    except InvalidSessionIdentifierError as err_id:
        return _err(2, "INVALID_SESSION_IDENTIFIER", str(err_id))

    return 0, {
        "session_id": meta.session_id,
        "domain_namespace": meta.domain_namespace,
        "enterprise_owner": meta.enterprise_owner,
        "status": meta.status,
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }


def handle_list(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """3. `opendv ontology studio list`."""
    session_mgr = _get_session_manager(executor)
    sessions = session_mgr.list_sessions()
    status_filter = getattr(args, "status", None)
    if status_filter:
        sessions = [s for s in sessions if s.status.upper() == status_filter.upper()]

    offset = getattr(args, "offset", 0) or 0
    limit = getattr(args, "limit", 50) or 50
    paged = sessions[offset : offset + limit]

    return 0, [
        {
            "session_id": s.session_id,
            "domain_namespace": s.domain_namespace,
            "enterprise_owner": s.enterprise_owner,
            "status": s.status,
            "active_version": s.head_version,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in paged
    ]


def handle_status(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """4. `opendv ontology studio status -s <session>`."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, meta, _, _ = ctx

    return 0, {
        "session_id": meta.session_id,
        "status": meta.status,
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "active_jobs": 0,
    }


def handle_upload(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """5. `opendv ontology studio upload -s <session> -f <file>`."""
    fpath_raw = getattr(args, "file", None)
    if not fpath_raw:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file argument")

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, _, sdir, sid = ctx

    fpath = Path(fpath_raw)
    if not fpath.is_absolute() and executor:
        fpath = (executor.workspace_root / fpath).resolve()
    if not fpath.is_file():
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"File not found: {fpath_raw}")

    try:
        content = fpath.read_bytes()
    except OSError as e:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Failed to read file: {e}")

    if not content:
        return _err(4, "CORRUPT_SOURCE_BLOB", f"Source file '{fpath.name}' is empty or corrupt.")

    sha256_hex = hashlib.sha256(content).hexdigest()
    sdir = session_mgr.get_session_dir(sid)
    cas = session_mgr.get_cas(sid)
    cas.put(content)

    do_not_send = bool(
        getattr(args, "do_not_send_to_agent", False) or getattr(args, "confidential", False)
    )

    lock_file = sdir / ".session.lock"
    with studio_lock(lock_file, level=L5_SESSION, session_id=sid):
        manifest_file = sdir / "inputs" / "manifest.json"
        manifest: dict[str, Any] = {"session_id": sid, "files": [], "sources": []}
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as err_man:
                return _err(
                    4, "CORRUPT_SESSION_STATE", f"Failed to parse inputs manifest.json: {err_man}"
                )

        existing = manifest.get("files", []) or manifest.get("sources", [])
        files = [f for f in existing if f.get("sha256") != sha256_hex]
        source_entry = {
            "source_id": sha256_hex,
            "source_sha256": sha256_hex,
            "file_name": fpath.name,
            "filename": fpath.name,
            "sha256": sha256_hex,
            "size_bytes": len(content),
            "byte_count": len(content),
            "mime_type": "application/octet-stream",
            "do_not_send_to_agent": do_not_send,
            "uploaded_at": datetime.now(UTC).isoformat(),
        }
        files.append(source_entry)
        manifest["files"] = files
        manifest["sources"] = files
        manifest["total_bytes"] = sum(f.get("size_bytes", 0) for f in files)

        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_m = manifest_file.parent / f".manifest.{uuid.uuid4().hex[:8]}.tmp"
        tmp_m.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        set_owner_only_permissions(tmp_m)
        os.replace(tmp_m, manifest_file)
        set_owner_only_permissions(manifest_file)

    return 0, source_entry


def handle_parse(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """6. `opendv ontology studio parse -s <session>` (CRIT-01)."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, _, sdir, sid = ctx

    manifest_file = sdir / "inputs" / "manifest.json"
    files: list[dict[str, Any]] = []
    if manifest_file.is_file():
        try:
            mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
            files = mdata.get("files", []) or mdata.get("sources", [])
        except (json.JSONDecodeError, OSError) as err_man:
            return _err(
                4, "CORRUPT_SESSION_STATE", f"Failed to parse inputs manifest.json: {err_man}"
            )

    cas = session_mgr.get_cas(sid)
    ingestion_service = IngestionService(
        adapters=[
            MarkdownDocumentAdapter(),
            DelimitedTabularAdapter(),
            PyArrowTabularAdapter(),
            StructuredDataDocumentAdapter(),
            MarkupDocumentAdapter(),
            PlainTextDocumentAdapter(),
        ]
    )

    parsed_chunks: list[EvidenceChunk] = []
    parsed_tables: list[TableStructure] = []
    diagnostics: list[str] = []

    for f in files:
        sha = f.get("sha256") or f.get("source_sha256", "")
        fn = f.get("filename") or f.get("file_name", "document.txt")
        try:
            raw_bytes = cas.get(sha)
            res = ingestion_service.ingest_bytes(data=raw_bytes, filename=fn)
            parsed_chunks.extend(res.chunks)
            if hasattr(res, "tables") and res.tables:
                parsed_tables.extend(res.tables)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            diagnostics.append(f"Failed to ingest source {fn} ({sha[:8]}): {exc}")

    lock_file = sdir / ".session.lock"
    with studio_lock(lock_file, level=L5_SESSION, session_id=sid):
        evidence_dir = sdir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        chunks_file = evidence_dir / "chunks.jsonl"
        tmp_chunks = evidence_dir / f".chunks.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp_chunks, "w", encoding="utf-8") as fp:
            for c in parsed_chunks:
                fp.write(json.dumps(c.model_dump(mode="json", exclude_none=True)) + "\n")
        set_owner_only_permissions(tmp_chunks)
        os.replace(tmp_chunks, chunks_file)
        set_owner_only_permissions(chunks_file)

        if parsed_tables:
            tables_file = evidence_dir / "tables.jsonl"
            tmp_tables = evidence_dir / f".tables.{uuid.uuid4().hex[:8]}.tmp"
            with open(tmp_tables, "w", encoding="utf-8") as fp:
                for t in parsed_tables:
                    fp.write(json.dumps(t.model_dump(mode="json", exclude_none=True)) + "\n")
            set_owner_only_permissions(tmp_tables)
            os.replace(tmp_tables, tables_file)
            set_owner_only_permissions(tables_file)

    return 0, {
        "session_id": sid,
        "status": "COMPLETED",
        "parsed_sources_count": len(files),
        "chunks_count": len(parsed_chunks),
        "tables_count": len(parsed_tables),
        "stage": "PARSED",
        "diagnostics": diagnostics,
    }


def handle_clarify(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """7. `opendv ontology studio clarify -s <session>` (CRIT-01)."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, _, sdir, sid = ctx

    chunks_file = sdir / "evidence" / "chunks.jsonl"
    chunks: list[EvidenceChunk] = []
    if chunks_file.is_file():
        for line in chunks_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(Exception):
                    chunks.append(EvidenceChunk.model_validate_json(line))

    harness = ClarificationHarness(session_dir=sdir)
    max_q = int(getattr(args, "max_questions", 5) or 5)
    questions = harness.generate_questions(
        candidate_terms=None,
        evidence_chunks=chunks,
        max_questions=max_q,
    )

    q_file = sdir / "clarifications" / "questions.json"
    q_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_q = q_file.parent / f".questions.{uuid.uuid4().hex[:8]}.tmp"
    q_dicts = [q.model_dump(mode="json", exclude_none=True) for q in questions]
    tmp_q.write_text(json.dumps(q_dicts, indent=2), encoding="utf-8")
    set_owner_only_permissions(tmp_q)
    os.replace(tmp_q, q_file)
    set_owner_only_permissions(q_file)

    return 0, {
        "session_id": sid,
        "status": "COMPLETED",
        "questions_count": len(questions),
        "questions": q_dicts,
    }


def handle_answer(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """8. `opendv ontology studio answer -s <session> -f <file>`."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, _, sdir, sid = ctx
    ans_file = getattr(args, "file", None)
    if ans_file:
        fpath = Path(ans_file)
        if not fpath.is_absolute() and executor:
            fpath = (executor.workspace_root / fpath).resolve()
        if not fpath.is_file():
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Answers file not found: {ans_file}")
        try:
            loaded_answers = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed answers JSON: {e}")
    else:
        loaded_answers = None

    target_ans = sdir / "clarifications" / "answers.json"
    target_ans.parent.mkdir(parents=True, exist_ok=True)
    lock_file = sdir / ".session.lock"

    with studio_lock(lock_file, level=L5_SESSION, session_id=sid):
        # Load existing answers
        answers_dict: dict[str, Any] = {}
        if target_ans.is_file():
            try:
                raw = json.loads(target_ans.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    answers_dict = raw
                elif isinstance(raw, list):
                    for idx, item in enumerate(raw):
                        if isinstance(item, dict):
                            answers_dict[item.get("question_id", str(idx))] = item
            except (json.JSONDecodeError, OSError) as e:
                return _err(
                    1,
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt answers record in session '{sid}': {e}",
                )

        if isinstance(loaded_answers, dict):
            answers_dict.update(loaded_answers)
        elif isinstance(loaded_answers, list):
            for idx, item in enumerate(loaded_answers):
                if isinstance(item, dict):
                    answers_dict[item.get("question_id", str(idx))] = item

        # CLI flag overrides
        qid = getattr(args, "question_id", None) or getattr(args, "question", None)
        oid = getattr(args, "option_id", None) or getattr(args, "option", None)
        ftext = getattr(args, "text", None) or getattr(args, "free_text", None)
        if qid:
            entry = answers_dict.get(qid, {"question_id": qid})
            if oid:
                entry["selected_option_ids"] = [oid] if isinstance(oid, str) else oid
            if ftext:
                entry["free_text"] = ftext
            answers_dict[qid] = entry

        out_bytes = json.dumps(answers_dict, indent=2).encode("utf-8")
        tmp_ans = target_ans.parent / f".answers.{uuid.uuid4().hex[:8]}.tmp"
        tmp_ans.write_bytes(out_bytes)
        set_owner_only_permissions(tmp_ans)
        os.replace(tmp_ans, target_ans)
        set_owner_only_permissions(target_ans)
        answers_sha = hashlib.sha256(out_bytes).hexdigest()

    return 0, {
        "session_id": sid,
        "status": "ACCEPTED",
        "answers_recorded": len(answers_dict),
        "answers_sha256": answers_sha,
    }


def handle_construct(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """9. `opendv ontology studio construct -s <session>` (CRIT-01)."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, meta, sdir, sid = ctx

    auto_commit = bool(getattr(args, "auto_commit", False))
    mode = getattr(args, "mode", "auto") or "auto"
    base_ver = getattr(args, "base_version", None)
    base_sha = getattr(args, "base_sha256", None)

    if auto_commit:
        if mode != "deterministic" or not base_ver or not base_sha:
            return _err(
                2,
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "--auto-commit requires --mode deterministic, --base-version, and --base-sha256",
            )
        clean_sha = base_sha.strip('"')
        clean_base_ver = base_ver.lstrip("v")
        head_ver = meta.head_version.lstrip("v")
        if clean_base_ver != head_ver or clean_sha != meta.head_sha256:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Precondition mismatch: base_version='{base_ver}' (head='{meta.head_version}'), "
                f"base_sha='{base_sha}' (head='{meta.head_sha256}')",
            )

    # 1. Load base ontology
    if base_ver and not _VERSION_PATTERN.match(str(base_ver)):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid --base-version format: '{base_ver}'")

    active_version = (base_ver or meta.head_version).lstrip("v")
    vdir = (sdir / "versions" / f"v{active_version}").resolve()
    if not vdir.is_relative_to(sdir.resolve()):
        return _err(
            2, "INVALID_WORKSPACE_PATH", f"Base version path traversal detected: '{base_ver}'"
        )
    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"

    if base_ver and (not vdir.is_dir() or not ont_file.is_file()):
        return _err(
            2, "SYNTAX_INVALID_ARGUMENT", f"Specified --base-version does not exist: '{base_ver}'"
        )

    if ont_file.is_file():
        try:
            if ont_file.suffix == ".json":
                base_ontology = GraphAOntology.model_validate_json(
                    ont_file.read_text(encoding="utf-8")
                )
            else:
                base_ontology = GraphAOntology.model_validate(
                    yaml.safe_load(ont_file.read_text(encoding="utf-8"))
                )
        except (ValidationError, yaml.YAMLError, OSError, ValueError) as exc:
            return _err(4, "CORRUPT_SESSION_STATE", f"Failed to load base ontology artifact: {exc}")
    else:
        base_ontology = GraphAOntology(
            domain_name=meta.domain_namespace or "default",
            concepts={},
        )

    # Compute actual base ontology SHA
    base_onto_sha = (
        compute_ontology_sha256(base_ontology) if ont_file.is_file() else meta.head_sha256
    )

    # 2. Load evidence chunks & tables
    chunks_file = sdir / "evidence" / "chunks.jsonl"
    chunks: list[EvidenceChunk] = []
    if chunks_file.is_file():
        for line in chunks_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(
                    ValidationError, json.JSONDecodeError, ValueError, TypeError
                ):
                    chunks.append(EvidenceChunk.model_validate_json(line))

    tables_file = sdir / "evidence" / "tables.jsonl"
    tables: list[TableStructure] = []
    if tables_file.is_file():
        for line in tables_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(
                    ValidationError, json.JSONDecodeError, ValueError, TypeError
                ):
                    tables.append(TableStructure.model_validate_json(line))

    # 3. Load clarification answers
    answers_file = sdir / "clarifications" / "answers.json"
    answers_dict: dict[str, Any] = {}
    if answers_file.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError, ValueError):
            loaded_ans = json.loads(answers_file.read_text(encoding="utf-8"))
            if isinstance(loaded_ans, dict):
                answers_dict = loaded_ans
            elif isinstance(loaded_ans, list):
                for idx, item in enumerate(loaded_ans):
                    if isinstance(item, dict):
                        answers_dict[item.get("question_id", str(idx))] = item

    pipeline = DeterministicConstructionPipeline()
    opts = ConstructOptions(auto_commit=False, if_match=None)
    try:
        proposal = pipeline.construct_proposal(
            base_ontology=base_ontology,
            evidence_chunks=chunks,
            tables=tables,
            clarification_answers=answers_dict,
            options=opts,
        )
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        return _err(4, "CONSTRUCTION_FAILED", f"Deterministic construction pipeline failed: {exc}")

    proposal_ops = (
        proposal.delta.operations
        if hasattr(proposal, "delta") and hasattr(proposal.delta, "operations")
        else getattr(proposal, "operations", [])
    )

    # Save proposal to disk atomically
    props_dir = sdir / "proposals"
    props_dir.mkdir(parents=True, exist_ok=True)
    prop_id = getattr(proposal, "proposal_id", None) or f"prop-{uuid.uuid4().hex[:8]}"
    pfile = props_dir / f"{prop_id}.json"
    if pfile.is_file():
        with contextlib.suppress(Exception):
            existing = json.loads(pfile.read_text(encoding="utf-8"))
            if existing.get("status") in ("ACCEPTED", "REJECTED"):
                return _err(
                    6,
                    "PROPOSAL_ALREADY_RESOLVED",
                    f"Proposal '{prop_id}' is already {existing.get('status')}.",
                )

    prop_data = {
        "proposal_id": prop_id,
        "session_id": sid,
        "base_version": base_ver or meta.head_version,
        "base_sha256": base_onto_sha,
        "status": "PENDING",
        "operations": [
            op.model_dump(mode="json", exclude_none=True) if hasattr(op, "model_dump") else op
            for op in proposal_ops
        ],
        "rationale": getattr(proposal, "rationale", "Deterministic pipeline construction"),
        "proposer": "system:deterministic",
        "created_at": datetime.now(UTC).isoformat(),
    }
    _atomic_write_json(pfile, prop_data)

    if auto_commit:
        if not proposal_ops:
            return _err(
                4, "ONT_EVO_CONTRACT_VIOLATION", "Cannot auto-commit proposal with zero operations."
            )

        # Check grounding: low-confidence mappings require manual steward review and waiver (MAJ-01)
        ungrounded = _collect_ungrounded_concepts(proposal_ops)
        if ungrounded:
            return _err(
                4,
                "ONT_EVO_LOW_CONFIDENCE_GROUNDING",
                f"Proposal has low-confidence grounding for concepts: {sorted(ungrounded)} "
                "and requires human review.",
            )

        is_genesis = meta.head_version == "0.0.0"
        parent_sha = "" if is_genesis else meta.head_sha256
        temp_delta = (
            proposal.delta
            if hasattr(proposal, "delta")
            else OntologyDelta.model_construct(
                delta_id=f"delta-{uuid.uuid4().hex[:8]}",
                parent_sha256=parent_sha,
                base_version=meta.head_version,
                target_version=None,
                operations=proposal_ops,
            )
        )
        base_ont = _load_session_base_ontology(sdir, meta.head_version)
        try:
            target_ver = compute_next_semver(meta.head_version, temp_delta, base_ontology=base_ont)
        except (
            ValueError,
            TypeError,
            KeyError,
            SemVerCalculusError,
            ValidationError,
            AttributeError,
        ) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")

        delta_obj = OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256=parent_sha,
            base_version=meta.head_version,
            target_version=target_ver,
            operations=proposal_ops,
            author="system:deterministic",
            rationale=prop_data["rationale"],
        )

        commit_svc = AtomicCommitService(session_manager=session_mgr)
        try:
            res = commit_svc.commit_delta(
                session_id=sid,
                delta=delta_obj,
                proposal_id=prop_id,
                author="system:deterministic",
                if_match=meta.head_sha256,
            )
        except ConcurrentModificationError as err_occ:
            return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            OSError,
        ) as err_commit:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", str(err_commit))

        prop_data["status"] = "ACCEPTED"
        _atomic_write_json(pfile, prop_data)

        return 0, {
            "session_id": sid,
            "status": "COMMITTED",
            "committed": True,
            "proposal_id": prop_id,
            "target_version": res.version,
            "target_sha256": res.target_sha256,
            "parent_sha256": res.parent_sha256,
        }

    return 0, {
        "session_id": sid,
        "proposal_id": prop_id,
        "status": "PENDING",
        "base_version": prop_data["base_version"],
        "base_sha256": prop_data["base_sha256"],
        "operations_count": len(proposal_ops),
        "proposer": "system:deterministic",
    }


def handle_delta_validate(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """10. `opendv ontology studio delta validate -s <session> -f <delta_file>` (MAJ-06)."""
    fpath_raw = getattr(args, "file", None) or getattr(args, "delta", None)
    if not fpath_raw:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")

    fpath = Path(fpath_raw)
    if not fpath.is_absolute() and executor:
        fpath = (executor.workspace_root / fpath).resolve()
    if not fpath.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {fpath_raw}")

    try:
        delta_dict = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err_json:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed delta JSON: {err_json}")

    sid = getattr(args, "session", None)
    base_file_arg = getattr(args, "base", None) or getattr(args, "base_file", None)
    base_ver_arg = getattr(args, "base_version", None)
    meta = None
    sdir = None
    base_ontology: GraphAOntology | None = None

    if sid:
        err = _validate_session(sid)
        if err:
            return err
        session_mgr = _get_session_manager(executor)
        try:
            meta = session_mgr.get_session(sid)
            sdir = session_mgr.get_session_dir(sid)
        except SessionNotFoundError:
            return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")
    elif not base_file_arg:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Either --session or --base must be provided for delta validation.",
        )

    # 1. Resolve base ontology artifact, base_version, and expected_parent_sha
    expected_parent_sha: str = ""
    if base_file_arg:
        base_path = Path(base_file_arg)
        if not base_path.is_absolute() and executor:
            base_path = (executor.workspace_root / base_path).resolve()
        if not base_path.is_file():
            return _err(3, "FILE_NOT_FOUND", f"Base ontology file not found: '{base_file_arg}'")
        try:
            raw_text = base_path.read_text(encoding="utf-8")
            if base_path.suffix == ".json":
                base_ontology = GraphAOntology.model_validate_json(raw_text)
            else:
                base_ontology = GraphAOntology.model_validate(yaml.safe_load(raw_text))
            base_ver = base_ontology.ontology_version
            expected_parent_sha = compute_ontology_sha256(base_ontology)
        except (OSError, yaml.YAMLError, ValidationError, ValueError, TypeError) as exc:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Failed to parse base ontology file '{base_file_arg}': {exc}",
            )
    elif base_ver_arg and sdir:
        base_ver = base_ver_arg
        if base_ver not in ("0.0.0", "", None):
            base_ontology = _load_session_base_ontology(sdir, base_ver)
            if base_ontology is None:
                return _err(3, "VERSION_NOT_FOUND", f"Base version artifact missing: '{base_ver}'")
            expected_parent_sha = compute_ontology_sha256(base_ontology)
        else:
            base_ver = "0.0.0"
            expected_parent_sha = ""
    elif sdir and meta:
        base_ver = meta.head_version
        if base_ver in ("0.0.0", "", None):
            base_ver = "0.0.0"
            expected_parent_sha = ""
            base_ontology = None
        else:
            base_ontology = _load_session_base_ontology(sdir, base_ver)
            expected_parent_sha = meta.head_sha256
    else:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Either --session or --base must be provided for delta validation.",
        )

    raw_ops = delta_dict.get("operations", [])
    try:
        norm_ops = [_normalize_delta_operation(op) for op in raw_ops]
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return 4, {
            "valid": False,
            "status": "ERROR",
            "error_code": "ONT_EVO_CONTRACT_VIOLATION",
            "detail": f"Malformed operation in delta: {exc}",
            "violations": [str(exc)],
            "diagnostics": [],
        }

    violations: list[str] = []

    # Grounding check (fail-closed when verify_grounding enabled, or if waiver requested)
    if getattr(args, "verify_grounding", False):
        ungrounded = _collect_ungrounded_concepts(norm_ops)
        if ungrounded and not getattr(args, "low_confidence_waiver", False):
            violations.append(
                "Operations contain low-confidence mappings for concepts: "
                f"{sorted(ungrounded)} and require an administrative waiver."
            )

    # 1. Structural & Schema Delta Construction Validation
    delta_obj: OntologyDelta | None = None
    tgt_ver = delta_dict.get("target_version")
    parent_sha = delta_dict.get("parent_sha256") or expected_parent_sha
    try:
        delta_obj = OntologyDelta(
            delta_id=delta_dict.get("delta_id", f"delta-{uuid.uuid4().hex[:8]}"),
            parent_sha256=parent_sha,
            base_version=base_ver,
            target_version=tgt_ver,
            operations=norm_ops,
            author=delta_dict.get("author", "validator"),
            rationale=delta_dict.get("rationale"),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        violations.append(f"Delta structural validation failure: {exc}")

    # 2. Lineage Precondition Validation
    parent_given = str(delta_dict.get("parent_sha256") or "").strip()
    if base_ver == "0.0.0":
        if parent_given not in ("", None):
            violations.append("Genesis delta parent_sha256 must be empty")
    else:
        if parent_given and parent_given != expected_parent_sha:
            violations.append(
                f"parent_sha256 '{parent_given}' does not match expected base SHA "
                f"'{expected_parent_sha}' for base version '{base_ver}'"
            )

    # 3. SemVer Bump and Semantic Concept Conflict Checks
    req_tgt = delta_dict.get("target_version")
    if req_tgt and delta_obj is not None:
        try:
            expected_next = compute_next_semver(
                base_version=base_ver, delta=delta_obj, base_ontology=base_ontology
            )
            if req_tgt.lstrip("v") != expected_next.lstrip("v"):
                violations.append(
                    f"target_version '{req_tgt}' does not match required semver bump "
                    f"'{expected_next}'."
                )
        except (ValueError, TypeError, KeyError, SemVerCalculusError, ValidationError) as exc:
            violations.append(f"SemVer bump computation failure: {exc}")

    if violations:
        return 4, {
            "valid": False,
            "error_code": "ONT_EVO_CONTRACT_VIOLATION",
            "violations": violations,
            "diagnostics": [],
        }

    return 0, {
        "valid": True,
        "error_code": None,
        "violations": [],
        "diagnostics": [
            f"Validation complete: {len(norm_ops)} operations evaluated, 0 violations detected."
        ],
    }


def handle_delta_apply(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """11. `opendv ontology studio delta apply -s <session> -f <delta_file>` (MAJ-07, MAJ-10)."""
    fpath_raw = getattr(args, "file", None)
    if not fpath_raw:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")

    fpath = Path(fpath_raw)
    if not fpath.is_absolute() and executor:
        fpath = (executor.workspace_root / fpath).resolve()
    if not fpath.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {fpath_raw}")

    try:
        delta_dict = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err_json:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed delta JSON: {err_json}")

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, meta, sdir, sid = ctx

    # Mandatory base_sha256 OCC check (MAJ-05)
    base_sha = getattr(args, "base_sha256", None) or delta_dict.get("parent_sha256")
    is_genesis = meta.head_version in ("0.0.0", "", None)
    clean_base_sha = base_sha.strip('"') if base_sha else ""

    if not clean_base_sha and not is_genesis:
        return _err(
            2,
            "MISSING_PRECONDITION",
            "Missing mandatory --base-sha256 precondition for delta apply.",
        )

    if clean_base_sha and clean_base_sha != meta.head_sha256:
        return _err(
            5,
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Base SHA mismatch: expected {meta.head_sha256}, got {clean_base_sha}",
        )

    raw_ops = delta_dict.get("operations", [])
    if not raw_ops:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", "Cannot apply delta with zero operations.")

    try:
        norm_ops = [_normalize_delta_operation(op) for op in raw_ops]
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid delta operation: {exc}")

    studio_root = _get_studio_root(executor)
    waiver_res = _verify_grounding_and_waiver(
        norm_ops=norm_ops,
        args=args,
        head_sha256=meta.head_sha256,
        proposal_id=None,
        executor=executor,
        studio_root=studio_root,
    )
    if isinstance(waiver_res, tuple):
        return waiver_res
    waiver_record = waiver_res

    temp_delta = OntologyDelta.model_construct(
        delta_id=f"delta-{uuid.uuid4().hex[:8]}",
        parent_sha256="" if meta.head_version == "0.0.0" else meta.head_sha256,
        base_version=meta.head_version,
        target_version=None,
        operations=norm_ops,
    )
    base_ont = _load_session_base_ontology(sdir, meta.head_version)
    try:
        target_version = compute_next_semver(meta.head_version, temp_delta, base_ontology=base_ont)
    except (
        ValueError,
        TypeError,
        KeyError,
        SemVerCalculusError,
        ValidationError,
        AttributeError,
    ) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")
    parent_sha = "" if meta.head_version == "0.0.0" else meta.head_sha256

    user_target = delta_dict.get("target_version")
    if (
        user_target
        and isinstance(user_target, str)
        and user_target.lstrip("v") != target_version.lstrip("v")
    ):
        return _err(
            4,
            "ONT_EVO_CONTRACT_VIOLATION",
            f"User target_version '{user_target}' does not match required semver bump "
            f"'{target_version}'.",
        )

    try:
        delta_obj = OntologyDelta(
            delta_id=delta_dict.get("delta_id", f"delta-{uuid.uuid4().hex[:12]}"),
            parent_sha256=parent_sha,
            base_version=meta.head_version,
            target_version=target_version,
            operations=norm_ops,
            author=delta_dict.get("author", "steward"),
            rationale=getattr(args, "rationale", None)
            or delta_dict.get("rationale", "Direct delta apply"),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Delta construction failure: {exc}")

    eval_dict = (
        {"waiver": waiver_record.model_dump(mode="json", exclude_none=True)}
        if waiver_record
        else None
    )
    commit_svc = AtomicCommitService(session_manager=session_mgr)
    try:
        res = commit_svc.commit_delta(
            session_id=sid,
            delta=delta_obj,
            author="steward",
            if_match=clean_base_sha if clean_base_sha else meta.head_sha256,
            evaluation=eval_dict,
        )
    except ConcurrentModificationError as err_occ:
        return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
    except (
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
        OSError,
    ) as err_commit:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", str(err_commit))

    bump_type = (
        "major"
        if target_version.split(".")[0] != meta.head_version.split(".")[0]
        else (
            "minor" if target_version.split(".")[1] != meta.head_version.split(".")[1] else "patch"
        )
    )

    return 0, {
        "session_id": sid,
        "target_version": res.version,
        "target_sha256": res.target_sha256,
        "parent_sha256": res.parent_sha256,
        "delta_id": delta_obj.delta_id,
        "bump_type": bump_type,
        "applied_operations_count": len(norm_ops),
    }


def handle_proposal_list(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """12. `opendv ontology studio proposal list -s <session>`."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, _, sdir, _ = ctx

    props_dir = sdir / "proposals"
    results: list[dict[str, Any]] = []
    if props_dir.exists():
        for pfile in sorted(props_dir.glob("*.json")):
            with contextlib.suppress(json.JSONDecodeError, OSError):
                results.append(json.loads(pfile.read_text(encoding="utf-8")))
    return 0, results


def handle_proposal_show(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """13. `opendv ontology studio proposal show -s <session> -p <proposal_id>` (MAJ-01)."""
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, _, sdir, _ = ctx

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    if not pfile.is_file():
        return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

    try:
        return 0, json.loads(pfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return _err(4, "CORRUPT_PROPOSAL_FILE", f"Failed to read proposal JSON: {e}")


def handle_proposal_accept(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """14. `opendv ontology studio proposal accept` (CRIT-01, MAJ-01, MAJ-02, MAJ-03, MAJ-05)."""
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    base_sha = getattr(args, "base_sha256", None)
    if not pid or base_sha is None:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing --proposal-id or --base-sha256")

    if bool(getattr(args, "low_confidence_waiver", False)):
        waiver_rat = getattr(args, "waiver_rationale", None) or getattr(
            args, "grounding_rationale", None
        )
        if not waiver_rat or len(waiver_rat.strip()) < 10:
            return _err(
                4,
                "INVALID_GROUNDING_WAIVER",
                "Administrative grounding waiver requires a rationale of at least 10 characters.",
            )
        studio_root = _get_studio_root(executor)
        key_res = _load_and_verify_waiver_credentials(args, executor, studio_root)
        if isinstance(key_res, tuple):
            return key_res

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, meta, sdir, sid = ctx

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    lock_file = sdir / ".session.lock"
    with studio_lock(lock_file, level=L5_SESSION, session_id=sid):
        meta = session_mgr.get_session(sid)
        if not pfile.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

        try:
            prop_data = json.loads(pfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return _err(4, "CORRUPT_PROPOSAL_FILE", f"Failed to read proposal JSON: {e}")

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(
                6,
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{pid}' has already been resolved ({prop_data.get('status')}).",
            )

        # OCC precondition check: compare caller's base_sha256 with head (HTTP If-Match semantics)
        clean_base_sha = base_sha.strip('"')
        is_genesis_occ = meta.head_version in ("0.0.0", "", None) and clean_base_sha == ""
        if clean_base_sha != meta.head_sha256 and not is_genesis_occ:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Base SHA-256 mismatch: expected {meta.head_sha256}, got {base_sha}",
            )

        prop_base = prop_data.get("base_sha256") or ""
        is_genesis = meta.head_version in ("0.0.0", "", None) and (
            prop_base == "" or clean_base_sha == ""
        )

        # Stale proposal check (MAJ-05): proposal.base_sha256 must match head (unless genesis)
        if prop_base != meta.head_sha256 and not is_genesis:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Proposal base SHA ('{prop_base}') is stale; rebase onto head "
                f"'{meta.head_sha256}' is required before acceptance.",
            )

        raw_ops = prop_data.get("operations", [])
        try:
            norm_ops = [_normalize_delta_operation(op) for op in raw_ops]
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}")

        studio_root = _get_studio_root(executor)
        waiver_res = _verify_grounding_and_waiver(
            norm_ops=norm_ops,
            args=args,
            head_sha256=meta.head_sha256,
            proposal_id=pid,
            executor=executor,
            studio_root=studio_root,
        )
        if isinstance(waiver_res, tuple):
            return waiver_res
        waiver_record = waiver_res

        temp_delta = OntologyDelta.model_construct(
            delta_id=f"delta-{uuid.uuid4().hex[:8]}",
            parent_sha256="" if meta.head_version == "0.0.0" else meta.head_sha256,
            base_version=meta.head_version,
            target_version=None,
            operations=norm_ops,
        )
        base_ont = _load_session_base_ontology(sdir, meta.head_version)
        try:
            next_ver = compute_next_semver(meta.head_version, temp_delta, base_ontology=base_ont)
        except (
            ValueError,
            TypeError,
            KeyError,
            SemVerCalculusError,
            ValidationError,
            AttributeError,
        ) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")
        parent_sha = "" if meta.head_version == "0.0.0" else meta.head_sha256

        try:
            delta_obj = OntologyDelta(
                delta_id=pid,
                parent_sha256=parent_sha,
                base_version=meta.head_version,
                target_version=next_ver,
                operations=norm_ops,
                author="steward",
                rationale=prop_data.get("rationale")
                or getattr(args, "waiver_rationale", None)
                or getattr(args, "grounding_rationale", None)
                or "Accepted via opendv ontology studio proposal accept",
            )
        except (ValidationError, ValueError, TypeError) as exc:
            return _err(
                4, "ONT_EVO_CONTRACT_VIOLATION", f"Proposal delta construction failure: {exc}"
            )

        eval_dict = (
            {"waiver": waiver_record.model_dump(mode="json", exclude_none=True)}
            if waiver_record
            else None
        )
        commit_svc = AtomicCommitService(session_manager=session_mgr)
        try:
            res = commit_svc.commit_delta(
                session_id=sid,
                delta=delta_obj,
                proposal_id=pid,
                author="steward",
                if_match=meta.head_sha256,
                evaluation=eval_dict,
            )
        except ConcurrentModificationError as err_occ:
            if "already been resolved" in str(err_occ):
                return _err(6, "PROPOSAL_ALREADY_RESOLVED", str(err_occ))
            return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            OSError,
        ) as err_commit:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", str(err_commit))

        prop_data["status"] = "ACCEPTED"
        prop_data["accepted_at"] = datetime.now(UTC).isoformat()
        prop_data["commit_sha256"] = res.target_sha256
        _atomic_write_json(pfile, prop_data)

        bump_type = (
            "major"
            if res.version.split(".")[0] != meta.head_version.split(".")[0]
            else (
                "minor" if res.version.split(".")[1] != meta.head_version.split(".")[1] else "patch"
            )
        )

        return 0, {
            "session_id": sid,
            "target_version": res.version,
            "target_sha256": res.target_sha256,
            "parent_sha256": res.parent_sha256,
            "delta_id": pid,
            "proposal_id": pid,
            "bump_type": bump_type,
            "applied_operations_count": len(norm_ops),
        }


def handle_proposal_reject(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """15. `opendv ontology studio proposal reject` (MAJ-01)."""
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, _, sdir, sid = ctx

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    lock_file = sdir / ".session.lock"
    with studio_lock(lock_file, level=L5_SESSION, session_id=sid):
        if not pfile.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

        try:
            prop_data = json.loads(pfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return _err(4, "CORRUPT_PROPOSAL_FILE", f"Failed to read proposal JSON: {e}")

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(
                6,
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{pid}' has already been resolved ({prop_data.get('status')}).",
            )

        reason = getattr(args, "reason", None) or "Rejected by steward"
        prop_data["status"] = "REJECTED"
        prop_data["rejection_reason"] = reason

        tmp_p = sdir / "proposals" / f".{pid}.reject.tmp"
        tmp_p.write_text(json.dumps(prop_data, indent=2), encoding="utf-8")
        set_owner_only_permissions(tmp_p)
        os.replace(tmp_p, pfile)
        set_owner_only_permissions(pfile)

    return 0, {
        "proposal_id": pid,
        "status": "REJECTED",
        "reason": reason,
    }


def handle_proposal_rebase(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """16. `opendv ontology studio proposal rebase` (MAJ-01, MAJ-05)."""
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    base_sha = getattr(args, "base_sha256", None) or getattr(args, "head_sha256", None)
    if not pid or not base_sha:
        return _err(
            2, "SYNTAX_INVALID_ARGUMENT", "Missing --proposal-id or --base-sha256 / --head-sha256"
        )

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, meta, sdir, sid = ctx

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    lock_file = sdir / ".session.lock"
    with studio_lock(lock_file, level=L5_SESSION, session_id=sid):
        meta = session_mgr.get_session(sid)
        if not pfile.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

        try:
            prop_data = json.loads(pfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return _err(4, "CORRUPT_PROPOSAL_FILE", f"Failed to read proposal JSON: {e}")

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(
                6,
                "PROPOSAL_ALREADY_RESOLVED",
                f"Cannot rebase proposal '{pid}': status is {prop_data.get('status')}",
            )

        clean_sha = base_sha.strip('"')
        if clean_sha != meta.head_sha256:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Base SHA mismatch: expected {meta.head_sha256}, got {clean_sha}",
            )

        raw_ops = prop_data.get("operations", [])
        try:
            ops = [_normalize_delta_operation(op) for op in raw_ops]
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}")

        # Semantic conflict check against head ontology
        if meta.head_version not in ("0.0.0", "", None):
            head_ver = meta.head_version.lstrip("v")
            head_ont_file = sdir / "versions" / f"v{head_ver}" / "ontology.json"
            if not head_ont_file.is_file():
                head_ont_file = sdir / "versions" / f"v{head_ver}" / "ontology.yaml"
            if head_ont_file.is_file():
                try:
                    if head_ont_file.suffix == ".json":
                        head_data = json.loads(head_ont_file.read_text(encoding="utf-8"))
                    else:
                        head_data = yaml.safe_load(head_ont_file.read_text(encoding="utf-8")) or {}
                    head_concepts: set[str] = set()
                    raw_concepts = head_data.get("concepts")
                    if isinstance(raw_concepts, dict):
                        for k, v in raw_concepts.items():
                            head_concepts.add(str(k))
                            if isinstance(v, dict):
                                u = v.get("concept_uri") or v.get("uri") or v.get("id")
                                if u:
                                    head_concepts.add(str(u))
                    elif isinstance(raw_concepts, list):
                        for c in raw_concepts:
                            if isinstance(c, dict):
                                u = c.get("concept_uri") or c.get("uri") or c.get("id")
                                if u:
                                    head_concepts.add(str(u))
                            elif isinstance(c, str):
                                head_concepts.add(c)
                    for c in head_data.get("canonical_business_concepts", []):
                        if isinstance(c, dict):
                            u = c.get("concept_uri") or c.get("uri") or c.get("id")
                            if u:
                                head_concepts.add(str(u))
                        elif isinstance(c, str):
                            head_concepts.add(c)
                    for op in ops:
                        op_type = getattr(op, "op_type", None) or (
                            op.get("op_type") if isinstance(op, dict) else None
                        )
                        if op_type in ("add_concept", "AddConcept", "AddConceptOp"):
                            c_uri = getattr(op, "concept_uri", None) or (
                                op.get("concept_uri") if isinstance(op, dict) else None
                            )
                            if c_uri and str(c_uri) in head_concepts:
                                return _err(
                                    4,
                                    "ONT_EVO_CONTRACT_VIOLATION",
                                    f"Rebase conflict: concept '{c_uri}' already exists in head "
                                    f"ontology version {meta.head_version}.",
                                )
                except (OSError, json.JSONDecodeError, yaml.YAMLError):
                    pass

        # Validate structural integrity of delta operations against new head
        temp_delta = OntologyDelta.model_construct(
            delta_id=f"delta-{uuid.uuid4().hex[:8]}",
            parent_sha256="" if meta.head_version in ("0.0.0", "", None) else meta.head_sha256,
            base_version=meta.head_version or "0.0.0",
            target_version=None,
            operations=ops,
        )
        base_ont = _load_session_base_ontology(sdir, meta.head_version)
        try:
            t_ver = compute_next_semver(
                meta.head_version or "0.0.0", temp_delta, base_ontology=base_ont
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            SemVerCalculusError,
            ValidationError,
            AttributeError,
        ) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")

        try:
            OntologyDelta(
                delta_id=f"delta-{uuid.uuid4().hex[:12]}",
                parent_sha256="" if meta.head_version in ("0.0.0", "", None) else meta.head_sha256,
                base_version=meta.head_version,
                target_version=t_ver,
                operations=ops,
                author=prop_data.get("proposer", "unknown"),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            return _err(
                4,
                "ONT_EVO_CONTRACT_VIOLATION",
                f"Rebase delta structural integrity check failed: {exc}",
            )

        prop_data["base_sha256"] = meta.head_sha256
        prop_data["base_version"] = meta.head_version
        prop_data["rebased_at"] = datetime.now(UTC).isoformat()
        prop_data["status"] = "PENDING"

        _atomic_write_json(pfile, prop_data)

    return 0, {
        "proposal_id": pid,
        "status": "REBASED",
        "base_sha256": meta.head_sha256,
        "base_version": meta.head_version,
    }


def handle_export(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """17. `opendv ontology studio export -s <session>` (MAJ-01, MAJ-07)."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, meta, sdir, sid = ctx

    raw_ver = getattr(args, "version", None)
    if raw_ver:
        if not _VERSION_PATTERN.match(str(raw_ver)):
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid version identifier '{raw_ver}'.")
        ver_tag = str(raw_ver) if str(raw_ver).startswith("v") else f"v{raw_ver}"
        vdir = (sdir / "versions" / ver_tag).resolve()
    else:
        vdir = (sdir / "versions" / f"v{meta.head_version.lstrip('v')}").resolve()

    if not vdir.is_relative_to(sdir.resolve()):
        return _err(
            2, "INVALID_WORKSPACE_PATH", f"Version path escapes session directory: '{raw_ver}'"
        )

    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"

    if not ont_file.is_file():
        return _err(
            3, "VERSION_NOT_FOUND", f"Ontology artifact not found for version '{vdir.name}'"
        )

    try:
        if ont_file.suffix == ".json":
            ont = GraphAOntology.model_validate_json(ont_file.read_text(encoding="utf-8"))
        else:
            ont = GraphAOntology.model_validate(
                yaml.safe_load(ont_file.read_text(encoding="utf-8"))
            )
    except (ValidationError, yaml.YAMLError, OSError, ValueError) as exc:
        return _err(4, "CORRUPT_SESSION_STATE", f"Failed to load ontology artifact: {exc}")

    fmt = (
        getattr(args, "export_format", None) or getattr(args, "format", "yaml") or "yaml"
    ).lower()
    if fmt not in (
        "yaml",
        "yml",
        "json",
        "ttl",
        "turtle",
        "rdf",
        "owl",
        "jsonld",
        "json-ld",
        "nt",
        "ntriples",
        "xml",
    ):
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            f"Unsupported export format '{fmt}'. "
            "Supported: yaml, json, ttl, turtle, rdf, owl, jsonld.",
        )
    try:
        if fmt in ("ttl", "turtle", "rdf", "owl", "jsonld", "json-ld", "nt", "ntriples", "xml"):
            res = export_ontology(ont, export_format=fmt)
            exported_text = res[0] if isinstance(res, tuple) else str(res)
        elif fmt == "json":
            exported_text = json.dumps(ont.model_dump(mode="json", exclude_none=True), indent=2)
        else:
            exported_text = yaml.safe_dump(ont.model_dump(mode="json", exclude_none=True))
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        return _err(4, "EXPORT_FAILED", f"Ontology serialization failed: {exc}")

    out_file = getattr(args, "out", None)
    if out_file:
        out_p = Path(out_file)
        if not out_p.is_absolute() and executor:
            out_p = (executor.workspace_root / out_p).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(exported_text, encoding="utf-8")

    return 0, {
        "session_id": sid,
        "version": vdir.name,
        "format": fmt,
        "content": exported_text,
    }


def handle_promote(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """18. `opendv ontology studio promote` (MAJ-01, MAJ-05, MAJ-07)."""
    rationale = getattr(args, "rationale", None)
    if not rationale or len(rationale.strip()) < 10:
        return _err(
            1,
            "PROMOTION_RATIONALE_REQUIRED",
            "Promotion rationale of at least 10 characters is required.",
        )

    ws_sha = getattr(args, "workspace_sha256", None)
    head_sha = getattr(args, "head_sha256", None)
    if not ws_sha or not head_sha:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing --workspace-sha256 or --head-sha256")

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    session_mgr, meta, sdir, sid = ctx

    base_ws = executor.workspace_root if executor else Path.cwd().resolve()
    ws_arg = getattr(args, "workspace", None)
    target_ws = (base_ws / ws_arg).resolve() if ws_arg else base_ws.resolve()

    if not target_ws.is_relative_to(base_ws.resolve()):
        return _err(
            2,
            "INVALID_WORKSPACE_PATH",
            f"Promote target workspace escapes base workspace: '{ws_arg}'",
        )

    target_ws.mkdir(parents=True, exist_ok=True)
    target_contract = target_ws / "graph_a_ontology.yaml"
    if not target_contract.exists():
        pe_contract = target_ws / "pe_graph_a_ontology.yaml"
        if pe_contract.exists():
            target_contract = pe_contract

    w_lock = target_ws / ".workspace.lock"
    s_lock = sdir / ".session.lock"

    with multi_studio_lock(
        [
            StudioLock(w_lock, level=L4_WORKSPACE, session_id=target_ws.name),
            StudioLock(s_lock, level=L5_SESSION, session_id=sid),
        ]
    ):
        meta = session_mgr.get_session(sid)
        if meta.head_version in ("0.0.0", "", None):
            return _err(
                4,
                "INVALID_PROPOSAL_STATE",
                "Cannot promote uncommitted genesis draft (version 0.0.0). "
                "A committed version bump is required before promotion.",
            )

        if head_sha.strip('"') != meta.head_sha256:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Head SHA-256 mismatch: expected {meta.head_sha256}, got {head_sha}",
            )

        current_ws_sha = ""
        raw_ws_sha = ""
        ws_ver: str | None = None
        if target_contract.is_file():
            raw_bytes = target_contract.read_bytes()
            raw_ws_sha = hashlib.sha256(raw_bytes).hexdigest()
            try:
                ws_data = yaml.safe_load(raw_bytes.decode("utf-8"))
                if isinstance(ws_data, dict):
                    ws_ver = ws_data.get("ontology_version")
                current_ws_sha = (
                    compute_ontology_sha256(ws_data) if isinstance(ws_data, dict) else raw_ws_sha
                )
            except (
                yaml.YAMLError,
                ValueError,
                TypeError,
                OSError,
                AttributeError,
                ValidationError,
            ):
                current_ws_sha = raw_ws_sha

            clean_ws_sha = ws_sha.strip('"')
            if clean_ws_sha not in (current_ws_sha, raw_ws_sha):
                return _err(
                    5,
                    "ONT_EVO_CONCURRENT_MODIFICATION",
                    f"Workspace contract SHA mismatch: expected {current_ws_sha}, got {ws_sha}",
                )

        if ws_ver:
            try:
                head_tuple = parse_semver(meta.head_version.lstrip("v"))
                ws_tuple = parse_semver(str(ws_ver).lstrip("v"))
                if head_tuple < ws_tuple:
                    ack = getattr(args, "acknowledge_historical_contract_warning", False)
                    rat = getattr(args, "historical_contract_rationale", None)
                    if not ack:
                        return _err(
                            4,
                            "HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED",
                            f"Promoting version {meta.head_version} downgrades existing workspace "
                            f"contract version {ws_ver}. "
                            "Requires --acknowledge-historical-contract-warning.",
                        )
                    if not rat or len(rat.strip()) < 10:
                        return _err(
                            1,
                            "HISTORICAL_CONTRACT_RATIONALE_REQUIRED",
                            "Historical contract rationale of at least 10 characters is required "
                            "when acknowledging historical contract warning.",
                        )
            except (SemVerCalculusError, ValueError, TypeError):
                pass

        ver_tag = f"v{meta.head_version.lstrip('v')}"
        ont_src = sdir / "versions" / ver_tag / "ontology.yaml"
        if not ont_src.is_file():
            ont_src = sdir / "versions" / ver_tag / "ontology.json"

        if not ont_src.is_file():
            return _err(3, "VERSION_NOT_FOUND", f"Head version artifact missing: '{ver_tag}'")

        src_bytes = ont_src.read_bytes()
        try:
            if ont_src.suffix == ".json":
                src_ont = GraphAOntology.model_validate_json(src_bytes.decode("utf-8"))
            else:
                src_ont = GraphAOntology.model_validate(yaml.safe_load(src_bytes.decode("utf-8")))
            src_canonical_sha = compute_ontology_sha256(src_ont)
        except (OSError, ValueError, TypeError, yaml.YAMLError, ValidationError) as exc:
            return _err(
                4, "CORRUPT_VERSION_ARTIFACT", f"Failed to validate version artifact: {exc}"
            )
        if src_canonical_sha != meta.head_sha256:
            return _err(
                4,
                "CORRUPT_VERSION_ARTIFACT",
                f"Version artifact SHA '{src_canonical_sha}' does not match head SHA "
                f"'{meta.head_sha256}'",
            )

        tmp_contract = target_ws / f".contract.{uuid.uuid4().hex[:8]}.tmp"
        tmp_contract.write_bytes(src_bytes)
        set_owner_only_permissions(tmp_contract)
        os.replace(tmp_contract, target_contract)
        set_owner_only_permissions(target_contract)

        audit_rec = {
            "action": "PROMOTE",
            "session_id": sid,
            "promoted_version": meta.head_version,
            "head_sha256": meta.head_sha256,
            "workspace_sha256": ws_sha,
            "rationale": rationale.strip(),
            "historical_contract_acknowledged": getattr(
                args, "acknowledge_historical_contract_warning", False
            ),
            "historical_contract_rationale": getattr(args, "historical_contract_rationale", None),
            "promoted_at": datetime.now(UTC).isoformat(),
            "target_contract_path": str(target_contract),
        }
        prom_log = target_ws / ".promotion_audit.jsonl"
        with open(prom_log, "a", encoding="utf-8") as afp:
            afp.write(json.dumps(audit_rec) + "\n")
        set_owner_only_permissions(prom_log)

    return 0, {
        "session_id": sid,
        "promoted_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "workspace_sha256": ws_sha,
        "promoted_at": datetime.now(UTC).isoformat(),
        "target_contract_path": str(target_contract),
    }


def handle_bundle_export(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """19. `opendv ontology studio bundle export` (MAJ-07, MAJ-08)."""
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx
    _, _, sdir, sid = ctx

    out_zip = getattr(args, "out", None) or f"{sid}.odvstudio.zip"
    out_p = Path(out_zip)
    if not out_p.is_absolute() and executor:
        out_p = (executor.workspace_root / out_p).resolve()

    ack_confidential = bool(getattr(args, "acknowledge_confidential_source_export", False))
    export_rationale = getattr(args, "export_rationale", None)

    try:
        res_path = export_bundle(
            session_dir=sdir,
            output_zip_path=out_p,
            acknowledge_confidential=ack_confidential,
            export_rationale=export_rationale,
        )
    except ArchiveSecurityViolationError as exc:
        return 4, {
            "status": "ERROR",
            "error_code": exc.error_code,
            "detail": str(exc),
        }
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        return _err(4, "BUNDLE_EXPORT_FAILED", str(exc))

    return 0, {
        "session_id": sid,
        "bundle_path": str(res_path),
        "size_bytes": res_path.stat().st_size,
    }


def handle_bundle_import(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """20. `opendv ontology studio bundle import -f <in_zip>` (MAJ-07)."""
    in_zip = getattr(args, "file", None)
    if not in_zip:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")

    in_p = Path(in_zip)
    if not in_p.is_absolute() and executor:
        in_p = (executor.workspace_root / in_p).resolve()
    if not in_p.is_file():
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"File not found: {in_zip}")

    studio_root = _get_studio_root(executor)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    new_sid = getattr(args, "new_session_id", None)
    if new_sid:
        err_new = _validate_session(new_sid)
        if err_new:
            return err_new

    force = bool(getattr(args, "force", False))
    ack_unverified = bool(getattr(args, "acknowledge_unverified_import_attribution", False))
    ack_rationale = getattr(args, "acknowledgement_rationale", None)
    try:
        res = import_bundle(
            zip_path=in_p,
            target_dir=studio_root,
            security_manager=sec_mgr,
            new_session_id=new_sid,
            acknowledge_unverified_import_attribution=ack_unverified,
            acknowledgement_rationale=ack_rationale,
            force=force,
        )
    except ArchiveSecurityViolationError as exc:
        return 4, {
            "status": "ERROR",
            "error_code": exc.error_code,
            "detail": str(exc),
        }
    except zipfile.BadZipFile as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Corrupt bundle zip archive: {exc}")
    except FileExistsError as exc:
        return _err(6, "SESSION_ALREADY_EXISTS", str(exc))
    except (ValueError, KeyError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        return _err(4, "IMPORT_GROUNDING_VIOLATION", str(exc))

    return 0, {
        "session_id": res.session_id,
        "status": "IMPORTED",
        "version": res.head_version,
        "head_sha256": res.head_sha256,
    }


def handle_bundle_clone(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """21. `opendv ontology studio bundle clone` (MAJ-01, MAJ-03, MIN-03)."""
    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err

    new_sid = getattr(args, "new_session_id", None) or f"s-{secrets.token_hex(8)}"
    err_new = _validate_session(new_sid)
    if err_new:
        return err_new

    session_mgr = _get_session_manager(executor)
    res_sess = _require_session(session_mgr, sid)
    if isinstance(res_sess, tuple):
        return res_sess

    sdir = session_mgr.get_session_dir(sid)
    studio_root = _get_studio_root(executor)
    new_sdir = studio_root / new_sid
    if new_sdir.exists():
        return _err(6, "SESSION_ALREADY_EXISTS", f"Session '{new_sid}' already exists")

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    with tempfile.TemporaryDirectory(prefix="clone_bundle_") as tmp_dir:
        tmp_zip = Path(tmp_dir) / f"{sid}_clone.zip"
        try:
            export_bundle(
                session_dir=sdir,
                output_zip_path=tmp_zip,
                acknowledge_confidential=True,
                export_rationale="Session bundle clone",
            )
            res = import_bundle(
                zip_path=tmp_zip,
                target_dir=studio_root,
                security_manager=sec_mgr,
                new_session_id=new_sid,
                force=False,
            )
        except FileExistsError:
            return _err(6, "SESSION_ALREADY_EXISTS", f"Session '{new_sid}' already exists")
        except (
            ArchiveSecurityViolationError,
            zipfile.BadZipFile,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            return _err(4, "CLONE_FAILED", f"Failed to clone bundle: {exc}")

    return 0, {
        "source_session_id": sid,
        "new_session_id": new_sid,
        "session_id": new_sid,
        "status": "CLONED",
        "head_version": res.head_version,
        "head_sha256": res.head_sha256,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_STUDIO_DISPATCH = {
    "init": handle_init,
    "create": handle_create,
    "list": handle_list,
    "status": handle_status,
    "upload": handle_upload,
    "parse": handle_parse,
    "clarify": handle_clarify,
    "answer": handle_answer,
    "construct": handle_construct,
    ("delta", "validate"): handle_delta_validate,
    ("delta", "apply"): handle_delta_apply,
    ("proposal", "list"): handle_proposal_list,
    ("proposal", "show"): handle_proposal_show,
    ("proposal", "accept"): handle_proposal_accept,
    ("proposal", "reject"): handle_proposal_reject,
    ("proposal", "rebase"): handle_proposal_rebase,
    "export": handle_export,
    "promote": handle_promote,
    ("bundle", "export"): handle_bundle_export,
    ("bundle", "import"): handle_bundle_import,
    ("bundle", "clone"): handle_bundle_clone,
}


def run_ontology_studio_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, Any]:
    """Top-level entrypoint for `opendv ontology studio <cmd>` in-process execution."""
    action = getattr(args, "studio_action", None)
    if not action:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Missing studio action (e.g. init, create, list, status, ...)",
        )

    # Check for nested subcommands (delta, proposal, bundle)
    if action in ("delta", "proposal", "bundle"):
        sub_action = getattr(args, f"studio_{action}_action", None)
        if not sub_action:
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Missing {action} subcommand")
        handler = _STUDIO_DISPATCH.get((action, sub_action))
    else:
        handler = _STUDIO_DISPATCH.get(action)

    if not handler:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Unknown studio subcommand '{action}'")

    return handler(args, executor=executor)


# ---------------------------------------------------------------------------
# ArgumentParser Subparser Registration
# ---------------------------------------------------------------------------


def _build_studio_parent() -> argparse.ArgumentParser:
    """Shared parent parser providing global options (--json, --format, --role, etc.)."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--format", dest="format", choices=["toon", "json", "text"], default=argparse.SUPPRESS
    )
    parent.add_argument("--verbose", dest="verbose", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument(
        "--json", "--json-output", dest="json", action="store_true", default=argparse.SUPPRESS
    )
    parent.add_argument("--toon", dest="toon", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument(
        "--role",
        dest="role",
        choices=[
            "PUBLIC",
            "INTERNAL_MODELER",
            "DATA_STEWARD",
            "AUDITOR_FINANCE",
            "RESTRICTED_PII_CUSTODIAN",
        ],
        default=argparse.SUPPRESS,
    )
    return parent


def register_studio_subparsers(parser: argparse.ArgumentParser) -> None:
    """Attach full 14-group / 21-leaf subcommand hierarchy to ArgumentParser."""
    # Find top-level subcommand action
    sub_action = next(
        (a for a in parser._actions if isinstance(a.choices, dict) and "ontology" in a.choices),
        None,
    )
    if not sub_action:
        return

    onto_parser = sub_action.choices["ontology"]
    onto_sub = next(
        (a for a in onto_parser._actions if isinstance(a.choices, dict) and "suggest" in a.choices),
        None,
    )
    if not onto_sub:
        return
    sub_p = _build_studio_parent()
    if "studio" in onto_sub.choices:
        studio_p = onto_sub.choices["studio"]
    else:
        studio_p = onto_sub.add_parser(
            "studio",
            parents=[sub_p],
            help="Interactive Ontology Construction Studio (CAS sessions, dual-mode engine)",
            description=(
                "Manage interactive Ontology Construction Studio sessions, ingestion, and promotion"
            ),
        )

    studio_sub_action = next(
        (a for a in studio_p._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    studio_subs = (
        studio_sub_action
        if studio_sub_action is not None
        else studio_p.add_subparsers(dest="studio_action", title="studio subcommands")
    )

    # 1. init
    init_p = studio_subs.add_parser(
        "init", parents=[_build_studio_parent()], help="Initialize local workbench loopback token"
    )
    init_p.add_argument("--force", action="store_true", default=False, help="Rotate local secret")
    init_p.add_argument(
        "--show-token",
        dest="show_token",
        action="store_true",
        default=False,
        help="Display local authentication token",
    )

    # 2. create
    create_p = studio_subs.add_parser(
        "create", parents=[_build_studio_parent()], help="Create new CAS studio session"
    )
    create_p.add_argument("-s", "--session", dest="session", type=str, help="Studio session ID")
    create_p.add_argument(
        "-n", "--domain-namespace", dest="domain_namespace", type=str, default="default"
    )
    create_p.add_argument(
        "-o",
        "--enterprise-owner",
        dest="enterprise_owner",
        type=str,
        default="Enterprise",
    )
    create_p.add_argument(
        "-d", "--description", dest="description", type=str, help="Session description"
    )
    create_p.add_argument("--from-workspace", dest="from_workspace", type=str)
    create_p.add_argument("--session-label", dest="session_label", type=str)
    create_p.add_argument("--ontology-id", dest="ontology_id", type=str)

    # 3. list
    list_p = studio_subs.add_parser(
        "list", parents=[_build_studio_parent()], help="List studio sessions"
    )
    list_p.add_argument("--limit", dest="limit", type=int, default=50)
    list_p.add_argument("--offset", dest="offset", type=int, default=0)
    list_p.add_argument("--status", dest="status", type=str)

    # 4. status
    status_p = studio_subs.add_parser(
        "status", parents=[_build_studio_parent()], help="Get session status"
    )
    status_p.add_argument("-s", "--session", dest="session", type=str, required=True)

    # 5. upload
    upload_p = studio_subs.add_parser(
        "upload", parents=[_build_studio_parent()], help="Upload source document into CAS"
    )
    upload_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    upload_p.add_argument("-f", "--file", dest="file", type=str, required=True)
    upload_p.add_argument(
        "--do-not-send-to-agent",
        dest="do_not_send_to_agent",
        action="store_true",
        default=False,
    )
    upload_p.add_argument("--confidential", dest="confidential", action="store_true", default=False)
    upload_p.add_argument("-d", "--description", dest="description", type=str)

    # 6. parse
    parse_p = studio_subs.add_parser(
        "parse", parents=[_build_studio_parent()], help="Parse uploaded source documents"
    )
    parse_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    parse_p.add_argument("--source-id", dest="source_id", type=str)
    parse_p.add_argument("--source-ids", dest="source_ids", nargs="+")
    parse_p.add_argument(
        "--ocr-mode",
        dest="ocr_mode",
        choices=["auto", "force", "off"],
        default="auto",
    )
    parse_p.add_argument("--wait", dest="wait", action="store_true", default=False)
    parse_p.add_argument("--stream", dest="stream", action="store_true", default=False)

    # 7. clarify
    clarify_p = studio_subs.add_parser(
        "clarify", parents=[_build_studio_parent()], help="Run domain clarification harness"
    )
    clarify_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    clarify_p.add_argument("--focus-concept", dest="focus_concept", type=str)
    clarify_p.add_argument("--max-questions", dest="max_questions", type=int, default=10)
    clarify_p.add_argument("--wait", dest="wait", action="store_true", default=False)
    clarify_p.add_argument("--stream", dest="stream", action="store_true", default=False)

    # 8. answer
    answer_p = studio_subs.add_parser(
        "answer", parents=[_build_studio_parent()], help="Submit clarification answers"
    )
    answer_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    answer_p.add_argument("-f", "--file", dest="file", type=str)
    answer_p.add_argument("-q", "--question-id", dest="question_id", type=str)
    answer_p.add_argument("-o", "--option", dest="option", nargs="+")
    answer_p.add_argument("-t", "--text", dest="text", type=str)

    # 9. construct
    construct_p = studio_subs.add_parser(
        "construct", parents=[_build_studio_parent()], help="Synthesize ontology candidates"
    )
    construct_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    construct_p.add_argument("--base-version", dest="base_version", type=str)
    construct_p.add_argument("--base-sha256", dest="base_sha256", type=str)
    construct_p.add_argument(
        "--mode",
        dest="mode",
        choices=["auto", "agent", "deterministic"],
        default="auto",
    )
    construct_p.add_argument(
        "--auto-commit", dest="auto_commit", action="store_true", default=False
    )
    construct_p.add_argument("--wait", dest="wait", action="store_true", default=False)
    construct_p.add_argument("--stream", dest="stream", action="store_true", default=False)

    # 10. delta
    delta_p = studio_subs.add_parser(
        "delta", parents=[_build_studio_parent()], help="Delta operations (validate, apply)"
    )
    delta_subs = delta_p.add_subparsers(dest="studio_delta_action", title="delta subcommands")

    # 10a. delta validate
    dval_p = delta_subs.add_parser(
        "validate", parents=[_build_studio_parent()], help="Validate ontology delta"
    )
    dval_p.add_argument("-s", "--session", dest="session", type=str, required=False)
    dval_p.add_argument("-f", "--file", dest="file", type=str, required=True)
    dval_p.add_argument("-b", "--base", dest="base", type=str, required=False)
    dval_p.add_argument("--base-version", dest="base_version", type=str)
    dval_p.add_argument("--base-sha256", dest="base_sha256", type=str)
    dval_p.add_argument(
        "--verify-grounding",
        dest="verify_grounding",
        action="store_true",
        default=False,
    )
    dval_p.add_argument(
        "--low-confidence-waiver",
        dest="low_confidence_waiver",
        action="store_true",
        default=False,
    )

    # 10b. delta apply
    dapp_p = delta_subs.add_parser(
        "apply", parents=[_build_studio_parent()], help="Apply delta directly to session head"
    )
    dapp_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    dapp_p.add_argument("-f", "--file", dest="file", type=str, required=True)
    dapp_p.add_argument("--base-version", dest="base_version", type=str)
    dapp_p.add_argument("--base-sha256", dest="base_sha256", type=str)
    dapp_p.add_argument("-r", "--rationale", dest="rationale", type=str)
    dapp_p.add_argument(
        "--low-confidence-waiver",
        dest="low_confidence_waiver",
        action="store_true",
        default=False,
    )
    dapp_p.add_argument("--grounding-rationale", dest="grounding_rationale", type=str)
    dapp_p.add_argument("--waiver-rationale", dest="waiver_rationale", type=str)
    dapp_p.add_argument("--waiver-key-file", dest="waiver_key_file", type=str)
    dapp_p.add_argument("--ungrounded-concept", dest="ungrounded_concept", nargs="+")
    dapp_p.add_argument("--evidence-grounding-file", dest="evidence_grounding_file", type=str)
    dapp_p.add_argument("--authorized-by", dest="authorized_by", type=str)
    dapp_p.add_argument("--authorized-role", dest="authorized_role", type=str)

    # 11. proposal
    prop_p = studio_subs.add_parser(
        "proposal",
        parents=[_build_studio_parent()],
        help="Proposal operations (list, show, accept, reject, rebase)",
    )
    prop_subs = prop_p.add_subparsers(dest="studio_proposal_action", title="proposal subcommands")

    # 11a. proposal list
    plist_p = prop_subs.add_parser(
        "list", parents=[_build_studio_parent()], help="List session proposals"
    )
    plist_p.add_argument("-s", "--session", dest="session", type=str, required=True)

    # 11b. proposal show
    pshow_p = prop_subs.add_parser(
        "show", parents=[_build_studio_parent()], help="Show proposal details"
    )
    pshow_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    pshow_p.add_argument("-p", "--proposal-id", dest="proposal_id", type=str, required=True)

    # 11c. proposal accept
    pacc_p = prop_subs.add_parser(
        "accept", parents=[_build_studio_parent()], help="Accept proposal and roll head forward"
    )
    pacc_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    pacc_p.add_argument("-p", "--proposal-id", dest="proposal_id", type=str, required=True)
    pacc_p.add_argument("--base-sha256", dest="base_sha256", type=str, required=True)
    pacc_p.add_argument(
        "--allow-self-accepted-proposals",
        dest="allow_self_accepted_proposals",
        action="store_true",
        default=False,
    )
    pacc_p.add_argument(
        "--low-confidence-waiver",
        dest="low_confidence_waiver",
        action="store_true",
        default=False,
    )
    pacc_p.add_argument("--grounding-rationale", dest="grounding_rationale", type=str)
    pacc_p.add_argument("--waiver-rationale", dest="waiver_rationale", type=str)
    pacc_p.add_argument("--waiver-key-file", dest="waiver_key_file", type=str)
    pacc_p.add_argument("--ungrounded-concept", dest="ungrounded_concept", nargs="+")
    pacc_p.add_argument("--evidence-grounding-file", dest="evidence_grounding_file", type=str)
    pacc_p.add_argument("--authorized-by", dest="authorized_by", type=str)
    pacc_p.add_argument("--authorized-role", dest="authorized_role", type=str)

    # 11d. proposal reject
    prej_p = prop_subs.add_parser(
        "reject", parents=[_build_studio_parent()], help="Reject proposal"
    )
    prej_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    prej_p.add_argument("-p", "--proposal-id", dest="proposal_id", type=str, required=True)
    prej_p.add_argument("-r", "--reason", dest="reason", type=str)

    # 11e. proposal rebase
    preb_p = prop_subs.add_parser(
        "rebase", parents=[_build_studio_parent()], help="Rebase proposal onto current head"
    )
    preb_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    preb_p.add_argument("-p", "--proposal-id", dest="proposal_id", type=str, required=True)
    preb_p.add_argument("--base-sha256", dest="base_sha256", type=str)
    preb_p.add_argument("--head-sha256", dest="head_sha256", type=str)

    # 12. export
    export_p = studio_subs.add_parser(
        "export", parents=[_build_studio_parent()], help="Export session ontology"
    )
    export_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    export_p.add_argument(
        "-e",
        "--export-format",
        dest="export_format",
        choices=["yaml", "json", "turtle", "jsonld"],
        default="yaml",
    )
    export_p.add_argument("-v", "--version", dest="version", type=str)
    export_p.add_argument("-o", "--out", dest="out", type=str)

    # 13. promote
    promote_p = studio_subs.add_parser(
        "promote",
        parents=[_build_studio_parent()],
        help="Promote committed ontology into workspace",
    )
    promote_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    promote_p.add_argument("-w", "--workspace", dest="workspace", type=str)
    promote_p.add_argument("--workspace-sha256", dest="workspace_sha256", type=str, required=True)
    promote_p.add_argument("--head-sha256", dest="head_sha256", type=str, required=True)
    promote_p.add_argument("-r", "--rationale", dest="rationale", type=str, required=True)
    promote_p.add_argument(
        "--acknowledge-historical-contract-warning",
        dest="acknowledge_historical_contract_warning",
        action="store_true",
        default=False,
    )
    promote_p.add_argument(
        "--historical-contract-rationale",
        dest="historical_contract_rationale",
        type=str,
    )

    # 14. bundle
    bundle_p = studio_subs.add_parser(
        "bundle",
        parents=[_build_studio_parent()],
        help="Studio bundle operations (export, import, clone)",
    )
    bundle_subs = bundle_p.add_subparsers(dest="studio_bundle_action", title="bundle subcommands")

    # 14a. bundle export
    bexp_p = bundle_subs.add_parser(
        "export", parents=[_build_studio_parent()], help="Export session archive bundle"
    )
    bexp_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    bexp_p.add_argument("-o", "--out", dest="out", type=str, required=True)
    bexp_p.add_argument(
        "--acknowledge-confidential-source-export",
        dest="acknowledge_confidential_source_export",
        action="store_true",
        default=False,
    )
    bexp_p.add_argument("--export-rationale", dest="export_rationale", type=str)

    # 14b. bundle import
    bimp_p = bundle_subs.add_parser(
        "import", parents=[_build_studio_parent()], help="Import session archive bundle"
    )
    bimp_p.add_argument("-f", "--file", dest="file", type=str, required=True)
    bimp_p.add_argument(
        "--acknowledge-unverified-import-attribution",
        dest="acknowledge_unverified_import_attribution",
        action="store_true",
        default=False,
    )
    bimp_p.add_argument("--acknowledgement-rationale", dest="acknowledgement_rationale", type=str)
    bimp_p.add_argument("--new-session-id", dest="new_session_id", type=str)
    bimp_p.add_argument("--force", action="store_true", default=False)
    bimp_p.add_argument(
        "--acknowledge-historical-contract-warning",
        dest="acknowledge_historical_contract_warning",
        action="store_true",
        default=False,
    )
    bimp_p.add_argument(
        "--historical-contract-rationale",
        dest="historical_contract_rationale",
        type=str,
    )

    # 14c. bundle clone
    bclone_p = bundle_subs.add_parser(
        "clone", parents=[_build_studio_parent()], help="Clone existing session"
    )
    bclone_p.add_argument("-s", "--session", dest="session", type=str, required=True)
    bclone_p.add_argument("--new-session-id", dest="new_session_id", type=str)
    bclone_p.add_argument("-l", "--session-label", dest="session_label", type=str)


__all__ = [
    "handle_answer",
    "handle_bundle_clone",
    "handle_bundle_export",
    "handle_bundle_import",
    "handle_clarify",
    "handle_construct",
    "handle_create",
    "handle_delta_apply",
    "handle_delta_validate",
    "handle_export",
    "handle_init",
    "handle_list",
    "handle_parse",
    "handle_promote",
    "handle_proposal_accept",
    "handle_proposal_list",
    "handle_proposal_rebase",
    "handle_proposal_reject",
    "handle_proposal_show",
    "handle_status",
    "handle_upload",
    "register_studio_subparsers",
    "run_ontology_studio_command",
]
