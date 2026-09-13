"""Prove an edit cannot change the simulated chip, without simulating.

Tier 1: after the preprocessor, the token streams are identical (comments, whitespace, code inside
an `ifdef that is off).
Tier 2: the elaborated netlists are the same graph once names are ignored (a rename). Checked with a
Weisfeiler-Lehman hash over cell types, parameters and wiring, with top-level ports anchored by name.

Either answer only lets us skip a simulation. Nothing is ever blamed from this.
The preprocessor defines, Yosys top and parameters come from design.py.
"""
import hashlib
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import design

TOKEN = re.compile(r"\w+|\S")
YOSYS_PARAMS = design.chparams(design.DEFAULTS)


def tokens(verilog_text):
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "x.v").write_text(verilog_text)
        out = subprocess.run(["iverilog", *design.defines(), "-E", "-o", "pp.v", "x.v"], cwd=tmp,
                             capture_output=True, text=True)
        if out.returncode != 0:
            return None
        text = Path(tmp, "pp.v").read_text()
    text = re.sub(r"^\s*`line.*$", "", text, flags=re.M)
    text = re.sub(r"//.*$", "", text, flags=re.M)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return TOKEN.findall(text)


def wl_signature(verilog_text, rounds=4):
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "x.v").write_text(verilog_text)
        run = subprocess.run(["yosys", "-q", "-p", f"read_verilog x.v; {design.hierarchy()}; "
                              "proc; flatten; opt -purge; write_json net.json"],
                             cwd=tmp, capture_output=True, text=True, timeout=120)
        if run.returncode != 0:
            return None
        net = json.loads(Path(tmp, "net.json").read_text())
    top = next(iter(net["modules"].values()))

    driver_of = {}           # bit -> node label that drives it
    for pname, port in top["ports"].items():
        if port["direction"] == "input":
            for i, b in enumerate(port["bits"]):
                driver_of[b] = f"in:{pname}[{i}]"
    cells = {}
    for cname, c in top["cells"].items():
        if c["type"] == "$scopeinfo":
            continue
        params = {k: v for k, v in c.get("parameters", {}).items() if k != "MEMID"}
        cells[cname] = {"label": c["type"] + json.dumps(params, sort_keys=True), "conns": c["connections"],
                        "dirs": c["port_directions"]}
        for p, bits in c["connections"].items():
            if c["port_directions"].get(p) == "output":
                for i, b in enumerate(bits):
                    driver_of[b] = (cname, p, i)

    color = {name: hashlib.sha1(c["label"].encode()).hexdigest() for name, c in cells.items()}
    for _ in range(rounds):
        new = {}
        for name, c in cells.items():
            parts = []
            for p in sorted(c["conns"]):
                if c["dirs"].get(p) == "output":
                    continue
                for i, b in enumerate(c["conns"][p]):
                    src = driver_of.get(b, f"const:{b}") if isinstance(b, int) else f"const:{b}"
                    tag = color[src[0]] + f"/{src[1]}/{src[2]}" if isinstance(src, tuple) else src
                    parts.append(f"{p}[{i}]<-{tag}")
            new[name] = hashlib.sha1((color[name] + "|" + ",".join(parts)).encode()).hexdigest()
        color = new
    outputs = []
    for pname, port in top["ports"].items():
        if port["direction"] == "output":
            for i, b in enumerate(port["bits"]):
                src = driver_of.get(b, f"const:{b}") if isinstance(b, int) else f"const:{b}"
                outputs.append(f"out:{pname}[{i}]<-" + (color[src[0]] + f"/{src[1]}/{src[2]}" if isinstance(src, tuple) else src))
    return Counter(color.values()), sorted(outputs)


def same_netlist(before_text, after_text):
    """'tokens_equal' | 'netlist_equal' | 'different' | 'error'."""
    a, b = tokens(before_text), tokens(after_text)
    if a is None or b is None:
        return "error"
    if a == b:
        return "tokens_equal"
    try:
        sa, sb = wl_signature(before_text), wl_signature(after_text)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return "error"
    if sa is None or sb is None:
        return "error"
    return "netlist_equal" if sa == sb else "different"
