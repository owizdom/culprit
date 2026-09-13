"""Turn results/raw/*.jsonl into results/REPORT.md.

Every rate carries a 95% Wilson interval; zero events use the exact one-sided bound.
"""
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evals.power import wilson, zero_event_upper  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "results" / "raw"
NAMES = {"A": "LLM only", "B": "Delta debugging + circuit proof", "C": "CULPRIT"}
RATE_METRICS = [("hunk_loc", "Culprit edit found"), ("line_loc", "Exact line found"), ("validated_fix", "Fix passes"),
                ("intent_fix", "Fix keeps the author's work"), ("wrong_blame", "Wrong blame (lower is better)"),
                ("null_correct", "Correct on non-RTL / not-reproduced")]


def rate(rows, key):
    vals = [r[key] for r in rows if key in r and r[key] is not None]
    k, n = sum(bool(v) for v in vals), len(vals)
    if n == 0:
        return {"k": 0, "n": 0, "lo": None, "hi": None}
    if k == 0:
        return {"k": 0, "n": n, "lo": 0.0, "hi": round(zero_event_upper(n), 3)}
    iv = wilson(k, n)
    return {"k": k, "n": n, "lo": round(iv.lo, 3), "hi": round(iv.hi, 3)}


def summarize():
    arms = []
    for arm in ("A", "B", "C"):
        path = RAW / f"{arm}.jsonl"
        if not path.exists():
            continue
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        rtl = [r for r in rows if r["category"] not in ("non_rtl", "not_reproduced")]
        nulls = [r for r in rows if r["category"] in ("non_rtl", "not_reproduced")]
        metrics = {}
        for key, _ in RATE_METRICS:
            metrics[key] = rate(nulls if key == "null_correct" else (rows if key == "wrong_blame" else rtl), key)
        arms.append({"arm": arm, "name": NAMES[arm], "metrics": metrics, "cases": len(rows),
                     "median_sims": statistics.median([r.get("sims") or 0 for r in rows]) if rows else None,
                     "median_seconds": statistics.median([r.get("seconds") or 0 for r in rows]) if rows else None,
                     "usd_per_case": round(sum(r.get("usd") or 0 for r in rows) / max(len(rows), 1), 4)})
    sweep = ROOT / "corpus" / "sweep.json"
    kill = json.loads(sweep.read_text()) if sweep.exists() else {}
    return {"n_cases": max((a["cases"] for a in arms), default=0), "arms": arms,
            "kill_rate": {"k": kill.get("killed"), "n": kill.get("total")}, "stability": stability(), "history": history(),
            "mutants": tagged("mutants", RATE_METRICS), "combined": combined()}


def rows_of(name):
    path = RAW / f"{name}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def split(rows, key):
    """The rows a metric is measured on: nulls for null handling, every case for wrong blame, RTL cases otherwise."""
    nulls = [r for r in rows if r["category"] in ("non_rtl", "not_reproduced")]
    rtl = [r for r in rows if r["category"] not in ("non_rtl", "not_reproduced")]
    return nulls if key == "null_correct" else (rows if key == "wrong_blame" else rtl)


def stability():
    """Each model arm's rates on every run, and how many cases got the same grade on all of them."""
    out = []
    for arm in ("A", "C"):
        runs = [run for run in [rows_of(arm)] + [rows_of(f"{arm}.run{n}") for n in (2, 3)] if run]
        if len(runs) < 2:
            continue
        cases = sorted(set.intersection(*[{r["case"] for r in run} for run in runs]))
        by_run = [{r["case"]: r for r in run} for run in runs]
        metrics = {}
        for key, _ in RATE_METRICS:
            per_run = [rate(split([b[c] for c in cases], key), key) for b in by_run]
            same = sum(1 for c in cases if len({json.dumps(b[c].get(key)) for b in by_run}) == 1)
            metrics[key] = {"runs": per_run, "same": same, "cases": len(cases)}
        out.append({"arm": arm, "name": NAMES[arm], "runs": len(runs), "cases": len(cases), "metrics": metrics})
    return out


HISTORY_METRICS = [("hunk_loc", "Culprit edit found"), ("line_loc", "Exact line found"), ("validated_fix", "Fix passes"),
                   ("matches_upstream", "Fix is the upstream fix"), ("wrong_blame", "Wrong blame (lower is better)")]


def tagged(tag, metrics):
    """Every arm's rates on one extra corpus, e.g. tag 'mutants' reads results/raw/<arm>.mutants.jsonl."""
    arms = []
    for arm in ("A", "B", "C"):
        rows = rows_of(f"{arm}.{tag}")
        if rows:
            arms.append({"arm": arm, "name": NAMES[arm], "cases": len(rows),
                         "metrics": {key: rate(split(rows, key), key) for key, _ in metrics},
                         "usd_per_case": round(sum(r.get("usd") or 0 for r in rows) / len(rows), 4)})
    return arms


def combined():
    """The seeded corpus and the mutant corpus together, for tighter intervals."""
    arms = []
    for arm in ("A", "B", "C"):
        seeded, mutants = rows_of(arm), rows_of(f"{arm}.mutants")
        if seeded and mutants:
            rows = seeded + mutants
            arms.append({"arm": arm, "name": NAMES[arm], "cases": len(rows),
                         "metrics": {key: rate(split(rows, key), key) for key, _ in RATE_METRICS}})
    return arms


def history():
    """The arms on real bugs from the PicoRV32 author's own fix history (corpus/history)."""
    arms = []
    for arm in ("A", "B", "C"):
        rows = rows_of(f"{arm}.history")
        if rows:
            arms.append({"arm": arm, "name": NAMES[arm], "cases": len(rows),
                         "metrics": {key: rate(rows, key) for key, _ in HISTORY_METRICS},
                         "usd_per_case": round(sum(r.get("usd") or 0 for r in rows) / len(rows), 4)})
    return arms


def pct(m):
    if not m["n"]:
        return "n/a"
    lo, hi = m["lo"], m["hi"]
    return f"{m['k']}/{m['n']} ({100 * m['k'] / m['n']:.0f}%, 95% CI {100 * lo:.0f}–{100 * hi:.0f}%)"


def markdown(summary):
    arms = summary["arms"]
    lines = ["# Results", "", f"Cases: {summary['n_cases']}. Suite kill rate on seeded mutants: "
             f"{summary['kill_rate'].get('k')}/{summary['kill_rate'].get('n')}.", "",
             "| Metric | " + " | ".join(f"{a['arm']}: {a['name']}" for a in arms) + " |",
             "|---|" + "---|" * len(arms)]
    for key, label in RATE_METRICS:
        lines.append(f"| {label} | " + " | ".join(pct(a["metrics"][key]) for a in arms) + " |")
    lines.append("| Median simulations | " + " | ".join(str(a["median_sims"]) for a in arms) + " |")
    lines.append("| Median seconds | " + " | ".join(str(a["median_seconds"]) for a in arms) + " |")
    lines.append("| $ per case | " + " | ".join(f"{a['usd_per_case']:.2f}" for a in arms) + " |")
    lines += ["", "With this many cases, differences smaller than roughly 25 points are within noise; "
                  "overlapping intervals are ties."]
    stab = summary.get("stability") or []
    if stab:
        lines += ["", "## Run-to-run stability", "",
                  "The arms that call the model were run again on the same cases. Each cell lists the result of every "
                  "run, then on how many cases the grade was identical across all runs.", "",
                  "| Metric | " + " | ".join(f"{s['arm']}: {s['name']} ({s['runs']} runs)" for s in stab) + " |",
                  "|---|" + "---|" * len(stab)]
        for key, label in RATE_METRICS:
            cells = []
            for s in stab:
                m = s["metrics"][key]
                results = " · ".join(f"{r['k']}/{r['n']}" if r["n"] else "n/a" for r in m["runs"])
                cells.append(f"{results} (same on {m['same']}/{m['cases']})")
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
    hist = summary.get("history") or []
    if hist:
        n = max(a["cases"] for a in hist)
        lines += ["", "## Real upstream bugs", "",
                  f"{n} bugs that the PicoRV32 author fixed upstream, re-introduced into today's picorv32.v and kept only "
                  "when the regression catches them (corpus/history/README.md).", "",
                  "| Metric | " + " | ".join(f"{a['arm']}: {a['name']}" for a in hist) + " |", "|---|" + "---|" * len(hist)]
        for key, label in HISTORY_METRICS:
            lines.append(f"| {label} | " + " | ".join(pct(a["metrics"][key]) for a in hist) + " |")
        lines.append("| $ per case | " + " | ".join(f"{a['usd_per_case']:.2f}" for a in hist) + " |")
    for key, title, intro in (("mutants", "Mutant corpus", "Pull requests built automatically: one semantic single-token "
                               "bug the regression catches, mixed with harmless decoy edits (corpus/mutants/README.md)."),
                              ("combined", "All seeded and mutant cases", "The two corpora together; wider samples give "
                               "narrower intervals.")):
        arms = summary.get(key) or []
        if not arms:
            continue
        n = max(a["cases"] for a in arms)
        lines += ["", f"## {title}", "", f"{intro} Cases: {n}.", "",
                  "| Metric | " + " | ".join(f"{a['arm']}: {a['name']}" for a in arms) + " |", "|---|" + "---|" * len(arms)]
        for metric, label in RATE_METRICS:
            lines.append(f"| {label} | " + " | ".join(pct(a["metrics"][metric]) for a in arms) + " |")
    icarus12 = ROOT / "results" / "icarus12.md"
    if icarus12.exists():
        agreement = next((l for l in icarus12.read_text().splitlines() if l.lower().startswith("agreement")), None)
        lines += ["", "## Icarus Verilog 12 against 13", "", (agreement or "See results/icarus12.md.") +
                  " Details in results/icarus12.md."]
    return "\n".join(lines) + "\n"


def main():
    summary = summarize()
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "REPORT.md").write_text(markdown(summary))
    print(markdown(summary))


if __name__ == "__main__":
    main()
