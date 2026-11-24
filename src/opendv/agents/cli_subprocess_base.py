"""Isolated child env, Windows shim unwrap, temp cwd, token counting, and process-tree reaper."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

import tiktoken

from opendv.agents._cli_ndjson import (
    extract_json_from_stdout,
    is_non_result_stream_event,
    parse_ndjson_step_events,
    parse_ndjson_stream_line,
    redact_telemetry_text,
    strip_markdown_json_fences,
    validate_cli_structured_output,
)
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunStep

if TYPE_CHECKING:
    from opendv.agents.ports import AgentTurnRequest

_MAX_CLI_FLAG_CHARS = 7500
_CL100K_ENCODING = tiktoken.get_encoding("cl100k_base")


def build_stateless_turn_prompt(
    request: AgentTurnRequest,
    *,
    include_system: bool = True,
    inline_schema_json: str | None = None,
) -> str:
    """Build stateless turn prompt including STATELESS HOLD RESUME CONTEXT when present."""
    parts: list[str] = []
    if include_system and request.system_instructions.strip():
        parts.append(request.system_instructions.strip())
    if request.checkpoint is not None:
        cp = request.checkpoint
        resume_blob = json.dumps(
            {
                "checkpoint_phase": cp.phase,
                "checkpoint_status": cp.status,
                "resume_payload": cp.resume_payload,
            },
            separators=(",", ":"),
        )
        parts.append(f"STATELESS HOLD RESUME CONTEXT:\n{resume_blob}")
    if inline_schema_json:
        parts.append(f"Return ONLY valid JSON conforming to schema:\n{inline_schema_json}")
    parts.append(request.prompt.strip())
    return "\n\n".join(p for p in parts if p)

BASE_OS_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "COMSPEC",
    "WINDIR",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
)

_ALLOWLISTED_OS_ENV_KEYS: tuple[str, ...] = (
    *BASE_OS_ENV_ALLOWLIST,
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
)

_PROVIDER_KEY_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "agy": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "claude-cli": ("ANTHROPIC_API_KEY",),
}


def count_cl100k_tokens(text: str) -> int:
    """Count exact cl100k_base tokens for a string."""
    if not text:
        return 0
    return len(_CL100K_ENCODING.encode(text))


def unwrap_windows_npm_shim(
    bin_path: str,
    *,
    os_name: str | None = None,
    is_file_fn: Callable[[str], bool] | None = None,
) -> str:
    """Bypass Windows npm .cmd/.bat shims to invoke native claude.exe directly."""
    effective_os = os.name if os_name is None else os_name
    check_file = os.path.isfile if is_file_fn is None else is_file_fn
    if effective_os == "nt" and bin_path.lower().endswith((".cmd", ".bat")):
        exe = os.path.join(
            os.path.dirname(bin_path),
            "node_modules",
            "@anthropic-ai",
            "claude-code",
            "bin",
            "claude.exe",
        )
        if check_file(exe):
            return exe
    return bin_path


def build_isolated_child_env(
    *,
    provider: Literal["agy", "claude-cli"],
    step: RunStep,
    role_kind: Literal["coord", "scout", "none"] = "none",
    role_token: str | None = None,
    api_key: str | None = None,
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal allowlisted environment for isolated CLI child execution."""
    src_env = os.environ if parent_env is None else parent_env
    child_env: dict[str, str] = {}
    for key in _ALLOWLISTED_OS_ENV_KEYS:
        val = src_env.get(key)
        if val is not None and val != "":
            child_env[key] = val

    child_env["NO_COLOR"] = "1"
    child_env["NoDefaultCurrentDirectoryInExePath"] = "1"

    allowed_provider_keys = _PROVIDER_KEY_ALLOWLIST.get(provider, ())
    if api_key and api_key.strip() and allowed_provider_keys:
        child_env[allowed_provider_keys[0]] = api_key.strip()
    else:
        for pkey in allowed_provider_keys:
            val = src_env.get(pkey)
            if val and val.strip():
                child_env[pkey] = val.strip()

    if step == "step4" and role_kind == "coord" and role_token and role_token.strip():
        child_env["OPENDV_MCP_COORD_TOKEN"] = role_token.strip()
    elif step == "step4" and role_kind == "scout" and role_token and role_token.strip():
        child_env["OPENDV_MCP_SCOUT_TOKEN"] = role_token.strip()

    return child_env


class IsolatedTempCwd:
    """Context manager providing an isolated empty temporary working directory."""

    def __init__(self, *, prefix: str = "opendv_agent_cwd_") -> None:
        self._prefix = prefix
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self._temp_dir = tempfile.TemporaryDirectory(prefix=self._prefix)
        self.path = Path(self._temp_dir.name)
        return self.path

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._temp_dir is not None:
            with contextlib.suppress(OSError):
                self._temp_dir.cleanup()
        self._temp_dir = None
        self.path = None


def _configure_win32_kernel32() -> Any | None:
    if os.name != "nt":
        return None
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    kernel32: Any = windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _attach_windows_job_object(pid: int) -> int | None:
    """Create a KILL_ON_JOB_CLOSE Win32 Job Object and attach pid (best-effort)."""
    kernel32 = _configure_win32_kernel32()
    if kernel32 is None:
        return None
    raw_job = kernel32.CreateJobObjectW(None, None)
    job_handle = int(raw_job) if raw_job else 0
    if not job_handle:
        return None

    class _BasicLimitInfo(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(f"c{i}", ctypes.c_uint64) for i in range(6)]

    class _ExtendedLimitInfo(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInfo),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _ExtendedLimitInfo()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok_info = kernel32.SetInformationJobObject(
        ctypes.c_void_p(job_handle),
        9,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    raw_proc = kernel32.OpenProcess(0x001F0FFF, False, pid)
    proc_handle = int(raw_proc) if raw_proc else 0
    if ok_info and proc_handle:
        assigned = bool(
            kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(job_handle),
                ctypes.c_void_p(proc_handle),
            )
        )
        kernel32.CloseHandle(ctypes.c_void_p(proc_handle))
        if assigned:
            return job_handle
    if proc_handle:
        kernel32.CloseHandle(ctypes.c_void_p(proc_handle))
    kernel32.CloseHandle(ctypes.c_void_p(job_handle))
    return None


def _close_windows_job_object(job_handle: int | None) -> None:
    if job_handle is None:
        return
    kernel32 = _configure_win32_kernel32()
    if kernel32 is not None:
        with contextlib.suppress(OSError, ValueError):
            kernel32.CloseHandle(ctypes.c_void_p(job_handle))


async def reap_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    job_handle: int | None = None,
) -> None:
    """Terminate a subprocess and its entire child process tree cleanly."""
    try:
        if proc.returncode is None:
            if os.name == "nt":
                taskkill_bin = shutil.which("taskkill")
                if taskkill_bin and proc.pid:
                    with contextlib.suppress(OSError, subprocess.SubprocessError):
                        await asyncio.to_thread(
                            subprocess.run,
                            [taskkill_bin, "/T", "/F", "/PID", str(proc.pid)],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
            else:
                killpg = getattr(os, "killpg", None)
                sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                if callable(killpg) and proc.pid:
                    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                        killpg(proc.pid, sigkill)
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
            with contextlib.suppress(OSError, ProcessLookupError, TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
    finally:
        if job_handle is not None:
            _close_windows_job_object(job_handle)
            job_handle = None


async def stream_cli_subprocess_lines(
    cmd: list[str],
    *,
    input_text: str,
    env: dict[str, str],
    cwd: Path,
    timeout_seconds: float,
    state_out: dict[str, Any],
    cli_label: str,
) -> AsyncIterator[str]:
    """Spawn CLI in an isolated cwd with OS process-tree containment and yield stdout lines."""
    cwd.mkdir(parents=True, exist_ok=True)
    spawn_kwargs: dict[str, Any] = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": env,
        "cwd": str(cwd),
        "limit": 16 * 1024 * 1024,
    }
    if os.name == "nt":
        spawn_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        spawn_kwargs["start_new_session"] = True

    proc = await asyncio.create_subprocess_exec(*cmd, **spawn_kwargs)
    job_handle = _attach_windows_job_object(proc.pid) if proc.pid else None
    stdout_lines: list[str] = []
    stderr_bytes = b""
    returncode = -1
    try:
        try:
            async with asyncio.timeout(timeout_seconds):
                if proc.stdin is not None:
                    stdin_ok = False
                    try:
                        if input_text:
                            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                                proc.stdin.write(input_text.encode("utf-8"))
                                await proc.stdin.drain()
                        stdin_ok = True
                    finally:
                        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                            proc.stdin.close()
                    if stdin_ok:
                        wait_closed = getattr(proc.stdin, "wait_closed", None)
                        if callable(wait_closed):
                            with contextlib.suppress(
                                BrokenPipeError, ConnectionResetError, OSError, TimeoutError
                            ):
                                await asyncio.wait_for(wait_closed(), timeout=0.05)
                assert proc.stdout is not None
                while True:
                    raw_bytes = await proc.stdout.readline()
                    if not raw_bytes:
                        break
                    decoded = raw_bytes.decode("utf-8", errors="replace")
                    stdout_lines.append(decoded)
                    yield decoded.rstrip("\r\n")
                stderr_bytes = await proc.stderr.read() if proc.stderr is not None else b""
                returncode = await proc.wait()
        except TimeoutError as exc:
            raise OpenDVError(
                f"{cli_label} timed out after {timeout_seconds}s.",
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            ) from exc
    finally:
        await reap_process_tree(proc, job_handle=job_handle)

    state_out["returncode"] = returncode
    state_out["stdout"] = "".join(stdout_lines)
    state_out["stderr"] = stderr_bytes.decode("utf-8", errors="replace")


__all__ = [
    "BASE_OS_ENV_ALLOWLIST",
    "_MAX_CLI_FLAG_CHARS",
    "IsolatedTempCwd",
    "build_isolated_child_env",
    "build_stateless_turn_prompt",
    "count_cl100k_tokens",
    "extract_json_from_stdout",
    "is_non_result_stream_event",
    "parse_ndjson_step_events",
    "parse_ndjson_stream_line",
    "reap_process_tree",
    "redact_telemetry_text",
    "stream_cli_subprocess_lines",
    "strip_markdown_json_fences",
    "unwrap_windows_npm_shim",
    "validate_cli_structured_output",
]
