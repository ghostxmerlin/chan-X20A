// The three big modes (owner 2026-11-20, collector-0.5.21): 关 / 收集 / 智能 at the top of the page, owned by the
// robot (telemetry big_mode); the small switches are folded into 详细设置 and last only until the robot restarts.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const bm=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/bigmode-ui.js',import.meta.url)).toString('base64'));
const read=f=>readFileSync(new URL(f,import.meta.url),'utf8');
const robot=(mode='collect',cap=true)=>({boot:'b1',capabilities:cap?{big_mode:'supported'}:{},telemetry:{big_mode:mode?{mode}:undefined}});

test('three modes in order with plain Chinese names',()=>{
 assert.deepEqual(bm.BIG_MODES.map(m=>[m.id,m.name]),[['off','关'],['collect','收集'],['smart','智能']]);
 for(const m of bm.BIG_MODES)assert.ok(m.detail.length>4,m.id);
 assert.match(bm.BIG_MODES[0].detail,/不上传/);assert.match(bm.BIG_MODES[1].detail,/不说话/);assert.match(bm.BIG_MODES[2].detail,/付费/);
});
test('the robot says which mode it is in',()=>{
 const v=bm.bigModeView(robot('smart'),true);
 assert.deepEqual([v.known,v.mode,v.can],[true,'smart',true]);
 assert.equal(bm.bigModeView(robot('smart'),false).can,false);              // stale telemetry: no clicks
 const old=bm.bigModeView(robot(null,false),true);
 assert.deepEqual([old.known,old.can],[false,false]);assert.match(old.detail,/0\.5\.21/);
 assert.equal(bm.bigModeView(robot('weird'),true).known,false);
 assert.equal(bm.bigModeView(null,false).known,false);
});
test('choosing a mode goes to the robot with its boot',()=>{
 assert.deepEqual(bm.bigModeRequest('off',robot()),{action:'big_mode',mode:'off',boot:'b1'});
 assert.throws(()=>bm.bigModeRequest('all',robot()));
});
test('the main page: big modes on top, small switches folded, no Docking switch, no mode bar',()=>{
 const index=read('./public/index.html');
 assert.match(index,/id="bigmode"/);
 assert.ok(index.indexOf('id="bigmode"')<index.indexOf('id="switches"'));
 assert.match(index,/<details[^>]*id="switch-details"[\s\S]*详细设置[\s\S]*id="switches"[\s\S]*<\/details>/);
 assert.match(index,/重启/);
 assert.doesNotMatch(index,/id="modebar"/);
 assert.doesNotMatch(index,/mode-ui\.js/);
 assert.match(read('./public/app.js'),/BigModeBar/);
 assert.match(read('./server.mjs'),/'\/bigmode-ui\.js'/);
 const sw=read('./public/switches-ui.js');
 assert.doesNotMatch(sw,/const TOGGLES=\[[^\]]*'meet'/);
});
