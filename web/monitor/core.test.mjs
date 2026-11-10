import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHmac } from 'node:crypto';
import { AudioDecoder, DeviceClient, Monitor, validateBinding, deviceRequest, hearingEvent } from './core.mjs';

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

test('hearing events join the combined log once, in device time, and gaps in the cursor are reported', () => {
  const monitor = new Monitor({}); monitor.acceptInfo({ boot_id: 'a' });
  const hearing = { events: [
    { seq: 1, age_ms: 4000, kind: 'link', label: 'connected', detail: '' },
    { seq: 2, age_ms: 3000, kind: 'sound', label: '键盘打字', detail: 'typing', p: 0.42 },
    { seq: 3, age_ms: 1000, kind: 'speech', label: '你好', detail: 'happy/known/主人', p: 0.57 } ] };
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 1, hearing }, 100_000);
  monitor.acceptTelemetry({ boot_id: 'a', seq: 2, timestamp_us: 2, hearing }, 100_500); // same events again
  const logged = monitor.events.filter(e => e.kind === 'hearing' || e.kind === 'sound');
  assert.equal(logged.length, 3); assert.equal(monitor.hearingSeq, 3);
  assert.deepEqual(logged.map(e => e.at), [96_000, 97_000, 99_000]);
  assert.match(logged[1].text, /键盘打字（typing） 0.42/); assert.equal(logged[1].kind, 'sound');
  assert.match(logged[2].text, /听到「你好」.*情绪 happy.*身份 主人.*人声 0.57/);
  monitor.acceptTelemetry({ boot_id: 'a', seq: 3, timestamp_us: 3, hearing: { events: [
    { seq: 7, age_ms: 0, kind: 'link', label: 'heartbeat_timeout', detail: 'timeout' } ] } }, 101_000);
  assert.ok(monitor.events.some(e => /缺 3 条/.test(e.text)));
  assert.match(monitor.events.at(-1).text, /听觉断开：心跳超时.*timeout/);
  monitor.acceptInfo({ boot_id: 'b' }); assert.equal(monitor.hearingSeq, 0);
  assert.equal(hearingEvent({ kind: 'gap', detail: '4' }).text, '听觉丢包 4 个（服务端发现序号缺口）');
  assert.equal(hearingEvent({ kind: 'other' }), null);
});

test('the telemetry poll asks for hearing events and no IMU batch, and never opens the raw audio stream', () => {
  const source = fs.readFileSync(new URL('./core.mjs', import.meta.url), 'utf8');
  const poll = source.slice(source.indexOf('async loop('));
  assert.match(poll, /hearing_after=\$\{this\.hearingSeq\}/);
  assert.doesNotMatch(poll, /imu_after/); assert.doesNotMatch(poll, /ensureAudio\(/);
});

test('consecutive hearing gaps within 2 s collapse into one running line', () => {
  const monitor = new Monitor({}); monitor.acceptInfo({ boot_id: 'a' });
  const gap = (seq, age, n) => ({ seq, age_ms: age, kind: 'gap', label: '', detail: String(n) });
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 1, hearing: { events: [gap(1, 3000, 7), gap(2, 2500, 8), gap(3, 1800, 4)] } }, 10_000);
  monitor.acceptTelemetry({ boot_id: 'a', seq: 2, timestamp_us: 2, hearing: { events: [gap(4, 0, 5)] } }, 14_000);
  const lines = monitor.events.filter(e => e.kind === 'hearing');
  assert.equal(lines.length, 2);
  assert.equal(lines[0].text, '听觉丢包 共 19 个（3 次，服务端发现序号缺口）');
  assert.equal(lines[1].text, '听觉丢包 5 个（服务端发现序号缺口）');
});

test('emotion responses are logged with what was perceived and which clip played', () => {
  const monitor = new Monitor({});
  monitor.acceptInfo({ boot_id: 'a', voice_library: { clips: [{ id: 6, title: '武林外传', text: '额滴神啊' }] } });
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 1, hearing: { events: [
    { seq: 1, age_ms: 0, kind: 'response', label: '今天真开心', detail: 'happy/6' },
    { seq: 2, age_ms: 0, kind: 'response', label: '烦死了', detail: 'angry/30/ESP_ERR_INVALID_STATE' },
    { seq: 3, age_ms: 0, kind: 'response', label: '嗯', detail: 'sad/35/expired' }] } }, 1000);
  const lines = monitor.events.filter(e => e.kind === 'response').map(e => e.text);
  assert.deepEqual(lines, [
    '感知「今天真开心」· 情绪 开心 → 播放 #6 武林外传：额滴神啊',
    '感知「烦死了」· 情绪 生气 → 选了 #30，未播放（播放器忙或未就绪）',
    '感知「嗯」· 情绪 低落 → 选了 #35，未播放（选中后超过 0.5 s 未能开始）']);
});

test('responses report the wait for quiet and give-ups while people keep talking', () => {
  const monitor = new Monitor({});
  monitor.acceptInfo({ boot_id: 'a', voice_library: { clips: [{ id: 41, title: '常见答题回应', text: '正确，答对了。' }] } });
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 1, hearing: { events: [
    { seq: 1, age_ms: 0, kind: 'response', label: '对吧', detail: 'neutral/41//2300' },
    { seq: 2, age_ms: 0, kind: 'response', label: '然后呢', detail: 'unknown/41/talking/10000' }] } }, 1000);
  assert.deepEqual(monitor.events.filter(e => e.kind === 'response').map(e => e.text), [
    '感知「对吧」· 情绪 平静 → 等安静 2.3 s 后播放 #41 常见答题回应：正确，答对了。',
    '感知「然后呢」· 情绪 未确定 → 选了 #41 常见答题回应：正确，答对了。，未播放（一直有人在说话，放弃，等了 10.0 s）']);
});

test('speech with no words or no voice stays out of the log and is only counted', () => {
  const monitor = new Monitor({}); monitor.acceptInfo({ boot_id: 'a' });
  monitor.acceptTelemetry({ boot_id: 'a', seq: 1, timestamp_us: 1, hearing: { events: [
    { seq: 1, age_ms: 0, kind: 'speech', label: '.', detail: 'unknown/none', p: 0.0 },
    { seq: 2, age_ms: 0, kind: 'speech', label: '。？', detail: 'unknown/none', p: 0.4 },
    { seq: 3, age_ms: 0, kind: 'speech', label: '嗯好', detail: 'neutral/none', p: 0.1 },
    { seq: 4, age_ms: 0, kind: 'speech', label: '今天天气不错', detail: 'happy/none', p: 0.62 },
    { seq: 5, age_ms: 0, kind: 'speech', label: 'OK', detail: 'neutral/none' }] } }, 1000);
  const heard = monitor.events.filter(e => e.kind === 'hearing').map(e => e.text);
  assert.equal(heard.length, 2); assert.match(heard[0], /今天天气不错/); assert.match(heard[1], /「OK」/);
  assert.equal(monitor.snapshot().filteredSpeech, 3);
  monitor.acceptInfo({ boot_id: 'b' }); assert.equal(monitor.snapshot().filteredSpeech, 0);
});
test('tingwu sentences get their own log kind and known speakers are marked',()=>{
  assert.deepEqual(hearingEvent({kind:'tingwu',label:'今天开会',detail:''}),{kind:'tingwu',text:'听悟「今天开会」'});
  assert.match(hearingEvent({kind:'tingwu',label:'你好',detail:'self'}).text,/hello 自己的声音/);
  assert.deepEqual(hearingEvent({kind:'tingwu',label:'',detail:'error: TimeoutError'}),{kind:'tingwu',text:'听悟出错：TimeoutError'});
  assert.equal(hearingEvent({kind:'speech',label:'你好',detail:'neutral/known/me',p:.5}).known,true);
  assert.equal(hearingEvent({kind:'speech',label:'你好',detail:'neutral/unsure',p:.5}).known,false);
});
