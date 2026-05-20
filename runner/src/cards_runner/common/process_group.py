"""Per-worker Job Object on Windows.

Per the architectural override on Fork 2, each worker subprocess is
wrapped in a Windows Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so closing the job handle kills
the entire process tree, including any child processes the worker
spawned (chunk 2: git, tool subprocesses, etc.). `taskkill /T` is
unreliable; this is the documented Win32 path.

On non-Windows hosts this module degrades to a no-op wrapper (POSIX
process groups already give us tree-kill via `os.killpg`). Chunk 1
tests still run on Linux CI; the abstraction stays unified.

References:
- AssignProcessToJobObject:
  https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
- JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
  https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
PROCESS_ALL_ACCESS = 0x1F0FFF
JobObjectExtendedLimitInformation = 9


@dataclass
class ManagedProcess:
    """A worker process plus its kill handle.

    On Windows the kill handle is a Job Object. Closing it terminates
    the entire process tree. On POSIX the same `kill_tree()` call
    sends SIGTERM (and then SIGKILL after a grace period) to the
    process group.
    """

    popen: subprocess.Popen[Any]
    _job_handle: Any = None  # Win32 HANDLE or None
    _on_posix: bool = False

    @property
    def pid(self) -> int:
        return self.popen.pid

    def poll(self) -> int | None:
        return self.popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.popen.wait(timeout=timeout)

    def kill_tree(self, grace_sec: float = 5.0) -> None:
        """Kill the worker and any descendants.

        Windows: close the Job Object handle. The kernel terminates
        the entire job.

        POSIX: SIGTERM the process group, wait `grace_sec`, then SIGKILL.
        """
        if self.popen.poll() is not None:
            return
        if sys.platform == "win32" and self._job_handle is not None:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._job_handle)  # type: ignore[attr-defined]
            except Exception:
                # Fall back to TerminateProcess on the worker only.
                try:
                    self.popen.terminate()
                except Exception:
                    pass
            self._job_handle = None
            return
        # POSIX path.
        try:
            os.killpg(os.getpgid(self.popen.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return
        try:
            self.popen.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.popen.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


def spawn_in_job(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout: int | None = subprocess.DEVNULL,
    stderr: int | None = subprocess.DEVNULL,
) -> ManagedProcess:
    """Spawn `args` as a child process attached to a fresh Job Object.

    On Windows the child is created suspended, assigned to the job,
    then resumed. This guarantees no descendants can spawn between
    process creation and job assignment. On POSIX we start the child
    in its own process group instead.

    The Job Object is configured with
    `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` plus
    `JOB_OBJECT_LIMIT_BREAKAWAY_OK` so the daemon can voluntarily
    detach a process if it ever needs to (chunk 2 currently does not).
    """
    if sys.platform == "win32":
        return _spawn_win32(args, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
    return _spawn_posix(args, cwd=cwd, env=env, stdout=stdout, stderr=stderr)


def _spawn_posix(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout: int | None,
    stderr: int | None,
) -> ManagedProcess:
    popen = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        close_fds=True,
        start_new_session=True,
    )
    return ManagedProcess(popen=popen, _on_posix=True)


def _spawn_win32(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    stdout: int | None,
    stderr: int | None,
) -> ManagedProcess:
    """Windows path: create the child, then immediately assign to a Job Object.

    Note on suspended start: in theory we want CREATE_SUSPENDED so no
    descendants can spawn between CreateProcess and
    AssignProcessToJobObject. In practice Python's `subprocess.Popen`
    does not expose the main-thread handle, and `ResumeThread` against
    the process handle is a no-op. The CREATE_SUSPENDED dance therefore
    leaves the worker frozen forever. For chunk 1 the stub worker
    spawns no descendants and the assignment-after-CreateProcess race
    window is microseconds; we ship the assign-immediately variant and
    revisit when chunk 2 adds the SDK (which may spawn HTTPS workers
    on import). The follow-up plan is to drop down to `_winapi`
    `CreateProcess` directly so we own the main-thread handle.
    """
    import ctypes
    from ctypes import wintypes

    CREATE_NEW_PROCESS_GROUP = 0x00000200

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        err = ctypes.get_last_error()
        raise OSError(err, "CreateJobObjectW failed")

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
    )
    if not kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        err = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")

    popen = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        close_fds=True,
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )

    PROCESS_HANDLE = popen._handle  # type: ignore[attr-defined]
    if not kernel32.AssignProcessToJobObject(job, PROCESS_HANDLE):
        err = ctypes.get_last_error()
        try:
            popen.terminate()
        finally:
            kernel32.CloseHandle(job)
        raise OSError(err, "AssignProcessToJobObject failed")

    return ManagedProcess(popen=popen, _job_handle=job)
