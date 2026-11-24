"""Portable .opendv.zip bundle exporter, importer, and zero-trust zip guards."""

from __future__ import annotations

from opendv.service.bundles._bundle_ports import (
    BundleSecretStorePort,
    BundleStateStorePort,
)
from opendv.service.bundles.exporter import BundleMode, export_project_bundle
from opendv.service.bundles.importer import ConflictPolicy, import_project_bundle
from opendv.service.bundles.zip_guard import (
    MAX_BUNDLE_ENTRIES,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    MAX_COMPRESSION_RATIO,
    RATIO_CHECK_THRESHOLD_BYTES,
    VerifiedBundle,
    inspect_and_verify_archive,
    reject_symlinks_on_path_and_ancestors,
    safe_extract_binary_entries,
    safe_open_no_follow,
    validate_archive_member,
    validate_containment,
)

__all__ = [
    "MAX_BUNDLE_ENTRIES",
    "MAX_BUNDLE_UNCOMPRESSED_BYTES",
    "MAX_COMPRESSION_RATIO",
    "RATIO_CHECK_THRESHOLD_BYTES",
    "BundleMode",
    "BundleSecretStorePort",
    "BundleStateStorePort",
    "ConflictPolicy",
    "VerifiedBundle",
    "export_project_bundle",
    "import_project_bundle",
    "inspect_and_verify_archive",
    "reject_symlinks_on_path_and_ancestors",
    "safe_extract_binary_entries",
    "safe_open_no_follow",
    "validate_archive_member",
    "validate_containment",
]
