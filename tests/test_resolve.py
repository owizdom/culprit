"""When CI goes green, the pull request's newest investigation shows the Resolve step and status."""
import json

import culprit
from apps import act, github, poll
from runs import store


def test_resolve_marks_only_the_newest_run_of_that_pull_request(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RUNS", tmp_path)
    old = store.Run(7, "a" * 40)
    old.meta(started="2026-09-13T01:00:00+00:00")
    new = store.Run(7, "b" * 40)
    new.meta(started="2026-09-13T02:00:00+00:00")
    new.write("apps.json", {"slack": {"ts": "1.2"}})
    other = store.Run(77, "c" * 40)
    seen = {}

    def fake_resolve(info, sha):
        seen["resolve"] = (info["number"], sha)
        return {"linear_closed": True}

    def fake_snapshot(pr, sha, slack_ts=None):
        seen["snapshot"] = (pr, sha, slack_ts)
        return {"linear": {"state": "Done"}}

    monkeypatch.setattr(github, "pull", lambda n: {"number": n, "url": f"https://example.test/pull/{n}"})
    monkeypatch.setattr(github, "statuses", lambda sha: [{"state": "success" if sha == "d" * 40 else "failure"}])
    monkeypatch.setattr(act, "resolve", fake_resolve)
    monkeypatch.setattr(poll, "snapshot", fake_snapshot)

    culprit.cmd_resolve("7", "d" * 40)

    assert seen["resolve"] == (7, "d" * 40)
    assert seen["snapshot"] == (7, "b" * 40, "1.2")
    assert new.read("meta.json")["status"] == "resolved"
    events = [json.loads(line) for line in (new.dir / "events.jsonl").read_text().splitlines()]
    assert [(e["step"], e["state"]) for e in events] == [("resolve", "start"), ("resolve", "done")]
    assert new.read("apps.json")["slack"]["ts"] == "1.2"
    assert new.read("apps.json")["github"]["status"] == "success"
    assert not (old.dir / "events.jsonl").exists()
    assert other.read("meta.json")["status"] == "investigating"
