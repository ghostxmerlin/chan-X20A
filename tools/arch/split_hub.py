"""One-off mechanical split of server/ear/realtime_link.py (2026-11-19 refactor 4.2). No code is rewritten: blocks are
cut by the AST's line ranges and pasted into new flat modules (the ear deploys flat files).

  base           everything above `class RealtimeHub` -> rt_base.py, except `log = print` and `_LOG_FAILED`, which stay
                 in realtime_link (tests patch realtime_link.log / _LOG_FAILED); rt_base gets late-bound shims for both.
                 realtime_link keeps every old name (globals().update from rt_base).
  mixin MOD CLS  the RealtimeHub methods named on stdin (one per line) -> MOD.py as `class CLS:`; RealtimeHub gains CLS
                 as a base. A method's leading comment lines go with it.

    python3 tools/arch/split_hub.py base
    printf 'quiet_on\\nlow_on\\n' | python3 tools/arch/split_hub.py mixin rt_manner MannerMixin "说话方式模式 ..."
"""
import ast
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EAR = os.path.join(ROOT, "server", "ear")
RL = os.path.join(EAR, "realtime_link.py")
IMPORT_BASE = ('import rt_base as _rt_base  # noqa: E402\n'
               '# every name of rt_base (also the underscore ones), so the moved code resolves exactly as before\n'
               'globals().update({_k: _v for _k, _v in vars(_rt_base).items() if not _k.startswith("__")})\n')


def read():
    with open(RL, encoding="utf-8") as f:
        return f.read()


def hub(tree):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RealtimeHub")


def do_base():
    src = read()
    lines = src.split("\n")
    tree = ast.parse(src)
    cls = hub(tree)
    start = cls.lineno - 1 - len(cls.decorator_list)
    # the comment block right above the class stays with it
    while start > 0 and lines[start - 1].startswith("#"):
        start -= 1
    head, rest = lines[:start], lines[start:]
    doc = ast.get_docstring(tree)
    first = tree.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant), "module docstring first"
    doc_end = first.end_lineno
    keep_doc, body = head[:doc_end], head[doc_end:]
    moved = []
    for ln in body:
        if re.match(r"^log = print\b", ln) or re.match(r"^_LOG_FAILED = set\(\)", ln):
            continue
        moved.append(ln)
    base = ['"""Docking hub, the module level: constants, the text and prompt helpers, small classes (moved out of',
            'realtime_link.py unchanged on 2026-11-19, refactor 4.2; realtime_link re-exports every name).',
            '',
            'log and _LOG_FAILED stay in realtime_link (tests patch them there): the shims below look them up at call time."""',
            'import sys as _sys',
            '',
            '',
            'def log(*a, **k):',
            '    m = _sys.modules.get("realtime_link")',
            '    return (m.log if m is not None else print)(*a, **k)',
            '',
            '',
            'class _LateFailed:',
            '    """realtime_link._LOG_FAILED, looked up at use (a test may swap the set)."""',
            '    def _s(self):',
            '        m = _sys.modules.get("realtime_link")',
            '        return m._LOG_FAILED if m is not None else _FALLBACK',
            '',
            '    def __contains__(self, x):',
            '        return x in self._s()',
            '',
            '    def add(self, x):',
            '        self._s().add(x)',
            '',
            '',
            '_FALLBACK = set()',
            '_LOG_FAILED = _LateFailed()',
            ''] + moved
    with open(os.path.join(EAR, "rt_base.py"), "w", encoding="utf-8") as f:
        f.write("\n".join(base).rstrip("\n") + "\n")
    # imports of the moved header must come first in rt_base: they are plain `import x` lines in `moved`; Python
    # allows them after the shims. realtime_link keeps its docstring, log, _LOG_FAILED, and imports rt_base.
    new = keep_doc + ["", "log = print                                         # tests silence it",
                      "_LOG_FAILED = set()                                 # run-log helpers that have failed once (said once on the console)",
                      "", IMPORT_BASE.rstrip("\n"), "", ""] + rest
    with open(RL, "w", encoding="utf-8") as f:
        f.write("\n".join(new))
    print(f"rt_base.py: {len(base)} lines; realtime_link.py: {len(new)} lines")


def do_mixin(mod, cls_name, doc, names):
    src = read()
    lines = src.split("\n")
    tree = ast.parse(src)
    cls = hub(tree)
    by = {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [n for n in names if n not in by]
    assert not missing, f"not RealtimeHub methods: {missing}"
    spans = []
    for n in names:
        node = by[n]
        s = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
        while s > 0 and lines[s - 1].strip().startswith("#"):      # its leading comments
            s -= 1
        spans.append((s, node.end_lineno))
    spans.sort()
    cut = set()
    blocks = []
    for s, e in spans:
        blocks.append("\n".join(lines[s:e]))
        cut.update(range(s, e))
    out = [f'"""{doc}"""', IMPORT_BASE.rstrip("\n"), "", "", f"class {cls_name}:", ""]
    out += [b + "\n" for b in blocks]
    with open(os.path.join(EAR, f"{mod}.py"), "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    rest = [ln for i, ln in enumerate(lines) if i not in cut]
    text = "\n".join(rest)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # the base class and the import
    m = re.search(r"^class RealtimeHub(\(([^)]*)\))?:", text, re.M)
    bases = [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]
    bases.append(cls_name)
    text = text[:m.start()] + f"class RealtimeHub({', '.join(bases)}):" + text[m.end():]
    imp = f"from {mod} import {cls_name}  # noqa: E402"
    text = text.replace(IMPORT_BASE.rstrip("\n"), IMPORT_BASE.rstrip("\n") + "\n" + imp, 1)
    with open(RL, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"{mod}.py: {len(names)} methods; realtime_link.py: {text.count(chr(10))} lines")


if __name__ == "__main__":
    if sys.argv[1] == "base":
        do_base()
    elif sys.argv[1] == "mixin":
        names = [ln.strip() for ln in sys.stdin.read().split() if ln.strip()]
        do_mixin(sys.argv[2], sys.argv[3], sys.argv[4], names)
