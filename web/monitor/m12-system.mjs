import { spawn } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';

// Read-only: no sudo, configuration writes, function switches or video capture.
const remoteStats = `
trap 'exit 0' HUP TERM INT
G=/sys/devices/system/cpu/cpufreq/policy0
while true; do
  boot=$(cat /proc/sys/kernel/random/boot_id)
  up=$(cut -d' ' -f1 /proc/uptime)
  temp=$(awk '{print $2}' /sys/class/mstar/msys/TEMP_R)
  freq=$(cat $G/scaling_cur_freq)
  gov=$(cat $G/scaling_governor)
  fn=$(ps | awk '!f && /[.]\\/bin\\// { s=$0; sub(/.*[.]\\/bin\\//, "", s); sub(/[^a-z_].*/, "", s); print s; f=1 }')
  link=$(cat /sys/class/net/wlan0/operstate)
  [ -n "$temp" ] || temp=missing
  [ -n "$fn" ] || fn=none
  printf 'STAT %s %s %s %s %s %s %s\\n' "$boot" "$up" "$temp" "$freq" "$gov" "$fn" "$link" || exit
  sleep 2
done
`;

export function parseSystemLine(line) {
  const parts = line.trim().split(/\s+/);
  if (parts.length !== 8 || parts[0] !== 'STAT') return null;
  const [, boot, up, temp, freq, governor, mode, link] = parts;
  const uptime = Number(up), frequency = Number(freq), temperature = Number(temp);
  if (!/^[\w-]{1,64}$/.test(boot) || !Number.isFinite(uptime) || uptime < 0 ||
      !Number.isFinite(frequency) || frequency < 0 || frequency > 5000000 ||
      !/^[a-z_]{1,40}$/.test(mode) || !/^[a-z_]{1,40}$/.test(governor) || !['up','down','unknown','dormant'].includes(link)) return null;
  return { boot, uptime, celsius: Number.isFinite(temperature) && temperature >= -40 && temperature <= 150 ? temperature : null,
    frequencyMHz: frequency / 1000, governor, mode, wifiUp: link === 'up' };
}

export function sshCommand(config, root) {
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(config.host || '') || config.host.split('.').some(n => Number(n) > 255) ||
      !/^[a-zA-Z0-9_][\w-]{0,63}$/.test(config.user || '') || !/^[\w][\w.-]{0,127}$/.test(config.host_key_alias || ''))
    throw new Error('M12 本机绑定配置无效');
  const args = ['-F','/dev/null','-T','-o','StrictHostKeyChecking=yes','-o',`HostKeyAlias=${config.host_key_alias}`,
    '-o',`UserKnownHostsFile=${path.join(os.homedir(), '.ssh/known_hosts')}`, '-o','GlobalKnownHostsFile=/dev/null',
    '-o','LogLevel=ERROR','-o','PubkeyAuthentication=no','-o','PreferredAuthentications=password,keyboard-interactive',
    '-o','NumberOfPasswordPrompts=1','-o','ConnectTimeout=5','-o','ServerAliveInterval=3','-o','ServerAliveCountMax=2',
    `${config.user}@${config.host}`, `echo ${Buffer.from(remoteStats).toString('base64')} | base64 -d | sh`];
  const env = {...process.env, SSH_ASKPASS:path.join(root,'m12/panel/askpass.sh'), SSH_ASKPASS_REQUIRE:'force',
    UNITV2_PASS:config.password || '12345678', DISPLAY:process.env.DISPLAY || ':0'};
  return { args, env };
}

export class M12System {
  constructor(config, options = {}) {
    this.config = config; this.root = options.root || process.cwd(); this.spawn = options.spawn || spawn;
    this.sample = null; this.at = 0; this.history = []; this.active = false; this.child = null; this.epoch = 0; this.error = '';
    if (config) sshCommand(config, this.root); // Validate before accepting viewers.
  }
  accept(sample, now = Date.now()) {
    if (!sample) return;
    if (sample.boot === this.sample?.boot && sample.uptime === this.sample.uptime) return;
    if (sample.boot !== this.sample?.boot || sample.uptime < this.sample?.uptime) this.history = [];
    this.sample = sample; this.at = now; this.error = '';
    if (sample.celsius !== null) this.history.push({ at: now, celsius: sample.celsius });
    this.history = this.history.filter(p => now-p.at <= 120000).slice(-61);
  }
  snapshot(now = Date.now()) {
    const connected = !!this.sample && now >= this.at && now-this.at < 6000;
    const valid = connected && this.sample.celsius !== null;
    return { configured: !!this.config, connected, receivedAt: this.at,
      error: connected ? '' : this.error || (this.config ? '等待 M12 网络数据' : 'M12 网络未配置'),
      mode: connected ? this.sample.mode : null, wifiUp: connected ? this.sample.wifiUp : null,
      frequencyMHz: connected ? this.sample.frequencyMHz : null, uptime: connected ? this.sample.uptime : null,
      temperature: { available: !!this.config, valid, celsius: valid ? this.sample.celsius : null, at: this.at,
        source: 'M12 芯片 / TEMP_R', reason: valid ? '' : connected ? '温度读取无效' : this.error || '等待 M12 网络数据' },
      history: connected ? this.history : [] };
  }
  start() { if (this.active || !this.config) return; this.active = true; this.connect(); }
  stop() {
    this.active = false; this.epoch++; clearTimeout(this.retry); clearInterval(this.watchdog);
    this.child?.kill(); this.child = null; this.sample = null; this.at = 0; this.history = [];
  }
  connect() {
    if (!this.active) return;
    const epoch = ++this.epoch, {args,env} = sshCommand(this.config,this.root);
    const child = this.spawn('/usr/bin/ssh',args,{env,stdio:['ignore','pipe','pipe']}); this.child = child;
    let pending = '', lastGood = Date.now(), failure = '';
    child.stderr.resume();
    const fail = message => { failure = message; child.kill(); };
    this.watchdog = setInterval(() => { if (Date.now()-lastGood > 10000) fail('M12 未返回系统数据，正在重连'); },1000);
    child.stdout.on('data', chunk => {
      if (!this.active || epoch !== this.epoch) return;
      pending += chunk.toString(); if (pending.length > 8192) { fail('M12 系统响应过大'); return; }
      const lines = pending.split('\n'); pending = lines.pop();
      for (const line of lines) { const sample = parseSystemLine(line); if (sample) { this.accept(sample); lastGood=Date.now(); } }
    });
    child.on('error', () => { failure='无法启动 M12 SSH 读取'; });
    child.on('close', code => {
      if (epoch !== this.epoch) return;
      clearInterval(this.watchdog); this.child=null;this.sample=null;this.at=0;
      this.error=failure || `M12 SSH 连接中断 (${code ?? 'signal'})，检查网络或本机绑定`;
      if (this.active) this.retry=setTimeout(()=>this.connect(),3000);
    });
  }
}
