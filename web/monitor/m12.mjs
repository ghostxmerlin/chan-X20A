// M12 (m12d) status for the page, from the robot's telemetry (2026-11-10). Liveness is the
// m12d heartbeat (every 2 s; offline after 6 s), the vision mode is what m12d confirmed in
// its cfg reply, the frame rate and temperature come from the heartbeat.
const finite = v => typeof v === 'number' && Number.isFinite(v);
export class M12Tracker {
  constructor() { this.clear(); }
  clear() { this.key = ''; }
  accept(data) { this.key = `${data?.boot_id}`; }   // kept for the monitor's call sites; m12d reports its own fps
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
    };
  }
}
