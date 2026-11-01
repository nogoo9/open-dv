"""HTTP API client for `opendv` and `axi-dv` CLI commands in `--api-url` remote mode."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx


class OpenDVCliApiError(RuntimeError):
    """Raised when the remote Open-DV Platform API returns an error status."""

    def __init__(self, status_code: int, message: str, payload: Any = None) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload


def validate_api_url(api_url: str) -> str:
    """Validate that `--api-url` uses an explicit http:// or https:// scheme."""
    cleaned = api_url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"Invalid --api-url {api_url!r}: must start with http:// or https:// and include host."
        )
    return cleaned


class OpenDVCliApiClient:
    """Synchronous HTTP client wrapping `/api/v1` endpoints with 5-Tier RBAC headers."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        role: str = "INTERNAL_MODELER",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_api_url(base_url)
        self.role = role
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "X-OpenDV-Role": self.role,
                "Accept": "application/json",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying HTTPX client connection pool."""
        self._client.close()

    def __enter__(self) -> OpenDVCliApiClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _handle_response(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("detail") or body.get("title") or response.text
            except Exception:
                body = None
                detail = response.text
            raise OpenDVCliApiError(response.status_code, str(detail), payload=body)
        if not response.content:
            return {}
        return response.json()

    def get_health(self) -> dict[str, Any]:
        """GET `/api/v1/health`."""
        res = self._client.get("/api/v1/health")
        return dict(self._handle_response(res))

    def get_capabilities(self) -> dict[str, Any]:
        """GET `/api/v1/platform/capabilities`."""
        res = self._client.get("/api/v1/platform/capabilities")
        return dict(self._handle_response(res))

    def list_projects(self) -> dict[str, Any]:
        """GET `/api/v1/projects`."""
        res = self._client.get("/api/v1/projects")
        data = self._handle_response(res)
        if isinstance(data, list):
            return {"items": data, "total": len(data)}
        return dict(data)

    def get_project(self, project_id: str) -> dict[str, Any]:
        """GET `/api/v1/projects/{project_id}`."""
        res = self._client.get(f"/api/v1/projects/{project_id}")
        return dict(self._handle_response(res))

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `/api/v1/projects`."""
        res = self._client.post("/api/v1/projects", json=payload)
        return dict(self._handle_response(res))

    def list_runs(self, project_id: str | None = None) -> dict[str, Any]:
        """GET `/api/v1/runs`."""
        params = {"project_id": project_id} if project_id else None
        res = self._client.get("/api/v1/runs", params=params)
        data = self._handle_response(res)
        if isinstance(data, list):
            return {"items": data, "total": len(data)}
        return dict(data)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """GET `/api/v1/runs/{run_id}`."""
        res = self._client.get(f"/api/v1/runs/{run_id}")
        return dict(self._handle_response(res))

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `/api/v1/runs`."""
        res = self._client.post("/api/v1/runs", json=payload)
        return dict(self._handle_response(res))

    def abort_run(self, run_id: str) -> dict[str, Any]:
        """POST `/api/v1/runs/{run_id}/abort`."""
        res = self._client.post(f"/api/v1/runs/{run_id}/abort")
        return dict(self._handle_response(res))

    def get_gates(self, run_id: str) -> dict[str, Any]:
        """GET `/api/v1/runs/{run_id}/gates`."""
        res = self._client.get(f"/api/v1/runs/{run_id}/gates")
        return dict(self._handle_response(res))

    def evaluate_gates(
        self, run_id: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """POST `/api/v1/runs/{run_id}/gates`."""
        res = self._client.post(f"/api/v1/runs/{run_id}/gates", json=payload or {})
        return dict(self._handle_response(res))

    def get_sandbox(self, run_id: str) -> dict[str, Any]:
        """GET `/api/v1/runs/{run_id}/sandbox`."""
        res = self._client.get(f"/api/v1/runs/{run_id}/sandbox")
        return dict(self._handle_response(res))

    def execute_sandbox(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `/api/v1/runs/{run_id}/sandbox`."""
        res = self._client.post(f"/api/v1/runs/{run_id}/sandbox", json=payload)
        return dict(self._handle_response(res))

    def get_decisions(self, run_id: str) -> dict[str, Any]:
        """GET `/api/v1/runs/{run_id}/decisions`."""
        res = self._client.get(f"/api/v1/runs/{run_id}/decisions")
        return dict(self._handle_response(res))

    def submit_decision(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `/api/v1/runs/{run_id}/decisions`."""
        res = self._client.post(f"/api/v1/runs/{run_id}/decisions", json=payload)
        return dict(self._handle_response(res))

    def get_workbench_view(self, run_id: str, view_id: str) -> dict[str, Any]:
        """GET `/api/v1/runs/{run_id}/workbench/{view_id}`."""
        res = self._client.get(f"/api/v1/runs/{run_id}/workbench/{view_id}")
        return dict(self._handle_response(res))

    def get_artifact(self, sha256: str) -> dict[str, Any]:
        """GET `/api/v1/artifacts/{sha256}`."""
        res = self._client.get(f"/api/v1/artifacts/{sha256}")
        return dict(self._handle_response(res))

    def upload_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `/api/v1/artifacts`."""
        res = self._client.post("/api/v1/artifacts", json=payload)
        return dict(self._handle_response(res))

    def stream_events_text(
        self, run_id: str, *, last_event_id: str | None = None
    ) -> str:
        """GET `/api/v1/runs/{run_id}/events` and return the raw SSE stream body."""
        headers: dict[str, str] = {"Accept": "text/event-stream"}
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        res = self._client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
        if res.status_code >= 400:
            self._handle_response(res)
        return res.text
