"""Per-plan cursor for the live-tailer.

The cursor is the highest `event_id` we've already emitted as a Loki log line
for a given plan. Persisted to disk so a restart resumes cleanly.

Format on disk: a single integer in plain text, atomically replaced on every
update. Atomicity matters because a partial write could send us back to the
beginning of the table on restart and we'd duplicate every log line we'd
already shipped.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class Cursor:
    """File-backed monotonic int cursor for a single plan_id."""

    def __init__(self, plan_id: str, *, root: Path | None = None) -> None:
        self._plan_id = plan_id
        base = root or Path(os.environ.get("CLARION_DATA_DIR", "data")) / "livetail"
        base.mkdir(parents=True, exist_ok=True)
        self._path = base / f"{plan_id}.cursor"
        self._value = self._load()

    def _load(self) -> int:
        if not self._path.exists():
            return 0
        try:
            return int(self._path.read_text().strip() or "0")
        except (OSError, ValueError):
            return 0

    @property
    def value(self) -> int:
        return self._value

    def advance_to(self, new_value: int) -> None:
        """Atomically replace the cursor file. Refuses to go backwards."""
        if new_value <= self._value:
            return
        # Write to a temp file in the same directory, then rename — POSIX rename
        # is atomic, so there's no torn-write window where the cursor reads as
        # empty or partial.
        fd, tmp = tempfile.mkstemp(
            prefix=f"{self._plan_id}.",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(str(new_value))
            os.replace(tmp, self._path)
        except OSError:
            # If anything went wrong, scrub the temp file rather than leaking.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._value = new_value

    def reset(self) -> None:
        """Wipe the cursor (start from the top of the table)."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        self._value = 0
