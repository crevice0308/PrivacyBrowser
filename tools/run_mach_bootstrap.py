#!/usr/bin/env python3
"""Expose otherwise swallowed configure errors from the pinned mach bootstrap.

This changes logging only. The verified source's mach entry point, bootstrap
arguments, exception handling and exit status remain authoritative.
"""
import json
import logging
import os
from pathlib import Path
import runpy
import stat
import sys


BOOTSTRAP_ARGS = ["--no-interactive", "bootstrap", "--application-choice", "browser",
                  "--no-system-changes"]
MAX_ERRORS = 16
MAX_MESSAGE_CHARS = 2048


class ConfigureErrors(logging.Handler):
    def __init__(self, stream):
        super().__init__(logging.ERROR)
        self.stream = stream
        self.emitted = 0

    def emit(self, record):
        if self.emitted >= MAX_ERRORS:
            return
        self.emitted += 1
        try:
            message = record.getMessage()
            print(json.dumps({"phase": "bootstrap-configure", "level": record.levelname,
                              "message": message[:MAX_MESSAGE_CHARS],
                              "truncated": len(message) > MAX_MESSAGE_CHARS,
                              "diagnostic_limit_reached": self.emitted == MAX_ERRORS},
                             ensure_ascii=True), file=self.stream, flush=True)
        except Exception:
            # Diagnostics must never replace mach's original failure/exit.
            # Do not catch KeyboardInterrupt or SystemExit (BaseException).
            return


def require_source():
    if (os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("Bootstrap diagnostics require the ephemeral hosted Windows runner")
    if os.path.normcase(os.getcwd()) != os.path.normcase(r"D:\pb\source"):
        raise ValueError("Bootstrap must use the verified D:\\pb\\source working directory")
    source = Path.cwd()
    mach = source / "mach"
    for path in (source, mach):
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Bootstrap source and mach must not be reparse entries")
    if not stat.S_ISREG(mach.stat().st_mode):
        raise ValueError("Verified mach must be a regular source file")


def main():
    if len(sys.argv) != 1:
        raise ValueError("No bootstrap argument overrides are supported")
    require_source()
    logger = logging.getLogger("moz.configure")
    handler = ConfigureErrors(sys.stderr)
    logger.addHandler(handler)
    old_argv, old_path = sys.argv, sys.path[:]
    try:
        sys.argv = ["./mach", *BOOTSTRAP_ARGS]
        # Match direct script execution before mach creates its own virtualenv.
        sys.path[0] = str(Path.cwd())
        runpy.run_path("./mach", run_name="__main__")
    finally:
        logger.removeHandler(handler)
        sys.argv = old_argv
        sys.path[:] = old_path


if __name__ == "__main__":
    main()
