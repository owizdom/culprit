"""Publishing the same failure twice must not create anything the second time, and nothing outside the
allow-list may be written. GitHub, Linear and Slack are replaced by small in-memory fakes."""
import itertools
import json

import pytest

from apps import act, github, http, linear, slack
from localize.confirm import Blame, Fact
from localize.hunks import Hunk

CFG = {"github": {"repo": "owizdom/picorv32-ci"}, "slack": {"channel": "C123"},
       "linear": {"team_id": "team", "done_state_id": "done", "users": {"owizdom": "user-1"}}}
SHA = "a" * 40
ids = itertools.count(1)


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.headers, self.text = data, status, {}, ""

    def json(self):
        return self._data

    def raise_for_status(self):
        return self


class FakeGitHub:
    def __init__(self):
        self.comments, self.statuses, self.posts = [], [], 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None):
        if url.endswith("/comments"):
            return FakeResponse(list(self.comments))
        if url.endswith("/statuses"):
            return FakeResponse(list(self.statuses))
        raise AssertionError(url)

    def post(self, url, json=None):
        self.posts += 1
        if "/statuses/" in url:
            self.statuses.insert(0, {"context": json["context"], "state": json["state"],
                                     "description": json["description"], "id": next(ids)})
            return FakeResponse(self.statuses[0])
        comment = {"id": next(ids), "body": json["body"], "line": json.get("line"),
                   "html_url": f"https://github.com/x/pull/1#c{len(self.comments)}"}
        self.comments.append(comment)
        return FakeResponse(comment)

    def patch(self, url, json=None):
        comment = next(c for c in self.comments if url.endswith(str(c["id"])))
        comment["body"] = json["body"]
        return FakeResponse(comment)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    gh = FakeGitHub()
    issues, comments = [], []
    messages = [{"ts": "1", "text": "<@U1> has joined the channel", "metadata": None}]   # a real channel is never empty
    flaky = {"empty": 0}
    for var in ("GITHUB_TOKEN", "LINEAR_API_KEY", "SLACK_BOT_TOKEN"):
        monkeypatch.setenv(var, "test")
    monkeypatch.setattr(http, "config", lambda: CFG)
    monkeypatch.setattr(http, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(github, "client", lambda: gh)

    def gql(query, variables=None):
        if "issues(filter" in query:
            m = variables["m"]
            return {"issues": {"nodes": [i for i in issues if m in i["title"]]}}
        if "issueCreate" in query:
            i = dict(variables["i"], id=f"i{next(ids)}", identifier=f"DV-{len(issues) + 1}", url="https://linear.app/x",
                     state={"name": "Todo", "type": "unstarted"}, assignee={"name": "Wisdom"})
            issues.append(i)
            return {"issueCreate": {"issue": i}}
        if "issue(id" in query:
            return {"issue": {"comments": {"nodes": [c for c in comments if c["issueId"] == variables["id"]]}}}
        if "commentCreate" in query:
            comments.append(variables["i"])
            return {"commentCreate": {"success": True}}
        if "issueUpdate" in query:
            next(i for i in issues if i["id"] == variables["id"])["state"] = {"name": "Done", "type": "completed"}
            return {"issueUpdate": {"success": True}}
        raise AssertionError(query)

    monkeypatch.setattr(linear, "gql", gql)
    updates = []

    class FakeSlack:
        def conversations_history(self, **kw):
            if flaky["empty"]:
                flaky["empty"] -= 1
                return {"messages": []}
            return {"messages": [m for m in messages if "thread_ts" not in m]}

        def conversations_replies(self, **kw):
            # Like Slack, a thread starts with its parent message.
            return {"messages": [m for m in messages if m["ts"] == kw["ts"]] + [m for m in messages if m.get("thread_ts") == kw["ts"]]}

        def chat_postMessage(self, **kw):
            m = {"ts": str(next(ids)), "text": kw["text"], "metadata": kw.get("metadata"), "blocks": kw.get("blocks")}
            if kw.get("thread_ts"):
                m["thread_ts"] = kw["thread_ts"]
            messages.append(m)
            return m

        def chat_update(self, **kw):
            m = next(m for m in messages if m["ts"] == kw["ts"])
            m.update(text=kw["text"], blocks=kw.get("blocks"), metadata=kw.get("metadata", m["metadata"]))
            updates.append(kw["ts"])
            return m

        def chat_getPermalink(self, **kw):
            return {"permalink": f"https://slack.test/archives/C123/p{kw['message_ts']}"}

    monkeypatch.setattr(slack, "client", lambda: FakeSlack())
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    return {"github": gh, "issues": issues, "comments": comments, "messages": messages, "updates": updates, "flaky": flaky}


def blame():
    h = Hunk(1, "picorv32.v", 1344, ("\t\t\tcpuregs[latched_rd] <= cpuregs_wrdata;\n",), 1344,
             ("\t\t\tcpuregs[latched_rd ^ 1] <= cpuregs_wrdata;\n",))
    b = Blame(kind="rtl", hunks=[h], culprit=[frozenset({1})], sims=3,
              line_fix={"line": 1344, "before": h.new[0], "replacement": h.old[0], "method": "restore_line"})
    b.facts = [Fact("confirmed", "head fails; merge base passes"),
               Fact("confirmed", "undoing edit [1] alone makes the regression pass", "picorv32.v", 1344)]
    return b


PR = {"number": 7, "head_sha": SHA, "title": "Tidy regfile write", "url": "https://github.com/x/pull/7", "author": "owizdom"}
PATCH = {"trust": "confirmed", "start": 1344, "end": 1344, "replacement": "\t\t\tcpuregs[latched_rd] <= cpuregs_wrdata;\n",
         "explanation": "the destination index was XORed with 1"}


def test_second_publish_creates_nothing(fakes):
    first = act.publish(PR, blame(), PATCH)
    second = act.publish(PR, blame(), PATCH)
    assert first["created"] == 4
    assert second["created"] == 0
    assert len(fakes["github"].comments) == 1
    assert len(fakes["issues"]) == 1
    assert len(parents(fakes)) == 1


def test_comment_carries_a_validated_suggestion_and_tof(fakes):
    act.publish(PR, blame(), PATCH)
    body = fakes["github"].comments[0]["body"]
    assert "```suggestion" in body
    assert "finding CULPRIT-001 confirmed" in body


def test_resolve_closes_the_issue_once(fakes):
    act.publish(PR, blame(), PATCH)
    assert act.resolve(PR, "b" * 40)["linear_closed"] is True
    assert act.resolve(PR, "b" * 40)["linear_closed"] is False


def parents(fakes):
    return [m for m in fakes["messages"] if "thread_ts" not in m and ((m.get("metadata") or {}).get("event_payload") or {}).get("key") == "7"]


def parent(fakes):
    return parents(fakes)[0]


def test_slack_parent_shows_the_status_and_links_the_linear_issue(fakes):
    act.publish(PR, blame(), PATCH)
    shown = " ".join(f["text"] for f in parent(fakes)["blocks"][1]["fields"])
    assert "Fixed" in shown and "picorv32.v:1344" in shown
    assert "https://linear.app/x" in parent(fakes)["text"]


def test_replaying_a_publish_redraws_nothing(fakes):
    act.publish(PR, blame(), PATCH)
    act.publish(PR, blame(), PATCH)
    assert fakes["updates"] == []


def test_resolve_redraws_the_parent_once_and_keeps_the_linear_link(fakes):
    act.publish(PR, blame(), PATCH)
    act.resolve(PR, "b" * 40)
    act.resolve(PR, "b" * 40)
    assert len(fakes["updates"]) == 1
    assert "Resolved" in parent(fakes)["text"] and "https://linear.app/x" in parent(fakes)["text"]
    replies = [m for m in fakes["messages"] if m.get("thread_ts")]
    assert sum(m["text"].startswith("Resolved") for m in replies) == 1
    assert sum("Resolved" in c["body"] for c in fakes["comments"]) == 1


def test_a_pull_request_title_cannot_ping_the_channel(fakes):
    act.publish(dict(PR, title="<!channel> tidy"), blame(), PATCH)
    p = parent(fakes)
    assert "<!channel>" not in p["text"]
    assert "<!channel>" not in json.dumps(p["blocks"][1:])


def test_linear_issue_reads_as_a_report_and_links_the_review_comment(fakes):
    act.publish(PR, blame(), PATCH)
    issue = fakes["issues"][0]
    assert issue["title"] == '[culprit #7] picorv32.v:1344 breaks the tests in "Tidy regfile write"'
    assert "## The fix" in issue["description"] and "+ cpuregs[latched_rd] <= cpuregs_wrdata;" in issue["description"]
    assert fakes["github"].comments[0]["html_url"] in issue["description"]
    assert "Slack thread: https://slack.test/" in fakes["comments"][0]["body"]


def test_an_empty_page_from_slack_does_not_post_a_second_parent(fakes):
    act.publish(PR, blame(), PATCH)
    fakes["flaky"]["empty"] = 1
    assert act.publish(PR, blame(), PATCH)["created"] == 0
    assert len(parents(fakes)) == 1


def test_slack_that_never_answers_still_never_posts_twice(fakes, monkeypatch):
    """The ledger holds the parent's ts, so an empty page neither blocks the publish nor posts a second parent."""
    act.publish(PR, blame(), PATCH)
    fakes["flaky"]["empty"] = 99
    slept = []
    monkeypatch.setattr(slack.time, "sleep", slept.append)
    assert act.publish(PR, blame(), PATCH)["created"] == 0
    assert len(parents(fakes)) == 1 and slept == []


def test_a_first_post_is_not_held_up_by_an_empty_page(fakes, monkeypatch):
    """Nothing to duplicate before this install has posted for the pull request, so one look at Slack is enough."""
    fakes["flaky"]["empty"] = 99
    slept = []
    monkeypatch.setattr(slack.time, "sleep", slept.append)
    act.publish(PR, blame(), PATCH)
    assert len(parents(fakes)) == 1 and slept == []


def test_verify_still_checks_slack_itself(fakes, monkeypatch):
    act.publish(PR, blame(), PATCH)
    fakes["flaky"]["empty"] = 99
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    with pytest.raises(slack.Unanswered):
        slack._find(str(PR), trust_ledger=False)


def test_guard_refuses_writes_outside_the_allow_list():
    with pytest.raises(http.UnsafeWrite):
        http.guard("github", "PUT", "/repos/owizdom/picorv32-ci/pulls/7/merge", "x", CFG)
    with pytest.raises(http.UnsafeWrite):
        http.guard("github", "POST", "/repos/someone/else/pulls/7/comments", "x", CFG)
    with pytest.raises(http.UnsafeWrite):
        http.guard("slack", "chat.postMessage", "C999", "x", CFG)
