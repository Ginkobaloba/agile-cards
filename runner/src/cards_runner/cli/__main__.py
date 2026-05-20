"""`cards-runner` CLI.

Chunk 1 surfaces the minimal subset documented in the design doc
item 11 plus what the chunk 1 ask spells out:

- `start`   boot the daemon (foreground)
- `stop`    signal the daemon to drain and exit
- `status`  print daemon state (PID, active count, last tick)
- `reclaim` force-reclaim a specific card in active/

Chunks 2-4 will add `verify`, `approve`, `pause`, `resume`, `doctor`,
and `pricing reload`.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from ..common.locks import FileLock, pid_alive
from ..common.types import DaemonConfig, RuntimePaths
from ..daemon.daemon import Daemon, DaemonAlreadyRunning
from ..daemon.orphan import force_reclaim


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cards-runner",
        description="agile-cards runner CLI (chunk 1)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="boot the daemon (foreground)")
    _add_common(p_start)
    p_start.add_argument("--poll-interval-sec", type=float, default=5.0)
    p_start.add_argument("--max-parallel", type=int, default=4)
    p_start.add_argument("--orphan-timeout-minutes", type=int, default=120)
    p_start.add_argument("--heartbeat-interval-sec", type=float, default=30.0)
    p_start.add_argument("--stub-sleep-sec", type=float, default=3.0)
    p_start.add_argument(
        "--skip-worktree",
        action="store_true",
        help="skip git worktree creation (for tests against non-git roots)",
    )

    p_stop = sub.add_parser("stop", help="signal the daemon to drain and exit")
    _add_common(p_stop)
    p_stop.add_argument("--timeout-sec", type=float, default=60.0)

    p_status = sub.add_parser("status", help="print daemon state")
    _add_common(p_status)
    p_status.add_argument("--json", action="store_true")

    p_reclaim = sub.add_parser(
        "reclaim", help="force-reclaim a card from active/ to backlog/"
    )
    _add_common(p_reclaim)
    p_reclaim.add_argument("card_id")
    p_reclaim.add_argument(
        "--force",
        action="store_true",
        help="skip the interactive confirmation",
    )

    args = parser.parse_args(argv)
    if args.cmd == "start":
        return _cmd_start(args)
    if args.cmd == "stop":
        return _cmd_stop(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "reclaim":
        return _cmd_reclaim(args)
    parser.error(f"unknown subcommand {args.cmd}")
    return 2  # unreachable


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--todo-root",
        type=Path,
        default=Path(os.environ.get("CARDS_TODO_ROOT", r"C:\dev\todo")),
    )


def _cmd_start(args: argparse.Namespace) -> int:
    cfg = DaemonConfig(
        todo_root=args.todo_root,
        poll_interval_sec=args.poll_interval_sec,
        max_parallel=args.max_parallel,
        orphan_timeout_minutes=args.orphan_timeout_minutes,
        heartbeat_interval_sec=args.heartbeat_interval_sec,
        stub_sleep_sec=args.stub_sleep_sec,
        skip_worktree=args.skip_worktree,
    )
    try:
        return Daemon(cfg).run()
    except DaemonAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _cmd_stop(args: argparse.Namespace) -> int:
    paths = RuntimePaths.from_root(args.todo_root)
    lock = FileLock(paths.daemon_lock)
    pid = lock.read_pid()
    if pid is None:
        print("daemon not running (no lockfile PID)", file=sys.stderr)
        return 2
    if not pid_alive(pid):
        print(
            f"daemon lockfile holds pid={pid} but the process is gone",
            file=sys.stderr,
        )
        return 2
    try:
        if sys.platform == "win32":
            # On Windows os.kill with signal.SIGTERM raises; use
            # CTRL_BREAK_EVENT against the daemon's process group.
            os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"failed to signal daemon pid={pid}: {exc}", file=sys.stderr)
        return 1
    print(f"sent stop signal to daemon pid={pid}; waiting...")
    deadline = time.monotonic() + args.timeout_sec
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            print("daemon exited")
            return 0
        time.sleep(0.5)
    print("daemon still running after timeout", file=sys.stderr)
    return 1


def _cmd_status(args: argparse.Namespace) -> int:
    paths = RuntimePaths.from_root(args.todo_root)
    lock = FileLock(paths.daemon_lock)
    pid = lock.read_pid()
    running = pid is not None and pid_alive(pid)
    active_count = _count_files(paths.active)
    backlog_count = _count_files(paths.backlog)
    done_count = _count_files(paths.done)
    blocked_count = _count_files(paths.blocked)
    sentinel_present = paths.atomic_rename_sentinel.is_file()
    payload: dict[str, Any] = {
        "todo_root": str(paths.todo_root),
        "daemon_pid": pid,
        "daemon_running": running,
        "atomic_rename_sentinel": sentinel_present,
        "counts": {
            "backlog": backlog_count,
            "active": active_count,
            "done": done_count,
            "blocked": blocked_count,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"todo_root: {payload['todo_root']}")
    print(
        f"daemon: {'running' if running else 'stopped'} "
        f"(pid={pid if pid else 'none'})"
    )
    print(
        f"atomic-rename sentinel: "
        f"{'present' if sentinel_present else 'missing (parallel forced to 1)'}"
    )
    print(
        "counts: backlog={backlog} active={active} done={done} blocked={blocked}"
        .format(**payload["counts"])
    )
    return 0


def _cmd_reclaim(args: argparse.Namespace) -> int:
    paths = RuntimePaths.from_root(args.todo_root)
    if not args.force:
        ans = input(f"reclaim {args.card_id} from active/ -> backlog/? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("aborted")
            return 0
    try:
        new_path = force_reclaim(args.card_id, paths=paths)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"reclaimed: {new_path}")
    return 0


def _count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1 for p in directory.iterdir()
        if p.is_file() and p.suffix == ".md"
    )


if __name__ == "__main__":
    raise SystemExit(main())
