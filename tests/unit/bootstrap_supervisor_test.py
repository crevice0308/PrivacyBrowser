"""Bounded inert process fixtures; no downloads, mach, or installer execution."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import supervise_mach_bootstrap as supervisor


class BootstrapSupervisorTests(unittest.TestCase):
    def test_cli_rejects_overrides_before_source_or_process_access(self):
        with mock.patch.object(sys, "argv", ["supervisor", "--timeout", "1"]), \
                mock.patch.object(supervisor.bootstrap, "require_source") as source:
            with self.assertRaises(ValueError):
                supervisor.main()
            source.assert_not_called()

    def test_nonhost_rejected_before_spawn(self):
        with mock.patch.object(sys, "argv", ["supervisor"]), \
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                mock.patch.object(subprocess, "Popen") as spawn:
            with self.assertRaises(ValueError):
                supervisor.main()
            spawn.assert_not_called()

    def test_exit_timeout_interrupt_and_assignment_failure(self):
        for mode in ("success", "failure", "timeout", "interrupt", "assignment"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                record = Path(temporary) / "result.json"
                process, job = mock.Mock(), mock.Mock()
                process.stdin = io.BytesIO()
                process.poll.return_value = None
                if mode == "timeout":
                    process.wait.side_effect = [subprocess.TimeoutExpired("inert", 1), 1]
                elif mode == "interrupt":
                    process.wait.side_effect = [KeyboardInterrupt(), 1]
                else:
                    process.wait.return_value = 7 if mode == "failure" else 0
                if mode == "assignment":
                    job.assign.side_effect = OSError("inert assignment rejection")
                with mock.patch.object(subprocess, "Popen", return_value=process) as spawn:
                    call = lambda: supervisor.supervise(["inert"], record, timeout=1, job_factory=lambda: job)
                    if mode == "interrupt":
                        with self.assertRaises(KeyboardInterrupt):
                            call()
                    elif mode == "assignment":
                        with self.assertRaises(OSError):
                            call()
                    else:
                        self.assertEqual(call(), {"success": 0, "failure": 7, "timeout": 124}[mode])
                spawn.assert_called_once_with(["inert"], stdin=subprocess.PIPE)
                job.assign.assert_called_once_with(process)
                job.close.assert_called_once_with()
                self.assertEqual(process.kill.call_count, int(mode == "assignment"))
                data = json.loads(record.read_text())
                self.assertEqual(data["timed_out"], mode == "timeout")
                self.assertEqual(data["interrupted"], mode == "interrupt")
                self.assertEqual(data["tree_assigned"], mode != "assignment")
                self.assertEqual(data["timeout_seconds"], 1)
                self.assertTrue(process.stdin.closed)

    def test_existing_evidence_prevents_process_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "result.json"
            record.write_text("original")
            factory = mock.Mock()
            with self.assertRaises(FileExistsError):
                supervisor.supervise(["inert"], record, job_factory=factory)
            factory.assert_not_called()
            self.assertEqual(record.read_text(), "original")

    def test_child_requires_private_handshake_before_mach(self):
        for incoming in (b"", b"wrong\n"):
            with mock.patch.object(sys, "argv", ["supervisor", "--child"]), \
                    mock.patch.object(supervisor.bootstrap, "require_source"), \
                    mock.patch.object(sys, "stdin", mock.Mock(buffer=io.BytesIO(incoming))), \
                    mock.patch.object(supervisor.runpy, "run_path") as run:
                with self.assertRaises(ValueError):
                    supervisor.main()
                run.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows job-object execution runs on Windows CI")
    def test_real_windows_job_bounds_inert_child_and_preserves_exit(self):
        for seconds, code in ((0, 0), (0, 7), (60, 0)):
            with self.subTest(seconds=seconds, code=code), tempfile.TemporaryDirectory() as temporary:
                record = Path(temporary) / "result.json"
                script = ("import sys,time; "
                          f"assert sys.stdin.buffer.readline() == {supervisor.HANDSHAKE!r}; "
                          f"time.sleep({seconds}); sys.exit({code})")
                result = supervisor.supervise([sys.executable, "-c", script], record, timeout=2)
                self.assertEqual(result, 124 if seconds else code)
                self.assertEqual(json.loads(record.read_text())["timed_out"], bool(seconds))


    def test_cleanup_failure_is_recorded_without_masking_interrupt(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "result.json"
            process, job = mock.Mock(), mock.Mock()
            process.stdin = io.BytesIO()
            process.wait.side_effect = [KeyboardInterrupt(), OSError("inert cleanup failure")]
            with mock.patch.object(subprocess, "Popen", return_value=process):
                with self.assertRaises(KeyboardInterrupt):
                    supervisor.supervise(["inert"], record, job_factory=lambda: job)
            data = json.loads(record.read_text())
            self.assertTrue(data["interrupted"])
            self.assertEqual(data["cleanup_error_type"], "OSError")

    @unittest.skipUnless(sys.platform == "win32", "Windows job tree execution runs on Windows CI")
    def test_real_job_kills_grandchild_but_not_unrelated_process(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with tempfile.TemporaryDirectory() as temporary:
                pid_file = Path(temporary) / "grandchild.pid"
                record = Path(temporary) / "result.json"
                script = ("import sys,time,subprocess,pathlib; "
                          f"assert sys.stdin.buffer.readline() == {supervisor.HANDSHAKE!r}; "
                          "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                          f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); "
                          "time.sleep(60)")
                self.assertEqual(supervisor.supervise([sys.executable, "-c", script], record, timeout=5), 124)
                pid = int(pid_file.read_text())
                handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
                if handle:
                    try:
                        self.assertEqual(api.WaitForSingleObject(handle, 5000), 0)
                    finally:
                        api.CloseHandle(handle)
                else:
                    self.assertEqual(ctypes.get_last_error(), 87)  # Process already gone
                self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
