"""Smoke test: run a real CULPRIT investigation end to end (no model, no GitHub/Linear/Slack) and check the result.

  python evals/smoke.py <picorv32-ci base checkout> [case_dir ...]

For each case the head tree is a copy of the base checkout (without .git) with the case's head/ files laid over it.
Each investigation runs `culprit.py local <base> <head> <pr>` in its own process with CULPRIT_HOME set to a fresh
temp directory (paths.HOME is read at import), then meta.json and path.json of the run are checked:
status fixed or confirmed, the verdict line where the case's line is known here, no propagation error and at least
one waveform-confirmed hop. Prints one JSON line per case and exits non-zero if any case fails.
Needs git-free inputs only: iverilog, vvp and yosys on PATH, and the stock firmware in sim/fw-seed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ["corpus/cases/t01", "corpus/history/cases/u-2cab981"]
# What each case must end as. Lines are only listed where they were checked by hand for this smoke test.
EXPECT = {
    "t01": {"status": {"fixed"}, "lines": {1344}},
    "u-2cab981": {"status": {"fixed"}},
}
TOOLS = ("iverilog", "vvp", "yosys")
TIMEOUT = 3600


def build_head(base, case, dest):
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns(".git"))
    layered = []
    for src in sorted((case / "head").rglob("*")):
        if src.is_file():
            rel = src.relative_to(case / "head")
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / rel)
            layered.append(rel.as_posix())
    return layered


def check(case, home):
    name = case.name
    expect = EXPECT.get(name, {})
    runs = sorted((home / "runs").glob("*/meta.json"))
    if len(runs) != 1:
        return {"case": name, "ok": False, "problems": [f"expected one run under {home / 'runs'}, found {len(runs)}"]}
    meta = json.loads(runs[0].read_text(encoding="utf-8"))
    path_file = runs[0].parent / "path.json"
    path = json.loads(path_file.read_text(encoding="utf-8")) if path_file.exists() else None
    verdict = meta.get("verdict") or {}
    problems = []
    statuses = expect.get("status", {"fixed", "confirmed"})
    if meta.get("status") not in statuses:
        problems.append(f"status {meta.get('status')!r} not in {sorted(statuses)}")
    if "lines" in expect and verdict.get("line") not in expect["lines"]:
        problems.append(f"verdict line {verdict.get('line')!r} not in {sorted(expect['lines'])}")
    if not meta.get("finished"):
        problems.append("run never finished")
    if path is None:
        problems.append("no path.json")
    elif path.get("error"):
        problems.append(f"propagation error: {path['error']}")
    elif (path.get("confirmed_hops") or 0) < 1:
        problems.append(f"confirmed_hops {path.get('confirmed_hops')!r} < 1")
    return {"case": name, "ok": not problems, "status": meta.get("status"), "line": verdict.get("line"),
            "trap_cycle": meta.get("trap_cycle"), "sims": meta.get("sims"),
            "confirmed_hops": (path or {}).get("confirmed_hops"), "error": (path or {}).get("error"),
            "run": runs[0].parent.name, "problems": problems}


def smoke(base, case, pr):
    with tempfile.TemporaryDirectory(prefix="culprit-smoke-", ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        home, head = tmp / "home", tmp / "head"
        home.mkdir()
        layered = build_head(base, case, head)
        env = dict(os.environ, CULPRIT_HOME=str(home), PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):      # the smoke test never calls the model
            env.pop(key, None)
        print(f"== {case.name}: head = base + {', '.join(layered)}", flush=True)
        started = time.time()
        try:
            proc = subprocess.run([sys.executable, str(ROOT / "culprit.py"), "local", str(base), str(head), str(pr)],
                                  cwd=ROOT, env=env, timeout=TIMEOUT)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
        result = check(case, home)
        result["seconds"] = round(time.time() - started, 1)
        if code != 0:
            result["ok"] = False
            result["problems"].insert(0, f"culprit.py local exited {code}")
        return result


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    base = Path(argv[1]).resolve()
    if not (base / "picorv32.v").is_file() or not (base / "testbench.v").is_file():
        sys.exit(f"{base} is not a picorv32-ci checkout (no picorv32.v / testbench.v)")
    missing = [t for t in TOOLS if shutil.which(t) is None]
    if missing:
        sys.exit(f"not on PATH: {', '.join(missing)}")
    # Run each tool the way CULPRIT does. A tool can be on PATH and still not start (on Windows, a wrong DLL ahead of
    # the tool's own on PATH exits 0xC0000139), which would otherwise surface as a failed simulation.
    broken = []
    for tool in TOOLS:
        r = subprocess.run([tool, "-V"], capture_output=True, text=True)
        print(tool, shutil.which(tool), "exit", r.returncode, ((r.stdout or r.stderr).strip().splitlines() or [""])[0])
        if r.returncode != 0:
            broken.append(tool)
    if broken:
        sys.exit(f"does not run: {', '.join(broken)}")
    cases = [Path(c) if Path(c).is_absolute() else ROOT / c for c in (argv[2:] or DEFAULT_CASES)]
    results = []
    for i, case in enumerate(cases, start=1):
        if not (case / "head").is_dir():
            results.append({"case": case.name, "ok": False, "problems": [f"{case} has no head/ directory"]})
            continue
        results.append(smoke(base, case.resolve(), i))
    print("== smoke summary", flush=True)
    for r in results:
        print(json.dumps(r), flush=True)
    return 0 if results and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
