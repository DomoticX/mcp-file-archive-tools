"""Subprocess execution helpers shared by every backend."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 300  # seconds


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "cp850"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _run_raw(
    exe: Path,
    args: list[str],
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    input_data: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    if not exe.exists():
        raise FileNotFoundError(f"Required executable not found: {exe}")
    kwargs: dict[str, Any] = {"cwd": str(cwd) if cwd else None, "capture_output": True, "timeout": timeout}
    if input_data is not None:
        kwargs["input"] = input_data
    else:
        # No stdin needed: explicitly close it so a tool that unexpectedly
        # prompts fails fast instead of hanging the MCP server.
        kwargs["stdin"] = subprocess.DEVNULL
    try:
        proc = subprocess.run([str(exe), *args], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"'{exe.name} {' '.join(args)}' timed out after {timeout}s") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _run(
    exe: Path,
    args: list[str],
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    input_data: bytes | None = None,
) -> tuple[int, str, str]:
    rc, out, err = _run_raw(exe, args, cwd=cwd, timeout=timeout, input_data=input_data)
    return rc, _decode(out), _decode(err)


def _describe_exit_code(rc: int, codes: dict[int, str]) -> str:
    return codes.get(rc, f"Unknown exit code {rc}")


def _check(rc: int, out: str, err: str, action: str, codes: dict[int, str]) -> None:
    if rc != 0:
        detail = (err or out).strip()
        raise RuntimeError(f"{action} failed: {_describe_exit_code(rc, codes)} (exit {rc}).\n{detail}")
