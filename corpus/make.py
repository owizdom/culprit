"""Build the evaluation corpus from corpus/cases.yaml and check that every case behaves as labeled.

Each case is a set of exact-text edits to the PicoRV32 checkout. Ground truth comes from the edit
spec itself (which edit carries the bug, which line fixes it), never from what CULPRIT answers.
truth.json is written next to each case; only evals/ may read it.

  python corpus/make.py            build every case and simulate it
  python corpus/make.py --check    print kept/total per category and exit non-zero on a mismatch
  python corpus/make.py --sweep 40 seeded single-token mutants, to report how many the regression catches
"""
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localize import hunks as H  # noqa: E402
from sim import icarus  # noqa: E402

ROOT = Path(__file__).resolve().parent
CHECKOUT = Path(os.environ.get("CULPRIT_CHIP_REPO") or ROOT.parent.parent / "picorv32-ci")


def load_cases():
    return yaml.safe_load((ROOT / "cases.yaml").read_text())


def apply_edits(base_tree, edits):
    tree = dict(base_tree)
    for e in edits:
        text = tree[e["path"]]
        count = text.count(e["find"])
        if count != 1:
            raise ValueError(f"{e['path']}: find text matches {count} times, needs exactly 1: {e['find'][:60]!r}")
        tree[e["path"]] = text.replace(e["find"], e["replace"])
    return tree


def line_of(text, needle):
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return None


def truth_for(case, base_tree, head_tree):
    t = dict(case.get("truth") or {})
    t["category"] = case["category"]
    hs = H.parse_tree(base_tree, head_tree)
    t["hunks"] = [{"id": h.id, "path": h.path, "new_start": h.new_start, "changed": sorted(h.changed_head_lines())}
                  for h in hs]
    if "bug_contains" in t:
        needles = t["bug_contains"] if isinstance(t["bug_contains"], list) else [t["bug_contains"]]
        lines = [line_of(head_tree["picorv32.v"], n) for n in needles]
        t["bug_lines"] = [l for l in lines if l]
        t["bug_hunks"] = sorted({h.id for h in hs if h.path == "picorv32.v" and set(t["bug_lines"]) & h.changed_head_lines()})
    return t


def build(case, base_tree, base_result):
    head_tree = apply_edits(base_tree, case["edits"])
    out = ROOT / "cases" / case["id"]
    for path in {e["path"] for e in case["edits"]}:
        p = out / "head" / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(head_tree[path])
    truth = truth_for(case, base_tree, head_tree)
    result = icarus.run(head_tree)
    expected_fail = case["category"] != "not_reproduced"
    ok = (not result.passed) == expected_fail and base_result.passed
    if truth.get("bug_contains") and not truth.get("bug_lines"):
        ok = False
    sim = {"head_status": result.status, "failure": result.failing_tests or result.trap_reason,
           "trap_cycle": result.trap_cycle, "seconds": round(result.seconds, 1), "kept": ok}
    (out / "truth.json").write_text(json.dumps(truth, indent=2))
    (out / "sim.json").write_text(json.dumps(sim, indent=2))
    meta = {k: case[k] for k in ("id", "category", "title", "body") if k in case}
    (out / "case.json").write_text(json.dumps(meta, indent=2))
    return case["id"], case["category"], ok, sim


def sweep(base_tree, n, seed=7):
    """Single-token mutants on core lines outside formal/debug-only code. Reports how many the regression kills."""
    text = base_tree["picorv32.v"]
    lines = text.splitlines(keepends=True)
    skip, depth = set(), 0
    for i, line in enumerate(lines):
        if depth == 0 and re.match(r"\s*`ifdef\s+(RISCV_FORMAL|FORMAL|DEBUGASM|DEBUG|PICORV32_TESTBUG\w*)\b", line):
            depth = 1
        elif depth and re.match(r"\s*`if(n?def)\b", line):
            depth += 1                              # a nested ifdef inside code that is not compiled
        if depth:
            skip.add(i)
            if re.match(r"\s*`endif", line):
                depth -= 1
    swaps = [(" == ", " != "), (" != ", " == "), (" + ", " - "), (" - ", " + "), (" & ", " | "), (" | ", " & "),
             (" < ", " <= "), (" <= ", " < "), ("[0]", "[1]"), ("[1]", "[0]")]
    core_start = line_of(text, "module picorv32 #(") or 0
    core_end = line_of(text, "module picorv32_regs") or len(lines)
    candidates = [(i, a, b) for i, line in enumerate(lines) if core_start <= i < core_end and i not in skip
                  and not line.strip().startswith("//") for a, b in swaps if a in line]
    random.Random(seed).shuffle(candidates)
    mutants = []
    for i, a, b in candidates[:n]:
        mutated = list(lines)
        mutated[i] = mutated[i].replace(a, b, 1)
        mutants.append((i + 1, a.strip(), b.strip(), dict(base_tree, **{"picorv32.v": "".join(mutated)})))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda m: icarus.run(m[3]), mutants))
    killed = sum(1 for r in results if not r.passed)
    detail = [{"line": line, "from": a, "to": b, "killed": not r.passed, "status": r.status}
              for (line, a, b, _), r in zip(mutants, results)]
    (ROOT / "sweep.json").write_text(json.dumps({"killed": killed, "total": len(mutants), "mutants": detail}, indent=2))
    return killed, len(mutants)


def main(argv):
    base_tree = icarus.read_tree(CHECKOUT)
    if "--sweep" in argv:
        n = int(argv[argv.index("--sweep") + 1])
        killed, total = sweep(base_tree, n)
        print(f"regression kills {killed}/{total} seeded single-token mutants")
        return
    base_result = icarus.run(base_tree)
    if not base_result.passed:
        sys.exit("base checkout does not pass the regression; fix that first")
    cases = load_cases()
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda c: build(c, base_tree, base_result), cases))
    by_cat = {}
    for cid, cat, ok, sim in rows:
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        by_cat[cat][0] += ok
        print(f"{'kept' if ok else 'DROP'}  {cid:<8} {cat:<22} head={sim['head_status']:<13} {sim['failure']} @ {sim['trap_cycle']}")
    print()
    for cat, (kept, total) in sorted(by_cat.items()):
        print(f"{cat:<24} kept {kept}/{total}")
    if "--check" in argv and any(kept != total for kept, total in by_cat.values()):
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
