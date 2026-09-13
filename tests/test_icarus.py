"""The simulator never sees a long firmware path: testbench.v stores +firmware in 128 characters."""
from pathlib import Path

from sim import icarus


def test_firmware_is_passed_by_a_short_name_whatever_the_install_path(monkeypatch, tmp_path):
    deep = tmp_path / ("x" * 200) / "firmware.hex"
    deep.parent.mkdir(parents=True)
    deep.write_text("00000013\n")
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "TRAP after 471610 clock cycles\nALL TESTS PASSED.\n", ""

    def fake_run(cmd, cwd=None, **kwargs):
        if cmd[0] == "vvp":
            seen["args"] = cmd
            seen["hex"] = (Path(cwd) / "firmware.hex").read_text()
        return Done()

    monkeypatch.setattr(icarus, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(icarus, "firmware", lambda tree: (deep, ""))
    monkeypatch.setattr(icarus.subprocess, "run", fake_run)
    result = icarus.run({"picorv32.v": "module m; endmodule\n", "testbench.v": "module t; endmodule\n"})
    assert result.passed
    assert "+firmware=firmware.hex" in seen["args"] and all(len(a) < 128 for a in seen["args"])
    assert seen["hex"] == "00000013\n"
