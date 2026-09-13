"""Every write CULPRIT makes to GitHub, Linear or Slack goes through here.

A write is refused before any network call unless it is one of the few things CULPRIT is allowed to
do: comment on and set a status for a pull request in the configured repo, create or update its own
Linear issue in the configured team, or post in the configured Slack channel. It never pushes,
merges or approves. Every allowed write is appended to a ledger.
"""
import json
import os
import re
import threading
from datetime import datetime, timezone

import yaml

from paths import HOME, ROOT

LEDGER = HOME / "runs" / "ledger.jsonl"
ENV = HOME / ".env"
CONFIG = ROOT / "config.yaml"
LOCAL_CONFIG = HOME / "config.local.yaml"
_lock = threading.Lock()


class UnsafeWrite(Exception):
    pass


def load_env():
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


TOKENS = {"github": "GITHUB_TOKEN", "linear": "LINEAR_API_KEY", "slack": "SLACK_BOT_TOKEN"}


def has_token(app):
    load_env()
    return bool(os.environ.get(TOKENS[app]))


def read_yaml(path):
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def merged(base, override):
    out = dict(base)
    for key, value in override.items():
        out[key] = merged(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def config():
    """config.yaml (public defaults) with config.local.yaml (written by the app's setup, gitignored) on top."""
    return merged(read_yaml(CONFIG), read_yaml(LOCAL_CONFIG))


def allowed(app, op, target, cfg, key=None):
    repo = re.escape(cfg["github"]["repo"])
    if app == "github":
        if any(re.fullmatch(p, f"{op} {target}") for p in (
            rf"POST /repos/{repo}/pulls/\d+/comments",
            rf"PATCH /repos/{repo}/pulls/comments/\d+",
            rf"POST /repos/{repo}/statuses/[0-9a-f]{{40}}",
        )):
            return True
        # Test Run is the one path that creates a branch and a pull request, and only a culprit-test/ one.
        # The investigation itself never pushes, merges or approves.
        key = key or ""
        return op == "POST" and bool(
            re.fullmatch(rf"/repos/{repo}/git/(blobs|trees|commits)", target) and key.startswith("culprit-test/")
            or re.fullmatch(rf"/repos/{repo}/git/refs", target) and key.startswith("refs/heads/culprit-test/")
            or re.fullmatch(rf"/repos/{repo}/pulls", target) and key.startswith("culprit-test/"))
    if app == "linear":
        return op in ("issueCreate", "commentCreate", "issueUpdate") and target.startswith("[culprit")
    if app == "slack":
        return op in ("chat.postMessage", "chat.update") and target == cfg["slack"]["channel"]
    return False


def record(app, op, target, key, response_id):
    line = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "app": app, "op": op,
            "target": target, "key": key, "id": response_id}
    with _lock:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(line) + "\n")


def guard(app, op, target, key, cfg=None):
    cfg = cfg or config()
    if not allowed(app, op, target, cfg, key):
        raise UnsafeWrite(f"refused {app} {op} {target}")
