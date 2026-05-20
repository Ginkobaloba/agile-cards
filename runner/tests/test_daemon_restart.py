"""Daemon restart finds active cards and resumes correctly.

We simulate a daemon crash by:

1. Claiming a card (no worker spawned).
2. Constructing a fresh `Daemon` against the same `todo_root`.
3. Running `_boot()` directly. This is what the production boot
   sequence does after acquiring the singleton lock.

The new daemon must NOT respawn the card (because its heartbeat
is still fresh in this test). It must orphan-reclaim cards whose
heartbeat is stale.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cards_runner.common.card_io import parse_card_file, write_card_file
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.claim import attempt_claim
from cards_runner.daemon.daemon import Daemon


def _stale_card(card_path: Path, *, minutes: int) -> None:
    snap = parse_card_file(card_path)
    stale = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    snap.frontmatter["last_heartbeat"] = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_card_file(card_path, snap)


def test_boot_leaves_fresh_active_card_alone(
    paths: RuntimePaths, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    card_path = card_factory("bTST-07-restart")
    claim = attempt_claim(card_path, paths=paths, claimed_by="tester")
    # Fresh daemon. We deliberately do not call .run() so we do not
    # block; _boot() is enough to exercise the reconcile path.
    d = Daemon(daemon_cfg)
    d._boot()
    # The card stays in active/ because its heartbeat is fresh.
    assert claim.active_path.exists()
    assert not (paths.backlog / card_path.name).exists()


def test_boot_reclaims_stale_active_card(
    paths: RuntimePaths, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    card_path = card_factory("bTST-08-stale")
    claim = attempt_claim(card_path, paths=paths, claimed_by="tester")
    _stale_card(claim.active_path, minutes=daemon_cfg.orphan_timeout_minutes + 1)
    d = Daemon(daemon_cfg)
    d._boot()
    # Card has been moved back to backlog/.
    assert not claim.active_path.exists()
    assert (paths.backlog / card_path.name).exists()
    snap = parse_card_file(paths.backlog / card_path.name)
    assert snap.frontmatter["claimed_by"] is None


def test_boot_repairs_malformed_claim(
    paths: RuntimePaths, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    """A card in active/ with no claimed_by simulates a daemon kill
    between move and stamp. Boot must re-stamp it so the next pass
    can reason about it normally."""
    card_path = card_factory("bTST-09-malformed", status="active")
    # Move into active/ manually without stamping.
    moved = paths.active / card_path.name
    card_path.rename(moved)
    d = Daemon(daemon_cfg)
    d._boot()
    snap = parse_card_file(moved)
    assert snap.frontmatter["claimed_by"] == "daemon-boot-reconcile"
    assert snap.frontmatter["started_at"] is not None
    assert snap.frontmatter["last_heartbeat"] is not None
