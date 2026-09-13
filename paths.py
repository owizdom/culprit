"""Where CULPRIT reads the files it ships with, and where it writes its own.

ROOT holds the code and bundled files (web/dist, corpus/cases.yaml, config.yaml, sim/fw-seed). HOME holds what
CULPRIT writes: .env, config.local.yaml, runs/ and .cache/. A source checkout keeps both in the repository; an
installed `culprit` command writes to the user's data folder (macOS Application Support, Windows %APPDATA%,
Linux ~/.local/share). CULPRIT_HOME overrides both.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _home():
    if os.environ.get("CULPRIT_HOME"):
        return Path(os.environ["CULPRIT_HOME"])
    if (ROOT / "pyproject.toml").exists():
        return ROOT
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CULPRIT"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "CULPRIT"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "culprit"


HOME = _home()
if HOME != ROOT:
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
