"""How the bug travelled: from the culprit line, through signal flow, to what the testbench saw.

1. Elaborate the head design with Yosys under the testbench's parameters.
2. Seed from the cells built from the culprit lines.
3. Pass 1: dump the core's bus and trace ports in base and head, only up to where head stopped.
   The first port that differs is where the bug became visible, and when.
4. Pass 2: dump every named signal of the core in a window before that moment, and note when each
   first differed.
5. Walk the netlist backwards from that port to a seed, only through signals that changed no later
   than the signal after them. Every named hop on that path is CONFIRMED by the waveforms.
   If no such path exists, fall back to the shortest path and mark its hops PROPOSED.
6. Return path.json for the window: the hops in order, each with its base and head samples.

The RTL file, Yosys top and parameters, core hierarchy, observed ports and memories come from design.py.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import design
from localize import graph
from localize.confirm import describe
from sim import icarus
from surface import dump

_D = design.DEFAULTS
OBSERVED_PORTS = list(_D["observed_ports"])
YOSYS_PARAMS = design.chparams(_D)
WINDOW = 5000
SAMPLES = 48


def netlist(tree):
    cfg = design.get()
    rtl = cfg["rtl_file"]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:     # Windows can hold a file briefly
        source = Path(tmp, rtl)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(tree[rtl])
        subprocess.run(["yosys", "-q", "-p", f"read_verilog {rtl}; {design.hierarchy(cfg)}; "
                        "proc; flatten; opt_clean; write_json net.json"], cwd=tmp, check=True, capture_output=True)
        return graph.load(Path(tmp, "net.json"))


def timescale_ps(vcd_path):
    head = Path(vcd_path).read_text(errors="ignore")[:2000]
    m = re.search(r"\$timescale\s*(\d+)\s*(s|ms|us|ns|ps|fs)", head)
    if not m:
        return 1
    return int(m.group(1)) * {"s": 10**12, "ms": 10**9, "us": 10**6, "ns": 10**3, "ps": 1, "fs": 10**-3}[m.group(2)]


def first_difference(a, b):
    """Earliest time two change lists disagree, or None. Each list is [(time, value), ...]."""
    times = sorted({t for t, _ in a} | {t for t, _ in b})
    ia = ib = 0
    va = vb = None
    for t in times:
        while ia < len(a) and a[ia][0] <= t:
            va = a[ia][1]
            ia += 1
        while ib < len(b) and b[ib][0] <= t:
            vb = b[ib][1]
            ib += 1
        if va != vb:
            return t
    return None


def to_hex(value):
    """VCD binary ('1010', 'x', '1x0z') to hex; any unknown bit makes that nibble 'x'."""
    if len(value) == 1:
        return value
    value = value.zfill((len(value) + 3) // 4 * 4)
    out = []
    for i in range(0, len(value), 4):
        nibble = value[i:i + 4]
        out.append("x" if any(c not in "01" for c in nibble) else "%x" % int(nibble, 2))
    return "".join(out)


def cycle_of(t, unit_ps):
    return dump.ns_to_cycle(t * unit_ps // 1000)


def samples(changes, around_cycle, unit_ps, n=SAMPLES):
    """Value changes near the divergence, as [cycle, hex]."""
    points = [[cycle_of(t, unit_ps), to_hex(v)] for t, v in changes]
    before = [p for p in points if p[0] < around_cycle][-(n // 3):]
    after = [p for p in points if p[0] >= around_cycle][:n - len(before)]
    return before + after


def simulate_dump(tree, signals, start=None, end=None, workdir=None):
    text = dump.module(signals, vcd="culprit.vcd", start_cycle=start, end_cycle=end)
    keep = Path(workdir or tempfile.mkdtemp())
    result = icarus.run(tree, extra_verilog={"culprit_dump.v": text}, keep=keep)
    vcd = keep / "culprit.vcd"
    if not vcd.exists():
        keep.mkdir(parents=True, exist_ok=True)
        (keep / "dump_failed.log").write_text(result.log)
        return {}, 1
    return graph.read_vcd(vcd), timescale_ps(vcd)


PLAIN = re.compile(r"[A-Za-z_]\w*")
REGFILE_WORDS = _D["memories"]["cpuregs"]


def dumpable(d):
    """Core signals the simulator can address by name, plus each word of each memory."""
    cfg = design.get()
    prefix = cfg["hierarchy"].get("netlist_prefix") or ""
    memories = cfg.get("memories") or {}
    names = set()
    for n in d["signals"]:
        if n.startswith(prefix):
            short = n[len(prefix):]
            if PLAIN.fullmatch(short):
                names.add(short)
    for mem in memories:
        names.discard(mem)
    return sorted(names) + [f"{mem}[{i}]" for mem, words in memories.items() for i in range(words)]


def by_core_name(traces):
    """{'reg_pc': changes, ...} keyed by the name below the core (picorv32_core. by default)."""
    prefix = design.core_prefix()
    out = {}
    for key, changes in traces.items():
        if prefix in key:
            out.setdefault(key.split(prefix, 1)[1].lstrip("\\"), changes)
    return out


def render(base_tree, head_tree, culprit_lines, workdir=None):
    cfg = design.get()
    rtl, prefix = cfg["rtl_file"], cfg["hierarchy"].get("netlist_prefix") or ""
    ports, memories = list(cfg["observed_ports"]), cfg.get("memories") or {}
    work = Path(workdir or tempfile.mkdtemp(prefix="culprit-path-"))
    d = netlist(head_tree)
    seeds = graph.cells_on_lines(d, set(culprit_lines))
    if not seeds:
        return {"error": "no netlist cells come from the culprit lines", "nodes": []}

    head_run = icarus.run(head_tree)
    end = (head_run.trap_cycle or 0) + 100 if not head_run.passed else None

    base_ports, unit = simulate_dump(base_tree, ports, 0, end, work / "base1")
    head_ports, _ = simulate_dump(head_tree, ports, 0, end, work / "head1")
    base_ports, head_ports = by_core_name(base_ports), by_core_name(head_ports)
    observed = None
    for port in ports:
        if port in base_ports and port in head_ports:
            t = first_difference(base_ports[port], head_ports[port])
            if t is not None and (observed is None or t < observed[1]):
                observed = (port, t)
    if observed is None:
        return {"error": "base and head never differ at the core ports", "nodes": []}
    port, t_obs = observed
    obs_cycle = cycle_of(t_obs, unit)
    target = d["signals"].get(prefix + port)
    if target is None:
        return {"error": f"{port} is not in the netlist", "nodes": []}

    start, stop = max(obs_cycle - WINDOW, 0), obs_cycle + 50
    # A signal renamed by the pull request exists in only one of the two designs; asking the other
    # simulation to dump it would fail to compile and lose every waveform. Compare what both have.
    in_base = set(dumpable(netlist(base_tree)))
    names = [n for n in dumpable(d) if n in in_base]
    base_sig, unit2 = simulate_dump(base_tree, names, start, stop, work / "base2")
    head_sig, _ = simulate_dump(head_tree, names, start, stop, work / "head2")
    base_sig, head_sig = by_core_name(base_sig), by_core_name(head_sig)
    first_diff = {}
    for short in names:
        if short in base_sig and short in head_sig:
            t = first_difference(base_sig[short], head_sig[short])
            first_diff[prefix + short] = None if t is None else cycle_of(t, unit2)
    for mem in memories:
        word_diffs = [c for n, c in first_diff.items() if f"{mem}[" in n and c is not None]
        if any(f"{mem}[" in n for n in first_diff):
            first_diff[prefix + mem] = min(word_diffs) if word_diffs else None

    cells = graph.causal_path(d, seeds, set(target["bits"]), obs_cycle, first_diff) if first_diff else None
    causal = cells is not None
    if not causal:
        cells = graph.shortest_path(d, seeds, set(target["bits"]))
    if not cells:
        return {"error": f"no path from the culprit lines to {port}", "nodes": []}
    hops = graph.named_hops(d, cells)

    line = min(culprit_lines)
    nodes = [{"id": "n0", "label": f"{rtl}:{line}", "kind": "line", "src": f"{rtl}:{line}",
              "trust": "confirmed", "first_diff_cycle": None}]
    for h in hops:
        short = h["name"].split(prefix, 1)[-1] if prefix else h["name"]
        mem = next((m for m in memories if m in h["name"]), next(iter(memories), None))
        mem_diff = first_diff.get(prefix + mem) if mem else None
        node = {"id": f"n{len(nodes)}", "label": short, "src": h["src"], "first_diff_cycle": None,
                "kind": "memory" if h["memory"] else ("register" if h["register"] else "signal"),
                "trust": "unobservable" if h["memory"] else "proposed", "base": [], "head": []}
        if h["memory"] and mem_diff is not None:
            c = mem_diff
            word = min((n for n, v in first_diff.items() if f"{mem}[" in n and v == c), default=None)
            node.update(trust="confirmed" if causal else "proposed", first_diff_cycle=c)
            if word:
                w = word.split(prefix, 1)[1] if prefix else word
                node.update(label=w, base=samples(base_sig[w], c, unit2), head=samples(head_sig[w], c, unit2))
        elif not h["memory"] and short in base_sig and short in head_sig:
            c = first_diff.get(prefix + short)
            if c is None:
                node["trust"] = "contradicted"
            else:
                node["first_diff_cycle"] = c
                node["trust"] = "confirmed" if causal else "proposed"
                node["base"] = samples(base_sig[short], c, unit2)
                node["head"] = samples(head_sig[short], c, unit2)
        nodes.append(node)

    # A hop cannot be caused by something that changed after it. Walk from the observation back to
    # the line; any confirmed hop that differs later than the confirmed hop after it loses CONFIRMED.
    later = obs_cycle
    for node in reversed(nodes[1:]):
        c = node.get("first_diff_cycle")
        if node["trust"] == "confirmed" and c is not None:
            if c > later:
                node["trust"] = "proposed"
            else:
                later = c

    failure = describe(head_run) if not head_run.passed else "differs from base"
    nodes.append({"id": f"n{len(nodes)}", "label": failure, "kind": "observable", "src": port,
                  "trust": "confirmed", "first_diff_cycle": obs_cycle, "trap_cycle": head_run.trap_cycle})
    confirmed = sum(1 for n in nodes if n["kind"] in ("signal", "register") and n["trust"] == "confirmed")
    return {"nodes": nodes, "window": {"start": start, "end": stop}, "causal": causal, "confirmed_hops": confirmed,
            "observable": {"port": port, "cycle": obs_cycle, "failure": failure, "trap_cycle": head_run.trap_cycle,
                           "test": (head_run.failing_tests or [None])[0]}}
