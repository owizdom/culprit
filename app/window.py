"""CULPRIT desktop window: pywebview around the built web/ UI.

Build first (cd web && npm install && npm run build), then: uv run python culprit.py app
For UI work: cd web && npm run dev, then: uv run python culprit.py app --dev
"""

import json
import socket
import threading
import time
import webbrowser
from pathlib import Path

import webview

from app import connect, testrun, tools
from paths import ROOT
from runs import store

INDEX = ROOT / "web" / "dist" / "index.html"
ICON = ROOT / "brand" / "png" / "icon-1024.png"
DEV_URL, DEV_PORT = "http://localhost:5173", 5173
WATCHED = ("meta.json", "path.json", "apps.json", "blame.json", "patch.json")
APPS = ("github", "linear", "slack")


def read_json(path: Path):
    """Parsed JSON at path, or None when missing or malformed."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def parse_lines(raw) -> list:
    """Well-formed JSON values from newline-separated text or bytes."""
    out = []
    for line in raw.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def run_folders() -> list[Path]:
    store.RUNS.mkdir(parents=True, exist_ok=True)
    return [d for d in store.RUNS.iterdir() if (d / "meta.json").is_file()]


def run_folder(run_id) -> Path | None:
    """Folder for one run id, refusing ids that could escape the runs dir."""
    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    folder = store.RUNS / run_id
    return folder if (folder / "meta.json").is_file() else None


class Api:
    """Methods the UI calls as window.pywebview.api.<name>()."""

    def list_runs(self):
        metas = []
        for folder in run_folders():
            meta = read_json(folder / "meta.json")
            if isinstance(meta, dict):
                metas.append({"id": folder.name, **meta})
        return sorted(metas, key=lambda m: m.get("started") or "", reverse=True)

    def get_run(self, run_id):
        folder = run_folder(run_id)
        meta = read_json(folder / "meta.json") if folder else None
        if not isinstance(meta, dict):
            return None
        events = folder / "events.jsonl"
        return {
            "meta": {"id": folder.name, **meta},
            "events": parse_lines(events.read_text()) if events.is_file() else [],
            "blame": read_json(folder / "blame.json"),
            "patch": read_json(folder / "patch.json"),
            "apps": read_json(folder / "apps.json"),
        }

    def get_path(self, run_id):
        folder = run_folder(run_id)
        return read_json(folder / "path.json") if folder else None

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return webbrowser.open(url)
        return False

    # Connecting apps

    def connections(self, force=False):
        return connect.connections(bool(force))

    def github_device_start(self):
        return connect.device_start()

    def github_device_poll(self):
        return connect.device_poll()

    def github_use_cli(self):
        return connect.use_cli()

    def save_token(self, app, token):
        return connect.save_token(app, token) if app in APPS else connect.check(error="Unknown app.")

    def disconnect(self, app):
        return connect.disconnect(app) if app in APPS else connect.check(error="Unknown app.")

    def logout(self):
        return connect.logout()

    def save_anthropic(self, key):
        return connect.save_anthropic(key)

    def tools(self):
        return tools.check()

    def build_toolchain(self):
        return tools.build_toolchain()

    def list_repos(self):
        return connect.list_repos()

    def check_repo(self, full_name):
        return connect.check_repo(full_name)

    def linear_teams(self):
        return connect.linear_teams()["teams"]

    def slack_channel(self, text):
        return connect.slack_channel(text)

    def save_setup(self, choice):
        choice = choice if isinstance(choice, dict) else {}
        try:
            return {"ok": True, "setup": connect.save_setup(
                repo=choice.get("repo"), team_id=choice.get("team_id"),
                channel=choice.get("channel"), channel_name=choice.get("channel_name"))}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    # Test Run: a real pull request with a seeded bug, handled end to end

    def start_test_run(self):
        return testrun.start()

    def test_run_status(self):
        return testrun.status()


def push(window, payload: dict) -> None:
    """Hand one payload to window.culprit.push in the page."""
    try:
        window.evaluate_js("window.culprit && window.culprit.push(" + json.dumps(payload) + ")")
    except Exception:  # page not loaded yet, or the window is closing
        pass


def watch(window) -> None:
    """Every 0.5 s: push new events.jsonl lines and changed run files to the UI."""
    offsets: dict[Path, int] = {}
    mtimes: dict[Path, float] = {}
    primed = False
    while True:
        try:
            for folder in run_folders():
                log = folder / "events.jsonl"
                if log.is_file():
                    size = log.stat().st_size
                    start = offsets.get(log, 0 if primed else size)
                    start = 0 if size < start else start
                    if size > start:
                        with log.open("rb") as fh:
                            fh.seek(start)
                            chunk = fh.read(size - start)
                        complete = chunk.rfind(b"\n") + 1  # hold back a half-written last line
                        for event in parse_lines(chunk[:complete]):
                            push(window, {"type": "event", "id": folder.name, "event": event})
                        start += complete
                    offsets[log] = start
                changed = False
                for f in (folder / name for name in WATCHED):
                    if f.is_file() and mtimes.get(f) != (mtime := f.stat().st_mtime):
                        changed = changed or primed
                        mtimes[f] = mtime
                if changed:
                    push(window, {"type": "run_updated", "id": folder.name})
            primed = True
        except OSError:
            pass
        time.sleep(0.5)


def wait_for_port(port, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def main(dev=False) -> None:
    if dev:
        if not wait_for_port(DEV_PORT):
            raise SystemExit("Vite dev server is not running: cd web && npm run dev")
        url = DEV_URL
    elif INDEX.is_file():
        url = str(INDEX)
    else:
        raise SystemExit(f"UI not built: run `npm install && npm run build` in {INDEX.parent.parent}")
    window = webview.create_window("CULPRIT", url=url, js_api=Api(), width=1440, height=900,
                                  min_size=(1180, 760), background_color="#faf8f3")
    threading.Thread(target=watch, args=(window,), daemon=True).start()
    webview.start(debug=dev, icon=str(ICON) if ICON.is_file() else None)


if __name__ == "__main__":
    main()
