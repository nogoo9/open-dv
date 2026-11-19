"""Phase 2 Track C Failing-First Tests: Ontology Studio Router Split & Size/Endpoint Contracts.

Verifies Decision D16 & Phase 2 Track C router decomposition:
1. `ontology_studio_{sessions,evidence,proposals,citations,logs,exports}.py` and
   `dv_schema_explorer.py` exist under `apps/opendv-platform-api/src/opendv_platform_api/routers/`.
2. Every split router (and `ontology_studio.py` facade) is `< 200` lines, `< 15,000` bytes,
   `< 5,000` `cl100k_base` tokens, and satisfies thin-adapter delegation.
3. `apps/opendv-platform-api/src/opendv_platform_api/main.py` is `< 5,000` `cl100k_base` tokens.
4. `create_app()` exposes 100% of legacy Ontology Studio endpoints AND all new Phase 2
   Studio + DV Schema Explorer endpoints with zero duplicate OpenAPI `operationId`s.
5. `routers.ontology_studio` facade preserves backward-compatible exports required by tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tiktoken
from fastapi.routing import APIRoute

from opendv_platform_api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORM_API_SRC = REPO_ROOT / "apps" / "opendv-platform-api" / "src" / "opendv_platform_api"
ROUTERS_DIR = PLATFORM_API_SRC / "routers"
MAIN_FILE = PLATFORM_API_SRC / "main.py"

TOKEN_LIMIT = 5_000
LINE_LIMIT = 200
BYTE_LIMIT = 15_000

SPLIT_ROUTER_FILES = (
    "ontology_studio_sessions.py",
    "ontology_studio_evidence.py",
    "ontology_studio_proposals.py",
    "ontology_studio_citations.py",
    "ontology_studio_logs.py",
    "ontology_studio_exports.py",
    "dv_schema_explorer.py",
)

EXPECTED_LEGACY_STUDIO_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/ontology/studio/init"),
    ("GET", "/api/v1/ontology/studio/sessions"),
    ("POST", "/api/v1/ontology/studio/sessions"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}"),
    ("DELETE", "/api/v1/ontology/studio/sessions/{session_id}"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/status"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/manifest"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/sources"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/sources"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/sources/text"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/sources/{source_id}"),
    ("DELETE", "/api/v1/ontology/studio/sessions/{session_id}/sources/{source_id}"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/sources/{source_id}/content"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/parse"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/clarifications"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/questions"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/clarify"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/answers"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/answers"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/construct"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}"),
    ("GET", "/api/v1/ontology/studio/jobs/{job_id}"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/events"),
    ("GET", "/api/v1/ontology/studio/jobs/{job_id}/events"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/ontology/studio/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/validate"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/deltas/validate"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/delta"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/deltas/apply"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/proposals"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/proposals"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/proposals/{proposal_id}"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/proposals/{proposal_id}/accept"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/proposals/{proposal_id}/reject"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/proposals/{proposal_id}/rebase"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/proposals/{proposal_id}/refine"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/staged-candidates"),
    (
        "POST",
        "/api/v1/ontology/studio/sessions/{session_id}/staged-candidates/{candidate_id}/promote",
    ),
    ("GET", "/api/v1/ontology/studio/starter-packs"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/starter-packs/apply"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/waiver"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/feedback"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/export"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/promote"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/bundle"),
    ("POST", "/api/v1/ontology/studio/sessions/bundle/import"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/bundle/clone"),
    ("POST", "/api/v1/ontology/studio/auth/launch-nonce"),
    ("POST", "/api/v1/ontology/studio/auth/token"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/export-blueprint"),
}

EXPECTED_PHASE2_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/ontology/studio/sessions/import-ontology"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/sources/connectors"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/evidence/tables"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/evidence/chunks"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/citations/resolve"),
    ("GET", "/api/v1/ontology/studio/fibo/lookup"),
    ("POST", "/api/v1/ontology/studio/sessions/{session_id}/fibo-qa"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/logs"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/logs/stream"),
    ("GET", "/api/v1/ontology/studio/sessions/{session_id}/logs/export"),
    ("GET", "/api/v1/runs/{run_id}/dv-schema"),
    ("GET", "/api/v1/runs/{run_id}/dv-schema/tables/{table}/rows"),
    ("POST", "/api/v1/runs/{run_id}/dv-schema/query"),
}


@pytest.mark.parametrize("router_name", SPLIT_ROUTER_FILES)
def test_split_router_exists_and_meets_size_token_and_thinness_budgets(router_name: str) -> None:
    """Every decomposed Studio/Explorer router exists and is < 200 lines, < 15 KB, < 5k tokens."""
    router_path = ROUTERS_DIR / router_name
    assert router_path.is_file(), f"Missing split router: {router_path}"

    text = router_path.read_text(encoding="utf-8")
    lines = len(text.splitlines())
    byte_size = len(text.encode("utf-8"))
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = len(enc.encode(text))

    assert lines < LINE_LIMIT, f"{router_name}: {lines} lines >= {LINE_LIMIT}"
    assert byte_size < BYTE_LIMIT, f"{router_name}: {byte_size} bytes >= {BYTE_LIMIT}"
    assert tokens < TOKEN_LIMIT, f"{router_name}: {tokens} tokens >= {TOKEN_LIMIT}"
    assert (
        "OpenDVService" in text
        or "get_opendv_service" in text
        or "StudioSessionManager" in text
    ), f"{router_name}: must reference OpenDVService / get_opendv_service / StudioSessionManager"


def test_ontology_studio_facade_and_main_meet_token_budget() -> None:
    """Facade `routers/ontology_studio.py` and `main.py` are strictly < 5,000 cl100k_base tokens."""
    enc = tiktoken.get_encoding("cl100k_base")

    main_text = MAIN_FILE.read_text(encoding="utf-8")
    main_tokens = len(enc.encode(main_text))
    assert main_tokens < TOKEN_LIMIT, f"main.py: {main_tokens} tokens >= {TOKEN_LIMIT}"

    facade_path = ROUTERS_DIR / "ontology_studio.py"
    assert facade_path.is_file(), f"Missing facade router: {facade_path}"
    facade_text = facade_path.read_text(encoding="utf-8")
    facade_tokens = len(enc.encode(facade_text))
    facade_lines = len(facade_text.splitlines())
    facade_bytes = len(facade_text.encode("utf-8"))
    assert facade_tokens < TOKEN_LIMIT, (
        f"ontology_studio.py: {facade_tokens} tokens >= {TOKEN_LIMIT}"
    )
    assert facade_lines < LINE_LIMIT, f"ontology_studio.py: {facade_lines} lines >= {LINE_LIMIT}"
    assert facade_bytes < BYTE_LIMIT, f"ontology_studio.py: {facade_bytes} bytes >= {BYTE_LIMIT}"


def _collect_registered_routes(routes: list[object]) -> set[tuple[str, str]]:
    registered: set[tuple[str, str]] = set()
    for route in routes:
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    registered.add((method, route.path))
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                path = getattr(ctx, "path", None)
                methods = getattr(ctx, "methods", None) or ()
                if path:
                    for method in methods:
                        if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                            registered.add((method, path))
        elif hasattr(route, "routes"):
            registered.update(_collect_registered_routes(list(route.routes)))
    return registered


def test_app_exposes_all_legacy_and_phase2_endpoints_without_duplicates() -> None:
    """FastAPI app registers every legacy + Phase 2 route with unique OpenAPI operationIds."""
    app = create_app(workspace_root=REPO_ROOT)

    registered = _collect_registered_routes(list(app.routes))

    missing_legacy = sorted(EXPECTED_LEGACY_STUDIO_ROUTES - registered)
    assert not missing_legacy, f"Missing legacy Studio routes after split: {missing_legacy}"

    missing_phase2 = sorted(EXPECTED_PHASE2_ROUTES - registered)
    assert not missing_phase2, f"Missing Phase 2 routes: {missing_phase2}"

    openapi_spec = app.openapi()
    seen_op_ids: dict[str, str] = {}
    duplicates: list[str] = []
    for path, path_item in openapi_spec.get("paths", {}).items():
        for method, op in path_item.items():
            if not isinstance(op, dict) or "operationId" not in op:
                continue
            op_id = op["operationId"]
            endpoint_label = f"{method.upper()} {path}"
            if op_id in seen_op_ids:
                duplicates.append(f"{op_id}: {seen_op_ids[op_id]} vs {endpoint_label}")
            else:
                seen_op_ids[op_id] = endpoint_label
    assert not duplicates, f"Duplicate OpenAPI operationIds detected: {duplicates}"


def test_ontology_studio_facade_preserves_legacy_exports() -> None:
    """Existing test callers importing symbols from routers.ontology_studio continue working."""
    from opendv_platform_api.routers import ontology_studio as studio_facade

    for attr in (
        "MAX_SOURCE_UPLOAD_BYTES",
        "MAX_BUNDLE_IMPORT_BYTES",
        "get_session_manager",
        "require_viewer",
        "_normalize_delta_operation",
        "_import_bundle_sync",
    ):
        assert hasattr(studio_facade, attr), f"routers.ontology_studio missing export '{attr}'"
