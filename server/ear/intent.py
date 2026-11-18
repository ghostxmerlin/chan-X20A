"""Which tools a sentence needs before the model answers (owner 2026-11-18: 先找技能，再找工具；渐进; a very fast
local method). Two layers, both local:
  1. rules: words that are certain (提醒, 天气, 转过来 ...) -> a skill, ~0 ms;
  2. a local Chinese embedding model (bge-small-zh-v1.5 ONNX, ~2-3 ms a sentence on the server) against hand-written
     examples per skill (config/skills/examples.json) and a "none" group (chit-chat): the nearest group wins when it
     is close enough and clearly ahead of "none".
classify(text) -> (skill id or None, score, via) with via "rule" / "embed" / "none" / "short" / "off".
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.expanduser(os.environ.get("SOUL_INTENT_MODEL", "~/soul-spike/models/bge-small-zh-v1.5"))
EXAMPLES = [os.path.join(HERE, "skill_examples.json"),
            os.path.join(HERE, "..", "..", "config", "skills", "examples.json")]
MIN_SIM = 0.62                 # nearest example of a skill at least this similar
MARGIN = 0.03                  # ... and this much ahead of the nearest chit-chat example

RULES = (     # first match wins; certain words only (the embedding layer handles the rest)
    ("daily", re.compile(r"提醒|闹钟|叫我一下|叫醒我|日程|安排|几点.*(开会|出发)|分钟后|小时后|告诉我一声|跟我说一声|盯着")),
    ("world", re.compile(r"天气|下雨|气温|多少度|汇率|兑|币价|比特币|以太坊|股价|新闻|搜一下|搜搜|查一下|查查|上网|网上|研究一下")),
    ("body", re.compile(r"转过来|转过去|转身|向左|向右|左转|右转|看着我|点点头|点头|摇摇头|摇头|表情|音量|大声点|小声点|声音大|声音小|电量|跟着我转")),
    ("manner", re.compile(r"详细(一点|点|些|说)|简单(点|些|说)|说重点|安静|别插话|别插嘴|在聊天|看电视|扮演|当个|做回你自己|可以说话了|啰嗦")),
    ("recall", re.compile(r"上次|昨天|前天|前几天|之前|以前|还记得|记不记得|刚才.*(说|聊)|上周|上个月|今天上午|今天早上")),
    ("note", re.compile(r"记住|记下来|记一下|帮我记")),
    ("look", re.compile(r"你看(看|到|见)|看一下|看一眼|手里|面前|谁在|认识(他|她)|什么声音")),
    ("self", re.compile(r"为什么(不理|没回|那么说|不回答)|你能干什么|你会(些)?什么|花了多少|什么模式|开关")),
)


def rule(text):
    t = re.sub(r"\s+", "", str(text or ""))
    for sid, rx in RULES:
        if rx.search(t):
            return sid
    return None


class Embedder:
    def __init__(self, model_dir=MODEL_DIR, threads=1):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        path = os.path.join(model_dir, "model_quantized.onnx")
        if not os.path.exists(path):
            path = os.path.join(model_dir, "model.onnx")
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tok.enable_truncation(64)

    def __call__(self, text):
        np = self.np
        e = self.tok.encode(str(text))
        ids = np.array([e.ids], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": np.array([e.attention_mask], dtype=np.int64)}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        v = self.sess.run(None, feed)[0][0, 0]
        return v / (np.linalg.norm(v) or 1.0)


def load_examples(paths=EXAMPLES):
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if not k.startswith("_") and isinstance(v, list)}
        except (OSError, ValueError):
            continue
    return {}


class Classifier:
    def __init__(self, embed=None, examples=None, min_sim=MIN_SIM, margin=MARGIN):
        self.embed = embed
        self.min_sim, self.margin = min_sim, margin
        ex = examples if examples is not None else load_examples()
        self.groups = {}
        if embed is not None:
            for k, sents in ex.items():
                self.groups[k] = [embed(s) for s in sents]

    def scores(self, text):
        v = self.embed(text)
        return {k: max(float(v @ e) for e in vs) for k, vs in self.groups.items() if vs}

    def classify(self, text):
        t = re.sub(r"[\s，。！？、,.!?…~～]+", "", str(text or ""))
        if len(t) <= 1:
            return None, 0.0, "short"
        sid = rule(t)
        if sid:
            return sid, 1.0, "rule"
        if not self.groups:
            return None, 0.0, "off"
        sc = self.scores(t)
        none = sc.pop("none", 0.0)
        if not sc:
            return None, none, "none"
        best = max(sc, key=sc.get)
        if sc[best] >= self.min_sim and sc[best] >= none + self.margin:
            return best, round(sc[best], 3), "embed"
        return None, round(none, 3), "none"


# ---- 自动档 (owner 2026-11-18: "自动挡会是我们最倚重的档位"; nightly retraining: tools/intent/train.py) ----
ART_DIR = os.path.expanduser(os.environ.get("SOUL_INTENT_DIR", "~/soul-spike/models/intent"))
SKILL_IDS = ("look", "recall", "world", "daily", "body", "manner", "self", "note")
SECOND_SKILL = 0.6


class Auto:
    """head.json {w, b, threshold}: logistic regression on [embedding, rule hit] -> P(tools needed); labelled.json
    [[text, class], ...]: the labelled real sentences, whose nearest tool-needing ones pick the skills. Built by
    tools/intent/train.py; reload() picks up a new night's files (mtime)."""
    def __init__(self, embed, art_dir=ART_DIR, k=5):
        self.embed, self.dir, self.k = embed, art_dir, k
        self.np = embed.np if hasattr(embed, "np") else __import__("numpy")
        self.ready, self._mtime = False, None
        self.reload()

    def _paths(self):
        return os.path.join(self.dir, "head.json"), os.path.join(self.dir, "labelled.json")

    def reload(self):
        """-> True when (re)loaded. Never raises (a broken night keeps yesterday's head)."""
        head_p, lab_p = self._paths()
        try:
            mtime = (os.path.getmtime(head_p), os.path.getmtime(lab_p))
        except OSError:
            return False
        if mtime == self._mtime:
            return False
        try:
            with open(head_p, encoding="utf-8") as f:
                head = json.load(f)
            with open(lab_p, encoding="utf-8") as f:
                lab = [(t, c) for t, c in json.load(f) if c in SKILL_IDS]
            np = self.np
            w, b = np.array(head["w"], dtype=np.float32), float(head["b"])
            vecs = np.stack([self.embed(t) for t, _ in lab]) if lab else np.zeros((0, len(w) - 1), dtype=np.float32)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self.w, self.b, self.threshold = w, b, float(head.get("threshold", 0.5))
        self.version = head.get("version")
        self.pos_vecs, self.pos_cls = vecs, [c for _, c in lab]
        self._mtime, self.ready = mtime, True
        return True

    def decide(self, text):
        """-> {"need": P(tools needed), "skills": [up to 2 skill ids], "via": "head"/"short"/"off"}."""
        t = re.sub(r"[\s，。！？、,.!?…~～]+", "", str(text or ""))
        if len(t) <= 1:
            return {"need": 0.0, "skills": [], "via": "short"}
        if not self.ready:
            return {"need": 0.0, "skills": [], "via": "off"}
        np = self.np
        v = self.embed(t)
        hit = rule(t)
        x = np.concatenate([v, [1.0 if hit else 0.0]]).astype(np.float32)
        p = float(1 / (1 + np.exp(-(x @ self.w + self.b))))
        skills = [hit] if hit else []
        if p >= self.threshold and len(self.pos_cls):
            sims = self.pos_vecs @ v
            votes = {}
            for j in np.argsort(-sims)[: self.k]:
                votes[self.pos_cls[j]] = votes.get(self.pos_cls[j], 0.0) + float(sims[j])
            ranked = sorted(votes, key=votes.get, reverse=True)
            top = votes[ranked[0]]
            for c in ranked:                          # a second skill only when it is nearly as likely as the first
                if c not in skills and (not skills or votes[c] >= SECOND_SKILL * top):
                    skills.append(c)
        return {"need": round(p, 3), "skills": skills[:2] if p >= self.threshold else skills[:1], "via": "head"}
