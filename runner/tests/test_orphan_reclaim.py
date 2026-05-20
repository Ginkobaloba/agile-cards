"""Orphan reclaim.

We claim a card, manually rewind its `last_heartbeat` past the
orphan threshold, then assert the orphan scan flags it and that
reclaim moves it back to backlog with the claim fields cleared.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cards_runner.common.card_io import parse_card_file, write_card_file
from cards_runner.common.types import DaemonConfig, RuntimePaths
from cards_runner.daemon.claim import attempt_claim
from cards_runner.daemon.orphan import (
    force_reclaim, reclaim, scan_for_orphans,
)


def _rewind_heartbeat(card_path: Path, *, minutes: int) -> None:
    snap = parse_card_file(card_path)
    stale = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    snap.frontmatter["last_heartbeat"] = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_card_file(card_path, snap)


def test_scan_finds_orphan_after_heartbeat_goes_stale(
    paths: RuntimePaths, card_factory: Any, daemon_cfg: DaemonConfig
) -> None:
    card_path = card_factory("bTST-04-orphan")
    claim = attempt_claim(card_path, paths=paths, claimed_by="tester")
    # No orphans yet.
    assert scan_for_orphans(paths=paths, cfg=daemon_cfg) == []
    # Rewind heartbeat past the orphan window.
    _rewind_heartbeat(
        claim.active_path,
        minutes=daemon_cfg.orphan_timeout_minutes + 5,
    )
    orphans = scan_for_orphans(paths=paths, cfg=daemon_cfg)
    assert len(orphans) == 1
    assert orphans[0].name == card_path.name


def test_reclaim_moves_card_back_and_clears_fields(
    paths: RuntimePaths, card_factory: Any
) -> None:
    card_path = card_factory("bTST-05-reclaim")
    claim = attempt_claim(card_path, paths=paths, claimed_by="tester")
    _rewind_heartbeat(claim.active_path, minutes=999)

    backlog_path = reclaim(claim.active_path, paths=paths)
    assert backlog_path.parent == paths.backlog
    assert not (paths.active / card_path.name).exists()
    snap = parse_card_file(backlog_path)
    assert snap.frontmatter["status"] == "backlog"
    assert snap.frontmatter["claimed_by"] is None
    assert snap.frontmatter["started_at"] is None
    assert snap.frontmatter["last_heartbeat"] is None
    assert snap.frontmatter["attempt_trace_id"] is None


def test_force_reclaim_works_by_card_id(
    paths: RuntimePaths, card_factory: Any
) -> None:
    card_factory("bTST-06-force")
    attempt_claim(
        paths.backlog / "bTST-06-force.md",
        paths=paths, claimed_by="tester",
    )
    new_path = force_reclaim("bTST-06-force", paths=paths)
    assert new_path.parent == paths.backlog
