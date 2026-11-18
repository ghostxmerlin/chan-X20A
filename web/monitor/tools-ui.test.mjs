import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { render, summaryText, params } from './public/tools-ui.js';

const reg = JSON.parse(fs.readFileSync(new URL('../../config/tools/registry.json', import.meta.url)));

test('tools page: every tool appears once, filter by status works', () => {
  const html = render(reg);
  for (const t of reg.tools) assert.ok(html.includes(`<code>${t.name}</code>`), t.name);
  const onlyNew = render(reg, 'new');
  assert.ok(reg.tools.filter(t => t.status !== 'new').every(t => !onlyNew.includes(`<code>${t.name}</code>`)));
  assert.match(summaryText(reg), /^共 \d+ 个工具：/);
  assert.equal(params({ type: 'object', properties: {} }), '无');
  assert.match(params({ type: 'object', properties: { q: { type: 'string' } }, required: [] }), /q\?/);
});
