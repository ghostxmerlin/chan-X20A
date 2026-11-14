import http from 'node:http';
import {StringDecoder} from 'node:string_decoder';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DeviceClient, Monitor } from './core.mjs';
import {SerialDeviceClient} from './serial-client.mjs';
import { Controls } from './control.mjs';
import { existingHearingConfig } from './hearing.mjs';
import { M12System } from './m12-system.mjs';
import { mindFromEnv } from './mind.mjs';
import { parseEnv } from 'node:util';

const directory = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(directory, '../..');
export function createMonitorServer(monitor, controls = new Controls(monitor,{loadHearingConfig:()=>existingHearingConfig(path.join(root,'.env'))}), mind = null) {
  const assets = new Map([['/speech-settings.js', ['speech-settings.js','text/javascript; charset=utf-8']], ['/', ['index.html', 'text/html; charset=utf-8']],
    ['/hearing-ui.js', ['hearing-ui.js','text/javascript; charset=utf-8']], ['/servo-tuning-ui.js', ['servo-tuning-ui.js','text/javascript; charset=utf-8']], ['/wifi-ui.js', ['wifi-ui.js','text/javascript; charset=utf-8']], ['/eyes-ui.js', ['eyes-ui.js', 'text/javascript; charset=utf-8']], ['/control-ui.js', ['control-ui.js', 'text/javascript; charset=utf-8']], ['/voice-ui.js', ['voice-ui.js', 'text/javascript; charset=utf-8']], ['/app.js', ['app.js', 'text/javascript; charset=utf-8']], ['/m12-ui.js', ['m12-ui.js', 'text/javascript; charset=utf-8']], ['/style.css', ['style.css', 'text/css; charset=utf-8']],
    ['/mind.html', ['mind.html', 'text/html; charset=utf-8']], ['/mind-ui.js', ['mind-ui.js', 'text/javascript; charset=utf-8']], ['/mind.css', ['mind.css', 'text/css; charset=utf-8']],
    ['/db.html', ['db.html', 'text/html; charset=utf-8']], ['/db-ui.js', ['db-ui.js', 'text/javascript; charset=utf-8']], ['/db.css', ['db.css', 'text/css; charset=utf-8']],
    ['/switches-ui.js', ['switches-ui.js', 'text/javascript; charset=utf-8']], ['/say-ui.js', ['say-ui.js', 'text/javascript; charset=utf-8']], ['/scene-ui.js', ['scene-ui.js', 'text/javascript; charset=utf-8']], ['/switches.css', ['switches.css', 'text/css; charset=utf-8']],
    ['/mode-ui.js', ['mode-ui.js', 'text/javascript; charset=utf-8']], ['/mode.css', ['mode.css', 'text/css; charset=utf-8']]]);
  const server = http.createServer((req, res) => {
    const port = server.address().port;
    const allowedHosts = [`127.0.0.1:${port}`, `localhost:${port}`];
    const origin = req.headers.origin;
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    const respond = (code, message) => { res.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end(message); };
    if (!allowedHosts.includes(req.headers.host) || (origin && !allowedHosts.some(host => origin === 'http://' + host)) ||
        req.headers['sec-fetch-site'] === 'cross-site') { respond(403, '仅允许本机观察台访问'); return; }
    const url = new URL(req.url, 'http://' + req.headers.host);
    if (req.method === 'POST' && url.pathname === '/api/control') {
      if (origin !== 'http://' + req.headers.host) { respond(403,'控制操作必须来自当前观察页面'); return; }
      if (req.headers['content-type'] !== 'application/json') { respond(415,'控制只接受 JSON'); return; }
      let body='', size=0, ended=false;
      const timer=setTimeout(()=>{ if (!ended) {ended=true; respond(408,'控制请求超时'); req.destroy();} },3000);
      req.on('close',()=>clearTimeout(timer));
      req.on('data',chunk=>{
        size+=chunk.length;
        if(size>1024) {ended=true;clearTimeout(timer);respond(413,'控制参数过长');req.destroy();} else body+=chunk;
      });
      req.on('end',async()=>{
        clearTimeout(timer);if(ended)return;ended=true;
        let input;try { input=JSON.parse(body); } catch {respond(400,'无效 JSON');return;}
        try {const result=await controls.execute(input);res.writeHead(202,{'Content-Type':'application/json'});res.end(JSON.stringify(result));}
        catch(error) {respond(error.status || 502,error.message);}
      });
      return;
    }
    if (req.method === 'POST' && url.pathname.startsWith('/api/mind/')) {
      const sub = url.pathname.slice('/api/mind'.length);
      if (!['/settings', '/mark', '/label', '/rerun', '/place', '/switch', '/switches', '/say', '/say/stop', '/voice/try', '/speech/settings', '/memory/edit', '/role/try', '/say/round', '/say/play', '/signal', '/say/prefetch', '/say/auto', '/db/person', '/db/person/note', '/db/memory', '/db/knowledge', '/mode', '/preset'].includes(sub)) { respond(404, '未找到页面'); return; }
      if (origin !== 'http://' + req.headers.host) { respond(403, '写入必须来自当前观察页面'); return; }
      if ((req.headers['content-type'] || '').split(';')[0].trim() !== 'application/json') { respond(415, '只接受 JSON'); return; }
      if (!mind) { respond(503, '情景服务未配置'); return; }
      const maxBody=['/settings','/speech/settings'].includes(sub)?512*1024:16384;
      const decoder=new StringDecoder('utf8');
      let body = '', size = 0, ended = false;
      const timer = setTimeout(() => { if (!ended) { ended = true; respond(408, '请求超时'); req.destroy(); } }, 3000);
      req.on('close', () => clearTimeout(timer));
      req.on('data', chunk => { size += chunk.length; if (size > maxBody) { ended = true; clearTimeout(timer); respond(413, '内容过长'); req.destroy(); } else body += decoder.write(chunk); });
      req.on('end', () => {
        clearTimeout(timer); if (ended) return; ended = true;
        body+=decoder.end();
        mind.post(sub, body).then(r => {
          res.writeHead(r.status, { 'Content-Type': r.status < 300 ? 'application/json; charset=utf-8' : 'text/plain; charset=utf-8' });
          res.end(r.body);
        }, () => respond(502, '情景服务出错'));
      });
      return;
    }
    if (req.method !== 'GET') { respond(405, '此路径不接受写入'); return; }
    if (url.pathname === '/health') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ service: 'stackchan-monitor', pid: process.pid })); return; }
    if (assets.has(url.pathname)) {
      const [filename, type] = assets.get(url.pathname);
      try { res.setHeader('Content-Type', type); res.end(fs.readFileSync(path.join(directory, 'public', filename))); }
      catch { respond(503, '页面文件尚未准备好'); }
      return;
    }
    if (url.pathname.startsWith('/api/mind/')) {
      const sub = url.pathname.slice('/api/mind'.length);
      if (!['/state', '/timeline', '/minute', '/now', '/segments', '/scene', '/settings', '/names', '/prompts', '/labels', '/stats', '/switches', '/frames/latest', '/eye/latest', '/eye', '/notes', '/roles', '/utterances', '/voices', '/speech/settings', '/memories', '/memory/history', '/say/explain', '/say/rounds', '/say/stats', '/scenes', '/signals', '/db/overview', '/db/people', '/db/person', '/db/memories', '/db/knowledge', '/db/item', '/mode', '/mode/seq'].includes(sub)) { respond(404, '未找到页面'); return; }
      if (!mind) { respond(503, '情景服务未配置'); return; }
      mind.get(sub + url.search).then(r => {
        res.writeHead(r.status, { 'Content-Type': r.status === 200 ? 'application/json; charset=utf-8' : 'text/plain; charset=utf-8' });
        res.end(r.body);
      }, () => respond(502, '情景服务出错'));
      return;
    }
    if (url.pathname === '/api/state') {
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(monitor.snapshot())); return;
    }
    if (url.pathname === '/api/frame') {
      if (!monitor.frame || !monitor.snapshot().fresh.camera) { respond(503, '画面尚未收到或已过期'); return; }
      res.writeHead(200, { 'Content-Type': 'image/jpeg', 'X-Frame-Seq': String(monitor.frame.seq) });
      res.end(monitor.frame.bytes); return;
    }
    if (url.pathname === '/api/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', Connection: 'keep-alive' }); res.flushHeaders();
      let waitingForDrain = false, pending = false;
      const send = () => {
        if (res.destroyed || res.writableEnded) return;
        // USB can deliver many log records in one read. Respect backpressure
        // and merge their notifications into the next current snapshot, whose
        // event cache still contains every retained log entry.
        if (waitingForDrain) { pending = true; return; }
        pending = false;
        waitingForDrain = !res.write(`data: ${JSON.stringify(monitor.snapshot())}\n\n`);
      };
      const drain = () => { waitingForDrain = false; if (pending) send(); };
      res.on('drain', drain);
      const viewer = monitor.addViewer({ camera: url.searchParams.get('camera') === '1', servo_diagnostics: url.searchParams.get('servo') === '1' });
      monitor.on('state', send); send();
      const heartbeat = setInterval(send, 1000);
      res.on('close', () => { clearInterval(heartbeat); res.off('drain', drain); monitor.off('state', send); monitor.removeViewer(viewer); });
      return;
    }
    // /api/listen (browser playback of the raw mic stream) was removed with the stream itself.
    respond(404, '未找到页面');
  });
  server.requestTimeout = 10000; server.headersTimeout = 10000;
  return server;
}

// Dev-only local demo (docs/mode-demo.md): SOUL_MIND_LOCAL set = a second monitor on its own port (SOUL_MONITOR_PORT,
// default 8807) pointed at scripts/mind-local-demo.py. It reads neither .private/stackchan-monitor.json nor .env, and
// it never opens the robot's USB port or Wi-Fi link (the running monitor keeps them). Unset: null, today's startup.
class NoRobotClient {
  constructor() { this.binding = { address: '' }; this.verified = false; }
  async connect() { throw new Error('本机演示：没有连机器人（只演示模式切换）'); }
  async json() { throw new Error('本机演示：没有连机器人'); }
  async request() { throw new Error('本机演示：没有连机器人'); }
}
export function localDemo(env) {
  if (!env.SOUL_MIND_LOCAL) return null;
  const mind = mindFromEnv(env, {});
  const port = env.SOUL_MONITOR_PORT === undefined ? 8807 : Number(env.SOUL_MONITOR_PORT);
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || port === 8787) throw new Error('SOUL_MONITOR_PORT 端口无效（1024–65535，不能用正式观察台的 8787）');
  const monitor = new Monitor(new NoRobotClient(), new M12System(null));
  const controls = new Controls(monitor, { loadHearingConfig: () => { throw new Error('本机演示不读 .env'); } });
  return { port, monitor, mind, controls };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.umask(0o077);
  const demo = localDemo(process.env);
  let monitor, mind, controls, port;
  if (demo) ({ monitor, mind, controls, port } = demo);
  else {
    const filename = path.join(root, '.private/stackchan-monitor.json');
    if (!fs.existsSync(filename)) { console.error('缺少本机绑定配置，见 web/monitor/README.md'); process.exit(1); }
    const config = JSON.parse(fs.readFileSync(filename));
    port = config.port ?? 8787;
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('本机端口无效');
    monitor = new Monitor(config.transport==='http'?new DeviceClient(config):new SerialDeviceClient(config), new M12System(config.m12, {root}));
    let env = {};
    try { env = parseEnv(fs.readFileSync(path.join(root, '.env'), 'utf8')); } catch {}
    mind = mindFromEnv({}, env);
  }
  const server = createMonitorServer(monitor, controls, mind);
  server.on('error', error => { console.error('本机观察台启动失败：' + error.code); process.exit(1); });
  server.listen(port, '127.0.0.1', () => console.log(demo ? `本机模式演示观察台（没有机器人）：http://127.0.0.1:${port}/` : `StackChan 观察台：http://127.0.0.1:${port}/`));
  const close = async () => { monitor.stop(); mind.stop(); server.closeAllConnections(); await monitor.client.close?.(); server.close(() => process.exit(0)); };
  process.on('SIGTERM', close); process.on('SIGINT', close);
}
