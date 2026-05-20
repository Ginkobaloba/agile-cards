"""Shared fixtures for the chunk 1 test suite."""
from __future__ import annotations

import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

# Make `src/cards_runner` importable without an install. We do this
# here so `pip install -e .` is not required to run the suite from a
# fresh clone; the README still documents `pip install -e .[dev]` as
# the supported path.
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from cards_runner.common.types import (  # noqa: E402
    DaemonConfig, RuntimePaths,
)


def _make_card_text(
    card_id: str,
    *,
    status: str = "backlog",
    trace_id: str | None = None,
) -> str:
    trace = trace_id or str(uuid.uuid4())
    return textwrap.dedent(
        f"""\
        ---
        verifier_schema_version: "1.3"
        id: {card_id}
        title: Test card {card_id}
        project: /tmp/test-project
        status: {status}
        points: 2
        stakes: low
        difficulty: shallow
        thinking_depth: shallow
        model: claude-haiku-4-5-20251001
        extended_thinking: false
        model_floor: haiku
        pin_required: false
        requires_pre_approval: false
        cost_cap_usd: null
        estimated_tokens: 0
        actual_tokens: null
        estimated_duration_minutes: 0
        actual_duration_minutes: null
        trace_id: {trace}
        sizing_note: "test card"
        depends_on: []
        touches: []
        batch: bTST
        story_hash: deadbeef
        created: 2026-05-19
        started_at: null
        finished_at: null
        claimed_by: null
        model_used: null
        last_heartbeat: null
        branch: card/{card_id}
        base_branch: main
        merge_status: pending
        verified_at: null
        verified_by: null
        verifier_skipped_reason: null
        cascade_history: []
        verifier_cascade_history: []
        standup_reason: null
        ---

        ## Context

        Test card.

        ## Scope

        - nothing.

        ## Out of scope

        - real work.

        ## Acceptance criteria

        ```yaml
        acceptance_criteria:
          - description: "Smoke"
            type: file_exists
            path: "README.md"
        ```

        ## Pointers

        - none.
        """
    )


@pytest.fixture
def todo_root(tmp_path: Path) -> Path:
    root = tmp_path / "todo"
    paths = RuntimePaths.from_root(root)
    paths.ensure()
    return root


@pytest.fixture
def paths(todo_root: Path) -> RuntimePaths:
    return RuntimePaths.from_root(todo_root)


@pytest.fixture
def daemon_cfg(todo_root: Path) -> DaemonConfig:
    return DaemonConfig(
        todo_root=todo_root,
        poll_interval_sec=0.1,
        max_parallel=4,
        max_parallel_pinned=1,
        orphan_timeout_minutes=120,
        heartbeat_interval_sec=0.5,
        stub_sleep_sec=0.5,
        worktree_forensic_ttl_hours=24,
        skip_worktree=True,
    )


@pytest.fixture
def card_factory(paths: RuntimePaths) -> Any:
    """Drop a card into `backlog/` and return its path."""

    def make(card_id: str = "bTST-01-test", **overrides: Any) -> Path:
        path = paths.backlog / f"{card_id}.md"
        path.write_text(_make_card_text(card_id, **overrides), encoding="utf-8")
        return path

    return make


@pytest.fixture(autouse=True)
def _stamp_atomic_rename_sentinel(paths: RuntimePaths) -> Iterator[None]:
    """Most tests pre-stamp the sentinel so we exercise the parallel path.

    Tests that exercise the sentinel itself remove the file at the
    top of the test body.
    """
    paths.atomic_rename_sentinel.write_text("test", encoding="utf-8")
    yield
