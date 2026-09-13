"""Split a change into hunks and rebuild any subset of it.

Hunks are the same shape as `git diff -U3`. A tree is {repo-relative path: file text}.
"""
import difflib
import re
from dataclasses import dataclass

IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
KEYWORDS = set("""always assign begin case casez default else end endcase endfunction endgenerate endmodule
function generate genvar if initial input inout integer localparam module negedge or output parameter posedge
reg signed wire for while ifdef ifndef elsif endif define include""".split())


@dataclass(frozen=True)
class Hunk:
    id: int
    path: str
    old_start: int      # 1-based line in base where this hunk begins
    old: tuple
    new_start: int      # 1-based line in head where this hunk begins
    new: tuple

    def changed_head_lines(self):
        """Head line numbers that were added or modified (not context)."""
        out = set()
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, self.old, self.new, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                out.update(range(self.new_start + j1, self.new_start + j2))
        return out


def parse(base, head, path, first_id=1):
    a, b = base.splitlines(keepends=True), head.splitlines(keepends=True)
    hunks = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in matcher.get_grouped_opcodes(3):
        i1, i2 = group[0][1], group[-1][2]
        j1, j2 = group[0][3], group[-1][4]
        hunks.append(Hunk(first_id + len(hunks), path, i1 + 1, tuple(a[i1:i2]), j1 + 1, tuple(b[j1:j2])))
    return hunks


def parse_tree(base_tree, head_tree):
    hunks = []
    for path in sorted(set(base_tree) | set(head_tree)):
        before, after = base_tree.get(path, ""), head_tree.get(path, "")
        if before != after:
            hunks += parse(before, after, path, first_id=len(hunks) + 1)
    return hunks


def apply(base, hunks, keep):
    """Base text with only the hunks whose id is in keep applied."""
    lines = base.splitlines(keepends=True)
    out, cursor = [], 0
    for h in sorted(hunks, key=lambda h: h.old_start):
        start = h.old_start - 1
        out += lines[cursor:start]
        out += list(h.new if h.id in keep else h.old)
        cursor = start + len(h.old)
    out += lines[cursor:]
    return "".join(out)


def apply_tree(base_tree, hunks, keep):
    tree = dict(base_tree)
    for path in {h.path for h in hunks}:
        text = apply(base_tree.get(path, ""), [h for h in hunks if h.path == path], keep)
        if text:
            tree[path] = text
        else:
            tree.pop(path, None)
    return tree


def revert_tree(base_tree, hunks, drop):
    return apply_tree(base_tree, hunks, {h.id for h in hunks} - set(drop))


def groups(hunks, base_tree, head_tree):
    """Hunks that must move together: they share an identifier that exists only before or only after
    the change (a rename touching several places). Reverting one of them alone would not compile."""
    parent = {h.id: h.id for h in hunks}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner = {}
    for path in {h.path for h in hunks}:
        before = set(IDENT.findall(base_tree.get(path, "")))
        after = set(IDENT.findall(head_tree.get(path, "")))
        coupled = (before ^ after) - KEYWORDS
        for h in (h for h in hunks if h.path == path):
            for name in set(IDENT.findall("".join(h.old) + "".join(h.new))) & coupled:
                if name in owner:
                    parent[find(h.id)] = find(owner[name])
                else:
                    owner[name] = h.id
    out = {}
    for h in hunks:
        out.setdefault(find(h.id), set()).add(h.id)
    return [frozenset(ids) for ids in sorted(out.values(), key=min)]
