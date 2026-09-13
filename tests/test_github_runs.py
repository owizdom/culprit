"""A run whose pull request was merged before CI finished still belongs to that pull request."""
import httpx

from apps import github


def test_merged_pull_request_is_found_from_the_commit(monkeypatch):
    lookups = []

    def handler(request):
        if request.url.path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": [
                {"id": 1, "head_sha": "d" * 40, "head_branch": "decoder-imm-comments", "conclusion": "success",
                 "pull_requests": [], "html_url": "u1"},
                {"id": 2, "head_sha": "e" * 40, "head_branch": "tidy", "conclusion": "failure",
                 "pull_requests": [{"number": 1}], "html_url": "u2"},
            ]}, headers={"ETag": "t"})
        lookups.append(request.url.path)
        return httpx.Response(200, json=[{"number": 9, "head": {"ref": "other"}},
                                         {"number": 2, "head": {"ref": "decoder-imm-comments"}}])

    monkeypatch.setattr(github, "client", lambda: httpx.Client(base_url=github.API, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(github, "repo", lambda: "o/r")
    monkeypatch.setattr(github, "PR_BY_COMMIT", {})
    runs, _ = github.completed_runs()
    github.completed_runs()
    assert [r["pr"] for r in runs] == [2, 1]
    assert lookups == [f"/repos/o/r/commits/{'d' * 40}/pulls"]
