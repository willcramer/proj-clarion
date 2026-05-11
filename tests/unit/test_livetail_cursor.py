"""Unit tests for the live-tail cursor file."""

from __future__ import annotations

from pathlib import Path

import pytest

from proj_clarion.livetail.cursor import Cursor


def test_cursor_starts_at_zero(tmp_path: Path) -> None:
    c = Cursor("plan-a", root=tmp_path)
    assert c.value == 0


def test_cursor_advances_and_persists(tmp_path: Path) -> None:
    c = Cursor("plan-a", root=tmp_path)
    c.advance_to(42)
    assert c.value == 42

    # New instance, same file, must read the persisted value
    c2 = Cursor("plan-a", root=tmp_path)
    assert c2.value == 42


def test_cursor_refuses_to_go_backwards(tmp_path: Path) -> None:
    c = Cursor("plan-a", root=tmp_path)
    c.advance_to(100)
    c.advance_to(50)  # Should be a no-op — never go backwards.
    assert c.value == 100


def test_cursor_reset_clears_file_and_value(tmp_path: Path) -> None:
    c = Cursor("plan-a", root=tmp_path)
    c.advance_to(7)
    c.reset()
    assert c.value == 0

    c2 = Cursor("plan-a", root=tmp_path)
    assert c2.value == 0


def test_cursor_handles_corrupt_file(tmp_path: Path) -> None:
    """A torn write or stray editor save shouldn't crash the tailer at boot."""
    cursor_dir = tmp_path / "livetail"
    cursor_dir.mkdir()
    (cursor_dir / "plan-a.cursor").write_text("not-an-int")

    c = Cursor("plan-a", root=tmp_path)
    assert c.value == 0  # Falls back to 0 instead of raising.


def test_separate_plans_have_separate_cursors(tmp_path: Path) -> None:
    a = Cursor("plan-a", root=tmp_path)
    b = Cursor("plan-b", root=tmp_path)
    a.advance_to(10)
    b.advance_to(99)
    assert Cursor("plan-a", root=tmp_path).value == 10
    assert Cursor("plan-b", root=tmp_path).value == 99
