"""A model proposal that is really the old code, padded with a comment or reformatted, is not a repair."""
from localize.hunks import Hunk
from repair.claude import check, normalized

HEAD = "".join(f"line {i}\n" for i in range(1, 1343)) + (
    "`else\n"
    "\t\t\tcpuregs[latched_rd ^ 1] <= cpuregs_wrdata;\n"
    "`endif\n")
HUNK = Hunk(1, "picorv32.v", 1344, ("\t\t\tcpuregs[latched_rd] <= cpuregs_wrdata;\n",), 1344,
            ("\t\t\tcpuregs[latched_rd ^ 1] <= cpuregs_wrdata;\n",))


def test_normalized_ignores_comments_and_whitespace():
    assert normalized("a <= b;  // note\n") == normalized("a<=b;\n/* x */")


def test_revert_padded_with_a_comment_is_rejected_before_simulating():
    patch = {"start_line": 1344, "end_line": 1344,
             "replacement": "\t\t\t// tidy: write straight into the register\n\t\t\tcpuregs[latched_rd] <= cpuregs_wrdata;\n",
             "explanation": ""}
    tree, reason, result = check(patch, HEAD, {}, {"picorv32.v": HEAD}, [HUNK])
    assert tree is None and result is None
    assert "restores the old version" in reason


def test_lines_outside_the_culprit_edit_are_rejected():
    patch = {"start_line": 10, "end_line": 10, "replacement": "x\n", "explanation": ""}
    tree, reason, _ = check(patch, HEAD, {}, {"picorv32.v": HEAD}, [HUNK])
    assert tree is None and "outside the culprit edit" in reason
