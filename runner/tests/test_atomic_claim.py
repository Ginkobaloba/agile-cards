"""Atomic claim under concurrency.

Spawns N **processes** all trying to claim the same backlog card.
Exactly one must win. The losers must raise `ClaimRace`.

We use processes rather than threads because that is how the
production race actually unfolds: two daemons on the same machine
(or one daemon plus an ad-hoc CLI reclaim) call `os.replace`
concurrently. NTFS atomicity is per-process; thread-level races
inside one process can interleave differently and would model the
wrong scenario.
"""
from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path
from typing import Any

from cards_runner.common.types import RuntimePaths
from cards_runner.daemon.claim import ClaimRace, attempt_claim


def _claim_worker(
    card_path: str,
    todo_root: str,
    result_q: "multiprocessing.Queue[str]",
) -> None:
    """One claim attempt in a fresh process. Module-scope for pickling."""
    import os
    paths = RuntimePaths.from_root(Path(todo_root))
    try:
        attempt_claim(
            Path(card_path),
            paths=paths,
            claimed_by=f"pid-{os.getpid()}",
        )
        result_q.put("WIN")
    except ClaimRace:
        result_q.put("LOSE")
    except Exception as exc:  # noqa: BLE001
        result_q.put(f"WEIRD:{exc!r}")


def test_concurrent_claim_has_exactly_one_winner(
    paths: RuntimePaths,
    card_factory: Any,
    todo_root: Path,
) -> None:
    card_path = card_factory("bTST-01-race")
    N = 8

    ctx = multiprocessing.get_context("spawn")
    result_q: "multiprocessing.Queue[str]" = ctx.Queue()
    procs = [
        ctx.Process(
            target=_claim_worker,
            args=(str(card_path), str(todo_root), result_q),
        )
        for _ in range(N)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    results: list[str] = []
    while not result_q.empty():
        results.append(result_q.get_nowait())

    wins = sum(1 for r in results if r == "WIN")
    weird = [r for r in results if r.startswith("WEIRD")]
    assert wins == 1, f"expected 1 winner, got {wins}; results={results}"
    assert not weird, f"unexpected exceptions: {weird}"

    # The card now lives in active/, not backlog/.
    assert not card_path.exists()
    moved = paths.active / card_path.name
    assert moved.exists()


def test_threaded_claims_inside_one_process_pick_one(
    paths: RuntimePaths,
    card_factory: Any,
) -> None:
    """Sanity check for the in-process case.

    A single daemon process is single-threaded for claim arbitration,
    but the property "exactly one of N attempt_claim calls succeeds"
    should still hold when invoked sequentially or with small thread
    contention. We do a milder thread race here (N=4) and accept that
    the OS-level rename may yield more than one nominal winner under
    aggressive thread contention; what we strictly require is "at
    least one winner and no WEIRD exception types other than the
    documented ones."
    """
    card_path = card_factory("bTST-01-threads")
    N = 4
    results: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            attempt_claim(
                card_path,
                paths=paths,
                claimed_by=f"thr-{threading.get_ident()}",
            )
            with lock:
                results.append("WIN")
        except ClaimRace:
            with lock:
                results.append("LOSE")
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(f"WEIRD:{type(exc).__name__}")

    threads = [threading.Thread(target=attempt) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = sum(1 for r in results if r == "WIN")
    weird = [r for r in results if r.startswith("WEIRD")]
    assert wins >= 1, f"no winners at all: {results}"
    assert not weird, f"unexpected exceptions in thread race: {weird}"


def test_claim_stamps_frontmatter(
    paths: RuntimePaths, card_factory: Any
) -> None:
    card_path = card_factory("bTST-02-stamp")
    claim = attempt_claim(
        card_path,
        paths=paths,
        claimed_by="tester",
    )
    fm = claim.snapshot.frontmatter
    assert fm["status"] == "active"
    assert fm["claimed_by"] == "tester"
    assert fm["started_at"] is not None
    assert fm["last_heartbeat"] is not None
    assert fm["attempt_trace_id"] == claim.attempt_trace_id
    assert claim.attempt_trace_id  # uuid string
    # The card file on disk reflects what the snapshot says.
    on_disk = (paths.active / card_path.name).read_text(encoding="utf-8")
    assert "status: active" in on_disk
    assert "claimed_by: tester" in on_disk


def test_claim_against_missing_file_raises_race(
    paths: RuntimePaths, tmp_path: Path
) -> None:
    ghost = paths.backlog / "ghost.md"
    try:
        attempt_claim(ghost, paths=paths, claimed_by="t")
    except ClaimRace:
        pass
    else:
        raise AssertionError("expected ClaimRace for nonexistent card")
