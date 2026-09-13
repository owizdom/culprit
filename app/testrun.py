"""Test Run: open a real pull request with a known bug on the watched repository, and let CULPRIT handle it.

The bug comes from the seeded corpus: only a case's edits, title and description are read from cases.yaml, never
its answer key. CI on GitHub turns red, the watcher picks the run up, and the investigation posts to GitHub, Linear
and Slack exactly as it would for any other pull request.
"""
import json
import time

import httpx
import yaml

import design
from apps import github, http
from runs import store

CORPUS = http.ROOT / "corpus"
# Cases where the evaluation's CULPRIT arm (evals/results/REPORT.md) found the culprit edit and its fix passed every
# test while keeping the author's intended change.
POOL = ["a01", "m03", "a07", "m05", "a04", "m01", "a02", "m06", "a09", "h03", "a03", "m04", "a05", "m02", "m08", "m07"]
STALE_SECONDS = 20 * 60


def state_path():
    return store.RUNS / "testrun.json"


def read_state():
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def order(state):
    """Cases never used first, then the ones used longest ago."""
    used = [case for case in state.get("used", []) if case in POOL]
    return [case for case in POOL if case not in used] + used


def case_details(case):
    cases = yaml.safe_load((CORPUS / "cases.yaml").read_text())
    return next(c for c in cases if c["id"] == case)


def case_changes(case, read):
    """The case's edits applied to the files as main holds them now, or None if main has moved on and an edit
    no longer matches exactly once (for example after an earlier test pull request was merged)."""
    files = {}
    for edit in case_details(case)["edits"]:
        text = files[edit["path"]] if edit["path"] in files else read(edit["path"])
        if text.count(edit["find"]) != 1:
            return None
        files[edit["path"]] = text.replace(edit["find"], edit["replace"])
    return files


def start():
    if not design.get()["corpus_features"]:
        return {"error": "Test Run uses the PicoRV32 corpus; it is off for this design."}
    if not http.has_token("github"):
        return {"error": "Connect GitHub first."}
    current = status()
    if current["active"] and time.time() - read_state().get("current", {}).get("started_at", 0) < STALE_SECONDS:
        return {"error": f"A test run is already going: pull request #{current['pr']}."}
    state = read_state()
    try:
        case, files = next(((c, f) for c in order(state) if (f := case_changes(c, github.file_at_main))), (None, None))
    except (httpx.HTTPError, KeyError) as e:
        return {"error": f"Could not read main on GitHub: {e}"}
    if case is None:
        return {"error": "None of the corpus bugs applies cleanly to main any more."}
    details = case_details(case)
    branch = f"{github.TEST_BRANCH}{case}-{time.strftime('%Y%m%d-%H%M%S')}"
    body = f"{details['body']}\n\nOpened by CULPRIT Test Run: a seeded bug from the corpus (case {case})."
    try:
        pr = github.open_test_pull_request(files, details["title"], body, branch)
    except (httpx.HTTPError, http.UnsafeWrite, KeyError) as e:
        return {"error": f"Could not open the test pull request: {e}"}
    used = [c for c in state.get("used", []) if c != case] + [case]
    store.write_json(state_path(), {"current": dict(pr, case=case, title=details["title"], started_at=time.time()),
                                    "used": used})
    return {"pr": pr["pr"], "url": pr["url"], "case": case, "title": details["title"]}


def status():
    current = read_state().get("current")
    out = {"active": False, "pr": None, "url": None, "case": None, "title": None, "ci": "none", "run_url": None,
           "investigation": None, "investigation_status": None, "posted": False, "error": None}
    if not current:
        return out
    out.update(pr=current["pr"], url=current["url"], case=current["case"], title=current["title"])
    try:
        run = github.latest_run_for(current["sha"])
    except (httpx.HTTPError, KeyError) as e:
        run, out["error"] = None, f"Could not read CI: {e}"
    if run:
        out.update(ci=run["conclusion"] if run["status"] == "completed" else run["status"], run_url=run["url"])
    folder = store.RUNS / f"{current['pr']}@{current['sha'][:12]}"
    meta = folder / "meta.json"
    if meta.is_file():
        out.update(investigation=folder.name, investigation_status=json.loads(meta.read_text()).get("status"))
        events = folder / "events.jsonl"
        lines = events.read_text().splitlines() if events.is_file() else []
        out["posted"] = any('"step": "act"' in line and '"state": "done"' in line for line in lines)
    out["active"] = not (out["posted"] or out["ci"] in ("success", "cancelled"))
    return out
