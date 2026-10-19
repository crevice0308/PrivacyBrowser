"""Fail-closed native SessionFile patch for verified source builds only."""
from pathlib import Path

MARKER = "// Privacy Browser native session persistence guard v0.1.0"
READ_ANCHOR = "  async read() {\n    // Load session files with lz4 compression."
WRITE_ANCHOR = "  write(aData) {\n    if (lazy.RunState.isClosed) {"

READ_PATCH = '''  async read() {
    // Privacy Browser native session persistence guard v0.1.0
    if (!Services.prefs.getBoolPref("privacybrowser.history.sessions", false)) {
      this._readOrigin = "empty";
      this._usingOldExtension = false;
      await IOUtils.remove(this.Paths.clean, { ignoreAbsent: true });
      await IOUtils.remove(PathUtils.join(profileDir, "sessionstore.js"), {
        ignoreAbsent: true,
      });
      await IOUtils.remove(this.Paths.backups, {
        recursive: true,
        ignoreAbsent: true,
      });
      return {
        origin: "empty",
        source: "",
        parsed: null,
        useOldExtension: false,
        noFilesFound: true,
      };
    }
    // Load session files with lz4 compression.'''

WRITE_PATCH = '''  write(aData) {
    // Privacy Browser native session persistence guard v0.1.0
    if (!Services.prefs.getBoolPref("privacybrowser.history.sessions", false)) {
      const privacyBrowserFinalWrite = lazy.RunState.isClosing;
      if (privacyBrowserFinalWrite) {
        lazy.RunState.setClosed();
      }
      return Promise.resolve().then(() => {
        if (privacyBrowserFinalWrite) {
          Services.obs.notifyObservers(
            null,
            "sessionstore-final-state-write-complete"
          );
        }
      });
    }
    if (lazy.RunState.isClosed) {'''


def patch_session_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        raise ValueError("Native session guard already applied; use a clean source tree")
    if text.count(READ_ANCHOR) != 1 or text.count(WRITE_ANCHOR) != 1:
        raise ValueError("ESR SessionFile changed: manual source patch review required")
    text = text.replace(READ_ANCHOR, READ_PATCH).replace(WRITE_ANCHOR, WRITE_PATCH)
    path.write_text(text, encoding="utf-8")
