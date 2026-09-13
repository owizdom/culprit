from localize import hunks as H

BASE = "".join(f"line {i}\n" for i in range(1, 41))


def edit(text, lineno, new):
    lines = text.splitlines(keepends=True)
    lines[lineno - 1] = new
    return "".join(lines)


def test_apply_all_is_head_and_apply_none_is_base():
    head = edit(edit(BASE, 5, "changed 5\n"), 30, "changed 30\n")
    hs = H.parse(BASE, head, "f.v")
    assert len(hs) == 2
    assert H.apply(BASE, hs, {h.id for h in hs}) == head
    assert H.apply(BASE, hs, set()) == BASE


def test_any_subset_rebuilds_that_subset():
    head = edit(edit(BASE, 5, "changed 5\n"), 30, "changed 30\n")
    hs = H.parse(BASE, head, "f.v")
    only_second = H.apply(BASE, hs, {hs[1].id})
    assert "changed 30" in only_second and "changed 5" not in only_second


def test_changed_head_lines_excludes_context():
    head = edit(BASE, 12, "changed 12\n")
    (h,) = H.parse(BASE, head, "f.v")
    assert h.changed_head_lines() == {12}


def test_rename_in_two_places_is_one_group():
    base = "wire foo;\n" + "".join(f"x {i}\n" for i in range(20)) + "assign y = foo;\n"
    head = base.replace("foo", "bar")
    hs = H.parse(base, head, "f.v")
    assert len(hs) == 2
    groups = H.groups(hs, {"f.v": base}, {"f.v": head})
    assert groups == [frozenset({1, 2})]


def test_unrelated_edits_stay_separate():
    head = edit(edit(BASE, 5, "line 500\n"), 30, "line 3000\n")
    hs = H.parse(BASE, head, "f.v")
    assert len(H.groups(hs, {"f.v": BASE}, {"f.v": head})) == 2
