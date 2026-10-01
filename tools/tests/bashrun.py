"""Finding a bash that can run tools/*.sh, and running it, for the tests in this folder.

On the Captain's PC `bash` on PATH is often C:\\Windows\\System32\\bash.exe, the WSL launcher: it can't see D:\\ paths the way
the scripts expect, so a plain `shutil.which("bash")` would test the wrong shell. Here Windows uses Git for Windows' bash
(<install>\\bin\\bash.exe, the launcher that sets up PATH); Linux and macOS use whatever `bash` is on PATH (CI's ubuntu).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path


def find_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        for sub in ("Git", os.path.join("Programs", "Git")):
            candidate = Path(base) / sub / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return None


BASH = find_bash()


def posix(path) -> str:
    """C:/Users/x/file: what bash on Windows takes as it is (and what Linux already has)."""
    return Path(path).as_posix()


def bash_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    if os.name == "nt" and BASH:
        root = Path(BASH).parent.parent               # <install>\bin\bash.exe -> <install>
        env["PATH"] = os.pathsep.join([str(root / "usr" / "bin"), str(root / "mingw64" / "bin"), env.get("PATH", "")])
    env.update(extra or {})
    return env


def run_bash(args: list[str], env: dict | None = None, timeout: float = 60, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, *args], env=bash_env(env), capture_output=True, text=True, timeout=timeout, input=input)


def free_port() -> int:
    """A port nothing is listening on right now (so a /health probe of it fails at once)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
