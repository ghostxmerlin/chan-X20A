"""Bounded, accounted JSON calls shared by speech and memory learning."""
import json
import threading

from . import llm, pause, settings
from .runlog import hello_log, trace as log_trace

MODEL = 'gpt-6-luna'
_LOCK = threading.RLock()


class BudgetExceeded(RuntimeError):
    pass


def object_schema(properties):
    return {'type':'object', 'additionalProperties':False, 'required':list(properties), 'properties':properties}


def run_json(env, store, s, step, prompt, schema, messages, now_ms, chat=None, lane='live', max_tokens=2000,
             reasoning_effort='low', timeout=45, model=MODEL, temperature=None):
    if temperature is None:
        temperature = 0.65 if step=='say' else 0.1
    body = {'model':model, 'messages':messages, 'temperature':temperature,
            'max_tokens':max_tokens, 'reasoning_effort':reasoning_effort,
            'response_format':{'type':'json_schema', 'json_schema':{'name':step, 'strict':True, 'schema':schema}}}
    # The one daily cap (2026-11-17, mind/pause.py): today's spend over every paid item reached pause.cap_cny_per_day
    # and the owner has not pressed 9 since. Replaces budget_usd_per_day (mind_runs only).
    if pause.budget_stop(store.db, now_ms):
        raise BudgetExceeded('今天的花费已到上限')
    # The HTTP call runs outside any lock so a talk-show round can write its lines in parallel
    # (2026-11-13). The cap is read from the run log's ledger every 30 s: a few calls can pass at the limit.
    # run log: call.<step> with _ as dots (say_line -> call.say.line, memory_extract -> call.memory.extract)
    with hello_log.scope('call.'+step.replace('_','.'), trace=log_trace(),
                         ref={'minute':settings.minute_floor(now_ms), 'lane':lane, 'step':step}):
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
