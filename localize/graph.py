"""Signal graph of a Yosys netlist, trimmed from picorv32-depgraph.

A chip is registers with logic between them, so "how did this change reach that output"
is a walk through wires and cells. Yosys writes the netlist as JSON: every wire bit has
a number and every cell carries the source lines it came from.
"""
import json
import re
from collections import deque

import design

REGISTER_TYPES = {"$dff", "$adff", "$dffe", "$adffe", "$sdff", "$sdffe", "$dffsr", "$dlatch", "$adlatch",
                  "$memwr", "$memwr_v2"}


def src_lines(obj):
    out = set()
    for part in obj.get("attributes", {}).get("src", "").split("|"):
        m = re.search(r":(\d+)\.\d+-(\d+)\.\d+$", part)
        if m:
            out.update(range(int(m.group(1)), int(m.group(2)) + 1))
    return out


def where(obj):
    src = obj.get("attributes", {}).get("src", "").split("|")[0]
    if ":" not in src:
        return None
    path, rest = src.rsplit(":", 1)
    return path.split("/")[-1] + ":" + rest.split(".")[0]


def load(path):
    net = json.load(open(path))
    top = next(iter(net["modules"].values()))

    signals, names_of = {}, {}
    for name, net in top["netnames"].items():
        if net.get("hide_name"):
            continue
        bits = [b for b in net["bits"] if isinstance(b, int)]
        signals[name] = {"bits": bits, "where": where(net), "lines": src_lines(net)}
        for b in bits:
            names_of.setdefault(b, []).append(name)

    cells = []
    for c in top["cells"].values():
        conns = {p: (c["port_directions"].get(p, "input"), bits) for p, bits in c["connections"].items()}
        cells.append({
            "type": c["type"], "where": where(c), "lines": src_lines(c),
            "memid": c.get("parameters", {}).get("MEMID"),
            "ins": {b for d, bs in conns.values() if d != "output" for b in bs if isinstance(b, int)},
            "outs": {b for d, bs in conns.values() if d == "output" for b in bs if isinstance(b, int)},
            "is_register": c["type"] in REGISTER_TYPES,
        })

    # Memory write and read cells share a MEMID but no wire. Give each memory one invented
    # negative bit that the write drives and the reads depend on, or every walk dies there.
    invented = {}
    for c in cells:
        if not c["memid"]:
            continue
        b = invented.setdefault(c["memid"], -len(invented) - 1)
        if c["type"].startswith("$memwr"):
            c["outs"].add(b)
        elif c["type"].startswith("$memrd"):
            c["ins"].add(b)
    for memid, b in invented.items():
        names_of.setdefault(b, []).append(memid.lstrip("\\"))

    writers, readers = {}, {}
    for i, c in enumerate(cells):
        for b in c["outs"]:
            writers.setdefault(b, []).append(i)
        for b in c["ins"]:
            readers.setdefault(b, []).append(i)

    return {"signals": signals, "names_of": names_of, "cells": cells, "writers": writers, "readers": readers,
            "core_prefix": design.get()["hierarchy"].get("netlist_prefix") or ""}


def cells_on_lines(d, lines):
    return {i for i, c in enumerate(d["cells"]) if c["lines"] & lines}


def shortest_path(d, seed_cells, target_bits):
    """Cells from a seed to a writer of target_bits, found by walking backwards from the target.

    Returns the cell indices in signal-flow order (seed first), or None if the target is not
    reachable from any seed.
    """
    parent, seen = {}, set()
    queue = deque()
    for b in target_bits:
        for i in d["writers"].get(b, []):
            if i not in seen:
                seen.add(i)
                parent[i] = None
                queue.append(i)
    while queue:
        i = queue.popleft()
        if i in seed_cells:
            path = [i]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            return path
        for b in d["cells"][i]["ins"]:
            for j in d["writers"].get(b, []):
                if j not in seen:
                    seen.add(j)
                    parent[j] = i
                    queue.append(j)
    return None


def causal_path(d, seed_cells, target_bits, target_cycle, first_diff):
    """Like shortest_path, but only through signals the waveforms show changing in time.

    first_diff maps a signal name to the first clock cycle its value differed between base and head
    (None when it never differed). Walking backwards from the target, a named signal may only be used
    if it differed no later than the signal after it. Unnamed cells and memories inherit the bound.
    """
    parent, best = {}, {}
    queue = deque()
    for b in target_bits:
        for i in d["writers"].get(b, []):
            if i not in best:
                best[i] = target_cycle
                parent[i] = None
                queue.append(i)
    while queue:
        i = queue.popleft()
        if i in seed_cells:
            path = [i]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            return path
        bound = best[i]
        for b in d["cells"][i]["ins"]:
            for j in d["writers"].get(b, []):
                name = best_name(d, d["cells"][j]["outs"] & d["cells"][i]["ins"])
                new_bound = bound
                if name is not None and name in first_diff and b >= 0:
                    t = first_diff[name]
                    if t is None or t > bound:
                        continue
                    new_bound = t
                if j not in best or new_bound > best[j]:
                    first_visit = j not in best
                    best[j] = new_bound
                    parent[j] = i
                    if first_visit:
                        queue.append(j)
    return None


def best_name(d, bits):
    """The most readable signal name carried by any of these bits (prefer short, core-level names)."""
    names = {n for b in bits for n in d["names_of"].get(b, [])}
    if not names:
        return None
    # The flattened netlist also names these bits by the wrapper's ports (pcpi_rs1, trace_data).
    # Prefer the name inside the core, which is the one the simulator hierarchy and the dumps use.
    prefix = d.get("core_prefix")
    if prefix is None:
        prefix = design.get()["hierarchy"].get("netlist_prefix") or ""
    return min(names, key=lambda n: (not n.startswith(prefix), n.count("."), len(n), n))


def named_hops(d, path):
    """Collapse a cell path to the named signals it passes through, keeping where each came from."""
    hops = []
    for a, b in zip(path, path[1:] + [None]):
        cell = d["cells"][a]
        link = cell["outs"] & d["cells"][b]["ins"] if b is not None else cell["outs"]
        name = best_name(d, link)
        if name is None or (hops and hops[-1]["name"] == name):
            continue
        signal = d["signals"].get(name)
        hops.append({"name": name, "cell": a, "type": cell["type"],
                     "src": (signal and signal["where"]) or cell["where"],
                     "register": cell["is_register"], "memory": any(x < 0 for x in link)})
    return hops


def read_vcd(path):
    """{signal path: [(time, value), ...]} from a VCD file."""
    codes, scope, traces, t, in_header = {}, [], {}, 0, True
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        if in_header:
            if line.startswith("$scope"):
                scope.append(line.split()[2])
            elif line.startswith("$upscope"):
                scope and scope.pop()
            elif line.startswith("$var"):
                parts = line.split()
                codes.setdefault(parts[3], []).append(".".join(scope + [parts[4]]))
            elif line.startswith("$enddefinitions"):
                in_header = False
            continue
        if line.startswith("#"):
            t = int(line[1:])
        elif line[0] in "bB":
            value, _, code = line[1:].partition(" ")
            for n in codes.get(code.strip(), []):
                traces.setdefault(n, []).append((t, value))
        elif line[0] not in "rR$":
            for n in codes.get(line[1:], []):
                traces.setdefault(n, []).append((t, line[0]))
    return traces
