"""Worker entry point used by the daemon's spawner.

Lifecycle (chunk 1):

1. Read CARDS_RUNNER_CARD_PATH and CARDS_RUNNER_WORKTREE from env.
2. Parse the card frontmatter.
3. Start a heartbeat thread that:
   - touches `<worktree>/.cards-heartbeat` every
     `CARDS_RUNNER_HEARTBEAT_INTERVAL_SEC` seconds (default 30),
   - updates the card frontmatter `last_heartbeat` field every Nth
     heartbeat (default every heartbeat in chunk 1; we keep the
     cadence high so tests see propagation quickly).
4. Call the `Invoker` (chunk 1: `StubInvoker`).
5. On clean return, append completion notes to the card body,
   stamp `finished_at`, `actual_tokens`, `model_used`, write the
   card back, exit 0.
6. On exception, write a short error block, exit with EXIT_STUB_ERROR.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..common.atomic import atomic_touch
from ..common.card_io import (
    append_completion_notes,
    parse_card_file,
    write_card_file,
)
from ..common.logging_setup import setup_worker_logging
from ..common.types import (
    EXIT_CLEAN,
    EXIT_STUB_ERROR,
    EXIT_UNCAUGHT,
    HEARTBEAT_FILE,
    now_utc_iso,
)
from .invoker import InvokeRequest, Invoker, StubInvoker


log = logging.getLogger(__name__)


class _Heartbeat:
    """Background thread that writes the heartbeat file and frontmatter.

    Stops when `cancel()` is called. Designed to be cheap: tempfile-
    rename touch on the heartbeat file, full card rewrite for the
    frontmatter (which we accept because the cadence is low).
    """

    def __init__(
        self,
        *,
        card_path: Path,
        worktree: Path,
        interval_sec: float,
        frontmatter_every_n: int = 1,
    ) -> None:
        self.card_path = card_path
        self.worktree = worktree
        self.interval_sec = interval_sec
        self.frontmatter_every_n = max(1, frontmatter_every_n)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._tick = 0

    def start(self) -> None:
        # Always write at least one heartbeat synchronously so the
        # daemon's first poll after spawn sees fresh evidence.
        self._beat()
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_sec):
            try:
                self._beat()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat tick failed")

    def _beat(self) -> None:
        self._tick += 1
        hb_path = self.worktree / HEARTBEAT_FILE
        atomic_touch(hb_path)
        if self._tick % self.frontmatter_every_n == 0:
            self._update_card_heartbeat()

    def _update_card_heartbeat(self) -> None:
        try:
            snap = parse_card_file(self.card_path)
        except FileNotFoundError:
            # Daemon may have reclaimed the card during a long beat
            # cycle. Stop trying.
            self._stop.set()
            return
        snap.frontmatter["last_heartbeat"] = now_utc_iso()
        try:
            write_card_file(self.card_path, snap)
        except FileNotFoundError:
            self._stop.set()


def run_worker(
    *,
    card_path: Path,
    worktree: Path,
    attempt_trace_id: str,
    trace_id: str,
    heartbeat_interval_sec: float,
    invoker: Invoker,
) -> int:
    """Run one worker lifecycle. Returns the process exit code."""
    snap = parse_card_file(card_path)
    request = InvokeRequest(
        snapshot=snap,
        worktree=worktree,
        attempt_trace_id=attempt_trace_id,
        trace_id=trace_id,
    )
    heartbeat = _Heartbeat(
        card_path=card_path,
        worktree=worktree,
        interval_sec=heartbeat_interval_sec,
    )
    heartbeat.start()
    started_at_mono = time.monotonic()
    try:
        result = invoker.invoke(request)
    except Exception:  # noqa: BLE001
        log.exception("invoker raised; writing error completion notes")
        heartbeat.cancel()
        _stamp_error(card_path, attempt_trace_id)
        return EXIT_STUB_ERROR
    heartbeat.cancel()

    duration_sec = time.monotonic() - started_at_mono
    log.info(
        "worker invoker returned success=%s duration_sec=%.1f tokens=%d model=%s",
        result.success, duration_sec, result.actual_tokens, result.model_used,
    )
    _stamp_success(
        card_path=card_path,
        result_notes=result.completion_notes_markdown,
        actual_tokens=result.actual_tokens,
        model_used=result.model_used,
        attempt_trace_id=attempt_trace_id,
        duration_sec=duration_sec,
    )
    return EXIT_CLEAN if result.success else EXIT_STUB_ERROR


def _stamp_success(
    *,
    card_path: Path,
    result_notes: str,
    actual_tokens: int,
    model_used: str | None,
    attempt_trace_id: str,
    duration_sec: float,
) -> None:
    try:
        snap = parse_card_file(card_path)
    except FileNotFoundError:
        log.warning(
            "card %s vanished before success stamp (probably reclaimed)",
            card_path,
        )
        return
    now_iso = now_utc_iso()
    snap.frontmatter["finished_at"] = now_iso
    snap.frontmatter["last_heartbeat"] = now_iso
    if model_used is not None:
        snap.frontmatter["model_used"] = model_used
    _set_actual_tokens(snap, actual_tokens)
    _set_actual_duration(snap, duration_sec)
    snap.frontmatter["attempt_trace_id"] = attempt_trace_id
    append_completion_notes(snap, result_notes)
    write_card_file(card_path, snap)
    log.info("stamped success on %s", card_path)


def _stamp_error(card_path: Path, attempt_trace_id: str) -> None:
    try:
        snap = parse_card_file(card_path)
    except FileNotFoundError:
        return
    now_iso = now_utc_iso()
    snap.frontmatter["last_heartbeat"] = now_iso
    snap.frontmatter["attempt_trace_id"] = attempt_trace_id
    append_completion_notes(
        snap,
        "Stub executor (chunk 1) raised an unexpected exception.\n"
        "See `_runs/<attempt_trace_id>/worker.log` for the full trace.\n",
    )
    try:
        write_card_file(card_path, snap)
    except FileNotFoundError:
        return


def _set_actual_tokens(snap: object, actual_tokens: int) -> None:
    """Best-effort. The frontmatter writer in chunk 1 only handles
    a fixed allowlist of scalar fields, so this is a no-op unless
    the planner has the field pre-baked. We still record duration
    via the same mechanism. Chunk 2 will extend the writer to
    handle nested fields properly.
    """
    fm = getattr(snap, "frontmatter", None)
    if isinstance(fm, dict):
        fm.setdefault("actual_tokens", actual_tokens)


def _set_actual_duration(snap: object, duration_sec: float) -> None:
    fm = getattr(snap, "frontmatter", None)
    if isinstance(fm, dict):
        fm.setdefault("actual_duration_minutes", round(duration_sec / 60.0, 2))


def main_from_env() -> int:
    """Default entry. Pulls paths and ids from env, builds the StubInvoker."""
    card_path = Path(os.environ["CARDS_RUNNER_CARD_PATH"])
    worktree = Path(os.environ["CARDS_RUNNER_WORKTREE"])
    attempt_trace_id = os.environ["CARDS_RUNNER_ATTEMPT_TRACE_ID"]
    trace_id = os.environ.get("CARDS_RUNNER_TRACE_ID", attempt_trace_id)
    heartbeat_interval_sec = float(
        os.environ.get("CARDS_RUNNER_HEARTBEAT_INTERVAL_SEC", "30")
    )
    stub_sleep_sec = float(os.environ.get("CARDS_RUNNER_STUB_SLEEP_SEC", "3"))
    run_dir = Path(
        os.environ.get(
            "CARDS_RUNNER_RUN_DIR",
            str(worktree.parent),
        )
    )
    setup_worker_logging(run_dir)

    log.info(
        "stub worker boot card=%s attempt=%s sleep=%.1fs hb=%.1fs",
        card_path, attempt_trace_id, stub_sleep_sec, heartbeat_interval_sec,
    )

    invoker = StubInvoker(sleep_sec=stub_sleep_sec)
    try:
        return run_worker(
            card_path=card_path,
            worktree=worktree,
            attempt_trace_id=attempt_trace_id,
            trace_id=trace_id,
            heartbeat_interval_sec=heartbeat_interval_sec,
            invoker=invoker,
        )
    except Exception:
        log.exception("worker outer loop crashed")
        return EXIT_UNCAUGHT


# Keep import-time side effects minimal so tests that import this
# module do not start a worker.
_BOOT_AT = datetime.now(tz=timezone.utc)
