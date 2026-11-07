import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { Monitor } from './core.mjs';
import { createMonitorServer } from './server.mjs';

test('local read-only API does not disclose binding secret and rejects foreign origins, hosts and writes', async t => {
  const monitor = new Monitor({ binding: { address: 'http://127.0.0.1:8080', token: 'b'.repeat(32) } });
  const server = createMonitorServer(monitor);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const url = `http://127.0.0.1:${server.address().port}`;
  const response = await fetch(url + '/api/state');
  assert.equal(response.status, 200);
  assert.ok(!(await response.text()).includes('b'.repeat(32)));
  assert.equal((await fetch(url + '/api/state', { headers: { Origin: 'https://foreign.invalid' } })).status, 403);
  // Node fetch replaces Host; use the HTTP transport to actually send this header.
  const badHostStatus = await new Promise((resolve, reject) => {
    http.get(url + '/api/state', { headers: { Host: 'foreign.invalid' } }, res => {
      res.resume(); resolve(res.statusCode);
    }).on('error', reject);
  });
  assert.equal(badHostStatus, 403);
  assert.equal((await fetch(url + '/api/state', { method: 'POST' })).status, 405);
  assert.equal((await fetch(url + '/api/frame')).status, 503);
  assert.equal(monitor.running, false);
});
