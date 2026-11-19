"""Run every test suite and record each test's outcome (the refactor's "behaviour unchanged" check,
docs/plans/2026-11-19-server-layers.md 4.0).

Two Python environments, because neither runs everything: the Mac (python3: the firmware host tests that compile
upstream/, no numpy) and the server (~/soul-spike/.venv, py3.11: the ear tests with numpy / sherpa / opuslib, no
firmware tree). Plus the monitor's node tests. Each run writes docs/arch/suites/<label>.json {suite: {test id:
ok|fail|error|skip}}; `compare A B` lists every test whose outcome changed (a test new in B is reported as new, one
gone as gone).

    python3 tools/arch/suites.py run base          # all three, writes docs/arch/suites/base.json
    python3 tools/arch/suites.py run step-4.2.1
    python3 tools/arch/suites.py compare base step-4.2.1
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "docs", "arch", "suites")
LINE = re.compile(r"^(test\S+) \(([\w.]+)\)(?:\n.*?)? \.\.\. (ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$", re.M)


def parse_unittest(text):
    out = {}
    for name, cls, st in LINE.findall(text):
        st = "ok" if st == "ok" else "fail" if st == "FAIL" else "error" if st == "ERROR" else "skip"
        out[f"{cls}.{name}"] = st
    # loader failures (a module that did not import)
    for mod in re.findall(r"^ERROR: (\S+) \(unittest\.loader\._FailedTest\.\S+\)$", text, re.M):
        out[f"loader.{mod}"] = "error"
    return out


def env_sh():
    """The server's ssh settings from server/ear/_env.sh (SOUL_SERVER_SSH, SSH)."""
    cmd = f". {ROOT}/server/ear/_env.sh >/dev/null 2>&1; printf '%s\\n%s' \"$SOUL_SERVER_SSH\" \"$SSH\""
    # _env.sh finds the repo from $0's folder; under `bash -c` that is the working directory, so run it from there
    host, ssh = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                               cwd=os.path.join(ROOT, "server", "ear")).stdout.split("\n")[:2]
    if not host:
        raise SystemExit("SOUL_SERVER_SSH missing (.env)")
    return host, ssh


def run_local():
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v", "-s", "tests", "-p", "test_*.py"],
                       cwd=ROOT, capture_output=True, text=True, timeout=3000)
    return parse_unittest(r.stderr + r.stdout)


def run_server():
    host, ssh = env_sh()
    sh = lambda c: subprocess.run(["bash", "-c", f"{ssh} {host} {json.dumps(c)}"], capture_output=True, text=True,  # noqa: E731
                                  timeout=3000)
    sh("rm -rf /tmp/rf-run && mkdir -p /tmp/rf-run/upstream/StackChan/firmware/main/collector")
    subprocess.run(["rsync", "-a", "--exclude", "__pycache__", "-e", ssh, "server", "tests", "config", "tools",
                    f"{host}:/tmp/rf-run/"], cwd=ROOT, check=True, timeout=600)
    subprocess.run(["rsync", "-a", "-e", ssh, "upstream/StackChan/firmware/main/collector/m12_words.h",
                    f"{host}:/tmp/rf-run/upstream/StackChan/firmware/main/collector/"], cwd=ROOT, check=True)
    r = sh("cd /tmp/rf-run && ~/soul-spike/.venv/bin/python -B -m unittest discover -v -s tests -p 'test_*.py' 2>&1")
    return parse_unittest(r.stdout + r.stderr)


def run_node():
    d = os.path.join(ROOT, "web", "monitor")
    files = sorted(f for f in os.listdir(d) if f.endswith(".test.mjs"))
    r = subprocess.run(["node", "--test", "--test-reporter=tap", *files], cwd=d, capture_output=True, text=True, timeout=900)
    out = {}
    for m in re.finditer(r"^\s*(not ok|ok) \d+ - (.+?)(?: # (SKIP|TODO).*)?$", r.stdout, re.M):
        out[m.group(2).strip()] = "skip" if m.group(3) else "ok" if m.group(1) == "ok" else "fail"
    return out


def summary(res):
    return {k: {s: sum(1 for v in d.values() if v == s) for s in ("ok", "fail", "error", "skip")} for k, d in res.items()}


def main(argv):
    if len(argv) >= 2 and argv[0] == "run":
        res = {"local": run_local(), "server": run_server(), "node": run_node()}
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f"{argv[1]}.json"), "w", encoding="utf-8") as f:
            json.dump({"summary": summary(res), "tests": res}, f, ensure_ascii=False, indent=0, sort_keys=True)
        print(json.dumps(summary(res), ensure_ascii=False))
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        a, b = (json.load(open(os.path.join(OUT, f"{x}.json"), encoding="utf-8"))["tests"] for x in argv[1:])
        changed = 0
        for suite in sorted(set(a) | set(b)):
            sa, sb = a.get(suite, {}), b.get(suite, {})
            for t in sorted(set(sa) | set(sb)):
                if t not in sb:
                    print(f"  gone   {suite}: {t} (was {sa[t]})"); changed += 1
                elif t not in sa:
                    print(f"  new    {suite}: {t} -> {sb[t]}")
                elif sa[t] != sb[t]:
                    print(f"  CHANGE {suite}: {t} {sa[t]} -> {sb[t]}"); changed += 1
        print(f"{changed} changed / gone")
        return 1 if changed else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
