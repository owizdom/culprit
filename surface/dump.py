"""Generate a tiny Verilog module that dumps only the signals we care about.

It is compiled as a second top-level module next to the untouched testbench
(`iverilog -s testbench -s culprit_dump ...`), so the regression itself never changes.
The core's hierarchy and the clock timing come from design.py.
"""
import design

_D = design.DEFAULTS
CORE = _D["hierarchy"]["sim_core"]
RESET_CYCLES = _D["timing"]["reset_cycles"]     # testbench.v holds resetn low for 100 clock edges
PERIOD_NS = _D["timing"]["period_ns"]           # clk toggles every 5 ns


def cycle_to_ns(cycle):
    timing = design.get()["timing"]
    return (cycle + timing["reset_cycles"]) * timing["period_ns"]


def ns_to_cycle(t_ns):
    timing = design.get()["timing"]
    return t_ns // timing["period_ns"] - timing["reset_cycles"]


def module(signals, vcd="culprit.vcd", start_cycle=None, end_cycle=None):
    """signals are names relative to the core, e.g. 'reg_pc' or 'picorv32_core.reg_pc'.

    With an end_cycle the simulation stops there, so a passing base run does not keep going for its
    full 470k cycles after the part we need.
    """
    cfg = design.get()
    core, prefix = cfg["hierarchy"]["sim_core"], cfg["hierarchy"].get("netlist_prefix") or ""
    lines = ["`timescale 1 ns / 1 ps", "module culprit_dump;", "  initial begin",
             '    $dumpfile("%s");' % vcd]
    for s in signals:
        s = s.split(prefix, 1)[-1] if prefix else s
        lines.append("    $dumpvars(0, %s.%s);" % (core, s))
    if start_cycle is not None:
        lines.append("    $dumpoff;")
        lines.append("    #%d $dumpon;" % cycle_to_ns(max(start_cycle, 0)))
        if end_cycle is not None:
            lines.append("    #%d $dumpoff;" % ((end_cycle - max(start_cycle, 0)) * cfg["timing"]["period_ns"]))
            lines.append("    $finish;")
    lines += ["  end", "endmodule", ""]
    return "\n".join(lines)
