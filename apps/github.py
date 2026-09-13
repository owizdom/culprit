"""GitHub: find failed regression runs, read the pull request, post the evidence, set a status."""
import os

import httpx

from apps import http

API = "https://api.github.com"


def client():
    http.load_env()
    return httpx.Client(base_url=API, timeout=30, follow_redirects=True, headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })


def repo():
    return http.config()["github"]["repo"]


def completed_runs(etag=None):
    """Latest completed pull_request runs of the regression workflow. Returns (runs, etag)."""
    headers = {"If-None-Match": etag} if etag else {}
    with client() as c:
        r = c.get(f"/repos/{repo()}/actions/runs", params={"event": "pull_request", "status": "completed",
                                                          "per_page": 20}, headers=headers)
    if r.status_code == 304:
        return [], etag
    r.raise_for_status()
    runs = []
    with client() as c:
        for run in r.json()["workflow_runs"]:
            prs = run.get("pull_requests") or []
            pr = prs[0]["number"] if prs else pr_for_commit(c, run["head_sha"], run["head_branch"])
            runs.append({"id": run["id"], "sha": run["head_sha"], "branch": run["head_branch"],
                         "conclusion": run["conclusion"], "pr": pr, "url": run["html_url"]})
    return runs, r.headers.get("ETag")


PR_BY_COMMIT = {}


def pr_for_commit(c, sha, branch):
    """GitHub empties a run's pull_requests once the pull request is merged or closed, even when CI finishes after
    the merge. Ask which pull request holds the commit instead, once per commit."""
    if sha not in PR_BY_COMMIT:
        r = c.get(f"/repos/{repo()}/commits/{sha}/pulls")
        if r.status_code != 200:
            return None
        pulls = r.json()
        match = next((p for p in pulls if p["head"]["ref"] == branch), pulls[0] if pulls else None)
        PR_BY_COMMIT[sha] = match["number"] if match else None
    return PR_BY_COMMIT[sha]


def pull(number):
    with client() as c:
        pr = c.get(f"/repos/{repo()}/pulls/{number}").json()
    return {"number": number, "title": pr["title"], "body": pr.get("body") or "", "author": pr["user"]["login"],
            "base_sha": pr["base"]["sha"], "head_sha": pr["head"]["sha"], "url": pr["html_url"],
            "state": pr["state"], "base_ref": pr["base"]["ref"]}


def marker(pr, sha):
    return f"<!-- culprit:{pr}@{sha[:12]} -->"


def upsert_review_comment(pr, sha, path, line, body):
    """One comment per (PR, head SHA), found again by its marker. Returns (comment, created)."""
    body = marker(pr, sha) + "\n" + body
    with client() as c:
        existing = c.get(f"/repos/{repo()}/pulls/{pr}/comments", params={"per_page": 100}).json()
        for comment in existing:
            if comment["body"].startswith(marker(pr, sha)):
                if comment["body"] != body:
                    target = f"/repos/{repo()}/pulls/comments/{comment['id']}"
                    http.guard("github", "PATCH", target, marker(pr, sha))
                    comment = c.patch(target, json={"body": body}).raise_for_status().json()
                    http.record("github", "PATCH", target, marker(pr, sha), comment["id"])
                return comment, False
        target = f"/repos/{repo()}/pulls/{pr}/comments"
        http.guard("github", "POST", target, marker(pr, sha))
        r = c.post(target, json={"body": body, "commit_id": sha, "path": path, "line": line, "side": "RIGHT"})
        if r.status_code == 422:
            r = c.post(target, json={"body": body, "commit_id": sha, "path": path, "subject_type": "file"})
        comment = r.raise_for_status().json()
        http.record("github", "POST", target, marker(pr, sha), comment["id"])
        return comment, True


def set_status(sha, state, description, target_url=None):
    """Skipped when the latest culprit status already says the same thing, so a replay writes nothing."""
    latest = statuses(sha)
    if latest and latest[0]["state"] == state and latest[0].get("description") == description[:140]:
        return False
    target = f"/repos/{repo()}/statuses/{sha}"
    http.guard("github", "POST", target, f"status:{sha[:12]}")
    with client() as c:
        r = c.post(target, json={"state": state, "context": "culprit", "description": description[:140],
                                 "target_url": target_url})
    r.raise_for_status()
    http.record("github", "POST", target, f"status:{sha[:12]}:{state}", r.json()["id"])


TEST_BRANCH = "culprit-test/"


def open_test_pull_request(files, title, body, branch):
    """Test Run only: put files on top of main in a new culprit-test/ branch and open a pull request from it.

    Uses the Git Data API, so nothing is cloned. The write guard allows these calls only for culprit-test/ branches.
    """
    if not branch.startswith(TEST_BRANCH):
        raise http.UnsafeWrite(f"test branches must start with {TEST_BRANCH}")
    r = repo()
    with client() as c:
        main = c.get(f"/repos/{r}/git/ref/heads/main").raise_for_status().json()["object"]["sha"]
        base_tree = c.get(f"/repos/{r}/git/commits/{main}").raise_for_status().json()["tree"]["sha"]
        entries = []
        for path, text in files.items():
            target = f"/repos/{r}/git/blobs"
            http.guard("github", "POST", target, branch)
            blob = c.post(target, json={"content": text, "encoding": "utf-8"}).raise_for_status().json()
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        target = f"/repos/{r}/git/trees"
        http.guard("github", "POST", target, branch)
        tree = c.post(target, json={"base_tree": base_tree, "tree": entries}).raise_for_status().json()
        target = f"/repos/{r}/git/commits"
        http.guard("github", "POST", target, branch)
        commit = c.post(target, json={"message": title, "tree": tree["sha"], "parents": [main]}).raise_for_status().json()
        target = f"/repos/{r}/git/refs"
        http.guard("github", "POST", target, f"refs/heads/{branch}")
        c.post(target, json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]}).raise_for_status()
        target = f"/repos/{r}/pulls"
        http.guard("github", "POST", target, branch)
        pr = c.post(target, json={"title": title, "head": branch, "base": "main", "body": body}).raise_for_status().json()
    http.record("github", "POST", target, branch, pr["number"])
    return {"pr": pr["number"], "url": pr["html_url"], "sha": commit["sha"], "branch": branch}


def file_at_main(path):
    """The text of one file as it is on main right now."""
    with client() as c:
        r = c.get(f"/repos/{repo()}/contents/{path}", params={"ref": "main"},
                  headers={"Accept": "application/vnd.github.raw+json"})
    r.raise_for_status()
    return r.text


def latest_run_for(sha):
    """The newest workflow run for a commit, whatever its state, or None before CI has picked it up."""
    with client() as c:
        r = c.get(f"/repos/{repo()}/actions/runs", params={"head_sha": sha, "per_page": 5})
    r.raise_for_status()
    runs = r.json().get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    return {"id": run["id"], "status": run["status"], "conclusion": run["conclusion"], "url": run["html_url"]}


def comments_with_marker(pr, sha):
    with client() as c:
        return [x for x in c.get(f"/repos/{repo()}/pulls/{pr}/comments", params={"per_page": 100}).json()
                if x["body"].startswith(marker(pr, sha))]


def statuses(sha):
    with client() as c:
        return [s for s in c.get(f"/repos/{repo()}/commits/{sha}/statuses").json() if s["context"] == "culprit"]
