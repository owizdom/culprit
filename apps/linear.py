"""Linear: one issue per pull request, found again by the [culprit #N] title marker."""
import os

import httpx

from apps import http

API = "https://api.linear.app/graphql"


def gql(query, variables=None):
    http.load_env()
    r = httpx.post(API, json={"query": query, "variables": variables or {}}, timeout=30,
                   headers={"Authorization": os.environ["LINEAR_API_KEY"], "Content-Type": "application/json"})
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def marker(pr):
    return f"[culprit #{pr}]"


def find(pr):
    data = gql("query($m: String!) { issues(filter: { title: { contains: $m } }) "
               "{ nodes { id identifier url title state { name type } assignee { name } } } }", {"m": marker(pr)})
    return data["issues"]["nodes"]


def upsert_issue(pr, title, description, assignee_id=None):
    """Returns (issue, created). Never creates a second issue for the same pull request."""
    existing = find(pr)
    if existing:
        return existing[0], False
    cfg = http.config()
    full_title = f"{marker(pr)} {title}"
    http.guard("linear", "issueCreate", full_title, marker(pr), cfg)
    payload = {"teamId": cfg["linear"]["team_id"], "title": full_title, "description": description}
    if assignee_id:
        payload["assigneeId"] = assignee_id
    data = gql("mutation($i: IssueCreateInput!) { issueCreate(input: $i) "
               "{ success issue { id identifier url title state { name type } assignee { name } } } }", {"i": payload})
    issue = data["issueCreate"]["issue"]
    http.record("linear", "issueCreate", full_title, marker(pr), issue["id"])
    return issue, True


def comment_once(pr, issue_id, key, body):
    """A per-commit note on the issue, skipped if one with the same key is already there."""
    tag = f"<!-- {key} -->"
    data = gql("query($id: String!) { issue(id: $id) { comments { nodes { body } } } }", {"id": issue_id})
    if any(tag in c["body"] for c in data["issue"]["comments"]["nodes"]):
        return False
    http.guard("linear", "commentCreate", marker(pr), key)
    gql("mutation($i: CommentCreateInput!) { commentCreate(input: $i) { success } }",
        {"i": {"issueId": issue_id, "body": body + "\n\n" + tag}})
    http.record("linear", "commentCreate", marker(pr), key, issue_id)
    return True


def close(pr):
    cfg = http.config()
    issues = find(pr)
    if not issues or issues[0]["state"]["type"] == "completed":
        return False
    http.guard("linear", "issueUpdate", marker(pr), f"close:{pr}", cfg)
    gql("mutation($id: String!, $s: String!) { issueUpdate(id: $id, input: { stateId: $s }) { success } }",
        {"id": issues[0]["id"], "s": cfg["linear"]["done_state_id"]})
    http.record("linear", "issueUpdate", marker(pr), f"close:{pr}", issues[0]["id"])
    return True
