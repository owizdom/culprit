"""Build a second corpus of seeded broken pull requests: one single-token mutation plus 1-2 harmless decoy edits.

Every case lands in corpus/mutants/cases/<id>/ in the same layout corpus/make.py writes for corpus/cases/:
head/picorv32.v, case.json, sim.json, truth.json. Only corpus/ (this builder) and evals/ (the grader) read truth.json.

  python corpus/mutants.py              screen mutants, add decoys, simulate, write the cases and README.md
  python corpus/mutants.py --want 35    number of cases to keep (default 35)

Screening runs the same iverilog/vvp commands as sim/icarus.py with a 240 s wall-clock cap; a mutant still running
after that (a zero-delay loop, not the testbench's own 1,000,000-cycle TIMEOUT) is dropped, because the grader's
simulator would otherwise sit on it for 1800 s. Screen results are saved to corpus/mutants/screen.json and reused.
"""
import json
import random
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

ROOT = Path(__file__).resolve().parent / "mutants"
SEED = 20260913
N_CANDIDATES = 150
SCREEN_TIMEOUT = 240
MIN_GAP = 40                     # lines between the mutation and each decoy, and between decoys
MAX_PER_CLASS = 8                # at most this many kept cases per operator class, for variety
WORKERS = 6
DEFINED = {"COMPRESSED_ISA"}     # testbench build: iverilog -DCOMPRESSED_ISA
MODULES = {"picorv32", "picorv32_pcpi_mul", "picorv32_pcpi_div", "picorv32_axi", "picorv32_axi_adapter"}

SWAPS = [(" == ", " != "), (" != ", " == "), (" + ", " - "), (" - ", " + "), (" & ", " | "), (" | ", " & "),
         (" && ", " || "), (" || ", " && "), (" < ", " >= "), (" >= ", " < "), (" >> ", " << "), (" << ", " >> "),
         ("1'b0", "1'b1"), ("1'b1", "1'b0")]
BIT = re.compile(r"\[(\d+)\]")
SYSTASK = re.compile(r"\$(display|write|finish|stop|any\w*)\b|\bassert\b|\bassume\b")
LHS = re.compile(r"^\s*(?:assign\s+)?([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*<?=[^=]")

SECTIONS = [("IRQ Interface", "irq"), ("Trace Interface", "trace"), ("Internal PCPI Cores", "pcpi"),
            ("Memory Interface", "mem"), ("Instruction Decoder", "decoder"), ("Main State Machine", "cpu")]
MODULE_PREFIX = {"picorv32_pcpi_mul": "mul", "picorv32_pcpi_div": "div", "picorv32_axi": "axi",
                 "picorv32_axi_adapter": "axi"}
COMMENTS = ["{lhs} for this instruction", "{lhs} in this cycle", "the {lhs} register",
            "{lhs} for the instruction in this cycle", "keep {lhs} in this state"]


def compiled_lines(lines):
    """0-based indices of lines that the testbench build compiles, with the module each belongs to."""
    out, stack, module = {}, [], None
    for i, line in enumerate(lines):
        s = line.strip()
        m = re.match(r"`(ifdef|ifndef|elsif)\s+(\w+)", s)
        if m:
            kind, name = m.groups()
            if kind == "elsif":
                parent, _, taken = stack.pop()
                stack.append((parent, parent and not taken and name in DEFINED, taken or name in DEFINED))
            else:
                parent = all(a for _, a, _ in stack)
                active = (name in DEFINED) == (kind == "ifdef")
                stack.append((parent, parent and active, active))
            continue
        if s.startswith("`else"):
            parent, _, taken = stack.pop()
            stack.append((parent, parent and not taken, True))
            continue
        if s.startswith("`endif"):
            stack.pop()
            continue
        if not all(a for _, a, _ in stack):
            continue
        if s.startswith("`define"):
            DEFINED.add(s.split()[1].split("(")[0])
        mm = re.match(r"module\s+(\w+)", s)
        if mm:
            module = mm.group(1)
        if module in MODULES:
            out[i] = module
        if s.startswith("endmodule"):
            module = None
    return out


def code_part(line):
    return line.split("//", 1)[0]


def candidates(lines, compiled):
    cands = []
    for i, module in compiled.items():
        line = lines[i]
        code = code_part(line)
        if not code.strip() or '"' in line or SYSTASK.search(code) or code.lstrip().startswith(("`", "*", "/*")):
            continue
        for a, b in SWAPS:
            for m in re.finditer(re.escape(a), code):
                cands.append((i, m.start(), a, b))
        for m in BIT.finditer(code):
            n = int(m.group(1))
            cands.append((i, m.start(), m.group(0), f"[{n - 1 if n > 0 else n + 1}]"))
    return cands


def mutate(lines, cand):
    i, col, a, b = cand
    assert lines[i][col:col + len(a)] == a
    return lines[i][:col] + b + lines[i][col + len(a):]


def op_class(a):
    return "[N]" if a.startswith("[") else a.strip()


def quick_run(tree, timeout=SCREEN_TIMEOUT):
    """sim/icarus.py's compile and run commands, capped at `timeout` seconds; status 'hang' past the cap."""
    started = time.time()
    hexfile, _ = icarus.firmware(tree)
    with tempfile.TemporaryDirectory() as tmp:
        icarus.export({"picorv32.v": tree["picorv32.v"], "testbench.v": tree["testbench.v"]}, tmp)
        shutil.copy(hexfile, Path(tmp) / "firmware.hex")
        comp = subprocess.run(["iverilog", "-DCOMPRESSED_ISA", "-s", "testbench", "-o", "tb.vvp", "testbench.v",
                               "picorv32.v"], cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        if comp.returncode != 0:
            return {"status": "compile_error", "seconds": round(time.time() - started, 1)}
        try:
            sim = subprocess.run(["vvp", "-N", "tb.vvp", "+firmware=firmware.hex"], cwd=tmp, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "hang", "seconds": round(time.time() - started, 1)}
    r = icarus.parse(sim.stdout + sim.stderr)
    return {"status": r.status, "failure": r.failing_tests or r.trap_reason, "trap_cycle": r.trap_cycle,
            "seconds": round(time.time() - started, 1)}


def screen(base_tree, lines, compiled):
    cands = candidates(lines, compiled)
    rng = random.Random(SEED)
    rng.shuffle(cands)
    picked, seen = [], set()
    for c in cands:
        if c[0] not in seen:
            seen.add(c[0])
            picked.append(c)
        if len(picked) == N_CANDIDATES:
            break
    path = ROOT / "screen.json"
    saved = {}
    if path.exists():
        prev = json.loads(path.read_text())
        if prev.get("seed") == SEED:
            saved = {(m["base_line"], m["col"], m["from"], m["to"]): m for m in prev["mutants"]}

    def one(c):
        key = (c[0] + 1, c[1], c[2], c[3])
        if key in saved:
            return saved[key]
        mutated = list(lines)
        mutated[c[0]] = mutate(lines, c)
        r = quick_run(dict(base_tree, **{"picorv32.v": "".join(mutated)}))
        return {"base_line": c[0] + 1, "col": c[1], "from": c[2], "to": c[3], "module": compiled[c[0]], **r}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(one, picked))
    path.write_text(json.dumps({"seed": SEED, "total_candidates": len(cands), "screened": len(rows),
                                "mutants": rows}, indent=2))
    return len(cands), rows


def section_of(lines, i, module):
    if module != "picorv32":
        return MODULE_PREFIX[module]
    prefix = "cpu"
    for j in range(i + 1):
        s = lines[j].strip()
        for name, short in SECTIONS:
            if s == f"// {name}":
                prefix = short
    return prefix


def pick_decoys(lines, compiled, base_text, base_words, bug_i, n, kinds, rng):
    pool = []
    for i, module in compiled.items():
        line = lines[i]
        if "//" in line or '"' in line or not line.rstrip().endswith(";") or abs(i - bug_i) < MIN_GAP:
            continue
        if i > 0 and lines[i - 1].rstrip().endswith("\\"):
            continue
        m = LHS.match(line)
        if m:
            pool.append((i, m.group(1), module))
    rng.shuffle(pool)
    chosen = []
    for kind in kinds:
        for i, lhs, module in pool:
            if any(abs(i - j) < MIN_GAP for j, *_ in chosen):
                continue
            indent = line_indent(lines[i])
            body = lines[i].rstrip("\n")
            if kind == "reindent":
                if not indent.startswith("\t"):
                    continue
                new_line = "\t" + lines[i]
                snippet = "\n" + new_line.rstrip("\n") + "\n"
            else:
                templates = [t for t in COMMENTS
                             if set(re.findall(r"[A-Za-z_]\w*", t.replace("{lhs}", ""))) <= base_words]
                text = "// " + rng.choice(templates).format(lhs=lhs)
                if kind == "comment_above":
                    new_line = indent + text + "\n"
                    snippet = text
                else:
                    new_line = body + " " + text + "\n"
                    snippet = body.strip() + " " + text
            if snippet in base_text:
                continue
            chosen.append((i, kind, lhs, module, new_line, snippet))
            break
    return sorted(chosen)


def line_indent(line):
    return line[:len(line) - len(line.lstrip())]


def assemble(lines, bug=None, decoys=()):
    """Head text from base lines. bug = (index, new_line). Returns (text, head line of the bug or None)."""
    inserts = {i: new for i, kind, _, _, new, _ in decoys if kind == "comment_above"}
    replaces = {i: new for i, kind, _, _, new, _ in decoys if kind != "comment_above"}
    if bug:
        replaces[bug[0]] = bug[1]
    out, bug_line = [], None
    for i, line in enumerate(lines):
        if i in inserts:
            out.append(inserts[i])
        out.append(replaces.get(i, line))
        if bug and i == bug[0]:
            bug_line = len(out)
    return "".join(out), bug_line


def describe(lines, decoys):
    section = section_of(lines, decoys[0][0], decoys[0][3])
    names = []
    for _, _, lhs, _, _, _ in decoys:
        if lhs not in names:
            names.append(lhs)
    comments = [d for d in decoys if d[1] != "reindent"]
    reindent = [d for d in decoys if d[1] == "reindent"]
    cnames = " and ".join(dict.fromkeys(d[2] for d in comments))
    if comments and reindent:
        title = f"{section}: annotate {cnames}, re-indent {reindent[0][2]}"
        body = (f"Small readability pass while reading the {section} code: a short comment next to the {cnames} "
                f"assignment and the {reindent[0][2]} assignment re-indented. No functional change intended.")
    elif comments:
        title = f"{section}: annotate {cnames}"
        what = "a short comment" if len(comments) == 1 else "short comments"
        body = (f"Adds {what} next to the {cnames} assignment{'s' if len(comments) > 1 else ''} so the "
                f"{section} code is easier to follow. No functional change intended.")
    else:
        title = f"{section}: re-indent {' and '.join(names)}"
        body = f"Re-indents the {' and '.join(names)} assignment. Whitespace only, no functional change intended."
    return title, body


def main(argv):
    started = time.time()
    want = int(argv[argv.index("--want") + 1]) if "--want" in argv else 35
    base_tree = icarus.read_tree(CHECKOUT)
    base_result = icarus.run(base_tree)
    if not base_result.passed:
        sys.exit("base checkout does not pass the regression")
    base_text = base_tree["picorv32.v"]
    lines = base_text.splitlines(keepends=True)
    base_words = set(re.findall(r"[A-Za-z_]\w*", base_text))
    compiled = compiled_lines(lines)
    ROOT.mkdir(parents=True, exist_ok=True)

    total, rows = screen(base_tree, lines, compiled)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"candidates {total}, screened {len(rows)}: {counts}")
    killed = [r for r in rows if r["status"] in ("fail", "timeout")]

    # Assemble specs in screen order, capped per operator class, with rotating decoy kinds.
    kinds_cycle = ["comment_above", "trailing_comment", "comment_above", "reindent", "trailing_comment"]
    specs, per_class, k = [], {}, 0
    for r in killed:
        cls = op_class(r["from"])
        if per_class.get(cls, 0) >= MAX_PER_CLASS:
            continue
        rng = random.Random(SEED * 1000 + r["base_line"])
        n = rng.choice([1, 2])
        kinds = [kinds_cycle[(k + d) % len(kinds_cycle)] for d in range(n)]
        bug_i = r["base_line"] - 1
        decoys = pick_decoys(lines, compiled, base_text, base_words, bug_i, n, kinds, rng)
        if len(decoys) != n:
            continue
        new_line = mutate(lines, (bug_i, r["col"], r["from"], r["to"]))
        head_text, bug_line = assemble(lines, (bug_i, new_line), decoys)
        decoy_text, _ = assemble(lines, None, decoys)
        head_tree = dict(base_tree, **{"picorv32.v": head_text})
        hs = H.parse_tree(base_tree, head_tree)
        bug_hunks = sorted(h.id for h in hs if bug_line in h.changed_head_lines())
        groups = H.groups(hs, base_tree, head_tree)
        if len(hs) != 1 + n or len(bug_hunks) != 1 or any(len(g) != 1 for g in groups) \
                or head_text.splitlines()[bug_line - 1] != new_line.rstrip("\n"):
            print(f"skip base line {r['base_line']}: hunk structure {len(hs)} hunks, bug hunks {bug_hunks}")
            continue
        per_class[cls] = per_class.get(cls, 0) + 1
        k += 1
        specs.append({"screen": r, "decoys": decoys, "head_tree": head_tree, "bug_line": bug_line,
                      "decoy_tree": dict(base_tree, **{"picorv32.v": decoy_text}), "hunks": hs,
                      "bug_hunks": bug_hunks, "new_line": new_line})
    specs = specs[:want + 6]                        # a few spares in case a head or decoy tree misbehaves
    print(f"specs assembled: {len(specs)} (per class {per_class})")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        decoy_results = list(pool.map(lambda s: icarus.run(s["decoy_tree"]), specs))
        head_results = list(pool.map(lambda s: icarus.run(s["head_tree"]), specs))

    cases_dir = ROOT / "cases"
    if cases_dir.exists():
        shutil.rmtree(cases_dir)
    kept, dropped = [], []
    for s, dr, hr in zip(specs, decoy_results, head_results):
        ok = dr.passed and hr.status in ("fail", "timeout")
        if not ok or len(kept) >= want:
            if not ok:
                dropped.append((s["screen"]["base_line"], dr.status, hr.status))
            continue
        cid = f"x{len(kept) + 1:02d}"
        r = s["screen"]
        title, body = describe(lines, s["decoys"])
        out = cases_dir / cid
        (out / "head").mkdir(parents=True, exist_ok=True)
        (out / "head" / "picorv32.v").write_text(s["head_tree"]["picorv32.v"])
        truth = {
            "category": "mutant_with_decoys",
            "hunks": [{"id": h.id, "path": h.path, "new_start": h.new_start, "changed": sorted(h.changed_head_lines())}
                      for h in s["hunks"]],
            "bug_lines": [s["bug_line"]],
            "bug_hunks": s["bug_hunks"],
            "fix_line": lines[r["base_line"] - 1].rstrip("\n"),
            "intended_contains": [d[5] for d in s["decoys"]],
            "mutation": {"line": s["bug_line"], "base_line": r["base_line"], "from": r["from"].strip(),
                         "to": r["to"].strip(), "module": r["module"]},
            "decoys": [{"base_line": d[0] + 1, "kind": d[1], "text": d[5]} for d in s["decoys"]],
        }
        sim = {"head_status": hr.status, "failure": hr.failing_tests or hr.trap_reason, "trap_cycle": hr.trap_cycle,
               "seconds": round(hr.seconds, 1), "kept": True, "decoy_only_status": dr.status,
               "mutant_only_status": r["status"]}
        (out / "truth.json").write_text(json.dumps(truth, indent=2))
        (out / "sim.json").write_text(json.dumps(sim, indent=2))
        (out / "case.json").write_text(json.dumps({"id": cid, "category": "mutant_with_decoys", "title": title,
                                                   "body": body}, indent=2))
        kept.append((cid, truth, sim, title))
        print(f"kept {cid} base {r['base_line']} -> head {s['bug_line']} {r['from'].strip()!r}->{r['to'].strip()!r} "
              f"head={hr.status} {sim['failure']} @ {hr.trap_cycle}; decoys={len(s['decoys'])} decoy_only={dr.status}")
    for d in dropped:
        print(f"DROP base line {d[0]}: decoy_only={d[1]} head={d[2]}")

    kinds_verified = sorted({d["kind"] for _, t, _, _ in kept for d in t["decoys"]})
    write_readme(total, rows, counts, len(specs), dropped, kept, kinds_verified, time.time() - started)
    print(f"kept {len(kept)} cases in {time.time() - started:.0f} s")


def write_readme(total, rows, counts, n_specs, dropped, kept, kinds, seconds):
    table = "\n".join(
        f"| {cid} | {t['mutation']['line']} (base {t['mutation']['base_line']}) | {t['mutation']['module']} | "
        f"`{t['mutation']['from']}` → `{t['mutation']['to']}` | {s['head_status']} | "
        f"{', '.join(s['failure']) if isinstance(s['failure'], list) else s['failure']} | {s['trap_cycle']} | "
        f"{len(t['decoys'])} ({', '.join(d['kind'] for d in t['decoys'])}) |"
        for cid, t, s, _ in kept)
    text = f"""# Mutant corpus

Seeded broken pull requests built by `corpus/mutants.py`. Each case is one single-token mutation in `picorv32.v`
that makes the PicoRV32 regression fail, plus one or two harmless decoy edits at least {MIN_GAP} lines away from the
mutation and from each other. The agent has to find which edit breaks the regression.

Grade with `python evals/run.py --corpus corpus/mutants/cases --tag mutants`.

## Method

1. Candidate lines: lines of `picorv32.v` that the testbench build compiles (`iverilog -DCOMPRESSED_ISA`, preprocessor
   `ifdef`/`ifndef`/`elsif`/`else` evaluated), inside the modules the testbench instantiates
   ({', '.join(sorted(MODULES))}). Comment text, lines with strings or system tasks, and preprocessor lines are skipped.
2. Operators: `==`/`!=`, `+`/`-`, `&`/`|`, `&&`/`||`, `<`/`>=`, `>>`/`<<`, `1'b0`/`1'b1`, bit index `[N]` to `[N-1]`
   (`[0]` to `[1]`). `<=` is never touched, so non-blocking assignments stay valid.
3. Seed {SEED}. All {total} candidates are shuffled, one per line is kept, and the first {len(rows)} are simulated with
   the same compile and `vvp` commands as `sim/icarus.py`, capped at {SCREEN_TIMEOUT} s wall clock
   (status `hang` past the cap; the testbench's own 1,000,000-cycle `TIMEOUT` is status `timeout`). Results:
   `screen.json`.
4. Mutants whose mutant-only tree ends `fail` or `timeout` are eligible, at most {MAX_PER_CLASS} per operator class.
   `hang`, `compile_error` and `pass` are dropped.
5. Decoys are comment or whitespace edits on assignment lines of compiled code: a comment line above the statement
   (`comment_above`), a trailing comment (`trailing_comment`), or one extra leading tab (`reindent`). Comment words
   are drawn only from words already present in `picorv32.v`, so no decoy introduces a new identifier and
   `localize/hunks.py` `groups()` keeps every hunk on its own.
6. Every case is checked: base passes; the decoy-only tree passes by simulation (every case, not only one per decoy
   kind); the head tree (base + mutation + decoys) fails; the head differs from base in exactly 1 + N hunks and the
   mutated line is in exactly one of them.
7. `truth.json`: `bug_lines` is the mutated head line, `bug_hunks` the hunk containing it, `intended_contains` the
   decoy text (a fix that keeps the author's harmless edits keeps these strings), `mutation` = line, base line, from,
   to, module; `fix_line` is the original base line.

## Counts

| stage | count |
|---|---|
| candidate (line, operator) pairs | {total} |
| screened (one per line) | {len(rows)} |
| screen `fail` | {counts.get('fail', 0)} |
| screen `timeout` (testbench TIMEOUT) | {counts.get('timeout', 0)} |
| screen `pass` (mutant not caught) | {counts.get('pass', 0)} |
| screen `compile_error` | {counts.get('compile_error', 0)} |
| screen `hang` (over {SCREEN_TIMEOUT} s) | {counts.get('hang', 0)} |
| cases assembled with decoys and simulated | {n_specs} |
| dropped after head/decoy simulation | {len(dropped)} |
| kept | {len(kept)} |

Decoy kinds verified by simulation in kept cases: {', '.join(kinds)}. Build wall time: {seconds:.0f} s.

## Kept cases

| id | head line | module | mutation | head status | failure | trap cycle | decoys |
|---|---|---|---|---|---|---|---|
{table}
"""
    (ROOT / "README.md").write_text(text)


if __name__ == "__main__":
    main(sys.argv)
