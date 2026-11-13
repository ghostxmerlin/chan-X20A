"""Bounded, accounted JSON calls shared by speech and memory learning."""
import json
import threading

from . import llm, settings

MODEL = 'gpt-6-luna'
_LOCK = threading.RLock()


class BudgetExceeded(RuntimeError):
    pass


def object_schema(properties):
    return {'type':'object', 'additionalProperties':False, 'required':list(properties), 'properties':properties}


def run_json(env, store, s, step, prompt, schema, messages, now_ms, chat=None, lane='live', max_tokens=2000,
             reasoning_effort='low', timeout=45, model=MODEL):
    body = {'model':model, 'messages':messages, 'temperature':0.65 if step=='say' else 0.1,
            'max_tokens':max_tokens, 'reasoning_effort':reasoning_effort,
            'response_format':{'type':'json_schema', 'json_schema':{'name':step, 'strict':True, 'schema':schema}}}
    with _LOCK:
        if store.cost_today(s['tz'],now_ms)>=s['budget_usd_per_day']:
            raise BudgetExceeded('今天的模型预算已用完')
    # The HTTP call runs outside the lock so a talk-show round can write its lines in parallel
    # (2026-11-13). The budget check above may let a few concurrent calls through at the limit.
    resp, latency, err = (chat or llm.chat)(env['base_url'],env['api_key'],body,timeout=timeout)
    out = None
    if resp and not err:
        try:
            out = json.loads(resp['choices'][0]['message']['content'])
            if not isinstance(out,dict):
                raise ValueError('not a JSON object')
        except (KeyError,TypeError,ValueError,IndexError):
            out,err = None,'invalid model JSON'
    usage = (resp or {}).get('usage') or {}
    with _LOCK:
        run_id = store.put_run({'lane':lane, 'minute':settings.minute_floor(now_ms), 'step':step, 'prompt':prompt,
                                'model':model, 'request':body, 'response':resp, 'error':err, 'latency_ms':latency,
                                'usage':usage, 'cost_usd':llm.cost_usd(model,usage), 'at':now_ms})
    return out,run_id,err
