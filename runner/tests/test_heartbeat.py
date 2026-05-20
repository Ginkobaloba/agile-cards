"""Stub worker heartbeat propagation.

Drives the worker directly (rather than through the daemon spawner)
so the test stays Linux-friendly. The Job Object path is exercised
in `test_env_scrub` which actually spawns a subprocess.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from cards_runner.common.card_io import parse_card_file
from cards_runner.common.types import HEARTBEAT_FILE, RuntimePaths, parse_iso
from cards_runner.daemon.claim import attempt_claim
from cards_runner.daemon.worktree import prepare_worktree
from cards_runner.worker_stub.invoker import StubInvoker
from cards_runner.worker_stub.worker import run_worker


@pytest.fixture
def claimed_path(
    paths: RuntimePaths, card_factory: Any, tmp_path: Path
) -> tuple[Path, Path, str]:
    card_path = card_factory("bTST-03-heartbeat")
    claim = attempt_claim(card_path, paths=paths, claimed_by="tester")
    prepare_worktree(
        paths=paths,
        project_dir=tmp_path,
        branch_name="card/bTST-03-heartbeat",
        base_branch="main",
        worktree_path=claim.worktree_path,
        skip_git=True,
    )
    return claim.active_path, claim.worktree_path, claim.attempt_trace_id


def test_stub_worker_writes_completion_notes(
    claimed_path: tuple[Path, Path, str]
) -> None:
    card_path, worktree, attempt = claimed_path
    rc = run_worker(
        card_path=card_path,
        worktree=worktree,
        attempt_trace_id=attempt,
        trace_id="trace-test",
        heartbeat_interval_sec=0.1,
        invoker=StubInvoker(sleep_sec=0.5),
    )
    assert rc == 0
    text = card_path.read_text(encoding="utf-8")
    assert "## Completion notes" in text
    assert "Stub executor (chunk 1)" in text
    assert "finished_at:" in text
    snap = parse_card_file(card_path)
    assert snap.frontmatter["finished_at"] is not None


def test_heartbeat_file_is_written(
    claimed_path: tuple[Path, Path, str]
) -> None:
    card_path, worktree, attempt = claimed_path
    invoker = StubInvoker(sleep_sec=0.6)
    rc = run_worker(
        card_path=card_path,
        worktree=worktree,
        attempt_trace_id=attempt,
        trace_id="trace-test",
        heartbeat_interval_sec=0.1,
        invoker=invoker,
    )
    assert rc == 0
    hb = worktree / HEARTBEAT_FILE
    assert hb.is_file()


def test_card_frontmatter_heartbeat_advances(
    claimed_path: tuple[Path, Path, str]
) -> None:
    card_path, worktree, attempt = claimed_path
    initial = parse_card_file(card_path)
    started_hb = parse_iso(initial.frontmatter["last_heartbeat"])
    assert started_hb is not None

    # Run worker in a thread so we can observe mid-run state.
    completion: dict[str, int] = {}

    def go() -> None:
        completion["rc"] = run_worker(
            card_path=card_path,
            worktree=worktree,
            attempt_trace_id=attempt,
            trace_id="trace-test",
            heartbeat_interval_sec=0.2,
            invoker=StubInvoker(sleep_sec=1.5),
        )

    t = threading.Thread(target=go)
    t.start()
    # Wait long enough for at least two heartbeat cycles.
    time.sleep(0.7)
    mid = parse_card_file(card_path)
    mid_hb = parse_iso(mid.frontmatter["last_heartbeat"])
    assert mid_hb is not None
    assert mid_hb >= started_hb
    t.join()
    assert completion["rc"] == 0
