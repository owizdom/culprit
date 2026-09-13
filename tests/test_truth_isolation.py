"""The agent must never see the answers. Only corpus/ (which writes them) and evals/ (which grades) may
mention truth.json."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"corpus", "evals", "tests"}


def test_only_the_builder_and_the_grader_touch_truth_json():
    offenders = []
    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT)
        if rel.parts[0] in ALLOWED or rel.parts[0].startswith(".") or "node_modules" in rel.parts:
            continue
        if "truth.json" in py.read_text(errors="ignore") or "truth_for" in py.read_text(errors="ignore"):
            offenders.append(str(rel))
    assert offenders == []
