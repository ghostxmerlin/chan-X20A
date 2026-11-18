// M12 (m12d) status for the page, from the robot's telemetry (2026-11-10). Liveness is the
// m12d heartbeat (every 2 s; offline after 6 s), the vision mode is what m12d confirmed in
// its cfg reply, the frame rate and temperature come from the heartbeat.
// m12d 0.2.0 (collector-0.5.02): event lines arrive in telemetry m12.events.recent (newest 12, by seq); the tracker
// keeps the last EVENTS_KEPT for the page and watches serial bandwidth (heartbeat fps / drop, received bytes per s).
const finite = v => typeof v === 'number' && Number.isFinite(v);
export const EVENTS_KEPT = 40;
export const SERIAL_BYTES_PER_S = 11520;          // 115200 baud 8N1
// type -> [Chinese name, group]; groups get one colour each on the page
export const M12_EVENTS = {
  wake:['待机唤醒','wake'], cover:['捂住镜头','light'], uncover:['拿开遮挡','light'], light:['灯光变化','light'],
  dark:['变暗','light'], bright:['变亮','light'], enter:['有人来了','presence'], leave:['有人走了','presence'],
  near:['靠近','presence'], far:['离远','presence'], look:['脸朝着我','head'], look_end:['脸转开','head'],
  nod:['点头','head'], shake:['摇头','head'], tilt:['歪头','head'], smile:['笑了','head'],
};
const pct = v => `${Math.round(v * 100)}%`;
const DUR_WORDS = { uncover:'捂了', leave:'待了', look_end:'朝着' };
// One telemetry event -> {label, group, who, detail}: what the page lists (type, face/person id, key fields).
export function m12EventView(e) {
  const [name, group] = M12_EVENTS[e?.ev] || [`未知 ${String(e?.ev ?? '')}`.trim(), 'other'];
  const label = e?.ev === 'light' && typeof e.on === 'boolean' ? (e.on ? '开灯' : '关灯') : name;
  const who = Number.isInteger(e?.face) && e.face >= 0 ? `脸#${e.face}` : Number.isInteger(e?.person) && e.person >= 0 ? `人形#${e.person}` : '';
  const parts = [];
  if (finite(e?.n)) parts.push(`${e.n} 下`);
  if (e?.side === 'l' || e?.side === 'r') parts.push(e.side === 'l' ? '头顶向画面左' : '头顶向画面右');
  if (Array.isArray(e?.d) && e.d.length === 2 && e.d.every(finite)) parts.push(`${e.d[0]} → ${e.d[1]} 米`);
  if (finite(e?.dur)) parts.push(`${DUR_WORDS[e.ev] || '持续'} ${e.dur} 秒`);
  if (Array.isArray(e?.lum) && e.lum.length === 2 && e.lum.every(finite)) parts.push(e.lum[0] >= 0 ? `亮度 ${e.lum[0]} → ${e.lum[1]}` : `亮度 ${e.lum[1]}`);
  if (finite(e?.amt)) parts.push(`变化 ${pct(e.amt)}`);
  if (Array.isArray(e?.area) && e.area.length === 4 && e.area.every(finite)) parts.push(`区域 ${e.area[0]},${e.area[1]} ${e.area[2]}×${e.area[3]}`);
  if (e?.pan === true) parts.push('镜头被转动');
  if (e?.hot === true) parts.push('过热，没有分析');
  if (Array.isArray(e?.faces) || Array.isArray(e?.persons)) {
    const faces = Array.isArray(e.faces) ? e.faces : [], persons = Array.isArray(e.persons) ? e.persons : [];
    const named = faces.filter(f => typeof f?.name === 'string' && f.name).map(f => finite(f.sim) ? `${f.name} ${pct(f.sim)}` : f.name);
    const nf = Number.isInteger(e.faces_seen) ? e.faces_seen : faces.length, np = Number.isInteger(e.persons_seen) ? e.persons_seen : persons.length;
    parts.push(`看到脸 ${nf}${named.length ? `（${named.join('、')}）` : ''} · 人形 ${np}`);
  }
  if (finite(e?.cd)) parts.push(`冷却 ${e.cd} 秒`);
  return { label, group, who, detail: parts.join(' · ') };
}
export class M12Tracker {
  constructor() { this.clear(); }
  clear() { this.key = ''; this.events = []; this.evSeq = 0; this.missed = 0; this.rx = []; this.drops = []; }
  // Once per new telemetry (now = when it arrived, ms). -> the events new since the last call, oldest first, each
  // with `at` (wall ms: arrival minus how long ago the robot received the line) and `age_ms`.
  accept(data, now = Date.now()) {
    this.key = `${data?.boot_id}`;
    const m = data?.m12 || {}, us = data?.timestamp_us;
    const recent = Array.isArray(m.events?.recent) ? m.events.recent.filter(e => Number.isSafeInteger(e?.seq)) : [];
    const added = recent.filter(e => e.seq > this.evSeq).sort((a, b) => a.seq - b.seq);
    if (added.length && this.evSeq && added[0].seq > this.evSeq + 1) this.missed += added[0].seq - this.evSeq - 1;
    const out = added.map(e => {
      const age = finite(us) && finite(e.at_us) ? Math.max(0, (us - e.at_us) / 1000) : 0;
      return { ...e, ...m12EventView(e), at: now - age, age_ms: age };
    });
    if (out.length) { this.evSeq = out.at(-1).seq; this.events = [...[...out].reverse(), ...this.events].slice(0, EVENTS_KEPT); }
    if (finite(us) && finite(m.rx_bytes)) {
      if (this.rx.length && (m.rx_bytes < this.rx.at(-1).bytes || us <= this.rx.at(-1).us)) this.rx = [];
      this.rx.push({ us, bytes: m.rx_bytes }); this.rx = this.rx.filter(p => us - p.us <= 10e6);
    }
    const hb = m.heartbeat;
    if (finite(us) && finite(hb?.drop) && finite(hb?.up) && hb.up !== this.drops.at(-1)?.up) {
      if (this.drops.length && (hb.drop < this.drops.at(-1).drop || hb.up < this.drops.at(-1).up)) this.drops = [];   // M12 restarted
      this.drops.push({ us, up: hb.up, drop: hb.drop }); this.drops = this.drops.filter(p => us - p.us <= 60e6);
    }
    return out;
  }
  // Serial bandwidth: heartbeat fps and drop (lines m12d dropped because the link was too slow), bytes received per s.
  serial(data) {
    const hb = data?.m12?.heartbeat;
    const first = this.rx[0], last = this.rx.at(-1);
    const span = first && last ? (last.us - first.us) / 1e6 : 0;
    return { fps: finite(hb?.fps) ? hb.fps : null, drop: finite(hb?.drop) ? hb.drop : null,
      dropMinute: this.drops.length > 1 ? this.drops.at(-1).drop - this.drops[0].drop : null,
      bytesPerSecond: span >= 1.5 ? (last.bytes - first.bytes) / span : null, capacity: SERIAL_BYTES_PER_S };
  }
  snapshot(data, fresh) {
    const m = data?.m12 || {};
    const hb = m.heartbeat || null;
    const age = finite(m.last_result_us) && m.last_result_us > 0 ? Math.max(0, (data.timestamp_us - m.last_result_us) / 1e6) : null;
    let status;
    if (!fresh) status = 'disconnected';
    else if (!m.valid) status = 'serial_unavailable';
    else if (!m.online) status = 'offline';
    else if (hb?.tier === 3) status = 'hot';
    else if (m.mode_request_pending || m.function !== m.requested_function) status = m.requested_function === 'detect' ? 'starting' : 'stopping';
    else if (m.function === 'standby') status = 'standby';
    else if (m.function === 'detect') status = age != null && age <= 1.5 ? 'detecting' : 'stalled';
    else status = 'starting';
    const detecting = status === 'detecting';
    const box = f => ['x','y','w','h','prob'].every(k => finite(f?.[k])) && f.w > 0 && f.h > 0 && f.prob >= 0 && f.prob <= 1;
    const faces = detecting ? (m.faces || []).filter(box).slice(0, 10) : [];
    const persons = detecting ? (m.persons || []).filter(p => ['x','y','w','h'].every(k => finite(p?.[k])) && p.w > 0 && p.h > 0).slice(0, 5) : [];
    return {
      status, fps: detecting && finite(hb?.fps) ? hb.fps : status === 'stalled' ? 0 : null, ageSeconds: age,
      faces, persons, count: detecting ? m.num : null,
      temperature: fresh && m.online && finite(hb?.t) ? hb.t : null, tier: hb?.tier ?? null,
      library: m.library?.valid ? (m.library.faces || []) : null,
      commands: Array.isArray(m.commands) ? m.commands : [],
      events: this.events, eventsTotal: finite(m.events?.total) ? m.events.total : null,
      eventsUnknown: finite(m.events?.unknown) ? m.events.unknown : 0, eventsMissed: this.missed,
      serial: fresh ? this.serial(data) : null,
    };
  }
}
