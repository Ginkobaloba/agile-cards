"""Atomic-rename-test sentinel.

Per design item 12 the daemon forces max_parallel=1 when the
sentinel is missing AND the embedded test fails on the host. The
embedded test's outcome is host-dependent: NTFS + AV + indexer
interactions can produce occasional "two winners" rounds that the
test correctly flags. Tests here verify the function returns a bool
and the boot flow honors it; they do NOT assert a specific outcome
because the outcome reflects the host's reality.
"""
from __future__ import annotations

from pathlib import Path

from cards_runner.common.types import RuntimePaths
from cards_runner.daemon.atomic_rename_sentinel import (
    ensure_sentinel_or_force_serial,
    is_sentinel_present,
    run_embedded_test,
    write_sentinel,
)


def test_run_embedded_test_returns_bool(tmp_path: Path) -> None:
    """The embedded test must run without raising and return a bool.

    The actual True/False outcome depends on the host's filesystem.
    Drew's NTFS volumes with AV active can yield intermittent
    "two winners" rounds; that is precisely what the sentinel is
    meant to detect.
    """
    result = run_embedded_test(work_dir=tmp_path / "race")
    assert isinstance(result, bool)


def test_ensure_returns_max_parallel_when_sentinel_present(
    paths: RuntimePaths,
) -> None:
    write_sentinel(paths)
    assert is_sentinel_present(paths)
    n = ensure_sentinel_or_force_serial(paths, max_parallel=4)
    assert n == 4


def test_ensure_demotes_to_serial_when_embedded_fails(
    paths: RuntimePaths,
    monkeypatch: object,
) -> None:
    """Sentinel missing AND embedded test fails -> max_parallel=1."""
    from cards_runner.daemon import atomic_rename_sentinel as mod
    paths.atomic_rename_sentinel.unlink()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        mod, "run_embedded_test", lambda **kw: False
    )
    n = ensure_sentinel_or_force_serial(paths, max_parallel=8)
    assert n == 1
    assert not is_sentinel_present(paths)


def test_ensure_writes_sentinel_when_embedded_passes(
    paths: RuntimePaths,
    monkeypatch: object,
) -> None:
    """Sentinel missing AND embedded test passes -> sentinel stamped."""
    from cards_runner.daemon import atomic_rename_sentinel as mod
    paths.atomic_rename_sentinel.unlink()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        mod, "run_embedded_test", lambda **kw: True
    )
    n = ensure_sentinel_or_force_serial(paths, max_parallel=8)
    assert n == 8
    assert is_sentinel_present(paths)
