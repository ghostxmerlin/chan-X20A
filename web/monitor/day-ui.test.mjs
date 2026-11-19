import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { dayHtml, taskHtml, progress, pill } from './public/day-ui.js';
import { readDay, root } from './server.mjs';

test('day page: the plan file reads, every task shows with its steps, nothing unescaped', () => {
  const d = readDay(path.join(root, 'docs/day'), '2026-11-19');
  const html = dayHtml(d);
  for (const t of d.tasks) assert.ok(html.includes(`id="task-${t.id}"`), t.id);
  assert.ok(!/style="/.test(html), 'no inline styles (the CSP drops them)');
  assert.ok(taskHtml({ id: 'x', n: 1, title: '<b>', status: 'doing', plan: [] }).includes('&lt;b&gt;'));
  assert.equal(readDay(path.join(root, 'docs/day')).date >= '2026-11-19', true);       // newest without a date
  assert.throws(() => readDay(path.join(root, 'docs/day'), '../../.env'));
});

test('day page: progress and status pills', () => {
  assert.equal(progress({ plan: [{ status: 'done' }, { status: 'todo' }, { status: 'later' }] }), 5);
  assert.equal(progress({ status: 'done', plan: [] }), 10);
  assert.match(pill('needs_you'), /s-needs_you">要你定/);
  assert.match(pill('bogus'), /s-todo/);
});
