"""Layer-2b Open-DV V2 service layer public exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opendv.service.app_service import OpenDVService
from opendv.service.bundles import (
    MAX_BUNDLE_ENTRIES,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    MAX_COMPRESSION_RATIO,
    RATIO_CHECK_THRESHOLD_BYTES,
    BundleMode,
    ConflictPolicy,
    VerifiedBundle,
    export_project_bundle,
    import_project_bundle,
    inspect_and_verify_archive,
    reject_symlinks_on_path_and_ancestors,
    safe_extract_binary_entries,
    safe_open_no_follow,
    validate_archive_member,
    validate_containment,
)
from opendv.service.citations import (
    ensure_no_redacted_placeholder,
    resolve_citation,
)
from opendv.service.guided_translator import (
    CertificationLabel,
    GuidedUxTranslator,
    build_guided_run_view,
)
from opendv.service.harness_svc import HarnessService
from opendv.service.os_platform import (
    atomic_write_bytes,
    atomic_write_text,
    check_wal_filesystem_safe,
    get_process_start_ts,
    is_process_instance_alive,
    locked_file_access,
    parse_linux_proc_stat,
    replace_with_retry,
    resolve_opendv_home,
    resolve_user_state_dir,
    set_owner_only_permissions,
    validate_workspace_containment,
    verify_owner_only_permissions,
)
from opendv.service.secrets import (
    KeyringSecretStore,
    SecretSource,
    probe_keyring_available,
    scoped_env_first,
)
from opendv.service.settings_store import SettingsStore
from opendv.service.settings_svc import SettingsService
from opendv.service.state_store import (
    ProjectStateManager,
    ServiceControlSignalReader,
    ServiceRunCheckpointStore,
    ServiceRunEventSink,
)
from opendv.service.turtle_writer import export_ontology_turtle, write_ontology_turtle
from opendv.service.workflows import (
    DiscoveryService,
    ProjectsService,
    StorageService,
    VaultService,
)

if TYPE_CHECKING:
    from opendv.service.external_mcp import ExternalMcpGateway
    from opendv.service.mcp_broker import JobMcpBroker, ServiceScoutToolDispatcher


def __getattr__(name: str) -> Any:
    """Lazy-load MCP/uvicorn transport helpers to preserve Layer-4 subprocess isolation."""
    if name == "ExternalMcpGateway":
        from opendv.service.external_mcp import ExternalMcpGateway

        return ExternalMcpGateway
    if name == "JobMcpBroker":
        from opendv.service.mcp_broker import JobMcpBroker

        return JobMcpBroker
    if name == "ServiceScoutToolDispatcher":
        from opendv.service.mcp_broker import ServiceScoutToolDispatcher

        return ServiceScoutToolDispatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MAX_BUNDLE_ENTRIES",
    "MAX_BUNDLE_UNCOMPRESSED_BYTES",
    "MAX_COMPRESSION_RATIO",
    "RATIO_CHECK_THRESHOLD_BYTES",
    "BundleMode",
    "CertificationLabel",
    "ConflictPolicy",
    "DiscoveryService",
    "ExternalMcpGateway",
    "GuidedUxTranslator",
    "HarnessService",
    "JobMcpBroker",
    "KeyringSecretStore",
    "OpenDVService",
    "ProjectStateManager",
    "ProjectsService",
    "SecretSource",
    "ServiceControlSignalReader",
    "ServiceRunCheckpointStore",
    "ServiceRunEventSink",
    "ServiceScoutToolDispatcher",
    "SettingsService",
    "SettingsStore",
    "StorageService",
    "VaultService",
    "VerifiedBundle",
    "atomic_write_bytes",
    "atomic_write_text",
    "build_guided_run_view",
    "check_wal_filesystem_safe",
    "ensure_no_redacted_placeholder",
    "export_ontology_turtle",
    "export_project_bundle",
    "get_process_start_ts",
    "import_project_bundle",
    "inspect_and_verify_archive",
    "is_process_instance_alive",
    "locked_file_access",
    "parse_linux_proc_stat",
    "probe_keyring_available",
    "reject_symlinks_on_path_and_ancestors",
    "replace_with_retry",
    "resolve_citation",
    "resolve_opendv_home",
    "resolve_user_state_dir",
    "safe_extract_binary_entries",
    "safe_open_no_follow",
    "scoped_env_first",
    "set_owner_only_permissions",
    "validate_archive_member",
    "validate_containment",
    "validate_workspace_containment",
    "verify_owner_only_permissions",
    "write_ontology_turtle",
]
