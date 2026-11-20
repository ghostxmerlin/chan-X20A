import test from 'node:test';
import assert from 'node:assert/strict';
import * as ui from './public/bodies-ui.js';

const A = { hearing: true, voice_module: 'unit-asr', camera: true, faces: 'm12-128', body_sense: true, motion: 'servo', screen: '320x240', touch: 'screen', clips: 'sd', ota: true };
const M = { ...A, voice_module: 'on-device', faces: 'apple-featureprint', body_sense: false, motion: 'eyes', screen: 'window', touch: 'mouse', clips: 'bundled', ota: false };
const D = { robot: 'hello', database: 'hello', online: 1, unknown: [],
  kinds: [{ kind: 'stackchan', name: '机身', abilities: A, tuning: { quiet_labels: true, unclear_is_voice: true, denoise: true }, online: true,
            bodies: [{ id: 'stackchan-7c4f<x>', online: true, links: { hearing: 1, vision: 1 }, last_seen_ms: 1000, device: { firmware: 'collector-0.5.17' } }] },
          { kind: 'mac', name: 'Mac', abilities: M, tuning: { quiet_labels: false, unclear_is_voice: false, denoise: true }, online: false, bodies: [] }] };

test('the bodies page: a card per kind, the devices seen, the rows where the kinds differ', () => {
  const h = ui.pageHtml(D, 61000);
  assert.match(h, /<b>2<\/b><span>种身体/);
  assert.match(h, /在线（听觉 1 条，视觉 1 条连接）/);
  assert.match(h, /1 分钟前/);
  assert.match(h, /stackchan-7c4f&lt;x&gt;/);                           // escaped
  assert.match(h, /这套服务还没见过这种身体/);
  assert.match(h, /<tr class="diff"><th class="k">动作.*<td>舵机<\/td><td>用眼睛动作代替<\/td>/);
  assert.match(h, /<tr><th class="k">听觉/);                            // the same on both: not marked
  assert.match(h, /<tr><th class="k">降噪/);
  assert.match(h, /<td class="no">没有<\/td>/);
  assert.doesNotMatch(h, /style=/);                                     // the monitor's CSP drops inline styles
  assert.equal(ui.ago(0, 5), '没见过');
});
