"""The files an investigation writes, which the CULPRIT window reads.

runs/<pr>@<sha12>/meta.json, events.jsonl, blame.json, patch.json, path.json, apps.json.
Every JSON file is written to a temp name and renamed, so the window never reads half a file.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from paths import HOME

RUNS = HOME / "runs"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


class Run:
    def __init__(self, pr, sha):
        self.id = f"{pr}@{sha[:12]}"
        self.dir = RUNS / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / "meta.json").exists():
            write_json(self.dir / "meta.json", {"id": self.id, "pr": pr, "sha": sha, "status": "investigating",
                                                "started": now(), "finished": None, "sims": 0})

    def meta(self, **fields):
        path = self.dir / "meta.json"
        data = json.loads(path.read_text())
        data.update(fields)
        write_json(path, data)
        return data

    def event(self, step, state, text, trust=None, sims=0, data=None):
        line = {"t": now(), "step": step, "state": state, "trust": trust, "text": text, "sims": sims,
                "data": data or {}}
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(line, default=str) + "\n")
        return line

    def write(self, name, obj):
        write_json(self.dir / name, obj)

    def read(self, name, default=None):
        path = self.dir / name
        return json.loads(path.read_text()) if path.exists() else default
