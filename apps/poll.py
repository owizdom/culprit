"""Read-only pulls of GitHub, Linear and Slack for the CULPRIT window. Never writes anything remote."""
from datetime import datetime, timezone

from apps import github, http, linear, slack


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot(pr, sha, slack_ts=None):
    out = {app: {"skipped": "no token"} for app in ("github", "linear", "slack") if not http.has_token(app)}
    if "github" not in out:
        out.update(_github(pr, sha))
    if "linear" not in out:
        out.update(_linear(pr))
    if "slack" not in out:
        out.update(_slack(slack_ts))
    return out


def _github(pr, sha):
    out = {}
    try:
        comments = github.comments_with_marker(pr, sha)
        statuses = github.statuses(sha)
        out["github"] = {"comment_url": comments[0]["html_url"] if comments else None,
                         "suggestion": bool(comments and "```suggestion" in comments[0]["body"]),
                         "status": statuses[0]["state"] if statuses else None, "synced": now()}
    except Exception as e:          # the window shows the app as unreachable instead of crashing
        out["github"] = {"error": str(e)[:200], "synced": now()}
    return out


def _linear(pr):
    out = {}
    try:
        issues = linear.find(pr)
        if issues:
            i = issues[0]
            out["linear"] = {"identifier": i["identifier"], "state": i["state"]["name"],
                             "assignee": (i.get("assignee") or {}).get("name"), "url": i["url"], "synced": now()}
        else:
            out["linear"] = {"synced": now()}
    except Exception as e:
        out["linear"] = {"error": str(e)[:200], "synced": now()}
    return out


def _slack(slack_ts):
    out = {}
    try:
        if slack_ts:
            out["slack"] = {"permalink": slack.permalink(slack_ts), "replies": slack.thread(slack_ts)[-3:], "synced": now()}
        else:
            out["slack"] = {"synced": now()}
    except Exception as e:
        out["slack"] = {"error": str(e)[:200], "synced": now()}
    return out
