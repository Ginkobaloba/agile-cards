"""Worker entry point used by the daemon's spawner.

The worker reads and writes the per-run projected card file the
daemon wrote into the run dir (CARDS_RUNNER_CARD_PATH). That file is
an ephemeral per-run view; on worker exit the daemon parses it back
into the canonical card store. The worker never touches the store
directly -- it works a Markdown file exactly as a v1 worker did.

Lifecycle:

1. Read CARDS_RUNNER_CARD_PATH and CARDS_RUNNER_WORKTREE from env.
2. Parse the projected card frontmatter.
3. Start a heartbeat thread that:
   - touches `<worktree>/.cards-heartbeat` every
     `CARDS_RUNNER_HEARTBEAT_INTERVAL_SEC` seconds (default 30),
   - updates the projected card's `last_heartbeat` field on each
     beat.
4. Call the `Invoker` (the chunk 2b-i stub `StubInvoker`; chunk 2b-ii
   swaps in the real SDK-backed executor).
5. On clean return, append completion notes to the card body,
   stamp `finished_at`, `actual_tokens`, `actual_duration_minutes`,
   `model_used`, write the projected card back, exit 0.
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
    # The projected card file is rewritten whole now, so the worker
    # can set these fields directly -- no allowlist, no no-op for
    # fields the planner did not pre-bake.
    snap.frontmatter["actual_tokens"] = actual_tokens
    snap.frontmatter["actual_duration_minutes"] = round(duration_sec / 60.0, 2)
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
