"""Subprocess driver for the existing Clarion CLI.

The UI never re-implements business logic — it spawns the same
`uv run python -m proj_clarion.cli.main ...` invocation an SE would type
and streams stdout/stderr line-by-line over SSE.

Why subprocess instead of in-process Python calls:
- Preserves the CLI as the single canonical surface; UI bugs can't
  diverge from CLI behavior.
- One run = one OS process = clean cancellation via signal.
- `init_telemetry()` is `@lru_cache(maxsize=1)`; running multiple
  generate calls back-to-back in-process would reuse the cached
  TracerProvider built at API startup, which polluted the smoke
  earlier. A subprocess starts fresh.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# Whitelist of CLI subcommands the UI is allowed to spawn. Anything else 400s.
# Locks down the surface so a future bug in input handling can't escalate
# into "run any shell command".
RunKind = Literal["generate", "provision", "kg-publish", "live-tail"]
ALLOWED_RUNS: dict[RunKind, list[str]] = {
    "generate":   ["generate", "run"],
    "provision":  ["provision", "run"],
    "kg-publish": ["kg",       "publish"],
    "live-tail":  ["live-tail", "run"],
}


@dataclass
class RunRequest:
    """One UI-initiated CLI invocation."""

    kind: RunKind
    plan_id: str
    extra_args: list[str] = field(default_factory=list)


@dataclass
class RunHandle:
    """Live state for one in-flight subprocess. Held in the in-memory registry below."""

    run_id: str
    kind: RunKind
    plan_id: str
    started_at: datetime
    process: asyncio.subprocess.Process
    log_buffer: list[str] = field(default_factory=list)
    finished: bool = False
    return_code: int | None = None


# In-memory registry — fine for a local-only single-user app. If we ever ship
# this multi-user we'd persist run state, but that's not the v0.7 scope.
_RUNS: dict[str, RunHandle] = {}


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _build_argv(req: RunRequest) -> list[str]:
    """Construct the exact argv the CLI would see. Plan ids come straight from
    the request — `plan_id` itself is read by the CLI's prefix resolver, which
    refuses to match anything that doesn't look like a UUID prefix, so this is
    safe even before the whitelist runs."""
    sub = ALLOWED_RUNS[req.kind]
    return [
        "uv", "run", "python", "-m", "proj_clarion.cli.main",
        *sub,
        req.plan_id,
        *req.extra_args,
    ]


async def start_run(req: RunRequest) -> RunHandle:
    """Spawn the subprocess; return immediately with a handle whose log_buffer
    will be populated by the background reader."""
    if req.kind not in ALLOWED_RUNS:
        raise ValueError(f"run kind not allowed: {req.kind!r}")

    argv = _build_argv(req)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # interleave so log order is preserved
    )

    handle = RunHandle(
        run_id=_new_run_id(),
        kind=req.kind,
        plan_id=req.plan_id,
        started_at=datetime.now(timezone.utc),
        process=proc,
        log_buffer=[f"$ {' '.join(shlex.quote(a) for a in argv)}"],
    )
    _RUNS[handle.run_id] = handle
    asyncio.create_task(_drain(handle))  # noqa: RUF006 — fire-and-forget by design
    return handle


async def _drain(handle: RunHandle) -> None:
    """Pump the subprocess's stdout into the buffer, line by line.

    Stays alive until EOF (process exits). Sets `finished` and `return_code`
    when done so the SSE endpoint knows when to close the stream.
    """
    assert handle.process.stdout is not None
    while True:
        line = await handle.process.stdout.readline()
        if not line:
            break
        handle.log_buffer.append(line.decode(errors="replace").rstrip("\n"))
    handle.return_code = await handle.process.wait()
    handle.finished = True


def get_run(run_id: str) -> RunHandle | None:
    return _RUNS.get(run_id)


def list_runs() -> list[RunHandle]:
    """Newest first."""
    return sorted(_RUNS.values(), key=lambda r: r.started_at, reverse=True)


async def stream_run_lines(run_id: str) -> AsyncIterator[str]:
    """Yield log lines as they arrive. Sends already-buffered lines immediately,
    then polls for new ones until the process finishes."""
    handle = get_run(run_id)
    if handle is None:
        raise KeyError(run_id)

    cursor = 0
    while True:
        # Drain everything currently in the buffer
        while cursor < len(handle.log_buffer):
            yield handle.log_buffer[cursor]
            cursor += 1
        if handle.finished:
            yield f"__exit__ {handle.return_code}"
            return
        # Idle: small wait so we don't busy-loop. The producer side appends
        # whenever the subprocess writes a newline, so latency is bounded by
        # this sleep + line-buffering on the child's side.
        await asyncio.sleep(0.1)


async def cancel_run(run_id: str) -> bool:
    """SIGTERM the subprocess. Returns True if signaled, False if not running."""
    handle = get_run(run_id)
    if handle is None or handle.finished:
        return False
    handle.process.terminate()
    return True
