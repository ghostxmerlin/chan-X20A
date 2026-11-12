import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { Monitor } from './core.mjs';
import { createMonitorServer } from './server.mjs';

test('a burst of large log snapshots keeps SSE connected and delivers the latest events', async t => {
  const monitor = new Monitor({ binding: { address: 'http://127.0.0.1:8080' } });
  monitor.running = true; // Exercise the real cache and HTTP stream without opening a device.
  for (let i = 0; i < 400; i++) monitor.event('system', 'x'.repeat(240));
  const server = createMonitorServer(monitor);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let response, closed = false, buffer = '', latest;
  const request = http.get(`http://127.0.0.1:${server.address().port}/api/events`, res => {
    response = res; res.setEncoding('utf8');
    res.on('error', () => {}); res.on('close', () => { closed = true; });
    res.on('data', chunk => {
      buffer += chunk; let end;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const packet = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        if (packet.startsWith('data: ')) latest = JSON.parse(packet.slice(6));
      }
    });
  });
  request.on('error', () => { closed = true; });
  t.after(async () => {
    request.destroy(); response?.destroy(); server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    clearTimeout(monitor.idleTimer);
  });
  const until = async condition => {
    const deadline = Date.now() + 2000;
    while (!condition() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
    assert.ok(condition(), 'SSE did not deliver the expected state');
  };
  await until(() => latest);
  const expected = Array.from({length: 30}, (_, i) => `task burst ${i}`);
  for (const line of expected) { monitor.event('system', line); monitor.emit('state', monitor.snapshot()); }
  await until(() => closed || latest.events.at(-1)?.text === expected.at(-1));
  assert.equal(closed, false, 'log burst must not disconnect a healthy reader');
  assert.deepEqual(latest.events.slice(-30).map(e => e.text), expected);
  monitor.event('hearing', 'speech after burst'); monitor.emit('state', monitor.snapshot());
  await until(() => closed || latest.events.at(-1)?.text === 'speech after burst');
  assert.equal(closed, false); assert.equal(latest.events.at(-1).kind, 'hearing');
});

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
