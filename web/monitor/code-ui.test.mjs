import test from 'node:test';
import assert from 'node:assert/strict';
import * as ui from './public/code-ui.js';

const D = {generated: '2026-11-19 18:00', commit: 'abc', layer_names: {1: '接口层', 2: '编排层', 3: '规则层', 4: '适配层', 5: '基础层', tool: '脚本工具'},
  totals: {ear: {modules: 2, lines: 3700}},
  modules: {'ear/realtime_link.py': {pkg: 'ear', lines: 3640, layer: 2, zh: 'Docking 会话', doc: '', imports: ['ear/mouth_link.py'], collections: ['frames']},
            'ear/mouth_link.py': {pkg: 'ear', lines: 60, layer: 1, zh: null, doc: 'the mouth <link>', imports: [], collections: []}},
  violations: [{from: 'ear/realtime_link.py', to: 'ear/mouth_link.py', from_layer: 2, to_layer: 1}]};

test('the code map page: services, layers, bars, upward imports, escaping', () => {
  const h = ui.pageHtml(D);
  assert.match(h, /3\.7k/);
  assert.match(h, /往上调的 1 处/);
  assert.match(h, /class="mod big"/);                          // over 800 lines
  assert.match(h, /class="L2 w10"/);                           // the largest module: full bar
  assert.match(h, /class="L1 w1"/);                            // at least one tenth
  assert.match(h, /<code class="up">mouth_link\.py<\/code>/);
  assert.match(h, /the mouth &lt;link&gt;/);
  assert.doesNotMatch(h, /style=/);                            // the monitor's CSP drops inline styles
  assert.equal(ui.short('mind/api.py'), 'api.py');
});
