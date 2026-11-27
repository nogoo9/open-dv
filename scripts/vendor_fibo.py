#!/usr/bin/env python3
"""FIBO Pack v2 Reference Ontology Compilation Script (`grounding/providers/fibo/`)."""

from __future__ import annotations

import argparse
import json
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
    ALL_V2_MODULES,
    CORE_MODULES,
    DEFAULT_COMPILED_AT,
    EXPLANATORY_NOTE,
    FIBO_BASE_URI_PREFIX,
    PROD_MODULES,
    UPSTREAM_REPO,
    compile_fibo_pack,
    create_mock_fibo_graph,
    extract_fibo_graph,
    extract_fibo_graph_v2,
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
    verify_starter_packs_path_a,
)

from opendv.models.errors import OpenDVError  # noqa: E402


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

DEFAULT_OUTPUT_DIR = REPO_ROOT / "src" / "opendv" / "grounding" / "providers" / "fibo"
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
    source_dir: Path | None = None,
    archive_sha256: str | None = None,
    repo_url: str = UPSTREAM_REPO,
    tag: str = UPSTREAM_TAG,
    expected_commit: str = UPSTREAM_COMMIT,
    is_production: bool = False,
    allow_dev: bool = False,
    source_mode: str = "https_zip",
    zip_url: str | None = None,
) -> tuple[Path, str]:
    """Ensure FIBO source files are locally available and verify provenance."""
    mode = "git" if source_mode == "git" else "https_zip"
    return _ensure_fibo_source_impl(
        cache_dir=cache_dir,
        offline_archive=offline_archive,
        source_dir=source_dir,
        archive_sha256=archive_sha256,
        repo_url=repo_url,
        tag=tag,
        expected_commit=expected_commit,
        is_production=is_production,
        pinned_archive_sha256=PINNED_ARCHIVE_SHA256,
        allow_dev=allow_dev,
        source_mode=mode,
        zip_url=zip_url,
    )


def _parse_modules_arg(raw_modules: str | None, *, allow_dev: bool) -> tuple[str, ...]:
    if not raw_modules:
        return ALL_V2_MODULES if allow_dev else PROD_MODULES
    parsed = tuple(
        dict.fromkeys(m.strip().upper() for m in raw_modules.split(",") if m.strip())
    )
    invalid = [m for m in parsed if m not in ALL_V2_MODULES]
    if invalid:
        raise ValueError(f"Unsupported FIBO modules: {invalid}; valid={list(ALL_V2_MODULES)}")
    if "MD" in parsed and not allow_dev:
        raise ValueError("Module 'MD' requires --allow-dev (absent from prod.ttl.zip)")
    return parsed


def _update_baseline_digests(verification: BudgetVerificationResult) -> None:
    from opendv.grounding.providers.fibo.loader import (
        _cached_sha256_file,
        clear_fibo_loader_cache,
    )
    from opendv.sources.starter_packs import (
        STARTER_PACK_IDS,
        compute_starter_pack_sha256,
        get_starter_pack_ontology,
    )

    _cached_sha256_file.cache_clear()
    clear_fibo_loader_cache()

    baseline_path = REPO_ROOT / "schemas" / "v2" / "baseline-digests.json"
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    data["fibo_core_pack"] = {
        "path": "src/opendv/grounding/providers/fibo/fibo-core-pack.json",
        "sha256": verification.sha256_full,
        "pack_schema_version": "2.0.0",
        "concept_count": verification.class_count,
        "property_count": verification.property_count,
        "individual_count": verification.individual_count,
    }
    starter_section = data.setdefault("starter_packs", {})
    for pack_id in STARTER_PACK_IDS:
        ont = get_starter_pack_ontology(pack_id)
        starter_section[pack_id] = {
            "domain_namespace": ont.domain_namespace,
            "sha256": compute_starter_pack_sha256(ont),
            "hub_count": len(ont.concepts),
        }

    staging = baseline_path.with_suffix(".json.tmp")
    try:
        staging.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, baseline_path)
    finally:
        staging.unlink(missing_ok=True)


def _run_verify_mode(args: argparse.Namespace, *, selected_modules: tuple[str, ...]) -> int:
    target_pack: Path | None = None
    if args.verify_pack and args.verify_pack != "":
        target_pack = Path(args.verify_pack).resolve()
    elif args.out is not None:
        target_pack = Path(args.out).resolve()
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
        required_modules=selected_modules,
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
    """CLI entry point for FIBO Pack v2 compilation, verification, and repo sync."""
    _init_windows_platform()
    parser = argparse.ArgumentParser(
        description="FIBO compilation and verification script for Open-DV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--offline-archive", type=Path, default=None)
    parser.add_argument("--source-dir", type=Path, default=None)
    parser.add_argument("--archive-sha256", type=str, default=None)
    parser.add_argument("--modules", type=str, default=None)
    parser.add_argument("--allow-dev", action="store_true", default=False)
    parser.add_argument("--production", action="store_true", default=False)
    parser.add_argument("--update-repo", action="store_true", default=False)
    parser.add_argument("--mock-fixture", action="store_true")
    parser.add_argument("--verify-pack", nargs="?", const="", default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--compiled-at", type=str, default=DEFAULT_COMPILED_AT)
    parser.add_argument("--tag", type=str, default=UPSTREAM_TAG)
    parser.add_argument("--commit", type=str, default=UPSTREAM_COMMIT)
    parser.add_argument("--repo-url", type=str, default=UPSTREAM_REPO)
    args = parser.parse_args(argv)

    try:
        selected_modules = _parse_modules_arg(args.modules, allow_dev=args.allow_dev)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    if args.update_repo:
        args.output_dir = DEFAULT_OUTPUT_DIR
        args.production = True

    effective_out_dir = args.out.parent if args.out is not None else args.output_dir
    is_prod_target = args.production or is_production_target(effective_out_dir)
    if args.mock_fixture and is_prod_target:
        print(
            "Error: --mock-fixture requires a non-production --output-dir.",
            file=sys.stderr,
        )
        return 1

    if args.verify_only or args.verify_pack is not None:
        return _run_verify_mode(args, selected_modules=selected_modules)

    verified_commit = args.commit
    if args.mock_fixture:
        g = create_mock_fibo_graph(include_dev=args.allow_dev)
    else:
        try:
            src_mode = "git" if args.repo_url != UPSTREAM_REPO else "https_zip"
            repo_path, verified_commit = ensure_fibo_source(
                cache_dir=args.cache_dir,
                offline_archive=args.offline_archive,
                source_dir=args.source_dir,
                archive_sha256=args.archive_sha256,
                repo_url=args.repo_url,
                tag=args.tag,
                expected_commit=args.commit,
                is_production=is_prod_target,
                allow_dev=args.allow_dev,
                source_mode=src_mode,
            )
            g = load_fibo_from_directory(
                repo_path, allow_parse_errors=False, modules=selected_modules
            )
        except (
            RuntimeError,
            OSError,
            ValueError,
            tarfile.TarError,
            zipfile.BadZipFile,
        ) as err:
            print(f"Error preparing FIBO source: {err}", file=sys.stderr)
            return 1

    classes, properties, individuals = extract_fibo_graph_v2(
        g, modules=selected_modules, include_cmns=not args.mock_fixture
    )
    raw_bytes, _, sha256_full, sha256_8 = compile_fibo_pack(
        classes,
        properties,
        individuals,
        upstream_tag=args.tag,
        upstream_commit=verified_commit,
        compiled_at=args.compiled_at,
        modules=selected_modules,
        is_fixture=args.mock_fixture,
    )

    effective_out_dir.mkdir(parents=True, exist_ok=True)
    canonical_filename = f"fibo-core-{sha256_8}.json"
    staging_path = effective_out_dir / f".staging-{sha256_8}.tmp"
    alias_staging = effective_out_dir / ".staging-pack.tmp"
    try:
        staging_path.write_bytes(raw_bytes)
        verification = verify_pack(
            staging_path,
            allow_fixture=args.mock_fixture or not is_prod_target,
            require_production_counts=is_prod_target and not args.mock_fixture,
            expected_commit=args.commit,
            expected_tag=args.tag,
            required_modules=selected_modules,
        )
        if not verification.is_valid:
            gate_prefix = (
                "VERIFICATION_GATE_FAILED: " if args.update_repo else "Budget verification FAILED: "
            )
            print(f"{gate_prefix}{verification.error_message}", file=sys.stderr)
            return 2 if args.update_repo else 1

        if args.update_repo:
            try:
                verify_starter_packs_path_a(staging_path)
            except OpenDVError as err:
                print(f"VERIFICATION_GATE_FAILED: {err}", file=sys.stderr)
                return 2

        if args.out is not None:
            alias_staging.write_bytes(raw_bytes)
            os.replace(alias_staging, args.out)
        else:
            if not is_prod_target:
                os.replace(staging_path, effective_out_dir / canonical_filename)
            alias_staging.write_bytes(raw_bytes)
            os.replace(alias_staging, effective_out_dir / "fibo-core-pack.json")
    finally:
        staging_path.unlink(missing_ok=True)
        alias_staging.unlink(missing_ok=True)

    if args.out is None:
        for old_pack in effective_out_dir.glob("fibo-core-*.json"):
            if re.match(r"^fibo-core-[0-9a-f]{8}\.json$", old_pack.name) and (
                is_prod_target or old_pack.name != canonical_filename
            ):
                old_pack.unlink()

    if args.update_repo:
        _update_baseline_digests(verification)

    print(
        f"Compilation and Budget Verification Successful: {sha256_full} "
        f"(classes={verification.class_count}, properties={verification.property_count}, "
        f"individuals={verification.individual_count})"
    )
    return 0


__all__ = [
    "ALL_V2_MODULES",
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
    "PROD_MODULES",
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
    "extract_fibo_graph_v2",
    "get_fibo_module",
    "is_production_target",
    "load_fibo_from_directory",
    "main",
    "safe_extract_archive",
    "verify_pack",
    "verify_starter_packs_path_a",
]

if __name__ == "__main__":
    sys.exit(main())
