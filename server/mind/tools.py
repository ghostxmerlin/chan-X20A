"""hello's tool framework (B1, owner 2026-11-18; rules: docs/hello-tools.md, registry: config/tools/registry.json).

A tool is one ability hello can call (look, recall, say, move, change a setting). Skills call tools; tools are also
exported to Qwen's function calling. Every call goes through Tools.call():
  - the name must be in the registry and have an implementation here;
  - the arguments are checked against the registry's parameters (the function-calling subset: object of string /
    integer / number / boolean / array-of-scalars, enum, maxLength, minimum, maximum, required);
  - the answer is one envelope {ok, data, error{code, message}, ms, cost, source, trace};
  - one run-log row call.tool.<name> per call, on the caller's trace when it gives one.
Error codes (docs/hello-tools.md §3): bad_args, not_available, switch_off, paused, budget, robot_offline, timeout,
refused, failed. The message is one Chinese sentence hello may say as it is.

The registry ships with mind's deploy as ~/soul-mind/tools/registry.json (in the repository config/tools/). Only
tools whose status is "live" AND that have an implementation are exported to a model; any implemented tool can be
called (that is how a new one is tested before it is marked live).
"""
import json
import time
from pathlib import Path

from .runlog import hello_log

from .tool_errors import CODES, Paid, ToolError  # noqa: F401  (moved 2026-11-19, re-exported)
MODEL_CALLERS = ("qwen",)                         # callers that are models (security review 2026-11-18)
_HERE = Path(__file__).resolve()
REGISTRY_PATHS = (_HERE.parents[1] / "tools" / "registry.json",                # deployed: ~/soul-mind/tools/
                  _HERE.parents[2] / "config" / "tools" / "registry.json")     # repository: config/tools/


def load_registry(path=None):
    for p in ([Path(path)] if path else REGISTRY_PATHS):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return {"tools": [], "groups": {}, "levels": {}, "statuses": {}}


def _check(name, schema, value):
    t = schema.get("type")
    if t == "string":
        if not isinstance(value, str):
            raise ToolError("bad_args", f"{name} 应该是文字")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolError("bad_args", f"{name} 太长（最多 {schema['maxLength']} 字）")
        if "enum" in schema and value not in schema["enum"]:
            raise ToolError("bad_args", f"{name} 只能是 {' / '.join(schema['enum'])}")
        return value
    if t in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (t == "integer" and value != int(value)):
            raise ToolError("bad_args", f"{name} 应该是{'整数' if t == 'integer' else '数字'}")
        value = int(value) if t == "integer" else value
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            raise ToolError("bad_args", f"{name} 要在 {schema.get('minimum', '')}–{schema.get('maximum', '')} 之间")
        return value
    if t == "boolean":
        if not isinstance(value, bool):
            raise ToolError("bad_args", f"{name} 应该是是 / 否")
        return value
    if t == "array":
        if not isinstance(value, list) or len(value) > 50:
            raise ToolError("bad_args", f"{name} 应该是列表（最多 50 项）")
        return [_check(f"{name}[{i}]", schema.get("items", {}), v) for i, v in enumerate(value)]
    raise ToolError("bad_args", f"{name} 的类型不认识")


def validate(parameters, args):
    """args checked against a registry parameters object -> the cleaned dict (unknown keys refused)."""
    args = {} if args is None else args
    if not isinstance(args, dict):
        raise ToolError("bad_args", "参数应该是一个对象")
    props = (parameters or {}).get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError("bad_args", f"不认识的参数：{'、'.join(unknown)}")
    missing = [k for k in (parameters or {}).get("required", []) if args.get(k) in (None, "")]
    if missing:
        raise ToolError("bad_args", f"缺少参数：{'、'.join(missing)}")
    return {k: _check(k, props[k], v) for k, v in args.items() if v is not None}


class Tools:
    """registry: load_registry(); impls: name -> fn(args) -> data (or raise ToolError); cost of a paid tool: return
    {"data": ..., "cost": {"amount", "cur"}} wrapped by the implementation in a Paid(...)."""
    def __init__(self, registry, impls, clock_ms=None, log=None, proposals=None, describe=None):
        self.registry = registry or {}
        self.by_name = {t["name"]: t for t in self.registry.get("tools", [])}
        self.impls = dict(impls)
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.emit = log or hello_log.emit
        self.proposals = proposals                     # proposals.Proposals: C tools not asked by the owner in person
        self.describe = describe or {}                 # name -> fn(args) -> one Chinese line for the 待确认 list

    def available(self):
        return sorted(n for n in self.impls if n in self.by_name)

    def export(self, include_unlisted=False):
        """The function-calling list a model receives: live tools with an implementation (all implemented ones when
        include_unlisted, for tests and the page)."""
        out = []
        for name in self.available():
            t = self.by_name[name]
            if t.get("status") == "live" or include_unlisted:
                out.append({"type": "function", "function": {"name": name, "description": t["description"],
                                                             "parameters": t["parameters"]}})
        return out

    def call(self, name, args=None, trace=None, caller=None, owner=False):
        """owner: the owner asked in person (recognised) - a C tool then runs at once (2026-11-18), else it is a
        proposal."""
        t0 = time.monotonic()
        spec = self.by_name.get(name)
        data, cost, err = None, None, None
        try:
            if spec is None:
                raise ToolError("not_available", f"没有叫 {str(name)[:40]} 的工具")
            if caller in MODEL_CALLERS and spec.get("status") != "live":   # a model only gets what is tested
                raise ToolError("not_available", f"「{spec['title']}」还没接入")
            fn = self.impls.get(name)
            if fn is None:
                raise ToolError("not_available", f"「{spec['title']}」还没做好")
            clean = validate(spec.get("parameters"), args)
            if spec.get("level") == "C" and not owner and self.proposals is not None:
                line = self.describe[name](clean) if name in self.describe else spec["title"]
                out = self.proposals.create(name, spec["title"], clean, by=caller, trace=trace, summary=line)
            else:
                out = fn(clean)
            if isinstance(out, Paid):
                data, cost = out.data, out.cost
            else:
                data = out
        except ToolError as e:
            err = {"code": e.code, "message": e.message}
        except Exception as e:                          # noqa: BLE001 - a tool bug is one failed call, never a 500
            err = {"code": "failed", "message": f"出错了（{type(e).__name__}）"}
        ms = int((time.monotonic() - t0) * 1000)
        env = {"ok": err is None, "data": data if err is None else None, "error": err, "ms": ms, "cost": cost,
               "source": (spec or {}).get("runs_on", "mind"), "trace": trace}
        try:
            self.emit(f"call.tool.{name if spec else 'unknown'}", trace=trace, by=caller, ok=err is None, ms=ms,
                      err=err["code"] if err else None, cost=cost,
                      sum=(f"{spec['title']}" if spec else str(name)[:40]) + ("" if err is None else f"：{err['message'][:40]}"),
                      extra={"tool": str(name)[:60], "args": _short(args)})
        except Exception:                               # noqa: BLE001 - logging never fails a call
            pass
        return env


def _short(args):
    try:
        text = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return json.loads(text) if len(text) <= 400 else {"_cut": text[:400]}
