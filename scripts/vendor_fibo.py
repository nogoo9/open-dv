#!/usr/bin/env python3
"""FIBO Reference Ontology Compilation Script for Open-DV V2 (`src/opendv/sources/fibo/`)."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.vendor_fibo_archive import (  # noqa: E402
    FILE_ATTRIBUTE_REPARSE_POINT,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    SCRIPT_CACHE_MARKER,
    ArchiveSecurityError,
    safe_extract_archive,
)
from scripts.vendor_fibo_archive import (  # noqa: E402
    ensure_fibo_source as _ensure_fibo_source_impl,
)
from scripts.vendor_fibo_archive import (  # noqa: E402
    hash_file_sha256 as _hash_file_sha256,
)
from scripts.vendor_fibo_extract import (  # noqa: E402
    CORE_MODULES,
    DEFAULT_COMPILED_AT,
    EXPLANATORY_NOTE,
    FIBO_BASE_URI_PREFIX,
    UPSTREAM_REPO,
    compile_fibo_pack,
    create_mock_fibo_graph,
    extract_fibo_graph,
    get_fibo_module,
    load_fibo_from_directory,
)
from scripts.vendor_fibo_extract import (  # noqa: E402
    extract_annotations as _extract_annotations,
)
from scripts.vendor_fibo_extract import (  # noqa: E402
    load_baseline_fibo_config as _load_baseline_fibo_config,
)
from scripts.vendor_fibo_extract import (  # noqa: E402
    pick_best_literal as _pick_best_literal,
)
from scripts.vendor_fibo_verify import (  # noqa: E402
    MAX_DISK_SIZE_BYTES,
    MAX_LOAD_LATENCY_MS,
    MAX_MEMORY_BYTES,
    MIN_PRODUCTION_CLASSES,
    MIN_PRODUCTION_PROPERTIES,
    BudgetVerificationResult,
    verify_pack,
)


def _init_windows_platform() -> None:
    if sys.platform == "win32":
        os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"  # noqa: SIM112
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")


_baseline_commit, _baseline_tag, PINNED_ARCHIVE_SHA256 = _load_baseline_fibo_config()
UPSTREAM_TAG = _baseline_tag
UPSTREAM_COMMIT = _baseline_commit

DEFAULT_OUTPUT_DIR = REPO_ROOT / "src" / "opendv" / "sources" / "fibo"
DEFAULT_CACHE_DIR = REPO_ROOT / ".opendv" / "cache" / "fibo"


def is_production_target(target_path: Path) -> bool:
    """Check if target path is inside V2 production src/opendv/ directory."""
    try:
        resolved = target_path.resolve()
        prod_dir = (REPO_ROOT / "src" / "opendv").resolve()
        return resolved == prod_dir or resolved.is_relative_to(prod_dir)
    except (ValueError, OSError):
        parts = target_path.parts
        return "src" in parts and "opendv" in parts


def ensure_fibo_source(
    *,
    cache_dir: Path,
    offline_archive: Path | None = None,
    archive_sha256: str | None = None,
    repo_url: str = UPSTREAM_REPO,
    tag: str = UPSTREAM_TAG,
    expected_commit: str = UPSTREAM_COMMIT,
    is_production: bool = False,
) -> tuple[Path, str]:
    """Ensure FIBO source files are locally available and verify git provenance."""
    return _ensure_fibo_source_impl(
        cache_dir=cache_dir,
        offline_archive=offline_archive,
        archive_sha256=archive_sha256,
        repo_url=repo_url,
        tag=tag,
        expected_commit=expected_commit,
        is_production=is_production,
        pinned_archive_sha256=PINNED_ARCHIVE_SHA256,
    )


def _run_verify_mode(args: argparse.Namespace) -> int:
    target_pack: Path | None = None
    if args.verify_pack and args.verify_pack != "":
        target_pack = Path(args.verify_pack).resolve()
    else:
        candidates = sorted(
            p
            for p in args.output_dir.glob("fibo-core-*.json")
            if p.name != "fibo-core-pack.json"
        )
        if len(candidates) > 1:
            print(
                f"Error: Multiple hash-named packs found in {args.output_dir}",
                file=sys.stderr,
            )
            return 1
        if candidates:
            target_pack = candidates[0]
        elif (args.output_dir / "fibo-core-pack.json").is_file():
            target_pack = args.output_dir / "fibo-core-pack.json"

    if target_pack is None or not target_pack.is_file():
        print(f"Error: No FIBO pack found to verify in {args.output_dir}", file=sys.stderr)
        return 1

    is_prod = args.production or is_production_target(target_pack)
    result = verify_pack(
        target_pack,
        allow_fixture=not is_prod,
        require_production_counts=is_prod,
        expected_commit=args.commit,
        expected_tag=args.tag,
    )
    if not result.is_valid:
        print(f"FIBO Pack Verification FAILED: {result.error_message}", file=sys.stderr)
        return 1

    alias_file = target_pack.parent / "fibo-core-pack.json"
    if (
        alias_file.is_file()
        and target_pack != alias_file
        and alias_file.read_bytes() != target_pack.read_bytes()
    ):
        print("Alias fibo-core-pack.json does not match hash-named pack", file=sys.stderr)
        return 1

    print(f"FIBO Pack Verification PASSED: {target_pack.name} ({result.sha256_full})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for FIBO compilation and verification."""
    _init_windows_platform()
    parser = argparse.ArgumentParser(
        description="FIBO compilation and verification script for Open-DV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--offline-archive", type=Path, default=None)
    parser.add_argument("--archive-sha256", type=str, default=None)
    parser.add_argument("--production", action="store_true", default=False)
    parser.add_argument("--mock-fixture", action="store_true")
    parser.add_argument("--verify-pack", nargs="?", const="", default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--compiled-at", type=str, default=DEFAULT_COMPILED_AT)
    parser.add_argument("--tag", type=str, default=UPSTREAM_TAG)
    parser.add_argument("--commit", type=str, default=UPSTREAM_COMMIT)
    parser.add_argument("--repo-url", type=str, default=UPSTREAM_REPO)
    args = parser.parse_args(argv)

    is_prod_target = args.production or is_production_target(args.output_dir)
    if args.mock_fixture and is_prod_target:
        print(
            "Error: --mock-fixture requires a non-production --output-dir.",
            file=sys.stderr,
        )
        return 1

    if args.verify_only or args.verify_pack is not None:
        return _run_verify_mode(args)

    verified_commit = args.commit
    if args.mock_fixture:
        g = create_mock_fibo_graph()
    else:
        try:
            repo_path, verified_commit = ensure_fibo_source(
                cache_dir=args.cache_dir,
                offline_archive=args.offline_archive,
                archive_sha256=args.archive_sha256,
                repo_url=args.repo_url,
                tag=args.tag,
                expected_commit=args.commit,
                is_production=is_prod_target,
            )
            g = load_fibo_from_directory(repo_path, allow_parse_errors=False)
        except (RuntimeError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as err:
            print(f"Error preparing FIBO source: {err}", file=sys.stderr)
            return 1

    classes, properties = extract_fibo_graph(g)
    raw_bytes, _, sha256_full, sha256_8 = compile_fibo_pack(
        classes,
        properties,
        upstream_tag=args.tag,
        upstream_commit=verified_commit,
        compiled_at=args.compiled_at,
        modules=CORE_MODULES,
        is_fixture=args.mock_fixture,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    canonical_filename = f"fibo-core-{sha256_8}.json"
    staging_path = args.output_dir / f".staging-{sha256_8}.tmp"
    alias_staging = args.output_dir / ".staging-pack.tmp"
    try:
        staging_path.write_bytes(raw_bytes)
        verification = verify_pack(
            staging_path,
            allow_fixture=args.mock_fixture or not is_prod_target,
            require_production_counts=is_prod_target and not args.mock_fixture,
            expected_commit=args.commit,
            expected_tag=args.tag,
        )
        if not verification.is_valid:
            print(
                f"Budget verification FAILED: {verification.error_message}",
                file=sys.stderr,
            )
            return 1

        if not is_prod_target:
            os.replace(staging_path, args.output_dir / canonical_filename)
        alias_staging.write_bytes(raw_bytes)
        os.replace(alias_staging, args.output_dir / "fibo-core-pack.json")
    finally:
        staging_path.unlink(missing_ok=True)
        alias_staging.unlink(missing_ok=True)

    for old_pack in args.output_dir.glob("fibo-core-*.json"):
        if re.match(r"^fibo-core-[0-9a-f]{8}\.json$", old_pack.name) and (
            is_prod_target or old_pack.name != canonical_filename
        ):
            old_pack.unlink()

    print(f"Compilation and Budget Verification Successful: {sha256_full}")
    return 0


__all__ = [
    "CORE_MODULES",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_COMPILED_AT",
    "DEFAULT_OUTPUT_DIR",
    "EXPLANATORY_NOTE",
    "FIBO_BASE_URI_PREFIX",
    "FILE_ATTRIBUTE_REPARSE_POINT",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_ARCHIVE_UNCOMPRESSED_BYTES",
    "MAX_DISK_SIZE_BYTES",
    "MAX_LOAD_LATENCY_MS",
    "MAX_MEMORY_BYTES",
    "MIN_PRODUCTION_CLASSES",
    "MIN_PRODUCTION_PROPERTIES",
    "PINNED_ARCHIVE_SHA256",
    "SCRIPT_CACHE_MARKER",
    "UPSTREAM_COMMIT",
    "UPSTREAM_REPO",
    "UPSTREAM_TAG",
    "ArchiveSecurityError",
    "BudgetVerificationResult",
    "_extract_annotations",
    "_hash_file_sha256",
    "_load_baseline_fibo_config",
    "_pick_best_literal",
    "compile_fibo_pack",
    "create_mock_fibo_graph",
    "ensure_fibo_source",
    "extract_fibo_graph",
    "get_fibo_module",
    "is_production_target",
    "load_fibo_from_directory",
    "main",
    "safe_extract_archive",
    "verify_pack",
]

if __name__ == "__main__":
    sys.exit(main())
