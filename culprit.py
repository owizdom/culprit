"""CULPRIT: find the line that broke a chip regression, prove it, fix it, show how the bug travelled.

  culprit.py app                     open the window and watch the repo
  culprit.py watch                   watch without the window
  culprit.py run <pr>                investigate one pull request now
  culprit.py local <base> <head>     investigate two local checkouts (no apps; for development and evals)
  culprit.py resolve <pr> <sha>      close the loop after CI went green on <sha>
  culprit.py verify <pr> <sha>       check GitHub, Linear and Slack agree
"""
import difflib
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import design
from localize import confirm
from runs.store import Run
from runs.store import now as store_now
from sim import icarus


def checkout(repo_url, sha, dest):
    subprocess.run(["git", "clone", "-q", repo_url, dest], check=True)
    subprocess.run(["git", "-C", dest, "checkout", "-q", sha], check=True)
    return icarus.read_tree(dest)


def investigate(base_tree, head_tree, run, intent="", pr_info=None, apps=True):
    started = time.time()

    def emit(step, state, text, trust=None, sims=0, data=None):
        run.event(step, state, text, trust, sims, data)
        print(f"[{step}:{state}] {text}")

    blame = confirm.localize(base_tree, head_tree, emit)
    run.write("blame.json", blame_json(blame))
    status = {"rtl": "confirmed", "interaction": "confirmed", "non_rtl": "not_rtl"}.get(blame.kind, "abstained")
    lines = culprit_lines(blame, None)
    verdict_line = blame.line_fix["line"] if blame.line_fix else (min(lines) if lines else None)
    failing_test = (blame.head.failing_tests or [None])[0] if blame.head else None
    run.meta(status=status, sims=blame.sims, failing_test=failing_test, cost_usd=0.0,
             trap_cycle=blame.head.trap_cycle if blame.head else None,
             failure=confirm.describe(blame.head) if blame.head else None,
             verdict={"path": blame.culprit_file or design.get()["rtl_file"], "line": verdict_line, "test": failing_test,
                      "failure": confirm.describe(blame.head) if blame.head else None,
                      "trust": "confirmed" if blame.culprit or blame.kind == "non_rtl" else "proposed"})

    patch = None
    if blame.culprit:
        patch = repair(base_tree, head_tree, blame, intent, emit)
        if patch:
            run.write("patch.json", patch)
            meta = run.read("meta.json")
            fields = {"cost_usd": patch.get("cost_usd", 0.0)}
            if patch.get("trust") == "confirmed":
                fields.update(status="fixed", verdict=dict(meta["verdict"], line=patch["start"], trust="confirmed"))
            run.meta(**fields)

    lines = culprit_lines(blame, patch)
    tracer = None
    if lines:
        tracer = threading.Thread(target=trace, args=(base_tree, head_tree, lines, run, emit), daemon=True)
        tracer.start()

    if apps and pr_info:
        from apps import act
        if tracer:
            tracer.join(timeout=60)         # a path usually takes a second or two; never wait longer than this
        emit("act", "start", "posting to GitHub, Linear and Slack")
        out = act.publish(pr_info, blame, patch, path_summary=path_line(run.read("path.json")))
        from apps import poll
        snapshot = poll.snapshot(pr_info["number"], pr_info["head_sha"], out.get("slack_ts"))
        snapshot["github"] = dict(snapshot.get("github", {}), pr_url=pr_info["url"])
        if out.get("slack_ts"):
            snapshot["slack"] = dict(snapshot.get("slack", {}), ts=out["slack_ts"])
        snapshot["skipped"] = out.get("skipped", [])
        run.write("apps.json", snapshot)
        skipped = f", skipped {', '.join(out['skipped'])} (no token)" if out.get("skipped") else ""
        emit("act", "done", f"created {out['created']}, unchanged {out['unchanged']}{skipped}", "confirmed")
    if tracer:
        tracer.join()
    run.meta(finished=store_now(), seconds=round(time.time() - started, 1))
    return blame, patch


def repair(base_tree, head_tree, blame, intent, emit):
    if blame.line_fix:
        fix = blame.line_fix
        emit("repair", "done", f"restoring line {fix['line']} alone passes; the rest of the change is kept", "confirmed", 0)
        return {"trust": "confirmed", "path": design.get()["rtl_file"], "start": fix["line"], "end": fix["line"],
                "before": fix["before"], "replacement": fix["replacement"], "method": "restore_line",
                "explanation": "the line differs from its base version; restoring it alone passes", "attempts": 0,
                "result": "ALL TESTS PASSED."}
    revert = whole_edit_revert(base_tree, blame)
    if revert and revert["lines_changed"] == 1:
        emit("repair", "done", f"the edit at line {revert['start']} is one line and it is the bug: revert it", "confirmed", 0)
        return revert
    # A longer edit can hold intended work around the bug, so ask for a fix that keeps it first.
    patch = None
    try:
        from repair import claude
        patch = claude.fix(base_tree, head_tree, blame, intent=intent, emit=emit)
    except Exception as e:
        emit("repair", "fail", f"model repair unavailable: {e}", "proposed", 0)
    confirmed = bool(patch and patch.get("trust") == "confirmed")
    spent = {"attempts": (patch or {}).get("attempts", 0), "cost_usd": (patch or {}).get("cost_usd", 0.0)}
    if revert and not confirmed:
        where = f"line {revert['start']}" if revert["start"] == revert["end"] else f"lines {revert['start']}-{revert['end']}"
        emit("repair", "done", f"no fix that keeps the rest of the edit passed; reverting {where} does", "confirmed", 0)
        return dict(revert, **spent,
                    explanation="no fix that keeps the rest of this edit passed the regression; undoing the edit does")
    if not confirmed:
        # A bug spread over several edits: undo exactly those edits, keep every other change, and simulate it.
        undone = multi_edit_revert(head_tree, blame)
        if undone:
            emit("repair", "done", f"the bug spans {len(undone['edits'])} edits; undoing those edits and keeping the rest "
                                   "passes the regression", "confirmed", 1)
            return dict(undone, **spent)
    return patch


def edit_region(h):
    """The changed part of one edit, without its diff context, and the base text that undoes it."""
    ops = [op for op in difflib.SequenceMatcher(None, h.old, h.new, autojunk=False).get_opcodes() if op[0] != "equal"]
    i1, j1 = ops[0][1], ops[0][3]
    i2, j2 = ops[-1][2], ops[-1][4]
    lines_changed = max(i2 - i1, j2 - j1)
    before, replacement = h.new[j1:j2], h.old[i1:i2]
    if j1 == j2:
        # The edit only deleted lines: put them back in front of the line that now sits there.
        before, replacement = h.new[j1:j1 + 1], h.old[i1:i2] + h.new[j1:j1 + 1]
        j2 = j1 + 1
    return {"start": h.new_start + j1, "end": h.new_start + j2 - 1, "before": "".join(before),
            "replacement": "".join(replacement), "lines_changed": lines_changed}


def apply_edits(text, edits):
    lines = text.splitlines(keepends=True)
    for e in sorted(edits, key=lambda e: e["start"], reverse=True):
        lines[e["start"] - 1:e["end"]] = e["replacement"].splitlines(keepends=True)
    return "".join(lines)


def whole_edit_revert(base_tree, blame):
    """When the culprit is the only RTL edit, undoing it gives back the base RTL, which revert-confirm
    already simulated as passing. For a one-line edit that is the fix; for a longer one it is the fallback."""
    ids = set().union(*blame.culprit) if blame.culprit else set()
    rtl = [h for h in blame.hunks if h.path == design.get()["rtl_file"]]
    culprit = [h for h in rtl if h.id in ids]
    if len(culprit) != 1 or len(rtl) != 1:
        return None
    region = edit_region(culprit[0])
    return {"trust": "confirmed", "path": design.get()["rtl_file"], "start": region["start"], "end": region["end"],
            "before": region["before"], "replacement": region["replacement"], "method": "revert_edit", "attempts": 0,
            "explanation": "this one-line edit is the only RTL change and undoing it passes; there is nothing else in it to keep",
            "result": "ALL TESTS PASSED.", "cost_usd": 0.0, "lines_changed": region["lines_changed"]}


def multi_edit_revert(head_tree, blame):
    """A culprit spread over several edits: undo each of them and keep every other change. Counts only if it passes."""
    ids = set().union(*blame.culprit) if blame.culprit else set()
    culprit = sorted((h for h in blame.hunks if h.path == design.get()["rtl_file"] and h.id in ids), key=lambda h: h.new_start)
    if len(culprit) < 2:
        return None
    edits = [{k: v for k, v in edit_region(h).items() if k != "lines_changed"} for h in culprit]
    tree = dict(head_tree, **{design.get()["rtl_file"]: apply_edits(head_tree[design.get()["rtl_file"]], edits)})
    if not icarus.run(tree).passed:
        return None
    first = edits[0]
    return {"trust": "confirmed", "path": design.get()["rtl_file"], "start": first["start"], "end": first["end"],
            "before": first["before"], "replacement": first["replacement"], "edits": edits, "method": "revert_edits",
            "explanation": f"the bug spans {len(edits)} edits; undoing those edits and keeping every other change passes "
                           "the regression", "result": "ALL TESTS PASSED."}


def culprit_lines(blame, patch):
    if patch and patch.get("edits"):
        return set().union(*(range(e["start"], e["end"] + 1) for e in patch["edits"]))
    if patch and patch.get("start"):
        return set(range(patch["start"], patch["end"] + 1))
    ids = set().union(*blame.culprit) if blame.culprit else set()
    out = set()
    for h in blame.hunks:
        if h.id in ids:
            out |= h.changed_head_lines()
    return out


def trace(base_tree, head_tree, lines, run, emit):
    from surface import propagation
    emit("propagation", "start", "tracing how the bug travelled through the chip")
    try:
        path = propagation.render(base_tree, head_tree, lines, workdir=run.dir / "trace")
    except Exception as e:
        emit("propagation", "fail", f"could not trace: {e}", None)
        return
    run.write("path.json", path)
    if path.get("error"):
        emit("propagation", "fail", path["error"], None)
    else:
        emit("propagation", "done", f"{path['confirmed_hops']} hops confirmed by waveform, visible at "
                                    f"{path['observable']['port']} on cycle {path['observable']['cycle']}", "confirmed")


def path_line(path):
    """One line for the pull request: the confirmed hops from the culprit line to the failure."""
    if not path or path.get("error") or not path.get("nodes"):
        return None
    obs = path["observable"]
    hops = [n for n in path["nodes"] if n["kind"] in ("memory", "signal", "register")]
    chain = " -> ".join(f"{n['label']}{'' if n['trust'] == 'confirmed' else ' (' + n['trust'] + ')'}" for n in hops)
    if not hops or hops[-1]["label"] != obs["port"]:
        chain += f" -> {obs['port']}"
    first = path["nodes"][0]["label"]
    return (f"Propagation, {path['confirmed_hops']} hops confirmed by waveform: {first} -> {chain}, "
            f"visible at cycle {obs['cycle']:,}")


def blame_json(blame):
    return {"kind": blame.kind, "sims": blame.sims, "culprit_file": blame.culprit_file,
            "hunks": [{"id": h.id, "path": h.path, "new_start": h.new_start, "old_start": h.old_start,
                       "new": list(h.new), "old": list(h.old), "verdict": blame.verdicts.get(h.id, "untested"),
                       "method": blame.inert.get(next((g for g in blame.inert if h.id in g), None))}
                      for h in blame.hunks],
            "culprit": [sorted(g) for g in blame.culprit], "line_fix": blame.line_fix,
            "facts": [vars(f) for f in blame.facts],
            "head": {"status": blame.head.status, "failing_tests": blame.head.failing_tests,
                     "trap_cycle": blame.head.trap_cycle, "reason": blame.head.trap_reason} if blame.head else None}


def cmd_local(base_dir, head_dir, pr="0"):
    base, head = icarus.read_tree(base_dir), icarus.read_tree(head_dir)
    digest = hashlib.sha256("".join(head[p] for p in sorted(head)).encode()).hexdigest()
    run = Run(pr, digest)
    base_digest = hashlib.sha256("".join(base[p] for p in sorted(base)).encode()).hexdigest()
    run.meta(title=f"local {Path(head_dir).name}", author="local", base_sha=base_digest)
    investigate(base, head, run, apps=False)
    print("run written to", run.dir)


def cmd_run(pr):
    from apps import github, http
    cfg = http.config()
    info = github.pull(int(pr))
    url = f"https://github.com/{cfg['github']['repo']}.git"
    with tempfile.TemporaryDirectory() as tmp:
        head = checkout(url, info["head_sha"], f"{tmp}/head")
        merge_base = subprocess.run(["git", "-C", f"{tmp}/head", "merge-base", info["head_sha"],
                                     f"origin/{info['base_ref']}"], capture_output=True, text=True).stdout.strip()
        base = checkout(url, merge_base or info["base_sha"], f"{tmp}/base")
    run = Run(info["number"], info["head_sha"])
    run.meta(title=info["title"], author=info["author"], base_sha=merge_base or info["base_sha"], url=info["url"])
    investigate(base, head, run, intent=f"{info['title']}\n\n{info['body']}", pr_info=info)


MAX_ATTEMPTS = 3


def cmd_watch(interval=5):
    from apps import http
    from runs import store
    seen, failures, etag, last_error = set(), {}, None, None
    while True:
        if not http.has_token("github"):     # logged out: wait for the next sign-in instead of failing every pass
            store.write_json(store.RUNS / "watcher.json", {"state": "signed_out", "error": None,
                                                          "last_error": last_error, "checked": store_now()})
            time.sleep(interval)
            continue
        errors = []
        # A failed action is retried on the next pass, so ask GitHub for the full list instead of "not modified".
        retrying = any(key not in seen for key in failures)
        try:
            etag = watch_once(seen, None if retrying else etag, failures, errors)
        except Exception as e:          # a bad token or a network error shows in the window instead of ending the thread
            errors.append(f"{type(e).__name__}: {e}")
        if errors:
            last_error = {"error": "; ".join(errors)[:500], "at": store_now()}
        state = {"state": "error" if errors else "watching", "error": last_error["error"] if errors else None,
                 "last_error": last_error}
        store.write_json(store.RUNS / "watcher.json", dict(state, checked=store_now()))
        time.sleep(interval)


def watch_once(seen, etag=None, failures=None, errors=None):
    """Act on the newest completed run of each pull request.

    A red run is investigated unless that commit already was (so opening the app does not redo old work).
    A green run resolves a pull request only if CULPRIT investigated it and has not resolved it yet.
    An action that fails is tried again on later passes, up to MAX_ATTEMPTS; every write it makes is an upsert,
    so a retry never posts twice.
    """
    from apps import github
    from runs import store
    failures = {} if failures is None else failures
    errors = [] if errors is None else errors
    runs, etag = github.completed_runs(etag)
    newest = {}
    for r in runs:
        if r["pr"] and r["pr"] not in newest:
            newest[r["pr"]] = r
    for r in newest.values():
        key = (r["id"], r["conclusion"])
        if key in seen:
            continue
        try:
            if r["conclusion"] == "failure":
                meta = store.RUNS / f"{r['pr']}@{r['sha'][:12]}" / "meta.json"
                if not (meta.is_file() and json.loads(meta.read_text()).get("finished")):
                    cmd_run(r["pr"])
            elif r["conclusion"] == "success":
                run = latest_run(r["pr"])
                if run and run.read("meta.json").get("status") != "resolved":
                    cmd_resolve(r["pr"], r["sha"])
        except Exception as e:
            failures[key] = failures.get(key, 0) + 1
            gave_up = failures[key] >= MAX_ATTEMPTS
            errors.append(f"PR #{r['pr']}: {type(e).__name__}: {e}" + (" (gave up)" if gave_up else " (will retry)"))
            if gave_up:
                seen.add(key)
            continue
        seen.add(key)
        failures.pop(key, None)
    return etag


def latest_run(pr):
    """The newest investigation folder for a pull request, or None."""
    from runs import store
    folders = [f for f in store.RUNS.glob(f"{pr}@*") if (f / "meta.json").is_file()]
    if not folders:
        return None
    metas = [json.loads((f / "meta.json").read_text()) for f in folders]
    newest = max(metas, key=lambda m: m.get("started") or "")
    return Run(pr, newest["sha"])


def cmd_resolve(pr, green_sha):
    """CI is green: close the Linear issue, reply in Slack, set a success status, and show it in the window."""
    from apps import act, github, poll
    pr = int(pr)
    info = github.pull(pr)
    run = latest_run(pr)
    if run:
        run.event("resolve", "start", f"CI is green on {green_sha[:12]}; closing the loop")
    try:
        out = act.resolve(info, green_sha)
    except Exception as e:
        if run:
            run.event("resolve", "fail", f"could not close the loop yet ({type(e).__name__}: {e}); CULPRIT will try again",
                      "proposed")
        raise
    text = (f"CI is green on {green_sha[:12]}: Linear issue {'closed' if out.get('linear_closed') else 'already closed'}, "
            "resolved reply in the Slack thread, success status on the commit")
    print(f"[resolve:done] {text}")
    if run:
        run.event("resolve", "done", text, "confirmed")
        run.meta(status="resolved", resolved_sha=green_sha)
        ts = (run.read("apps.json", {}).get("slack") or {}).get("ts")
        snapshot = poll.snapshot(pr, run.read("meta.json")["sha"], ts)
        # The review comment belongs to the red commit; the status that matters now is the green one's.
        green = github.statuses(green_sha)
        snapshot["github"] = dict(snapshot.get("github", {}), pr_url=info["url"],
                                  status=green[0]["state"] if green else None)
        if ts:
            snapshot["slack"] = dict(snapshot.get("slack", {}), ts=ts)
        run.write("apps.json", snapshot)
    return out


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "local":
        cmd_local(argv[2], argv[3], int(argv[4]) if len(argv) > 4 else 0)
    elif cmd == "run":
        cmd_run(argv[2])
    elif cmd == "watch":
        cmd_watch()
    elif cmd == "resolve":
        cmd_resolve(argv[2], argv[3])
    elif cmd == "verify":
        from apps import verify
        problems = verify.end_state(int(argv[2]), argv[3])
        print("OK" if not problems else "\n".join(problems))
    elif cmd == "app":
        threading.Thread(target=cmd_watch, daemon=True).start()
        from app import window
        window.main(dev="--dev" in argv)
    else:
        sys.exit(__doc__)


def cli():
    """The `culprit` command installed with uv or pip. With no arguments it opens the app."""
    main(["culprit", *(sys.argv[1:] or ["app"])])


if __name__ == "__main__":
    main(sys.argv)
