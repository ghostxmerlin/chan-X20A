"""资料库 (server/mind/docs_kb.py, 2026-11-19): chunking the three kinds of material (pre-chunked jsonl, "### [ID]"
entries, heading sections), the import (new / changed / same / removed), the doc kind in the semantic sync (never in
semantic_search), and doc_search (vectors + exact words, project filter, the 3000-character budget).
Synthetic material only (the real packs are company documents, kept outside the repo)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from mind_fixtures import FakeDB  # noqa: E402
from mind import docs_kb, semantic  # noqa: E402
from mind.tools import Paid, ToolError  # noqa: E402
from test_mind_semantic import FakeEmbed  # noqa: E402

TAB = """# 工程经验

## 1. 格式

```
### [HW-001] 一句话结论式标题
- 标签: 关键词
```

## 2. 总线

### [HW-001] 系统 I2C 上接东西会卡住触摸
- 标签: I2C, GT911, G31
- 现象 / 背景: 接了传感器以后触摸没反应
- 经验法则: 系统总线上别挂外设
- 状态: 已验证
- 来源: luna-work/abc1234

### [HW-002] PSRAM 里放 LVGL 缓冲
- 标签: PSRAM, LVGL
- 状态: 推测
- 来源: memory/x.md
"""

PHONE = "# 手机说明\n\n简介。\n\n## 产品\n\n" + ("这是一段很长的产品介绍。" * 30 + "\n\n") * 8 + "## 接口\n\n用 USB-C 充电，GPIO17 是中断。\n"


def pack(root):
    fp = Path(root, "fingerprint moudle", "data")
    fp.mkdir(parents=True)
    rows = [{"schema_version": 1, "id": "kb-1", "project": "P", "snapshot_date": "2026-11-19", "title": "传感器 / ICN7332",
             "branch": "trial/7.7", "commit": "abc", "scope": "current_snapshot", "document": "02_关键IO与传感器.md",
             "source_refs": ["S01"], "verification_status": "source_review", "hardware_verified": False,
             "tags": ["ICN7332"], "text": "ICN7332 录入 12 次，ICN7352 录入 8 次。"},
            {"schema_version": 1, "id": "kb-2", "project": "P", "snapshot_date": "2026-11-19", "title": "Flash / 模板分区",
             "branch": "all", "commit": "abc", "scope": "current_snapshot", "document": "04_业务流程与Flash.md",
             "source_refs": ["S07"], "verification_status": "source_review", "hardware_verified": False, "tags": ["Flash"],
             "text": "31 个 20 KiB 物理槽加 4 KiB 索引共 624 KiB，与日志重叠 56 KiB。"}]
    (fp / "knowledge.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    Path(root, "fingerprint moudle", "项目完整说明.md").write_text("# 不该导入\n\n" + "重复内容。" * 50, encoding="utf-8")
    Path(root, "tab5").mkdir()
    Path(root, "tab5", "01-hardware-debug.md").write_text(TAB, encoding="utf-8")
    Path(root, "aidea phone").mkdir()
    Path(root, "aidea phone", "AIdea-项目说明.md").write_text(PHONE, encoding="utf-8")


class ChunkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pack(self.tmp.name)
        self.p = dict(docs_kb.load_root(self.tmp.name))

    def test_prechunked_jsonl_alone(self):
        fp = self.p["fingerprint moudle"]
        self.assertEqual([c["cid"] for c in fp], ["kb-1", "kb-2"])          # the full Markdown is not imported too
        self.assertEqual((fp[0]["branch"], fp[0]["status"], fp[0]["source"]), ("trial/7.7", "source_review", "S01"))

    def test_entries_skip_the_format_example(self):
        tab = self.p["tab5"]
        self.assertEqual([c["cid"] for c in tab], ["HW-001", "HW-002"])
        self.assertEqual(tab[0]["title"], "系统 I2C 上接东西会卡住触摸")
        self.assertEqual((tab[0]["status"], tab[1]["status"]), ("verified", "guess"))
        self.assertEqual(tab[0]["tags"], ["I2C", "GT911", "G31"])
        self.assertEqual(tab[0]["source"], "luna-work/abc1234")
        self.assertNotIn("PSRAM", tab[0]["text"])

    def test_headings_and_long_sections_split(self):
        ph = self.p["aidea phone"]
        self.assertTrue(all(len(c["text"]) <= docs_kb.CHUNK_MAX for c in ph))
        prod = [c for c in ph if c["title"].startswith("产品")]
        self.assertGreater(len(prod), 1)
        self.assertEqual(prod[1]["title"], "产品（2）")
        self.assertTrue(any(c["title"] == "接口" and "GPIO17" in c["text"] for c in ph))

    def test_words(self):
        self.assertEqual(docs_kb.words("ICN7332 和 7352 录入次数"), ["ICN7332", "7352"])
        self.assertEqual(docs_kb.words("HW-035 讲什么"), ["HW-035"])
        self.assertEqual(docs_kb.words("USB 和 UART 的 F1/F2"), ["USB", "UART", "F1/F2"])
        self.assertEqual(docs_kb.words("今天天气"), [])


class ImportSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pack(self.tmp.name)
        self.db = FakeDB()
        self.embed = FakeEmbed()

    def imp(self):
        return docs_kb.import_root(self.db, self.tmp.name, now_ms=1000)

    def test_import_counts_and_reimport(self):
        r = self.imp()
        self.assertEqual(r["fingerprint-moudle"]["new"], 2)
        self.assertEqual(r["tab5"]["new"], 2)
        doc = self.db["doc_chunks"].find_one({"_id": "tab5:HW-001"})
        self.assertEqual(doc["project_title"], "Tab5（X10A）")
        self.assertEqual(self.imp()["tab5"], {"chunks": 2, "new": 0, "changed": 0, "same": 2, "removed": 0})
        p = Path(self.tmp.name, "tab5", "01-hardware-debug.md")
        p.write_text(TAB.replace("PSRAM 里放 LVGL 缓冲", "PSRAM 里放 LVGL 双缓冲").split("### [HW-002]")[0]
                     + "### [HW-003] 新条目\n- 状态: 未解决\n", encoding="utf-8")
        r = self.imp()["tab5"]
        self.assertEqual((r["new"], r["removed"], r["same"]), (1, 1, 1))
        self.assertEqual(self.db["doc_chunks"].find_one({"_id": "tab5:HW-003"})["status"], "open")

    def test_doc_is_synced_but_not_in_semantic_search(self):
        self.imp()
        res = semantic.sync(self.db, embed_fn=self.embed)
        self.assertEqual(res["kinds"]["doc"]["embedded"], self.db.count("doc_chunks"))
        self.assertIn(semantic.TEXT_MAX, (2000,))
        ss = semantic.search_tool(self.db, self.embed, lambda: {"tz": "Asia/Shanghai"}, lambda ids: [])
        out = ss({"query": "ICN7332 录入次数"})
        self.assertFalse(any(h["kind"] == "doc" for h in out["hits"]))

    def test_doc_search_finds_by_meaning_and_by_exact_word(self):
        self.imp()
        semantic.sync(self.db, embed_fn=self.embed)
        tool = docs_kb.search_tool(self.db, self.embed, lambda: 1000)
        out = tool({"query": "ICN7332 录入几次"})
        self.assertIsInstance(out, dict)                  # not Paid: the call.embed row carries the cost
        self.assertEqual(out["hits"][0]["id"], "kb-1")
        self.assertIn("按意思", out["how"])
        self.assertIn("按字面", out["how"])
        self.assertEqual(out["hits"][0]["branch"], "trial/7.7")
        out = tool({"query": "GPIO17", "project": "AIdea"})
        self.assertEqual(out["project"], "AIdea 手机")
        self.assertTrue(all(h["project"] == "AIdea 手机" for h in out["hits"]))
        self.assertIn("GPIO17", out["hits"][0]["text"])
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False)), 3000 + 1500)

    def test_an_entry_id_comes_first(self):
        self.imp()
        semantic.sync(self.db, embed_fn=self.embed)
        out = docs_kb.search_tool(self.db, self.embed, lambda: 1000)({"query": "HW-002 讲了什么"})
        self.assertEqual(out["hits"][0]["id"], "HW-002")

    def test_a_heading_in_a_code_block_is_not_a_section(self):
        ch = docs_kb.chunks_from_headings("# T\n\n## 格式\n\n```\n### [HW-001] 例子\n```\n\n## 下一节\n\n正文正文正文正文\n", "r.md")
        self.assertEqual([c["title"] for c in ch], ["格式", "下一节"])

    def test_doc_search_without_vectors_still_finds_words(self):
        self.imp()
        tool = docs_kb.search_tool(self.db, None, lambda: 1000)
        out = tool({"query": "HW-002"})
        self.assertIsInstance(out, dict)
        self.assertEqual(out["hits"][0]["id"], "HW-002")
        self.assertEqual(out["hits"][0]["status"], "guess")

    def test_errors(self):
        tool = docs_kb.search_tool(self.db, self.embed, lambda: 1000)
        with self.assertRaises(ToolError):
            tool({"query": "x"})                          # empty library
        self.imp()
        with self.assertRaises(ToolError):
            tool({"query": ""})
        with self.assertRaises(ToolError) as e:
            tool({"query": "x", "project": "不存在"})
        self.assertIn("指纹模组", str(e.exception.args))


if __name__ == "__main__":
    unittest.main()


class AnswerTest(unittest.TestCase):
    """docs_qa.py (2026-11-19): search, then sol reasons over the excerpts; one follow-up search when it is unsure."""
    def setUp(self):
        from mind import docs_qa
        self.qa = docs_qa
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pack(self.tmp.name)
        self.db = FakeDB()
        self.embed = FakeEmbed()
        docs_kb.import_root(self.db, self.tmp.name, now_ms=1000)
        semantic.sync(self.db, embed_fn=self.embed)
        self.bodies, self.replies = [], []

    def send(self, env, body, timeout):
        self.bodies.append(body)
        text = self.replies.pop(0) if self.replies else '{"answer": "x", "used": [1], "confidence": "高"}'
        return ({"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                 "usage": {"input_tokens": 1000, "output_tokens": 200}}, 1200, None)

    def tool(self, env={"api_key": "k", "base_url": "u"}):
        return self.qa.answer_tool(self.db, env, self.embed, lambda: 1000, send=self.send)

    def test_answer_from_the_excerpts(self):
        self.replies = ['好的```json\n{"answer": "ICN7332 录 12 次，ICN7352 录 8 次（kb-1）。", "used": [1], "confidence": "高",'
                        ' "caveats": "没实板验证", "missing": "", "followup": ""}\n```']
        d = self.tool()({"question": "ICN7332 和 ICN7352 录入次数有什么不同？"})
        self.assertIn("12 次", d["answer"])
        self.assertEqual(d["sources"][0]["id"], "kb-1")
        self.assertEqual((d["confidence"], d["caveats"], d["rounds"], d["model"]), ("高", "没实板验证", 1, "gpt-6.2-sol"))
        b = self.bodies[0]
        self.assertEqual((b["model"], b["reasoning"]), ("gpt-6.2-sol", {"effort": "medium"}))
        self.assertIn("[1] 指纹模组 · kb-1", b["input"])
        self.assertIn("问题：ICN7332", b["input"])

    def test_one_follow_up_search_when_unsure(self):
        self.replies = ['{"answer": "不确定", "used": [], "confidence": "低", "followup": "GPIO17 中断"}',
                        '{"answer": "GPIO17 是中断。", "used": [1], "confidence": "中", "followup": "再查"}']
        from unittest import mock
        with mock.patch.object(self.qa, "K", 2):                      # a small first find: the follow-up adds new chunks
            d = self.tool()({"question": "那个脚是干嘛的"})
        self.assertEqual((d["rounds"], d["searched"], d["answer"]), (2, ["那个脚是干嘛的", "GPIO17 中断"], "GPIO17 是中断。"))
        self.assertIn("GPIO17", self.bodies[1]["input"])
        self.assertEqual(len(self.bodies), 2)                           # never a third

    def test_refusals(self):
        with self.assertRaises(ToolError):
            self.tool(env=None)({"question": "x"})
        self.replies = ["不是 JSON"]
        with self.assertRaises(ToolError) as e:
            self.tool()({"question": "ICN7332"})
        self.assertEqual(e.exception.code, "failed")
        self.assertEqual(self.qa.parse('{"answer": 3}'), None)
