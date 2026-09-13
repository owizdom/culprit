"""A line-level fix that keeps the author's change: Claude proposes, the simulator decides.

A proposal is only CONFIRMED when all of these hold:
  - it only touches lines inside the culprit edit,
  - the edit does not simply go back to the base version (a revert is not a repair),
  - it compiles, and the regression prints ALL TESTS PASSED. with every other edit still applied.
Otherwise the simulator's answer is fed back, up to three attempts, then it stays PROPOSED.
"""
import json
import os
import re

import anthropic

import design
from localize.confirm import describe
from repair import budget
from sim import icarus

MODEL = "claude-opus-5"
MAX_TOKENS = 12000

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "replacement": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["start_line", "end_line", "replacement", "explanation"],
    "additionalProperties": False,
}

SYSTEM_TEMPLATE = """You fix regressions in {name}, {description} ({rtl_file}).

A pull request broke the simulation regression. Undoing one edit of that pull request makes the
regression pass again, so the bug is inside that edit. The rest of the edit is intended work that
the author wants to keep.

Return the smallest replacement for a range of head lines inside that edit that fixes the bug and
keeps the author's intent. Do not simply restore the old version of the whole edit. start_line and
end_line are inclusive 1-based line numbers in the head file; replacement is the full new text for
those lines (it may be more or fewer lines). explanation is one or two plain sentences on what was
wrong. You will be told the simulator's verdict on each attempt."""


def system_prompt(cfg=None):
    """The system prompt for the configured design (design.py)."""
    cfg = cfg or design.get()
    return SYSTEM_TEMPLATE.format(name=cfg["name"], description=cfg["description"], rtl_file=cfg["rtl_file"])


SYSTEM = system_prompt(design.DEFAULTS)


def normalized(text):
    """Verilog with comments and all whitespace removed, so a revert padded with a comment is still a revert."""
    text = re.sub(r"//.*$", "", text, flags=re.M)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"\s+", "", text)


def numbered(lines, start):
    return "".join(f"{start + i:5d}  {line}" for i, line in enumerate(lines))


def prompt(intent, culprit_hunks, other_verdicts, failure):
    parts = [f"Pull request intent:\n{intent or '(no description)'}\n",
             f"Regression result at head: {failure}\n"]
    for h in culprit_hunks:
        parts.append(f"Culprit edit (head lines {h.new_start}-{h.new_start + len(h.new) - 1}):\n"
                     f"{numbered(h.new, h.new_start)}\nSame region before the pull request:\n{numbered(h.old, h.old_start)}")
    if other_verdicts:
        parts.append("Other edits in this pull request and what the tools found:\n" +
                     "\n".join(f"  edit at line {line}: {verdict}" for line, verdict in other_verdicts))
    return "\n\n".join(parts)


def check(patch, head_text, base_tree, head_tree, culprit_hunks):
    """Apply a proposal and let the simulator judge it. Returns (tree or None, reason, result)."""
    lines = head_text.splitlines(keepends=True)
    start, end = patch["start_line"], patch["end_line"]
    inside = any(h.new_start <= start and end <= h.new_start + len(h.new) - 1 for h in culprit_hunks)
    if not inside or start > end:
        return None, f"lines {start}-{end} are outside the culprit edit", None
    replacement = patch["replacement"]
    if replacement and not replacement.endswith("\n"):
        replacement += "\n"
    new_lines = lines[:start - 1] + replacement.splitlines(keepends=True) + lines[end:]
    new_text = "".join(new_lines)
    delta = len(new_lines) - len(lines)
    for h in culprit_hunks:
        region = "".join(new_lines[h.new_start - 1:h.new_start - 1 + len(h.new) + delta])
        if normalized(region) == normalized("".join(h.old)):
            return None, "that restores the old version of the edit (comments and whitespace aside); keep the author's change and fix only the bug", None
    tree = dict(head_tree, **{design.get()["rtl_file"]: new_text})
    result = icarus.run(tree)
    if result.status == "compile_error":
        return None, "it does not compile:\n" + result.log[-1500:], result
    if not result.passed:
        return None, f"the regression still fails: {describe(result)}", result
    return tree, "ALL TESTS PASSED.", result


def fix(base_tree, head_tree, blame, intent="", max_attempts=3, emit=lambda *a, **k: None):
    rtl_file = design.get()["rtl_file"]
    rtl = [h for h in blame.hunks if h.path == rtl_file]
    culprit_ids = set().union(*blame.culprit)
    culprit_hunks = [h for h in rtl if h.id in culprit_ids]
    others = [(h.new_start, blame.verdicts.get(h.id, "untested")) for h in rtl if h.id not in culprit_ids]
    budget._env()
    client = anthropic.Anthropic()
    effort = os.environ.get("CULPRIT_EFFORT", "medium")
    history = [{"role": "user", "content": prompt(intent, culprit_hunks, others, describe(blame.head))}]
    usage = {"input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    last, reason = None, "no attempt made"
    for attempt in range(1, max_attempts + 1):
        try:
            budget.check("repair attempt")
        except budget.OverBudget as e:
            return _out(last, "proposed", str(e), attempt - 1, usage)
        emit("repair", "start", f"attempt {attempt}: asking {MODEL} for a line-level fix")
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": PATCH_SCHEMA}},
            system=system_prompt(), messages=history)
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        usage["usd"] += budget.record(MODEL, resp.usage, "repair")
        if resp.stop_reason == "refusal":
            return _out(last, "proposed", "the model declined", attempt, usage)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            patch = json.loads(text)
        except json.JSONDecodeError:
            patch, reason, result = None, "the answer was not valid JSON for the schema", None
        else:
            _, reason, result = check(patch, head_tree[rtl_file], base_tree, head_tree, culprit_hunks)
            last = patch
            if reason == "ALL TESTS PASSED.":
                emit("repair", "done", f"fix at lines {patch['start_line']}-{patch['end_line']} passes the regression",
                     "confirmed", 1)
                return _out(patch, "confirmed", reason, attempt, usage, result)
        emit("repair", "fail", f"attempt {attempt} rejected: {reason.splitlines()[0]}", "proposed", 1 if result else 0)
        history += [{"role": "assistant", "content": resp.content},
                    {"role": "user", "content": f"Simulator verdict: {reason}\nTry again."}]
    return _out(last, "proposed", reason, max_attempts, usage)


def _out(patch, trust, reason, attempts, usage, result=None):
    out = {"trust": trust, "reason": reason, "attempts": attempts, "usage": usage, "cost_usd": round(usage["usd"], 4),
           "result": result.status if result else None}
    if patch:
        out.update({"path": design.get()["rtl_file"], "start": patch["start_line"], "end": patch["end_line"],
                    "replacement": patch["replacement"], "explanation": patch["explanation"]})
    return out
