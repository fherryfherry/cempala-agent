"""Interactive PTY sessions for the browser Terminal menu. One PTY per WebSocket
connection; no persistence/reattachment across reloads (v1 scope) — reload gets a
fresh shell, same tradeoff FastAPI/SSE already makes elsewhere in this app.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
from dataclasses import dataclass


@dataclass
class PtySession:
    pid: int
    fd: int


def spawn(repo_path: str, cols: int = 80, rows: int = 24) -> PtySession:
    # pty.openpty() + subprocess.Popen, not pty.fork(): this backend runs inside
    # FastAPI/uvicorn, which is already multi-threaded (aiosqlite runs each DB
    # connection on its own thread) — pty.fork()'s raw os.fork() only carries the
    # calling thread into the child, so locks held by other threads can deadlock
    # it. Popen avoids that by using posix_spawn/vfork internally.
    shell = os.environ.get("SHELL", "/bin/bash")
    master_fd, slave_fd = pty.openpty()
    resize(master_fd, cols, rows)
    proc = subprocess.Popen(
        [shell],
        cwd=repo_path,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env={**os.environ, "TERM": "xterm-256color"},
        start_new_session=True,
    )
    os.close(slave_fd)
    return PtySession(pid=proc.pid, fd=master_fd)


def resize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def cleanup(session: PtySession) -> None:
    # ponytail: no idle-timeout / max-session cap in v1 — mirrors opencode --auto
    # already having no such limit; add only if orphaned shells become a real problem.
    # SIGKILL (not SIGHUP) + a blocking waitpid: this must actually terminate the
    # shell, not just ask nicely — same "kill switch must really kill" bar as the
    # run kill switch elsewhere in this app.
    with contextlib.suppress(ProcessLookupError):
        os.kill(session.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        os.close(session.fd)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(session.pid, 0)
