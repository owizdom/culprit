"""The propagation path for a known bug must be real signal flow, confirmed in causal order.

Needs the PicoRV32 checkout (CULPRIT_CHIP_REPO, default ../picorv32-ci), Icarus and Yosys; skipped without them.
"""
import os
import shutil
from pathlib import Path

import pytest

CHECKOUT = Path(os.environ.get("CULPRIT_CHIP_REPO") or Path(__file__).resolve().parent.parent.parent / "picorv32-ci")
pytestmark = pytest.mark.skipif(not (CHECKOUT.exists() and shutil.which("iverilog") and shutil.which("yosys")),
                                reason="needs the picorv32-ci checkout, iverilog and yosys")


@pytest.fixture(scope="module")
def path():
    from sim import icarus
    from surface import propagation
    base = icarus.read_tree(CHECKOUT)
    lines = base["picorv32.v"].splitlines(keepends=True)
    assert "cpuregs[latched_rd] <= cpuregs_wrdata;" in lines[1343]
    lines[1343] = lines[1343].replace("cpuregs[latched_rd]", "cpuregs[latched_rd ^ 1]")
    head = dict(base, **{"picorv32.v": "".join(lines)})
    return propagation.render(base, head, {1344})


def test_path_starts_at_the_line_and_ends_at_the_failure(path):
    assert not path.get("error"), path.get("error")
    assert path["nodes"][0]["kind"] == "line" and path["nodes"][0]["src"] == "picorv32.v:1344"
    assert path["nodes"][-1]["kind"] == "observable"


def test_several_hops_are_confirmed_by_waveform(path):
    assert path["causal"]
    assert path["confirmed_hops"] >= 2


def test_confirmed_hops_never_go_back_in_time(path):
    cycles = [n["first_diff_cycle"] for n in path["nodes"][1:]
              if n["trust"] == "confirmed" and n.get("first_diff_cycle") is not None]
    assert cycles == sorted(cycles)


def test_every_confirmed_hop_has_differing_samples(path):
    for n in path["nodes"]:
        if n["kind"] in ("signal", "register") and n["trust"] == "confirmed":
            assert n["base"] != n["head"], n["label"]
