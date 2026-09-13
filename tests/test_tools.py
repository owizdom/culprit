"""The tool check reports what is missing with the command that fixes it, and Docker never blocks setup."""
from app import tools

FOUND = {"git": "git version 2.50.1", "iverilog": "Icarus Verilog version 13.0 (stable)", "yosys": "Yosys 0.69",
         "docker": "29.4.1"}


def fake(missing=(), image=True):
    def run(cmd, timeout=30):
        if cmd[0] in missing:
            return None, f"{cmd[0]}: not found"
        if cmd[:3] == ["docker", "images", "-q"]:
            return 0, "3493ee7750e3" if image else ""
        return 0, FOUND[cmd[0]]
    return run


def by_name(result):
    return {row["name"]: row for row in result["tools"]}


def test_everything_installed_is_ready(monkeypatch):
    monkeypatch.setattr(tools, "_run", fake())
    result = tools.check()
    rows = by_name(result)
    assert result["ready"] and rows["Icarus Verilog"]["version"].startswith("Icarus Verilog version 13.0")
    assert rows["Docker"]["version"] == "Docker 29.4.1" and rows["Docker"]["image"]


def test_a_missing_tool_blocks_and_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(tools, "_run", fake(missing=("yosys",)))
    result = tools.check()
    assert not result["ready"]
    assert by_name(result)["Yosys"]["fix"] == tools.SIM_FIX and by_name(result)["git"]["fix"] is None


def test_docker_down_or_without_the_image_does_not_block(monkeypatch):
    monkeypatch.setattr(tools, "_run", fake(missing=("docker",)))
    result = tools.check()
    assert result["ready"] and not by_name(result)["Docker"]["ok"]
    monkeypatch.setattr(tools, "_run", fake(image=False))
    assert tools.check()["ready"] and not by_name(tools.check())["Docker"]["image"]
