"""The chip tools CULPRIT runs, checked live on this computer.

git, Icarus Verilog and Yosys are required. Docker is optional: the firmware for picorv32-ci main ships with CULPRIT
(sim/fw-seed), so Docker is only needed when a pull request changes Makefile, firmware/ or tests/.
"""
import subprocess
import sys
import threading

import design
from paths import ROOT

if sys.platform == "darwin":
    GIT_FIX, SIM_FIX = "xcode-select --install", "brew install icarus-verilog yosys"
elif sys.platform == "win32":
    GIT_FIX = "Install Git for Windows: https://git-scm.com/download/win"
    SIM_FIX = ("Install OSS CAD Suite (Icarus Verilog and Yosys) and add its bin folder to PATH: "
               "https://github.com/YosysHQ/oss-cad-suite-build/releases")
else:
    GIT_FIX, SIM_FIX = "sudo apt install git", "sudo apt install iverilog yosys"
_build = {"state": "idle", "log": ""}
_lock = threading.Lock()


def _run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def _row(name, cmd, fix, required=True):
    code, out = _run(cmd)
    ok = code == 0
    return {"name": name, "ok": ok, "required": required, "version": out.splitlines()[0][:80] if ok and out else None,
            "fix": None if ok else fix}


def docker():
    row = _row("Docker", ["docker", "info", "--format", "{{.ServerVersion}}"],
               "Install Docker Desktop and start it: https://www.docker.com/products/docker-desktop/", required=False)
    row["note"] = "Only needed when a pull request changes the firmware."
    row["image"] = False
    if row["ok"]:
        row["version"] = f"Docker {row['version']}"
        # `docker image inspect` misses locally built images under Docker Desktop's containerd store; the listing does not.
        fw = design.get()["firmware"] or design.DEFAULTS["firmware"]
        code, out = _run(["docker", "images", "-q", fw["image"]])
        row["image"] = code == 0 and bool(out)
    row["build"] = dict(_build)
    return row


def check():
    rows = [_row("git", ["git", "--version"], GIT_FIX),
            _row("Icarus Verilog", ["iverilog", "-V"], SIM_FIX),
            _row("Yosys", ["yosys", "-V"], SIM_FIX)]
    if design.get()["firmware"]:        # a design without a firmware step never needs Docker
        rows.append(docker())
    return {"tools": rows, "ready": all(r["ok"] for r in rows if r["required"])}


def build_toolchain():
    """Build the firmware toolchain image in the background; poll with docker()["build"]."""
    fw = design.get()["firmware"]
    if not fw:
        return {"state": "failed", "log": "This design has no firmware step, so it needs no toolchain image."}
    with _lock:
        if _build["state"] == "building":
            return dict(_build)
        _build.update(state="building", log="")

    def work():
        code, out = _run(["docker", "build", "-t", fw["image"], str(ROOT / fw["dockerfile_dir"])], timeout=1800)
        _build.update(state="done" if code == 0 else "failed", log=out[-2000:])

    threading.Thread(target=work, daemon=True).start()
    return dict(_build)
