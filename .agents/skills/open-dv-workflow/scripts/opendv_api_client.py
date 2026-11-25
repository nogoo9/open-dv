"""Shared HTTP client, rate limiter, and repo doc search for Open-DV V2 workflows."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8420"
DEFAULT_LOOPBACK_RPS = 5.0
DEFAULT_REMOTE_RPS = 1.0
MAX_ATTEMPTS = 4


class ApiError(RuntimeError):
    """HTTP error raised when Open-DV V2 API returns a non-success response."""

    def __init__(self, status_code: int, method: str, url: str, body: Any) -> None:
        self.status_code = status_code
        self.method = method
        self.url = url
        self.body = body
        detail = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
        super().__init__(f"{method} {url} failed with HTTP {status_code}: {detail}")


class RateLimitError(ApiError):
    """Raised when persistent HTTP 429 responses exceed retry budget."""


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the Open-DV repository root containing pyproject.toml and schemas/v2/."""
    cur = (start or Path(__file__)).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "schemas" / "v2"
        ).is_dir():
            return candidate
    return Path.cwd().resolve()


def is_loopback_url(api_url: str) -> bool:
    """Return True when the target host resolves to loopback."""
    host = (urlparse(api_url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _rate_lock_path(api_url: str) -> Path:
    safe_host = re.sub(r"[^a-z0-9._-]+", "_", urlparse(api_url).netloc.lower() or "local")
    return Path(tempfile.gettempdir()) / f"opendv_workflow_rate_{safe_host}.lock"


@contextmanager
def _cross_process_rate_slot(api_url: str, rps: float) -> Any:
    """Coordinate outgoing requests across subagents using a cross-process file lock."""
    interval = 1.0 / max(rps, 0.1)
    lock_file = _rate_lock_path(api_url)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw = handle.read().decode("utf-8", errors="ignore").strip()
            last_ts = float(raw) if raw else 0.0
            now = time.monotonic()
            wait = interval - (now - last_ts)
            if wait > 0:
                time.sleep(wait)
            new_ts = f"{time.monotonic():.6f}".encode()
            handle.seek(0)
            handle.truncate(0)
            handle.write(new_ts)
            handle.flush()
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    yield


class OpenDVClient:
    """Rate-limited synchronous REST client targeting Open-DV V2 `/api/v1` endpoints."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        *,
        rps: float | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.rps = (
            rps
            if rps is not None
            else (DEFAULT_LOOPBACK_RPS if is_loopback_url(self.api_url) else DEFAULT_REMOTE_RPS)
        )
        self._client = httpx.Client(base_url=self.api_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | list[Any] | None = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        expect_bytes: bool = False,
    ) -> Any:
        url = path if path.startswith("/") else f"/{path}"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with _cross_process_rate_slot(self.api_url, self.rps):
                resp = self._client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    files=files,
                )
            if resp.status_code in {429, 502, 503, 504} and attempt < MAX_ATTEMPTS:
                delay = float(2 ** (attempt - 1))
                print(
                    f"[opendv-workflow] HTTP {resp.status_code} on {method} {url}; "
                    f"retrying in {delay:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                try:
                    parsed: Any = resp.json()
                except ValueError:
                    parsed = resp.text
                if resp.status_code == 429:
                    raise RateLimitError(
                        resp.status_code,
                        method,
                        f"{self.api_url}{url} (limit={self.rps} rps)",
                        parsed,
                    )
                raise ApiError(resp.status_code, method, f"{self.api_url}{url}", parsed)
            if expect_bytes:
                return resp.content
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                return resp.text
        raise RuntimeError(f"Exhausted retries for {method} {url}")

    def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post_json(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self.request("POST", path, json_body=body or {}, params=params)

    def put_json(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PUT", path, json_body=body)

    def patch_json(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PATCH", path, json_body=body)

    def delete_json(self, path: str) -> Any:
        return self.request("DELETE", path)

    def get_bytes(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        data = self.request("GET", path, params=params, expect_bytes=True)
        return bytes(data)

    def poll_until(
        self,
        path: str,
        *,
        terminal_statuses: set[str],
        timeout_seconds: float = 180.0,
        interval_seconds: float = 1.5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = dict(self.get_json(path))
            status = str(state.get("status", "")).upper()
            if status in terminal_statuses or time.monotonic() >= deadline:
                return state
            time.sleep(interval_seconds)


def slug_id(raw: str) -> str:
    """Normalize a raw name into an Open-DV V2 kebab/snake slug."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw.strip().lower()).strip("_-")
    return cleaned or "opendv_project"


def write_json_output(output_path: str | None, payload: Any) -> None:
    """Write formatted JSON to output_path (or compact summary to stdout if omitted)."""
    rendered = json.dumps(payload, indent=2, default=str)
    if output_path:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output_file": str(dest), "bytes": len(rendered)}))
        return
    print(rendered)


def search_repo_docs(
    repo_root: Path,
    query: str,
    scope: str = "all",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Search Open-DV V2 markdown docs and schemas/v2 contracts for Q&A grounding."""
    candidates: list[Path] = []
    if scope in {"all", "design"}:
        root_docs = [
            repo_root / "DESIGN.md",
            repo_root / "README.md",
            repo_root / "AGENTS.md",
            repo_root / "CLAUDE.md",
            repo_root / "examples" / "v2_asset_management" / "README.md",
        ]
        candidates.extend(p for p in root_docs if p.is_file())
        if (repo_root / "docs" / "designs").is_dir():
            candidates.extend(sorted((repo_root / "docs" / "designs").glob("*.md")))
    if scope in {"all", "docs"} and (repo_root / "docs").is_dir():
        candidates.extend(sorted((repo_root / "docs").glob("*.md")))
    if scope in {"all", "cli"}:
        cli_spec = repo_root / "schemas" / "v2" / "cli-spec.json"
        if cli_spec.is_file():
            candidates.append(cli_spec)
    if scope in {"all", "api"}:
        for name in ("openapi.json", "error-codes.json", "baseline-digests.json"):
            spec_path = repo_root / "schemas" / "v2" / name
            if spec_path.is_file():
                candidates.append(spec_path)

    terms = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not terms:
        return []
    hits: list[dict[str, Any]] = []
    seen_files: set[Path] = set()
    for path in candidates:
        if path in seen_files:
            continue
        seen_files.add(path)
        rel = path.relative_to(repo_root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, 1):
            lower = line.lower()
            score = sum(1 for t in terms if t in lower)
            if score == 0:
                continue
            start = max(0, idx - 2)
            end = min(len(lines), idx + 2)
            hits.append(
                {
                    "score": score,
                    "file": rel,
                    "line": idx,
                    "snippet": "\n".join(lines[start:end])[:420],
                }
            )
    hits.sort(key=lambda item: (-int(item["score"]), str(item["file"]), int(item["line"])))
    return hits[:limit]
