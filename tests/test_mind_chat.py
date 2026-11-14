"""Chat v0 (plan docs/superpowers/plans/2026-11-14-chat-v0.md): mind/chat.py gate, one turn at a time, context, the
chat speak path, leaving chat, chat_turns records, settings, /chat/heard and the mode hook. FakeDB, fake model, fake
soul-tts: nothing is paid or played."""
import copy
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0
from mind import api, chat as chat_mod, runner, selfvoice, speak, speech_settings
from mind.library import Library
from mind.settings_store import SettingsStore, validate
from mind import settings as settings_mod
from mind.store import Store

ROOT = Path(__file__).resolve().parents[1]
ENV = {'base_url': 'http://model.invalid', 'api_key': 'k'}


class Tts:
    def __init__(self):
        self.said, self.stops = [], 0

    def health(self):
        return {'mouth': {'device': {'device_id': 'x'}}}

    def say(self, text, **kw):
        self.said.append((text, kw))
        return f'tts{len(self.said)}'

    def stop(self):
        self.stops += 1
        return []

    def status(self, uid, wait=0):
        gate = getattr(self, 'playing', None)
        if gate is not None:
            gate.wait(5)                                  # the line is playing until the test releases it
        return {'state': 'done', 'created': time.time(), 'timings_s': {'first_accepted': 0.5, 'first_sound': 0.7, 'done': 2.0}}


class Model:
    """llm.chat stand-in: returns `reply` (or an error), records every body; `during` runs inside the call."""
    def __init__(self, reply='我在听，你说。', error=None):
        self.reply, self.error, self.bodies, self.during = reply, error, [], None

    def __call__(self, base, key, body, timeout=90):
        self.bodies.append(copy.deepcopy(body))
        if self.during:
            fn, self.during = self.during, None
            fn()
        if self.error:
            return None, 5, self.error
        return {'choices': [{'message': {'content': self.reply}}], 'usage': {'prompt_tokens': 1000, 'completion_tokens': 20}}, 400, None


def heard(text='今天中午吃什么好呢', n=1, **over):
    body = {'session': 's1', 'id': f's-{n}', 'pos': [n * 1000, n * 1000 + 800], 't': T0 + n * 1000, 'dur': 1.2,
            'text': text, 'lang': 'zh', 'emotion': 'neutral', 'event': 'speech',
            'speaker': {'band': 'unknown', 'name': None, 'score': 0.2},
            'voice': {'p': 0.5, 'band': 'voice', 'acoustic_band': 'unclear', 'loud_db': 20.0}, 'loud_db': 20.0,
            'self': {'sounding': False, 'moving': False},
            'debug': {'vad_wait_ms': 400, 'asr_ms': 150, 'server_ms': 500}, 'sent_at': T0 + n * 1000 + 1500}
    body.update(over)
    return body


class ChatBase(unittest.TestCase):
    def setUp(self):
        speak._PLAYING.clear()
        selfvoice.set_clips([])
        selfvoice.set_spoken([])
        self.db = FakeDB()
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': 'tts'}}, upsert=True)
        self.tts, self.model, self.mode = Tts(), Model(), ['chat']
        self.now, self.mono = [T0 + 10_000], [100.0]
        self.logs = []
        self.chat = chat_mod.Chat(self.db, self.store, settings_fn=lambda: self.ss.current()[0],
                                  roles_fn=lambda: speak.load_roles(str(ROOT / 'config/roles')), tts=self.tts, env=ENV,
                                  mode_fn=lambda: self.mode[0], chat_fn=self.model, clock_ms=lambda: self.now[0],
                                  mono=lambda: self.mono[0], log=lambda *a, **k: self.logs.append(' '.join(map(str, a))),
                                  threaded=False)
        self.chat.enter()

    def tearDown(self):
        speak._PLAYING.clear()
        selfvoice.set_spoken([])

    def turns(self):
        return sorted(self.db['chat_turns'].docs, key=lambda d: d['at'])

    def turn(self, tid):
        return next(d for d in self.db['chat_turns'].docs if d['tid'] == tid)


class GateTest(ChatBase):
    def skip_of(self, body):
        return self.chat.heard(body)['skip']

    def test_a_near_clear_sentence_is_accepted(self):
        r = self.chat.heard(heard())
        self.assertEqual((r['accepted'], r['skip']), (True, None))
        self.assertEqual(self.turn(r['tid'])['state'], 'queued')

    def test_every_gate_rule(self):
        cases = [
            ('self', heard(self={'sounding': True, 'moving': False})),
            ('empty', heard(text='  。')),
            ('filler', heard(text='嗯嗯啊')),
            ('filler', heard(text='Um.')),
            ('not_voice', heard(loud_db=9.5, voice={'p': 0.1, 'band': 'none', 'loud_db': 9.5})),
            ('far_quiet', heard(loud_db=4.0, voice={'p': 0.5, 'band': 'voice', 'loud_db': 4.0})),
            ('far_quiet', heard(loud_db=4.0, voice={'p': 0.1, 'band': 'none', 'loud_db': 4.0})),
            (None, heard(loud_db=11.5, voice={'p': 0.1, 'band': 'none', 'loud_db': 11.5})),      # none but loud: answered
            (None, heard(loud_db=8.5, voice={'p': 0.3, 'band': 'unclear', 'loud_db': 8.5})),   # just above near_db, unclear
            (None, heard(loud_db=30.0, voice={'p': 0.1, 'band': 'none', 'loud_db': 30.0})),
        ]
        for n, (reason, body) in enumerate(cases, start=1):
            body['id'] = f's-{100 + n}'
            self.assertEqual(self.skip_of(body), reason, body)

    def test_hello_own_voice_is_skipped(self):
        selfvoice.set_spoken(['今天中午吃什么好呢我来告诉你'])
        self.assertEqual(self.skip_of(heard()), 'hello_voice')

    def test_heard_while_hello_speaks_is_skipped(self):
        speak._PLAYING.add('x')
        self.assertEqual(self.skip_of(heard()), 'hello_speaking')

    def test_not_in_chat_and_mouth_not_tts(self):
        self.mode[0] = 'talk'
        self.assertEqual(self.skip_of(heard(n=1)), 'not_chat')
        self.mode[0] = 'chat'
        self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': 'clips'}})
        self.assertEqual(self.skip_of(heard(n=2)), 'mouth_not_tts')

    def test_near_db_is_the_setting(self):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['speech']['chat']['near_db'] = 25
        self.ss.save(s, 'test', expected_version=v)
        self.assertEqual(self.skip_of(heard(n=1)), 'far_quiet')           # 20 dB above the floor < 25
        self.assertEqual(self.skip_of(heard(n=2, loud_db=None, voice={'p': 0.5, 'band': 'voice', 'loud_db': None})), None)

    def test_not_voice_margin_follows_near_db(self):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['speech']['chat']['near_db'] = 10
        self.ss.save(s, 'test', expected_version=v)
        none = lambda n, loud: heard(n=n, loud_db=loud, voice={'p': 0.1, 'band': 'none', 'loud_db': loud})
        self.assertEqual(self.skip_of(none(1, 12.5)), 'not_voice')     # 10 + 3
        self.assertIsNone(self.skip_of(none(2, 13.5)))

    def test_unknown_floor_is_not_far(self):
        self.assertIsNone(self.skip_of(heard(loud_db=None, voice={'p': 0.5, 'band': 'unclear', 'loud_db': None})))

    def test_no_model_or_no_tts(self):
        self.chat.env = None
        self.assertEqual(self.skip_of(heard(n=1)), 'no_model')
        self.chat.env, self.chat.tts = ENV, None
        self.assertEqual(self.skip_of(heard(n=2)), 'no_tts')

    def test_duplicates_count_once(self):
        self.chat.heard(heard())
        self.assertEqual(self.chat.heard(heard())['skip'], 'duplicate')
        self.assertEqual(len(self.turns()), 1)

    def test_skips_are_recorded_and_logged_without_text(self):
        self.chat.heard(heard(text='嗯'))
        row = self.turns()[0]
        self.assertEqual((row['state'], row['skip'], row['text']), ('skipped', 'filler', '嗯'))
        self.assertTrue(any('skip filler' in line for line in self.logs))
        self.assertFalse(any('嗯' in line for line in self.logs))

    def test_malformed_bodies(self):
        for bad in ({'text': 3}, {'text': 'x', 'pos': 'a'}, {'text': 'x', 'speaker': []}, {'text': 'x', 'loud_db': 'loud'},
                    {'text': 'x' * 600}):
            with self.assertRaises(ValueError):
                self.chat.heard(bad)


class TurnTest(ChatBase):
    def test_one_turn_end_to_end(self):
        tid = self.chat.heard(heard())['tid']
        self.mono[0] += 0.2
        out = self.chat.process_once()
        self.assertEqual(out['state'], 'answered')
        text, kw = self.tts.said[0]
        self.assertEqual(text, '我在听，你说。')
        self.assertEqual(kw['voice'], 'wuxian')
        self.assertTrue(kw['interrupt'])
        self.assertIn('平静', kw['instruction'])                     # deadpan's delivery
        body = self.model.bodies[0]
        self.assertEqual((body['model'], body['reasoning_effort']), ('gpt-6.1-terra', 'none'))
        self.assertNotIn('response_format', body)
        row = self.turn(tid)
        self.assertEqual((row['reply'], row['model'], row['skip']), ('我在听，你说。', 'gpt-6.1-terra', None))
        self.assertAlmostEqual(row['cost_usd'], (1000 * 1.0 + 20 * 5.0) / 1e6)
        t = row['timings']
        self.assertEqual(t['ear'], {'vad_wait_ms': 400, 'asr_ms': 150, 'server_ms': 500})
        self.assertEqual(t['push_ms'], self.now[0] - (T0 + 2500))
        self.assertEqual((t['wait_ms'], t['tries'], t['tts_first_sound_ms'], t['tts_first_accepted_ms']), (200, 1, 700, 500))
        for k in ('context_ms', 'llm_ms', 'submit_at', 'end_to_submit_ms'):
            self.assertIn(k, t)
        u = self.db['utterances'].docs[0]
        self.assertEqual((u['role'], u['pipeline'], u['voice'], u['chat_tid'], u['state']), ('chat', 'chat', 'wuxian', tid, 'done'))
        run = self.db['mind_runs'].docs[0]
        self.assertEqual((run['step'], run['model']), ('chat', 'gpt-6.1-terra'))

    def test_model_is_a_setting(self):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['speech']['chat']['model'] = 'claude-haiku-5-5'
        self.ss.save(s, 'test', expected_version=v)
        self.chat.heard(heard())
        self.chat.process_once()
        self.assertEqual((self.model.bodies[0]['model'], self.model.bodies[0]['reasoning_effort']), ('claude-haiku-5-5', 'none'))

    def test_long_reply_is_trimmed(self):
        self.model.reply = '（歪头）“好的。' + '这是一句很长很长的话' * 10 + '。”'
        self.chat.heard(heard())
        text = self.chat.process_once()['reply']
        self.assertLessEqual(len(text), 61)
        self.assertNotIn('歪头', text)
        self.assertNotIn('“', text)

    def test_empty_reply_is_a_skip(self):
        self.model.reply = '  '
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual((self.turn(tid)['state'], self.turn(tid)['skip']), ('skipped', 'empty_reply'))
        self.assertEqual(self.tts.said, [])

    def test_the_daily_budget_guard_stops_chat_calls(self):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['budget_usd_per_day'] = 0.5
        self.ss.save(s, 'test', expected_version=v)
        self.db.add('mind_runs', {'at': self.now[0] - 1000, 'cost_usd': 0.6, 'step': 'fusion'})
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual((self.turn(tid)['skip'], self.model.bodies), ('budget', []))

    def test_model_error_retries_once_then_skips(self):
        self.model.error = 'HTTP 500: x'
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(len(self.model.bodies), 2)
        self.assertEqual((self.turn(tid)['skip'], self.turn(tid)['timings']['tries']), ('llm_error', 2))
        self.assertEqual(self.tts.said, [])

    def test_newest_waiting_sentence_wins_and_old_ones_go_stale(self):
        a = self.chat.heard(heard('第一句话说完了', n=1))['tid']
        b = self.chat.heard(heard('第二句话说完了', n=2))['tid']
        self.assertEqual(self.turn(a)['skip'], 'replaced')
        self.mono[0] += 6
        self.chat.process_once()
        self.assertEqual(self.turn(b)['skip'], 'stale')
        self.assertEqual(self.tts.said, [])
        self.assertIsNone(self.chat.process_once())

    def test_one_turn_at_a_time_answers_the_newest(self):
        a = self.chat.heard(heard('先问一个问题吧', n=1))['tid']
        holder = {}
        self.model.during = lambda: holder.update(b=self.chat.heard(heard('不对我换个问题', n=2))['tid'])
        self.chat.process_once()
        self.assertEqual((self.turn(a)['state'], self.turn(a)['skip']), ('superseded', 'superseded'))
        self.assertEqual(self.tts.said, [])
        self.chat.process_once()                                       # the newest gets the answer
        self.assertEqual(self.turn(holder['b'])['state'], 'answered')
        self.assertEqual(len(self.tts.said), 1)
        last_user = self.model.bodies[-1]['messages'][-1]['content']
        self.assertIn('先问一个问题吧', last_user)                    # the dropped one still counts as heard
        self.assertIn('不对我换个问题', last_user)

    def playing_tts(self):
        """soul-tts lines really play: status() blocks until released, on the finish thread (as live)."""
        self.tts.playing = threading.Event()
        self.chat.finish_async = True
        self.addCleanup(self.tts.playing.set)

    def three_segments(self):
        """One utterance split in three: 1 is dropped for 2, 2 is spoken although 3 waits (never twice in a row)."""
        self.chat.heard(heard(n=1, text='第一句话说完'))
        self.model.during = lambda: self.chat.heard(heard(n=2, text='第二句话说完'))
        self.chat.process_once()
        holder = {}
        self.model.during = lambda: holder.update(c=self.chat.heard(heard(n=3, text='第三句话说完'))['tid'])
        self.assertEqual(self.chat.process_once()['state'], 'answered')
        return holder['c']

    def test_never_dropped_twice_in_a_row(self):
        c = self.three_segments()
        self.assertEqual(self.chat.process_once()['state'], 'answered')
        self.assertEqual(self.turn(c)['state'], 'answered')

    def test_a_waiting_sentence_never_cuts_the_playing_reply(self):
        self.playing_tts()
        c = self.three_segments()
        self.assertTrue(speak.speaking())                              # the second reply is playing
        done = threading.Event()
        threading.Thread(target=lambda: (self.chat.process_once(), done.set()), daemon=True).start()
        time.sleep(0.3)
        self.assertEqual(len(self.tts.said), 1)                        # waits: the playing line is not cut
        self.assertFalse(done.is_set())
        self.tts.playing.set()                                         # the line ends
        self.assertTrue(done.wait(3))
        self.assertEqual(self.turn(c)['state'], 'answered')
        self.assertEqual(len(self.tts.said), 2)

    def test_a_sentence_that_waits_too_long_behind_the_reply_is_skipped(self):
        self.playing_tts()
        c = self.three_segments()
        threading.Timer(0.2, lambda: self.mono.__setitem__(0, self.mono[0] + 6)).start()
        self.chat.process_once()
        row = self.turn(c)
        self.assertEqual((row['state'], row['skip'], len(self.tts.said)), ('skipped', 'hello_speaking', 1))
        self.tts.playing.set()
        self.chat._finish_wait()
        self.chat.heard(heard(n=4, text='第四句话说完'))
        self.chat.process_once()
        self.assertIn('第三句话说完', self.model.bodies[-1]['messages'][-1]['content'])   # it still counts as heard

    def test_a_replaced_sentence_still_counts_as_heard(self):
        self.chat.heard(heard('先说半句', n=1))
        self.chat.heard(heard('再说后半句', n=2))
        self.chat.process_once()
        self.assertEqual(self.model.bodies[-1]['messages'][-1]['content'], '先说半句\n再说后半句')

    def test_history_goes_into_the_next_turn(self):
        self.chat.heard(heard('你叫什么名字', n=1))
        self.chat.process_once()
        self.chat.heard(heard('那你几岁了', n=2))
        self.chat.process_once()
        msgs = self.model.bodies[-1]['messages']
        self.assertEqual([m['role'] for m in msgs], ['system', 'user', 'assistant', 'user'])
        self.assertEqual((msgs[1]['content'], msgs[2]['content'], msgs[3]['content']),
                         ('你叫什么名字', '我在听，你说。', '那你几岁了'))

    def test_history_comes_back_after_reentering(self):
        self.chat.heard(heard('你叫什么名字', n=1))
        self.chat.process_once()
        fresh = chat_mod.Chat(self.db, self.store, settings_fn=lambda: self.ss.current()[0], roles_fn=lambda: {},
                              tts=self.tts, env=ENV, mode_fn=lambda: 'chat', chat_fn=self.model,
                              clock_ms=lambda: self.now[0], mono=lambda: self.mono[0], log=lambda *a, **k: None,
                              threaded=False)
        fresh.enter()
        fresh.heard(heard('还记得吗', n=2))
        fresh.process_once()
        self.assertIn('你叫什么名字', [m['content'] for m in self.model.bodies[-1]['messages']])

    def test_history_turns_setting(self):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['speech']['chat']['history_turns'] = 0
        self.ss.save(s, 'test', expected_version=v)
        self.chat.heard(heard('你叫什么名字', n=1))
        self.chat.process_once()
        self.chat.heard(heard('那你几岁了', n=2))
        self.chat.process_once()
        self.assertEqual(len(self.model.bodies[-1]['messages']), 2)



class StreamTts(Tts):
    """soul-tts with streaming input: every call goes to one ordered log shared with the model."""
    def __init__(self, order):
        super().__init__()
        self.order, self.n, self.fail_begin, self.fail_text = order, 0, None, None
        self.text_state, self.begin_locked, self.end_fails = 'synthesizing', [], 0

    def begin(self, timeout=None, **kw):
        if self.fail_begin:
            raise self.fail_begin
        self.begin_locked.append(speak._ENQUEUE_LOCK._is_owned())
        self.n += 1
        sid = f'st{self.n}'
        self.order.append(('begin', sid, {**kw, 'timeout': timeout}))
        return sid

    def text(self, uid, piece):
        if self.fail_text:
            raise self.fail_text
        self.order.append(('text', uid, piece))
        return {'id': uid, 'state': self.text_state}

    def end(self, uid):
        self.order.append(('end', uid))
        if self.end_fails:
            self.end_fails -= 1
            raise RuntimeError('end failed')

    def cancel(self, uid, timeout=None):
        self.order.append(('cancel', uid))
        return {'id': uid, 'state': 'cancelled'}

    def say(self, text, **kw):
        self.order.append(('say', text))
        return super().say(text, **kw)

    def status(self, uid, wait=0):
        gate = getattr(self, 'playing', None)
        if gate is not None:
            gate.wait(5)
        # soul-tts times count from the line start (the stream open): text at 0.9 s, audio 1.3 s, sound 1.6 s
        return {'state': 'done', 'created': time.time() - 2, 'timings_s': {'first_text': 0.9, 'first_audio': 1.3,
                                                                          'first_sound': 1.6, 'done': 3.0}}


class StreamTest(ChatBase):
    """chat v1: the soul-tts line opens when the turn is picked up, the reply goes in after the model, first sentence
    first; every unspoken end cancels the line."""

    def setUp(self):
        super().setUp()
        self.order = []
        self.tts = self.chat.tts = StreamTts(self.order)
        model = self.model
        self.chat.chat_fn = lambda *a, **k: (self.order.append(('llm',)), model(*a, **k))[1]

    def kinds(self):
        return [o[0] for o in self.order]

    def test_the_line_opens_before_the_model_and_gets_the_first_sentence_first(self):
        self.model.reply = '好的。我在听，你说。'
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['begin', 'llm', 'text', 'text', 'end'])
        _, sid, kw = self.order[0]
        self.assertEqual((kw['voice'], kw['interrupt'], kw['timeout']), ('wuxian', True, 2.0))
        self.assertEqual(self.tts.begin_locked, [True])                   # review v1 M3: under _ENQUEUE_LOCK
        self.assertIn('平静', kw['instruction'])
        self.assertEqual([o[2] for o in self.order if o[0] == 'text'], ['好的。', '我在听，你说。'])
        self.assertTrue(all(o[1] == sid for o in self.order if o[0] in ('text', 'end')))
        self.assertEqual(self.tts.said, [])                               # no whole-line say
        row = self.turn(tid)
        t = row['timings']
        self.assertEqual((row['state'], row['tts_id'], t['stream']), ('answered', sid, True))
        self.assertEqual((t['tts_first_audio_ms'], t['tts_first_sound_ms']), (1300, 1600))
        self.assertEqual((t['tts_text_to_audio_ms'], t['tts_text_to_sound_ms']), (400, 700))
        for k in ('stream_open_ms', 'stream_open_at', 'first_text_sent_ms', 'sound_at', 'end_to_sound_ms',
                  'submit_to_sound_ms'):
            self.assertIn(k, t)
        self.assertEqual(self.db['utterances'].docs[0]['tts_id'], sid)

    def test_one_sentence_goes_in_one_piece(self):
        self.chat.heard(heard())
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['begin', 'llm', 'text', 'end'])
        self.assertEqual(self.order[2][2], '我在听，你说。')

    def assert_cancelled_unspoken(self):
        sid = self.order[0][1]
        self.assertEqual(self.kinds()[0], 'begin')
        self.assertIn(('cancel', sid), self.order)
        self.assertNotIn('text', self.kinds())
        self.assertNotIn('say', self.kinds())
        self.assertFalse(speak.speaking())

    def test_an_empty_reply_cancels_the_line(self):
        self.model.reply = ' '
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assert_cancelled_unspoken()
        self.assertEqual(self.turn(tid)['skip'], 'empty_reply')

    def test_a_model_error_cancels_the_line(self):
        self.model.error = 'HTTP 500: x'
        self.chat.heard(heard())
        self.chat.process_once()
        self.assert_cancelled_unspoken()

    def test_a_superseded_reply_cancels_its_line_and_the_newest_gets_its_own(self):
        self.chat.heard(heard('先问一个问题吧', n=1))
        self.model.during = lambda: self.chat.heard(heard('不对我换个问题', n=2))
        self.chat.process_once()
        self.assert_cancelled_unspoken()
        self.order.clear()
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['begin', 'llm', 'text', 'end'])
        self.assertNotEqual(self.order[0][1], 'st1')

    def test_leaving_while_thinking_cancels_the_line(self):
        def leave():
            self.mode[0] = 'idle'
            self.chat.leave()
        self.model.during = leave
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assert_cancelled_unspoken()
        self.assertEqual(self.turn(tid)['skip'], 'left_chat')

    def test_a_stop_while_thinking_cancels_the_line(self):
        self.model.during = lambda: speak.cancel_pending(self.tts)
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assert_cancelled_unspoken()
        self.assertEqual(self.turn(tid)['skip'], 'cancelled')

    def test_no_robot_on_open_falls_back_to_say(self):
        self.tts.fail_begin = speak.NotConnected('hello 的嘴没连上')
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['llm', 'say'])
        row = self.turn(tid)
        self.assertEqual((row['state'], row['stream_error'], row['timings']['stream']), ('answered', 'NotConnected', False))

    def test_a_broken_stream_falls_back_to_say_and_is_cancelled(self):
        self.tts.fail_text = RuntimeError('gone')
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['begin', 'llm', 'say', 'cancel'])
        self.assertEqual(self.turn(tid)['state'], 'answered')

    def test_text_into_a_line_that_was_cancelled_meanwhile_falls_back_to_say(self):
        """review v1 M1: a page 试听 or a mouth drop ended the line during the think; soul-tts still takes the text."""
        self.tts.text_state = 'cancelled'
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['begin', 'llm', 'text', 'say', 'cancel'])
        row = self.turn(tid)
        self.assertEqual((row['state'], row['tts_id'], row['timings']['stream']), ('answered', 'tts1', False))

    def test_a_stop_before_the_open_opens_nothing(self):
        """review v1 M3: the interrupting open checks the epoch / mode under the lock like every say path."""
        opts = self.chat._role_opts
        self.chat._role_opts = lambda s: (speak.cancel_pending(), opts(s))[1]
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(self.kinds(), [])                                 # no line, no model call
        self.assertEqual(self.turn(tid)['skip'], 'cancelled')

    def test_leaving_before_the_open_opens_nothing(self):
        opts = self.chat._role_opts

        def leave(s):
            self.mode[0] = 'idle'
            return opts(s)
        self.chat._role_opts = leave
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual(self.kinds(), [])
        self.assertEqual(self.turn(tid)['skip'], 'left_chat')

    def test_over_budget_opens_nothing(self):
        """review v1 M5: the budget check runs before the open."""
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['budget_usd_per_day'] = 0.5
        self.ss.save(s, 'test', expected_version=v)
        self.db.add('mind_runs', {'at': self.now[0] - 1000, 'cost_usd': 0.6, 'step': 'fusion'})
        tid = self.chat.heard(heard())['tid']
        self.chat.process_once()
        self.assertEqual((self.turn(tid)['skip'], self.order), ('budget', []))

    def test_a_failed_end_is_retried_once(self):
        self.tts.end_fails = 1
        self.chat.heard(heard())
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['begin', 'llm', 'text', 'end', 'end'])

    def test_stream_off_in_settings_is_the_whole_line_say(self):
        s, v = self.ss.current()
        s = copy.deepcopy(s)
        s['speech']['chat']['stream'] = False
        self.ss.save(s, 'test', expected_version=v)
        self.chat.heard(heard())
        self.chat.process_once()
        self.assertEqual(self.kinds(), ['llm', 'say'])

    def test_a_waiting_sentence_opens_no_line_while_hello_speaks(self):
        """open() interrupts: it must never happen while the previous reply plays (review I2 with streams)."""
        self.tts.playing = threading.Event()
        self.addCleanup(self.tts.playing.set)
        self.chat.finish_async = True
        self.chat.heard(heard(n=1, text='第一句话说完'))
        self.model.during = lambda: self.chat.heard(heard(n=2, text='第二句话说完'))
        self.chat.process_once()                                      # 1 superseded by 2
        self.model.during = lambda: self.chat.heard(heard(n=3, text='第三句话说完'))
        self.chat.process_once()                                      # 2 spoken (playing), 3 waits
        begins = self.kinds().count('begin')
        done = threading.Event()
        threading.Thread(target=lambda: (self.chat.process_once(), done.set()), daemon=True).start()
        time.sleep(0.3)
        self.assertEqual(self.kinds().count('begin'), begins)        # no new line while the reply plays
        self.tts.playing.set()
        self.assertTrue(done.wait(3))
        self.assertEqual(self.kinds().count('begin'), begins + 1)


class TtsHttpStreamTest(unittest.TestCase):
    def test_stream_calls_hit_soul_tts_and_say_is_unchanged(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        got = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                got.append((self.path, body, self.headers.get('Authorization')))
                out = json.dumps({'id': 'u9', 'state': 'synthesizing'}).encode()
                self.send_response(202 if self.path == '/v1/say' else 200)
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)
        srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        tts = speak.TtsHttp(f'http://127.0.0.1:{srv.server_address[1]}', 'tok')
        self.assertEqual(tts.begin(voice='wuxian', interrupt=True, instruction='x', timeout=2.0), 'u9')
        self.assertEqual(tts.text('u9', '好的。')['state'], 'synthesizing')
        tts.end('u9')
        tts.cancel('u9', timeout=2.0)
        self.assertEqual(tts.say('整句', voice='wuxian', interrupt=True), 'u9')
        self.assertEqual([(p, b) for p, b, _ in got], [
            ('/v1/say', {'text': '', 'stream': True, 'voice': 'wuxian', 'interrupt': True, 'instruction': 'x'}),
            ('/v1/say/u9/text', {'text': '好的。'}), ('/v1/say/u9/end', {}), ('/v1/say/u9/cancel', {}),
            ('/v1/say', {'text': '整句', 'voice': 'wuxian', 'interrupt': True})])
        self.assertTrue(all(a == 'Bearer tok' for _, _, a in got))

    def test_split_first(self):
        self.assertEqual(chat_mod.split_first('好的。我在听，你说。'), ('好的。', '我在听，你说。'))
        self.assertEqual(chat_mod.split_first('真的吗？！那太好了'), ('真的吗？！', '那太好了'))
        self.assertEqual(chat_mod.split_first('没有句号的一句'), ('没有句号的一句', ''))
        self.assertEqual(chat_mod.split_first('。开头'), ('。开头', ''))

class LeaveTest(ChatBase):
    def test_leaving_cancels_speech_and_the_waiting_turn(self):
        tid = self.chat.heard(heard())['tid']
        self.chat.leave()
        self.assertEqual(self.tts.stops, 1)
        self.assertEqual(self.turn(tid)['skip'], 'left_chat')
        self.assertIsNone(self.chat.process_once())

    def test_leaving_while_thinking_drops_the_reply(self):
        tid = self.chat.heard(heard())['tid']

        def leave():
            self.mode[0] = 'idle'
            self.chat.leave()
        self.model.during = leave
        self.chat.process_once()
        self.assertEqual((self.turn(tid)['skip'], self.tts.said), ('left_chat', []))

    def test_a_stop_while_thinking_drops_the_reply(self):
        tid = self.chat.heard(heard())['tid']
        self.model.during = lambda: speak.cancel_pending(self.tts)
        self.chat.process_once()
        self.assertEqual((self.turn(tid)['skip'], self.tts.said), ('cancelled', []))

    def test_the_speak_path_refuses_outside_chat(self):
        s = self.ss.current()[0]
        self.mode[0] = 'talk'
        with self.assertRaises(chat_mod.NotInChat):
            self.chat.speak_line('你好', s)
        self.mode[0] = 'chat'
        self.chat.leave()
        with self.assertRaises(chat_mod.NotInChat):
            self.chat.speak_line('你好', s)
        self.chat.enter()
        self.db['switches'].update_one({'_id': 'mouth'}, {'$set': {'mode': 'clips'}})
        with self.assertRaises(speak.NotConnected):
            self.chat.speak_line('你好', s)
        self.assertEqual(self.tts.said, [])

    def test_the_speak_path_is_not_the_talk_shows(self):
        """Chat speaks in chat although the talk show refuses there (only_talk) and never takes _SAY_LOCK."""
        s = self.ss.current()[0]
        uid, tts_id, _ = self.chat.speak_line('你好呀', s)
        self.assertFalse(speak._SAY_LOCK.locked())
        self.assertEqual(self.tts.said[0][0], '你好呀')

    def test_reentering_works(self):
        self.chat.leave()
        self.chat.enter()
        self.chat.heard(heard())
        self.assertEqual(self.chat.process_once()['state'], 'answered')


class ContextTest(ChatBase):
    def add_person(self):
        lib = Library(self.db)
        card = lib.put('people_cards', {'kind': 'regular', 'display': '访客甲', 'links': {'voice_names': ['guest1'], 'face_names': []},
                                        'treatment': {'tease': 'never'}}, item_id=None, expected_version=None,
                       scope_id='office', now_ms=T0)
        lib.put('people_notes', {'person_id': card['person_id'], 'kind': 'preference', 'text': '喜欢喝冰美式'},
                item_id=None, expected_version=None, scope_id='office', now_ms=T0 + 1)
        lib.put('people_notes', {'person_id': card['person_id'], 'kind': 'trait', 'text': '旧笔记', 'status': 'retired'},
                item_id=None, expected_version=None, scope_id='office', now_ms=T0 + 2)
        return card

    def add_scene_notes_memories(self):
        self.db.add('mind_scenes', {'lane': 'live', 'minute': self.now[0] - 60_000, 'version': 1, 'source': 'model',
                                    'scene': {'summary': '两个人在工位聊天', 'people_doing': {'text': '在讨论午饭'},
                                              'entries': [{'dimension': 'topic', 'text': '午饭吃什么'},
                                                          {'dimension': 'sound_scene', 'text': '键盘声'}]}})
        self.db.add('conversations', {'cid': 'c1', 'start_ms': self.now[0] - 120_000, 'end_ms': None,
                                      'title': '午饭', 'notes': '想吃面'})
        for i, st in enumerate(('主人中午常吃面', '下午三点常开会', '周五会点奶茶', '午饭后会散步')):
            self.db.add('mind_memories', {'lane': 'live', 'scope_id': 'office', 'memory_id': f'm{i}', 'version': 1,
                                          'status': 'known', 'statement': st, 'kind': 'habit', 'manual': True,
                                          'last_seen': self.now[0], 'latest_evidence': {'last_seen': self.now[0]}})

    def test_context_has_scene_notes_memories_persona_and_the_person(self):
        self.add_person()
        self.add_scene_notes_memories()
        self.chat.heard(heard('中午吃面还是吃饭', speaker={'band': 'known', 'name': 'guest1', 'score': 0.8}))
        row = self.chat.process_once()
        system = self.model.bodies[0]['messages'][0]['content']
        for want in ('在讨论午饭', '两个人在工位聊天', '午饭吃什么', '键盘声', '午饭：想吃面', '主人中午常吃面',
                     '访客甲', '喜欢喝冰美式', '不要调侃他', '一本正经', '医疗'):
            self.assertIn(want, system)
        self.assertNotIn('旧笔记', system)
        self.assertNotIn('下午三点常开会', system)                     # unrelated memories stay out
        self.assertEqual(self.model.bodies[0]['messages'][-1]['content'], '【访客甲】中午吃面还是吃饭')
        self.assertTrue(row['person_id'].startswith('p_'))
        self.assertLess(row['timings']['context_ms'], 1000)

    def test_at_most_three_related_memories(self):
        mems = [f'午饭第{i}条记忆' for i in range(6)]
        self.assertEqual(len(chat_mod.related_memories(mems, '午饭吃什么')), 3)
        self.assertEqual(chat_mod.related_memories(mems, '开会'), [])
        self.assertEqual(chat_mod.related_memories(['今天下午三点开会', '我们一个周末去爬山'], '今天我们吃什么'), [])
        self.assertEqual(chat_mod.related_memories(['今天下午三点开会'], '下午几点开会'), ['今天下午三点开会'])

    def test_unknown_speaker_has_no_card_and_owner_falls_back_to_settings(self):
        self.add_person()
        self.assertIsNone(self.chat.person(self.ss.current()[0], {'band': 'unsure', 'name': 'guest1'}))
        self.chat._refresh(self.now[0], budget_ms=None)
        owner = self.chat.person(self.ss.current()[0], {'band': 'known', 'name': 'me'})
        self.assertEqual(owner['person_id'], 'owner')

    def test_parts_refresh_only_after_their_ttl(self):
        self.chat._refresh(self.now[0], budget_ms=None)
        self.add_scene_notes_memories()
        self.chat._refresh(self.now[0], budget_ms=None)
        self.assertIsNone(self.chat._part('scene'))                    # cached: not yet
        self.mono[0] += 21
        self.chat._refresh(self.now[0], budget_ms=None)
        self.assertEqual(self.chat._part('scene')['doing'], '在讨论午饭')
        self.assertIsNone(self.chat._part('notes'))                    # 30 s TTL
        self.assertEqual(self.chat._part('memories'), [])              # 120 s TTL

    def test_a_failing_part_keeps_the_rest(self):
        self.db['mind_memories'].fail = RuntimeError('db down')
        self.add_scene_notes_memories()
        self.chat.heard(heard())
        self.assertEqual(self.chat.process_once()['state'], 'answered')
        self.assertTrue(any('memories unavailable' in line for line in self.logs))

    def test_stale_scene_and_ended_notes_are_left_out(self):
        self.db.add('mind_scenes', {'lane': 'live', 'minute': self.now[0] - 600_000, 'version': 1, 'source': 'model',
                                    'scene': {'summary': '很久以前', 'people_doing': {'text': '旧情景'}}})
        self.db.add('conversations', {'cid': 'c1', 'start_ms': self.now[0] - 3600_000, 'end_ms': self.now[0] - 1800_000,
                                      'title': '旧', 'notes': '旧纪要'})
        self.chat.heard(heard())
        self.chat.process_once()
        system = self.model.bodies[0]['messages'][0]['content']
        self.assertNotIn('旧情景', system)
        self.assertNotIn('旧纪要', system)


class SettingsTest(unittest.TestCase):
    def test_defaults_and_validation(self):
        s = settings_mod.load(None)
        self.assertEqual(speech_settings.chat_config(s), {'model': 'gpt-6.1-terra', 'near_db': 8, 'max_chars': 60,
                                                          'history_turns': 8, 'stream': True})
        self.assertEqual(validate(s), [])
        for bad in ({'stream': 'yes'}, {'stream': 1}, {'model': ''}, {'model': 'a b'}, {'model': 'gpt-6.1-terra\n'}, {'model': 3}, {'near_db': -1}, {'near_db': True},
                    {'max_chars': 10}, {'max_chars': 60.5}, {'history_turns': 21}, {'other': 1}):
            t = copy.deepcopy(s)
            t['speech']['chat'] = {**t['speech']['chat'], **bad}
            self.assertTrue(validate(t), bad)

    def test_old_stored_settings_without_chat_get_the_defaults(self):
        s = settings_mod.load(None)
        del s['speech']['chat']
        self.assertEqual(speech_settings.chat_config(s)['model'], 'gpt-6.1-terra')
        self.assertEqual(speech_settings.validate(s['speech'], s['memory']), [])


class Unit(unittest.TestCase):
    def test_trim(self):
        self.assertEqual(chat_mod.trim('hello：你好呀！', 60), '你好呀！')
        self.assertEqual(chat_mod.trim("Hello，I'm here.", 60), "Hello，I'm here.")
        self.assertEqual(chat_mod.trim('好的。' + '啊' * 80, 20), '好的。' + '啊' * 17 + '。')
        self.assertEqual(chat_mod.trim('第一句很长很长很长。第二句也很长很长很长很长', 15), '第一句很长很长很长。')

    def test_filler(self):
        for t in ('嗯', '嗯嗯', '啊？', 'hmm', 'Uh.'):
            self.assertTrue(chat_mod.is_filler(t), t)
        for t in ('好', '嗯好的', 'hello'):
            self.assertFalse(chat_mod.is_filler(t), t)


class HookAndApiTest(unittest.TestCase):
    """runner.wire_modes starts / stops chat on mode reports; /chat/heard and /chat/turns over HTTP."""

    def setUp(self):
        speak._PLAYING.clear()
        selfvoice.set_spoken([])
        self.db = FakeDB()
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        self.tts, self.model = Tts(), Model()
        self.chat = chat_mod.Chat(self.db, self.store, settings_fn=lambda: self.ss.current()[0],
                                  roles_fn=lambda: speak.load_roles(str(ROOT / 'config/roles')), tts=self.tts, env=ENV,
                                  chat_fn=self.model, log=lambda *a, **k: None, threaded=False)
        self.ms, self.billing, _ = runner.wire_modes(self.db, self.store, settings_store=self.ss, tts=self.tts,
                                                     say_worker=None, say_auto=None, say_round_fn=None,
                                                     log=lambda *a, **k: None, chat=self.chat)
        self.server = api.make_server(self.store, 'k', port=0, ctx={'settings_store': self.ss, 'mode_store': self.ms,
                                                                    'chat': self.chat, 'tts': self.tts})
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.seq = 0

    def call(self, path, body=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_address[1]}' + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Bearer k', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def report(self, mode, inp):
        self.seq += 1
        return self.call('/mode/report', {'boot': 'b1', 'conn': 1, 'age_ms': 0, 'seq': self.seq, 'mode': mode, 'dir': None,
                                          'host': None, 'return_to': None, 'by': 'asr', 'input': inp,
                                          'effects': ['stop_speech']})

    def test_enter_and_leave_follow_the_mode(self):
        self.assertFalse(self.chat.active())
        self.report('chat', 'play')
        self.assertTrue(self.chat.active())
        stops = self.tts.stops
        self.report('idle', 'pause')
        self.assertFalse(self.chat.active())
        self.assertGreater(self.tts.stops, stops)

    def test_leaving_chat_runs_before_the_other_effects(self):
        order = []
        self.tts.stop = lambda: order.append('stop') or []

        class FakeChat:
            mode_fn = None

            def enter(self, now):
                order.append('enter')

            def leave(self, now):
                order.append('leave')
        ms, _, _ = runner.wire_modes(FakeDB(), Store(FakeDB()), settings_store=self.ss, tts=self.tts, say_worker=None,
                                     say_auto=None, say_round_fn=None, log=lambda *a, **k: None, chat=FakeChat())
        rep = lambda seq, mode, inp: ms.report({'seq': seq, 'mode': mode, 'dir': None, 'host': None, 'return_to': None,
                                                'by': 'asr', 'input': inp, 'effects': ['stop_speech']}, boot='b2', conn=1)
        rep(1, 'chat', 'play')
        order.clear()
        rep(2, 'talk', 'forward')                                    # effects: stop_speech (+ talk_enter in live)
        self.assertEqual(order, ['leave', 'stop'])

    def test_settings_over_chat_stops_chat_and_close_comes_back(self):
        self.report('chat', 'play')
        self.seq += 1
        self.call('/mode/report', {'boot': 'b1', 'conn': 1, 'age_ms': 0, 'seq': self.seq, 'mode': 'settings', 'dir': None,
                                   'host': None, 'return_to': 'chat', 'by': 'asr', 'input': 'open', 'effects': ['stop_speech']})
        self.assertFalse(self.chat.active())
        self.report('chat', 'close')
        self.assertTrue(self.chat.active())

    def test_heard_over_http(self):
        self.report('chat', 'play')
        st, r = self.call('/chat/heard', heard())
        self.assertEqual((st, r['accepted']), (200, True))
        self.chat.process_once()
        st, rows = self.call('/chat/turns?limit=3')
        self.assertEqual((st, rows[0]['text'], rows[0]['reply'], rows[0]['state']), (200, heard()['text'], '我在听，你说。', 'answered'))
        self.assertEqual(self.call('/chat/heard', {'text': 5})[0], 400)

    def test_the_model_switches_through_speech_settings(self):
        st, cfg = self.call('/speech/settings')
        self.assertEqual((st, cfg['speech']['chat']['model']), (200, 'gpt-6.1-terra'))
        cfg['speech']['chat']['model'] = 'claude-haiku-5-5'
        st, out = self.call('/speech/settings', {'speech': cfg['speech'], 'memory': cfg['memory'],
                                                 'expected_version': cfg['version'], 'note': '对话模型'})
        self.assertEqual((st, out['speech']['chat']['model']), (200, 'claude-haiku-5-5'))
        self.assertEqual(speech_settings.chat_config(self.ss.current()[0])['model'], 'claude-haiku-5-5')
        cfg['speech']['chat']['model'] = 'bad model'
        self.assertEqual(self.call('/speech/settings', {'speech': cfg['speech'], 'memory': cfg['memory'],
                                                        'expected_version': out['version']})[0], 400)

    def test_heard_outside_chat_is_recorded_as_not_chat(self):
        st, r = self.call('/chat/heard', heard())
        self.assertEqual((st, r['skip']), (200, 'not_chat'))

    def test_mind_restart_in_chat_starts_chat(self):
        self.report('chat', 'play')
        chat2 = chat_mod.Chat(self.db, self.store, settings_fn=lambda: self.ss.current()[0], roles_fn=lambda: {},
                              tts=self.tts, env=ENV, chat_fn=self.model, log=lambda *a, **k: None, threaded=False)
        runner.wire_modes(self.db, self.store, settings_store=self.ss, tts=self.tts, say_worker=None, say_auto=None,
                          say_round_fn=None, log=lambda *a, **k: None, chat=chat2)
        self.assertTrue(chat2.active())


class ThreadedTest(ChatBase):
    def test_the_worker_thread_answers_by_itself_and_ends_on_leave(self):
        c = chat_mod.Chat(self.db, self.store, settings_fn=lambda: self.ss.current()[0], roles_fn=lambda: {},
                          tts=self.tts, env=ENV, mode_fn=lambda: self.mode[0], chat_fn=self.model,
                          log=lambda *a, **k: None, threaded=True)
        c.enter()
        r = c.heard(heard())
        deadline = time.time() + 3
        while self.turn(r['tid'])['state'] != 'answered' and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.turn(r['tid'])['state'], 'answered')
        t = c._thread
        self.mode[0] = 'idle'
        c.leave()
        t.join(2)
        self.assertFalse(t.is_alive())


if __name__ == '__main__':
    unittest.main()
