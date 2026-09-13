"""Test Run may create a culprit-test/ branch and a pull request from it, and nothing else."""
import itertools

import pytest

from app import testrun
from apps import github, http
from runs import store

CFG = {"github": {"repo": "owizdom/picorv32-ci"}, "slack": {"channel": "C123"}, "linear": {"team_id": "t", "users": {}}}
REPO = "/repos/owizdom/picorv32-ci"
shas = itertools.count(1)


def test_guard_allows_only_culprit_test_branches():
    http.guard("github", "POST", f"{REPO}/git/blobs", "culprit-test/a01-1", CFG)
    http.guard("github", "POST", f"{REPO}/git/refs", "refs/heads/culprit-test/a01-1", CFG)
    http.guard("github", "POST", f"{REPO}/pulls", "culprit-test/a01-1", CFG)
    for op, target, key in [
        ("POST", f"{REPO}/git/refs", "refs/heads/main"),
        ("PATCH", f"{REPO}/git/refs/heads/main", "refs/heads/culprit-test/x"),
        ("POST", f"{REPO}/pulls", "feature"),
        ("POST", f"{REPO}/git/commits", "main"),
        ("POST", "/repos/someone/else/git/refs", "refs/heads/culprit-test/x"),
        ("PUT", f"{REPO}/pulls/2/merge", "culprit-test/x"),
    ]:
        with pytest.raises(http.UnsafeWrite):
            http.guard("github", op, target, key, CFG)


class Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        return self


class FakeGit:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append(("GET", url))
        if url.endswith("/git/ref/heads/main"):
            return Response({"object": {"sha": "main-sha"}})
        return Response({"tree": {"sha": "main-tree"}})

    def post(self, url, json=None):
        self.calls.append(("POST", url))
        if url.endswith("/pulls"):
            self.pr = json
            return Response({"number": 3, "html_url": "https://github.com/owizdom/picorv32-ci/pull/3"})
        return Response({"sha": f"sha-{next(shas)}"})


def test_opening_a_test_pull_request_builds_a_branch_on_main(monkeypatch, tmp_path):
    fake = FakeGit()
    monkeypatch.setattr(http, "config", lambda: CFG)
    monkeypatch.setattr(http, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(github, "client", lambda: fake)
    out = github.open_test_pull_request({"picorv32.v": "module x; endmodule\n"}, "decoder: tidy", "body", "culprit-test/a01-1")
    assert [c[1].rsplit("/git/", 1)[-1] if "/git/" in c[1] else c[1].rsplit("/", 1)[-1] for c in fake.calls] == [
        "ref/heads/main", "commits/main-sha", "blobs", "trees", "commits", "refs", "pulls"]
    assert fake.pr == {"title": "decoder: tidy", "head": "culprit-test/a01-1", "base": "main", "body": "body"}
    assert out["pr"] == 3
    with pytest.raises(http.UnsafeWrite):
        github.open_test_pull_request({}, "t", "b", "main")


def test_case_choice_rotates_and_every_pool_case_is_an_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RUNS", tmp_path)
    assert testrun.order({})[0] == testrun.POOL[0]
    assert testrun.order({"used": [testrun.POOL[0]]})[0] == testrun.POOL[1]
    assert testrun.order({"used": list(testrun.POOL)})[0] == testrun.POOL[0]
    for case in testrun.POOL:
        details = testrun.case_details(case)
        assert details["title"] and details["edits"]


def test_edits_apply_to_main_as_it_is_now_or_not_at_all(monkeypatch):
    monkeypatch.setattr(testrun, "case_details", lambda case: {"edits": [{"path": "cpu.v", "find": "a <= b;", "replace": "a <= c;"}]})
    assert testrun.case_changes("x", lambda path: "x;\na <= b;\n") == {"cpu.v": "x;\na <= c;\n"}
    assert testrun.case_changes("x", lambda path: "a <= c;\n") is None           # main already changed that line
    assert testrun.case_changes("x", lambda path: "a <= b;\na <= b;\n") is None  # ambiguous


def test_start_skips_a_bug_that_no_longer_applies_to_main(monkeypatch, tmp_path):
    cases = {"old": {"title": "old", "body": "b", "edits": [{"path": "cpu.v", "find": "merged away", "replace": "bug"}]},
             "new": {"title": "new", "body": "b", "edits": [{"path": "cpu.v", "find": "still here", "replace": "bug"}]}}
    opened = {}
    monkeypatch.setattr(store, "RUNS", tmp_path)
    monkeypatch.setattr(testrun, "POOL", ["old", "new"])
    monkeypatch.setattr(testrun, "case_details", cases.__getitem__)
    monkeypatch.setattr(http, "has_token", lambda app: True)
    monkeypatch.setattr(github, "file_at_main", lambda path: "module cpu; still here; endmodule\n")
    monkeypatch.setattr(github, "latest_run_for", lambda sha: None)

    def fake_open(files, title, body, branch):
        opened.update(files=files, title=title, branch=branch)
        return {"pr": 4, "url": "https://example.test/pull/4", "sha": "d" * 40, "branch": branch}

    monkeypatch.setattr(github, "open_test_pull_request", fake_open)
    out = testrun.start()
    assert out["case"] == "new" and opened["files"] == {"cpu.v": "module cpu; bug; endmodule\n"}
    assert opened["branch"].startswith("culprit-test/new-")
    assert testrun.read_state()["used"] == ["new"]
