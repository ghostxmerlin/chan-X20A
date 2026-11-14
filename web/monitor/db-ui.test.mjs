import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/db-ui.js',import.meta.url)).toString('base64'));
const page=f=>readFileSync(new URL('./public/'+f,import.meta.url),'utf8');

// ---- from the task brief ----
test('addNotes sends one note per non-empty line and reports failures',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push([p,b]);if(b.fields.text==='坏')throw Error('不合法');return {version:1};});
 const r=await m.addNotes('owner','trait','爱笑\n\n  怕冷  \n坏\n'+'字'.repeat(201));
 assert.deepEqual(calls.map(c=>c[1].fields.text),['爱笑','怕冷','坏']);
 assert.ok(calls.every(c=>c[0]==='/db/person/note'&&c[1].fields.person_id==='owner'&&c[1].fields.kind==='trait'));
 assert.equal(r.saved,2);
 assert.deepEqual(r.failed.map(f=>f.error),['不合法','超过 200 字']);
});
test('stale reads cannot overwrite newer ones',async()=>{
 const rs=[];const m=new ui.LibraryModel(()=>new Promise(r=>rs.push(r)));
 const a=m.person('owner'),b=m.person('p_0123456789ab');
 rs[1]({card:{person_id:'p_0123456789ab'}});await b;rs[0]({card:{person_id:'owner'}});assert.equal(await a,false);
});
test('save sends id and expected_version only when editing',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push(b);return {};});
 await m.save('/db/memory',{kind:'event'});await m.save('/db/memory',{kind:'event'},{id:'e_0123456789ab',expected_version:3});
 assert.deepEqual(Object.keys(calls[0]),['fields']);assert.equal(calls[1].expected_version,3);
});
test('all views escape stored text',()=>{
 const x='<img src=x onerror=alert(1)>';
 const htmls=[ui.peopleHtml([{person_id:'p_0123456789ab',display:x,kind:'regular',notes:1}]),
  ui.personHtml({card:{person_id:'owner',display:x,kind:'owner',treatment:{tease:'ok',quote:'gist'},aliases:[x]},versions:[],notes:[{note_id:'n_0123456789ab',kind:'trait',text:x,version:1}],memories:[],knowledge:[]}),
  ui.memoriesHtml({items:[{memory_id:'e_0123456789ab',kind:'event',title:x,statement:x,subjects:[],when:{type:'point',start_ms:1}}],total:1,offset:0,limit:20}),
  ui.knowledgeHtml({items:[{kid:'k_0123456789ab',kind:'term',name:x,aliases:[x],heard_as:[x],statement:x}],total:1,offset:0,limit:20}),
  ui.historyHtml({versions:[{version:2,text:x,reason:x,actor:'owner',changed_at:1,origin:'owner'}],owner_original:{version:2,text:x},children:[]})];
 for(const h of htmls){assert.doesNotMatch(h,/<img/);assert.match(h,/&lt;img/);}
});
test('history shows owner original and offers switching back only when current differs',()=>{
 const owner={version:1,text:'原版',origin:'owner'},fused={version:2,text:'融合',origin:'fused'};
 assert.match(ui.historyHtml({versions:[fused,owner],owner_original:owner,children:[]}),/回到我的版本/);
 assert.doesNotMatch(ui.historyHtml({versions:[owner],owner_original:owner,children:[]}),/回到我的版本/);
});
test('overview shows counts and the server-only notice',()=>{
 const h=ui.overviewHtml({collections:{people_cards:{total:2},people_notes:{total:5},memory_items:{total:0},knowledge_items:{total:1}},voice_labels:{total:0},existing:{mind_memories:7}});
 assert.match(h,/5/);assert.match(h,/不进仓库/);assert.match(h,/没有删除/);
});
test('editable keeps only page-writable fields',()=>{
 const doc={note_id:'n_0123456789ab',person_id:'owner',kind:'trait',text:'爱笑',status:'active',observed_at:null,version:3,actor:'owner',reason:'',changed_at:1,supersedes:2,origin:'owner',owner_original:null,derived_from:[],fusion:null,scope_id:'office',_id:'x'};
 assert.deepEqual(Object.keys(ui.editable('people_notes',doc)).sort(),['kind','observed_at','person_id','status','text']);
});
test('disable re-saves the current version with only status changed',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push([p,b]);return {version:4};});
 const doc={memory_id:'e_0123456789ab',kind:'event',title:'t',statement:'s',subjects:[],topics:[],when:{type:'point',start_ms:1,end_ms:null},status:'active',version:3,scope_id:'office',source:{kind:'owner',refs:[]}};
 await m.setStatus('memory_items',doc,'disabled');
 assert.equal(calls[0][0],'/db/memory');assert.equal(calls[0][1].id,'e_0123456789ab');assert.equal(calls[0][1].expected_version,3);
 assert.equal(calls[0][1].fields.status,'disabled');assert.equal(calls[0][1].fields.title,'t');assert.ok(!('source' in calls[0][1].fields));
});

// ---- maintenance details ----
const SERVER_ONLY=['origin','actor','fusion','owner_original','derived_from','version','source','scope_id','changed_at','supersedes','reason','owner_rotation','merged_into','_id'];
const card={person_id:'p_0123456789ab',kind:'regular',display:'人物甲',aliases:['甲'],links:{voice_names:['v'],face_names:[],legacy_ids:['person:abc']},
 treatment:{tease:'never',quote:'gist'},scope_ids:['office'],status:'confirmed',owner_rotation:false,merged_into:null,source:{kind:'owner',ref:null},version:2,actor:'owner',reason:'',changed_at:1,supersedes:1};
const know={kid:'k_0123456789ab',kind:'commitment',name:'登山团体',aliases:[],heard_as:[],statement:'每月一次',subjects:['owner'],when:{valid_from:null,valid_to:null},
 scope_ids:['office'],status:'active',origin:'owner',owner_original:null,derived_from:[],fusion:null,version:1,actor:'owner',reason:'',changed_at:1,supersedes:null};
test('editable drops server fields for every collection and legacy link ids from cards',()=>{
 const c=ui.editable('people_cards',card);
 assert.deepEqual(Object.keys(c).sort(),['aliases','display','kind','links','scope_ids','status','treatment']);
 assert.deepEqual(c.links,{voice_names:['v'],face_names:[]});
 assert.deepEqual(Object.keys(ui.editable('knowledge_items',know)).sort(),['aliases','heard_as','kind','name','scope_ids','statement','status','subjects','when']);
 c.aliases.push('改');assert.deepEqual(card.aliases,['甲'],'editable copies, never aliases the version document');
});
test('setStatus on a card toggles retired with id person_id and reason',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push([p,b]);return {version:3};});
 await m.setStatus('people_cards',card,'retired','停用');
 assert.equal(calls[0][0],'/db/person');assert.equal(calls[0][1].id,'p_0123456789ab');assert.equal(calls[0][1].reason,'停用');
 assert.equal(calls[0][1].fields.status,'retired');assert.ok(!('legacy_ids' in calls[0][1].fields.links));
 for(const k of SERVER_ONLY)assert.ok(!(k in calls[0][1].fields),k);
 assert.equal(ui.toggleStatus('people_cards',card),'retired');assert.equal(ui.toggleStatus('people_cards',{...card,status:'retired'}),'confirmed');
 assert.equal(ui.toggleStatus('knowledge_items',know),'disabled');assert.equal(ui.toggleStatus('knowledge_items',{...know,status:'stale'}),'active');
});
test('restore sends id, expected_version and restore_version only',async()=>{
 const calls=[];const m=new ui.LibraryModel(async(p,b)=>{calls.push([p,b]);return {version:5};});
 await m.restore('/db/knowledge','k_0123456789ab',4,1);
 assert.deepEqual(calls,[['/db/knowledge',{id:'k_0123456789ab',expected_version:4,restore_version:1}]]);
});
test('buildFields: edit keeps kind, person and untouched times; create omits status and ids',()=>{
 const start=Date.UTC(2026,10,13,1,2,3,456);
 const mem={memory_id:'e_0123456789ab',kind:'told',title:'旧',statement:'s',subjects:['owner'],topics:[],when:{type:'point',start_ms:start,end_ms:null},status:'active',version:2,scope_id:'office',source:{kind:'owner',refs:[]}};
 const f=ui.buildFields('memory_items',{kind:'event',title:' 新 ',statement:'s',subjects:['owner'],topics:[],when_type:'point',start:ui.toLocalInput(start),end:''},mem);
 assert.equal(f.kind,'told');assert.equal(f.title,'新');assert.equal(f.when.start_ms,start,'unchanged minute keeps the exact stored ms');assert.equal(f.status,'active');
 for(const k of SERVER_ONLY)assert.ok(!(k in f),k);
 const n=ui.buildFields('memory_items',{kind:'event',title:'事',statement:'内容',subjects:[],topics:['k_0123456789ab'],when_type:'span',start:'2026-11-13T10:00',end:'2026-11-13T11:30'});
 assert.equal(n.when.type,'span');assert.equal(n.when.end_ms-n.when.start_ms,90*60000);assert.ok(!('status' in n));assert.deepEqual(n.topics,['k_0123456789ab']);
 const c=ui.buildFields('people_cards',{display:'人物乙',kind:'owner',aliases:'乙，小乙, 阿乙',tease:'ok',quote:'no'},card);
 assert.equal(c.kind,'regular');assert.deepEqual(c.aliases,['乙','小乙','阿乙']);assert.deepEqual(c.links,{voice_names:['v'],face_names:[]});assert.deepEqual(c.treatment,{tease:'ok',quote:'no'});
 const nn=ui.buildFields('people_notes',{person_id:'p_0123456789ab',kind:'boundary',text:' 别提体重 '});
 assert.deepEqual(nn,{person_id:'p_0123456789ab',kind:'boundary',text:'别提体重'});
 const k=ui.buildFields('knowledge_items',{kind:'term',name:'行话',aliases:'',heard_as:'航话\n杭话',statement:'',subjects:[],valid_from:'2026-11-01',valid_to:''});
 assert.deepEqual(k.heard_as,['航话','杭话']);assert.equal(typeof k.when.valid_from,'number');assert.equal(k.when.valid_to,null);
});
test('request keeps the HTTP status and reads plain-text errors from the monitor',async()=>{
 const old=globalThis.fetch;
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({error:'已被改过，请刷新后重试'}),{status:409});
  await assert.rejects(ui.request('/db/memory',{fields:{}}),e=>e.status===409&&/已被改过/.test(e.message));
  globalThis.fetch=async()=>new Response('情景服务连接中',{status:503});
  await assert.rejects(ui.request('/db/overview'),e=>e.status===503&&e.message==='情景服务连接中');
  let seen;globalThis.fetch=async(u,o)=>{seen=[u,o];return new Response('{"ok":1}');};
  assert.deepEqual(await ui.request('/db/person',{fields:{}}),{ok:1});assert.equal(seen[0],'/api/mind/db/person');assert.equal(seen[1].method,'POST');
 }finally{globalThis.fetch=old;}
});
test('list filters become a query string with paging of 20',async()=>{
 const paths=[];const m=new ui.LibraryModel(async p=>{paths.push(p);return {items:[],total:0,offset:0,limit:20};});
 await m.memories({kind:'event',person:'',q:'山',offset:40});await m.knowledge({});
 assert.equal(paths[0],'/db/memories?kind=event&q=%E5%B1%B1&offset=40&limit=20');assert.equal(paths[1],'/db/knowledge?offset=0&limit=20');
});
test('hash routes round-trip',()=>{
 assert.deepEqual(ui.parseHash(''),{view:'people'});
 assert.deepEqual(ui.parseHash('#person=p_0123456789ab'),{view:'person',id:'p_0123456789ab'});
 assert.deepEqual(ui.parseHash('#item=knowledge_items:k_0123456789ab'),{view:'item',c:'knowledge_items',id:'k_0123456789ab'});
 assert.deepEqual(ui.parseHash('#memories'),{view:'memories'});assert.deepEqual(ui.parseHash('#knowledge'),{view:'knowledge'});
 assert.deepEqual(ui.parseHash('#item=bogus:x'),{view:'people'});assert.deepEqual(ui.parseHash('#person=<b>'),{view:'people'});
});
test('disabled rows sort last and grey out; the switch hides them',()=>{
 const notes=[{note_id:'n_00000000000a',kind:'trait',text:'停了的',status:'disabled',version:2},{note_id:'n_00000000000b',kind:'trait',text:'在用的',status:'active',version:1}];
 const p={card:{...card},versions:[],notes,memories:[],knowledge:[]};
 const h=ui.personHtml(p);
 assert.ok(h.indexOf('在用的')<h.indexOf('停了的'));assert.match(h,/class="db-item off"[^>]*data-id="n_00000000000a"/);assert.match(h,/重新启用/);
 assert.doesNotMatch(ui.personHtml(p,{showOff:false}),/停了的/);
});
test('add-person form offers 主人 only while there is no owner card',()=>{
 assert.match(ui.peopleHtml([card]),/<option value="owner"/);
 assert.doesNotMatch(ui.peopleHtml([card,{...card,person_id:'owner',kind:'owner',display:'主人'}]),/<option value="owner"/);
});
test('person page has the bulk entry box and shows the save result with failed lines kept',()=>{
 const p={card:{...card},versions:[],notes:[],memories:[],knowledge:[]};
 const h=ui.personHtml(p,{bulk:{saved:3,failed:[{line:'<坏>',error:'超过 200 字'}],kind:'relation'}});
 for(const t of ['性格','稳定事实','喜好','不能碰的','和主人的关系','每行一条，最多 200 字','存了 3 条','超过 200 字'])assert.match(h,new RegExp(t));
 assert.match(h,/<textarea[^>]*name="text"[^>]*>&lt;坏&gt;<\/textarea>/);assert.match(h,/<option value="relation" selected>/);
});
test('edit form is prefilled, shows kind read-only and has a reason box',()=>{
 const p={card:{...card},versions:[],notes:[{note_id:'n_0123456789ab',person_id:'p_0123456789ab',kind:'fact',text:'做设计的',status:'active',version:1}],memories:[],knowledge:[]};
 const h=ui.personHtml(p,{editing:'people_notes:n_0123456789ab'});
 assert.match(h,/>做设计的<\/textarea>/);assert.match(h,/稳定事实（不能改）/);assert.match(h,/name="reason"[^>]*maxlength="200"/);
 const k=ui.knowledgeHtml({items:[know],total:1,offset:0,limit:20},{editing:'knowledge_items:k_0123456789ab'});
 assert.match(k,/value="登山团体"/);assert.match(k,/长期任务（不能改）/);
});
test('no delete button anywhere and no inline styles or handlers',()=>{
 const p={card:{...card},versions:[],notes:[],memories:[],knowledge:[]};
 const all=[ui.peopleHtml([card]),ui.personHtml(p),ui.memoriesHtml({items:[],total:0,offset:0,limit:20}),ui.knowledgeHtml({items:[know],total:1,offset:0,limit:20}),
  ui.historyHtml({versions:[know],owner_original:know,children:[]}),ui.voiceLabelsHtml({voice_labels:{total:0}})].join('');
 assert.doesNotMatch(all,/删除|delete/i);assert.doesNotMatch(all,/style="|\son[a-z]+=/);
 assert.match(ui.voiceLabelsHtml({voice_labels:{total:0}}),/0 条[\s\S]*第 4 期的每日回顾页会写这里/);
});
test('commitment knowledge opens to the memories hanging under it',()=>{
 const h=ui.historyHtml({versions:[know],owner_original:know,children:[{memory_id:'e_0123456789ab',kind:'event',title:'第一次爬山',statement:'s',subjects:[],when:{type:'point',start_ms:1}}]});
 assert.match(h,/挂在下面的记忆/);assert.match(h,/第一次爬山/);
});
test('mount shows a read failure instead of a blank page',async()=>{
 const root={innerHTML:'',addEventListener(){}};
 const win={location:{hash:'#memories'},addEventListener(){}};
 await ui.mount(root,new ui.LibraryModel(async()=>{throw Error('情景服务连接中');}),win);
 assert.match(root.innerHTML,/读取失败：情景服务连接中/);
 for(const t of ['人物','记忆','知识','现有日常记忆','声纹标注'])assert.match(root.innerHTML,new RegExp(t));
});
test('existing daily memories tab lists the first 10 read-only',async()=>{
 const paths=[];const root={innerHTML:'',addEventListener(){}};
 const req=async p=>{paths.push(p);if(p==='/memories?offset=0&limit=10')return {items:[{memory_id:'m'+'a'.repeat(24),kind:'routine',statement:'下午喝咖啡',status:'known',version:2}],total:1,offset:0,limit:10};
  if(p==='/db/overview')return {collections:{},voice_labels:{total:0},existing:{mind_memories:1}};throw Error('x');};
 await ui.mount(root,new ui.LibraryModel(req),{location:{hash:'#existing'},addEventListener(){}});
 assert.ok(paths.includes('/memories?offset=0&limit=10'));assert.match(root.innerHTML,/下午喝咖啡/);assert.match(root.innerHTML,/说话风格 \/ 场景 \/ 长期记忆/);
});

test('abandoned edit drafts are dropped on navigation and when another edit opens; create drafts stay',async()=>{
 const tick=()=>new Promise(r=>setTimeout(r,5));const id='n_0123456789ab',L={};const win={location:{hash:'#person=owner'},addEventListener(e,f){L[e]=f;}};
 const rootL={};const root={innerHTML:'',addEventListener(e,f){rootL[e]=f;}};
 const note={note_id:id,person_id:'owner',kind:'trait',text:'旧文字',status:'active',version:1,origin:'owner',changed_at:1};
 const req=async p=>{if(p==='/db/overview')return {collections:{},voice_labels:{total:0},existing:{}};
  if(p.startsWith('/db/person?'))return {card:{person_id:'owner',kind:'owner',display:'主人',version:1,status:'active',aliases:[]},notes:[note],memories:[],knowledge:[],versions:[]};
  if(p.startsWith('/db/choices'))return {people:[],knowledge:[]};return {};};
 await ui.mount(root,new ui.LibraryModel(req),win);
 const art={dataset:{c:'people_notes',id}};
 const click=act=>rootL.click({target:{closest:s=>s==='button[data-act]'?{dataset:{act},disabled:false,closest:q=>q==='[data-c]'?art:null}:null}});
 const type=(form,text)=>rootL.input({target:{closest:()=>({dataset:{form},querySelectorAll:()=>[{name:'text',value:text}]})}});
 click('edit');type('edit:people_notes:'+id,'没保存的草稿');
 win.location.hash='#people';L.hashchange();win.location.hash='#person=owner';L.hashchange();await tick();
 click('edit');
 assert.doesNotMatch(root.innerHTML,/没保存的草稿/);assert.match(root.innerHTML,/旧文字/);
 type('edit:people_notes:'+id,'又一份草稿');type('bulk:owner','批量里的字');
 L.hashchange();await tick();click('edit');
 assert.doesNotMatch(root.innerHTML,/又一份草稿/);
});

// ---- the page itself ----
test('db.html loads only /db.css and /db-ui.js, no inline script or style, and links back',()=>{
 const h=page('db.html');
 assert.deepEqual([...h.matchAll(/(?:src|href)="(\/[^"]*\.(?:js|css))"/g)].map(m=>m[1]).sort(),['/db-ui.js','/db.css']);
 assert.doesNotMatch(h,/<script>|<script(?![^>]*src=)[^>]*>|style="|\son[a-z]+=/);
 assert.match(h,/<h1>hello 库<\/h1>/);assert.match(h,/<a href="\/mind.html">← 情景<\/a>/);assert.match(h,/<a href="\/">观察台<\/a>/);assert.match(h,/<main id="db-root"><\/main>/);
});
test('mind page and monitor home link to the library page',()=>{
 assert.match(page('mind.html'),/<a href="\/">← 观察台<\/a>\s*<a href="\/db.html">库<\/a>/);
 assert.match(page('index.html'),/href="\/mind.html"[^>]*>情景<\/a><a[^>]*href="\/db.html"[^>]*>库<\/a>/);
});
