# Upstream bug corpus

Real PicoRV32 bugs, taken from the upstream author's own bug-fix commits and put back into today's
`picorv32.v`. Built by `corpus/history.py`; per-candidate log in `screen.json`.

## Method

1. Candidates: `git log -i -E --grep='fix|bug' -- picorv32.v` in the picorv32-ci checkout.
2. For each hunk of the commit's `picorv32.v` diff (`-U3`), the post-fix text (context + added lines) must
   occur exactly once in today's file; it is replaced by the pre-fix text (context + removed lines). If any
   hunk does not match exactly once the commit is skipped.
3. Applied commits that are not functional fixes (read by hand, reasons in `history.py` `NOT_A_BUG_FIX`),
   that change only whitespace/comments, or that change only code the regression never compiles
   (`RISCV_FORMAL`, `DEBUG*`, `PICORV32_TESTBUG_*`) are not simulated.
4. Kept only if today's base passes the regression and the head fails it (status `fail` or `timeout`, not
   `compile_error`). A 300 s wall-clock pre-screen runs first; kept heads are then run through
   `sim/icarus.py` `run`, whose result is what `sim.json` records.

Ignoring whitespace, 0 of the 26 commits that do not apply would apply; each of the others has a
hunk whose post-fix code no longer exists in today's file.

The upstream commit subject names the bug ("Fixed signed division by zero handling"), and the evaluation passes a
case's title to the agent, so `case.json` uses a neutral pull request title instead ("div: simplify the output sign
expression", "No functional change intended"), the same style as the seeded corpus. The subject and commit stay in
`case.json` for reference; the grader does not use them.

`truth.json` has the seeded corpus's fields (`category` is `upstream_bug`, `intended_contains` is empty) plus
`upstream_fix`: per hunk, the head line range and the upstream post-fix text. Applying all of them gives
today's `picorv32.v` back exactly.

## Counts

| stage | commits |
|---|---|
| candidates | 46 |
| does not apply exactly once to today's file | 26 |
| applied | 20 |
| judged not a functional fix | 6 |
| whitespace/comments only | 2 |
| only code the regression does not compile | 0 |
| head does not compile | 1 |
| head still passes the regression | 9 |
| kept (head fails the regression) | 2 |

## Kept cases

| dir | commit | subject | failure | trap cycle |
|---|---|---|---|---|
| `cases/u-2cab981` | [2cab981](https://github.com/YosysHQ/picorv32/commit/2cab981862b0ff4b362363de865151955ebedf01) | Fixed signed division by zero handling | fail: div | 110369 |
| `cases/u-aa17d58` | [aa17d58](https://github.com/YosysHQ/picorv32/commit/aa17d587843a78e3d49b5e24bc5e4ab76e8c6383) | Bugfix in C.SRAI implementation | fail: EBREAK instruction at 0x00000D42 | 267175 |

## Applied but not kept

| commit | subject | why |
|---|---|---|
| [de92ce5](https://github.com/YosysHQ/picorv32/commit/de92ce54e8a3f24f2adc6b5d04de35e4edb873e5) | Fix RV32E shifts | passes: head status `pass` |
| [29102c0](https://github.com/YosysHQ/picorv32/commit/29102c00a82ffd08f1e0b3c9cbac1c95c17f573b) | Bugfix: decode fence instruction | passes: head status `pass` |
| [100e421](https://github.com/YosysHQ/picorv32/commit/100e421be0aa5fb130b6e4f532d15fc7d1a03f02) | Fix copyright info | not_a_bug_fix: license header only (author name in a block comment) |
| [fac01ce](https://github.com/YosysHQ/picorv32/commit/fac01cee1c03f2209078b3e268987814309dc63d) | - fix missing brackets | compile_error: head status `compile_error` |
| [e6779ba](https://github.com/YosysHQ/picorv32/commit/e6779ba52b41c3e6551ae95a137c543abc91a4f6) | Disable verilator warnings, fixes #128 | cosmetic: only whitespace/comments change |
| [d046cbf](https://github.com/YosysHQ/picorv32/commit/d046cbfa4986acb50ef6b6e5ff58e9cab543980b) | Add PICORV32_TESTBUG_nnn ifdefs for testing purposes | not_a_bug_fix: adds PICORV32_TESTBUG_nnn ifdefs; the compiled `else branches equal today's lines |
| [bb9ebeb](https://github.com/YosysHQ/picorv32/commit/bb9ebeb9e37bc6103f527b434a13d9b4889a796b) | Fixed jalr, c_jalr, and c_jr insns (bug discovered by riscv-formal) | passes: head status `pass` |
| [436544c](https://github.com/YosysHQ/picorv32/commit/436544ccab9dcef61d074feda19e52c94fdb5c1b) | Fix decoding of C.ADDI instruction | passes: head status `pass` |
| [3495604](https://github.com/YosysHQ/picorv32/commit/3495604877d8e0cabd9c583a9dc7805803b3c83c) | Fix indenting in wishbone code | not_a_bug_fix: re-indents picorv32_wb; subject says indenting |
| [aaa9e25](https://github.com/YosysHQ/picorv32/commit/aaa9e25756c1765353e6ff42da82cbedeecf62cd) | Add DEBUGNETS debug flag | not_a_bug_fix: adds the DEBUGNETS debug flag (matched 'bug' inside 'debug') |
| [e4312b0](https://github.com/YosysHQ/picorv32/commit/e4312b0fab053cda38cb46623341db85e9f8a060) | Fix "mem_xfer is used before its declaration" warning | not_a_bug_fix: moves a wire declaration to silence a use-before-declaration warning |
| [f975ce1](https://github.com/YosysHQ/picorv32/commit/f975ce1e454077c58ecb750da3abb77dcdf74df7) | Fix picorv32_axi STACKADDR default value | passes: head status `pass` |
| [ef86b30](https://github.com/YosysHQ/picorv32/commit/ef86b30b2598df6993a473222ebe355b0348e226) | Fixed some linter warnings in picorv32.v | not_a_bug_fix: linter width padding ({16'b0, x}, 32'bx); same values after Verilog zero-extension |
| [54a8e4b](https://github.com/YosysHQ/picorv32/commit/54a8e4b311e207fa1d07226627dbf9dea6c13836) | Fixed catching jumps to misaligned insn | passes: head status `pass` |
| [f82af97](https://github.com/YosysHQ/picorv32/commit/f82af97595c3d8c594927a735e401b3c62d46be2) | Another bugfix regarding compressed ISA and unaligned insns | passes: head status `pass` |
| [c209c01](https://github.com/YosysHQ/picorv32/commit/c209c016b35a085ccc6044dead7e0b8461b239ca) | More fixes related to assertpmux checks | passes: head status `pass` |
| [38a760d](https://github.com/YosysHQ/picorv32/commit/38a760daf8e19c882afbeb9952c36cb8cb41bd5e) | Fix tabs | cosmetic: only whitespace/comments change |
| [789a411](https://github.com/YosysHQ/picorv32/commit/789a411eadaedbc44417ae595f3410e65f89cfc3) | Bugfix for CATCH_ILLINSN <-> WITH_PCPI interaction | passes: head status `pass` |
