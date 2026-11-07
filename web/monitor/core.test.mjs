import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHmac } from 'node:crypto';
import { AudioDecoder, DeviceClient, Monitor, validateBinding, deviceRequest } from './core.mjs';

function audio(seq, mic = 16384, reference = 32767) {
  const b = Buffer.alloc(32 + 480 * 4);
  b.write('SCA1'); b.writeUInt32LE(seq, 4); b.writeBigUInt64LE(1_000_000n, 8);
  b.writeUInt32LE(24000, 16); b.writeUInt32LE(480, 20);
  b.writeUInt16LE(2, 24); b.writeUInt16LE(16, 26); b.writeUInt32LE(1920, 28);
  for (let i = 0; i < 480; i++) { b.writeInt16LE(mic, 32 + i * 4); b.writeInt16LE(reference, 34 + i * 4); }
  return b;
}

test('fragmented PCM uses only mic channel; unsigned wrap is continuous and gaps are counted', () => {
  const parser = new AudioDecoder();
  const first = audio(0xffffffff);
  assert.deepEqual(parser.feed(first.subarray(0, 7)), []);
  const blocks = parser.feed(Buffer.concat([first.subarray(7), audio(0, 0), audio(3, -16384)]));
  assert.equal(blocks.length, 3);
  assert.ok(Math.abs(blocks[0].db + 6.0206) < .001);
  assert.equal(blocks[0].min, .5); assert.equal(blocks[0].max, .5);
  assert.equal(blocks[1].db, -96); assert.equal(blocks[1].gap, 0);
  assert.equal(blocks[2].gap, 2); assert.equal(blocks[2].min, -.5);
  assert.equal(blocks[0].packet.length, 1952);
});

test('invalid framing and incompatible stream format fail before payload allocation', () => {
  for (const mutate of [b => b.write('oops'), b => b.writeUInt32LE(0xffffffff, 28),
    b => b.writeUInt16LE(0, 24), b => b.writeUInt16LE(8, 26), b => b.writeUInt32LE(16000, 16)]) {
    const b = audio(1); mutate(b); assert.throws(() => new AudioDecoder().feed(b.subarray(0, 32)));
  }
});

const token = 'a'.repeat(32), deviceId = 'synthetic-device';
async function fakeDevice(t, mode = 'good') {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ path: req.url, authorization: req.headers.authorization });
    if (req.url.startsWith('/v1/discovery?')) {
      if (mode === 'redirect') { res.writeHead(302, { Location: '/v1/info' }); res.end(); return; }
      const nonce = new URL(req.url, 'http://local').searchParams.get('nonce');
      const proof = createHmac('sha256', token).update(`chan-discovery-v1\n${deviceId}\n${nonce}`).digest('hex');
      res.end(JSON.stringify({ protocol: 1, device_id: deviceId, nonce, proof: mode === 'bad' ? '0'.repeat(64) : proof }));
    } else {
      res.end(JSON.stringify({ firmware: 'synthetic', boot_id: 'boot-a', device_id: deviceId,
        audio_port: server.address().port, audio: { sample_rate: 24000, frames_per_block: 480, channels: 2, bits: 16 } }));
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { seen, binding: { address: `http://127.0.0.1:${server.address().port}`, token, device_id: deviceId } };
}

test('real HTTP transport challenges without credential, then fetches authenticated info', async t => {
  const fake = await fakeDevice(t);
  const client = new DeviceClient(fake.binding);
  assert.equal((await client.connect()).boot_id, 'boot-a');
  assert.equal(fake.seen[0].authorization, undefined);
  assert.equal(fake.seen[1].authorization, `Bearer ${token}`);
});

for (const mode of ['bad', 'redirect']) test(`${mode} identity response never receives a credential`, async t => {
  const fake = await fakeDevice(t, mode);
  await assert.rejects(new DeviceClient(fake.binding).connect());
  assert.equal(fake.seen.length, 1);
  assert.ok(fake.seen.every(req => !req.authorization));
});

test('binding rejects URLs that could redirect credentials or invoke other protocols', () => {
  for (const address of ['https://127.0.0.1:8080', 'http://user@127.0.0.1:8080', 'http://127.0.0.1:8080/path', 'file:///tmp/a'])
    assert.throws(() => validateBinding({ address, device_id: deviceId, token }));
});

test('new boot clears cached camera/audio/curves; duplicate telemetry cannot keep readings fresh', () => {
  const monitor = new Monitor({});
  monitor.acceptInfo({ boot_id: 'a', firmware: 'synthetic' });
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 100, imu: { valid: true, accel: [1,2,3], gyro: [4,5,6] } }, 1000);
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 100 }, 2000);
  assert.equal(monitor.snapshot(2600).fresh.telemetry, false);
  monitor.frame = { seq: 5, at: 1000, bytes: Buffer.from('old') };
  monitor.audio.at = 1000;
  monitor.acceptInfo({ boot_id: 'b', firmware: 'synthetic' });
  assert.equal(monitor.frame, null); assert.equal(monitor.audio.at, 0);
  assert.deepEqual(monitor.motion, []); assert.equal(monitor.telemetry, null);
});

test('ASR history uses device event age rather than pretending it just occurred', () => {
  const monitor = new Monitor({}); monitor.acceptInfo({ boot_id: 'a' });
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 20_000_000,
    asr: { events: [{ seq: 1, timestamp_us: 5_000_000, word: 'turn on' }] } }, 100_000);
  const event = monitor.events.find(e => e.kind === 'asr');
  assert.equal(event.at, 85_000);
  assert.match(event.text, /历史/);
});

test('stopping while a frame is pending cannot resurrect cached video or audio', async () => {
  let releaseFrame, signalFrame;
  const frameRequested = new Promise(resolve => { signalFrame = resolve; });
  const client = { verified: false,
    async connect() { this.verified = true; return { boot_id: 'a' }; },
    async json() { return { boot_id: 'a', seq: 1 }; },
    request() { signalFrame(); return new Promise(resolve => { releaseFrame = resolve; }); },
    openAudio() { assert.fail('stale loop must not open audio'); }
  };
  const monitor = new Monitor(client); monitor.start(); await frameRequested; monitor.stop();
  releaseFrame({ headers: { 'x-boot-id': 'a', 'x-seq': '1' }, body: Buffer.from([255,216,1,2,3]) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(monitor.frame, null); assert.equal(monitor.online, false);
});


test('user curl configuration cannot enable redirects or credential tracing', async t => {
  const fake = await fakeDevice(t, 'redirect');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'stackchan-curl-test-'));
  const trace = path.join(directory, 'trace.txt');
  fs.writeFileSync(path.join(directory, '.curlrc'), `location
trace-ascii = "${trace}"
`);
  const previous = process.env.CURL_HOME; process.env.CURL_HOME = directory;
  try {
    await assert.rejects(deviceRequest(fake.binding.address + '/v1/discovery?nonce=synthetic', { token }), /HTTP 302/);
    assert.equal(fake.seen.length, 1, 'must not follow the redirect');
    assert.equal(fs.existsSync(trace), false, 'must not create a credential trace');
  } finally {
    if (previous === undefined) delete process.env.CURL_HOME; else process.env.CURL_HOME = previous;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
