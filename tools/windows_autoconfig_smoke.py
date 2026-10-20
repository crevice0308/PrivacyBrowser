#!/usr/bin/env python3
"""Bounded hosted-Windows observation of automatic AutoConfig integration.

Called only after the installer test establishes ownership of known-folder data
roots. No product startup/configuration function is called by this probe.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import socket
import subprocess
import threading
import time

import verify_windows_native as native

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PureWindowsPath(r"D:\pb\candidate\PrivacyBrowser")
PROFILE = PureWindowsPath(r"D:\pb\windows-autoconfig-profile")
REPORT = "windows-autoconfig-smoke.json"
OBSERVATION = "windows-autoconfig-observation.json"
LOG = "windows-autoconfig-browser.log"
USER_CONFIG = "windows-autoconfig-user.js.txt"
MAX_LOG = 256 * 1024
MAX_MESSAGE = 512 * 1024
MAX_JSON = 128 * 1024
TOTAL_SECONDS = 180
HISTORY = ("browsing", "search", "cookies", "downloads", "forms", "cache", "sessions", "siteStorage")
MODULES = ("resource://privacybrowser/BrowserGlue.sys.mjs", "resource://privacybrowser/GeckoPrivacy.sys.mjs")
HOME = "chrome://privacybrowser/content/home.html"
CHECKS = (
    "loopback_session_bound_to_owned_profile", "exact_test_control_preferences",
    "packaged_autoconfig_defaults_effective", "automation_recommended_preferences_disabled",
    "modules_loaded_before_inspection", "automatic_privacy_startup_ready",
    "all_eight_history_flags_disabled", "effective_privacy_preference_plan",
    "automatic_browser_window_integration", "content_sandbox_and_fission_preserved",
    "custom_home_module_and_system_principal", "graceful_automatic_shutdown",
    "no_session_search_or_diagnostics_files", "native_binary_unchanged",
)
POLICY_PREFS = {
    "places.history.enabled": False, "browser.formfill.enable": False,
    "browser.cache.disk.enable": False, "browser.cache.disk.smart_size.enabled": False,
    "browser.cache.offline.enable": False, "browser.cache.memory.enable": True,
    "browser.sessionstore.resume_from_crash": False, "browser.sessionstore.max_tabs_undo": 0,
    "browser.sessionstore.max_windows_undo": 0, "browser.sessionstore.privacy_level": 2,
    "browser.sessionstore.resume_session_once": False, "browser.startup.page": 1,
    "browser.download.manager.addToRecentDocs": False,
    "browser.search.suggest.enabled": False, "browser.urlbar.suggest.searches": False,
    **{"privacybrowser.history." + key: False for key in HISTORY},
}
FORBIDDEN_ENV = (
    "MOZ_DISABLE_CONTENT_SANDBOX", "MOZ_DISABLE_GPU_SANDBOX", "MOZ_DISABLE_RDD_SANDBOX",
    "MOZ_DISABLE_GMP_SANDBOX", "MOZ_DISABLE_SOCKET_PROCESS_SANDBOX",
    "MOZ_DISABLE_UTILITY_SANDBOX", "MOZ_FORCE_DISABLE_E10S", "MOZ_FORCE_DISABLE_FISSION",
    "MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS", "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS",
    "MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA",
)


def parse_preferences(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        match = re.fullmatch(r'pref\(("(?:[^"\\]|\\.)*"),\s*(true|false|-?\d+|"(?:[^"\\]|\\.)*")\);', line)
        native.require(match is not None, "Unsupported source preference declaration")
        key, value = (json.loads(part) for part in match.groups())
        native.require(key not in result and type(value) in (bool, int, str), "Duplicate/unsupported source preference")
        result[key] = value
    native.require(bool(result), "Empty source preferences")
    return result


def expected_preferences() -> tuple[dict, dict]:
    tree = ast.parse((ROOT / "tools/build_dev.py").read_text(encoding="utf-8"))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "AUTOCONFIG" for target in node.targets)]
    native.require(len(values) == 1 and isinstance(values[0], str), "Expected literal AutoConfig preferences")
    defaults = parse_preferences(values[0])
    product = parse_preferences((ROOT / "browser/privacy/defaults.js").read_text(encoding="utf-8"))
    native.require(all(type(defaults[key]) is type(product[key]) and defaults[key] == product[key]
                       for key in set(defaults) & set(product)), "Conflicting AutoConfig/product defaults")
    defaults.update(product)
    return defaults, {**defaults, **POLICY_PREFS}


def user_config(port: int) -> bytes:
    native.require(type(port) is int and 1024 <= port <= 65535, "Invalid ephemeral Marionette port")
    return (f'user_pref("marionette.port", {port});\n'
            'user_pref("remote.prefs.recommended", false);\n').encode("ascii")


def expected_argv() -> list[str]:
    return [str(RUNTIME / "privacybrowser.exe"), "--headless", "--no-remote", "--marionette",
            "--remote-allow-system-access", "--profile", str(PROFILE), "about:blank"]


def child_environment() -> tuple[dict, dict]:
    """Remove build-only automation state in the child, never change the host."""
    native.require(not any(os.environ.get(name) for name in FORBIDDEN_ENV),
                   "Conflicting inherited runtime/security overrides")
    environment = os.environ.copy()
    inherited = "MOZ_AUTOMATION" in environment
    environment.pop("MOZ_AUTOMATION", None)
    # --headless and --marionette already make the native launcher wait. Keeping
    # a build automation override is unnecessary for this fresh-profile probe.
    environment["MOZ_CRASHREPORTER_NO_REPORT"] = "1"
    return environment, {
        "inherited_build_automation_present": inherited,
        "removed_build_control_keys": ["MOZ_AUTOMATION"] if inherited else [],
        "child_build_automation_present": False, "crash_reports_not_submitted": True,
    }


def same_values(actual, expected) -> bool:
    return isinstance(actual, dict) and set(actual) == set(expected) and all(
        type(actual[key]) is type(value) and actual[key] == value for key, value in expected.items())


def derive_checks(observation: dict, config: bytes, binary_hash: str) -> dict[str, bool]:
    defaults, effective = expected_preferences()
    def obj(value):
        return value if isinstance(value, dict) else {}
    startup, home = obj(observation.get("startup")), obj(observation.get("home"))
    session, after = obj(observation.get("session")), obj(observation.get("after_exit"))
    glue = obj(startup.get("glue"))
    privacy, controller = obj(glue.get("privacy")), obj(startup.get("controller"))
    port = observation.get("port")
    valid_port = type(port) is int and 1024 <= port <= 65535
    quit_result = obj(observation.get("quit"))
    sandbox = obj(startup.get("sandbox"))
    level = sandbox.get("effective_level")
    controls = obj(observation.get("environment_controls"))
    inherited = controls.get("inherited_build_automation_present")
    controlled_environment = (
        set(controls) == {"inherited_build_automation_present", "removed_build_control_keys",
                          "child_build_automation_present", "crash_reports_not_submitted"}
        and type(inherited) is bool
        and controls.get("removed_build_control_keys") == (["MOZ_AUTOMATION"] if inherited else [])
        and controls.get("child_build_automation_present") is False
        and controls.get("crash_reports_not_submitted") is True)
    return {
        "loopback_session_bound_to_owned_profile": (
            type(observation.get("schema_version")) is int and observation["schema_version"] == 1
            and observation.get("argv") == expected_argv()
            and observation.get("host") == "127.0.0.1" and valid_port
            and type(observation.get("active_port")) is int and observation["active_port"] == port
            and session.get("acceptInsecureCerts") is False
            and session.get("pageLoadStrategy") == "none"
            and session.get("unhandledPromptBehavior") == "ignore"
            and same_values(session.get("timeouts"), {"script": 10000, "pageLoad": 10000, "implicit": 0})
            and isinstance(session.get("moz:profile"), str)
            and PureWindowsPath(session["moz:profile"]) == PROFILE
            and type(session.get("moz:processID")) is int and session["moz:processID"] > 0),
        "exact_test_control_preferences": valid_port and config == user_config(port) and controlled_environment,
        "packaged_autoconfig_defaults_effective": same_values(startup.get("default_prefs"), defaults),
        "automation_recommended_preferences_disabled": (
            startup.get("recommended_preferences") is False
            and startup.get("recommended_preferences_applied") is False
            and startup.get("build_automation_present") is False),
        "modules_loaded_before_inspection": same_values(startup.get("loaded_before_inspection"), dict.fromkeys(MODULES, True)),
        "automatic_privacy_startup_ready": (glue.get("started") is True and privacy.get("state") == "ready"
            and set(privacy) == {"state", "flags", "failedCategories"} and controller == privacy
            and same_values(controller.get("flags"), dict.fromkeys(HISTORY, False))
            and privacy.get("failedCategories") == []
            and startup.get("unclean_exit") is True),
        "all_eight_history_flags_disabled": same_values(privacy.get("flags"), dict.fromkeys(HISTORY, False)),
        "effective_privacy_preference_plan": same_values(startup.get("effective_prefs"), effective),
        "automatic_browser_window_integration": (
            startup.get("window_attached") is True and startup.get("delayed_startup_finished") is True
            and startup.get("search_control") is True and startup.get("menu_control") is True
            and startup.get("newtab_url") == HOME and glue.get("diagnosticsEnabled") is False),
        "content_sandbox_and_fission_preserved": (
            sandbox.get("compiled") is True and type(level) is int and level > 0
            and type(sandbox.get("default_level")) is int and level == sandbox["default_level"]
            and sandbox.get("e10s") is True and sandbox.get("fission") is True
            and sandbox.get("window_remote_subframes") is True),
        "custom_home_module_and_system_principal": (
            home.get("uri") == HOME and home.get("module_ready") is True
            and home.get("system_principal") is True and home.get("search_input") is True
            and home.get("history_label") == "Zero History" and home.get("engine_name") == "Brave Search"),
        "graceful_automatic_shutdown": (
            quit_result.get("forced") is False and quit_result.get("in_app") is True
            and type(observation.get("exit_code")) is int and observation["exit_code"] == 0
            and observation.get("forced_process_cleanup") is False
            and observation.get("log_capture_error") is False
            and type(observation.get("log_truncated")) is bool
            and "error_type" not in observation and "owned_process_cleanup_failed" not in observation
            and after.get("unclean_exit_false") is True),
        "no_session_search_or_diagnostics_files": all(after.get(key) is True for key in (
            "session_files_absent", "search_files_absent", "diagnostics_absent")),
        "native_binary_unchanged": observation.get("native_binary_sha256_before") == binary_hash
            and observation.get("native_binary_sha256_after") == binary_hash,
    }


def write_json(path: Path, value: dict) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    native.require(len(data) <= MAX_JSON, "Runtime observation exceeds bound")
    with path.open("xb") as stream:
        stream.write(data)


def validate_retained(evidence: Path, runtime: Path) -> dict:
    report_path = native.regular_file(evidence, REPORT)
    native.require(report_path.stat().st_size <= MAX_JSON, "Automatic-startup summary exceeds bound")
    report = native.load_json(report_path)
    native.require(type(report.get("schema_version")) is int and report["schema_version"] == 1
                   and report.get("passed") is True and report.get("release_eligible") is False
                   and report.get("broader_privacy_security_acceptance") is False,
                   "Missing passed bounded automatic-startup evidence")
    binary_hash = native.digest(native.regular_file(runtime, "privacybrowser.exe"))
    native.require(report.get("native_binary_sha256") == binary_hash, "Automatic-startup binary digest mismatch")
    records = report.get("records")
    names = {"observation": OBSERVATION, "browser_log": LOG, "test_control_preferences": USER_CONFIG}
    native.require(isinstance(records, dict) and set(records) == set(names), "Missing automatic-startup evidence records")
    paths = {}
    for role, name in names.items():
        item = records[role]
        native.require(isinstance(item, dict) and item.get("path") == name, "Unexpected automatic-startup evidence path")
        path = native.regular_file(evidence, name)
        native.require(0 < path.stat().st_size <= (MAX_LOG if role == "browser_log" else MAX_JSON)
                       and item.get("sha256") == native.digest(path), "Automatic-startup retained evidence mismatch")
        paths[role] = path
    observation = native.load_json(paths["observation"])
    checks = derive_checks(observation, paths["test_control_preferences"].read_bytes(), binary_hash)
    native.require(set(checks) == set(CHECKS) and all(value is True for value in checks.values()),
                   "Automatic-startup observations do not satisfy every required invariant")
    native.require(same_values(report.get("checks"), checks) and report.get("argv") == expected_argv(),
                   "Automatic-startup summary differs from raw observations")
    return report


class Marionette:
    def __init__(self, connection, deadline: float):
        self.sock, self.deadline, self.sequence = connection, deadline, 0
        greeting = self.read()
        native.require(greeting == {"applicationType": "gecko", "marionetteProtocol": 3}, "Unexpected Marionette greeting")

    def receive(self, size: int) -> bytes:
        remaining = min(15, self.deadline - time.monotonic())
        native.require(remaining > 0, "Runtime probe deadline exceeded")
        self.sock.settimeout(remaining)
        data = self.sock.recv(size)
        native.require(bool(data), "Marionette connection closed")
        return data

    def read(self):
        header = bytearray()
        while True:
            char = self.receive(1)
            if char == b":":
                break
            native.require(char in b"0123456789" and len(header) < 7, "Invalid Marionette frame header")
            header.extend(char)
        native.require(bool(header), "Missing Marionette frame size")
        length = int(header)
        native.require(0 < length <= MAX_MESSAGE, "Oversized Marionette frame")
        data = bytearray()
        while len(data) < length:
            data.extend(self.receive(length - len(data)))
        def unique(pairs):
            result = {}
            for key, value in pairs:
                native.require(key not in result, "Duplicate Marionette JSON key")
                result[key] = value
            return result
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique)

    def command(self, method: str, parameters: dict | None = None):
        self.sequence += 1
        data = json.dumps([0, self.sequence, method, parameters or {}], ensure_ascii=False).encode("utf-8")
        native.require(len(data) <= MAX_MESSAGE, "Oversized Marionette command")
        remaining = min(15, self.deadline - time.monotonic())
        native.require(remaining > 0, "Runtime probe deadline exceeded")
        self.sock.settimeout(remaining)
        self.sock.sendall(str(len(data)).encode("ascii") + b":" + data)
        response = self.read()
        native.require(isinstance(response, list) and len(response) == 4 and type(response[0]) is int
                       and response[0] == 1 and type(response[1]) is int and response[1] == self.sequence,
                       "Mismatched Marionette response")
        native.require(response[2] is None, "Marionette command failed: " + method)
        return response[3]

    def script(self, script: str, args: list | None = None):
        result = self.command("WebDriver:ExecuteScript", {
            "script": script, "args": args or [], "newSandbox": True,
            "sandbox": "privacybrowser-windows-autoconfig-observer",
        })
        native.require(isinstance(result, dict) and set(result) == {"value"}, "Unexpected script response")
        return result["value"]


STARTUP_SCRIPT = r'''
const modules = ["resource://privacybrowser/BrowserGlue.sys.mjs", "resource://privacybrowser/GeckoPrivacy.sys.mjs"];
const loaded = Object.fromEntries(modules.map(uri => [uri, Components.utils.isESModuleLoaded(uri)]));
if (!Object.values(loaded).every(value => value === true)) return {loaded_before_inspection: loaded};
const glue = ChromeUtils.importESModule(modules[0]);
const privacy = ChromeUtils.importESModule(modules[1]).PrivacyBrowserPrivacy;
const defaults = Services.prefs.getDefaultBranch("");
function read(branch, expected) {
  return Object.fromEntries(Object.entries(expected).map(([name, value]) => {
    try {
      return [name, typeof value === "boolean" ? branch.getBoolPref(name) :
        typeof value === "number" ? branch.getIntPref(name) : branch.getStringPref(name)];
    } catch { return [name, null]; }
  }));
}
const win = Services.wm.getMostRecentWindow("navigator:browser");
return {
  loaded_before_inspection: loaded, glue: glue.getStatus(), controller: privacy.controller?.snapshot() ?? null,
  default_prefs: read(defaults, arguments[0]), effective_prefs: read(Services.prefs, arguments[1]),
  recommended_preferences: Services.prefs.getBoolPref("remote.prefs.recommended", true),
  recommended_preferences_applied: Services.prefs.getBoolPref("remote.prefs.recommended.applied", false),
  build_automation_present: Services.env.exists("MOZ_AUTOMATION"),
  unclean_exit: Services.prefs.getBoolPref("privacybrowser.uncleanExit", false),
  window_attached: win?.__privacyBrowserAttached === true,
  delayed_startup_finished: win?.gBrowserInit?.delayedStartupFinished === true,
  search_control: !!win?.document.getElementById("privacybrowser-search-button"),
  menu_control: !!win?.document.getElementById("privacybrowser-menu-button"),
  newtab_url: ChromeUtils.importESModule("resource:///modules/AboutNewTab.sys.mjs").AboutNewTab.newTabURL,
  sandbox: {
    compiled: ChromeUtils.importESModule("resource://gre/modules/AppConstants.sys.mjs").AppConstants.MOZ_SANDBOX,
    default_level: defaults.getIntPref("security.sandbox.content.level", -1),
    effective_level: Services.prefs.getIntPref("security.sandbox.content.level", -1),
    e10s: Services.appinfo.browserTabsRemoteAutostart, fission: Services.appinfo.fissionAutostart,
    window_remote_subframes: win?.docShell?.QueryInterface(Ci.nsILoadContext).useRemoteSubframes === true,
  },
};
'''
OPEN_HOME_SCRIPT = r'''
const win = Services.wm.getMostRecentWindow("navigator:browser");
const tab = win.gBrowser.addTrustedTab("chrome://privacybrowser/content/home.html", {inBackground: false});
win.gBrowser.selectedTab = tab;
return true;
'''
HOME_SCRIPT = r'''
const win = Services.wm.getMostRecentWindow("navigator:browser");
const browser = win?.gBrowser?.selectedBrowser;
const doc = browser?.contentDocument;
return {
  uri: browser?.currentURI?.spec ?? "", module_ready: doc?.documentElement?.dataset.privacyBrowserReady === "true",
  system_principal: doc?.nodePrincipal?.isSystemPrincipal === true,
  search_input: !!doc?.getElementById("search-input"),
  history_label: doc?.getElementById("history-label")?.textContent ?? "",
  engine_name: doc?.getElementById("engine-name")?.textContent ?? "",
};
'''


def after_exit(profile: Path) -> dict:
    prefs = native.regular_file(profile, "prefs.js")
    native.require(prefs.stat().st_size <= 2 * 1024**2, "Oversized generated preference file")
    text = prefs.read_text(encoding="utf-8")
    dirty = re.findall(r'^user_pref\("privacybrowser\.uncleanExit", (true|false)\);$', text, re.M)
    session_names = ("sessionstore.jsonlz4", "sessionstore.js", "sessionstore.bak", "sessionstore.baklz4")
    backups = profile / "sessionstore-backups"
    def absent(path):
        return not path.exists() and not path.is_symlink()
    return {
        "unclean_exit_false": dirty == ["false"],
        "session_files_absent": all(absent(profile / name) for name in session_names)
            and (absent(backups) or (backups.is_dir() and not native.is_link_or_reparse(backups)
                                   and not any(backups.iterdir()))),
        "search_files_absent": all(absent(profile / name) for name in (
            "privacybrowser-search-history.json", "privacybrowser-search-history.json.tmp")),
        "diagnostics_absent": absent(profile / "privacybrowser-diagnostics"),
    }


def wait_for_active_port(profile: Path, port: int, process, deadline: float) -> int:
    """Allow Gecko's direct CREATE/write to expose an initially empty file."""
    active = profile / "MarionetteActivePort"
    while True:
        native.require(process.poll() is None and time.monotonic() < deadline,
                       "Automatic browser startup did not expose owned Marionette port")
        try:
            active.lstat()
        except FileNotFoundError:
            pass
        else:
            # lstat notices even dangling links. Never retry links, oversize
            # files or a nonempty malformed/wrong-port record.
            active = native.regular_file(profile, "MarionetteActivePort")
            native.require(active.stat().st_size <= 5, "Unexpected active port file")
            with active.open("rb") as stream:
                data = stream.read(6)
            if data:
                native.require(len(data) <= 5 and data.isdigit() and int(data) == port,
                               "Owned Marionette port differs from requested port")
                return int(data)
        time.sleep(.1)


def run(runtime: Path, profile: Path, evidence: Path) -> dict:
    """Return a passing report, or raise after retaining bounded failure evidence."""
    native.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
                   and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "Hosted Windows runtime probe only")
    native.require(PureWindowsPath(runtime) == RUNTIME and PureWindowsPath(profile) == PROFILE
                   and PureWindowsPath(evidence) == PureWindowsPath(r"D:\pb\evidence"), "Unexpected runtime probe paths")
    environment, controls = child_environment()
    native.require(not profile.exists() and not profile.is_symlink(), "Runtime test profile must be new")
    for name in (REPORT, OBSERVATION, LOG, USER_CONFIG):
        native.require(not (evidence / name).exists() and not (evidence / name).is_symlink(), "Runtime evidence must be new")
    binary = native.regular_file(runtime, "privacybrowser.exe")
    digest = native.digest(binary)
    defaults, effective = expected_preferences()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = user_config(port)
    profile.mkdir()
    with (profile / "user.js").open("xb") as stream:
        stream.write(config)
    with (evidence / USER_CONFIG).open("xb") as stream:
        stream.write(config)
    deadline = time.monotonic() + TOTAL_SECONDS
    argv = expected_argv()
    observation = {"schema_version": 1, "argv": argv, "host": "127.0.0.1", "port": port,
                   "native_binary_sha256_before": digest, "forced_process_cleanup": False,
                   "environment_controls": controls}
    process = connection = reader = None
    log_state = {"truncated": False, "error": False}
    with (evidence / LOG).open("xb") as log:
        log.write((json.dumps({"argv": argv}) + "\n").encode("utf-8"))
        used = log.tell()
        def drain():
            nonlocal used
            try:
                while block := process.stdout.read(8192):
                    retained = block[:max(0, MAX_LOG - used)]
                    log.write(retained)
                    used += len(retained)
                    log_state["truncated"] |= len(retained) != len(block)
            except (OSError, ValueError):
                log_state["error"] = True
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=environment)
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            observation["active_port"] = wait_for_active_port(
                profile, port, process, min(deadline, time.monotonic() + 45))
            connection = socket.create_connection(("127.0.0.1", port), timeout=5)
            client = Marionette(connection, deadline)
            session = client.command("WebDriver:NewSession", {
                "acceptInsecureCerts": False, "pageLoadStrategy": "none", "unhandledPromptBehavior": "ignore",
                "timeouts": {"script": 10000, "pageLoad": 10000, "implicit": 0},
            })
            native.require(isinstance(session, dict) and isinstance(session.get("capabilities"), dict), "Missing Marionette session capabilities")
            capabilities = session["capabilities"]
            observation["session"] = {key: capabilities.get(key) for key in (
                "acceptInsecureCerts", "pageLoadStrategy", "unhandledPromptBehavior", "timeouts",
                "moz:profile", "moz:processID")}
            native.require(derive_checks(observation, config, digest)["loopback_session_bound_to_owned_profile"], "Remote session is not bound to expected owned profile")
            client.command("Marionette:SetContext", {"value": "chrome"})
            wait_until = min(deadline - 45, time.monotonic() + 30)
            while True:
                observation["startup"] = client.script(STARTUP_SCRIPT, [defaults, effective])
                current = derive_checks(observation, config, digest)
                startup_keys = CHECKS[:10]
                if all(current[key] for key in startup_keys):
                    break
                native.require(time.monotonic() < wait_until, "Automatic startup invariants did not become ready")
                time.sleep(.15)
            native.require(client.script(OPEN_HOME_SCRIPT) is True, "Custom home did not open")
            wait_until = min(deadline - 30, time.monotonic() + 20)
            while True:
                observation["home"] = client.script(HOME_SCRIPT)
                if derive_checks(observation, config, digest)["custom_home_module_and_system_principal"]:
                    break
                native.require(time.monotonic() < wait_until, "Custom home did not execute as a trusted local document")
                time.sleep(.15)
            observation["quit"] = client.command("Marionette:Quit", {"flags": ["eAttemptQuit"], "safeMode": False})
            process.wait(timeout=max(.1, min(30, deadline - time.monotonic())))
            observation["exit_code"] = process.returncode
            observation["after_exit"] = after_exit(profile)
            native.require((profile / "user.js").read_bytes() == config, "Test-control profile preferences changed")
        except Exception as error:
            observation["error_type"] = type(error).__name__
        finally:
            if connection is not None:
                connection.close()
            if process is not None and process.poll() is None:
                observation["forced_process_cleanup"] = True
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
                    process.wait(timeout=10)
                except (OSError, subprocess.SubprocessError):
                    observation["owned_process_cleanup_failed"] = True
            if reader is not None:
                reader.join(timeout=10)
                if reader.is_alive():
                    log_state["error"] = True
    observation["log_truncated"] = log_state["truncated"]
    observation["log_capture_error"] = log_state["error"]
    observation["native_binary_sha256_after"] = native.digest(binary)
    checks = derive_checks(observation, config, digest)
    passed = all(value is True for value in checks.values()) and "error_type" not in observation and not log_state["error"]
    write_json(evidence / OBSERVATION, observation)
    report = {"schema_version": 1, "passed": passed, "native_binary_sha256": digest,
              "argv": argv, "checks": checks, "release_eligible": False,
              "broader_privacy_security_acceptance": False,
              "limitation": "Bounded fresh-profile automatic startup, local privileged home and clean-shutdown observations only; not complete privacy/security or interactive acceptance.",
              "records": {role: {"path": name, "sha256": native.digest(evidence / name)} for role, name in (
                  ("observation", OBSERVATION), ("browser_log", LOG), ("test_control_preferences", USER_CONFIG))}}
    write_json(evidence / REPORT, report)
    native.require(passed, "Automatic AutoConfig/runtime checks failed; inspect bounded retained observations")
    return validate_retained(evidence, runtime)
