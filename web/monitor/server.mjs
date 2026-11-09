import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DeviceClient, Monitor } from './core.mjs';
import { Controls } from './control.mjs';
import { existingHearingConfig } from './hearing.mjs';
import { M12System } from './m12-system.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(directory, '../..');
export function createMonitorServer(monitor, controls = new Controls(monitor,{loadHearingConfig:()=>existingHearingConfig(path.join(root,'.env'))})) {
  const assets = new Map([['/', ['index.html', 'text/html; charset=utf-8']],
    ['/hearing-ui.js', ['hearing-ui.js','text/javascript; charset=utf-8']], ['/servo-tuning-ui.js', ['servo-tuning-ui.js','text/javascript; charset=utf-8']], ['/wifi-ui.js', ['wifi-ui.js','text/javascript; charset=utf-8']], ['/eyes-ui.js', ['eyes-ui.js', 'text/javascript; charset=utf-8']], ['/control-ui.js', ['control-ui.js', 'text/javascript; charset=utf-8']], ['/voice-ui.js', ['voice-ui.js', 'text/javascript; charset=utf-8']], ['/app.js', ['app.js', 'text/javascript; charset=utf-8']], ['/m12-ui.js', ['m12-ui.js', 'text/javascript; charset=utf-8']], ['/style.css', ['style.css', 'text/css; charset=utf-8']]]);
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
    if (req.method !== 'GET') { respond(405, '此路径不接受写入'); return; }
    if (url.pathname === '/health') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ service: 'stackchan-monitor', pid: process.pid })); return; }
    if (assets.has(url.pathname)) {
      const [filename, type] = assets.get(url.pathname);
      try { res.setHeader('Content-Type', type); res.end(fs.readFileSync(path.join(directory, 'public', filename))); }
      catch { respond(503, '页面文件尚未准备好'); }
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
      const send = state => {
        if (res.writableLength > 262144) { res.destroy(); return; }
        res.write(`data: ${JSON.stringify(state)}\n\n`);
      };
      monitor.addViewer(); monitor.on('state', send); send(monitor.snapshot());
      const heartbeat = setInterval(() => send(monitor.snapshot()), 1000);
      res.on('close', () => { clearInterval(heartbeat); monitor.off('state', send); monitor.removeViewer(); });
      return;
    }
    // /api/listen (browser playback of the raw mic stream) was removed with the stream itself.
    respond(404, '未找到页面');
  });
  server.requestTimeout = 10000; server.headersTimeout = 10000;
  return server;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.umask(0o077);
  const filename = path.join(root, '.private/stackchan-monitor.json');
  if (!fs.existsSync(filename)) { console.error('缺少本机绑定配置，见 web/monitor/README.md'); process.exit(1); }
  const config = JSON.parse(fs.readFileSync(filename));
  const port = config.port ?? 8787;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('本机端口无效');
  const monitor = new Monitor(new DeviceClient(config), new M12System(config.m12, {root}));
  const server = createMonitorServer(monitor);
  server.on('error', error => { console.error('本机观察台启动失败：' + error.code); process.exit(1); });
  server.listen(port, '127.0.0.1', () => console.log(`StackChan 观察台：http://127.0.0.1:${port}/`));
  const close = () => { monitor.stop(); server.closeAllConnections(); server.close(() => process.exit(0)); };
  process.on('SIGTERM', close); process.on('SIGINT', close);
}
