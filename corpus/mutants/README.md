# Mutant corpus

Seeded broken pull requests built by `corpus/mutants.py`. Each case is one single-token mutation in `picorv32.v`
that makes the PicoRV32 regression fail, plus one or two harmless decoy edits at least 40 lines away from the
mutation and from each other. The agent has to find which edit breaks the regression.

Grade with `python evals/run.py --corpus corpus/mutants/cases --tag mutants`.

## Method

1. Candidate lines: lines of `picorv32.v` that the testbench build compiles (`iverilog -DCOMPRESSED_ISA`, preprocessor
   `ifdef`/`ifndef`/`elsif`/`else` evaluated), inside the modules the testbench instantiates
   (picorv32, picorv32_axi, picorv32_axi_adapter, picorv32_pcpi_div, picorv32_pcpi_mul). Comment text, lines with strings or system tasks, and preprocessor lines are skipped.
2. Operators: `==`/`!=`, `+`/`-`, `&`/`|`, `&&`/`||`, `<`/`>=`, `>>`/`<<`, `1'b0`/`1'b1`, bit index `[N]` to `[N-1]`
   (`[0]` to `[1]`). `<=` is never touched, so non-blocking assignments stay valid.
3. Seed 20260913. All 694 candidates are shuffled, one per line is kept, and the first 150 are simulated with
   the same compile and `vvp` commands as `sim/icarus.py`, capped at 240 s wall clock
   (status `hang` past the cap; the testbench's own 1,000,000-cycle `TIMEOUT` is status `timeout`). Results:
   `screen.json`.
4. Mutants whose mutant-only tree ends `fail` or `timeout` are eligible, at most 8 per operator class.
   `hang`, `compile_error` and `pass` are dropped.
5. Decoys are comment or whitespace edits on assignment lines of compiled code: a comment line above the statement
   (`comment_above`), a trailing comment (`trailing_comment`), or one extra leading tab (`reindent`). Comment words
   are drawn only from words already present in `picorv32.v`, so no decoy introduces a new identifier and
   `localize/hunks.py` `groups()` keeps every hunk on its own.
6. Every case is checked: base passes; the decoy-only tree passes by simulation (every case, not only one per decoy
   kind); the head tree (base + mutation + decoys) fails; the head differs from base in exactly 1 + N hunks and the
   mutated line is in exactly one of them.
7. `truth.json`: `bug_lines` is the mutated head line, `bug_hunks` the hunk containing it, `intended_contains` the
   decoy text (a fix that keeps the author's harmless edits keeps these strings), `mutation` = line, base line, from,
   to, module; `fix_line` is the original base line.

## Counts

| stage | count |
|---|---|
| candidate (line, operator) pairs | 694 |
| screened (one per line) | 150 |
| screen `fail` | 83 |
| screen `timeout` (testbench TIMEOUT) | 18 |
| screen `pass` (mutant not caught) | 49 |
| screen `compile_error` | 0 |
| screen `hang` (over 240 s) | 0 |
| cases assembled with decoys and simulated | 41 |
| dropped after head/decoy simulation | 0 |
| kept | 35 |

Decoy kinds verified by simulation in kept cases: comment_above, reindent, trailing_comment. Build wall time: 401 s.

## Kept cases

| id | head line | module | mutation | head status | failure | trap cycle | decoys |
|---|---|---|---|---|---|---|---|
| x01 | 899 (base 899) | picorv32 | `[6]` → `[5]` | fail | OUT-OF-BOUNDS MEMORY READ FROM xxxxxxxX | 32539 | 1 (comment_above) |
| x02 | 1845 (base 1845) | picorv32 | `1'b1` → `1'b0` | fail | lui | 12569 | 1 (trailing_comment) |
| x03 | 1088 (base 1088) | picorv32 | `&&` → `||` | fail | OUT-OF-BOUNDS MEMORY READ FROM xxxxxxxX | 159437 | 2 (comment_above, reindent) |
| x04 | 497 (base 497) | picorv32 | `[12]` → `[11]` | fail | EBREAK instruction at 0x00000ED4 | 193793 | 2 (trailing_comment, reindent) |
| x05 | 940 (base 940) | picorv32 | `==` → `!=` | fail | OUT-OF-BOUNDS MEMORY WRITE TO fc020038 | 8406 | 2 (trailing_comment, comment_above) |
| x06 | 1069 (base 1069) | picorv32 | `&&` → `||` | fail | OUT-OF-BOUNDS MEMORY READ FROM fffffe00 | 8629 | 2 (comment_above, trailing_comment) |
| x07 | 1068 (base 1068) | picorv32 | `&&` → `||` | fail | None | 8504 | 1 (trailing_comment) |
| x08 | 876 (base 875) | picorv32 | `==` → `!=` | fail | None | 8226 | 2 (comment_above, reindent) |
| x09 | 1060 (base 1060) | picorv32 | `==` → `!=` | fail | None | 8504 | 1 (reindent) |
| x10 | 1213 (base 1213) | picorv32 | `&` → `|` | fail | OUT-OF-BOUNDS MEMORY READ FROM fffffffc | 8480 | 1 (trailing_comment) |
| x11 | 1049 (base 1049) | picorv32 | `==` → `!=` | fail | None | 8514 | 2 (trailing_comment, comment_above) |
| x12 | 1095 (base 1095) | picorv32 | `&&` → `||` | fail | None | 8814 | 2 (comment_above, trailing_comment) |
| x13 | 1059 (base 1058) | picorv32 | `&&` → `||` | fail | None | 16788 | 2 (comment_above, reindent) |
| x14 | 377 (base 377) | picorv32 | `||` → `&&` | timeout | None | 999900 | 2 (reindent, trailing_comment) |
| x15 | 975 (base 975) | picorv32 | `+` → `-` | fail | EBREAK instruction at 0x00000D42 | 153991 | 1 (trailing_comment) |
| x16 | 468 (base 468) | picorv32 | `[5]` → `[4]` | fail | OUT-OF-BOUNDS MEMORY WRITE TO 00020030 | 158854 | 2 (trailing_comment, comment_above) |
| x17 | 1071 (base 1070) | picorv32 | `&&` → `||` | fail | srl | 76934 | 2 (comment_above, trailing_comment) |
| x18 | 1087 (base 1087) | picorv32 | `&&` → `||` | fail | Illegal Instruction at 0x0000519C | 87308 | 2 (reindent, comment_above) |
| x19 | 503 (base 503) | picorv32 | `[2]` → `[1]` | timeout | None | 999900 | 2 (reindent, trailing_comment) |
| x20 | 2506 (base 2506) | picorv32_pcpi_div | `>>` → `<<` | fail | div | 108483 | 1 (trailing_comment) |
| x21 | 1338 (base 1338) | picorv32 | `&&` → `||` | fail | OUT-OF-BOUNDS MEMORY READ FROM xxxxxxxX | 8484 | 2 (comment_above, trailing_comment) |
| x22 | 329 (base 329) | picorv32 | `||` → `&&` | timeout | None | 999900 | 2 (comment_above, trailing_comment) |
| x23 | 491 (base 491) | picorv32 | `==` → `!=` | fail | Illegal Instruction at 0x00000C36 | 114783 | 2 (reindent, comment_above) |
| x24 | 2300 (base 2300) | picorv32_pcpi_mul | `[6]` → `[5]` | fail | mulh | 87027 | 2 (reindent, trailing_comment) |
| x25 | 1041 (base 1040) | picorv32 | `==` → `!=` | fail | None | 8497 | 2 (comment_above, trailing_comment) |
| x26 | 1240 (base 1240) | picorv32 | `-` → `+` | fail | auipc | 36219 | 1 (comment_above) |
| x27 | 1019 (base 1019) | picorv32 | `!=` → `==` | fail | Illegal Instruction at 0xTRAP | 90012 | 2 (comment_above, trailing_comment) |
| x28 | 1847 (base 1846) | picorv32 | `<<` → `>>` | fail | slli | 38318 | 2 (comment_above, reindent) |
| x29 | 574 (base 574) | picorv32 | `||` → `&&` | fail | OUT-OF-BOUNDS MEMORY READ FROM xxxxxxxx | 2 | 2 (trailing_comment, reindent) |
| x30 | 912 (base 912) | picorv32 | `+` → `-` | fail | None | 241000 | 1 (trailing_comment) |
| x31 | 2228 (base 2227) | picorv32_pcpi_mul | `==` → `!=` | fail | Illegal Instruction at 0x0000519C | 87325 | 1 (comment_above) |
| x32 | 1673 (base 1673) | picorv32 | `&` → `|` | fail | OUT-OF-BOUNDS MEMORY READ FROM fffffffc | 8831 | 1 (trailing_comment) |
| x33 | 486 (base 486) | picorv32 | `==` → `!=` | fail | Illegal Instruction at 0xFFFFF3 | 97273 | 1 (comment_above) |
| x34 | 332 (base 332) | picorv32 | `1'b1` → `1'b0` | fail | mulh | 86937 | 1 (reindent) |
| x35 | 1930 (base 1930) | picorv32 | `[0]` → `[1]` | fail | None | 20121 | 1 (trailing_comment) |
