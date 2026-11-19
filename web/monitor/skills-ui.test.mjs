import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { md, toolIndex, groupsHtml, uncovered, flowSvg, modeText, STAGES } from './public/skills-ui.js';
import { readSkills, root } from './server.mjs';

const cfg = readSkills(path.join(root, 'config/skills'));
const tools = toolIndex(JSON.parse(fs.readFileSync(path.join(root, 'config/tools/registry.json'))));

test('skills page: every skill in skills.json gets a card with its full text and tools', () => {
  const html = groupsHtml(cfg, tools);
  for (const sk of cfg.skills) {
    assert.ok(html.includes(`id="skill-${sk.id}"`), sk.id);
    assert.ok(sk.md, `${sk.id}.md missing`);
    for (const n of sk.tools) if (n !== '*') assert.ok(html.includes(`${n}<small>`), `${sk.id}: ${n}`);
  }
  assert.ok(!html.includes('未接入'), 'a skill names a tool that is not live');
});

test('skills page: markdown subset, escaping, unknown tool flagged', () => {
  const h = md('# 标题\n## 怎么做\n1. 调 `look_now`\n2. **别猜**\n- 一\n  - 二\n<script>');
  assert.ok(!h.includes('标题'));
  assert.ok(h.includes('<h4>怎么做</h4>') && h.includes('<ol><li>调 <code>look_now</code></li>') && h.includes('<b>别猜</b>'));
  assert.ok(h.includes('&lt;script&gt;'));
  const html = groupsHtml({ skills: [{ id: 'x', group: 'look', title: 't', when: 'w', tools: ['no_such_tool'], md: '' }] }, tools);
  assert.ok(html.includes('未接入') && html.includes('没有全文文件'));
});

test('skills page: tools no skill lists, current gear, flow drawing', () => {
  const left = uncovered(cfg.skills, tools);
  assert.ok(!left.includes('say') && !left.includes('db_query'));
  assert.equal(modeText({ realtime_tools: { on: true, mode: 'auto' } }), '自动');
  assert.equal(modeText({ realtime_tools: { on: false, mode: 'auto' } }), '关');
  const svg = flowSvg();
  assert.equal((svg.match(/class="box/g) || []).length, STAGES.length);
  assert.ok(svg.startsWith('<svg viewBox="0 0 900 '));
});
