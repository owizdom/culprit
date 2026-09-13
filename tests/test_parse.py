from pathlib import Path

from sim.icarus import parse

FIXTURES = Path(__file__).parent / "fixtures"


def test_clean_regression_passes():
    r = parse((FIXTURES / "pass.log").read_text())
    assert r.passed and r.status == "pass"
    assert r.trap_cycle == 471610
    assert r.failing_tests == []


def test_testbug_001_out_of_bounds_write_is_a_failure_with_a_cycle():
    r = parse((FIXTURES / "testbug001.log").read_text())
    assert not r.passed and r.status == "fail"
    assert r.trap_reason.startswith("OUT-OF-BOUNDS MEMORY WRITE TO")
    assert 8200 < r.trap_cycle < 8250          # from "$finish called at 83225000 (1ps)"


def test_testbug_002_trap_then_error():
    r = parse((FIXTURES / "testbug002.log").read_text())
    assert not r.passed and r.status == "fail"
    assert r.trap_cycle == 8224


def test_passed_only_counts_after_trap_line():
    log = "multest ERROR!\nTRAP after 5 clock cycles\nERROR!\n"
    assert not parse(log).passed


def test_timeout_is_not_a_pass():
    assert parse("TIMEOUT\n").status == "timeout"
