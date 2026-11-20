"""The server's code map (docs/plans/2026-11-19-server-layers.md): every module of server/{ear,mind,common,kws} with
its lines, layer (tools/arch/layers.json), purpose (the Chinese line there, else the docstring's first line), the
modules it imports, the Mongo collections it names, and the imports that go against the layers (a lower-numbered
layer imported by a higher-numbered one = calling upwards). Writes docs/arch/code-map.json (the monitor's 代码结构
page reads it) and, with --check, exits 1 when a violation is not in the allowed list (layers.json "allowed").

    python3 tools/arch/code_map.py            # write docs/arch/code-map.json
    python3 tools/arch/code_map.py --check    # also fail on a new upward import (--no-write: check only)
"""
import ast
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PKGS = ("ear", "mind", "common", "kws")
SHARED = {"hello_log": "common/hello_log.py", "hello_log_query": "common/hello_log_query.py", "prices": "common/prices.py", "bodies": "common/bodies.py"}
COLL_RE = re.compile(r"""\b(?:db|self\.db|_db|database)\s*\[\s*["']([a-z_0-9]+)["']\s*\]""")


def scan(layers):
    mods = {}
    for pkg in PKGS:
        d = os.path.join(ROOT, "server", pkg)
        if not os.path.isdir(d):
            continue
        local = {f[:-3]: f"{pkg}/{f}" for f in os.listdir(d) if f.endswith(".py")}
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py"):
                continue
            rel = f"{pkg}/{fn}"
            src = open(os.path.join(d, fn), encoding="utf-8").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            names = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom):
                    if n.level >= 1 and not n.module:
                        names.update(a.name for a in n.names)
                    elif n.module:
                        names.add(n.module.split(".")[0] if n.level == 0 else n.module.split(".")[0])
                elif isinstance(n, ast.Import):
                    names.update(a.name.split(".")[0] for a in n.names)
            imports = sorted({local.get(x) or SHARED.get(x) for x in names
                              if (local.get(x) or SHARED.get(x)) and (local.get(x) or SHARED.get(x)) != rel})
            doc = (ast.get_docstring(tree) or "").strip().split("\n")[0][:140]
            mods[rel] = {"pkg": pkg, "lines": src.count("\n") + 1, "layer": layers["modules"].get(rel, "?"),
                         "zh": layers.get("zh", {}).get(rel), "doc": doc, "imports": imports,
                         "collections": sorted(set(COLL_RE.findall(src)))}
    return mods


def violations(mods):
    out = []
    for rel, m in mods.items():
        if not isinstance(m["layer"], int):
            continue
        for dst in m["imports"]:
            ld = mods.get(dst, {}).get("layer")
            if isinstance(ld, int) and ld < m["layer"]:
                out.append({"from": rel, "to": dst, "from_layer": m["layer"], "to_layer": ld})
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    layers = json.load(open(os.path.join(ROOT, "tools", "arch", "layers.json"), encoding="utf-8"))
    mods = scan(layers)
    bad = violations(mods)
    try:
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        sha = None
    out = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "commit": sha, "layer_names": layers["names"],
           "modules": mods, "violations": bad, "unassigned": sorted(r for r, m in mods.items() if m["layer"] == "?"),
           "totals": {p: {"modules": sum(1 for m in mods.values() if m["pkg"] == p),
                          "lines": sum(m["lines"] for m in mods.values() if m["pkg"] == p)} for p in PKGS}}
    if "--no-write" not in argv:                 # the test only checks
        os.makedirs(os.path.join(ROOT, "docs", "arch"), exist_ok=True)
        with open(os.path.join(ROOT, "docs", "arch", "code-map.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
    print(f"{len(mods)} modules, {len(bad)} upward imports, {len(out['unassigned'])} unassigned")
    if "--check" in argv:
        allowed = {(a["from"], a["to"]) for a in layers.get("allowed", [])}
        new = [v for v in bad if (v["from"], v["to"]) not in allowed]
        for v in new:
            print(f"  upward: {v['from']} ({v['from_layer']}) -> {v['to']} ({v['to_layer']})")
        return 1 if new or out["unassigned"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
