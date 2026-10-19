"""Only mocked mach and self-authored shell commands; no upstream execution."""
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_mach_bootstrap as bootstrap


class BootstrapDiagnosticsTests(unittest.TestCase):
    def test_nonhost_is_rejected_before_mach_execution(self):
        with mock.patch.dict(bootstrap.os.environ, {"GITHUB_ACTIONS": "false"}), \
             mock.patch.object(sys, "argv", ["run_mach_bootstrap.py"]), \
             mock.patch.object(bootstrap.runpy, "run_path") as execute:
            with self.assertRaisesRegex(ValueError, "ephemeral hosted Windows"):
                bootstrap.main()
            execute.assert_not_called()

    def test_argument_override_is_rejected_before_source_access(self):
        with mock.patch.object(sys, "argv", ["run_mach_bootstrap.py", "--system-changes"]), \
             mock.patch.object(bootstrap, "require_source") as source:
            with self.assertRaisesRegex(ValueError, "overrides"):
                bootstrap.main()
            source.assert_not_called()

    def test_logging_preserves_exact_mach_context_and_exit(self):
        logger = logging.getLogger("moz.configure")
        prior_handlers = logger.handlers[:]
        stream = io.StringIO()
        prior_path = sys.path[:]
        argv = ["run_mach_bootstrap.py"]

        def fake_mach(path, run_name):
            self.assertEqual(path, "./mach")
            self.assertEqual(run_name, "__main__")
            self.assertEqual(sys.argv, ["./mach", *bootstrap.BOOTSTRAP_ARGS])
            self.assertEqual(sys.path[0], str(Path.cwd()))
            # The upstream sandbox disables propagation and captures into a
            # StringIO. Our additional handler must still expose its error.
            logger.error("inert configure failure\nsecond line")
            raise SystemExit(7)

        with mock.patch.object(bootstrap, "require_source") as source, \
             mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stderr", stream), \
             mock.patch.object(logger, "propagate", False), mock.patch.object(logger, "level", logging.ERROR), \
             mock.patch.object(bootstrap.runpy, "run_path", side_effect=fake_mach):
            with self.assertRaises(SystemExit) as raised:
                bootstrap.main()
            self.assertEqual(raised.exception.code, 7)
            self.assertIs(sys.argv, argv)
            source.assert_called_once_with()
        self.assertEqual(sys.path, prior_path)
        self.assertEqual(logger.handlers, prior_handlers)
        self.assertEqual(len(stream.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(stream.getvalue())["message"], "inert configure failure\nsecond line")

    def test_unexpected_exception_is_not_replaced_or_swallowed(self):
        failure = RuntimeError("inert test exception")
        with mock.patch.object(bootstrap, "require_source"), \
             mock.patch.object(sys, "argv", ["run_mach_bootstrap.py"]), \
             mock.patch.object(bootstrap.runpy, "run_path", side_effect=failure):
            with self.assertRaises(RuntimeError) as raised:
                bootstrap.main()
            self.assertIs(raised.exception, failure)

    def test_configure_errors_are_bounded_and_json_escaped(self):
        stream = io.StringIO()
        handler = bootstrap.ConfigureErrors(stream)
        message = "\n\r\x1b日本語" + "X" * 3000
        record = logging.LogRecord("moz.configure", logging.ERROR, "fixture", 1, message, (), None)
        for _ in range(bootstrap.MAX_ERRORS + 3):
            handler.emit(record)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), bootstrap.MAX_ERRORS)
        for line in lines:
            parsed = json.loads(line)
            self.assertEqual(parsed["message"], message[:bootstrap.MAX_MESSAGE_CHARS])
            self.assertTrue(parsed["truncated"])
            self.assertNotIn("\x1b", line)
        self.assertTrue(json.loads(lines[-1])["diagnostic_limit_reached"])

    def test_diagnostic_failure_cannot_replace_original_bootstrap_exit(self):
        class BadMessage:
            def __str__(self):
                raise ValueError("inert record formatting failure")

        logger = logging.getLogger("moz.configure")
        for broken_stream in (False, True):
            with self.subTest(broken_stream=broken_stream):
                stream = io.StringIO()
                if broken_stream:
                    stream.close()

                def fake_mach(*args, **kwargs):
                    logger.error("inert error" if broken_stream else BadMessage())
                    raise SystemExit(7)

                with mock.patch.object(bootstrap, "require_source"), \
                     mock.patch.object(sys, "argv", ["run_mach_bootstrap.py"]), \
                     mock.patch.object(sys, "stderr", stream), \
                     mock.patch.object(logger, "propagate", False), \
                     mock.patch.object(logger, "level", logging.ERROR), \
                     mock.patch.object(bootstrap.runpy, "run_path", side_effect=fake_mach):
                    with self.assertRaises(SystemExit) as raised:
                        bootstrap.main()
                    self.assertEqual(raised.exception.code, 7)

    def test_diagnostic_handler_does_not_swallow_base_exceptions(self):
        handler = bootstrap.ConfigureErrors(io.StringIO())
        record = mock.Mock()
        for error in (KeyboardInterrupt(), SystemExit(9)):
            record.getMessage.side_effect = error
            with self.subTest(kind=type(error)), self.assertRaises(type(error)):
                handler.emit(record)

    def test_shell_records_both_statuses_and_stops_on_either_failure(self):
        source = (ROOT / "tools/build_windows_native.sh").read_text()
        function = source.split("run_logged() {", 1)[1].split("\n}\n", 1)[0]
        for command_code, tee_code in ((0, 0), (7, 0), (0, 9), (7, 9)):
            with self.subTest(command=command_code, tee=tee_code), tempfile.TemporaryDirectory() as temporary:
                # No source mach, compiler, installer or native program is run.
                script = ('set -euo pipefail\nevidence="$PWD"\nrun_logged() {' + function + '\n}\n'
                          + ('tee() { cat >/dev/null; return 9; }\n' if tee_code else '')
                          + f"run_logged fixture bash -c 'printf inert; exit {command_code}'\n"
                          + 'printf success > continued.txt\n')
                result = subprocess.run(["bash", "-c", script], cwd=temporary, capture_output=True,
                                        text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, command_code or tee_code, result.stderr)
                proof = json.loads((Path(temporary) / "fixture-exit.json").read_text())
                self.assertEqual(proof["command_exit_code"], command_code)
                self.assertEqual(proof["tee_exit_code"], tee_code)
                self.assertEqual((Path(temporary) / "continued.txt").exists(), not (command_code or tee_code))


if __name__ == "__main__":
    unittest.main()
