"""The ear's deploy list (server/ear/deploy.sh FILES) carries every module ear_server.py imports, directly or not
(2026-11-19 night: the refactor added a dozen flat modules; one left out = an ear that does not start after deploy).
mind's deploy copies every *.py, so only the ear needs this."""
import ast
import re
import unittest
from pathlib import Path

EAR = Path(__file__).resolve().parents[1] / "server" / "ear"


def deploy_files():
    sh = (EAR / "deploy.sh").read_text(encoding="utf-8")
    return set(re.search(r'^FILES="([^"]*)"', sh, re.M).group(1).split())


def imports_of(path):
    local = {p.stem for p in EAR.glob("*.py")}
    names = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            names.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            names.add(n.module.split(".")[0])
    return {x for x in names if x in local}


class DeployListTest(unittest.TestCase):
    def test_every_module_the_ear_imports_is_deployed(self):
        need, todo = set(), ["ear_server"]
        while todo:
            m = todo.pop()
            if m in need:
                continue
            need.add(m)
            todo.extend(imports_of(EAR / f"{m}.py"))
        missing = sorted(f"{m}.py" for m in need if f"{m}.py" not in deploy_files())
        self.assertEqual(missing, [], "add these to FILES in server/ear/deploy.sh")


if __name__ == "__main__":
    unittest.main()
