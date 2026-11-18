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
test('tools page: 试一下 only for implemented read-only tools; argument skeleton', async () => {
  const { render, skeleton } = await import('./public/tools-ui.js');
  const html = render(reg, '', new Set(['switches_status', 'turn_head']));
  assert.ok(html.includes('data-tool="switches_status"'));
  assert.ok(!html.includes('data-tool="turn_head"'));            // A level: no button even if implemented
  assert.deepEqual(skeleton({ type: 'object', properties: { q: { type: 'string' }, n: { type: 'integer', minimum: 2 }, m: { type: 'string', enum: ['a', 'b'] } }, required: ['q', 'n', 'm'] }), { q: '', n: 2, m: 'a' });
});

test('proposal list escapes and shows both buttons', async () => {
  const { proposalHtml } = await import('./public/tools-ui.js');
  const html = proposalHtml([{ _id: 'q_1', summary: '把「识别」打开<script>', by: 'qwen', at: 0 }], 30 * 60000);
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('data-approve="1"') && html.includes('data-approve="0"'));
  assert.ok(html.includes('30 分钟前'));
  assert.ok(proposalHtml([]).includes('没有待确认'));
});
