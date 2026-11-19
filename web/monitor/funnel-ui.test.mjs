import test from 'node:test';
import assert from 'node:assert/strict';
import * as ui from './public/funnel-ui.js';

const row = (level, outcome, extra = {}) => ({ kind: 'gate.addressed', extra: { level, outcome, ...extra } });

test('the funnel counts levels and outcomes, names the call sources, no inline styles', () => {
  const rows = [row('must', 'answered', { called_source: 'asr' }), row('must', 'none', { called_source: 'touch' }),
    row('must', 'ambient', { called_source: 'touch' }), row('should', 'muted:repeat'), row('low', 'ambient')];
  const by = ui.count(rows);
  assert.equal(by.must.n, 3);
  assert.deepEqual(by.must.src, { asr: 1, touch: 2 });
  const h = ui.pageHtml(rows, '2026-11-19');
  assert.match(h, /33%/);                                    // must: 1 of 3 answered
  assert.match(h, /点屏幕 2/);
  assert.match(h, /千问没判定/);
  assert.match(h, /重复被挡/);
  assert.doesNotMatch(h, /style=/);
  assert.match(ui.pageHtml([], '2026-11-19'), /还没有记录/);
  assert.equal(ui.outZh('muted:xyz'), '被挡（xyz）');
  const [lo, hi] = ui.dayRange('2026-11-19');
  assert.equal(hi - lo, 86400000);
});
