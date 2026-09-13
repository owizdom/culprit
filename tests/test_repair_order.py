"""A one-line culprit edit is reverted without the model; a longer one asks the model first and only
falls back to the revert when no fix that keeps the rest of the edit passes."""
from types import SimpleNamespace

import culprit
from localize.hunks import Hunk
from repair import claude

ONE_LINE = Hunk(1, "picorv32.v", 1344, ("\t\t\tcpuregs[latched_rd] <= cpuregs_wrdata;\n",), 1344,
                ("\t\t\tcpuregs[latched_rd ^ 1] <= cpuregs_wrdata;\n",))
TWO_LINES = Hunk(1, "picorv32.v", 10, ("a = 1;\n", "b = 2;\n", "c = 3;\n"), 10, ("a = 1;\n", "B = 2;\n", "C = 3;\n"))


def blame_for(hunk):
    return SimpleNamespace(line_fix=None, culprit=[{hunk.id}], hunks=[hunk])


def quiet(*args, **kwargs):
    pass


def test_one_line_edit_is_reverted_without_the_model(monkeypatch):
    def no_model(*args, **kwargs):
        raise AssertionError("the model was called")
    monkeypatch.setattr(claude, "fix", no_model)
    patch = culprit.repair({}, {}, blame_for(ONE_LINE), "", quiet)
    assert patch["method"] == "revert_edit" and patch["start"] == patch["end"] == 1344


def test_longer_edit_falls_back_to_the_revert_when_the_model_fails(monkeypatch):
    monkeypatch.setattr(claude, "fix", lambda *a, **k: {"trust": "proposed", "attempts": 3, "cost_usd": 0.05,
                                                        "start": 11, "end": 11, "replacement": "B = 3;\n"})
    patch = culprit.repair({}, {}, blame_for(TWO_LINES), "", quiet)
    assert patch["method"] == "revert_edit" and (patch["start"], patch["end"]) == (11, 12)
    assert patch["replacement"] == "b = 2;\nc = 3;\n"
    assert patch["attempts"] == 3 and patch["cost_usd"] == 0.05


FIRST = Hunk(1, "picorv32.v", 10, ("a = 1;\n",), 10, ("a = 7;\n",))
SECOND = Hunk(2, "picorv32.v", 40, ("x = 1;\n",), 40, ("x = 9;\n",))
HEAD = "".join(f"l{i}\n" for i in range(1, 10)) + "a = 7;\n" + "".join(f"m{i}\n" for i in range(11, 40)) + "x = 9;\nz\n"


def test_a_bug_over_two_edits_is_fixed_by_undoing_both_when_the_simulator_passes_it(monkeypatch):
    monkeypatch.setattr(claude, "fix", lambda *a, **k: {"trust": "proposed", "attempts": 3, "cost_usd": 0.1})
    simulated = {}

    def run(tree):
        simulated["text"] = tree["picorv32.v"]
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(culprit.icarus, "run", run)
    blame = SimpleNamespace(line_fix=None, culprit=[{1, 2}], hunks=[SECOND, FIRST])
    patch = culprit.repair({}, {"picorv32.v": HEAD}, blame, "", quiet)
    assert patch["method"] == "revert_edits" and [e["start"] for e in patch["edits"]] == [10, 40]
    assert "a = 1;\n" in simulated["text"] and "x = 1;\n" in simulated["text"] and "a = 7" not in simulated["text"]
    assert patch["attempts"] == 3 and patch["cost_usd"] == 0.1
    assert culprit.culprit_lines(blame, patch) == {10, 40}


def test_undoing_several_edits_is_not_offered_when_the_simulator_still_fails(monkeypatch):
    proposal = {"trust": "proposed", "attempts": 3, "cost_usd": 0.1}
    monkeypatch.setattr(claude, "fix", lambda *a, **k: proposal)
    monkeypatch.setattr(culprit.icarus, "run", lambda tree: SimpleNamespace(passed=False))
    blame = SimpleNamespace(line_fix=None, culprit=[{1, 2}], hunks=[FIRST, SECOND])
    assert culprit.repair({}, {"picorv32.v": HEAD}, blame, "", quiet) is proposal


def test_the_team_sees_every_edit_of_a_fix_over_several_edits():
    from apps import act
    patch = {"trust": "confirmed", "start": 10, "end": 10, "replacement": "a = 1;\n", "method": "revert_edits", "edits": [
        {"start": 10, "end": 10, "before": "a = 7;\n", "replacement": "a = 1;\n"},
        {"start": 40, "end": 40, "before": "x = 9;\n", "replacement": "x = 1;\n"}]}
    text = act.diff_text(SimpleNamespace(hunks=[]), patch)
    assert "@@ line 40 @@" in text and "- a = 7;" in text and "+ x = 1;" in text
    assert "2 edits" in act.how_made(patch)


def test_longer_edit_keeps_a_confirmed_model_fix(monkeypatch):
    confirmed = {"trust": "confirmed", "attempts": 1, "cost_usd": 0.02, "start": 12, "end": 12, "replacement": "C = 2;\n"}
    monkeypatch.setattr(claude, "fix", lambda *a, **k: confirmed)
    assert culprit.repair({}, {}, blame_for(TWO_LINES), "", quiet) is confirmed
