"""The tool registry (config/tools/registry.json, A2 2026-11-18) follows docs/hello-tools.md: unique snake_case names,
known groups / levels / statuses, every field present, parameters in the function-calling subset Qwen and OpenAI both
accept (so the live tools can be exported as-is).

    python3 -B -m unittest tests.test_tool_registry -v
"""
import json
import re
import unittest
from pathlib import Path

REG = json.loads((Path(__file__).resolve().parents[1] / "config" / "tools" / "registry.json").read_text(encoding="utf-8"))
FIELDS = ("name", "title", "group", "level", "runs_on", "status", "description", "parameters", "returns", "source",
          "latency", "cost", "needs", "notes")
RUNS_ON = {"mind", "ear", "robot", "external"}
SCALARS = {"string", "integer", "number", "boolean"}


def check_schema(test, name, p):
    test.assertEqual(p.get("type"), "object", name)
    props = p.get("properties", {})
    test.assertIsInstance(props, dict, name)
    test.assertTrue(set(p.get("required", [])) <= set(props), f"{name}: required not in properties")
    for k, v in props.items():
        test.assertRegex(k, r"^[a-z][a-z0-9_]*$", name)
        t = v.get("type")
        if t == "array":
            test.assertIn(v.get("items", {}).get("type"), SCALARS, f"{name}.{k}: array of scalars only")
        else:
            test.assertIn(t, SCALARS, f"{name}.{k}")
        if "enum" in v:
            test.assertEqual(t, "string", f"{name}.{k}: enum on strings only")
        test.assertTrue(set(v) <= {"type", "description", "enum", "maxLength", "minimum", "maximum", "items"},
                        f"{name}.{k}: outside the function-calling subset")


class RegistryTest(unittest.TestCase):
    def test_every_tool_follows_the_conventions(self):
        names = [t["name"] for t in REG["tools"]]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")
        for t in REG["tools"]:
            self.assertEqual(set(FIELDS) - set(t), set(), t.get("name"))
            self.assertRegex(t["name"], r"^[a-z][a-z0-9_]{2,40}$")
            self.assertIn(t["group"], REG["groups"], t["name"])
            self.assertIn(t["level"], REG["levels"], t["name"])
            self.assertIn(t["status"], REG["statuses"], t["name"])
            self.assertIn(t["runs_on"], RUNS_ON, t["name"])
            self.assertTrue(t["description"] and len(t["description"]) <= 120, t["name"])
            self.assertIsInstance(t["needs"], list, t["name"])
            check_schema(self, t["name"], t["parameters"])

    def test_c_level_tools_say_how_they_are_confirmed(self):
        # docs/hello-tools.md §2: a C tool proposes; the owner asking in person counts as the confirmation
        for t in REG["tools"]:
            if t["level"] == "C" and not t["name"].endswith("_propose"):
                self.assertTrue(any(w in t["notes"] for w in ("提议", "确认", "C 级")), t["name"])

    def test_export_shape_for_function_calling(self):
        live = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["parameters"]}} for t in REG["tools"]]
        json.dumps(live, ensure_ascii=False)            # serialisable as the tools list a model receives
        self.assertTrue(all(re.match(r"^[a-z_0-9]+$", x["function"]["name"]) for x in live))


if __name__ == "__main__":
    unittest.main()
