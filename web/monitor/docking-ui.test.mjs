import test from 'node:test';
import assert from 'node:assert/strict';
import * as ui from './public/docking-ui.js';

test('the Docking flow: ten stages, two loops, the touch / tool-busy rules, tables, no inline styles', () => {
  const s = ui.flowSvg();
  assert.equal(ui.STAGES.length, 10);
  assert.equal((s.match(/class="box/g) || []).length, 10);
  assert.match(s, /工具运行中 = 算在说话/);
  assert.match(s, /class="box warnbox"/);
  assert.match(s, /再调一个工具/);
  assert.doesNotMatch(s, /style=/);
  const t = ui.tablesHtml();
  assert.match(t, /调试模式/);
  assert.match(t, /直到我说退出/);
  assert.match(t, /LISTEN/);
  assert.equal(ui.esc('<a>'), '&lt;a&gt;');
});
