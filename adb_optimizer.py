"""ADB optimizations for MumuPaster — SmartWaiter, batch shell, optional profiler."""

from __future__ import annotations

import os
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable, List, Optional, Tuple

_run_adb: Optional[Callable[[str, list], Any]] = None
_dump_ui: Optional[Callable[[str], Optional[ET.Element]]] = None
_invalidate: Optional[Callable[[Optional[str]], None]] = None


def configure_adb_optimizer(
    run_adb_fn: Callable[[str, list], Any],
    dump_ui_fn: Callable[[str], Optional[ET.Element]],
    invalidate_fn: Callable[[Optional[str]], None],
) -> None:
    global _run_adb, _dump_ui, _invalidate
    _run_adb = run_adb_fn
    _dump_ui = dump_ui_fn
    _invalidate = invalidate_fn


def invalidate_cache(serial: Optional[str]) -> None:
    if _invalidate:
        _invalidate(serial)


class SmartWaiter:
    """Poll UI until condition(root) is True — faster than fixed sleep loops."""

    def __init__(self, serial: str, poll: float = 0.15):
        self.serial = (serial or "").strip()
        self.poll = poll

    def wait_until(
        self,
        condition: Callable[[Optional[ET.Element]], bool],
        timeout: float = 4.0,
        *,
        poll: Optional[float] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        initial_delay: float = 0.0,
    ) -> Optional[ET.Element]:
        if initial_delay > 0:
            time.sleep(initial_delay)
        p = poll if poll is not None else self.poll
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if should_stop and should_stop():
                return None
            root = _dump_ui(self.serial) if _dump_ui and self.serial else None
            if root is not None and condition(root):
                return root
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(p, remaining))
        return None


class AdbBatch:
    def __init__(self, serial: str):
        self.serial = (serial or "").strip()
        self._commands: List[str] = []

    def tap(self, xy: Tuple[int, int]) -> "AdbBatch":
        self._commands.append(f"input tap {int(xy[0])} {int(xy[1])}")
        return self

    def sleep(self, seconds: float) -> "AdbBatch":
        self._commands.append(f"sleep {seconds:.3f}")
        return self

    def commands_raw(self, cmd: str) -> "AdbBatch":
        self._commands.append(cmd)
        return self

    def run(self) -> bool:
        if not self._commands or not _run_adb or not self.serial:
            return False
        combined = " && ".join(self._commands)
        r = _run_adb(self.serial, ["shell", combined])
        self._commands.clear()
        ok = bool(r and getattr(r, "returncode", 1) == 0)
        if ok:
            invalidate_cache(self.serial)
        return ok


def send_text_batch_short(
    serial: str,
    text: str,
    edit_xy: Tuple[int, int],
    send_xy: Tuple[int, int],
    *,
    post_tap: float = 0.12,
    post_text: float = 0.20,
) -> bool:
    """One shell session: tap → type → tap Send (short messages only)."""
    if not text or not _run_adb or not serial:
        return False
    if len(text) > 90 or "\n" in text:
        return False
    escaped = text.replace("'", "'\"'\"'")
    batch = AdbBatch(serial)
    (
        batch.tap(edit_xy)
        .sleep(post_tap)
        .commands_raw(f"input text '{escaped}'")
        .sleep(post_text)
        .tap(send_xy)
    )
    return batch.run()


class AdbProfiler:
    def __init__(self) -> None:
        self._enabled = False
        self._lock = threading.Lock()
        self._data: dict = {}

    def enable(self) -> None:
        self._enabled = True

    class _Timer:
        def __init__(self, profiler: "AdbProfiler", name: str):
            self.profiler = profiler
            self.name = name
            self._start = 0.0

        def __enter__(self):
            self._start = time.monotonic()
            return self

        def __exit__(self, *_):
            if not self.profiler._enabled:
                return
            elapsed = time.monotonic() - self._start
            with self.profiler._lock:
                d = self.profiler._data.setdefault(
                    self.name, {"count": 0, "total": 0.0, "max": 0.0}
                )
                d["count"] += 1
                d["total"] += elapsed
                d["max"] = max(d["max"], elapsed)

    def measure(self, name: str) -> "_Timer":
        return self._Timer(self, name)


profiler = AdbProfiler()
if os.environ.get("ADB_PROFILE"):
    profiler.enable()
