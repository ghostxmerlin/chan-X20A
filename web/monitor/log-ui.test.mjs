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
 assert.match(html,/<span class="svc-last" title="30 秒前，[^"]*">ear<\/span>/);
 assert.match(html,/eye 30 天内没有记录/);
 assert.doesNotMatch(ui.lastSeenHtml(last,T,false),/stale/);       // an old window: no alarm
});

test('rows: Chinese name, category badge, service, summary, time, cost; escaped', ()=>{
 const html=ui.rowHtml({_id:'ear:r1:2',at:T,svc:'ear',kind:'call.realtime.turn',model:'qwen-rt',sum:'<b>你好</b>',cost:{amount:0.0042,cur:'CNY'},ok:false,err:'stalled'});
 assert.match(html,/data-id="ear:r1:2"/);
 assert.match(html,/class="ev cat-speak bad"/);
 assert.match(html,/<span class="badge soft" aria-hidden="true">答<\/span>/);          // no body: an event, light badge
 const call=ui.rowHtml({_id:'n',at:T,svc:'notes',kind:'call.notes',model:'m',body:{system:SHA}});
 assert.match(call,/<span class="badge" aria-hidden="true">纪<\/span>/);              // a model call: solid
 assert.match(ui.rowHtml({_id:'t',at:T,svc:'ear',kind:'call.tingwu',model:'tingwu'}),/<span class="badge soft" aria-hidden="true">悟<\/span>/);
 assert.match(html,/<b>围观回答<\/b>/);
 assert.match(html,/<span class="tag model">qwen-rt<\/span>/);
 assert.match(html,/<span class="tag fail">失败<\/span>/);
 assert.match(html,/14:00:00/);
 assert.match(html,/&lt;b&gt;你好/);
 assert.match(html,/¥0.0042/);
 assert.match(ui.rowHtml({_id:'x',at:T,svc:'ear',kind:'heard.asr',sum:'一\n二'}),/class="ev cat-hear".*一 ／ 二/);
 assert.match(ui.rowHtml({_id:'x',at:T,svc:'ear',kind:'link.open',extra:{link:'vision'}}),/<b>连上：视觉<\/b>/);
 assert.match(ui.rowHtml({_id:'x',at:T,svc:'ear',kind:'call.unknown.thing'}),/<b>call.unknown.thing<\/b>/);   // unknown kinds keep their code
 assert.match(ui.rowHtml({_id:'x',at:T,svc:'ear',kind:'heard.asr'},true),/class="ev cat-hear sel"/);
});

test('kinds are grouped by what hello was doing', ()=>{
 const k={'heard.asr':'hear','call.tingwu':'hear','call.notes':'hear','call.eye':'see','call.look':'see','trigger.burst':'see',
  'call.fusion':'think','call.segment':'think','call.llm':'think','call.realtime.turn':'speak','call.realtime.context':'speak','call.tts':'speak',
  'call.chat':'speak','call.say.judge':'speak','gate.realtime':'speak','gate.rescue':'speak','filler.play':'speak','trigger.greet':'speak',
  'trigger.hush':'speak','call.memory':'mem','call.memory.extract':'mem','switch.set':'ctl','mode.input':'ctl','preset.apply':'ctl',
  'billing.set':'ctl','switch.pause':'ctl','budget.warn':'ctl','link.open':'link','robot.state':'link','svc.beat':'sys','svc.error':'sys'};
 for(const [kind,cat] of Object.entries(k))assert.equal(ui.category(kind),cat,kind);
 assert.deepEqual(ui.CATS.map(c=>c.glyph).join(''),'听看想说记控连系');
});

test('timeline: a heading at every new minute; categories counted', ()=>{
 const rows=[{_id:'a',at:T+61000,svc:'ear',kind:'heard.asr'},{_id:'b',at:T+60500,svc:'eye',kind:'call.eye'},{_id:'c',at:T+5000,svc:'mind',kind:'call.fusion'}];
 const html=ui.listHtml(rows,'b');
 assert.deepEqual([...html.matchAll(/<div class="minute">([^<]+)<\/div>/g)].map(m=>m[1]),['14:01','14:00']);
 assert.match(html,/class="ev cat-see sel"/);
 const n=ui.countCats(rows);
 assert.equal(n.hear,1);assert.equal(n.see,1);assert.equal(n.think,1);assert.equal(n.speak,0);
 const cats=ui.catsHtml(n,'see',3);
 assert.match(cats,/data-cat="" aria-pressed="false">.*全部.*<span class="count">3<\/span>/);
 assert.match(cats,/class="cat cat-see" data-cat="see" aria-pressed="true">/);
 assert.match(cats,/data-cat="speak" aria-pressed="false" data-empty>/);
});

test('kinds of the picked category: own character, solid when a model call, counted, nested under it', ()=>{
 const rows=[{_id:'1',at:T,svc:'ear',kind:'heard.asr'},{_id:'2',at:T,svc:'ear',kind:'heard.asr'},{_id:'3',at:T,svc:'ear',kind:'call.tingwu',model:'t'},
  {_id:'4',at:T,svc:'notes',kind:'call.notes',model:'m',body:{system:SHA}},{_id:'5',at:T,svc:'eye',kind:'call.eye',model:'m',body:{input:SHA}}];
 const k=ui.kindCounts(rows,'hear');
 assert.deepEqual(k.map(x=>[x.kind,x.n,x.prompt]),[['heard.asr',2,false],['call.notes',1,true],['call.tingwu',1,false]]);
 const html=ui.kindsHtml(k,'hear','call.notes');
 assert.match(html,/data-kind="call.notes" aria-pressed="true"><span class="badge" aria-hidden="true">纪<\/span><span class="kind-name">写听觉纪要/);
 assert.match(html,/data-kind="call.tingwu" aria-pressed="false"><span class="badge soft" aria-hidden="true">悟<\/span><span class="kind-name">听悟转写/);
 assert.equal(ui.kindsHtml([],'hear',''),'');
 const cats=ui.catsHtml(ui.countCats(rows),'hear',5,html);
 assert.ok(cats.indexOf('data-cat="hear"')<cats.indexOf('class="kinds')&&cats.indexOf('class="kinds')<cats.indexOf('data-cat="see"'));
 assert.deepEqual(Object.values(ui.KIND_GLYPHS).filter((g,i,a)=>a.indexOf(g)!==i),[]);                // every kind its own character
 assert.equal(ui.kindGlyph('call.new.thing'),'想');                                      // unknown: the category's
});

test('detail: header, stats, tabs; model calls get prompt sections, other bodies lazy; trace and heard candidates', ()=>{
 const r={_id:'ear:r1:2',at:T,svc:'ear',kind:'call.realtime.turn',model:'qwen-rt',ms:692,usage:{input_tokens:1444,output_tokens:82},
  cost:{amount:0.0029,cur:'CNY'},trace:'t9',body:{input:SHA,output:'b'.repeat(64),params:'c'.repeat(64)},
  extra:{heard:[{trace:'t1',text:'今天<几号>',at:T-2000},{trace:null,text:'嗯',at:T-500}]}};
 const html=ui.detailHtml(r);
 assert.match(html,/<h2>围观回答<\/h2>/);
 assert.match(html,/<span>call.realtime.turn<\/span><span>ear<\/span><span>2026-11-16 14:00:00<\/span>/);
 assert.match(html,/<dt>耗时<\/dt><dd>692 毫秒<\/dd>/);
 assert.match(html,/<div class="wide"><dt>Token<\/dt><dd>入 1444  出 82<\/dd>/);
 assert.match(html,/data-tab="prompt" aria-selected="true">提示词/);
 assert.match(html,/<div class="pane" data-pane="raw" hidden><pre class="envelope">[^]*"kind": "call.realtime.turn"/);
 assert.match(html,new RegExp(`data-load="input" data-body-id="${SHA}" data-at="${T}"`));
 assert.match(html,/data-load="output"/);
 assert.match(html,new RegExp(`data-lazy="${'c'.repeat(64)}" data-at="${T}" data-name="params"`));
 assert.match(html,/data-trace="t9" data-at="\d+">看整条链路 t9<\/button>/);
 assert.match(html,new RegExp(`data-trace="t1" data-at="${T-2000}">链路<`));
 assert.match(html,/今天&lt;几号&gt;/);
 assert.match(html,/「嗯」<span class="muted">（没有链路）/);
 const gate=ui.detailHtml({_id:'ear:r1:3',at:T,svc:'ear',kind:'gate.realtime',extra:{heard:[{trace:'t2',text:'走了',at:T}]}});
 assert.match(gate,/ear 听到的候选/);
 assert.doesNotMatch(ui.detailHtml({_id:'x',at:T,svc:'mind',kind:'call.chat',extra:{heard:[{trace:'t',text:'x',at:T}]}}),/候选/);
 const asr=ui.detailHtml({_id:'ear:r1:4',at:T,svc:'ear',kind:'heard.asr',sum:'你好',by:'asr',body:{input:SHA},extra:{dur:1.2},ok:false,err:'坏了'});
 assert.doesNotMatch(asr,/data-load/);                                    // not a model call: fields + a lazy body
 assert.match(asr,/data-tab="fields" aria-selected="true">详情/);
 assert.match(asr,/data-lazy=/);
 assert.match(asr,/<dt>谁触发<\/dt><dd>asr<\/dd>/);
 assert.match(asr,/<div class="banner">失败：坏了<\/div>/);
});

test('chain: oldest first rows and a way back', ()=>{
 const html=ui.chainHtml('t1',[{_id:'ear:r1:1',at:T,svc:'ear',kind:'heard.asr',sum:'你好'},{_id:'mind:r1:1',at:T+1000,svc:'mind',kind:'call.chat',sum:'你好呀'}]);
 assert.ok(html.indexOf('听到一句话')<html.indexOf('聊天回答'));
 assert.match(html,/链路 t1<\/b><span class="muted">2 条/);
 assert.match(html,/data-close-chain>回到全部记录/);
});

test('ledger: per service / model / currency, totals, unknown hint', ()=>{
 const l={day:'2026-11-16',by:[{svc:'ear',model:'qwen',cur:'CNY',amount:0.01,n:2,unknown_n:1},{svc:'mind',model:'m',cur:'USD',amount:0.5,n:1,unknown_n:0}],total:{CNY:0.01,USD:0.5},unknown_n:1};
 const html=ui.ledgerHtml(l);
 assert.match(html,/¥0.01/);
 assert.match(html,/\$0.5/);
 assert.match(html,/含未知 1 笔/);
 assert.equal(ui.ledgerTotal(l),'¥0.01  $0.5');
 assert.equal(ui.ledgerTotal({total:{}}),'没有花费');
 assert.doesNotMatch(ui.ledgerHtml({day:'2026-11-16',by:[],total:{},unknown_n:0}),/含未知/);
});

test('system prompt: head line, same as last time, or only the changed lines', ()=>{
 assert.equal(ui.systemText(JSON.stringify(['第一段','第二段'])),'第一段\n\n第二段');
 assert.equal(ui.systemText('纯文本'),'纯文本');
 const text='你是 hello。\n规则一\n规则二\n规则三\n规则四\n【情景】主人在打字';
 const same=ui.systemHtml(text,SHA,{id:SHA,at:T-60000,text:null});
 assert.match(same,/和上一次（13:59:00）一样/);
 assert.match(same,/<p class="sys-head">你是 hello。<\/p>/);
 assert.doesNotMatch(same,/class="diff"/);
 assert.match(same,/全文（\d+ 字）/);
 const changed=ui.systemHtml(text,SHA,{id:'b'.repeat(64),at:T-60000,text:text.replace('主人在打字','主人在吃饭')});
 assert.match(changed,/删 1 行，加 1 行/);
 assert.match(changed,/<div class="ln del">【情景】主人在<mark>吃饭<\/mark><\/div><div class="ln add">【情景】主人在<mark>打字<\/mark><\/div>/);
 assert.match(changed,/未变 4 行/);                                       // unchanged lines folded, one line of context kept
 assert.match(changed,/<div class="ln ctx">规则四<\/div>/);
 assert.match(ui.systemHtml(text,SHA,null),/7 天内没有上一次同类调用/);
 assert.match(ui.systemHtml(text,SHA,undefined),/读不到上一次/);
});

test('line diff keeps order and finds inserted and removed lines', ()=>{
 const ops=ui.lineDiff(['a','b','c','d'],['a','x','c','d','e']);
 assert.deepEqual(ops.map(o=>o.t+o.s),['=a','-b','+x','=c','=d','+e']);
 assert.deepEqual(ui.lineDiff(['a'],['a']).map(o=>o.t),['=']);
});

test('input as messages, images folded, realtime input by part; output pretty', ()=>{
 const input=JSON.stringify([{role:'user',content:[{type:'text',text:'看<图>'},{type:'image_url',image_url:{url:'[image omitted, 8192 bytes]'}},
  {type:'image_url',image_url:{url:'[image omitted, 2048 bytes]'}}]}]);
 const h=ui.messagesHtml(input);
 assert.match(h,/<span class="role">用户<\/span>/);
 assert.match(h,/看&lt;图&gt;/);
 assert.match(h,/图片 ×2（10 KB）/);
 assert.match(ui.messagesHtml(JSON.stringify({prompt:'[事件] 主人出现',heard:'你好'})),/ear 发的提示[^]*主人出现[^]*模型听到/);
 assert.match(ui.messagesHtml('一句话'),/<div class="prose">一句话<\/div>/);
 const tools=ui.outputHtml(JSON.stringify([{id:'c1',type:'function',function:{name:'eye',arguments:'{"people_count": 1}'}}]));
 assert.match(tools,/调用 eye/);
 assert.match(tools,/<span class="jk">"people_count"<\/span>: 1/);
 assert.match(ui.outputHtml('{"title":"讨论"}'),/<span class="jk">"title"<\/span>: <span class="js">"讨论"<\/span>/);
 assert.match(ui.outputHtml('哟，Kira！'),/<div class="prose">哟，Kira！<\/div>/);
 assert.equal(ui.tokensText({prompt_tokens:1639,completion_tokens:227,prompt_tokens_details:{cached_tokens:1024,image_tokens:492},completion_tokens_details:{reasoning_tokens:9}}),
  '入 1639（缓存 1024，图 492）  出 227（思考 9）');
 assert.equal(ui.tokensText({audio_s:12}),'audio_s 12');
 assert.equal(ui.promptGroup('call.realtime.turn'),'call.realtime.');
 assert.equal(ui.promptGroup('call.fusion'),'call.fusion');
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
 const el=id=>els[id]??=({id,value:'',checked:false,hidden:false,innerHTML:'',textContent:'',dataset:{},
  classList:{toggle(){},add(){},remove(){},contains(){return false;}},addEventListener(t,f){(listeners[id+':'+t]??=[]).push(f);},
  setAttribute(){},querySelectorAll(){return [];},insertAdjacentHTML(_,h){this.innerHTML+=h;},scrollIntoView(){}});
 return {els,listeners,doc:{getElementById:el,querySelectorAll:()=>[],addEventListener(t,f){listeners['doc:'+t]=f;}}};
}

test('mount: one page of up to 1000 records, heartbeats hidden, categories counted; ledger, 60 s poll, Beijing day', async ()=>{
 const {els,doc}=fakeDoc(),seen=[],timers=[];
 const rows=[{_id:'ear:r:3',at:T-1000,svc:'ear',kind:'heard.asr',sum:'你好'},{_id:'ear:r:2',at:T-2000,svc:'ear',kind:'svc.beat'},
  {_id:'mind:r:1',at:T-3000,svc:'mind',kind:'call.fusion',model:'m',sum:'情景'}];
 const fetchJson=async p=>{seen.push(p);
  if(p.startsWith('/log?'))return {from:T-3600000,to:T,more:false,rows};
  if(p.startsWith('/log/gaps'))return {from:T-3600000,to:T,gaps:[]};
  if(p.startsWith('/log/last'))return {now:T,svcs:{}};
  return {day:'2026-11-16',by:[],total:{CNY:0.5},unknown_n:0};};
 const now=()=>Date.UTC(2026,10,15,16,30,0);                                  // 00:30 on the 16th in Beijing
 const view=ui.mountLog(doc,{fetchJson,now,every:(f,ms)=>{timers.push(ms);return 1;},stopEvery:()=>{}});
 await new Promise(r=>setTimeout(r,20));
 assert.deepEqual(timers,[60000]);
 assert.match(seen.find(p=>p.startsWith('/log?')),/limit=1000/);
 assert.match(els['log-list'].innerHTML,/听到一句话/);
 assert.match(els['log-list'].innerHTML,/判断当前情景/);
 assert.doesNotMatch(els['log-list'].innerHTML,/心跳/);
 assert.match(els['log-cats'].innerHTML,/data-cat="" aria-pressed="true">.*<span class="count">2<\/span>/);
 assert.match(els['log-status'].textContent,/已读 3 条，1 条心跳没显示/);
 assert.match(els['log-detail'].innerHTML,/点一条记录/);
 assert.equal(els['log-day'].value,'2026-11-16');
 assert.equal(els['log-gaps-n'].textContent,'没有');
 assert.equal(seen.filter(p=>p.startsWith('/log/ledger')).length,1);
 await view.refresh();await new Promise(r=>setTimeout(r,20));
 assert.equal(seen.filter(p=>p.startsWith('/log/ledger')).length,2);
 assert.equal(els['log-to'].value,'2026-11-16T00:30');
});

test('seen.m12 (m12d 0.2.0 events): category 看, own character, title says what M12 saw', ()=>{
 assert.equal(ui.category('seen.m12'),'see');
 assert.equal(ui.kindGlyph('seen.m12'),'见');
 assert.equal(ui.kindTitle({kind:'seen.m12',extra:{ev:'nod',face:3,n:2}}),'M12 看到：点头');
 assert.equal(ui.kindTitle({kind:'seen.m12',extra:{ev:'light',on:true}}),'M12 看到：开灯');
 assert.equal(ui.kindTitle({kind:'seen.m12',extra:{ev:'zzz'}}),'M12 看到：zzz');
 assert.equal(ui.kindTitle({kind:'seen.m12'}),'M12 看到');
});
