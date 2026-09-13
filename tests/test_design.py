"""design.py: with no design section nothing changes; a design section alone points CULPRIT at another chip."""
import hashlib
from pathlib import Path

import design
from apps import http
from sim import icarus

TREE = {"picorv32.v": "module picorv32; endmodule\n", "testbench.v": "module testbench; endmodule\n",
        "firmware/start.S": "nop\n", "README": "x\n"}
PICORV32_HIERARCHY = ("hierarchy -top picorv32_axi -chparam COMPRESSED_ISA 1 -chparam ENABLE_MUL 1 -chparam ENABLE_DIV 1 "
                      "-chparam ENABLE_IRQ 1 -chparam ENABLE_TRACE 1")
CUSTOM = {"github": {"repo": "someone/blinky"},
          "design": {"name": "Blinky", "rtl_file": "rtl/blinky.v", "sources": ["tb/tb.v", "rtl/blinky.v"],
                     "sim": {"top": "tb", "defines": []}, "firmware": None,
                     "log": {"pass": r"^BLINKY OK$", "end": None}, "corpus_features": False}}


class Done:
    def __init__(self, stdout=""):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def fake_simulator(monkeypatch, tmp_path, log):
    calls = []

    def fake_run(cmd, cwd=None, **kwargs):
        files = sorted(p.relative_to(cwd).as_posix() for p in Path(cwd).rglob("*") if p.is_file())
        calls.append({"cmd": list(cmd), "files": files, "timeout": kwargs.get("timeout")})
        return Done(log if cmd[0] == "vvp" else "")

    monkeypatch.setattr(icarus, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(icarus.subprocess, "run", fake_run)
    return calls


def test_no_design_section_is_picorv32_and_has_no_digest(monkeypatch):
    assert "design" not in http.read_yaml(http.CONFIG)          # the example in config.yaml stays commented out
    monkeypatch.setattr(http, "config", lambda: {"github": {"repo": "owizdom/picorv32-ci"}})
    assert design.get() == design.DEFAULTS
    assert design.digest() == ""
    assert design.hierarchy() == PICORV32_HIERARCHY and design.defines() == ["-DCOMPRESSED_ISA"]


def test_default_design_runs_todays_commands_under_todays_cache_key(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "config", lambda: {})
    hexfile = tmp_path / "fw" / "097eba9c17594054" / "firmware.hex"
    hexfile.parent.mkdir(parents=True)
    hexfile.write_text("00000013\n")
    monkeypatch.setattr(icarus, "firmware", lambda tree: (hexfile, ""))
    calls = fake_simulator(monkeypatch, tmp_path, "TRAP after 471610 clock cycles\nALL TESTS PASSED.\n")
    dump = {"culprit_dump.v": "module culprit_dump; endmodule\n"}
    for extra, tops in ((None, ["-s", "testbench"]), (dump, ["-s", "testbench", "-s", "culprit_dump"])):
        calls.clear()
        result = icarus.run(TREE, extra_verilog=extra)
        # The formula sim/icarus.py used before design.py existed.
        old_key = hashlib.sha256((icarus.tree_hash(TREE, ("picorv32.v", "testbench.v")) + hexfile.parent.name +
                                  repr(sorted((extra or {}).items()))).encode()).hexdigest()[:20]
        assert result.passed and result.trap_cycle == 471610 and result.key == old_key
        assert [c["cmd"] for c in calls] == [
            ["iverilog", "-DCOMPRESSED_ISA", *tops, "-o", "tb.vvp", "testbench.v", "picorv32.v", *(extra or {})],
            ["vvp", "-N", "tb.vvp", "+firmware=firmware.hex"]]
        assert calls[1]["timeout"] == 1800
        assert calls[0]["files"] == sorted(["firmware.hex", "picorv32.v", "testbench.v", *(extra or {})])


def test_default_firmware_key_is_the_tree_hash_of_its_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "config", lambda: {})
    monkeypatch.setattr(icarus, "CACHE", tmp_path / "cache")
    tree = {"Makefile": "all:\n", "firmware/x.c": "int x;\n", "tests/add.S": "nop\n", "picorv32.v": "module m;\n"}
    key = icarus.tree_hash(tree, ("Makefile", "firmware", "tests"))
    seeded = tmp_path / "seed" / key / "firmware.hex"
    seeded.parent.mkdir(parents=True)
    seeded.write_text("00000013\n")
    monkeypatch.setattr(icarus, "SEED", tmp_path / "seed")
    assert icarus.firmware(tree) == (seeded, "")


def test_a_design_section_points_the_simulation_at_another_chip(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "config", lambda: CUSTOM)

    def no_firmware(tree):
        raise AssertionError("a design without a firmware step must not build firmware")

    monkeypatch.setattr(icarus, "firmware", no_firmware)
    calls = fake_simulator(monkeypatch, tmp_path, "starting\nBLINKY OK\n")
    tree = {"rtl/blinky.v": "module blinky; endmodule\n", "tb/tb.v": "module tb; endmodule\n", "README": "x\n"}
    result = icarus.run(tree)
    assert design.digest() != ""
    assert result.passed and result.status == "pass" and result.trap_cycle is None
    assert calls[0]["cmd"] == ["iverilog", "-s", "tb", "-o", "tb.vvp", "tb/tb.v", "rtl/blinky.v"]
    assert calls[0]["files"] == ["rtl/blinky.v", "tb/tb.v"]
    assert calls[1]["cmd"] == ["vvp", "-N", "tb.vvp"]
    # log.end is null, so the pass marker counts anywhere in the log, and PicoRV32's marker means nothing here.
    assert icarus.parse("BLINKY OK\n").passed
    assert not icarus.parse("TRAP after 5 clock cycles\nALL TESTS PASSED.\n").passed


def test_a_design_without_firmware_or_corpus_hides_docker_and_test_run(monkeypatch):
    from app import testrun, tools
    from apps import act
    monkeypatch.setattr(http, "config", lambda: CUSTOM)
    monkeypatch.setattr(tools, "_run", lambda cmd, timeout=30: (0, "ok"))
    assert [r["name"] for r in tools.check()["tools"]] == ["git", "Icarus Verilog", "Yosys"]
    assert testrun.start() == {"error": "Test Run uses the PicoRV32 corpus; it is off for this design."}
    assert act.sweep_kill_rate() is None
