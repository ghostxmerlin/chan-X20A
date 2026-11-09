import { spawn } from 'node:child_process';
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { M12Tracker } from './m12.mjs';
import { M12System } from './m12-system.mjs';

export function validateBinding(value) {
  const url = new URL(value.address);
  if (url.protocol !== 'http:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash ||
      !/^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) || url.hostname.split('.').some(n => Number(n) > 255) ||
      !/^[0-9a-f]{32}$/i.test(value.token || '') || !/^[\w-]{1,128}$/.test(value.device_id || ''))
    throw new Error('绑定配置无效');
  return { address: url.origin, token: value.token, device_id: value.device_id };
}

function launchCurl(url, token, streaming = false, {method = "GET", body, boot} = {}) {
  // -q must be first: never inherit redirects or credential traces from .curlrc.
  const extra = method === 'POST' ? ['--request','POST','--data-binary','@/dev/fd/3'] : [];
  const child = spawn('/usr/bin/curl', ['-q', '--noproxy', '*', '--proto', '=http', '--connect-timeout', '3',
    '--max-time', streaming ? '0' : '5', '--speed-limit', '1', '--speed-time', '5',
    '--silent', '--show-error', '--include', '--no-buffer', '--header', '@-', ...extra, url], { stdio: ['pipe', 'pipe', 'pipe', 'pipe'] });
  // Credentials travel through stdin, never process arguments or the browser.
  child.stdin.on('error', () => {});
  child.stdin.end((token ? `Authorization: Bearer ${token}\n` : '') + (method === 'POST' ? `Content-Type: application/json\nX-Boot-Id: ${boot}\n` : '') + 'Connection: close\n');
  child.stdio[3].on('error', () => {});
  child.stdio[3].end(method === 'POST' ? JSON.stringify(body) : '');
  child.stderr.resume();
  return child;
}

function splitResponse(data) {
  const at = data.indexOf('\r\n\r\n');
  if (at < 0 || at > 16384) throw new Error('设备响应头无效');
  const lines = data.subarray(0, at).toString('latin1').split('\r\n');
  const status = Number(lines.shift()?.match(/^HTTP\/\S+ (\d{3})/)?.[1]);
  if (status < 200 || status >= 300 || !status) throw Object.assign(new Error(`设备 HTTP ${status || '无效响应'}`), {status});
  const headers = {};
  for (const line of lines) { const i = line.indexOf(':'); if (i > 0) headers[line.slice(0, i).toLowerCase()] = line.slice(i + 1).trim(); }
  return { headers, body: data.subarray(at + 4) };
}

export function deviceRequest(url, { token, maxBytes = 524288, signal, method = "GET", body, boot } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error('监控已暂停')); return; }
    if (!['GET','POST'].includes(method) || (method === 'POST' && !/^[\w-]{1,79}$/.test(boot || ''))) { reject(new Error('写入请求无效')); return; }
    const child = launchCurl(url, token, false, {method,body,boot}); const chunks = []; let size = 0, error;
    const abort = () => { error = new Error('监控已暂停'); child.kill(); };
    signal?.addEventListener('abort', abort, { once: true });
    child.stdout.on('data', data => {
      size += data.length;
      if (size > maxBytes + 16384) { error = new Error('设备响应过大'); child.kill(); }
      else chunks.push(data);
    });
    child.on('error', () => { error = new Error('无法启动系统 curl'); });
    child.on('close', code => {
      signal?.removeEventListener('abort', abort);
      if (error || code !== 0) { reject(error || new Error(`设备连接失败 (curl ${code})`)); return; }
      try { resolve(splitResponse(Buffer.concat(chunks))); } catch (err) { reject(err); }
    });
  });
}

export class DeviceClient {
  constructor(binding) { this.binding = validateBinding(binding); this.verified = false; this.requestTail = Promise.resolve(); }
  async connect(signal) {
    this.verified = false;
    const nonce = randomBytes(16).toString('hex');
    const { address, token, device_id } = this.binding;
    const response = await deviceRequest(`${address}/v1/discovery?nonce=${nonce}`, { maxBytes: 2048, signal });
    const proof = JSON.parse(response.body);
    const expected = createHmac('sha256', token).update(`chan-discovery-v1\n${device_id}\n${nonce}`).digest();
    if (proof.protocol !== 1 || proof.device_id !== device_id || proof.nonce !== nonce ||
        !/^[0-9a-f]{64}$/i.test(proof.proof || '') || !timingSafeEqual(expected, Buffer.from(proof.proof, 'hex')))
      throw new Error('机器人身份校验未通过');
    this.verified = true;
    try {
      const info = await this.json('/v1/info', signal);
      if (info.device_id !== device_id || typeof info.boot_id !== 'string' || !info.boot_id ||
          !Number.isInteger(info.audio_port) || info.audio_port < 1 || info.audio_port > 65535)
        throw new Error('机器人声明无效');
      this.info = info; return info;
    } catch (err) { this.verified = false; throw err; }
  }
  async request(path, signal) {
    return this.serialRequest(() => {
    if (!this.verified || !path.startsWith('/v1/')) throw new Error('等待验证机器人');
    return deviceRequest(this.binding.address + path, { token: this.binding.token, signal });
    });
  }
  serialRequest(work) {
    const next = this.requestTail.then(work); this.requestTail = next.catch(() => {}); return next;
  }
  async control(path, body, boot, check) {
    return this.serialRequest(async () => {
      check();
      if (!this.verified || this.info?.boot_id !== boot || !['/v1/interaction','/v1/hearing','/v1/eyes','/v1/voice/play','/v1/m12/view','/v1/m12/mode','/v1/remote','/v1/motion/yaw-step','/v1/settings/pitch','/v1/camera','/v1/servo','/v1/wifi'].includes(path)) throw new Error('控制状态已变化');
      return JSON.parse((await deviceRequest(this.binding.address+path, {token:this.binding.token, method:'POST', body, boot, maxBytes:16384})).body);
    });
  }
  async json(path, signal) { return JSON.parse((await this.request(path, signal)).body); }
  openAudio() {
    if (!this.verified || !this.info) throw new Error('等待验证机器人');
    const url = new URL(this.binding.address); url.port = this.info.audio_port; url.pathname = '/v1/audio';
    return launchCurl(url.href, this.binding.token, true);
  }
}

export class AudioDecoder {
  constructor(format = { sample_rate: 24000, channels: 2, bits: 16, frames_per_block: 480 }) {
    this.format = format; this.buffer = Buffer.alloc(0); this.previous = null;
  }
  feed(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]); const result = [];
    if (this.buffer.length > 2097152) throw new Error('音频缓冲超过上限');
    while (this.buffer.length >= 32) {
      const h = this.buffer;
      const rate = h.readUInt32LE(16), frames = h.readUInt32LE(20), channels = h.readUInt16LE(24);
      const bits = h.readUInt16LE(26), bytes = h.readUInt32LE(28), stamp = h.readBigUInt64LE(8);
      if (h.toString('ascii', 0, 4) !== 'SCA1' || rate !== this.format.sample_rate || channels !== this.format.channels ||
          bits !== 16 || channels < 1 || channels > 8 || frames !== this.format.frames_per_block || frames < 1 || frames > rate ||
          bytes > 1048576 || bytes !== frames * channels * 2 || stamp > BigInt(Number.MAX_SAFE_INTEGER))
        throw new Error('音频格式或边界无效');
      if (this.buffer.length < 32 + bytes) break;
      const packet = Buffer.from(this.buffer.subarray(0, 32 + bytes)); this.buffer = this.buffer.subarray(32 + bytes);
      const seq = packet.readUInt32LE(4), delta = this.previous === null ? 1 : (seq - this.previous) >>> 0;
      if (delta === 0) continue;
      if (delta >= 0x80000000) throw new Error('音频序号倒退');
      this.previous = seq;
      let sum = 0, min = 1, max = -1;
      for (let i = 0; i < frames; i++) {
        const value = packet.readInt16LE(32 + i * channels * 2) / 32768;
        sum += value * value; min = Math.min(min, value); max = Math.max(max, value);
      }
      result.push({ seq, t: Number(stamp) / 1e6, min, max,
        db: Math.max(-96, 20 * Math.log10(Math.max(1e-9, Math.sqrt(sum / frames)))), gap: delta - 1, packet });
    }
    return result;
  }
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export class Monitor extends EventEmitter {
  constructor(client, m12System = new M12System(null)) {
    super(); this.m12System = m12System; this.client = client; this.info = null; this.telemetry = null; this.telemetryAt = 0;
    this.frame = null; this.motion = []; this.audio = { at: 0, db: -96, gaps: 0, history: [] };
    this.events = []; this.eventSeq = 0; this.asrSeq = 0; this.online = false; this.running = false;
    this.m12Tracker = new M12Tracker(); this.thermalHistory = [];
    this.viewers = 0; this.epoch = 0; this.error = ''; this.audioError = ''; this.cameraError = '';
  }
  event(kind, text, at = Date.now()) {
    this.events.push({ id: ++this.eventSeq, at, kind, text: String(text).slice(0, 240) });
    if (this.events.length > 120) this.events.shift();
  }
  clearStreams() {
    this.m12Tracker.clear(); this.thermalHistory = [];
    this.frame = null; this.motion = []; this.audio = { at: 0, db: -96, gaps: 0, history: [] };
    this.audioChild?.kill(); this.audioChild = null; this.audioEpoch = (this.audioEpoch || 0) + 1;
    this.emit('audioReset');
  }
  acceptInfo(info) {
    if (this.info?.boot_id !== info.boot_id) {
      if (this.info) this.event('boot', '机器人重新启动，旧画面和曲线已清空');
      this.clearStreams(); this.telemetry = null; this.telemetryAt = 0; this.asrSeq = 0;
    }
    this.info = info;
  }
  acceptTelemetry(data, now = Date.now()) {
    if (data.boot_id !== this.info?.boot_id || !Number.isSafeInteger(data.seq)) throw new Error('遥测启动标识不匹配');
    if (data.seq === this.telemetry?.seq) return;
    const previous = this.telemetry; this.telemetry = data; this.telemetryAt = now;
    this.m12Tracker.accept(data);
    const thermal = data.temperature;
    if (thermal?.valid && Number.isFinite(thermal.celsius) && thermal.timestamp_us > (this.thermalHistory.at(-1)?.timestamp_us || 0)) {
      this.thermalHistory.push({ timestamp_us: thermal.timestamp_us, celsius: thermal.celsius });
      this.thermalHistory = this.thermalHistory.filter(p => thermal.timestamp_us - p.timestamp_us <= 120000000).slice(-121);
    }
    const samples = data.imu_samples || [{ seq: data.seq, timestamp_us: data.imu?.timestamp_us || data.timestamp_us, ...data.imu }];
    for (const s of samples) {
      if (s.seq <= (this.motion.at(-1)?.seq ?? -1)) continue;
      this.motion.push({ seq: s.seq, t: s.timestamp_us / 1e6, accel: s.valid ? s.accel : null, gyro: s.valid ? s.gyro : null });
    }
    const newest = this.motion.at(-1)?.t || 0;
    this.motion = this.motion.filter(s => s.t >= newest - 4).slice(-240);
    for (const event of data.asr?.events || []) if (event.seq > this.asrSeq) {
      const ageMs = Math.max(0, (data.timestamp_us - event.timestamp_us) / 1000);
      const delay = Number.isFinite(ageMs) ? ageMs : 0;
      this.asrSeq = event.seq;
      this.event('asr', `ASR${delay > 2000 ? '（历史）' : ''} ${event.word || ('指令 ' + event.id)}`, now - delay);
    }
    if (data.m12?.function !== previous?.m12?.function || data.m12?.running !== previous?.m12?.running)
      this.event('m12', `M12 请求 ${data.m12?.function || '—'} · 设备报告 ${data.m12?.running || '尚无回应'}`);
    const state = data.remote?.motor?.state;
    if (state !== previous?.remote?.motor?.state && state) this.event('motor', `舵机状态 ${state}`);
  }
  snapshot(now = Date.now()) {
    const fresh = { telemetry: !!this.telemetryAt && now - this.telemetryAt < 1500,
      camera: !!this.frame?.at && now - this.frame.at < 1500, audio: !!this.audio.at && now - this.audio.at < 1000 };
    return { online: this.online, active: this.running, error: this.error, viewers: this.viewers,
      capabilities: this.info?.capabilities || {}, firmware: this.info?.firmware || '', boot: this.info?.boot_id || '', address: this.client.binding?.address || '',
      receivedAt: this.telemetryAt, fresh, telemetry: this.telemetry,
      voiceLibrary: this.info?.voice_library || {clips:[]},
      eyesCatalog: this.info?.eyes || {styles:[],states:[]},
      frame: this.frame ? { seq: this.frame.seq, at: this.frame.at, bytes: this.frame.bytes.length } : null,
      audio: { ...this.audio, error: this.audioError }, cameraError: this.cameraError,
      m12: { ...this.m12Tracker.snapshot(this.telemetry, this.online && fresh.telemetry),
        system: this.m12System.snapshot(now), temperature: this.m12System.snapshot(now).temperature }, thermalHistory: this.thermalHistory,
      motion: this.motion, events: this.events, now };
  }
  addViewer() {
    this.viewers++; clearTimeout(this.idleTimer);
    if (!this.running) this.start();
  }
  removeViewer() {
    this.viewers = Math.max(0, this.viewers - 1);
    if (!this.viewers) this.idleTimer = setTimeout(() => { if (!this.viewers) this.stop(); }, 1500);
  }
  start() {
    if (this.running) return;
    this.running = true; const epoch = ++this.epoch; this.controller = new AbortController();
    this.m12System.start();
    this.event('connection', '开始监控'); void this.loop(epoch, this.controller.signal);
  }
  stop() {
    this.running = false; this.online = false; this.epoch++; this.controller?.abort(); this.m12System.stop();
    this.client.verified = false; this.clearStreams(); this.event('connection', '监控已暂停，设备采集连接已释放');
    this.emit('state', this.snapshot());
  }
  ensureAudio(epoch) {
    if (this.audioChild || !this.online || !this.running || Date.now() < (this.nextAudio || 0)) return;
    const child = this.client.openAudio(); this.audioChild = child;
    const audioEpoch = this.audioEpoch; const parser = new AudioDecoder(this.info.audio);
    let header = Buffer.alloc(0), ready = false, failure = '';
    const fail = message => { failure = message; child.kill(); };
    child.stdout.on('data', chunk => {
      if (this.epoch !== epoch || this.audioEpoch !== audioEpoch) return;
      try {
        if (!ready) {
          header = Buffer.concat([header, chunk]); const at = header.indexOf('\r\n\r\n');
          if (at < 0) { if (header.length > 16384) fail('音频响应头过大'); return; }
          const response = splitResponse(header);
          if (response.headers['x-boot-id'] !== this.info.boot_id) throw new Error('音频启动标识不匹配');
          chunk = response.body; ready = true; header = null;
        }
        for (const block of parser.feed(chunk)) {
          this.audio.at = Date.now(); this.audio.db = block.db; this.audio.gaps += block.gap;
          const { packet, ...point } = block; this.audio.history.push(point);
          this.audio.history = this.audio.history.filter(p => p.t >= block.t - 4).slice(-240);
          this.audioError = ''; this.emit('pcm', packet);
        }
      } catch (err) { fail(err.message); }
    });
    child.on('error', () => { failure = '无法启动音频连接'; });
    child.on('close', () => {
      if (this.audioChild !== child) return;
      this.audioChild = null; this.nextAudio = Date.now() + 1500; this.audio.at = 0;
      this.emit('audioReset');
      if (this.running && epoch === this.epoch) this.audioError = failure || '音频连接中断，正在重连';
    });
  }
  async loop(epoch, signal) {
    let lastInfo = 0;
    while (this.running && epoch === this.epoch) {
      const began = Date.now();
      try {
        if (!this.client.verified || !this.online || began - lastInfo > 30000) {
          const info = await this.client.connect(signal);
          if (epoch !== this.epoch || signal.aborted) break;
          this.acceptInfo(info); lastInfo = Date.now();
        }
        const query = `/v1/telemetry?imu_after=${this.telemetry?.seq || 0}&remote_after=${this.telemetry?.remote?.rx_packets || 0}`;
        const data = await this.client.json(query, signal);
        if (epoch !== this.epoch || signal.aborted) break;
        if (data.boot_id !== this.info.boot_id) { this.client.verified = false; continue; }
        this.acceptTelemetry(data);
        if (!this.online) this.event('connection', '已验证机器人，实时数据已连接');
        this.online = true; this.error = '';
        // A single sequential main-port poll leaves room for Android's two requests.
        // The body camera is off by default; do not poll frames the robot will refuse.
        if (this.telemetry?.health?.camera_enabled === false) {
          if (this.frame) this.frame = null;
          this.cameraError = '机身相机已关闭（默认）；需要时在相机面板开启';
        } else try {
          const image = await this.client.request('/v1/frame', signal);
          if (epoch !== this.epoch || signal.aborted) break;
          const seq = Number(image.headers['x-seq']);
          if (image.headers['x-boot-id'] === this.info.boot_id && Number.isSafeInteger(seq) &&
              image.body.length > 4 && image.body[0] === 255 && image.body[1] === 216) {
            if (this.frame?.seq !== seq) this.frame = { seq, at: Date.now(), bytes: image.body };
            this.cameraError = '';
          } else this.cameraError = '画面信息不匹配';
        } catch (err) {
          if (epoch !== this.epoch || signal.aborted) break;
          this.cameraError = err.message;
        }
        this.ensureAudio(epoch);
      } catch (err) {
        if (epoch !== this.epoch) break;
        if (this.online || this.error !== err.message) this.event('connection', err.message);
        this.online = false; this.error = err.message; this.client.verified = false; this.clearStreams();
      }
      if (epoch !== this.epoch) break;
      this.emit('state', this.snapshot());
      await sleep(this.online ? Math.max(20, 250 - (Date.now() - began)) : 1500);
    }
  }
}
