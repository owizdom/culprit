"""Build firmware and run the PicoRV32 regression in Icarus, then read the result from the log.

Pass/fail is decided by text, not exit code: testbench.v prints "TRAP after N clock cycles"
and then "ALL TESTS PASSED." or "ERROR!". A TIMEOUT exits 0, so exit codes lie.
The file names, commands and log markers come from design.py; the defaults are PicoRV32's.
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import design
from paths import HOME, ROOT  # noqa: E402

_FW = design.DEFAULTS["firmware"]
CACHE = HOME / ".cache" / "sim"
SEED = ROOT / _FW["seed_dir"]       # firmware already built for picorv32-ci main, so most runs need no Docker
TOOLCHAIN_IMAGE = _FW["image"]
SIM_SLOTS = threading.Semaphore(int(os.environ.get("CULPRIT_SIM_SLOTS", "6")))
FIRMWARE_INPUTS = tuple(_FW["inputs"])


@dataclass
class Result:
    status: str                     # pass | fail | compile_error | build_error | timeout
    passed: bool
    failing_tests: list = field(default_factory=list)
    trap_cycle: int | None = None
    trap_reason: str | None = None
    log: str = ""
    seconds: float = 0.0
    key: str = ""


def tree_hash(tree, names):
    h = hashlib.sha256()
    for path in sorted(tree):
        if path.split("/")[0] in names or path in names:
            h.update(path.encode() + b"\0" + tree[path].encode() + b"\0")
    return h.hexdigest()[:16]


def export(tree, dest):
    for path, text in tree.items():
        p = Path(dest) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def read_tree(repo_dir):
    """Every text file of a checkout that the regression can depend on."""
    suffixes = tuple(design.get()["tree_suffixes"])
    tree = {}
    for p in Path(repo_dir).rglob("*"):
        rel = p.relative_to(repo_dir).as_posix()
        if p.is_file() and not rel.startswith((".git/", ".github/")) and p.suffix in suffixes \
                and p.stat().st_size < 2_000_000:
            try:
                tree[rel] = p.read_text()
            except UnicodeDecodeError:
                pass
    return tree


def firmware(tree):
    """Build the firmware image in the toolchain container; cached by the firmware sources.

    Returns (None, "") when the design has no firmware step.
    """
    fw = design.get()["firmware"]
    if not fw:
        return None, ""
    inputs = tuple(fw["inputs"])
    key = tree_hash(tree, inputs)
    name = Path(fw["output"]).name
    out = CACHE / "fw" / key
    hexfile = out / name
    if hexfile.exists():
        return hexfile, ""
    seed = SEED if fw.get("seed_dir") == _FW["seed_dir"] else (ROOT / fw["seed_dir"] if fw.get("seed_dir") else None)
    if seed is not None and (seed / key / name).exists():
        return seed / key / name, ""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        export({p: t for p, t in tree.items() if p.split("/")[0] in inputs or p in inputs}, tmp)
        proc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{tmp}:/w", "-w", "/w", fw["image"], *fw["command"]],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=fw["timeout_s"])
        if proc.returncode != 0:
            return None, proc.stdout[-4000:] + proc.stderr[-4000:]
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(tmp) / fw["output"], hexfile)
    return hexfile, ""


_LOG = design.DEFAULTS["log"]
TEST_LINE = re.compile(_LOG["test_line"], re.M)
TRAP_LINE = re.compile(_LOG["end"])
REASON = re.compile(_LOG["reason"])
END_TIME = re.compile(_LOG["end_time"])


def parse(log):
    cfg = design.get()
    lg, timing = cfg["log"], cfg["timing"]
    fail_word = lg.get("test_fail") or ""
    tests = re.findall(lg["test_line"], log, re.M) if lg.get("test_line") else []
    failing = [name for name, verdict in tests if verdict == fail_word]
    trap = re.search(lg["end"], log) if lg.get("end") else None
    after = (log[trap.end():] if trap else "") if lg.get("end") else log
    if lg.get("timeout") and lg["timeout"] in log and not trap:
        status = "timeout"
    elif lg.get("pass") and re.search(lg["pass"], after, re.M):
        status = "pass"
    else:
        status = "fail"
    for name in lg.get("named_tests") or []:
        if re.search(rf"^{name}.*{re.escape(fail_word)}", log, re.M | re.I):
            failing.append(name)
    reason = re.search(lg["reason"], log) if lg.get("reason") else None
    if reason is None:
        why = None
    elif len(reason.groups()) < 4:
        why = reason.group(0)
    elif reason.group(1):
        why = f"{reason.group(1)} at 0x{reason.group(2)}"
    else:
        why = f"{reason.group(3)} {reason.group(4)}"
    cycle = int(trap.group(1)) if trap and trap.groups() else None
    end = re.search(lg["end_time"], log) if lg.get("end_time") else None
    if cycle is None and end:
        # ps -> clock cycles (period_ns * 1000 ps each), minus the reset hold
        cycle = int(end.group(1)) // (timing["period_ns"] * 1000) - timing["reset_cycles"]
    return Result(status=status, passed=status == "pass", failing_tests=failing, trap_cycle=cycle, trap_reason=why,
                  log=log)


def run(tree, extra_verilog=None, keep=None):
    """Simulate one tree. extra_verilog = {"culprit_dump.v": text} adds a second top module.

    keep: a directory to copy the run's working files into (VCDs, traces).
    """
    started = time.time()
    cfg = design.get()
    fw, sim_cfg, sources = cfg["firmware"], cfg["sim"], list(cfg["sources"])
    hexfile = None
    if fw:
        hexfile, build_log = firmware(tree)
        if hexfile is None:
            return Result(status="build_error", passed=False, log=build_log, seconds=time.time() - started)
    extra_verilog = extra_verilog or {}
    # design.digest() is "" for the default design, so its cache keys are what they always were.
    key = hashlib.sha256((tree_hash(tree, tuple(sources)) + (hexfile.parent.name if hexfile else "") +
                          repr(sorted(extra_verilog.items())) + design.digest()).encode()).hexdigest()[:20]
    cached = CACHE / "runs" / f"{key}.log"
    if cached.exists() and keep is None:
        result = parse(cached.read_text())
        result.key, result.seconds = key, 0.0
        return result
    with SIM_SLOTS, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        export({**{s: tree[s] for s in sources}, **extra_verilog}, tmp)
        plusargs = []
        if fw:
            # testbench.v keeps the +firmware path in a 128-character register; a longer install or cache path would
            # be cut off and fail every run. The hex goes next to the simulation and is passed by its short name.
            shutil.copy(hexfile, Path(tmp) / fw["sim_name"])
            plusargs = [fw["plusarg"]] if fw.get("plusarg") else []
        tops = (["-s", sim_cfg["top"]] if sim_cfg.get("top") else []) + \
            sum((["-s", Path(n).stem] for n in extra_verilog), [])
        compile_ = subprocess.run(["iverilog", *design.defines(cfg), *tops, "-o", "tb.vvp", *sources,
                                   *extra_verilog], cwd=tmp, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        if compile_.returncode != 0:
            return Result(status="compile_error", passed=False, log=compile_.stderr[-4000:],
                          seconds=time.time() - started, key=key)
        # A broken CPU can print arbitrary bytes over its UART, so never decode the log strictly.
        sim = subprocess.run(["vvp", "-N", "tb.vvp", *plusargs],
                             cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=sim_cfg["timeout_s"])
        log = sim.stdout + sim.stderr
        if keep is not None:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for p in Path(tmp).iterdir():
                if p.suffix in (".vcd", ".trace"):
                    shutil.copy(p, Path(keep) / p.name)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(log)
    result = parse(log)
    result.key, result.seconds = key, time.time() - started
    return result
