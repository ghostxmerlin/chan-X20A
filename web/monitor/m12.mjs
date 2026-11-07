// Statistics from the device's result counter, independent of browser/HTTP frame rate.
export class M12Tracker {
  constructor() { this.clear(); }
  clear() { this.key = ''; this.points = []; }
  accept(data) {
    const m = data.m12 || {}, time = data.timestamp_us / 1e6;
    const key = `${data.boot_id}/${m.function}/${m.requested_us}`;
    if (key !== this.key) { this.clear(); this.key = key; }
    if (m.function !== 'face_detector' || !Number.isFinite(time) || !Number.isSafeInteger(m.results)) { this.points = []; return; }
    const last = this.points.at(-1);
    if (last && (time < last.time || m.results < last.count)) this.points = [];
    if (this.points.at(-1)?.time === time) return;
    this.points.push({ time, count: m.results });
    this.points = this.points.filter(p => time - p.time <= 5).slice(-100);
  }
  snapshot(data, fresh) {
    const m = data?.m12 || {};
    const active = m.function === 'face_detector';
    const age = m.last_result_us > 0 ? Math.max(0, (data.timestamp_us - m.last_result_us) / 1e6) : null;
    const currentRequest = m.last_result_us >= m.requested_us && m.last_result_us > 0;
    // running is a historical report, not a heartbeat. Even with a recent RX,
    // the UI labels this as reported standby rather than claiming live liveness.
    const standbyReported = /^(Audio FFT|audio_fft)$/.test(m.running || '') && m.last_rx_us >= m.requested_us && m.last_rx_us > 0;
    let status = !fresh ? 'disconnected' : !m.valid ? 'serial_unavailable' : !active ? (standbyReported ? 'standby' : 'standby_pending') : !currentRequest ? 'starting' : age > 1.5 ? 'stalled' : 'detecting';
    let fps = null;
    if (status === 'detecting') {
      const first = this.points[0], last = this.points.at(-1);
      if (first && last.time - first.time >= 1) fps = (last.count - first.count) / (last.time - first.time);
    } else if (status === 'stalled') fps = 0;
    const faces = status === 'detecting' ? (m.faces || []).filter(f =>
      ['x','y','w','h','prob'].every(k => Number.isFinite(f[k])) && f.w > 0 && f.h > 0 && f.prob >= 0 && f.prob <= 1).slice(0, 10) : [];
    return { status, fps, windowSeconds: 5, ageSeconds: age, faces, count: status === 'detecting' ? m.num : null,
      temperature: { available: false, valid: false, celsius: null, reason: 'M12 网络未接入', source: 'M12 芯片 / TEMP_R' } };
  }
}
