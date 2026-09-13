# CULPRIT: system and reliability brief

Everything below was measured on this machine, read from `results/`, or read from the source it cites.

## 1. Problem and user

A design-verification engineer opens a pull request to a chip's RTL and the simulation regression
goes red. Finding which edit and which line did it means reading logs and waveforms by hand. Debug
is the largest share of verification engineers' time (47%, Siemens/Wilson Research study as cited by
Sigasi), and only 14% of IC/ASIC projects reach first-silicon success (Siemens/Wilson Research 2024).

CULPRIT takes that failed pull request, returns the culprit line with simulator evidence and a fix
that keeps the author's intended change, shows how the bug travelled through the chip, and puts the
result where the team works: the pull request, Linear, Slack.

## 2. System

```
GitHub (failed regression run)
  -> gather: PR, diff, CI log                                  apps/github.py
  -> reproduce: head fails, merge base passes                  sim/icarus.py (Icarus 13, firmware in Ubuntu toolchain container)
  -> non-RTL attribution: RTL-only vs other-only reruns        localize/confirm.py
  -> rule out: harmless edits proven harmless, no simulation   localize/inert.py (tokens, then name-blind netlist hash via Yosys 0.69)
  -> confirm: undo each remaining edit, re-simulate            localize/confirm.py
  -> repair: restore one line; revert a one-line culprit;      culprit.py repair
     else Claude + simulator, falling back to the revert       repair/claude.py
  -> act: PR review comment + status, Linear issue, Slack      apps/act.py through apps/http.py (allow-list)
  -> propagation: netlist path confirmed by waveforms          surface/propagation.py
  -> resolve: CI green, close Linear, reply in Slack, success  culprit.py cmd_resolve, apps/act.py
  -> verify: GitHub, Linear and Slack agree                    apps/verify.py
Window: app/window.py (pywebview) + web/ (React), reading runs/<pr>@<sha>/*.json|jsonl
Install: uv tool install git+https://github.com/owizdom/culprit -> the culprit command (macOS, Windows, Linux);
         writable files in the user data folder (paths.py)
```

Measured on the PicoRV32 regression: a clean run is 471,610 clock cycles and about 15 s; a failing
run stops early (about 1 s for the register-write bug); Yosys elaborates the core in 0.24 s; the full
investigation of a three-edit pull request used 4 simulations and 2.8 s with the base result cached.

## 3. Trust model

| Claim | Minted by | Tag |
|---|---|---|
| head fails, base passes | two simulations | CONFIRMED |
| an edit cannot change the chip | identical preprocessed tokens, or identical name-blind netlist hash under the testbench parameters | CONFIRMED (used only to skip a simulation, never to blame) |
| an edit is the culprit | undoing it alone makes the regression pass | CONFIRMED |
| a fix works | it compiles and passes; a model patch must also stay inside the culprit edit and not be a revert | CONFIRMED; a model patch that never passes is PROPOSED and is not offered as a one-click suggestion |
| a propagation hop | the signal's waveform differs between base and head no later than the next hop | CONFIRMED; otherwise PROPOSED, and hops that never differ are shown as contradicted |
| the model's explanation of the bug | Claude | PROPOSED |

Findings are also emitted as Tapeout `tof` lines (`finding CULPRIT-NNN confirmed|proposed ...`).

## 4. Reliability

- Abstains when head passes locally (not reproduced) or base already fails.
- Writes go through one allow-list: review comments and commit statuses on `owizdom/picorv32-ci`,
  CULPRIT's own Linear issue in one team, posts in one Slack channel. The investigation never
  pushes, merges or approves. A write outside the list raises before any network call
  (`tests/test_apps.py`).
- The one exception is the Test Run button. It may create git blobs, a tree, a commit and a branch
  whose name starts with `culprit-test/`, and open a pull request from that branch, on the watched
  repository only. It uses a known bug from the corpus (never its answer key) so the whole
  production flow can be shown live. A push to `main`, another branch name or another repository is
  refused (`tests/test_testrun.py`).
- GitHub can be connected with the OAuth device flow (the CULPRIT OAuth App asks for `public_repo`),
  the GitHub CLI login, or a pasted token. Every option is checked live before it is saved.
- Idempotency: the Linear issue and Slack parent are keyed per pull request, the review comment and
  thread reply per (pull request, head SHA). Publishing the same failure twice creates nothing the
  second time (`tests/test_apps.py`).
- Replay on live GitHub: the failure on PR #1 was published 7 times. The review comment was created
  on the first and reported unchanged on the other 6, and the pull request holds exactly 1 CULPRIT
  comment (`runs/ledger.jsonl`, GitHub API). Commit statuses are append-only in GitHub, and the PR's
  commit carries 5 `culprit` entries. The second came 14 s after the first with the same text, which
  is why `set_status` now skips a status whose state and description match the latest; the 3 later
  entries each changed the description.
- Replay across all three apps: with Linear and Slack connected, PR #1 was published again. That run
  created the Linear issue (WIS-5), a Linear note for the commit, a Slack parent message and one thread
  reply, and left the GitHub comment unchanged. Running it once more created nothing ("created 0,
  unchanged 4"). `culprit.py verify 1 <sha>` then printed OK: 1 review comment, a status, 1 open Linear
  issue linking the comment, 1 Slack parent linking the issue, and no duplicate creates in the ledger.
- Resolve, live: CULPRIT's suggestion for line 1344 was committed to the pull request branch
  (`f7c6651`). CI passed (run 34740402896: `TRAP after 471610 clock cycles`, `ALL TESTS PASSED.`).
  `act.resolve` then moved WIS-5 to Done, posted "Resolved: CI is green on f7c66512403c" in the Slack
  thread, and set a `culprit` success status on the green commit: 3 writes. Running resolve again
  wrote nothing, and verify with the issue expected closed printed OK three times in a row.
- A Slack failure found and fixed: right after that resolve, the parent lookup came back empty twice
  although the parent existed. The cause: `conversations.history` sometimes answers `ok` with no
  messages at all (2 of 12 calls one second apart). A publish decides whether to create by that
  lookup, so a miss could post a second parent. `slack._messages` now treats an empty page as no
  answer, retries, and raises `Unanswered` instead of posting (`tests/test_apps.py`). PR #2's resolve
  hit it once live and succeeded on the watcher's next pass.
- Connecting apps from the window: a token is written only after a live read-only check with its
  service passes (GitHub `/user`, Linear `viewer`, Slack `auth.test`), one key at a time, through a
  temp file and rename, mode 600. Setup choices go to a gitignored `config.local.yaml` merged over the
  public `config.yaml`. The UI only ever receives the last four characters of a token
  (`tests/test_connect.py`).
- The CI watcher acts on the newest completed run of each pull request, skips a commit that was
  already investigated, and resolves only pull requests it investigated. A failed action is retried
  on later passes, up to 3 times, and its last error is kept for the window. GitHub empties a run's
  pull request list once the pull request is merged, so a run that finished after the merge is matched
  through its commit instead (`apps/github.py` `pr_for_commit`, found live on PR #2). While signed out
  the watcher waits instead of erroring (`tests/test_watch.py`, `tests/test_github_runs.py`).
- The testbench keeps the `+firmware` path in a 128-character register. A long install or cache path
  would be cut off, and every simulation would then fail for a reason unrelated to the chip (found while
  checking Icarus 12). The firmware is now copied next to each simulation and passed by its short name
  (`tests/test_icarus.py`).
- End-state verifier checks one comment, one status, one issue, one Slack parent, and that each
  links the others (a missing link is counted as cross-system desync).

## 5. Evaluation

- Corpus: 30 cases. 23 are RTL bugs: 10 bugs inside an intended multi-line edit, 8 pull requests
  with harmless edits plus one bug, the 2 known PicoRV32 register-write test bugs as plain edits,
  and 3 two-edit bugs. 7 are nulls: 5 failures caused by test, firmware or testbench edits, and 2
  pull requests whose regression passes. Every case was simulated when built and behaves as labeled
  (`corpus/make.py --check` exits 0). Failures range from a single instruction test (`blt`, `lh`,
  `mulhsu`, `rem`, `lui`, `sh`, ...) to illegal instructions, out-of-bounds memory access and one
  timeout, between cycle 8,222 and 999,900. Ground truth comes from the edit specification. Only the
  builder and the grader read `truth.json` (`tests/test_truth_isolation.py`).
- Suite kill rate on seeded single-token mutants: 37/40 (`corpus/make.py --sweep 40`).
- Arms: A LLM only (one Claude call with the pull request, the diff and the CI log tail; no
  simulator), B delta debugging with the netlist proof (no model), C CULPRIT (B, then the shipped
  repair path). Claude Opus 5 at medium effort, one run per case.
- Results with 95% Wilson intervals (`results/REPORT.md`, rows in `results/raw/`):

| Metric | A LLM only | B delta debugging | C CULPRIT |
|---|---|---|---|
| Culprit edit found (23 RTL cases) | 19/23 (63–93%) | 23/23 (86–100%) | 23/23 (86–100%) |
| Exact line found | 21/23 (73–98%) | 16/23 (49–84%) | 19/23 (63–93%) |
| Fix passes the regression | 19/23 (63–93%) | 19/23 (63–93%) | 23/23 (86–100%) |
| Fix keeps the author's work (19 cases with intended work) | 18/19 (75–99%) | 9/19 (27–68%) | 16/19 (62–94%) |
| Wrong blame (all 30 cases) | 2/30 (2–21%) | 0/30 (0–10%) | 0/30 (0–10%) |
| Nulls handled correctly | 7/7 (65–100%) | 7/7 (65–100%) | 7/7 (65–100%) |
| Median simulations, seconds | 0, 5.5 s | 4, 0.7 s | 4, 0.7 s |
| Model spend for the whole corpus | $0.62 (30 cases) | $0 | $0.62 (9 cases needed the model) |

- At this size only one comparison separates: A keeps the author's work more often than B (75–99%
  against 27–68%). Every other difference is a tie.
- What C adds over B is the repair. Fixes that keep the author's work go from 9/19 to 16/19, and
  passing fixes from 19 to 23, for about $0.62 across the 9 cases that needed the model.
- What C adds over A is that its claims are checked. A named the wrong edit on t02 and h03, and 4 of
  its 23 fixes fail the regression (h01, h02, h03, t02); nothing in its answer tells which. Every
  culprit C reports was confirmed by a simulation. A model patch is returned as CONFIRMED only after
  `repair/claude.py` simulates it passing, and the grader applies the same patch the same way
  (`evals/run.py` `apply_fix`).
- Where C loses: A found the exact line more often (21 against 19, a tie). C's four line misses are
  passing fixes placed on another line of the same edit: a one-line restore or removal on a
  neighbouring changed line (a08, a10), or a model patch whose range starts a line above the bug
  (m02, m08). The one-line restore also passes on a06, a08 and a10 while dropping intended text
  near the bug, so the cheap deterministic path costs intent on 3 of 19 cases.
- Two-edit bugs (h01, h02) are found by B and C. C now also fixes both: when no fix that keeps the
  author's work passes, it undoes exactly the edits that carry the bug, keeps every other change, and
  counts the fix only when the simulator passes it (`culprit.py` `multi_edit_revert`,
  `tests/test_repair_order.py`). C's rows for h01 and h02 were re-run on this repair in all three runs.
- Measured during the build window (`results/REPORT.md`):
  - Three runs per model arm. C scored 23/23 culprit edits, 23/23 passing fixes and 0/30 wrong
    blames on every run, identical on 30 of 30 cases; A's wrong blames went 2, 1, 2 out of 30.
  - A second corpus of 35 generated pull requests (`corpus/mutants/`): one semantic bug the
    regression catches, among 1–2 harmless decoy edits whose decoy-only tree passes. C: 35/35 on
    every measure, 0/35 wrong blames; A: 32/35, 3/35 wrong blames. Over all 65 cases C's fix
    passed 58/58 (94–100%) and it blamed wrongly 0/65 (0–4%).
  - Real upstream bugs (`corpus/history/`): 2 of the 11 PicoRV32 bug fixes that still apply to
    today's code are caught by the regression; all three arms found both, and each fix equals the
    upstream fix.
  - Icarus 12 and 13 agree on all 31 runs with byte-identical logs (`results/icarus12.md`).
- Changed after the first full run, and re-run:
  1. The harness's arm C skipped repair when the culprit was the only RTL edit, which the product
     does not do. Arm C now calls the product's `repair()`.
  2. That exposed a product rule that reverted any culprit that was the only RTL edit, which threw
     away intended work on a01 and a07. A one-line culprit is still reverted; a longer one now goes to
     the model first, with the revert as the fallback (`tests/test_repair_order.py`). The five
     affected cases were re-run: the model fixed a01, a02 and a07 on the first attempt keeping the
     intended work ($0.03), and t01 and t02 were reverted.
  3. Arm A's schema requires a `line`, and the grader counted that line as a blame on a
     not-reproduced case (n02) where A had answered correctly. It no longer does; A's wrong blame went
     from 3/30 to 2/30.

## 6. Limits

- The firmware stops at the first failing test, so one failure is visible per run.
- A netlist proof holds for the testbench's parameters only.
- Register-file words are compared individually; a hop through memory can remain PROPOSED.
- The regression is the oracle; bugs it does not catch are invisible.
- Local Icarus 13 and CI's Icarus 12 agree on every corpus case: status, trap cycle, failing test and the
  full simulator log are identical on all 31 runs (30 heads and the base; `results/icarus12.md`), and the
  clean run traps at 471,610 cycles on GitHub CI as well (CI run 34737320325).
- The one-line restore can pass while dropping intended work next to the bug (3 of 19 cases).
- Three runs per model arm; more would be needed to separate small differences between arms.
- Evidence covers one design (PicoRV32). Another design is a `design:` config section (`design.py`),
  not yet run on a second design.
- Live investigations ran on macOS and in an Ubuntu 24.04 container; Windows is covered by the CI
  investigation job (`.github/workflows/ci.yml`), which runs on push.
- The real upstream-bug set is two cases, because the regression catches only 2 of the 11 that apply.

## 7. Reproduce

```sh
uv sync
docker build -t culprit-tc sim/
export CULPRIT_CHIP_REPO=/path/to/picorv32-ci    # default: ../picorv32-ci next to this repository
uv run python corpus/make.py --check
uv run python evals/run.py --arms A,B,C && uv run python evals/report.py
uv run python culprit.py local <base> <head> <pr>
```

## 8. Prior art

Delta debugging (Zeller) finds a culprit change without a model. RTL localization and repair:
VeriPilot (arXiv 2606.23759), LiK (2409.15186), HWE-Bench (2604.14709), Phoenix-bench
(2605.15226), CirFix (ASPLOS 2022). Products: Normal Computing, ChipAgents, Cadence ChipStack,
Synopsys AgentEngineer, Siemens Questa One Debug Agent.
