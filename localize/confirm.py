"""Which part of a change broke the regression, confirmed by the simulator.

The simulator is the only thing that can CONFIRM. The netlist proof may only skip a simulation.
"""
import difflib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import combinations

import design
from localize import hunks as H
from localize import inert
from sim import icarus

RTL_FILES = {design.DEFAULTS["rtl_file"]}     # the default design; localize() reads design.get()
MAX_PAIRS = 10


@dataclass
class Fact:
    trust: str          # confirmed | proposed
    text: str
    path: str | None = None
    line: int | None = None


@dataclass
class Blame:
    kind: str           # rtl | non_rtl | interaction | not_reproduced | base_fails | unresolved
    hunks: list
    groups: list = field(default_factory=list)
    culprit: list = field(default_factory=list)        # list of frozenset(hunk ids)
    inert: dict = field(default_factory=dict)          # group -> method
    verdicts: dict = field(default_factory=dict)       # hunk id -> inert|culprit|benign|unresolved|untested
    facts: list = field(default_factory=list)
    line_fix: dict | None = None                       # deterministic single-line fix, if one exists
    culprit_file: str | None = None
    sims: int = 0
    head: object = None
    base: object = None


def _quiet(*args, **kwargs):
    pass


def localize(base_tree, head_tree, emit=_quiet):
    all_hunks = H.parse_tree(base_tree, head_tree)
    blame = Blame(kind="unresolved", hunks=all_hunks)
    pool = ThreadPoolExecutor(max_workers=6)

    def sim(tree):
        blame.sims += 1
        return icarus.run(tree)

    emit("reproduce", "start", "simulating the PR head and its merge base")
    head_f, base_f = pool.submit(sim, head_tree), pool.submit(sim, base_tree)
    blame.head, blame.base = head_f.result(), base_f.result()
    head, base = blame.head, blame.base
    if head.passed:
        blame.kind = "not_reproduced"
        blame.facts.append(Fact("confirmed", "the regression passes at head locally; nothing to localize"))
        emit("reproduce", "fail", "head passes locally: not reproduced", "confirmed", blame.sims)
        return blame
    if not base.passed:
        blame.kind = "base_fails"
        blame.facts.append(Fact("confirmed", "the merge base already fails; this PR did not introduce it"))
        emit("reproduce", "fail", "base already fails", "confirmed", blame.sims)
        return blame
    blame.facts.append(Fact("confirmed", f"head fails ({describe(head)}); merge base passes"))
    emit("reproduce", "done", f"head fails ({describe(head)}), base passes", "confirmed", blame.sims)

    rtl_file = design.get()["rtl_file"]
    rtl = [h for h in all_hunks if h.path == rtl_file]
    other = [h for h in all_hunks if h.path != rtl_file]
    if other and not rtl:
        blame.kind = "non_rtl"
        blame.culprit_file = _narrow_file(base_tree, all_hunks, other, sim, pool)
        blame.facts.append(Fact("confirmed", f"only non-RTL files changed; the failure comes from {blame.culprit_file or 'them'}",
                                blame.culprit_file))
        emit("rule_out", "done", f"no RTL changed: failure is in {blame.culprit_file or 'tests/firmware'}", "confirmed", blame.sims)
        return blame
    if other:
        only_rtl = H.revert_tree(base_tree, all_hunks, {h.id for h in other})
        only_other = H.revert_tree(base_tree, all_hunks, {h.id for h in rtl})
        a, b = pool.submit(sim, only_rtl), pool.submit(sim, only_other)
        a, b = a.result(), b.result()
        if a.passed and not b.passed:
            blame.kind = "non_rtl"
            blame.culprit_file = _narrow_file(base_tree, all_hunks, other, sim, pool)
            blame.facts.append(Fact("confirmed", "RTL changes alone pass; the non-RTL changes alone fail", blame.culprit_file))
            emit("rule_out", "done", f"RTL is fine; the failure comes from {blame.culprit_file}", "confirmed", blame.sims)
            return blame
        if a.passed and b.passed:
            blame.facts.append(Fact("proposed", "each side passes alone: the changed tests expose the RTL change"))
        elif not a.passed and not b.passed:
            blame.facts.append(Fact("confirmed", "both the RTL and the non-RTL changes fail on their own"))

    base_rtl, head_rtl = base_tree[rtl_file], head_tree[rtl_file]
    blame.groups = H.groups(rtl, base_tree, head_tree)
    emit("rule_out", "start", f"checking {len(blame.groups)} edit group(s) for circuit identity")
    candidates = []
    for g in blame.groups:
        without = H.apply(base_rtl, rtl, {h.id for h in rtl} - g)
        method = inert.same_netlist(without, head_rtl)
        if method in ("tokens_equal", "netlist_equal"):
            blame.inert[g] = method
            for hid in g:
                blame.verdicts[hid] = "inert"
            line = first_changed_line(rtl, g)
            why = "token stream identical after preprocessing" if method == "tokens_equal" else \
                "netlist identical under the testbench parameters"
            blame.facts.append(Fact("confirmed", f"the edit at {lines_label(rtl, g)} cannot change the simulated chip: {why}",
                                    rtl_file, line))
        else:
            candidates.append(g)
    emit("rule_out", "done", f"{len(blame.inert)} edit group(s) proven harmless, {len(candidates)} left to test",
         "confirmed", blame.sims, {"inert": [sorted(g) for g in blame.inert]})

    emit("confirm", "start", f"undoing each of {len(candidates)} edit group(s) and re-simulating")
    trees = {g: H.revert_tree(base_tree, all_hunks, g) for g in candidates}
    results = dict(zip(trees, pool.map(sim, trees.values())))
    for g, r in results.items():
        if r.passed:
            blame.culprit.append(g)
            for hid in g:
                blame.verdicts[hid] = "culprit"
        else:
            for hid in g:
                blame.verdicts[hid] = "unresolved" if r.status == "compile_error" else "benign"
    if not blame.culprit and len(candidates) >= 2:
        pairs = list(combinations(candidates, 2))[:MAX_PAIRS]
        trees = {a | b: H.revert_tree(base_tree, all_hunks, a | b) for a, b in pairs}
        for g, r in zip(trees, pool.map(sim, trees.values())):
            if r.passed:
                blame.culprit.append(g)
                for hid in g:
                    blame.verdicts[hid] = "culprit"
                break

    if not blame.culprit:
        blame.kind = "unresolved"
        blame.facts.append(Fact("proposed", "no single edit or pair explains the failure"))
        emit("confirm", "fail", "no edit explains the failure on its own", "proposed", blame.sims)
        return blame

    blame.kind = "interaction" if any(f.trust == "proposed" and "expose" in f.text for f in blame.facts) else "rtl"
    for g in blame.culprit:
        blame.facts.append(Fact("confirmed", f"undoing only the edit at {lines_label(rtl, g)} makes the regression pass",
                                rtl_file, first_changed_line(rtl, g)))
    emit("confirm", "done", f"culprit: the edit at {lines_label(rtl, blame.culprit[0])} (undo it and all tests pass)",
         "confirmed", blame.sims, {"culprit": [sorted(g) for g in blame.culprit]})

    if len(blame.culprit) == 1:
        blame.line_fix = _single_line_fix(base_tree, head_tree, rtl, blame.culprit[0], sim, pool)
        if blame.line_fix:
            blame.facts.append(Fact("confirmed", f"restoring only line {blame.line_fix['line']} makes the regression pass",
                                    rtl_file, blame.line_fix["line"]))
    pool.shutdown(wait=False)
    return blame


def _narrow_file(base_tree, all_hunks, other, sim, pool):
    paths = sorted({h.path for h in other})
    if len(paths) == 1:
        return paths[0]
    trees = {p: H.revert_tree(base_tree, all_hunks, {h.id for h in other if h.path == p}) for p in paths}
    for p, r in zip(trees, pool.map(sim, trees.values())):
        if r.passed:
            return p
    return None


def _single_line_fix(base_tree, head_tree, rtl, group, sim, pool):
    """Restore one changed line of the culprit edit at a time. If that alone passes and the edit still
    differs from base, the rest of the author's change is kept: a deterministic line-level fix."""
    rtl_file = design.get()["rtl_file"]
    head_lines = head_tree[rtl_file].splitlines(keepends=True)
    options = []
    for h in (h for h in rtl if h.id in group):
        matcher = difflib.SequenceMatcher(None, h.old, h.new, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "replace" and (i2 - i1) == (j2 - j1):
                for k in range(j2 - j1):
                    options.append((h.new_start + j1 + k, h.old[i1 + k]))
            elif tag == "insert":
                for k in range(j2 - j1):
                    options.append((h.new_start + j1 + k, ""))
    trees = {}
    for line, replacement in options:
        lines = list(head_lines)
        lines[line - 1] = replacement
        text = "".join(lines)
        if text != base_tree[rtl_file]:
            trees[(line, replacement)] = dict(head_tree, **{rtl_file: text})
    for (line, replacement), r in zip(trees, pool.map(sim, trees.values())):
        if r.passed:
            return {"line": line, "before": head_lines[line - 1], "replacement": replacement,
                    "method": "restore_line"}
    return None


def changed_lines(rtl, group):
    return sorted(set().union(*(h.changed_head_lines() for h in rtl if h.id in group)) or
                  {h.new_start for h in rtl if h.id in group})


def first_changed_line(rtl, group):
    return changed_lines(rtl, group)[0]


def lines_label(rtl, group):
    lines = changed_lines(rtl, group)
    if len(lines) == 1:
        return f"line {lines[0]}"
    if lines == list(range(lines[0], lines[-1] + 1)):
        return f"lines {lines[0]}-{lines[-1]}"
    return "lines " + ", ".join(map(str, lines[:6])) + ("..." if len(lines) > 6 else "")


def describe(result):
    cycle = f"{result.trap_cycle:,}" if result.trap_cycle is not None else "?"
    if result.failing_tests:
        return f"{result.failing_tests[0]}..ERROR at cycle {cycle}"
    reason = result.trap_reason or ""
    if reason.startswith("OUT-OF-BOUNDS MEMORY"):
        address = reason.rsplit(" ", 1)[-1]
        target = "an unknown address (x)" if "x" in address.lower() else f"0x{address}"
        verb = "wrote" if "WRITE" in reason else "read"
        return f"{verb} outside memory at {target}, cycle {cycle}"
    if reason:
        return f"{reason.lower()} at cycle {cycle}"
    if result.status == "timeout":
        return "timeout"
    return f"ERROR! at cycle {cycle}"
