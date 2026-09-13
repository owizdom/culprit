"""Run the three arms on every corpus case and grade them against the simulator and truth.json.

  A  LLM only: one call with the PR description, the diff and the CI log tail; no simulator, no graph.
  B  Delta debugging plus the circuit-identity proof: no model at all.
  C  CULPRIT: B, then a line-level fix (deterministic restore, or Claude with simulator feedback).

  uv run python evals/run.py --arms A,B,C [--cases a01,m07] [--run 2] [--corpus corpus/history/cases --tag history]
Results are appended to results/raw/<arm>[.<tag>][.run<N>].jsonl and resumed on rerun. Needs the picorv32-ci checkout
(CULPRIT_CHIP_REPO, default ../picorv32-ci next to this repository).
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localize import confirm  # noqa: E402
from localize import hunks as H  # noqa: E402
from sim import icarus  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "corpus" / "cases"
RAW = ROOT / "results" / "raw"
CHECKOUT = Path(os.environ.get("CULPRIT_CHIP_REPO") or ROOT.parent / "picorv32-ci")
SUFFIX = ""     # ".history", ".run2", ...: which result file a run appends to


def load_case(case_dir, base_tree):
    head = dict(base_tree)
    for p in (case_dir / "head").rglob("*"):
        if p.is_file():
            head[p.relative_to(case_dir / "head").as_posix()] = p.read_text()
    meta = json.loads((case_dir / "case.json").read_text())
    truth = json.loads((case_dir / "truth.json").read_text())
    return head, meta, truth


def apply_fix(head_tree, fix):
    if not fix or not fix.get("start"):
        return None
    lines = head_tree["picorv32.v"].splitlines(keepends=True)
    for e in sorted(fix.get("edits") or [fix], key=lambda e: e["start"], reverse=True):
        replacement = e["replacement"]
        if replacement and not replacement.endswith("\n"):
            replacement += "\n"
        lines[e["start"] - 1:e["end"]] = replacement.splitlines(keepends=True)
    return dict(head_tree, **{"picorv32.v": "".join(lines)})


def grade(arm, case_id, meta, truth, base_tree, head_tree, answer):
    """answer: {kind, hunks, line, fix, confirmed, sims, seconds, usd}"""
    cat = truth["category"]
    row = {"arm": arm, "case": case_id, "category": cat, **{k: answer.get(k) for k in ("kind", "line", "sims", "seconds", "usd")}}
    if cat == "not_reproduced":
        row["null_correct"] = answer.get("kind") in ("not_reproduced", None)
        # Arm A's schema makes it name a line even when it says not_reproduced; that is not a blame.
        row["wrong_blame"] = answer.get("kind") in ("rtl", "interaction") and bool(answer.get("confirmed") or answer.get("line"))
        return row
    if cat == "non_rtl":
        row["null_correct"] = answer.get("kind") == "non_rtl" and answer.get("file") == truth.get("culprit_file")
        row["wrong_blame"] = answer.get("kind") in ("rtl", "interaction") and bool(answer.get("confirmed") or answer.get("line"))
        return row
    bug_lines = set(truth.get("bug_lines") or [])
    bug_hunks = set(truth.get("bug_hunks") or [])
    predicted = set(answer.get("hunks") or [])
    if not predicted and answer.get("line"):
        # Arm A names a line, not an edit: the edit it blames is whichever one changed that line.
        predicted = {h["id"] for h in truth["hunks"] if h["path"] == "picorv32.v" and answer["line"] in h["changed"]}
    row["predicted_hunks"], row["fix"] = sorted(predicted), answer.get("fix")
    # A culprit may be a group of edits that only compile together (a rename across several places);
    # finding it means every buggy edit is inside what was blamed.
    row["hunk_loc"] = bool(bug_hunks) and bug_hunks <= predicted
    row["line_loc"] = answer.get("line") in bug_lines if answer.get("line") else False
    asserted = answer.get("confirmed") if arm != "A" else bool(answer.get("line"))
    row["wrong_blame"] = bool(asserted) and not (row["line_loc"] or predicted & bug_hunks)
    has_intent = bool(truth.get("intended_contains"))
    fixed_tree = apply_fix(head_tree, answer.get("fix"))
    if fixed_tree is None:
        if cat == "upstream_bug":
            row["matches_upstream"] = False
        row["validated_fix"] = False
        row["intent_fix"] = False if has_intent else None     # no intended work to keep: not scored
        return row
    result = icarus.run(fixed_tree)
    row["validated_fix"] = result.passed
    text = fixed_tree["picorv32.v"]
    keeps_intent = all(s in text for s in truth.get("intended_contains") or [])
    fix = answer["fix"]
    inside = all(any(h["new_start"] <= e["start"] and e["end"] <= max(h["changed"] or [h["new_start"]])
                     for h in truth["hunks"] if h["id"] in bug_hunks)
                 for e in fix.get("edits") or [fix]) if bug_hunks else False
    row["intent_fix"] = bool(result.passed and keeps_intent and inside and text != base_tree["picorv32.v"]) \
        if has_intent else None
    if cat == "upstream_bug":
        # The bug was re-introduced into today's file, so the upstream fix is exactly today's file.
        row["matches_upstream"] = bool(result.passed and text == base_tree["picorv32.v"])
    return row


def arm_b(base_tree, head_tree, meta, with_repair=False):
    started = time.time()
    blame = confirm.localize(base_tree, head_tree)
    culprit = sorted(set().union(*blame.culprit)) if blame.culprit else []
    line = blame.line_fix["line"] if blame.line_fix else None
    if line is None and culprit:
        changed = sorted(set().union(*(h.changed_head_lines() for h in blame.hunks if h.id in culprit)))
        line = changed[0] if changed else None
    fix, usd = None, 0.0
    if blame.line_fix:
        fix = {"start": blame.line_fix["line"], "end": blame.line_fix["line"], "replacement": blame.line_fix["replacement"]}
    elif culprit and not with_repair:
        rtl = [h for h in blame.hunks if h.id in culprit and h.path == "picorv32.v"]
        if len(rtl) == 1:
            h = rtl[0]
            fix = {"start": h.new_start, "end": h.new_start + len(h.new) - 1, "replacement": "".join(h.old)}
    if with_repair and culprit:
        # Arm C is the shipped repair path, called as the product calls it.
        import culprit as product
        intent = f"{meta.get('title', '')}\n\n{meta.get('body', '')}"
        patch = product.repair(base_tree, head_tree, blame, intent, emit=lambda *a, **k: None) or {}
        usd = patch.get("cost_usd", 0.0)
        fix = None
        if patch.get("start"):
            fix = {"start": patch["start"], "end": patch["end"], "replacement": patch["replacement"],
                   "edits": patch.get("edits")}
            line = patch["start"]
    return {"kind": blame.kind, "hunks": culprit, "line": line, "fix": fix, "file": blame.culprit_file,
            "confirmed": bool(blame.culprit) or blame.kind == "non_rtl", "sims": blame.sims,
            "seconds": round(time.time() - started, 1), "usd": usd}


A_SCHEMA = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["rtl", "non_rtl", "not_reproduced"]},
    "file": {"type": "string"}, "line": {"type": "integer"},
    "start_line": {"type": "integer"}, "end_line": {"type": "integer"}, "replacement": {"type": "string"}},
    "required": ["kind", "file", "line", "start_line", "end_line", "replacement"], "additionalProperties": False}


def arm_a(base_tree, head_tree, meta, ci_log):
    import os

    import anthropic

    from repair import budget
    budget._env()
    budget.check("arm A call")
    started = time.time()
    diff = "".join(
        f"--- {h.path} @@ -{h.old_start} +{h.new_start} @@\n" + "".join("-" + l for l in h.old) + "".join("+" + l for l in h.new)
        for h in H.parse_tree(base_tree, head_tree))
    msg = (f"A pull request to PicoRV32 (a RISC-V CPU in Verilog) broke the simulation regression.\n\n"
           f"Title: {meta.get('title')}\nDescription: {meta.get('body')}\n\nDiff:\n{diff}\n\nCI log tail:\n{ci_log}\n\n"
           "Say whether the RTL (picorv32.v) is at fault, which file and head line carries the bug, and give a "
           "replacement for a range of head lines that fixes it while keeping the intended change.")
    resp = anthropic.Anthropic().beta.messages.create(
        model="claude-opus-5", max_tokens=12000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": os.environ.get("CULPRIT_EFFORT", "medium"),
                       "format": {"type": "json_schema", "schema": A_SCHEMA}},
        messages=[{"role": "user", "content": msg}])
    usd = budget.record("claude-opus-5", resp.usage, "arm A")
    if resp.stop_reason == "refusal":
        return {"kind": None, "seconds": round(time.time() - started, 1), "usd": usd, "sims": 0}
    out = json.loads(next(b.text for b in resp.content if b.type == "text"))
    fix = {"start": out["start_line"], "end": out["end_line"], "replacement": out["replacement"]} \
        if out["kind"] == "rtl" and out["file"] == "picorv32.v" else None
    return {"kind": out["kind"], "file": out["file"], "line": out["line"] if out["file"] == "picorv32.v" else None,
            "fix": fix, "confirmed": False, "sims": 0, "seconds": round(time.time() - started, 1), "usd": usd}


def done(arm):
    path = RAW / f"{arm}{SUFFIX}.jsonl"
    return {json.loads(l)["case"] for l in path.read_text().splitlines()} if path.exists() else set()


def regrade():
    """Recompute localization and blame from stored rows and truth.json, without simulating again.
    Fix validity is kept as recorded, since that part needed the simulator."""
    for path in sorted(RAW.glob("*.jsonl")):
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        for row in rows:
            answer_key = CASES / row["case"] / "truth.json"
            if not answer_key.exists():          # a row from another corpus (--corpus); regrade that one separately
                continue
            truth = json.loads(answer_key.read_text())
            if truth["category"] in ("non_rtl", "not_reproduced"):
                row["wrong_blame"] = row.get("kind") in ("rtl", "interaction") and bool(row.get("line"))
                continue
            bug_lines, bug_hunks = set(truth.get("bug_lines") or []), set(truth.get("bug_hunks") or [])
            predicted = set(row.get("predicted_hunks") or [])
            if not predicted and row.get("line"):
                predicted = {h["id"] for h in truth["hunks"] if h["path"] == "picorv32.v" and row["line"] in h["changed"]}
            row["predicted_hunks"] = sorted(predicted)
            row["hunk_loc"] = bool(bug_hunks) and bug_hunks <= predicted
            row["line_loc"] = row.get("line") in bug_lines if row.get("line") else False
            asserted = row["arm"] == "A" and bool(row.get("line")) or row["arm"] != "A" and row.get("kind") in ("rtl", "interaction")
            row["wrong_blame"] = bool(asserted) and not (row["line_loc"] or predicted & bug_hunks)
            if not truth.get("intended_contains"):
                row["intent_fix"] = None
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"regraded {len(rows)} rows in {path.name}")


def main(argv):
    global CASES, SUFFIX
    if "--regrade" in argv:
        return regrade()
    if "--corpus" in argv:
        CASES = ROOT / argv[argv.index("--corpus") + 1]
    tags = [argv[argv.index("--tag") + 1]] if "--tag" in argv else []
    if "--run" in argv:
        tags.append(f"run{argv[argv.index('--run') + 1]}")
    SUFFIX = "".join(f".{t}" for t in tags)
    arms = argv[argv.index("--arms") + 1].split(",") if "--arms" in argv else ["B", "C"]
    only = set(argv[argv.index("--cases") + 1].split(",")) if "--cases" in argv else None
    base_tree = icarus.read_tree(CHECKOUT)
    RAW.mkdir(parents=True, exist_ok=True)
    for case_dir in sorted(CASES.iterdir()):
        sim = json.loads((case_dir / "sim.json").read_text()) if (case_dir / "sim.json").exists() else {}
        if not sim.get("kept") or (only and case_dir.name not in only):
            continue
        head, meta, truth = load_case(case_dir, base_tree)
        for arm in arms:
            if case_dir.name in done(arm):
                continue
            if arm == "A":
                log_tail = "\n".join(icarus.run(head).log.strip().splitlines()[-60:])
                answer = arm_a(base_tree, head, meta, log_tail)
            elif arm == "B":
                answer = arm_b(base_tree, head, meta)
            else:
                answer = arm_b(base_tree, head, meta, with_repair=True)
            row = grade(arm, case_dir.name, meta, truth, base_tree, head, answer)
            with open(RAW / f"{arm}{SUFFIX}.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
            print(json.dumps(row))


if __name__ == "__main__":
    main(sys.argv)
