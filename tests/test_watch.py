"""The watcher acts once on the newest run of each pull request and never redoes finished work."""
import json

import pytest

import culprit
from apps import github, http
from runs import store


def run(pr, sha, conclusion, run_id):
    return {"id": run_id, "pr": pr, "sha": sha * 40, "conclusion": conclusion}


@pytest.fixture
def calls(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RUNS", tmp_path)
    seen = {"run": [], "resolve": []}
    monkeypatch.setattr(culprit, "cmd_run", lambda pr: seen["run"].append(pr))
    monkeypatch.setattr(culprit, "cmd_resolve", lambda pr, sha: seen["resolve"].append((pr, sha)))
    return seen


def test_newest_run_per_pull_request_and_no_repeats(monkeypatch, calls):
    investigated = store.Run(1, "a" * 40)
    investigated.meta(status="fixed", finished="2026-09-13T05:00:00+00:00")
    runs = [run(1, "b", "success", 30), run(1, "a", "failure", 20),   # PR 1: green after red
            run(2, "c", "failure", 25),                              # PR 2: new failure
            run(3, "d", "success", 24)]                              # PR 3: never investigated
    monkeypatch.setattr(github, "completed_runs", lambda etag=None: (runs, "etag"))
    seen = set()
    culprit.watch_once(seen)
    culprit.watch_once(seen)
    assert calls["resolve"] == [(1, "b" * 40)]
    assert calls["run"] == [2]


def test_already_investigated_or_resolved_is_left_alone(monkeypatch, calls):
    store.Run(4, "e" * 40).meta(status="fixed", finished="2026-09-13T05:00:00+00:00")
    store.Run(5, "f" * 40).meta(status="resolved", finished="2026-09-13T05:00:00+00:00")
    runs = [run(4, "e", "failure", 40), run(5, "g", "success", 41)]
    monkeypatch.setattr(github, "completed_runs", lambda etag=None: (runs, None))
    culprit.watch_once(set())
    assert calls == {"run": [], "resolve": []}


def test_a_failed_action_is_retried_until_it_works(monkeypatch, calls):
    runs = [run(9, "h", "failure", 90)]
    monkeypatch.setattr(github, "completed_runs", lambda etag=None: (runs, None))
    outcomes = iter([RuntimeError("slack gave an empty page"), None])

    def flaky_run(pr):
        calls["run"].append(pr)
        outcome = next(outcomes)
        if outcome:
            raise outcome

    monkeypatch.setattr(culprit, "cmd_run", flaky_run)
    seen, failures, errors = set(), {}, []
    culprit.watch_once(seen, None, failures, errors)
    assert "will retry" in errors[0] and (90, "failure") not in seen
    culprit.watch_once(seen, None, failures, [])
    culprit.watch_once(seen, None, failures, [])
    assert calls["run"] == [9, 9] and (90, "failure") in seen and failures == {}


def test_a_failing_action_is_given_up_after_three_tries(monkeypatch, calls):
    runs = [run(8, "i", "failure", 80)]
    monkeypatch.setattr(github, "completed_runs", lambda etag=None: (runs, None))

    def broken(pr):
        calls["run"].append(pr)
        raise RuntimeError("down")

    monkeypatch.setattr(culprit, "cmd_run", broken)
    seen, failures = set(), {}
    for _ in range(5):
        culprit.watch_once(seen, None, failures, [])
    assert calls["run"] == [8, 8, 8] and (80, "failure") in seen


def stop(seconds):
    raise StopIteration


def test_signed_out_watcher_waits_quietly(monkeypatch, calls, tmp_path):
    def never(etag=None):
        raise AssertionError("asked GitHub while signed out")

    monkeypatch.setattr(http, "ENV", tmp_path / ".env")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(github, "completed_runs", never)
    monkeypatch.setattr(culprit.time, "sleep", stop)
    with pytest.raises(StopIteration):
        culprit.cmd_watch()
    state = json.loads((tmp_path / "watcher.json").read_text())
    assert state["state"] == "signed_out" and state["error"] is None


def test_an_error_is_recorded_and_the_loop_keeps_going(monkeypatch, calls, tmp_path):
    def broken(etag=None):
        raise RuntimeError("bad credentials")

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(github, "completed_runs", broken)
    monkeypatch.setattr(culprit.time, "sleep", stop)
    with pytest.raises(StopIteration):
        culprit.cmd_watch()
    state = json.loads((tmp_path / "watcher.json").read_text())
    assert state["state"] == "error" and "bad credentials" in state["error"]
