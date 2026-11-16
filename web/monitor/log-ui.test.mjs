import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {MindProxy} from './mind.mjs';
import {Monitor} from './core.mjs';
import {createMonitorServer} from './server.mjs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/log-ui.js',import.meta.url)).toString('base64'));

const T=Date.UTC(2026,10,16,6,0,0);             // 14:00 Beijing, whatever the test machine's zone
const SHA='a'.repeat(64);

test('filters become the /log query', ()=>{
 assert.equal(ui.buildQuery({kind:'call.',cost:true}),'kind=call.&cost=1&limit=200');
 assert.equal(ui.buildQuery({from:1,to:2,kind:'',svc:'ear',trace:'t1',cost:false,before:'ear:r1:5',limit:50}),
  'from=1&to=2&svc=ear&trace=t1&before=ear%3Ar1%3A5&limit=50');
 assert.equal(ui.buildQuery({}),'limit=200');
});

test('cost: currency sign, short amount, unknown price', ()=>{
 assert.equal(ui.fmtCost({amount:0.0042,cur:'CNY'}),'¥0.0042');
 assert.equal(ui.fmtCost({amount:null,cur:'CNY'}),'未知');
 assert.equal(ui.fmtCost({amount:1.5,cur:'USD'}),'$1.5');
 assert.equal(ui.fmtCost({amount:0.0000123,cur:'USD'}),'$0.0000123');
 assert.equal(ui.fmtCost({amount:0,cur:'CNY'}),'¥0');
 assert.equal(ui.fmtCost(undefined),'');
});

test('gaps: red bars placed on the window, labelled, clipped', ()=>{
 const gaps=[{svc:'ear',run:'r1',from_ms:T-10*60000,to_ms:T+15*60000,why:'no_beat'},{svc:'tts',run:'r2',from_ms:T+30*60000,to_ms:T+31*60000,why:'seq_jump'}];
 const seg=ui.gapSegments(gaps,T,T+60*60000);
 assert.deepEqual(seg.map(s=>[s.svc,s.left,s.width]),[['ear',0,25],['tts',50,1.667]]);
 const html=ui.gapsHtml(gaps,T,T+60*60000);
 assert.equal((html.match(/class="gap /g)||[]).length,2);
 assert.match(html,/data-l="0" data-w="25"/);
 assert.match(html,/ear 没有心跳 13:50:00–14:15:00/);
 assert.match(html,/tts 序号跳号 14:30:00–14:31:00/);
 assert.equal(ui.gapsHtml([],T,T+1),'');
});

test('gaps: a kill / quick restart (no_stop) is its own orange mark; seq jumps say how many were lost', ()=>{
 const gaps=[{svc:'mind',run:'r1',from_ms:T+5*60000,to_ms:T+5*60000+20000,why:'no_stop'},
  {svc:'ear',run:'r2',from_ms:T,to_ms:T+60000,why:'seq_jump',lost:4}];
 const html=ui.gapsHtml(gaps,T,T+60*60000);
 assert.match(html,/class="gap no_stop"/);
 assert.match(html,/mind 被杀\/重启 14:05:00–14:05:20/);
 assert.match(html,/ear 序号跳号（丢 4 条） 14:00:00–14:01:00/);
});

test('gaps are asked for at most the last 48 h of the window (mind refuses more)', ()=>{
 assert.equal(ui.gapsFrom(T-3600000,T),T-3600000);
 assert.equal(ui.gapsFrom(T-72*3600000,T),T-48*3600000);
 assert.equal(ui.GAPS_MAX_MS,48*3600000);
});

test('last seen: each service, red when older than 2 minutes while looking at now', ()=>{
 const last={ear:{at:T-30000,kind:'svc.beat'},mind:{at:T-5*60000,kind:'call.chat'},eye:null,notes:null,memory:null,tts:{at:T-1000,kind:'call.tts'}};
 assert.deepEqual(ui.staleSvcs(last,T),['mind','eye','notes','memory']);
 const html=ui.lastSeenHtml(last,T,true);
 assert.match(html,/<span class="svc-last stale" title="[^"]*">mind 5 分钟前<\/span>/);
 assert.match(html,/<span class="svc-last" title="[^"]*">ear 30 秒前<\/span>/);
 assert.match(html,/eye 30 天内没有记录/);
 assert.doesNotMatch(ui.lastSeenHtml(last,T,false),/stale/);       // an old window: no alarm
});

test('rows: time, service, kind, summary, cost; escaped', ()=>{
 const html=ui.rowHtml({_id:'ear:r1:2',at:T,svc:'ear',kind:'call.realtime.turn',sum:'<b>你好</b>',cost:{amount:0.0042,cur:'CNY'},ok:false});
 assert.match(html,/data-id="ear:r1:2"/);
 assert.match(html,/14:00:00/);
 assert.match(html,/&lt;b&gt;你好/);
 assert.match(html,/¥0.0042/);
 assert.match(html,/class="logrow bad"/);
});

test('detail: envelope JSON, one body slot per section, trace link, heard candidates', ()=>{
 const r={_id:'ear:r1:2',at:T,svc:'ear',kind:'call.realtime.turn',trace:'t9',body:{input:SHA,output:'b'.repeat(64)},
  extra:{heard:[{trace:'t1',text:'今天<几号>',at:T-2000},{trace:null,text:'嗯',at:T-500}]}};
 const html=ui.detailHtml(r);
 assert.match(html,/"kind": "call.realtime.turn"/);
 assert.match(html,new RegExp(`data-body="${SHA}" data-at="${T}" data-name="input"`));
 assert.match(html,/data-name="output"/);
 assert.match(html,/<button data-trace="t9" data-at="\d+">链路 t9<\/button>/);
 assert.match(html,new RegExp(`<button data-trace="t1" data-at="${T-2000}">`));
 assert.match(html,/今天&lt;几号&gt;/);
 assert.match(html,/「嗯」（没有链路）/);
 const gate=ui.detailHtml({_id:'ear:r1:3',at:T,svc:'ear',kind:'gate.realtime',extra:{heard:[{trace:'t2',text:'走了',at:T}]}});
 assert.match(gate,/ear 听到的候选/);
 assert.doesNotMatch(ui.detailHtml({_id:'x',at:T,svc:'mind',kind:'call.chat',extra:{heard:[{trace:'t',text:'x',at:T}]}}),/候选/);
});

test('chain: oldest first rows', ()=>{
 const html=ui.chainHtml('t1',[{_id:'ear:r1:1',at:T,svc:'ear',kind:'heard.asr',sum:'你好'},{_id:'mind:r1:1',at:T+1000,svc:'mind',kind:'call.chat',sum:'你好呀'}]);
 assert.ok(html.indexOf('heard.asr')<html.indexOf('call.chat'));
 assert.match(html,/链路 t1（2 条）/);
});

test('ledger: per service / model / currency, totals, unknown hint', ()=>{
 const html=ui.ledgerHtml({day:'2026-11-16',by:[{svc:'ear',model:'qwen',cur:'CNY',amount:0.01,n:2,unknown_n:1},{svc:'mind',model:'m',cur:'USD',amount:0.5,n:1,unknown_n:0}],total:{CNY:0.01,USD:0.5},unknown_n:1});
 assert.match(html,/¥0.01/);
 assert.match(html,/\$0.5/);
 assert.match(html,/含未知 1 笔/);
 assert.doesNotMatch(ui.ledgerHtml({day:'2026-11-16',by:[],total:{},unknown_n:0}),/含未知/);
});

test('the mind proxy lets the log paths and params through, strictly', ()=>{
 for(const p of ['/log?from=1&to=2&kind=call.&svc=ear&trace=t1&cost=1&limit=200&before=ear:r1a2:5',
  `/log/body?at=1794300000000&id=${SHA}`,'/log/chain?trace=a1b2c3&at=1','/log/ledger?day=2026-11-16','/log/gaps?from=1&to=2','/log/last'])
  assert.equal(MindProxy.check(p),p,p);
 for(const p of ['/log?svc=other','/log?kind=Call%3B','/log?cost=yes','/log?before=..','/log?trace=a%20b',
  '/log/body?id=../x','/log/ledger?day=16-11','/log?kind=call.&kind=gate.','/db/people?kind=call.'])
  assert.equal(MindProxy.check(p),null,p);
 assert.equal(MindProxy.check('/db/people?kind=owner'),'/db/people?kind=owner');    // library kinds unchanged
});

test('server forwards /api/mind/log* and serves the log page', async t=>{
 const calls=[],mind={get:async p=>{calls.push(p);return {status:200,body:'{"rows":[]}'};},stop(){}};
 const server=createMonitorServer(new Monitor({binding:{address:'http://127.0.0.1:8080',token:'b'.repeat(32)}}),undefined,mind);
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const url=`http://127.0.0.1:${server.address().port}`;
 for(const p of ['/log?limit=1','/log/body?at=1&id='+SHA,'/log/chain?trace=t&at=1','/log/ledger?day=2026-11-16','/log/gaps?from=1&to=2','/log/last'])
  assert.equal((await fetch(url+'/api/mind'+p)).status,200,p);
 assert.equal(calls.length,6);
 const page=await fetch(url+'/log.html');assert.equal(page.status,200);assert.match(await page.text(),/log-ui\.js/);
 assert.equal((await fetch(url+'/log-ui.js')).status,200);
 assert.equal((await fetch(url+'/log.css')).status,200);
 assert.match(readFileSync(new URL('./public/index.html',import.meta.url),'utf8'),/href="\/log.html"[^>]*>日志</);
});

test('times are Beijing time (+08:00), not the Mac zone', ()=>{
 assert.equal(ui.hms(Date.UTC(2026,10,15,16,0,5)),'00:00:05');
 assert.equal(ui.today(Date.UTC(2026,10,15,16,0,0)),'2026-11-16');            // Beijing midnight
 assert.equal(ui.today(Date.UTC(2026,10,15,15,59,59)),'2026-11-15');
 assert.equal(ui.parseBj('2026-11-16T14:00'),Date.UTC(2026,10,16,6,0,0));
 assert.equal(ui.bjInput(Date.UTC(2026,10,16,6,30,0)),'2026-11-16T14:30');
 assert.ok(Number.isNaN(ui.parseBj('')));
});

function fakeDoc(){
 const els={},listeners={};
 const el=id=>els[id]??=({id,value:id==='log-range'?'1h':'',checked:false,hidden:false,innerHTML:'',textContent:'',dataset:{},
  classList:{toggle(){},contains(){return false;}},addEventListener(t,f){(listeners[id+':'+t]??=[]).push(f);},
  querySelectorAll(){return [];},insertAdjacentHTML(_,h){this.innerHTML+=h;},scrollIntoView(){}});
 return {els,listeners,doc:{getElementById:el,addEventListener(t,f){listeners['doc:'+t]=f;}}};
}

test('mount: refresh reloads the ledger too; last seen polls every 60 s; default day in Beijing', async ()=>{
 const {els,doc}=fakeDoc(),seen=[],timers=[];
 const fetchJson=async p=>{seen.push(p);
  if(p.startsWith('/log?'))return {from:T-3600000,to:T,more:false,rows:[]};
  if(p.startsWith('/log/gaps'))return {from:T-3600000,to:T,gaps:[]};
  if(p.startsWith('/log/last'))return {now:T,svcs:{}};
  return {day:'2026-11-16',by:[],total:{},unknown_n:0};};
 const now=()=>Date.UTC(2026,10,15,16,30,0);                                  // 00:30 on the 16th in Beijing
 const view=ui.mountLog(doc,{fetchJson,now,every:(f,ms)=>{timers.push(ms);return 1;},stopEvery:()=>{}});
 await new Promise(r=>setTimeout(r,20));
 assert.deepEqual(timers,[60000]);
 assert.equal(els['log-day'].value,'2026-11-16');
 assert.equal(seen.filter(p=>p.startsWith('/log/ledger')).length,1);
 await view.refresh();await new Promise(r=>setTimeout(r,20));
 assert.equal(seen.filter(p=>p.startsWith('/log/ledger')).length,2);
 assert.equal(els['log-to'].value,'2026-11-16T00:30');
});
