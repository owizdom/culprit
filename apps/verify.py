"""Check the end state across GitHub, Linear and Slack with live reads only.

Returns a list of problems; an empty list means the three systems agree with each other.
"""
import json

from apps import github, http, linear, slack


def end_state(pr, sha, expect_closed=False):
    problems = []
    comments = github.comments_with_marker(pr, sha)
    if len(comments) != 1:
        problems.append(f"expected 1 review comment for {pr}@{sha[:12]}, found {len(comments)}")
    if not github.statuses(sha):
        problems.append("no culprit commit status on the head commit")

    issues = []
    if not http.has_token("linear"):
        problems.append("skipped Linear: no LINEAR_API_KEY")
    else:
        issues = linear.find(pr)
    if http.has_token("linear") and len(issues) != 1:
        problems.append(f"expected 1 Linear issue for #{pr}, found {len(issues)}")
    elif issues:
        done = issues[0]["state"]["type"] == "completed"
        if done != expect_closed:
            problems.append(f"Linear issue state is {issues[0]['state']['name']}, expected {'closed' if expect_closed else 'open'}")
        if comments and comments[0]["html_url"] not in (linear.gql(
                "query($id: String!) { issue(id: $id) { description } }", {"id": issues[0]["id"]})["issue"]["description"] or ""):
            problems.append("Linear issue does not link the review comment (desync)")

    if not http.has_token("slack"):
        problems.append("skipped Slack: no SLACK_BOT_TOKEN")
    else:
        try:
            parent = slack._find(str(pr), trust_ledger=False)
        except slack.Unanswered as e:
            problems.append(f"could not check Slack: {e}")
            parent = False
        if parent is False:
            pass
        elif parent is None:
            problems.append(f"no Slack parent message for #{pr}")
        elif issues and issues[0]["url"] not in parent.get("text", ""):
            problems.append("Slack parent does not link the Linear issue (desync)")

    ledger = http.LEDGER.read_text().splitlines() if http.LEDGER.exists() else []
    creates = [json.loads(l) for l in ledger]
    comment_posts = sum(1 for l in creates if l["op"] == "POST" and l["key"] == github.marker(pr, sha))
    if comment_posts > 1:
        problems.append(f"ledger shows {comment_posts} comment creates for one key (duplicate)")
    return problems
