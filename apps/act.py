"""Put the verdict where the team works: the pull request line, a Linear issue, a Slack thread.

Every write is an upsert keyed by the pull request (issue, thread) or by (pull request, head SHA)
(review comment, thread reply), so running CULPRIT again on the same failure creates nothing new.
"""
import re

import design
from apps import github, http, linear, slack
from localize.confirm import describe


def tof_block(blame, patch, sha):
    cfg = design.get()
    ip = re.sub(r"\W+", "_", cfg["name"]).lower()
    lines = ["tapeout 1", f"ip {ip} revision {sha[:7]} top {cfg['yosys']['top']}"]
    for i, f in enumerate(blame.facts, 1):
        severity = "blocking" if any(w in f.text for w in ("fails", "pass")) else "info"
        text = f.text.replace('"', "'")
        where = f" @ {f.path}:{f.line}" if f.path and f.line else (f" @ {f.path}" if f.path else f" @ {cfg['rtl_file']}")
        lines.append(f'finding CULPRIT-{i:03d} {f.trust} {severity} "{text}"{where}')
    if patch and patch.get("trust") == "confirmed":
        lines.append(f'finding CULPRIT-{len(blame.facts) + 1:03d} confirmed blocking '
                     f'"replacing lines {patch["start"]}-{patch["end"]} makes the regression pass" '
                     f'@ {cfg["rtl_file"]}:{patch["start"]}')
    return "\n".join(lines)


def sweep_kill_rate():
    """The share of seeded single-token mutants the regression catches, if the corpus sweep has run."""
    import json
    from pathlib import Path
    if not design.get()["corpus_features"]:
        return None
    sweep = Path(__file__).resolve().parent.parent / "corpus" / "sweep.json"
    if not sweep.exists():
        return None
    data = json.loads(sweep.read_text())
    return f"{data['killed']}/{data['total']}"


def comment_body(pr, blame, patch, path_summary, kill_rate):
    line = blame_line(blame, patch)
    trust = "CONFIRMED" if blame.kind in ("rtl", "interaction", "non_rtl") else "PROPOSED"
    head = f"**culprit: `{design.get()['rtl_file']}:{line}` breaks the regression ({trust})**" if blame.kind != "non_rtl" else \
        f"**culprit: `{blame.culprit_file}`, not the RTL ({trust})**"
    parts = [head, ""]
    if patch and patch.get("trust") == "confirmed" and len(patch.get("edits") or []) > 1:
        # A suggestion covers one line range; a fix over several edits is shown as a diff instead.
        parts += [f"The fix undoes {len(patch['edits'])} edits and passes every test:", "```diff",
                  diff_text(blame, patch), "```", ""]
    elif patch and patch.get("trust") == "confirmed":
        parts += ["```suggestion", patch["replacement"].rstrip("\n"), "```", ""]
    elif patch and patch.get("replacement"):
        parts += ["Proposed, not validated:", "```diff", "+" + patch["replacement"].rstrip("\n"), "```", ""]
    parts += [f"- {f.trust.upper()}: {f.text}" for f in blame.facts]
    if patch and patch.get("explanation"):
        parts.append(f"- {patch['trust'].upper()}: {patch['explanation']}")
    if path_summary:
        parts.append(f"- {path_summary}")
    oracle = f"It catches {kill_rate} seeded single-token mutants. " if kill_rate else ""
    parts += ["", f"_The regression is the oracle. {oracle}Simulations used: {blame.sims}._",
              "", "<details><summary>tof findings</summary>", "", "```", tof_block(blame, patch, blame.sha), "```",
              "</details>"]
    return "\n".join(parts)


def blame_line(blame, patch):
    if patch and patch.get("trust") == "confirmed":
        return patch["start"]
    if blame.line_fix:
        return blame.line_fix["line"]
    ids = set().union(*blame.culprit) if blame.culprit else set()
    h = next((h for h in blame.hunks if h.id in ids), None)
    return min(h.changed_head_lines()) if h and h.changed_head_lines() else (h.new_start if h else 1)


# What the messages say. Built from tool facts only; the model's words never reach Slack or Linear unmarked.

def fixed(patch):
    return bool(patch and patch.get("trust") == "confirmed")


def status_word(blame, patch):
    if blame.kind == "non_rtl":
        return "Not the chip"
    if fixed(patch):
        return "Fixed"
    return "Confirmed" if blame.culprit else "Needs review"


def culprit_label(blame, patch):
    return blame.culprit_file if blame.kind == "non_rtl" else f"{design.get()['rtl_file']}:{blame_line(blame, patch)}"


def failure_text(blame):
    """The symptom in words: 'ERROR! at cycle 8,233' says less to a reader than 'the regression failed at clock cycle 8,233'."""
    if blame.head is None:
        return "the regression fails"
    text = describe(blame.head)
    cycle = re.search(r"cycle ([\d,]+)", text)
    at = f" at clock cycle {cycle.group(1)}" if cycle else ""
    test = re.match(r"(\w+)\.\.ERROR", text)
    if test:
        return f"the {test.group(1)} test failed{at}"
    if text.startswith("ERROR!"):
        return f"the regression failed{at}"
    return text


def how_made(patch):
    if patch.get("method") == "restore_line":
        return "restored one line to its base version"
    if patch.get("method") == "revert_edit":
        return "reverted the edit, the only change to the chip"
    if patch.get("method") == "revert_edits":
        return f"undid the {len(patch.get('edits') or [])} edits that carry the bug, kept every other change"
    attempts = patch.get("attempts") or 1
    return f"model patch, accepted after {attempts} attempt{'s' if attempts != 1 else ''}"


def diff_text(blame, patch):
    edits = patch.get("edits") or [patch]
    out = []
    for e in edits:
        before = e.get("before")
        if before is None:
            for h in blame.hunks:
                if h.path == design.get()["rtl_file"] and h.new_start <= e["start"] < h.new_start + len(h.new):
                    before = "".join(h.new[e["start"] - h.new_start:e["end"] - h.new_start + 1])
        if len(edits) > 1:
            out.append(f"@@ line {e['start']} @@")
        out += [f"- {l.strip()}" for l in (before or "").splitlines() if l.strip()]
        out += [f"+ {l.strip()}" for l in e["replacement"].splitlines() if l.strip()]
    return "\n".join(out)


def parent_fields(pr_info, blame, patch, issue):
    """Everything the Slack parent shows. Stored in the message metadata so resolve can redraw it."""
    return {"pr": pr_info["number"], "title": pr_info["title"], "author": pr_info["author"], "pr_url": pr_info["url"],
            "culprit": culprit_label(blame, patch), "failure": failure_text(blame), "status": status_word(blame, patch),
            "sims": blame.sims, "linear": issue["identifier"] if issue else None,
            "linear_url": issue["url"] if issue else None}


def slack_parent(fields):
    """(fallback text, blocks) for the one message per pull request."""
    pr = fields["pr"]
    text = f"PR #{pr} {slack.safe(fields['title'], 140)}: {fields['culprit']}, {fields['status']}."
    items = [("Status", fields["status"]), ("Author", slack.safe(fields["author"], 80)),
             ("Culprit", f"`{fields['culprit']}`"), ("Failing check", slack.safe(fields["failure"], 200))]
    if fields.get("linear_url"):
        text += f" Linear {fields['linear']} {fields['linear_url']}"
        items.append(("Linear", f"<{fields['linear_url']}|{fields['linear']}>"))
    items.append(("Pull request", f"<{fields['pr_url']}|#{pr} on GitHub>"))
    if fields.get("green"):
        context = f"CULPRIT · CI is green on `{fields['green'][:7]}`"
    elif fields["status"] == "Needs review":
        context = f"CULPRIT · {fields.get('sims', 0)} simulations · the fix is a proposal, not confirmed"
    else:
        context = f"CULPRIT · {fields.get('sims', 0)} simulations · the simulator confirmed this"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"PR #{pr} · {fields['title']}"[:150]}},
              {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in items]},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]}]
    return text, blocks


def slack_reply(pr_info, blame, patch, comment_url, issue):
    """(fallback text, blocks) for the thread reply about one commit."""
    sha7, culprit = pr_info["head_sha"][:7], culprit_label(blame, patch)
    sections = [f"*What broke*\nCommit `{sha7}`: {slack.safe(failure_text(blame), 300)}. Culprit: `{culprit}`."]
    why = [f"• {f.trust.capitalize()}: {slack.safe(f.text, 300)}" for f in blame.facts[:3]]
    if fixed(patch):
        why.append("• Confirmed: with the fix applied, all tests pass.")
    sections.append("*Why we know*\n" + "\n".join(why))
    if patch and patch.get("replacement"):
        label = f"*The fix* ({how_made(patch)})" if fixed(patch) else "*Proposed fix* (it did not pass the tests)"
        sections.append(f"{label}\n```{slack.safe(diff_text(blame, patch), 1500)}```")
    links = []
    if comment_url:
        links.append(f"<{comment_url}|{'Suggested fix' if fixed(patch) else 'Review comment'} on GitHub>")
    if issue:
        links.append(f"<{issue['url']}|{issue['identifier']} in Linear>")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": s}} for s in sections]
    if links:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(links)}]})
    text = f"{culprit} broke PR #{pr_info['number']} at {sha7}." + (" The fix passes all tests." if fixed(patch) else "")
    return text + (f" {comment_url}" if comment_url else ""), blocks


def linear_title(pr_info, blame, patch):
    return f'{culprit_label(blame, patch)} breaks the tests in "{pr_info["title"]}"'


def linear_description(pr_info, blame, patch, path_summary, comment_url):
    title = pr_info["title"].replace("[", "(").replace("]", ")")
    failure = failure_text(blame)
    lines = [f"**[PR #{pr_info['number']} {title}]({pr_info['url']}) by {pr_info['author']} broke the chip tests.**", "",
             "## What broke", f"{failure[:1].upper()}{failure[1:]}. Culprit: `{culprit_label(blame, patch)}`.", "",
             "## Why we know"]
    lines += [f"- **{f.trust.capitalize()}**: {f.text}" for f in blame.facts]
    if fixed(patch):
        lines.append("- **Confirmed**: with the fix applied, all tests pass.")
    if patch and patch.get("replacement"):
        lines += ["", "## The fix" if fixed(patch) else "## Proposed fix (it did not pass the tests)",
                  "```diff", diff_text(blame, patch), "```"]
        if fixed(patch):
            lines.append(f"Made by: {how_made(patch)}. {blame.sims} simulations in total.")
    if path_summary:
        lines += ["", "## How the bug travelled", path_summary]
    lines += ["", "## Links", f"- Pull request: {pr_info['url']}"]
    if comment_url:
        lines.append(f"- Review comment: {comment_url}")
    return "\n".join(lines)


def publish(pr_info, blame, patch=None, path_summary=None):
    """Returns a dict of what was created vs found, for the run record and the replay check.

    An app whose token is missing is skipped and reported, so GitHub can be exercised alone.
    """
    cfg = http.config()
    pr, sha = pr_info["number"], pr_info["head_sha"]
    blame.sha = sha
    out = {"created": 0, "unchanged": 0, "skipped": [a for a in ("github", "linear", "slack") if not http.has_token(a)]}
    kill_rate = sweep_kill_rate()
    line = blame_line(blame, patch)
    if "github" in out["skipped"]:
        return out

    comment, created = github.upsert_review_comment(pr, sha, design.get()["rtl_file"], line,
                                                    comment_body(pr, blame, patch, path_summary, kill_rate))
    out["created" if created else "unchanged"] += 1
    out["comment_url"] = comment.get("html_url")
    state = "failure" if blame.kind in ("rtl", "interaction", "non_rtl") else "error"
    github.set_status(sha, state, f"culprit: {design.get()['rtl_file']}:{line}" if blame.kind != "non_rtl" else "culprit: not an RTL bug",
                      out["comment_url"])

    issue = None
    if "linear" not in out["skipped"]:
        assignee = cfg["linear"]["users"].get(pr_info["author"]) or None
        issue, created = linear.upsert_issue(pr, linear_title(pr_info, blame, patch),
                                             linear_description(pr_info, blame, patch, path_summary, out["comment_url"]),
                                             assignee)
        out["created" if created else "unchanged"] += 1
        out["linear"] = {"identifier": issue["identifier"], "url": issue["url"], "state": issue["state"]["name"]}

    permalink = None
    if "slack" not in out["skipped"]:
        fields = parent_fields(pr_info, blame, patch, issue)
        text, blocks = slack_parent(fields)
        ts, created = slack.upsert_parent(pr, text, blocks, fields)
        out["created" if created else "unchanged"] += 1
        reply_text, reply_blocks = slack_reply(pr_info, blame, patch, out["comment_url"], issue)
        replied = slack.reply_once(pr, ts, sha[:12], reply_text, reply_blocks)
        out["created" if replied else "unchanged"] += 1
        out["slack_ts"] = ts
        permalink = slack.permalink(ts)

    if issue:
        note = (f"Analysed commit `{sha[:7]}`: culprit `{culprit_label(blame, patch)}`, "
                f"{status_word(blame, patch).lower()}, {blame.sims} simulations.")
        if permalink:
            note += f"\n\nSlack thread: {permalink}"
        linear.comment_once(pr, issue["id"], f"culprit:{pr}@{sha[:12]}", note)
    return out


def resolve(pr_info, green_sha):
    """CI is green: note it and close the Linear issue, redraw the Slack parent as Resolved, set a success status."""
    pr = pr_info["number"]
    out = {"linear_closed": False}
    if http.has_token("linear"):
        issues = linear.find(pr)
        if issues:
            linear.comment_once(pr, issues[0]["id"], f"culprit:{pr}:resolved:{green_sha[:12]}",
                                f"Resolved: CI is green on `{green_sha[:7]}`.")
            out["linear_closed"] = linear.close(pr)
    if http.has_token("slack"):
        parent = slack._find(str(pr))
        if parent:
            fields = dict(((parent.get("metadata") or {}).get("event_payload") or {}), status="Resolved", green=green_sha)
            if "title" in fields:          # a parent without stored fields has nothing to redraw from
                text, blocks = slack_parent(fields)
                slack.upsert_parent(pr, text, blocks, fields)
            slack.reply_once(pr, parent["ts"], f"resolved:{green_sha[:12]}", f"Resolved: CI is green on {green_sha[:7]}.",
                             [{"type": "section", "text": {"type": "mrkdwn",
                                                           "text": f"*Resolved*\nCI is green on commit `{green_sha[:7]}`."}}])
    if http.has_token("github"):
        github.set_status(green_sha, "success", "culprit: regression passes")
    return out
