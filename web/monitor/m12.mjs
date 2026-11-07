// Statistics from the device's result counter, independent of browser/HTTP frame rate.
export class M12Tracker {
  constructor() { this.clear(); }
  clear() { this.key = ''; this.points = []; }
  accept(data) {
    const m = data.m12 || {}, time = data.timestamp_us / 1e6;
    const key = `${data.boot_id}/${m.function}/${m.requested_us}`;
    if (key !== this.key) { this.clear(); this.key = key; }
    const count = m.function === 'face_recognition' ? m.recognitions : m.results;
    if (!['face_detector','face_recognition'].includes(m.function) || !Number.isFinite(time) || !Number.isSafeInteger(count)) { this.points = []; return; }
    const last = this.points.at(-1);
    if (last && (time < last.time || count < last.count)) this.points = [];
    if (this.points.at(-1)?.time === time) return;
    this.points.push({ time, count });
    this.points = this.points.filter(p => time - p.time <= 5).slice(-100);
  }
  snapshot(data, fresh) {
    const m = data?.m12 || {};
    const recognition = m.function === 'face_recognition';
    const active = recognition || m.function === 'face_detector';
    const stamp = recognition ? m.last_recognition_us : m.last_result_us;
    const age = stamp > 0 ? Math.max(0, (data.timestamp_us - stamp) / 1e6) : null;
    const currentRequest = stamp >= m.requested_us && stamp > 0;
    // running is a historical report, not a heartbeat. Even with a recent RX,
    // the UI labels this as reported standby rather than claiming live liveness.
    const standbyReported = /^(Audio FFT|audio_fft)$/.test(m.running || '') && m.last_rx_us >= m.requested_us && m.last_rx_us > 0;
    let status = !fresh ? 'disconnected' : !m.valid ? 'serial_unavailable' : !active ? (standbyReported ? 'standby' : 'standby_pending') : !currentRequest ? (recognition && m.last_recognition_report_us >= m.requested_us && m.last_recognition_report_us > 0 ? 'recognition_waiting' : 'starting') : age > 1.5 ? 'stalled' : recognition ? 'recognizing' : 'detecting';
    let fps = null;
    if (['detecting','recognizing'].includes(status)) {
      const first = this.points[0], last = this.points.at(-1);
      if (first && last.time - first.time >= 1) fps = (last.count - first.count) / (last.time - first.time);
    } else if (status === 'stalled') fps = 0;
    const showing = ['detecting','recognizing'].includes(status) && (!recognition || (m.frame_recognition === true && m.frame_us >= m.requested_us));
    const faces = showing ? (m.faces || []).filter(f =>
      ['x','y','w','h','prob'].every(k => Number.isFinite(f[k])) && f.w > 0 && f.h > 0 && f.prob >= 0 && f.prob <= 1).slice(0, 10) : [];
    return { status, fps, windowSeconds: 5, ageSeconds: age, faces, recognition, count: showing ? m.num : null };
  }
}
