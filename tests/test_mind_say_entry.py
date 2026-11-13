"""speak.say_round / play_line: stock, check, fresh write, same-moment next line, thin-moment silence."""
import json
import random
import threading
import unittest
from unittest.mock import patch

from test_mind_speech_v2 import SpeechCase, ENV, ROOT
from test_mind_speak import FakeTts
from test_mind_say_round import RoutedChat, NoShuffle
from mind import say_prefetch, say_round, speak, speech_context, speech_settings


class SayEntryTest(SpeechCase):
    def setUp(self):
        super().setUp()
        self.s['speech']['pipeline'] = 'round'
        self.roles = speak.load_roles(ROOT / 'config/roles')
        self.tts = FakeTts()
        self.chat = RoutedChat()
        self.worker = say_prefetch.Worker(self.store, settings_fn=lambda: self.s, mouth_fn=lambda: 'tts',
                                          ctx_fn=lambda s, now: speech_context.build(self.db, self.store, s, now),
                                          write_fn=lambda ctx, now: None)

    def click(self, now=None, chat=None, **kw):
        with patch.object(speak, 'CHAT', chat or self.chat), patch.object(say_round, 'random', type('R', (), {'Random': NoShuffle})):
            return speak.say_round(ENV, self.store, self.s, self.roles, self.tts, now or self.now, worker=self.worker, wait=True, **kw)

    def stock(self, at, chat=None, weak=False):
        ctx = speech_context.build(self.db, self.store, self.s, at)
        r = say_round.write_round(ENV, self.store, self.s, self.roles, ctx, at, chat=chat or RoutedChat(weak=weak), rng=NoShuffle())
        self.worker.put(ctx['scope']['id'], r)
        return r

    def test_round_refused_without_mouth_costs_nothing(self):
        self.tts = FakeTts(connected=False)
        self.heard(age=8000)
        with self.assertRaises(speak.NotConnected):
            self.click()
        self.assertEqual(self.chat.calls, [])

    def test_stock_without_new_speech_plays_without_model_call(self):
        self.heard(age=8000)
        self.stock(self.now - 2000)
        rec = self.click()
        self.assertEqual((rec['why'], rec['played']), ('stock', 'deadpan'))
        self.assertEqual(self.chat.calls, [])
        self.assertEqual(self.tts.said[0][1]['voice'], speech_settings.role_config(self.roles['deadpan'], self.s)['voice'])

    def test_stock_with_new_speech_checks_once(self):
        self.heard(id=1, age=20000)
        self.stock(self.now - 15000)
        self.heard(id=2, age=3000, text='客户又说下周交付')
        chk = RoutedChat(check_pick=2)
        rec = self.click(chat=chk)
        self.assertEqual((rec['why'], rec['played']), ('checked', 'worst'))
        self.assertEqual([c[0] for c in chk.calls], ['check'])

    def test_same_moment_plays_next_line(self):
        self.heard(age=8000)
        self.stock(self.now - 2000)
        first = self.click()
        second = self.click(now=self.now + 5000)
        self.assertEqual((first['played'], second['played'], second['why']), ('deadpan', 'worst', 'next'))
        self.assertEqual(len(self.tts.said), 2)

    def test_thin_moment_huyu_then_silent(self):
        self.s['speech']['round'] = {'thin_judge_weak': True}        # opt-in since the 2026-11-13 replay
        self.heard(age=8000)
        self.stock(self.now - 2000, weak=True)
        first = self.click()
        second = self.click(now=self.now + 5000)
        self.assertEqual((first['why'], first['played']), ('huyu', 'huyu'))
        self.assertTrue(self.tts.said[0][1]['instruction'].startswith('请讲上海话。'))
        self.assertEqual((second['why'], second['played']), ('silent', None))
        self.assertEqual(len(self.tts.said), 1)

    def test_fresh_write_without_stock(self):
        self.heard(age=8000)
        rec = self.click()
        self.assertEqual(rec['why'], 'fresh')
        self.assertEqual([c[0] for c in self.chat.calls].count('line'), 5)

    def test_cancel_while_writing_never_speaks(self):
        self.heard(age=8000)
        inner = RoutedChat()
        def stopping(*a, **k):
            out = inner(*a, **k)
            speak.cancel_pending()
            return out
        with self.assertRaises(speak.NotConnected):
            self.click(chat=stopping)
        self.assertEqual(self.tts.said, [])

    def test_utterance_has_play_times_and_casting(self):
        self.heard(age=8000)
        self.stock(self.now - 2000)
        class TimedTts(FakeTts):
            def status(self, uid, wait=0):
                return {'state': 'done', 'timings_s': {'first_sound': 1.2, 'done': 4.0}, 'attempts': []}
        self.tts = TimedTts()
        rec = self.click()
        u = self.db['utterances'].find_one({'uid': rec['uid']})
        self.assertEqual((u['rid'], u['line'], u['pipeline']), (rec['rid'], 'deadpan', 'round'))
        self.assertEqual(u['play_start_at'] - u['tts_submit_at'], 1200)
        self.assertEqual(u['play_end_at'] - u['tts_submit_at'], 4000)
        self.assertEqual(u['casting']['voice'], self.tts.said[0][1]['voice'])

    def test_play_line_recast(self):
        self.heard(age=8000)
        r = self.stock(self.now - 2000)
        rec = speak.play_line(self.store, self.s, self.roles, self.tts, r['rid'], 'roast', self.now, voice='xiaohei', dialect='四川话')
        self.assertEqual(self.tts.said[0][1]['voice'], 'xiaohei')
        self.assertTrue(self.tts.said[0][1]['instruction'].startswith('请讲四川话。'))
        self.assertEqual(rec['line'], 'roast')

    def test_settings_round_defaults_validate(self):
        speech = speech_settings.defaults()
        self.assertEqual(speech['round']['prefetch_spacing_s'], 30)
        speech['pipeline'] = 'round'
        self.assertEqual(speech_settings.validate(speech, {'learning': True, 'apply': True}), [])
        speech['round']['prefetch_usd_per_hour'] = -1
        self.assertTrue(speech_settings.validate(speech, {'learning': True, 'apply': True}))


if __name__ == '__main__':
    unittest.main()
