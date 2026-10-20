"""Offline protocol/evidence contracts. Every native-looking fixture is INERT.

No browser, installer, socket listener, downloaded tool or native PE is executed.
Other gate tests may import make_retained_fixture for a supplied inert runtime.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import windows_autoconfig_smoke as smoke


def make_retained_fixture(evidence: Path, runtime: Path) -> dict:
    """Write complete INERT passing evidence; never create/modify/run the binary.

    runtime/privacybrowser.exe must already exist with the caller's inert bytes.
    All four evidence outputs must be absent. This helper is test-only.
    """
    evidence.mkdir(parents=True, exist_ok=True)
    digest = smoke.native.digest(smoke.native.regular_file(runtime, "privacybrowser.exe"))
    defaults, effective = smoke.expected_preferences()
    privacy = {"state": "ready", "flags": dict.fromkeys(smoke.HISTORY, False), "failedCategories": []}
    raw = {
        "fixture_notice": "INERT: synthetic observations; no Windows binary was executed.",
        "schema_version": 1, "argv": smoke.expected_argv(), "host": "127.0.0.1",
        "port": 49152, "active_port": 49152,
        "environment_controls": {"inherited_build_automation_present": False,
                                 "removed_build_control_keys": [],
                                 "child_build_automation_present": False,
                                 "crash_reports_not_submitted": True},
        "session": {"acceptInsecureCerts": False, "pageLoadStrategy": "none",
                    "unhandledPromptBehavior": "ignore", "moz:profile": str(smoke.PROFILE),
                    "moz:processID": 12345,
                    "timeouts": {"script": 10000, "pageLoad": 10000, "implicit": 0}},
        "startup": {
            "loaded_before_inspection": dict.fromkeys(smoke.MODULES, True),
            "glue": {"started": True, "privacy": privacy, "diagnosticsEnabled": False},
            "controller": deepcopy(privacy), "default_prefs": defaults, "effective_prefs": effective,
            "recommended_preferences": False, "recommended_preferences_applied": False,
            "build_automation_present": False,
            "unclean_exit": True, "window_attached": True, "delayed_startup_finished": True,
            "search_control": True, "menu_control": True, "newtab_url": smoke.HOME,
            "sandbox": {"compiled": True, "default_level": 6, "effective_level": 6,
                        "e10s": True, "fission": True, "window_remote_subframes": True},
        },
        "home": {"uri": smoke.HOME, "module_ready": True, "system_principal": True,
                 "search_input": True, "history_label": "Zero History", "engine_name": "Brave Search"},
        "quit": {"cause": "shutdown", "forced": False, "in_app": True}, "exit_code": 0,
        "forced_process_cleanup": False, "log_capture_error": False, "log_truncated": False,
        "after_exit": {"unclean_exit_false": True, "session_files_absent": True,
                       "search_files_absent": True, "diagnostics_absent": True},
        "native_binary_sha256_before": digest, "native_binary_sha256_after": digest,
    }
    config = smoke.user_config(raw["port"])
    for name, data in ((smoke.LOG, b"INERT synthetic browser output. No executable was launched.\n"),
                       (smoke.USER_CONFIG, config)):
        with (evidence / name).open("xb") as stream:
            stream.write(data)
    smoke.write_json(evidence / smoke.OBSERVATION, raw)
    report = {
        "fixture_notice": "INERT: for offline validation tests only.",
        "schema_version": 1, "passed": True, "native_binary_sha256": digest,
        "argv": smoke.expected_argv(), "checks": smoke.derive_checks(raw, config, digest),
        "release_eligible": False, "broader_privacy_security_acceptance": False,
        "limitation": "INERT test fixture, not evidence of execution or acceptance.",
        "records": {role: {"path": name, "sha256": smoke.native.digest(evidence / name)} for role, name in (
            ("observation", smoke.OBSERVATION), ("browser_log", smoke.LOG),
            ("test_control_preferences", smoke.USER_CONFIG))},
    }
    smoke.write_json(evidence / smoke.REPORT, report)
    return report


class RetainedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.runtime = self.work / "arbitrary-linux-unpack/PrivacyBrowser"
        self.runtime.mkdir(parents=True)
        self.binary = self.runtime / "privacybrowser.exe"
        self.binary.write_bytes(b"INERT fixture binary, never executable")
        self.evidence = self.work / "evidence"
        make_retained_fixture(self.evidence, self.runtime)
        self.original = smoke.native.load_json(self.evidence / smoke.OBSERVATION)

    def validate(self):
        return smoke.validate_retained(self.evidence, self.runtime)

    def replace_record(self, role, data):
        report = smoke.native.load_json(self.evidence / smoke.REPORT)
        path = self.evidence / report["records"][role]["path"]
        path.write_bytes(data)
        report["records"][role]["sha256"] = smoke.native.digest(path)
        (self.evidence / smoke.REPORT).write_text(json.dumps(report), encoding="utf-8")

    def replace_raw(self, raw):
        # Deliberately retain all-true summary checks, defeating summary-only gates.
        self.replace_record("observation", json.dumps(raw).encode())

    def test_complete_fixture_passes_at_arbitrary_linux_runtime_path(self):
        report = self.validate()
        self.assertEqual(set(report["checks"]), set(smoke.CHECKS))
        self.assertEqual(len(smoke.CHECKS), 14)
        self.assertTrue(all(value is True for value in report["checks"].values()))
        self.assertFalse(report["release_eligible"])
        self.assertFalse(report["broader_privacy_security_acceptance"])
        self.assertIn("INERT", report["fixture_notice"])
        self.assertEqual(self.binary.read_bytes(), b"INERT fixture binary, never executable")

    def test_fixture_outputs_are_exclusive(self):
        with self.assertRaises(FileExistsError):
            make_retained_fixture(self.evidence, self.runtime)

    def test_each_required_observation_cannot_be_forged_with_positive_summary(self):
        mutations = [
            (("schema_version",), True), (("host",), "0.0.0.0"), (("active_port",), 49153),
            (("port",), 0), (("port",), -1), (("port",), True), (("port",), 65536),
            (("session", "acceptInsecureCerts"), True), (("session", "moz:profile"), "D:\\other"),
            (("session", "moz:processID"), True), (("session", "moz:processID"), 0),
            (("session", "timeouts", "script"), 0), (("session", "pageLoadStrategy"), "normal"),
            (("session", "unhandledPromptBehavior"), "dismiss"),
            (("startup", "loaded_before_inspection", smoke.MODULES[0]), False),
            (("startup", "loaded_before_inspection", smoke.MODULES[1]), 1),
            (("startup", "glue", "started"), False),
            (("startup", "glue", "privacy", "state"), "failed"),
            (("startup", "glue", "privacy", "failedCategories"), ["cookies"]),
            (("startup", "controller", "flags", "browsing"), 0),
            (("startup", "recommended_preferences"), True),
            (("startup", "recommended_preferences_applied"), True),
            (("startup", "build_automation_present"), True),
            (("startup", "unclean_exit"), False), (("startup", "window_attached"), False),
            (("startup", "delayed_startup_finished"), False),
            (("startup", "search_control"), False), (("startup", "menu_control"), False),
            (("startup", "newtab_url"), "about:blank"),
            (("startup", "glue", "diagnosticsEnabled"), True),
            (("startup", "sandbox", "compiled"), False),
            (("startup", "sandbox", "effective_level"), 0),
            (("startup", "sandbox", "default_level"), 7),
            (("startup", "sandbox", "effective_level"), True),
            (("startup", "sandbox", "fission"), False), (("startup", "sandbox", "e10s"), False),
            (("startup", "sandbox", "window_remote_subframes"), False),
            (("home", "uri"), "https://example.invalid"), (("home", "module_ready"), False),
            (("home", "system_principal"), False), (("home", "search_input"), False),
            (("home", "history_label"), "History"), (("home", "engine_name"), "Other"),
            (("quit", "forced"), True), (("quit", "in_app"), False),
            (("exit_code",), 1), (("exit_code",), False), (("forced_process_cleanup",), True),
            (("after_exit", "unclean_exit_false"), False),
            (("after_exit", "session_files_absent"), False),
            (("after_exit", "search_files_absent"), False),
            (("after_exit", "diagnostics_absent"), False),
            (("native_binary_sha256_before",), "0" * 64),
            (("native_binary_sha256_after",), "0" * 64),
            (("log_capture_error",), True), (("log_truncated",), None),
            (("error_type",), "ValueError"), (("error_type",), ""),
            (("owned_process_cleanup_failed",), True), (("owned_process_cleanup_failed",), False),
            (("environment_controls", "inherited_build_automation_present"), 0),
            (("environment_controls", "removed_build_control_keys"), ["MOZ_DISABLE_CONTENT_SANDBOX"]),
            (("environment_controls", "child_build_automation_present"), True),
            (("environment_controls", "crash_reports_not_submitted"), False),
            (("startup",), None), (("session",), []), (("home",), "invalid"), (("quit",), False),
        ]
        for key in smoke.HISTORY:
            mutations.append((("startup", "glue", "privacy", "flags", key), True))
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                raw = deepcopy(self.original)
                container = raw
                for key in path[:-1]:
                    container = container[key]
                container[path[-1]] = value
                self.replace_raw(raw)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_every_default_and_effective_pref_is_type_strict(self):
        for branch in ("default_prefs", "effective_prefs"):
            for key, value in self.original["startup"][branch].items():
                with self.subTest(branch=branch, key=key):
                    raw = deepcopy(self.original)
                    raw["startup"][branch][key] = int(value) if type(value) is bool else None
                    self.replace_raw(raw)
                    with self.assertRaises(ValueError):
                        self.validate()

    def test_all_check_names_and_boolean_types_are_required(self):
        original = smoke.native.load_json(self.evidence / smoke.REPORT)
        for key in smoke.CHECKS:
            for value in (False, None, 1, "true"):
                report = deepcopy(original)
                report["checks"][key] = value
                (self.evidence / smoke.REPORT).write_text(json.dumps(report), encoding="utf-8")
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.validate()
        for checks in ({}, {**original["checks"], "unrequested": True}):
            report = deepcopy(original)
            report["checks"] = checks
            (self.evidence / smoke.REPORT).write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.validate()

    def test_test_control_bytes_are_exact_and_cannot_mask_privacy(self):
        config = smoke.user_config(49152)
        for altered in (config + b'user_pref("places.history.enabled", false);\n',
                        config.replace(b"false", b"true"), config.replace(b"49152", b"49153"),
                        config.replace(b"\n", b"\r\n"), config + b"// extra\n"):
            self.replace_record("test_control_preferences", altered)
            with self.assertRaises(ValueError):
                self.validate()

    def test_no_unrequested_argument_or_network_navigation(self):
        raw = deepcopy(self.original)
        raw["argv"] = smoke.expected_argv() + ["--remote-debugging-port", "9222"]
        self.replace_raw(raw)
        with self.assertRaises(ValueError):
            self.validate()

    def test_hashes_bind_all_retained_files_and_actual_binary(self):
        for name in (smoke.OBSERVATION, smoke.LOG, smoke.USER_CONFIG):
            path = self.evidence / name
            data = path.read_bytes()
            path.write_bytes(data + b" ")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate()
            path.write_bytes(data)
        self.binary.write_bytes(b"INERT modified binary")
        with self.assertRaises(ValueError):
            self.validate()

    def test_retained_log_and_json_are_bounded(self):
        self.replace_record("browser_log", b"x" * (smoke.MAX_LOG + 1))
        with self.assertRaises(ValueError):
            self.validate()
        self.replace_record("browser_log", b"INERT\n")
        self.replace_record("observation", b" " * (smoke.MAX_JSON + 1))
        with self.assertRaises(ValueError):
            self.validate()

    def test_record_paths_cannot_escape_or_change_names(self):
        original = smoke.native.load_json(self.evidence / smoke.REPORT)
        for path in ("../other.json", "another-observation.json", "/tmp/absolute.json"):
            report = deepcopy(original)
            report["records"]["observation"]["path"] = path
            (self.evidence / smoke.REPORT).write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.validate()


def frame(value):
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data


GREETING = {"applicationType": "gecko", "marionetteProtocol": 3}


class InertSocket:
    def __init__(self, data):
        self.data, self.sent, self.timeouts = data, [], []

    def recv(self, size):
        # Fragment every message; this is not an actual network socket.
        size = min(size, 3)
        result, self.data = self.data[:size], self.data[size:]
        return result

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.sent.append(value)


class ProtocolTests(unittest.TestCase):
    def client(self, responses=b""):
        sock = InertSocket(frame(GREETING) + responses)
        return smoke.Marionette(sock, time.monotonic() + 30), sock

    def test_direct_newsession_and_quit_results_and_utf8_framing(self):
        session = {"sessionId": "INERT", "capabilities": {"acceptInsecureCerts": False}}
        quit_result = {"cause": "shutdown", "forced": False, "in_app": True}
        client, sock = self.client(frame([1, 1, None, session]) + frame([1, 2, None, quit_result]))
        parameters = {"acceptInsecureCerts": False, "unhandledPromptBehavior": "ignore", "INERT": "日本語"}
        self.assertEqual(client.command("WebDriver:NewSession", parameters), session)
        self.assertEqual(client.command("Marionette:Quit", {"flags": ["eAttemptQuit"], "safeMode": False}), quit_result)
        header, encoded = sock.sent[0].split(b":", 1)
        self.assertEqual(int(header), len(encoded))
        self.assertEqual(json.loads(encoded), [0, 1, "WebDriver:NewSession", parameters])
        self.assertNotIn("capabilities", json.loads(encoded)[3])
        self.assertTrue(all(0 < value <= 15 for value in sock.timeouts))

    def test_execute_script_value_wrapper_is_required(self):
        client, sock = self.client(frame([1, 1, None, {"value": {"INERT": True}}]))
        self.assertEqual(client.script("return arguments[0];", [{"INERT": True}]), {"INERT": True})
        command = json.loads(sock.sent[0].split(b":", 1)[1])
        self.assertEqual(command[2], "WebDriver:ExecuteScript")
        self.assertTrue(command[3]["newSandbox"])
        for bad in (True, {"result": True}, {"value": True, "extra": 1}):
            client, _ = self.client(frame([1, 1, None, bad]))
            with self.assertRaises(ValueError):
                client.script("return true;")

    def test_wrong_greeting_response_id_error_and_eof_fail_closed(self):
        for greeting in ({}, {"applicationType": "gecko", "marionetteProtocol": 2}):
            with self.assertRaises(ValueError):
                smoke.Marionette(InertSocket(frame(greeting)), time.monotonic() + 30)
        for response in ([1, 2, None, {}], [1, True, None, {}], [0, 1, None, {}],
                         [True, 1, None, {}], [1, 1, {"error": "INERT failure"}, None], {}):
            client, _ = self.client(frame(response))
            with self.assertRaises(ValueError):
                client.command("INERT")
        client, _ = self.client()
        with self.assertRaises(ValueError):
            client.command("Marionette:Quit")

    def test_malformed_duplicate_oversized_or_expired_frame_fails_closed(self):
        duplicate = b'{"applicationType":"gecko","marionetteProtocol":3,"marionetteProtocol":3}'
        for data in (b":{}", b"x:{}", b"00000001:{}", b"0:",
                     str(smoke.MAX_MESSAGE + 1).encode() + b":", b"3:{", b"1:\xff",
                     str(len(duplicate)).encode() + b":" + duplicate):
            with self.subTest(data=data[:30]), self.assertRaises((ValueError, UnicodeError)):
                smoke.Marionette(InertSocket(data), time.monotonic() + 30)
        client, sock = self.client()
        client.deadline = time.monotonic() - 1
        with self.assertRaises(ValueError):
            client.command("INERT")
        self.assertEqual(sock.sent, [])


class SourceAndFilesystemTests(unittest.TestCase):
    def test_active_port_missing_then_empty_then_expected_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            active = profile / "MarionetteActivePort"
            process = mock.Mock()
            process.poll.return_value = None
            writes = iter((b"", b"49152"))
            def advance(_seconds):
                active.write_bytes(next(writes))
            with mock.patch.object(smoke.time, "monotonic", side_effect=(0, .1, .2)), mock.patch.object(smoke.time, "sleep", side_effect=advance) as sleep, mock.patch.object(smoke.socket, "create_connection") as connect:
                self.assertEqual(smoke.wait_for_active_port(profile, 49152, process, 1), 49152)
                self.assertEqual(sleep.call_count, 2)
                connect.assert_not_called()

    def test_active_port_forever_empty_or_dead_process_fails_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "MarionetteActivePort").write_bytes(b"")
            process = mock.Mock()
            process.poll.return_value = None
            with mock.patch.object(smoke.time, "monotonic", side_effect=(0, 1)), mock.patch.object(smoke.time, "sleep") as sleep:
                with self.assertRaises(ValueError):
                    smoke.wait_for_active_port(profile, 49152, process, 1)
                sleep.assert_called_once_with(.1)
            process.poll.return_value = 1
            with mock.patch.object(smoke.time, "sleep") as sleep:
                with self.assertRaises(ValueError):
                    smoke.wait_for_active_port(profile, 49152, process, float("inf"))
                sleep.assert_not_called()

    def test_active_port_invalid_nonempty_records_rejected_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            process = mock.Mock()
            process.poll.return_value = None
            for data in (b"49153", b"abc", b"49152\n", b"\n", b"000000", b"\xff", b"x" * 100):
                (profile / "MarionetteActivePort").write_bytes(data)
                with self.subTest(data=data), mock.patch.object(smoke.time, "sleep") as sleep:
                    with self.assertRaises(ValueError):
                        smoke.wait_for_active_port(profile, 49152, process, float("inf"))
                    sleep.assert_not_called()

    def test_active_port_reparse_record_rejected_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "MarionetteActivePort").write_bytes(b"")
            process = mock.Mock()
            process.poll.return_value = None
            with mock.patch.object(smoke.native, "is_link_or_reparse", return_value=True), mock.patch.object(smoke.time, "sleep") as sleep:
                with self.assertRaises(ValueError):
                    smoke.wait_for_active_port(profile, 49152, process, float("inf"))
                sleep.assert_not_called()

    def test_build_only_automation_is_removed_from_child_not_host(self):
        for present in (False, True):
            inherited = {"PATH": "INERT", **({"MOZ_AUTOMATION": "1"} if present else {})}
            with mock.patch.dict(smoke.os.environ, inherited, clear=True):
                child, controls = smoke.child_environment()
                self.assertEqual(dict(smoke.os.environ), inherited)
                self.assertNotIn("MOZ_AUTOMATION", child)
                self.assertEqual(child["MOZ_CRASHREPORTER_NO_REPORT"], "1")
                self.assertEqual(controls["removed_build_control_keys"], ["MOZ_AUTOMATION"] if present else [])
                self.assertIs(controls["inherited_build_automation_present"], present)
                self.assertIs(controls["child_build_automation_present"], False)

    def test_inherited_security_disabling_overrides_fail_closed(self):
        for key in smoke.FORBIDDEN_ENV:
            with self.subTest(key=key), mock.patch.dict(smoke.os.environ, {key: "1"}, clear=True):
                with self.assertRaises(ValueError):
                    smoke.child_environment()

    def test_expected_preferences_parse_literals_and_allow_identical_overlap(self):
        defaults, effective = smoke.expected_preferences()
        self.assertIs(defaults["app.update.auto"], False)
        self.assertIs(defaults["app.update.background.scheduling.enabled"], False)
        self.assertEqual(defaults["general.config.filename"], "privacybrowser.cfg")
        self.assertEqual(effective, {**defaults, **smoke.POLICY_PREFS})
        for declaration in ('pref("a", false);\npref("a", false);', 'pref("a", call());',
                            'user_pref("a", false);', 'pref("a", null);', ''):
            with self.assertRaises(ValueError):
                smoke.parse_preferences(declaration)
        self.assertEqual(smoke.parse_preferences('// note\npref("a", false);\npref("b", 2);'), {"a": False, "b": 2})

    def test_conflicting_overlap_fails_closed_without_executing_source(self):
        text = (ROOT / "browser/privacy/defaults.js").read_text(encoding="utf-8")
        original = Path.read_text
        def conflicting(path, *args, **kwargs):
            if path == ROOT / "browser/privacy/defaults.js":
                return text.replace('pref("app.update.auto", false);', 'pref("app.update.auto", true);')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", conflicting), self.assertRaises(ValueError):
            smoke.expected_preferences()

    def test_observer_cannot_mask_startup_or_mutate_policy(self):
        script = smoke.STARTUP_SCRIPT
        self.assertLess(script.index("isESModuleLoaded"), script.index("importESModule"))
        self.assertIn("if (!Object.values(loaded).every", script)
        self.assertIn("QueryInterface(Ci.nsILoadContext).useRemoteSubframes", script)
        scripts = script + smoke.OPEN_HOME_SCRIPT + smoke.HOME_SCRIPT
        for forbidden in (".startup(", ".configure(", ".clearAll(", "setBoolPref", "setIntPref", "setStringPref"):
            self.assertNotIn(forbidden, scripts)
        self.assertNotIn("http:", scripts)
        self.assertNotIn("https:", scripts)
        self.assertEqual(smoke.expected_argv()[-1], "about:blank")
        self.assertNotIn("--remote-debugging-port", smoke.expected_argv())
        self.assertEqual(smoke.user_config(49152), b'user_pref("marionette.port", 49152);\nuser_pref("remote.prefs.recommended", false);\n')

    def test_nonwindows_run_is_rejected_before_any_process_or_socket(self):
        with mock.patch.object(smoke.os, "name", "posix"), mock.patch.object(smoke.subprocess, "Popen") as popen, mock.patch.object(smoke.socket, "socket") as sock:
            with self.assertRaises(ValueError):
                smoke.run(Path("runtime"), Path("profile"), Path("evidence"))
            popen.assert_not_called()
            sock.assert_not_called()

    def test_after_exit_checks_only_owned_profile_and_requires_clean_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            prefs = profile / "prefs.js"
            prefs.write_text('user_pref("privacybrowser.uncleanExit", false);\n', encoding="utf-8")
            self.assertTrue(all(smoke.after_exit(profile).values()))
            prefs.write_text('user_pref("privacybrowser.uncleanExit", true);\n', encoding="utf-8")
            self.assertFalse(smoke.after_exit(profile)["unclean_exit_false"])
            (profile / "sessionstore.jsonlz4").write_bytes(b"INERT")
            self.assertFalse(smoke.after_exit(profile)["session_files_absent"])
            (profile / "privacybrowser-search-history.json.tmp").write_bytes(b"INERT")
            self.assertFalse(smoke.after_exit(profile)["search_files_absent"])
            (profile / "privacybrowser-diagnostics").mkdir()
            self.assertFalse(smoke.after_exit(profile)["diagnostics_absent"])


if __name__ == "__main__":
    unittest.main()
