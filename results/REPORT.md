# Results

Cases: 30. Suite kill rate on seeded mutants: 37/40.

| Metric | A: LLM only | B: Delta debugging + circuit proof | C: CULPRIT |
|---|---|---|---|
| Culprit edit found | 19/23 (83%, 95% CI 63–93%) | 23/23 (100%, 95% CI 86–100%) | 23/23 (100%, 95% CI 86–100%) |
| Exact line found | 21/23 (91%, 95% CI 73–98%) | 16/23 (70%, 95% CI 49–84%) | 19/23 (83%, 95% CI 63–93%) |
| Fix passes | 19/23 (83%, 95% CI 63–93%) | 19/23 (83%, 95% CI 63–93%) | 23/23 (100%, 95% CI 86–100%) |
| Fix keeps the author's work | 18/19 (95%, 95% CI 75–99%) | 9/19 (47%, 95% CI 27–68%) | 16/19 (84%, 95% CI 62–94%) |
| Wrong blame (lower is better) | 2/30 (7%, 95% CI 2–21%) | 0/30 (0%, 95% CI 0–10%) | 0/30 (0%, 95% CI 0–10%) |
| Correct on non-RTL / not-reproduced | 7/7 (100%, 95% CI 65–100%) | 7/7 (100%, 95% CI 65–100%) | 7/7 (100%, 95% CI 65–100%) |
| Median simulations | 0.0 | 4.0 | 4.0 |
| Median seconds | 5.5 | 0.7 | 0.7 |
| $ per case | 0.02 | 0.00 | 0.02 |

With this many cases, differences smaller than roughly 25 points are within noise; overlapping intervals are ties.

## Run-to-run stability

The arms that call the model were run again on the same cases. Each cell lists the result of every run, then on how many cases the grade was identical across all runs.

| Metric | A: LLM only (3 runs) | C: CULPRIT (3 runs) |
|---|---|---|
| Culprit edit found | 19/23 · 20/23 · 19/23 (same on 29/30) | 23/23 · 23/23 · 23/23 (same on 30/30) |
| Exact line found | 21/23 · 22/23 · 21/23 (same on 27/30) | 19/23 · 19/23 · 18/23 (same on 29/30) |
| Fix passes | 19/23 · 21/23 · 19/23 (same on 28/30) | 23/23 · 23/23 · 23/23 (same on 30/30) |
| Fix keeps the author's work | 18/19 · 19/19 · 18/19 (same on 29/30) | 16/19 · 16/19 · 15/19 (same on 29/30) |
| Wrong blame (lower is better) | 2/30 · 1/30 · 2/30 (same on 27/30) | 0/30 · 0/30 · 0/30 (same on 30/30) |
| Correct on non-RTL / not-reproduced | 7/7 · 7/7 · 7/7 (same on 30/30) | 7/7 · 7/7 · 7/7 (same on 30/30) |

## Real upstream bugs

2 bugs that the PicoRV32 author fixed upstream, re-introduced into today's picorv32.v and kept only when the regression catches them (corpus/history/README.md).

| Metric | A: LLM only | B: Delta debugging + circuit proof | C: CULPRIT |
|---|---|---|---|
| Culprit edit found | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) |
| Exact line found | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) |
| Fix passes | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) |
| Fix is the upstream fix | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) | 2/2 (100%, 95% CI 34–100%) |
| Wrong blame (lower is better) | 0/2 (0%, 95% CI 0–78%) | 0/2 (0%, 95% CI 0–78%) | 0/2 (0%, 95% CI 0–78%) |
| $ per case | 0.02 | 0.00 | 0.00 |

## Mutant corpus

Pull requests built automatically: one semantic single-token bug the regression catches, mixed with harmless decoy edits (corpus/mutants/README.md). Cases: 35.

| Metric | A: LLM only | B: Delta debugging + circuit proof | C: CULPRIT |
|---|---|---|---|
| Culprit edit found | 32/35 (91%, 95% CI 78–97%) | 35/35 (100%, 95% CI 90–100%) | 35/35 (100%, 95% CI 90–100%) |
| Exact line found | 32/35 (91%, 95% CI 78–97%) | 35/35 (100%, 95% CI 90–100%) | 35/35 (100%, 95% CI 90–100%) |
| Fix passes | 32/35 (91%, 95% CI 78–97%) | 35/35 (100%, 95% CI 90–100%) | 35/35 (100%, 95% CI 90–100%) |
| Fix keeps the author's work | 32/35 (91%, 95% CI 78–97%) | 35/35 (100%, 95% CI 90–100%) | 35/35 (100%, 95% CI 90–100%) |
| Wrong blame (lower is better) | 3/35 (9%, 95% CI 3–22%) | 0/35 (0%, 95% CI 0–8%) | 0/35 (0%, 95% CI 0–8%) |
| Correct on non-RTL / not-reproduced | n/a | n/a | n/a |

## All seeded and mutant cases

The two corpora together; wider samples give narrower intervals. Cases: 65.

| Metric | A: LLM only | B: Delta debugging + circuit proof | C: CULPRIT |
|---|---|---|---|
| Culprit edit found | 51/58 (88%, 95% CI 77–94%) | 58/58 (100%, 95% CI 94–100%) | 58/58 (100%, 95% CI 94–100%) |
| Exact line found | 53/58 (91%, 95% CI 81–96%) | 51/58 (88%, 95% CI 77–94%) | 54/58 (93%, 95% CI 84–97%) |
| Fix passes | 51/58 (88%, 95% CI 77–94%) | 54/58 (93%, 95% CI 84–97%) | 58/58 (100%, 95% CI 94–100%) |
| Fix keeps the author's work | 50/54 (93%, 95% CI 82–97%) | 44/54 (81%, 95% CI 69–90%) | 51/54 (94%, 95% CI 85–98%) |
| Wrong blame (lower is better) | 5/65 (8%, 95% CI 3–17%) | 0/65 (0%, 95% CI 0–4%) | 0/65 (0%, 95% CI 0–4%) |
| Correct on non-RTL / not-reproduced | 7/7 (100%, 95% CI 65–100%) | 7/7 (100%, 95% CI 65–100%) | 7/7 (100%, 95% CI 65–100%) |

## Icarus Verilog 12 against 13

Agreement: 31/31 runs (30 case heads + base) on status and trap cycle. Details in results/icarus12.md.
