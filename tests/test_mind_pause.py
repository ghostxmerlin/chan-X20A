"""hello's pause (server/mind/pause.py, 2026-11-17): nobody there for 10 minutes, or today's spend over the one daily
cap, pauses the paid items in a state of its own; the owner's switches are never touched and everything resumes as
the owner left it. Also the readers' gates (fusion, 提前写 / 连续说, the memory worker) and the API side (GET
/switches carries the pause, every explicit owner input reaches note_owner, the ear's 闭嘴 and hello's power-on 9
do not)."""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mind_fixtures import FakeDB, T0, away_pause, budget_pause, session  # noqa: E402
from mind import api, pause, runner, say_auto, say_prefetch, settings  # noqa: E402
from mind.settings_store import SettingsStore  # noqa: E402
from mind.store import Store  # noqa: E402

MIN = 60_000
CFG = pause.config({})
MIDNIGHT = pause.next_midnight(T0)          # T0 is 2026-11-10 16:40 Beijing


def decide(prev=None, *, now, presence=None, owner_at=None, owner_what=None, spend=None, cfg=CFG, started_at=T0):
    return pause.decide(prev or {}, now=now, cfg=cfg, presence=presence, owner_at=owner_at, owner_what=owner_what,
                        spend=spend, unknown_n=0, started_at=started_at)


def kinds(events):
    return [k for k, _ in events]


class DecideAwayTest(unittest.TestCase):
    def test_defaults_are_ten_minutes_twenty_yuan_and_7_2(self):
        self.assertEqual((CFG['away_ms'], CFG['cap_cny'], CFG['usd_cny']), (10 * MIN, 20.0, 7.2))
        self.assertEqual(pause.config({'pause': {'away_min': 3}})['away_ms'], 3 * MIN)

    def test_ten_minutes_without_a_face_a_known_voice_or_an_owner_input_pauses(self):
        pr = {'face_at': T0, 'voice_at': None, 'owner_at': None, 'faces': 0, 'at': T0 + 9 * MIN}
        doc, ev = decide(now=T0 + 9 * MIN, presence=pr)
        self.assertEqual((doc['on'], ev), (False, []))
        doc, ev = decide(doc, now=T0 + 10 * MIN, presence=dict(pr, at=T0 + 10 * MIN))
        self.assertEqual((doc['on'], doc['why'], doc['away_since'], kinds(ev)), (True, 'away', T0 + 10 * MIN, ['switch.pause']))
        self.assertEqual(ev[0][1]['extra']['why'], 'away')

    def test_ordinary_speech_is_not_presence_only_the_known_band_counts(self):
        # the ear writes voice_at only for band "known" (ear/presence.py); a TV leaves it alone
        pr = {'face_at': None, 'voice_at': T0 + 5 * MIN, 'faces': 0, 'at': T0 + 12 * MIN}
        self.assertFalse(decide(now=T0 + 12 * MIN, presence=pr)[0]['on'])
        self.assertTrue(decide(now=T0 + 16 * MIN, presence=dict(pr, at=T0 + 16 * MIN))[0]['on'])

    def test_a_face_in_view_is_presence_even_without_events(self):
        pr = {'face_at': T0, 'faces': 1, 'at': T0 + 59 * MIN}
        self.assertFalse(decide(now=T0 + 60 * MIN, presence=pr)[0]['on'])

    def test_faces_in_a_stale_presence_report_do_not_count(self):
        pr = {'face_at': T0, 'faces': 1, 'at': T0}                  # the ear stopped writing
        self.assertTrue(decide(now=T0 + 60 * MIN, presence=pr)[0]['on'])

    def test_back_by_a_face_resumes_and_says_why(self):
        doc, _ = decide(now=T0 + 11 * MIN, presence={'face_at': T0, 'at': T0 + 11 * MIN})
        doc, ev = decide(doc, now=T0 + 20 * MIN, presence={'face_at': T0 + 20 * MIN, 'at': T0 + 20 * MIN})
        self.assertEqual((doc['on'], kinds(ev)), (False, ['switch.resume']))
        self.assertEqual((ev[0][1]['extra']['why'], ev[0][1]['extra']['paused_ms']), ('face', 9 * MIN))

    def test_back_by_an_owner_input(self):
        doc, _ = decide(now=T0 + 11 * MIN, presence={'face_at': T0, 'at': T0 + 11 * MIN})
        doc, ev = decide(doc, now=T0 + 12 * MIN, owner_at=T0 + 12 * MIN, owner_what='page')
        self.assertEqual((doc['on'], ev[0][1]['extra']['why']), (False, 'owner:page'))
        doc, ev = decide(doc, now=T0 + 30 * MIN, presence={'owner_at': T0 + 29 * MIN, 'owner_what': 'touch:tap',
                                                           'at': T0 + 29 * MIN})
        self.assertEqual(doc['on'], False)

    def test_presence_never_goes_back_in_time(self):
        """An ear restart writes an empty report: mind keeps the last presence it knew."""
        doc, _ = decide(now=T0 + 5 * MIN, presence={'face_at': T0 + 5 * MIN, 'at': T0 + 5 * MIN})
        doc, _ = decide(doc, now=T0 + 12 * MIN, presence={'face_at': None, 'at': T0 + 12 * MIN})
        self.assertFalse(doc['on'])
        self.assertEqual(doc['last_presence'], T0 + 5 * MIN)

    def test_nothing_known_at_all_counts_from_the_start(self):
        self.assertFalse(decide(now=T0 + 9 * MIN)[0]['on'])
        self.assertTrue(decide(now=T0 + 10 * MIN)[0]['on'])


class DecideBudgetTest(unittest.TestCase):
    def present(self, now):
        return {'face_at': now, 'faces': 1, 'at': now}

    def test_spend_converts_usd_at_the_fixed_rate(self):
        self.assertEqual(pause.spend_cny({'total': {'CNY': 1.0, 'USD': 2.0}, 'unknown_n': 3}, 7.2), (15.4, 3))
        self.assertEqual(pause.spend_cny(None, 7.2), (0.0, 0))

    def test_warns_once_at_80_percent(self):
        now = T0
        doc, ev = decide(now=now, presence=self.present(now), spend=16.0)
        self.assertEqual((doc['on'], kinds(ev), doc['budget']['warn']), (False, ['budget.warn'], True))
        doc, ev = decide(doc, now=now + MIN, presence=self.present(now + MIN), spend=17.0)
        self.assertEqual(ev, [])

    def test_the_cap_pauses_until_midnight(self):
        doc, ev = decide(now=T0, presence=self.present(T0), spend=20.0)
        self.assertEqual((doc['on'], doc['why'], doc['budget_until']), (True, 'budget', MIDNIGHT))
        self.assertIn('switch.pause', kinds(ev))
        self.assertTrue(pause.active(doc, MIDNIGHT - 1))
        self.assertFalse(pause.active(doc, MIDNIGHT))                # ends by itself even with mind away
        nxt = MIDNIGHT + MIN
        doc, ev = decide(doc, now=nxt, presence=self.present(nxt), spend=0.1)
        self.assertEqual((doc['on'], ev[-1][1]['extra']['why']), (False, 'midnight'))

    def test_the_owners_nine_lifts_it_for_the_rest_of_the_day(self):
        doc, _ = decide(now=T0, presence=self.present(T0), spend=21.0)
        doc = dict(doc, override_day=pause.day_of(T0))              # what note_owner(nine=True) does
        doc, ev = decide(doc, now=T0 + MIN, presence=self.present(T0 + MIN), spend=22.0)
        self.assertEqual((doc['on'], ev[0][1]['extra']['why'], doc['budget']['override']), (False, 'nine', True))
        nxt = MIDNIGHT + MIN                                        # the next day the cap counts again
        self.assertTrue(decide(doc, now=nxt, presence=self.present(nxt), spend=25.0)[0]['on'])

    def test_raising_the_cap_lifts_it(self):
        doc, _ = decide(now=T0, presence=self.present(T0), spend=21.0)
        doc, ev = decide(doc, now=T0 + MIN, presence=self.present(T0 + MIN), spend=21.0,
                         cfg=pause.config({'pause': {'cap_cny_per_day': 30}}))
        self.assertEqual((doc['on'], ev[-1][1]['extra']['why']), (False, 'cap'))

    def test_unknown_spend_never_pauses(self):
        self.assertFalse(decide(now=T0, presence=self.present(T0), spend=None)[0]['on'])

    def test_both_reasons_and_one_lifting(self):
        doc, _ = decide(now=T0 + 11 * MIN, presence={'face_at': T0, 'at': T0 + 11 * MIN}, spend=25.0)
        self.assertEqual(doc['reasons'], ['budget', 'away'])
        doc, ev = decide(doc, now=T0 + 12 * MIN, presence=self.present(T0 + 12 * MIN), spend=25.0)
        self.assertEqual((doc['on'], doc['reasons'], kinds(ev)), (True, ['budget'], ['switch.pause']))
        self.assertEqual((ev[0][1]['extra']['lifted'], ev[0][1]['extra']['lifted_by']), (['away'], 'face'))


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.now = [T0]
        self.spend = [{'total': {}}]
        self.events = []
        self.settings = settings.load(None)
        for name, on in (('tingwu', True), ('eye', True), ('notes', True), ('realtime', False)):
            self.db['switches'].update_one({'_id': name}, {'$set': {'on': on}}, upsert=True)
        self.c = self.make()

    def make(self):
        return pause.Controller(self.db, settings_fn=lambda: self.settings, ledger_fn=lambda day: self.spend[0],
                                clock_ms=lambda: self.now[0], emit=lambda kind, **f: self.events.append((kind, f)))

    def switches(self):
        return {d['_id']: d.get('on') for d in self.db['switches'].docs if d['_id'] in ('tingwu', 'eye', 'notes', 'realtime')}

    def test_pause_and_resume_leave_the_owners_switches_alone(self):
        before = self.switches()
        self.now[0] = T0 + 11 * MIN
        self.c.tick()
        self.assertTrue(pause.paused(self.db, self.now[0]))
        self.assertEqual(self.switches(), before)
        self.c.note_owner('page')
        self.assertFalse(pause.paused(self.db, self.now[0]))         # lifted before the reply
        self.assertEqual(self.switches(), before)
        self.assertEqual([k for k, _ in self.events], ['switch.pause', 'switch.resume'])
        self.assertEqual(self.events[1][1]['extra']['why'], 'owner:page')

    def test_a_restart_keeps_the_pause(self):
        self.now[0] = T0 + 11 * MIN
        self.c.tick()
        self.c = self.make()
        self.now[0] += MIN
        self.c.tick()
        self.assertTrue(self.c.paused())
        self.assertEqual([k for k, _ in self.events], ['switch.pause'])

    def test_the_presence_report_counts(self):
        self.db['switches'].update_one({'_id': 'presence_status'}, {'$set': {'faces': 1, 'at': T0 + 30 * MIN}}, upsert=True)
        self.now[0] = T0 + 30 * MIN
        self.assertFalse(self.c.tick()['on'])

    def test_nine_over_the_cap_lifts_the_budget_pause(self):
        self.spend[0] = {'total': {'USD': 3.0}}                       # 21.6 CNY
        self.c.note_owner('page')                                     # someone is here
        self.assertTrue(pause.budget_stop(self.db, self.now[0]))
        self.c.note_owner('asr:digit')                                # not a 9: still over
        self.assertTrue(self.c.budget_stopped())
        self.c.note_owner('quick:digit', nine=True)
        self.assertFalse(pause.budget_stop(self.db, self.now[0]))
        self.assertEqual(self.c.view()['override'], True)

    def test_view_says_why_since_when_and_the_spend(self):
        self.spend[0] = {'total': {'CNY': 5.0}}
        self.now[0] = T0 + 11 * MIN
        self.c.tick()
        v = self.c.view()
        self.assertEqual((v['paused'], v['why'], v['away_since'], v['spent_cny'], v['cap_cny']),
                         (True, 'away', T0 + 11 * MIN, 5.0, 20.0))

    def test_the_ledger_is_read_at_most_every_30_s(self):
        n = [0]

        def ledger(day):
            n[0] += 1
            return {'total': {}}
        self.c.ledger_fn = ledger
        for k in range(6):
            self.now[0] = T0 + k * 5000
            self.c.tick()
        self.assertEqual(n[0], 1)


class GatesTest(unittest.TestCase):
    """The paused items in mind's own process and the memory worker."""
    def test_fusion_makes_no_call_while_paused(self):
        db = FakeDB()
        store = Store(db)
        self.assertEqual(runner.fuse({'base_url': 'x', 'api_key': 'k'}, store, settings.load(None), [T0], T0 + 2 * MIN,
                                     paused_fn=lambda: True), 0)
        self.assertEqual(db.count('mind_runs'), 0)

    def test_prefetch_and_auto_say_paused_and_stay_on(self):
        st = Store(FakeDB())
        w = say_prefetch.Worker(st, settings_fn=dict, mouth_fn=lambda: 'tts', ctx_fn=None, write_fn=None)
        a = say_auto.Worker(st, prefetch=w, settings_fn=dict, mouth_fn=lambda: 'tts', play_fn=None)
        a.set_enabled(True, T0)
        w.paused_fn = a.paused_fn = lambda: True
        self.assertEqual((w.step(T0), a.step(T0)), ('paused', 'paused'))
        self.assertEqual((w.enabled(), a.enabled()), (True, True))

    def test_the_memory_worker_skips_away_windows(self):
        from mind import memory_worker
        db = FakeDB().add('sessions', session('S', T0 - MIN))
        store, ss = Store(db), SettingsStore(db, '/nonexistent.json')
        memory_worker.tick(db, store, ss, {'base_url': 'x', 'api_key': 'k'}, T0)
        away_pause(db, since=T0)
        self.assertEqual(memory_worker.tick(db, store, ss, {'base_url': 'x', 'api_key': 'k'}, T0 + 700_000)['state'], 'paused')
        budget_pause(db)                          # the cap alone: run_json's BudgetExceeded, the window is kept
        self.assertFalse(pause.away(db))

    def test_readers_never_raise(self):
        db = FakeDB()
        db['switches'].fail = RuntimeError('db down')
        self.assertEqual((pause.paused(db), pause.budget_stop(db), pause.away(db)), (False, False, False))


class PauseApiTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.store = Store(self.db)
        self.ss = SettingsStore(self.db, '/nonexistent.json')
        self.now = [T0]
        self.notes = []
        self.ms, self.billing, self.gate = runner.wire_modes(self.db, self.store, settings_store=self.ss, tts=None,
                                                             say_worker=None, say_auto=None, say_round_fn=None)
        self.ctl = pause.Controller(self.db, settings_fn=lambda: self.ss.current()[0], ledger_fn=lambda d: {'total': {}},
                                    clock_ms=lambda: self.now[0], emit=lambda kind, **f: None)
        real = self.ctl.note_owner
        self.ctl.note_owner = lambda what, **kw: (self.notes.append((what, kw.get('nine', False))), real(what, **kw))
        self.server = api.make_server(self.store, 'k', port=0, ctx={'settings_store': self.ss, 'mode_store': self.ms,
                                                                    'billing': self.billing, 'pause': self.ctl})
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

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

    def test_switches_carry_the_pause(self):
        self.now[0] = T0 + 11 * MIN
        self.ctl.tick()
        p = self.call('/switches')[1]['pause']
        self.assertEqual((p['paused'], p['why'], p['cap_cny']), (True, 'away', 20.0))

    def test_page_switches_are_owner_inputs_the_ears_hush_is_not(self):
        self.call('/switches', {'name': 'notes', 'on': False})
        self.call('/switches', {'name': 'realtime', 'on': False, 'by': 'hush'})
        self.call('/switches', {'name': 'billing', 'on': True})
        self.call('/preset', {'n': 9})
        self.assertEqual(self.notes, [('page', False), ('page', True), ('page', True)])

    def test_hellos_reports_and_digits(self):
        rep = {'boot': 'b1', 'conn': 1, 'age_ms': 0, 'mode': 'crowd', 'dir': None, 'host': None, 'return_to': None,
               'effects': []}
        self.call('/mode/report', dict(rep, seq=1, by='boot', input='boot', effects=['stop_speech']))
        self.call('/mode/report', dict(rep, seq=2, by='touch', input='tap'))
        self.call('/mode/report', dict(rep, seq=3, by='timeout', input='timeout'))
        dig = {'device': 'robot', 'boot': 'b1', 'age_ms': 0, 'input': 'digit', 'value': 9}
        r = self.call('/mode/input', dict(dig, seq=1, source='quick'))[1]          # hello's power-on 9
        self.assertEqual((r['result']['toast'], r.get('power_on')), ('POWER ON: SWITCHES KEPT', True))
        self.call('/mode/input', dict(dig, seq=2, source='quick'))                 # the owner's 全开 after it
        self.assertEqual(self.notes, [('touch:tap', False), ('quick:digit', True)])

    def test_hellos_power_on_nine_keeps_the_switches(self):
        self.call('/switches', {'name': 'billing', 'on': False})
        self.call('/switches', {'name': 'notes', 'on': False})
        dig = {'device': 'robot', 'boot': 'b9', 'age_ms': 0, 'input': 'digit', 'value': 9, 'seq': 1, 'source': 'quick'}
        self.assertEqual(self.call('/mode/input', dig)[0], 200)
        sw = self.call('/switches')[1]
        self.assertEqual((sw['tingwu']['on'], sw['notes']['on'], sw['realtime']['on'], sw['mouth']['mode']),
                         (False, False, False, 'clips'))
        self.assertEqual(self.call('/mode/input', dict(dig, seq=2, source='boot'))[0], 200)   # a later firmware's word


if __name__ == '__main__':
    unittest.main()
