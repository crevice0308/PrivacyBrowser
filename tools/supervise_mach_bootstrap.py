#!/usr/bin/env python3
"""Bound the owned bootstrap process tree without relaxing any build gate.

The child waits for a private stdin handshake until it belongs to a Windows
kill-on-close job. Closing that job kills only this invocation's descendants,
including hung download workers; no process-name matching or PID-tree scan.
"""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import runpy
import subprocess
import sys

import run_mach_bootstrap as bootstrap

TIMEOUT_SECONDS = 30 * 60
HANDSHAKE = b"privacybrowser-bootstrap-job-ready\n"


class WindowsJob:
    def __init__(self):
        # ctypes layout follows JOBOBJECT_EXTENDED_LIMIT_INFORMATION. Pointer-
        # sized SIZE_T/ULONG_PTR members matter on the x64 hosted interpreter.
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        # Popen keeps the Windows process handle open throughout supervision.
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


def supervise(argv, evidence, *, timeout=TIMEOUT_SECONDS, job_factory=WindowsJob):
    """Internal test seam; production CLI accepts no command/path overrides."""
    record = {"schema_version": 1, "phase": "bootstrap", "timeout_seconds": timeout,
              "timed_out": False, "exit_code": None, "interrupted": False,
              "tree_assigned": False}
    process, job = None, None
    # Refuse to overwrite evidence before starting any process.
    with evidence.open("x", encoding="utf-8") as output:
        try:
            job = job_factory()
            process = subprocess.Popen(argv, stdin=subprocess.PIPE)
            job.assign(process)
            record["tree_assigned"] = True
            process.stdin.write(HANDSHAKE)
            process.stdin.close()
            try:
                record["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
            return 124 if record["timed_out"] else record["exit_code"]
        except BaseException as error:
            record["interrupted"] = isinstance(error, (KeyboardInterrupt, SystemExit))
            record["error_type"] = type(error).__name__
            raise
        finally:
            primary_error = sys.exc_info()[0] is not None
            cleanup_error = None
            try:
                if job is not None:
                    job.close()
            except BaseException as error:
                cleanup_error = error
            try:
                if process is not None:
                    # Before assignment the child can only wait at the stdin
                    # gate. Kill that exact handle if assigning the job failed.
                    if not record["tree_assigned"] and process.poll() is None:
                        process.kill()
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
                    process.wait(timeout=30)
            except BaseException as error:
                cleanup_error = cleanup_error or error
            finally:
                if cleanup_error is not None:
                    record["cleanup_error_type"] = type(cleanup_error).__name__
                json.dump(record, output, indent=2)
                output.write("\n")
            # A cleanup error must not erase the original failure/interrupt.
            # On an otherwise normal return it must never report success.
            if cleanup_error is not None and not primary_error:
                raise cleanup_error



def main():
    if sys.argv[1:] not in ([], ["--child"]):
        raise ValueError("No bootstrap command, path or timeout overrides are supported")
    bootstrap.require_source()
    if sys.argv[1:] == ["--child"]:
        if sys.stdin.buffer.readline(len(HANDSHAKE) + 1) != HANDSHAKE:
            raise ValueError("Bootstrap child did not receive its owned-job handshake")
        sys.argv = [str(Path(__file__).with_name("run_mach_bootstrap.py"))]
        runpy.run_path(sys.argv[0], run_name="__main__")
        return 0
    return supervise([sys.executable, str(Path(__file__).resolve()), "--child"],
                     Path(r"D:\pb\evidence\bootstrap-supervisor.json"))


if __name__ == "__main__":
    sys.exit(main())
