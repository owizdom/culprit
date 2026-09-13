"""Build a corpus of real PicoRV32 bugs from the upstream author's own bug-fix commits.

For every commit in the chip repo whose message matches fix|bug and that touches picorv32.v, the bug is put back into
today's picorv32.v: each hunk's post-fix text (context + added lines) must occur exactly once in today's file and is
replaced by its pre-fix text (context + removed lines). A case is kept only if today's base passes the regression and
the head fails it (status fail or timeout). Ground truth comes from the upstream diff, never from what CULPRIT answers.
truth.json is written next to each case; only evals/ may read it.

  python corpus/history.py          screen every candidate, simulate, write corpus/history/
  python corpus/history.py --dry    stop before simulating; print which commits apply
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corpus.make import CHECKOUT  # noqa: E402
from localize import hunks as H  # noqa: E402
from sim import icarus  # noqa: E402

OUT = Path(__file__).resolve().parent / "history"
UPSTREAM = "https://github.com/YosysHQ/picorv32/commit/"
PRESCREEN_SECONDS = 300          # the testbench itself stops at 1,000,000 cycles (~30 s); longer means a hang
# `ifdef bodies the regression never compiles (testbench.v + picorv32.v, -DCOMPRESSED_ISA only).
EXCLUDED_IFDEF = re.compile(r"\s*`ifdef\s+(RISCV_FORMAL\w*|FORMAL|DEBUGASM|DEBUGNETS|DEBUGREGS|DEBUG|PICORV32_TESTBUG_\w+)\b")
DEBUG_MACRO_LINE = re.compile(r"\s*`debug\(.*\)\s*;?\s*$")   # `debug(...) expands to nothing without DEBUG

# Commits that apply to today's file but do not fix a functional bug. Judged by reading each diff; logged in
# screen.json and README.md, never silently dropped.
NOT_A_BUG_FIX = {
    "100e421": "license header only (author name in a block comment)",
    "d046cbf": "adds PICORV32_TESTBUG_nnn ifdefs; the compiled `else branches equal today's lines",
    "3495604": "re-indents picorv32_wb; subject says indenting",
    "aaa9e25": "adds the DEBUGNETS debug flag (matched 'bug' inside 'debug')",
    "e4312b0": "moves a wire declaration to silence a use-before-declaration warning",
    "ef86b30": "linter width padding ({16'b0, x}, 32'bx); same values after Verilog zero-extension",
}


def git(*args):
    return subprocess.run(["git", "-C", str(CHECKOUT), *args], capture_output=True, text=True, check=True).stdout


def candidates():
    out = git("log", "--format=%H%x09%ad%x09%s", "--date=short", "-i", "-E", "--grep=fix|bug", "--", "picorv32.v")
    return [dict(zip(("sha", "date", "subject"), line.split("\t", 2))) for line in out.splitlines() if line]


def diff_hunks(sha):
    """[[(tag, line text with newline)], ...] for picorv32.v in one commit, tags ' ', '-', '+'."""
    out = git("show", sha, "--format=", "-U3", "--", "picorv32.v")
    hunks, cur = [], None
    for raw in out.split("\n")[:-1] if out.endswith("\n") else out.split("\n"):
        if raw.startswith("@@"):
            cur = []
            hunks.append(cur)
        elif cur is None:
            continue
        elif raw.startswith("\\"):                  # "\ No newline at end of file" applies to the previous line
            if cur:
                cur[-1] = (cur[-1][0], cur[-1][1].rstrip("\n"))
        elif raw[:1] in (" ", "-", "+"):
            cur.append((raw[0], raw[1:] + "\n"))
        elif raw == "":                              # some git versions drop the space on empty context lines
            cur.append((" ", "\n"))
    return hunks


def side(hunk, tags):
    return "".join(text for tag, text in hunk if tag in tags)


def code_tokens(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r"\s+", "", text)


def excluded_lines(text):
    """1-based lines that the regression's build never compiles, plus blank/comment-only lines."""
    out, depth = set(), 0
    for i, line in enumerate(text.splitlines(), 1):
        if not code_tokens(line) or DEBUG_MACRO_LINE.match(line):
            out.add(i)
        if depth == 0:
            if EXCLUDED_IFDEF.match(line):
                depth = 1
                out.add(i)
            continue
        out.add(i)
        if re.match(r"\s*`if(n?def)\b", line):
            depth += 1
        elif re.match(r"\s*`endif\b", line):
            depth -= 1
        elif depth == 1 and re.match(r"\s*`(else|elsif)\b", line):
            depth = 0                                # the `else branch is compiled
    return out


def reintroduce(today, hunks):
    """Head text with every hunk reverted, or (None, reason). Also where each hunk landed in head and base."""
    spots = []
    for n, h in enumerate(hunks, 1):
        post = side(h, " +")
        count = today.count(post) if post else 0
        if count != 1:
            return None, f"hunk {n}/{len(hunks)}: post-fix text occurs {count} times in today's picorv32.v", None
        spots.append((today.index(post), post, side(h, " -"), h))
    spots.sort(key=lambda s: s[0])
    for a, b in zip(spots, spots[1:]):
        if a[0] + len(a[1]) > b[0]:
            return None, "hunks overlap in today's picorv32.v", None
    head, cursor, placed = [], 0, []
    for at, post, pre, h in spots:
        head.append(today[cursor:at])
        head_line = "".join(head).count("\n") + 1
        base_line = today[:at].count("\n") + 1
        head.append(pre)
        cursor = at + len(post)
        placed.append({"hunk": h, "head_line": head_line, "base_line": base_line, "pre": pre, "post": post})
    head.append(today[cursor:])
    return "".join(head), None, placed


def line_map(placed):
    """Head lines that the upstream fix removed (the re-introduced bug) and base lines it added."""
    bug_lines, fix_lines = [], []
    for p in placed:
        hl, bl = p["head_line"], p["base_line"]
        for tag, _ in p["hunk"]:
            if tag in " -":
                if tag == "-":
                    bug_lines.append(hl)
                hl += 1
            if tag in " +":
                if tag == "+":
                    fix_lines.append(bl)
                bl += 1
    return bug_lines, fix_lines


def prescreen(tree):
    """icarus.run's compile and vvp commands with a short wall-clock cap; nothing is cached."""
    started = time.time()
    hexfile, build_log = icarus.firmware(tree)
    if hexfile is None:
        return icarus.Result(status="build_error", passed=False, log=build_log)
    with tempfile.TemporaryDirectory() as tmp:
        icarus.export({"picorv32.v": tree["picorv32.v"], "testbench.v": tree["testbench.v"]}, tmp)
        comp = subprocess.run(["iverilog", "-DCOMPRESSED_ISA", "-s", "testbench", "-o", "tb.vvp", "testbench.v",
                               "picorv32.v"], cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        if comp.returncode != 0:
            return icarus.Result(status="compile_error", passed=False, log=comp.stderr[-4000:],
                                 seconds=time.time() - started)
        try:
            sim = subprocess.run(["vvp", "-N", "tb.vvp", f"+firmware={hexfile}"], cwd=tmp, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=PRESCREEN_SECONDS)
        except subprocess.TimeoutExpired:
            return icarus.Result(status="timeout", passed=False, trap_reason=f"wall clock > {PRESCREEN_SECONDS} s",
                                 seconds=time.time() - started)
    result = icarus.parse(sim.stdout + sim.stderr)
    result.seconds = time.time() - started
    return result


def truth_for(base_tree, head_tree, placed):
    bug_lines, _ = line_map(placed)
    hs = H.parse_tree(base_tree, head_tree)
    bug_hunks = {h.id for h in hs if h.path == "picorv32.v" and set(bug_lines) & h.changed_head_lines()}
    missing = False
    for p in placed:
        if not any(tag == "-" for tag, _ in p["hunk"]):   # the fix only added lines: the bug is code that is absent
            missing = True
            end = p["head_line"] + p["pre"].count("\n")
            bug_hunks |= {h.id for h in hs if h.path == "picorv32.v"
                          and h.new_start <= end and p["head_line"] <= h.new_start + len(h.new)}
    return {
        "category": "upstream_bug",
        "hunks": [{"id": h.id, "path": h.path, "new_start": h.new_start, "changed": sorted(h.changed_head_lines())}
                  for h in hs],
        "bug_lines": sorted(bug_lines),
        "bug_hunks": sorted(bug_hunks),
        "bug_is_missing_code": missing,
        "intended_contains": [],
        # Replacing head lines start..end with replacement undoes one upstream hunk; all of them give back base.
        "upstream_fix": [{"path": "picorv32.v", "start": p["head_line"],
                          "end": p["head_line"] + p["pre"].count("\n") - 1,
                          "bug_text": p["pre"], "replacement": p["post"]} for p in placed],
    }


def screen(base_tree):
    today = base_tree["picorv32.v"]
    base_excluded = excluded_lines(today)
    rows = []
    for c in candidates():
        row = {**c, "sha7": c["sha"][:7]}
        rows.append(row)
        hunks = diff_hunks(c["sha"])
        if not hunks:
            row["stage"], row["reason"] = "no_diff", "no picorv32.v hunks"
            continue
        head, why, placed = reintroduce(today, hunks)
        if head is None:
            flat = re.sub(r"\s+", "", today)
            row["stage"], row["reason"] = "does_not_apply", why
            row["applies_ignoring_whitespace"] = all(flat.count(re.sub(r"\s+", "", side(h, " +"))) == 1 for h in hunks)
            continue
        if row["sha7"] in NOT_A_BUG_FIX:
            row["stage"], row["reason"] = "not_a_bug_fix", NOT_A_BUG_FIX[row["sha7"]]
            continue
        if all(code_tokens(p["pre"]) == code_tokens(p["post"]) for p in placed):
            row["stage"], row["reason"] = "cosmetic", "only whitespace/comments change"
            continue
        bug_lines, fix_lines = line_map(placed)
        head_excluded = excluded_lines(head)
        if set(bug_lines) <= head_excluded and set(fix_lines) <= base_excluded:
            row["stage"], row["reason"] = "not_simulated_code", \
                "every changed line is a comment or inside RISCV_FORMAL/DEBUG*/TESTBUG ifdefs or `debug()"
            continue
        row["stage"], row["head_tree"], row["placed"] = "applied", dict(base_tree, **{"picorv32.v": head}), placed
    return rows


def write_case(row, base_tree, result):
    cid = f"u-{row['sha7']}"
    out = OUT / "cases" / cid
    (out / "head").mkdir(parents=True, exist_ok=True)
    (out / "head" / "picorv32.v").write_text(row["head_tree"]["picorv32.v"])
    meta = {"id": cid, "category": "upstream_bug", "title": f"{row['subject']} (reintroduced)",
            "body": "Brings part of picorv32.v back to an earlier upstream revision.",
            "commit": row["sha"], "url": UPSTREAM + row["sha"]}
    sim = {"head_status": result.status, "failure": result.failing_tests or result.trap_reason,
           "trap_cycle": result.trap_cycle, "seconds": round(result.seconds, 1), "kept": True}
    truth = truth_for(base_tree, row["head_tree"], row["placed"])
    truth["commit"] = row["sha"]
    (out / "case.json").write_text(json.dumps(meta, indent=2))
    (out / "sim.json").write_text(json.dumps(sim, indent=2))
    (out / "truth.json").write_text(json.dumps(truth, indent=2))
    return cid


def readme(rows, kept):
    stages = {}
    for r in rows:
        stages[r["stage"]] = stages.get(r["stage"], 0) + 1
    n = len(rows)
    missed = stages.get("does_not_apply", 0) + stages.get("no_diff", 0)
    applied = n - missed
    ws = sum(1 for r in rows if r.get("applies_ignoring_whitespace"))
    lines = [
        "# Upstream bug corpus",
        "",
        "Real PicoRV32 bugs, taken from the upstream author's own bug-fix commits and put back into today's",
        "`picorv32.v`. Built by `corpus/history.py`; per-candidate log in `screen.json`.",
        "",
        "## Method",
        "",
        "1. Candidates: `git log -i -E --grep='fix|bug' -- picorv32.v` in the picorv32-ci checkout.",
        "2. For each hunk of the commit's `picorv32.v` diff (`-U3`), the post-fix text (context + added lines) must",
        "   occur exactly once in today's file; it is replaced by the pre-fix text (context + removed lines). If any",
        "   hunk does not match exactly once the commit is skipped.",
        "3. Applied commits that are not functional fixes (read by hand, reasons in `history.py` `NOT_A_BUG_FIX`),",
        "   that change only whitespace/comments, or that change only code the regression never compiles",
        "   (`RISCV_FORMAL`, `DEBUG*`, `PICORV32_TESTBUG_*`) are not simulated.",
        f"4. Kept only if today's base passes the regression and the head fails it (status `fail` or `timeout`, not",
        f"   `compile_error`). A {PRESCREEN_SECONDS} s wall-clock pre-screen runs first; kept heads are then run through",
        "   `sim/icarus.py` `run`, whose result is what `sim.json` records.",
        "",
        f"Ignoring whitespace, {ws} of the {missed} commits that do not apply would apply; each of the others has a",
        "hunk whose post-fix code no longer exists in today's file.",
        "",
        "The case title is the upstream commit subject, which names the area of the bug. CULPRIT's evaluation",
        "passes the title to the agent, so these cases carry that hint.",
        "",
        "`truth.json` has the seeded corpus's fields (`category` is `upstream_bug`, `intended_contains` is empty) plus",
        "`upstream_fix`: per hunk, the head line range and the upstream post-fix text. Applying all of them gives",
        "today's `picorv32.v` back exactly.",
        "",
        "## Counts",
        "",
        f"| stage | commits |",
        f"|---|---|",
        f"| candidates | {n} |",
        f"| does not apply exactly once to today's file | {missed} |",
        f"| applied | {applied} |",
        f"| judged not a functional fix | {stages.get('not_a_bug_fix', 0)} |",
        f"| whitespace/comments only | {stages.get('cosmetic', 0)} |",
        f"| only code the regression does not compile | {stages.get('not_simulated_code', 0)} |",
        f"| head does not compile | {stages.get('compile_error', 0)} |",
        f"| head still passes the regression | {stages.get('passes', 0)} |",
        f"| kept (head fails the regression) | {len(kept)} |",
        "",
        "## Kept cases",
        "",
        "| dir | commit | subject | failure | trap cycle |",
        "|---|---|---|---|---|",
    ]
    for r, res in kept:
        failure = ", ".join(res.failing_tests) if res.failing_tests else (res.trap_reason or res.status)
        lines.append(f"| `cases/u-{r['sha7']}` | [{r['sha7']}]({UPSTREAM}{r['sha']}) | {r['subject']} | "
                     f"{res.status}: {failure} | {res.trap_cycle} |")
    lines += ["", "## Applied but not kept", "", "| commit | subject | why |", "|---|---|---|"]
    for r in rows:
        if r["stage"] in ("kept", "does_not_apply", "no_diff"):
            continue
        why = r.get("reason") or f"head status `{r['prescreen']['status']}`"
        lines.append(f"| [{r['sha7']}]({UPSTREAM}{r['sha']}) | {r['subject']} | {r['stage']}: {why} |")
    (OUT / "README.md").write_text("\n".join(lines) + "\n")


def main(argv):
    base_tree = icarus.read_tree(CHECKOUT)
    rows = screen(base_tree)
    counts = {}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
        print(f"{r['stage']:<20} {r['sha7']} {r['date']} {r['subject'][:60]:<60} {r.get('reason', '')}")
    print(f"\n{len(rows)} candidates: {counts}")
    if "--dry" in argv:
        return
    base = icarus.run(base_tree)
    if not base.passed:
        sys.exit(f"base checkout does not pass the regression ({base.status}); fix that first")
    applied = [r for r in rows if r["stage"] == "applied"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        pre = list(pool.map(lambda r: prescreen(r["head_tree"]), applied))
    survivors = []
    for r, res in zip(applied, pre):
        r["prescreen"] = {"status": res.status, "failure": res.failing_tests or res.trap_reason,
                          "trap_cycle": res.trap_cycle, "seconds": round(res.seconds, 1)}
        if res.status in ("fail", "timeout"):
            survivors.append(r)
        else:
            r["stage"] = {"pass": "passes"}.get(res.status, res.status)
    # Heads that finished inside the cap go through the real runner so sim.json (and its cache) match evals/run.py.
    slow = [r for r in survivors if r["prescreen"]["failure"] == f"wall clock > {PRESCREEN_SECONDS} s"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        official = list(pool.map(lambda r: icarus.run(r["head_tree"]), [r for r in survivors if r not in slow]))
    kept = []
    shutil.rmtree(OUT / "cases", ignore_errors=True)
    for r, res in zip([r for r in survivors if r not in slow], official):
        if res.status not in ("fail", "timeout"):
            r["stage"], r["reason"] = "runner_disagrees", f"icarus.run status {res.status}"
            continue
        r["stage"] = "kept"
        write_case(r, base_tree, res)
        kept.append((r, res))
    for r in slow:
        r["stage"], r["reason"] = "wall_clock_timeout", "hung past the pre-screen cap; not written (would stall evals)"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "screen.json").write_text(json.dumps(
        [{k: v for k, v in r.items() if k not in ("head_tree", "placed")} for r in rows], indent=2))
    readme(rows, kept)
    counts = {}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1
    print(f"\nafter simulation: {counts}")
    for r, res in kept:
        print(f"kept u-{r['sha7']}  {r['subject'][:60]:<60} {res.status} {res.failing_tests or res.trap_reason} "
              f"@ {res.trap_cycle}")


if __name__ == "__main__":
    main(sys.argv)
