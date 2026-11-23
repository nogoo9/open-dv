"""Stdlib HTTP client, cross-process file-lock rate limiter, and repo doc search for Open-DV."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

DEFAULT_API_URL = "http://127.0.0.1:8420"
DEFAULT_LOOPBACK_RPS = 5.0
DEFAULT_REMOTE_RPS = 1.0
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
STRUCTURED_EXT_RE = re.compile(
    r"\.(parquet|csv|tsv|xlsx|xls|sqlite|sqlite3|db|duckdb|ddb|jsonl|ndjson|json)$", re.I
)
UNSTRUCTURED_EXT_RE = re.compile(r"\.(pdf|docx|doc|md|markdown|txt|html|htm|rtf)$", re.I)


class RateLimitError(Exception):
    """Raised when HTTP 429 rate limits persist after retries."""


class OpenDVHttpError(RuntimeError):
    """HTTP error carrying status code and decoded response body."""

    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} from {url}: {body}")


def _is_loopback_url(api_url: str) -> bool:
    host = (urllib_parse.urlparse(api_url).hostname or "").lower()
    return host in LOOPBACK_HOSTS


@contextmanager
def _interprocess_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an OS-level lock across concurrent processes on Windows or POSIX."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_fh:
        lock_fh.seek(0, os.SEEK_END)
        if lock_fh.tell() == 0:
            lock_fh.write(b"0")
            lock_fh.flush()
        lock_fh.seek(0)
        if os.name == "nt":
            import msvcrt

            for _ in range(50):
                try:
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                lock_fh.seek(0)
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_fh.seek(0)
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


class CrossProcessRateLimiter:
    """File-lock and monotonic-clock rate limiter shared across concurrent agents."""

    def __init__(self, rps: float, state_name: str = "opendv_workflow_ratelimit") -> None:
        self.rps = max(rps, 0.1)
        self.min_interval = 1.0 / self.rps
        tmp = Path(tempfile.gettempdir())
        self.lock_path = tmp / f"{state_name}.lock"
        self.state_path = tmp / f"{state_name}.txt"
        self._last_mono = 0.0

    def acquire(self) -> None:
        """Sleep until inter-request spacing is satisfied across processes."""
        with _interprocess_lock(self.lock_path):
            now_wall = time.time()
            prev_wall = 0.0
            if self.state_path.is_file():
                try:
                    prev_wall = float(self.state_path.read_text(encoding="utf-8").strip() or "0")
                except ValueError:
                    prev_wall = 0.0
            elapsed = max(0.0, time.monotonic() - self._last_mono)
            wall_elapsed = max(0.0, now_wall - prev_wall) if prev_wall > 0 else self.min_interval
            wait = max(self.min_interval - wall_elapsed, 0.0)
            if self._last_mono > 0:
                wait = max(wait, max(0.0, self.min_interval - elapsed))
            if wait > 0:
                time.sleep(wait)
            self._last_mono = time.monotonic()
            self.state_path.write_text(f"{time.time():.6f}", encoding="utf-8")


class OpenDVClient:
    """Stdlib urllib client for Open-DV REST API with rate limiting and retry handling."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        rps: float | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        effective_rps = rps if rps is not None else (
            DEFAULT_LOOPBACK_RPS if _is_loopback_url(self.api_url) else DEFAULT_REMOTE_RPS
        )
        self.rps = effective_rps
        self.timeout = timeout
        self.limiter = CrossProcessRateLimiter(effective_rps)

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        clean = path if path.startswith("/") else f"/{path}"
        url = f"{self.api_url}{clean}"
        if query:
            pairs = [(k, str(v)) for k, v in query.items() if v is not None]
            if pairs:
                url = f"{url}?{urllib_parse.urlencode(pairs)}"
        return url

    def request_raw(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: Any = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute an HTTP call with cross-process pacing, 5xx backoff, and 429 errors."""
        url = self._build_url(path, query)
        body_bytes = raw_body
        req_headers = {"Accept": "application/json"}
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)

        for attempt in range(retries):
            self.limiter.acquire()
            req = urllib_request.Request(url, data=body_bytes, headers=req_headers, method=method)
            try:
                with urllib_request.urlopen(req, timeout=self.timeout) as resp:
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    return resp.status, resp_headers, resp.read()
            except urllib_error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429:
                    wait = min(8, 2**attempt)
                    print(
                        f"Rate limited (HTTP 429) on {url} at {self.rps:.2f} req/s; "
                        f"retrying in {wait}s ({attempt + 1}/{retries})...",
                        file=sys.stderr,
                    )
                    if attempt == retries - 1:
                        raise RateLimitError(
                            f"HTTP 429 from {url} (configured limit {self.rps:.2f} req/s): "
                            f"{err_body}"
                        ) from exc
                    time.sleep(wait)
                    continue
                if exc.code >= 500:
                    wait = 2**attempt
                    print(
                        f"Transient server error HTTP {exc.code} on {url}; "
                        f"retrying in {wait}s ({attempt + 1}/{retries})...",
                        file=sys.stderr,
                    )
                    if attempt == retries - 1:
                        raise OpenDVHttpError(exc.code, url, err_body) from exc
                    time.sleep(wait)
                    continue
                raise OpenDVHttpError(exc.code, url, err_body) from exc
            except urllib_error.URLError as exc:
                raise RuntimeError(
                    f"Cannot reach Open-DV server at {url}: {exc.reason}. "
                    "Start it with: uv run opendv serve --host 127.0.0.1 --port 8420"
                ) from exc
        raise RuntimeError(f"Exhausted retries for {url}")

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> Any:
        """Perform an HTTP call expecting a JSON response."""
        status, _, raw = self.request_raw(
            method, path, query=query, json_body=json_body, headers=headers, retries=retries
        )
        if status == 204 or not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def upload_file(
        self,
        session_id: str,
        file_path: Path,
        *,
        ocr_mode: str = "auto",
        do_not_send: bool = False,
        description: str | None = None,
    ) -> Any:
        """Upload a local document or structured file via multipart/form-data."""
        boundary = f"----OpenDVBoundary{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n".encode()
            )

        add_field("ocr_mode", ocr_mode)
        add_field("do_not_send_to_agent", "true" if do_not_send else "false")
        if description:
            add_field("description", description)

        file_bytes = file_path.read_bytes()
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{file_path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
            + file_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )
        body = b"".join(parts)
        _, _, raw = self.request_raw(
            "POST",
            f"/api/v1/ontology/studio/sessions/{urllib_parse.quote(session_id)}/sources",
            raw_body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return json.loads(raw.decode("utf-8"))

    def poll_studio_job(
        self, session_id: str, job_id: str, timeout_s: float = 300.0
    ) -> dict[str, Any]:
        """Poll a Studio async job until completion or failure."""
        deadline = time.monotonic() + timeout_s
        path = (
            f"/api/v1/ontology/studio/sessions/{urllib_parse.quote(session_id)}"
            f"/jobs/{urllib_parse.quote(job_id)}"
        )
        while time.monotonic() < deadline:
            data = self.request_json("GET", path)
            status = str(data.get("status", "")).upper()
            if status in {"COMPLETED", "SUCCEEDED", "DONE"}:
                return data
            if status in {"FAILED", "CANCELLED", "CANCELED", "HOLD", "PAUSED_HOLD", "BUDGET_HOLD"}:
                return data
            time.sleep(1.0)
        raise TimeoutError(f"Timed out after {timeout_s}s waiting for Studio job '{job_id}'.")

    def poll_pipeline_run(self, run_id: str, timeout_s: float = 600.0) -> dict[str, Any]:
        """Poll a DV 2.0 pipeline run until settled (COMPLETED, PAUSED_HOLD, FAILED, or ABORTED)."""
        deadline = time.monotonic() + timeout_s
        path = f"/api/v1/runs/{urllib_parse.quote(run_id)}"
        while time.monotonic() < deadline:
            data = self.request_json("GET", path)
            status = str(data.get("status", "")).upper()
            if status and status not in {"RUNNING", "PENDING", "QUEUED"}:
                return data
            time.sleep(1.5)
        raise TimeoutError(f"Timed out after {timeout_s}s waiting for pipeline run '{run_id}'.")

    def extract_baseline_contract(self, run_id: str) -> str | None:
        """Fetch final_contract.yaml / contract_content from a certified run for D12."""
        rep = self.request_json("GET", f"/api/v1/runs/{urllib_parse.quote(run_id)}/report")
        if (
            isinstance(rep, dict)
            and isinstance(rep.get("contract_content"), str)
            and rep["contract_content"].strip()
        ):
            return rep["contract_content"]
        arts = self.request_json("GET", f"/api/v1/runs/{urllib_parse.quote(run_id)}/artifacts")
        if isinstance(arts, list):
            for art in arts:
                if not isinstance(art, dict):
                    continue
                aid = str(art.get("artifact_id", ""))
                fname = str(art.get("filename", ""))
                content = art.get("content")
                if isinstance(content, str) and content.strip() and (
                    aid == "final_contract" or fname == "final_contract.yaml"
                ):
                    return content
        return None


def slug_system(raw: str) -> str:
    """Normalize a filename or identifier into a clean source_system slug."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    return cleaned or "source"


def partition_session_sources(
    sources: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split Studio manifest sources into PipelineInputDTO feeds vs unstructured_documents."""
    feeds: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    for idx, src in enumerate(sources):
        if src.get("do_not_send_to_agent"):
            continue
        fname = str(src.get("file_name") or src.get("filename") or src.get("source_id") or "")
        mime = str(src.get("mime_type") or "").split(";")[0].strip().lower()
        sid = str(src.get("source_id") or f"src-{idx + 1}")
        sha = src.get("source_sha256")
        cas_uri = f"cas://{sha}" if sha else fname
        is_unstructured = not STRUCTURED_EXT_RE.search(fname) and (
            bool(UNSTRUCTURED_EXT_RE.search(fname))
            or mime in {"application/pdf", "text/markdown", "text/plain", "text/html"}
        )
        if is_unstructured:
            matched_text = "\n\n".join(
                str(c.get("text") or c.get("text_snippet") or "")
                for c in chunks
                if (
                    c.get("source_id") == sid
                    or c.get("document_id") == sid
                    or c.get("source_file") == fname
                )
                and str(c.get("text") or c.get("text_snippet") or "").strip()
            )
            doc: dict[str, Any] = {
                "doc_id": sid,
                "document_path": cas_uri,
                "metadata": {"title": fname, "source_id": sid},
            }
            if mime:
                doc["mime_type"] = mime
            if matched_text:
                doc["content"] = matched_text
            docs.append(doc)
        else:
            ext = Path(fname).suffix.lstrip(".").lower()
            feeds.append(
                {
                    "feed_id": sid,
                    "source_system": slug_system(fname),
                    "uri": cas_uri,
                    "options": {
                        "table_name": slug_system(Path(fname).stem),
                        "source_name": fname,
                        **({"source_format": ext} if ext else {}),
                    },
                }
            )
    return feeds, docs


def search_repo_docs(repo_root: Path, query: str, scope: str, limit: int) -> list[dict[str, Any]]:
    """Search Open-DV markdown docs and specs for Q&A grounding."""
    candidates: list[Path] = []
    if scope in {"all", "design"}:
        root_docs = [
            repo_root / "DESIGN.md",
            repo_root / "README.md",
            repo_root / "AGENTS.md",
            repo_root / "CLAUDE.md",
        ]
        candidates.extend(p for p in root_docs if p.is_file())
    if scope in {"all", "docs"} and (repo_root / "docs").is_dir():
        candidates.extend(sorted((repo_root / "docs").glob("*.md")))
    if scope in {"all", "cli"}:
        cli_spec = repo_root / "schemas" / "v1" / "cli-spec.json"
        if cli_spec.is_file():
            candidates.append(cli_spec)
    if scope in {"all", "api"}:
        api_spec = repo_root / "schemas" / "v1" / "openapi.json"
        if api_spec.is_file():
            candidates.append(api_spec)

    terms = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not terms:
        return []
    hits: list[dict[str, Any]] = []
    for path in candidates:
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
