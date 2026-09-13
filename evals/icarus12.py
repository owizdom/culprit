"""Does Icarus Verilog 12 (the ubuntu-24.04 apt package GitHub CI installs) agree with local Icarus 13?

For the picorv32-ci base tree and every kept corpus case head, build (or reuse) the firmware, run the regression with
local Icarus 13 through sim/icarus.run, run the same compile and vvp commands with Icarus 12 inside the culprit-iv12
image (sim/Dockerfile.iv12), parse both logs with icarus.parse and compare them. No model calls.

  docker build -t culprit-iv12 -f sim/Dockerfile.iv12 sim/
  .venv/bin/python evals/icarus12.py [--cases a01,r02] [--no-base] [--workers 4] [--out results]
Writes <out>/icarus12.json and <out>/icarus12.md.
"""
import argparse
import difflib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sim import icarus  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "corpus" / "cases"
CHECKOUT = Path(os.environ.get("CULPRIT_CHIP_REPO") or ROOT.parent / "picorv32-ci")
IMAGE = "culprit-iv12"
TIMEOUT = 900
FIELDS = ("status", "passed", "trap_cycle", "failing_tests", "trap_reason")


def version_line(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return (proc.stdout + proc.stderr).strip().splitlines()[0]


def load_head(case_dir, base_tree):
    """The base tree with the case's head/ files layered over it (same layering as evals/run.py)."""
    head = dict(base_tree)
    for p in (case_dir / "head").rglob("*"):
        if p.is_file():
            head[p.relative_to(case_dir / "head").as_posix()] = p.read_text()
    return head


def run_iv12(tree, hexfile):
    """icarus.run's compile and vvp commands, executed with Icarus 12 in the container."""
    started = time.time()
    name = f"culprit-iv12-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as tmp:
        icarus.export({"picorv32.v": tree["picorv32.v"], "testbench.v": tree["testbench.v"]}, tmp)
        Path(tmp, "firmware.hex").write_bytes(Path(hexfile).read_bytes())
        script = ("iverilog -DCOMPRESSED_ISA -s testbench -o tb.vvp testbench.v picorv32.v 2> compile.err "
                  "|| { cat compile.err >&2; exit 97; }; vvp -N tb.vvp +firmware=firmware.hex")
        try:
            proc = subprocess.run(["docker", "run", "--rm", "--name", name, "-v", f"{tmp}:/w", "-w", "/w", IMAGE,
                                   "sh", "-c", script],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return icarus.Result(status="harness_timeout", passed=False, log="", seconds=time.time() - started)
    if proc.returncode == 97:
        return icarus.Result(status="compile_error", passed=False, log=proc.stderr[-4000:],
                             seconds=time.time() - started)
    if proc.returncode == 125:      # docker itself failed (image missing, mount refused, ...)
        raise RuntimeError(f"docker run failed: {proc.stderr[-2000:]}")
    result = icarus.parse(proc.stdout + proc.stderr)
    result.seconds = time.time() - started
    return result


def summary(result, cached=None):
    row = {k: getattr(result, k) for k in FIELDS}
    row["seconds"] = round(result.seconds, 1)
    row["log_sha256"] = hashlib.sha256(result.log.encode()).hexdigest()[:16]
    row["log_bytes"] = len(result.log)
    if cached is not None:
        row["from_cache"] = cached
    if result.status in ("compile_error", "build_error"):
        row["log_tail"] = result.log[-2000:]
    return row


def log_diff(a, b, limit=40):
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), "icarus13", "icarus12", lineterm="", n=1))
    return lines[:limit]


def one(job):
    rid, category, tree, hexfile, recorded = job
    local = icarus.run(tree)
    local_cached = local.seconds == 0.0     # icarus.run returns seconds 0.0 only when it read its log cache
    iv12 = run_iv12(tree, hexfile)
    agree = {k: getattr(local, k) == getattr(iv12, k) for k in FIELDS}
    agree["status_and_trap_cycle"] = agree["status"] and agree["trap_cycle"]
    agree["all_fields"] = all(agree[k] for k in FIELDS)
    agree["log_identical"] = local.log == iv12.log
    row = {"id": rid, "category": category, "icarus13": summary(local, local_cached), "icarus12": summary(iv12),
           "agree": agree, "recorded_in_sim_json": recorded}
    if not agree["log_identical"]:
        row["log_diff_head"] = log_diff(local.log, iv12.log)
    print(f"{rid:5} {category:22} i13 {local.status}/{local.trap_cycle}  i12 {iv12.status}/{iv12.trap_cycle}  "
          f"agree={agree['all_fields']} log_identical={agree['log_identical']}  "
          f"({local.seconds:.0f}s, {iv12.seconds:.0f}s)", flush=True)
    return row


def cell(side):
    return f"{side['status']} / {side['trap_cycle']}"


def write_md(path, data):
    rows = data["runs"]
    n = len(rows)
    k_st = sum(r["agree"]["status_and_trap_cycle"] for r in rows)
    k_all = sum(r["agree"]["all_fields"] for r in rows)
    k_log = sum(r["agree"]["log_identical"] for r in rows)
    heads = sum(r["id"] != "base" for r in rows)
    has_base = any(r["id"] == "base" for r in rows)
    what = f"{heads} case heads" + (" + base" if has_base else "")
    out = [
        "# Icarus Verilog 12 vs 13 on the evaluation corpus", "",
        f"Generated by `evals/icarus12.py` on {data['date']}. Wall time {data['wall_seconds'] / 60:.1f} min.", "",
        "## Versions", "",
        f"- Local: `{data['versions']['icarus13']}` (Homebrew, macOS)",
        f"- CI stand-in: `{data['versions']['icarus12']}`, Ubuntu package `{data['versions']['icarus12_package']}`, "
        f"image `culprit-iv12` built from `sim/Dockerfile.iv12` (`ubuntu:noble`, the same apt package the "
        "picorv32-ci `regression.yml` job installs on `ubuntu-24.04`)", "",
        "## Method", "",
        "For the picorv32-ci base tree and each corpus case with `kept: true` in `sim.json`, the head tree is the base "
        "tree with the case's `head/` files layered over it. Firmware comes from `icarus.firmware(tree)`, so cases "
        "that change firmware, tests or the testbench get their own hex. The Icarus 13 result is `icarus.run(tree)` "
        "(`iverilog -DCOMPRESSED_ISA -s testbench -o tb.vvp testbench.v picorv32.v`, then "
        "`vvp -N tb.vvp +firmware=<hex>`). The Icarus 12 result runs the same two commands on the same "
        "`picorv32.v`, `testbench.v` and hex inside the container. Both logs go through `icarus.parse`, and the "
        f"parsed status, passed, trap cycle, failing tests and trap reason are compared. The full simulator logs "
        "are also compared byte for byte. CI's `make test` compiles without `-s testbench` "
        "(picorv32-ci `Makefile:59`); that flag only names the top module and was held the same on both sides.", "",
        "## Result", "",
        f"Agreement: {k_st}/{n} runs ({what}) on status and trap cycle.", "",
        f"All five parsed fields (status, passed, trap cycle, failing tests, trap reason): {k_all}/{n}.", "",
        f"Byte-identical simulator logs: {k_log}/{n}.", "",
        f"Icarus 13 results read from `icarus.run`'s content-addressed log cache: "
        f"{sum(bool(r['icarus13'].get('from_cache')) for r in rows)}/{n} (the rest simulated during this run). "
        "Icarus 12 results were all simulated during this run.", "",
        "Harness note: `testbench.v:249` stores the `+firmware` path in `reg [1023:0]`, which holds 128 bytes. A "
        "first attempt ran with `CULPRIT_HOME` in a long scratch directory, so the rebuilt `non_rtl` hex paths were "
        "139 characters. Icarus 13 received the truncated path, `$readmemh` failed to open it, and r01, r02, r03 "
        "and r05 falsely disagreed. That was a harness error, not a simulator difference. The script now refuses "
        "paths over 128 characters, and this run used the default cache path.", "",
    ]
    mismatches = [r for r in rows if not r["agree"]["all_fields"]]
    out += ["## Mismatches", ""]
    if not mismatches:
        out += ["None on the parsed fields.", ""]
    for r in mismatches:
        bad = [f for f in FIELDS if not r["agree"][f]]
        out.append(f"- `{r['id']}` ({r['category']}): differs on {', '.join(bad)}. "
                   + "; ".join(f"{f}: Icarus 13 `{r['icarus13'][f]}`, Icarus 12 `{r['icarus12'][f]}`" for f in bad))
    if mismatches:
        out.append("")
    nonident = [r for r in rows if not r["agree"]["log_identical"]]
    if nonident:
        out += ["Runs whose raw logs differ (see `log_diff_head` in `icarus12.json`): "
                + ", ".join(f"`{r['id']}`" for r in nonident) + ".", ""]
    out += ["## Every run", "",
            "| id | category | Icarus 13 status / cycle | Icarus 12 status / cycle | agree (status, cycle) "
            "| agree (all fields) | logs identical |",
            "|---|---|---|---|---|---|---|"]
    for r in rows:
        a = r["agree"]
        out.append(f"| {r['id']} | {r['category']} | {cell(r['icarus13'])} | {cell(r['icarus12'])} | "
                   f"{'yes' if a['status_and_trap_cycle'] else 'NO'} | {'yes' if a['all_fields'] else 'NO'} | "
                   f"{'yes' if a['log_identical'] else 'no'} |")
    out.append("")
    Path(path).write_text("\n".join(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", help="comma-separated case ids (default: every kept case)")
    ap.add_argument("--no-base", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args()
    if not subprocess.run(["docker", "images", "-q", IMAGE], capture_output=True, text=True).stdout.strip():
        sys.exit(f"image {IMAGE} missing: docker build -t {IMAGE} -f sim/Dockerfile.iv12 sim/")
    started = time.time()
    versions = {
        "icarus13": version_line(["iverilog", "-V"]),
        "icarus12": version_line(["docker", "run", "--rm", IMAGE, "iverilog", "-V"]),
        "icarus12_package": version_line(["docker", "run", "--rm", IMAGE, "dpkg-query", "-W", "-f=${Version}",
                                          "iverilog"]),
    }
    print(versions, flush=True)
    base = icarus.read_tree(CHECKOUT)
    wanted = set(args.cases.split(",")) if args.cases else None
    trees = [] if args.no_base else [("base", "base", base, None)]
    for case_dir in sorted(p for p in CASES.iterdir() if p.is_dir()):
        sim = json.loads((case_dir / "sim.json").read_text())
        if not sim.get("kept") or (wanted and case_dir.name not in wanted):
            continue
        meta = json.loads((case_dir / "case.json").read_text())
        recorded = {"head_status": sim.get("head_status"), "trap_cycle": sim.get("trap_cycle")}
        trees.append((case_dir.name, meta["category"], load_head(case_dir, base), recorded))
    jobs = []
    for rid, category, tree, recorded in trees:     # firmware first and serially: builds share one cache
        hexfile, log = icarus.firmware(tree)
        if hexfile is None:
            sys.exit(f"{rid}: firmware build failed, cannot compare\n{log[-2000:]}")
        if len(str(hexfile)) > 128:     # testbench.v keeps +firmware in reg [1023:0]: a longer path is truncated
            sys.exit(f"{rid}: firmware path is {len(str(hexfile))} chars; testbench.v reads at most 128 and "
                     f"$readmemh would fail. Use a shorter CULPRIT_HOME.")
        jobs.append((rid, category, tree, hexfile, recorded))
    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(one, jobs))
    data = {"date": time.strftime("%Y-%m-%d %H:%M %Z"), "wall_seconds": round(time.time() - started, 1),
            "versions": versions, "image": IMAGE, "checkout": str(CHECKOUT), "timeout_seconds": TIMEOUT,
            "runs": rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "icarus12.json").write_text(json.dumps(data, indent=2) + "\n")
    write_md(out / "icarus12.md", data)
    n = len(rows)
    print(f"status+cycle {sum(r['agree']['status_and_trap_cycle'] for r in rows)}/{n}  "
          f"all fields {sum(r['agree']['all_fields'] for r in rows)}/{n}  "
          f"identical logs {sum(r['agree']['log_identical'] for r in rows)}/{n}  wall {data['wall_seconds']}s")


if __name__ == "__main__":
    main()
