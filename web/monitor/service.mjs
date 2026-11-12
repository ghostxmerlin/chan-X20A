import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

process.umask(0o077);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const privateDir = path.join(root, '.private');
const pidfile = path.join(privateDir, 'stackchan-monitor.pid');
const server = path.join(root, 'web/monitor/server.mjs');
const configFile = path.join(privateDir, 'stackchan-monitor.json');
const config = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile)) : {};
const port = config.port ?? 8787;
const pid = fs.existsSync(pidfile) ? Number(fs.readFileSync(pidfile, 'utf8')) : 0;
const command = pid > 0 ? spawnSync('/bin/ps', ['-p', String(pid), '-o', 'command='], { encoding: 'utf8' }).stdout : '';
const running = !!command && (command.includes(server) || command.trim().endsWith('node web/monitor/server.mjs'));
const action = process.argv[2] || 'status';
if (!['start','stop','status'].includes(action)) throw new Error('用法：node web/monitor/service.mjs start|stop|status');
if (action === 'status') console.log(running ? `运行中 http://127.0.0.1:${port}/` : '已停止');
if (action === 'stop') {
  if (running) {
    process.kill(pid, 'SIGTERM');
    for (let i = 0; i < 40; i++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      const state = spawnSync('/bin/ps', ['-p', String(pid), '-o', 'stat='], { encoding: 'utf8' }).stdout?.trim();
      if (!state || state.startsWith('Z')) break;
      if (i === 39) throw new Error('服务尚未退出，请检查本机日志');
    }
  }
  fs.rmSync(pidfile, { force: true }); console.log('观察台已停止');
}
if (action === 'start') {
  let expectedPid = pid;
  if (!fs.existsSync(configFile)) throw new Error('请先配置本机 .private/stackchan-monitor.json，见 README');
  if (!running) {
    fs.mkdirSync(privateDir, { recursive: true, mode: 0o700 });
    fs.mkdirSync(path.join(root, 'logs'), { recursive: true });
    const log = fs.openSync(path.join(root, 'logs/stackchan-monitor.log'), 'a', 0o600);
    const child = spawn(process.execPath, [server], { cwd: root, detached: true, stdio: ['ignore', log, log] });
    fs.closeSync(log); child.unref(); fs.writeFileSync(pidfile, String(child.pid), { mode: 0o600 });
    expectedPid = child.pid;
  }
  let ready = false;
  for (let i = 0; i < 30; i++) {
    const result = spawnSync('/usr/bin/curl', ['-q', '--noproxy', '*', '--max-time', '1', '-sf', `http://127.0.0.1:${port}/health`], { encoding: 'utf8' });
    try { const health = JSON.parse(result.stdout); ready = health.service === 'stackchan-monitor' && health.pid === expectedPid; } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error('启动未确认，请查看 logs/stackchan-monitor.log');
  console.log(`http://127.0.0.1:${port}/`);
}
