"""Comandos com prazo, saída limitada e encerramento do grupo de processos."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MAX_OUTPUT = 200_000


def _clip(data: bytes) -> tuple[str, bool]:
    text = data.decode("utf-8", errors="replace")
    if len(text) <= MAX_OUTPUT:
        return text, False
    half = MAX_OUTPUT // 2
    return text[:half] + "\n…[saída truncada]…\n" + text[-half:], True


def run(command: str, cwd: Path, timeout: int, env: dict[str, str]) -> dict:
    windows = sys.platform == "win32"
    argv = (
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        if windows
        else ["/bin/bash", "-lc", command]
    )
    options = {}
    if windows:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        options["start_new_session"] = True
    started = time.monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **options,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(process, windows)
        stdout, stderr = process.communicate()
    stdout_text, cut_out = _clip(stdout)
    stderr_text, cut_err = _clip(stderr)
    return {
        "exit_code": process.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "timed_out": timed_out,
        "truncated": cut_out or cut_err,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _kill(process, windows):
    try:
        if windows:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()
