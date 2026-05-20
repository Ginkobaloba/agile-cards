"""Shared dataclasses and constants.

Kept narrow on purpose. The daemon and worker both depend on these
types; anything richer lives in its owning subpackage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final


# Canonical subfolder names per RUNNER_CONTRACT.md "Directory invariants".
SUBFOLDER_BACKLOG: Final[str] = "backlog"
SUBFOLDER_ACTIVE: Final[str] = "active"
SUBFOLDER_AMENDMENTS: Final[str] = "amendments"
SUBFOLDER_AWAITING_STANDUP: Final[str] = "awaiting_standup_review"
SUBFOLDER_DONE: Final[str] = "done"
SUBFOLDER_BLOCKED: Final[str] = "blocked"

ALL_SUBFOLDERS: Final[tuple[str, ...]] = (
    SUBFOLDER_BACKLOG,
    SUBFOLDER_ACTIVE,
    SUBFOLDER_AMENDMENTS,
    SUBFOLDER_AWAITING_STANDUP,
    SUBFOLDER_DONE,
    SUBFOLDER_BLOCKED,
)

# Where the per-attempt runtime data lives, relative to TODO root.
RUNS_DIRNAME: Final[str] = "_runs"

# Where preapproval markers live, relative to TODO root.
SIGNALS_DIRNAME: Final[str] = "_signals"

# Daemon singleton lock.
DAEMON_LOCK_NAME: Final[str] = ".daemon.lock"

# Global worktree-creation mutex.
RUNNER_LOCK_NAME: Final[str] = ".runner.lock"

# Atomic-rename-test sentinel name. Lives at TODO root.
ATOMIC_RENAME_SENTINEL: Final[str] = ".atomic_rename_test.passed"

# Per-worktree halt sentinel (chunk 2 fallback; chunk 1 stubs do not poll).
HALT_SENTINEL: Final[str] = ".cards-halt"

# Worker-side heartbeat file inside the worktree.
HEARTBEAT_FILE: Final[str] = ".cards-heartbeat"


def now_utc_iso() -> str:
    """ISO 8601 UTC timestamp with trailing Z, second resolution.

    Matches what the planner writes in `started_at`, `last_heartbeat`,
    etc. Second resolution is enough for orphan reclaim decisions and
    keeps the field human-readable.
    """
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp written by the runner. None passes through.

    Accepts both `...Z` and explicit `+00:00`. Returns timezone-aware
    UTC datetimes. Raises ValueError on malformed input rather than
    silently coercing to None.
    """
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class RuntimePaths:
    """All the disk paths the daemon and workers reference.

    Constructed once at daemon boot and passed down. Makes tests easy
    (the suite points everything at a tmp directory) and keeps path
    derivation out of business logic.
    """

    todo_root: Path
    backlog: Path
    active: Path
    amendments: Path
    awaiting_standup: Path
    done: Path
    blocked: Path
    runs: Path
    signals: Path
    daemon_lock: Path
    runner_lock: Path
    atomic_rename_sentinel: Path

    @classmethod
    def from_root(cls, todo_root: Path) -> "RuntimePaths":
        root = todo_root.resolve()
        return cls(
            todo_root=root,
            backlog=root / SUBFOLDER_BACKLOG,
            active=root / SUBFOLDER_ACTIVE,
            amendments=root / SUBFOLDER_AMENDMENTS,
            awaiting_standup=root / SUBFOLDER_AWAITING_STANDUP,
            done=root / SUBFOLDER_DONE,
            blocked=root / SUBFOLDER_BLOCKED,
            runs=root / RUNS_DIRNAME,
            signals=root / SIGNALS_DIRNAME,
            daemon_lock=root / DAEMON_LOCK_NAME,
            runner_lock=root / RUNNER_LOCK_NAME,
            atomic_rename_sentinel=root / ATOMIC_RENAME_SENTINEL,
        )

    def ensure(self) -> None:
        """Create the directory layout if missing. Idempotent.

        The daemon calls this at boot. The atomic-rename sentinel is
        NOT created here; that is the sentinel check's job.
        """
        for d in (
            self.todo_root,
            self.backlog,
            self.active,
            self.amendments,
            self.awaiting_standup,
            self.done,
            self.blocked,
            self.runs,
            self.signals,
        ):
            d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DaemonConfig:
    """Daemon-wide runtime knobs.

    Constructed from CLI flags. Project-level knobs (cascade thresholds,
    etc.) live on the per-card project config, not here.
    """

    todo_root: Path
    poll_interval_sec: float = 5.0
    max_parallel: int = 4
    max_parallel_pinned: int = 1
    orphan_timeout_minutes: int = 120
    heartbeat_interval_sec: float = 30.0
    worktree_forensic_ttl_hours: int = 24
    stub_sleep_sec: float = 3.0  # chunk 1: how long the stub worker sleeps.
    force_kill_after_seconds: int = 90
    skip_worktree: bool = False  # tests bypass git when not on a real repo.
    log_dir: Path | None = None

    @property
    def orphan_timeout_sec(self) -> int:
        return int(self.orphan_timeout_minutes * 60)


@dataclass
class CardSnapshot:
    """Everything we read from a card on disk in one shot.

    Mutable: the daemon updates the frontmatter dict in place and
    writes the snapshot back. The file path itself is not on this
    type because the snapshot survives subfolder moves.
    """

    card_id: str
    frontmatter: dict[str, Any]
    body: str
    raw_frontmatter_text: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        return self.frontmatter.get(key, default)


@dataclass(frozen=True)
class ClaimedCard:
    """A card the daemon has successfully claimed.

    Carries the new path under active/, the per-attempt trace id, and
    the worktree path the worker will receive.
    """

    card_id: str
    active_path: Path
    attempt_trace_id: str
    worktree_path: Path
    snapshot: CardSnapshot = field(repr=False)


# Exit codes from worker processes. Documented here so the daemon
# can route on them without magic numbers.
EXIT_CLEAN: Final[int] = 0
EXIT_STUB_ERROR: Final[int] = 10
EXIT_COST_CAP_HALT: Final[int] = 11  # reserved for chunk 2.
EXIT_HALT_SIGNAL: Final[int] = 12  # reserved for chunk 2.
EXIT_UNCAUGHT: Final[int] = 99
