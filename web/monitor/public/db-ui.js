// hello 库 (/db.html): the owner adds, edits, disables / re-enables, reads the history of and restores
// people cards, people notes, memories and knowledge (server/mind/library.py, reached as /api/mind/db/*).
// One module in four parts: constants & helpers · model · html (strings only, stored text always via esc) · mount.
// No imports: tests load this file from a data: URL, where absolute imports do not resolve.

// ======================= constants & helpers =======================
export const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=x=>x===undefined?undefined:JSON.parse(JSON.stringify(x));
const CONFLICT='已被改过，请刷新后重试';
const NOTICE='这些内容只在服务器数据库里，不进仓库；现在没有任何提示在读它们。没有删除，只能停用。';
const COLL={people_cards:{key:'person_id',path:'/db/person',title:'人物卡'},people_notes:{key:'note_id',path:'/db/person/note',title:'人物条目'},
 memory_items:{key:'memory_id',path:'/db/memory',title:'记忆'},knowledge_items:{key:'kid',path:'/db/knowledge',title:'知识'}};
// Page-writable fields, mirroring library.py _card / _note / _memory / _knowledge (and its restore table).
const WRITABLE={people_cards:['kind','display','aliases','links','treatment','scope_ids','status'],
 people_notes:['person_id','kind','text','status','observed_at'],
 memory_items:['kind','title','statement','subjects','topics','when','status'],
 knowledge_items:['kind','name','aliases','heard_as','statement','subjects','when','scope_ids','status']};
const NESTED={people_cards:{links:['voice_names','face_names'],treatment:['tease','quote']},
 memory_items:{when:['type','start_ms','end_ms']},knowledge_items:{when:['valid_from','valid_to']}};
const ON={people_cards:'confirmed',people_notes:'active',memory_items:'active',knowledge_items:'active'};
const OFF={people_cards:'retired',people_notes:'disabled',memory_items:'disabled',knowledge_items:'disabled'};
const PEOPLE_KIND={owner:'主人',regular:'常见的人',family:'家人',visitor:'访客',unknown:'不认识的人'};
const TEASE={words_only:'只接他说的话，不评价他本人',ok:'可以开他玩笑',never:'不开他玩笑'};
const QUOTE={gist:'只提意思',ok:'可以引用原话',no:'不引用'};
const NOTE_KIND={trait:'性格',fact:'稳定事实',preference:'喜好',boundary:'不能碰的',relation:'和主人的关系'};
const MEM_KIND={event:'发生过的事',told:'被告诉的情况',feeling:'心事'};
const KNOW_KIND={place:'地点',work:'工作背景、项目',commitment:'长期任务',life:'生活中的事',term:'行话',self:'hello 自己',other:'其他'};
const STATUS={confirmed:'在用',active:'在用',retired:'已退场',disabled:'已停用',stale:'已过时',candidate:'候选',proposed:'提议中',merged:'已合并'};
const ORIGIN={owner:'主人写的',fused:'融合',import:'导入'};
const SOURCE={owner:'主人写的',settings_import:'从设置导入',m12_name:'M12 名字',conversation:'对话',companion:'陪伴',rule:'规则',extracted:'自动提取'};
const EXISTING={candidate:'待积累',known:'已认识',review:'待复核',disabled:'已停用'};
const FIXED_SUBJECTS={owner:'主人',hello:'hello',room:'房间'};
const ID='(?:owner|[pnek]_[a-f0-9]{12})';

const statusLabel=(c,s)=>c==='people_cards'&&s==='retired'?'已停用':STATUS[s]||s||'—';
const isOff=(c,doc)=>doc.status!==ON[c];
export const toggleStatus=(c,doc)=>isOff(c,doc)?ON[c]:OFF[c];
export function collOf(doc){
 if(!doc)return null;
 for(const c of ['people_notes','memory_items','knowledge_items','people_cards'])if(COLL[c].key in doc)return c;
 return null;
}
const pad=n=>String(n).padStart(2,'0');
export const localTime=t=>typeof t==='number'?new Date(t).toLocaleString('zh-CN',{hour12:false}):'时间未记录';
const localDate=t=>typeof t==='number'?new Date(t).toLocaleDateString('zh-CN'):'';
export function toLocalInput(ms){if(typeof ms!=='number')return '';const d=new Date(ms);return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;}
const toDateInput=ms=>toLocalInput(ms).slice(0,10);
const parseTime=s=>{if(!s)return null;const t=new Date(s).getTime();return Number.isFinite(t)?t:null;};
const fromDateInput=s=>parseTime(s?s+'T00:00':'');
// An unchanged time input keeps the exact stored value (inputs only show minutes / days).
const keepTime=(input,old,fmt,parse)=>typeof old==='number'&&input===fmt(old)?old:parse(input);
const splitList=s=>Array.isArray(s)?s:String(s??'').split(/[,，、;；\n]+/).map(x=>x.trim()).filter(Boolean);
const arr=x=>Array.isArray(x)?x:x?[x]:[];
const trim=s=>String(s??'').trim();

/** Only the page-writable fields of one version document, deep-copied; never any server field. */
export function editable(c,doc){
 const out={};
 for(const k of WRITABLE[c]){
  if(!(k in doc))continue;
  let v=copy(doc[k]);const sub=NESTED[c]?.[k];
  if(sub&&v&&typeof v==='object')v=Object.fromEntries(sub.filter(s=>s in v).map(s=>[s,v[s]]));
  out[k]=v;
 }
 return out;
}
/** Form values -> fields for one collection. With `base` (editing) kind and person_id come from base. */
export function buildFields(c,v,base=null){
 const b=base?editable(c,base):{};
 if(c==='people_cards')return {...b,kind:base?b.kind:v.kind,display:trim(v.display),aliases:splitList(v.aliases),treatment:{tease:v.tease,quote:v.quote}};
 if(c==='people_notes')return {...b,person_id:base?b.person_id:v.person_id,kind:base?b.kind:v.kind,text:trim(v.text)};
 if(c==='memory_items'){
  const type=v.when_type==='span'?'span':'point';
  return {...b,kind:base?b.kind:v.kind,title:trim(v.title),statement:trim(v.statement),subjects:arr(v.subjects),topics:arr(v.topics),
   when:{type,start_ms:keepTime(v.start,b.when?.start_ms,toLocalInput,parseTime),end_ms:type==='span'?keepTime(v.end,b.when?.end_ms,toLocalInput,parseTime):null}};
 }
 return {...b,kind:base?b.kind:v.kind,name:trim(v.name),aliases:splitList(v.aliases),heard_as:splitList(v.heard_as),statement:trim(v.statement),subjects:arr(v.subjects),
  when:{valid_from:keepTime(v.valid_from,b.when?.valid_from,toDateInput,fromDateInput),valid_to:keepTime(v.valid_to,b.when?.valid_to,toDateInput,fromDateInput)}};
}
/** '#person=p_…' etc. -> {view, id?, c?}; anything unknown is the people list. */
export function parseHash(h){
 let s=String(h||'').replace(/^#/,'');try{s=decodeURIComponent(s);}catch{return {view:'people'};}
 if(['people','memories','knowledge','existing','voice'].includes(s))return {view:s};
 let m=new RegExp(`^person=(owner|p_[a-f0-9]{12})$`).exec(s);if(m)return {view:'person',id:m[1]};
 m=new RegExp(`^item=(people_cards|people_notes|memory_items|knowledge_items):(${ID})$`).exec(s);if(m)return {view:'item',c:m[1],id:m[2]};
 return {view:'people'};
}

// ======================= model =======================
export async function request(path,body){
 const r=await fetch('/api/mind'+path,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(30000)});
 const text=await r.text();let data=null;try{data=JSON.parse(text);}catch{}
 if(!r.ok){const e=Error(data?.error||text||'请求失败');e.status=r.status;throw e;}
 if(data===null)throw Error('返回的内容看不懂');
 return data;
}
const query=(f,keys)=>{const p=new URLSearchParams();for(const k of keys)if(f[k])p.set(k,f[k]);p.set('offset',String(f.offset||0));p.set('limit','20');return p.toString();};
export class LibraryModel{
 constructor(req=request){this.request=req;this.seq={};}
 // Each read has its own counter: a response that arrives after a newer one of the same read returns false.
 async read(name,path){const seq=this.seq[name]=(this.seq[name]||0)+1;const r=await this.request(path);return seq===this.seq[name]?r:false;}
 overview(){return this.read('overview','/db/overview');}
 people(){return this.read('people','/db/people');}
 person(id){return this.read('person','/db/person?id='+encodeURIComponent(id));}
 memories(f={}){return this.read('memories','/db/memories?'+query(f,['kind','person','q']));}
 knowledge(f={}){return this.read('knowledge','/db/knowledge?'+query(f,['kind','person','q']));}
 item(c,id){return this.read('item',`/db/item?c=${encodeURIComponent(c)}&id=${encodeURIComponent(id)}`);}
 existing(){return this.read('existing','/memories?offset=0&limit=10');}
 async choices(){
  const seq=this.seq.choices=(this.seq.choices||0)+1;
  const [p,k]=await Promise.all([this.request('/db/people'),this.request('/db/knowledge?offset=0&limit=100')]);
  return seq===this.seq.choices?{people:p.items||[],knowledge:k.items||[]}:false;
 }
 /** Create (no id) or append a version; resolves to the version document the server wrote. */
 save(path,fields,{id,expected_version,reason}={}){
  const body=id==null?{fields}:{id,expected_version,fields};
  if(reason)body.reason=String(reason).slice(0,200);
  return this.request(path,body);
 }
 restore(path,id,expected_version,restore_version){return this.request(path,{id,expected_version,restore_version});}
 setStatus(c,doc,status,reason){return this.save(COLL[c].path,{...editable(c,doc),status},{id:doc[COLL[c].key],expected_version:doc.version,reason});}
 async addNotes(personId,kind,text){
  const out={saved:0,failed:[]};
  for(const line of String(text??'').split('\n').map(x=>x.trim()).filter(Boolean)){
   if([...line].length>200){out.failed.push({line,error:'超过 200 字'});continue;}
   try{await this.save('/db/person/note',{person_id:personId,kind,text:line});out.saved++;}
   catch(e){out.failed.push({line,error:e.message||'没有存上'});}
  }
  return out;
 }
}

// ======================= html =======================
const opts=(map,sel)=>Object.entries(map).map(([v,l])=>`<option value="${esc(v)}"${v===sel?' selected':''}>${esc(l)}</option>`).join('');
const draft=(o,key,name,dflt)=>{const d=o.drafts?.[key];return d&&name in d?d[name]:dflt;};
const input=(label,name,value,attrs='')=>`<label>${label}<input name="${name}" value="${esc(value)}"${attrs?' '+attrs:''}></label>`;
const area=(label,name,value,attrs='')=>`<label>${label}<textarea name="${name}"${attrs?' '+attrs:''}>${esc(value)}</textarea></label>`;
const select=(label,name,map,sel)=>`<label>${label}<select name="${name}">${opts(map,sel)}</select></label>`;
const checks=(label,name,map,sel)=>`<fieldset><legend>${label}</legend>${Object.keys(map).length?Object.entries(map).map(([v,l])=>`<label class="chk"><input type="checkbox" name="${name}" value="${esc(v)}"${sel.includes(v)?' checked':''}> ${esc(l)}</label>`).join(''):'<span class="alt">还没有可选的</span>'}</fieldset>`;
const fixed=(label,text)=>`<p class="db-ro"><span class="alt">${label}</span> ${esc(text)}（不能改）</p>`;
const reasonBox=(o,key)=>input('为什么改（可空）','reason',draft(o,key,'reason',''),'maxlength="200"');
const buttons=editing=>`<div class="db-actions"><button data-act="${editing?'save-edit':'create'}">${editing?'保存':'加上'}</button>${editing?'<button data-act="cancel">取消</button>':''}</div>`;
const showOffSwitch=o=>`<label class="chk db-switch"><input type="checkbox" data-show-off${o.showOff===false?'':' checked'}> 显示已停用</label>`;
const meta=(c,d,extra='')=>`<p class="meta">${extra}${extra?' · ':''}第 ${esc(d.version??'—')} 版${d.changed_at!=null?' · '+esc(localTime(d.changed_at)):''}${isOff(c,d)?' · <b>'+esc(statusLabel(c,d.status))+'</b>':''}</p>`;
function names(o){
 const m={...FIXED_SUBJECTS};
 for(const p of o.people||[])if(p.person_id!=='owner')m[p.person_id]=p.display+(p.status==='retired'?'（已停用）':'');
 return m;
}
const nameList=(o,ids)=>{const m=names(o);return (ids||[]).map(i=>esc(m[i]||i)).join('、')||'—';};
const topicList=(o,ids)=>{const m=Object.fromEntries((o.knowledge||[]).map(k=>[k.kid,k.name]));return (ids||[]).map(i=>esc(m[i]||i)).join('、');};
function withExtra(map,sel){const m={...map};for(const s of sel)if(!(s in m))m[s]=s;return m;}
function sorted(c,rows,o){
 const list=[...(rows||[])].filter(r=>o.showOff!==false||!isOff(c,r));
 return list.sort((a,b)=>isOff(c,a)-isOff(c,b));   // stable: keeps the server's order inside each group
}
function whenLabel(w){
 if(!w)return '时间未记录';
 return w.type==='span'?`${esc(localTime(w.start_ms))} – ${esc(localTime(w.end_ms))}`:esc(localTime(w.start_ms));
}
function validLabel(w){
 if(!w||(w.valid_from==null&&w.valid_to==null))return '一直成立';
 return `${w.valid_from!=null?esc(localDate(w.valid_from)):'早先'} 起${w.valid_to!=null?'，到 '+esc(localDate(w.valid_to)):'，仍然成立'}`;
}

// ---- one row per item: body + 改 / 停用·重新启用 / 历史, or the edit form while editing ----
function row(c,doc,body,o){
 const id=doc[COLL[c].key],key=`${c}:${id}`;
 const act=`<div class="db-actions"><button data-act="edit">改</button><button data-act="toggle">${isOff(c,doc)?'重新启用':'停用'}</button>${o.noHistory?'':'<button data-act="history">历史</button>'}${c==='knowledge_items'&&doc.kind==='commitment'&&!o.noHistory?'<button data-act="history">下面的记忆</button>':''}</div>`;
 return `<article class="db-item${isOff(c,doc)?' off':''}" data-c="${c}" data-id="${esc(id)}">${o.editing===key?editForm(c,doc,o):body+act}</article>`;
}
function bodyOf(c,d,o){
 if(c==='people_cards'){
  const t=d.treatment||{},l=d.links||{};
  return `<p><b>${esc(d.display)}</b> · ${esc(PEOPLE_KIND[d.kind]||d.kind)}</p><p>开玩笑：${esc(TEASE[t.tease]||t.tease||'—')} · 引用原话：${esc(QUOTE[t.quote]||t.quote||'—')}</p>`+
   `${d.aliases?.length?`<p>别名：${d.aliases.map(esc).join('、')}</p>`:''}${l.voice_names?.length||l.face_names?.length?`<p class="alt">声纹名：${(l.voice_names||[]).map(esc).join('、')||'—'} · 脸名：${(l.face_names||[]).map(esc).join('、')||'—'}</p>`:''}`+meta(c,d);
 }
 if(c==='people_notes')return `<p><span class="tag">${esc(NOTE_KIND[d.kind]||d.kind)}</span> ${esc(d.text)}</p>`+meta(c,d,d.origin?esc(ORIGIN[d.origin]||d.origin):'');
 if(c==='memory_items'){
  const topics=topicList(o,d.topics);
  return `<p><span class="tag">${esc(MEM_KIND[d.kind]||d.kind)}</span> <b>${esc(d.title)}</b></p><p>${esc(d.statement)}</p>`+
   meta(c,d,`${whenLabel(d.when)} · 涉及：${nameList(o,d.subjects)}${topics?' · 知识：'+topics:''} · 来源：${esc(SOURCE[d.source?.kind]||d.source?.kind||'—')}`);
 }
 return `<p><span class="tag">${esc(KNOW_KIND[d.kind]||d.kind)}</span> <b>${esc(d.name)}</b>${d.aliases?.length?' · 也叫 '+d.aliases.map(esc).join('、'):''}${d.heard_as?.length?' · 常被听成 '+d.heard_as.map(esc).join('、'):''}</p>`+
  `${d.statement?`<p>${esc(d.statement)}</p>`:''}`+meta(c,d,`${validLabel(d.when)} · 涉及：${nameList(o,d.subjects)}${d.origin?' · 来历：'+esc(ORIGIN[d.origin]||d.origin):''}`);
}

// ---- forms: create (base = null) and edit (base = current version, prefilled) ----
function cardForm(key,base,o){
 const d=base?editable('people_cards',base):{kind:'regular',display:'',aliases:[],treatment:{tease:'words_only',quote:'gist'}};
 const g=(n,x)=>draft(o,key,n,x),t=d.treatment||{};
 const kinds=o.hasOwner?Object.fromEntries(Object.entries(PEOPLE_KIND).filter(([k])=>k!=='owner')):PEOPLE_KIND;   // only one owner card
 return `<div class="db-form" data-form="${esc(key)}">${base?fixed('种类',PEOPLE_KIND[d.kind]||d.kind):select('种类','kind',kinds,g('kind',d.kind))}`+
  input('称呼','display',g('display',d.display),'maxlength="40"')+input('别名（逗号隔开，可空）','aliases',g('aliases',(d.aliases||[]).join('，')))+
  select('开玩笑','tease',TEASE,g('tease',t.tease||'words_only'))+select('引用原话','quote',QUOTE,g('quote',t.quote||'gist'))+reasonBox(o,key)+buttons(!!base)+'</div>';
}
function noteForm(key,base,o){
 const g=(n,x)=>draft(o,key,n,x);
 return `<div class="db-form" data-form="${esc(key)}">${fixed('种类',NOTE_KIND[base.kind]||base.kind)}${fixed('是谁的',names(o)[base.person_id]||base.person_id)}`+
  area('内容','text',g('text',base.text),'rows="3" maxlength="200"')+reasonBox(o,key)+buttons(true)+'</div>';
}
function memoryForm(key,base,o){
 const d=base?editable('memory_items',base):{kind:'event',title:'',statement:'',subjects:[],topics:[],when:{type:'point',start_ms:null,end_ms:null}};
 const g=(n,x)=>draft(o,key,n,x),w=d.when||{},subj=arr(g('subjects',d.subjects||[])),top=arr(g('topics',d.topics||[]));
 return `<div class="db-form" data-form="${esc(key)}">${base?fixed('种类',MEM_KIND[d.kind]||d.kind):select('种类','kind',MEM_KIND,g('kind',d.kind))}`+
  input('标题','title',g('title',d.title),'maxlength="30"')+area('内容','statement',g('statement',d.statement),'rows="3" maxlength="300"')+
  checks('涉及谁','subjects',withExtra(names(o),subj),subj)+checks('关联的知识','topics',withExtra(Object.fromEntries((o.knowledge||[]).map(k=>[k.kid,k.name])),top),top)+
  `<div class="db-row">${select('时间','when_type',{point:'时间点',span:'时间段'},g('when_type',w.type||'point'))}${input('开始','start',g('start',toLocalInput(w.start_ms)),'type="datetime-local"')}${input('结束（时间段才填）','end',g('end',toLocalInput(w.end_ms)),'type="datetime-local"')}</div>`+
  reasonBox(o,key)+buttons(!!base)+'</div>';
}
function knowledgeForm(key,base,o){
 const d=base?editable('knowledge_items',base):{kind:'work',name:'',aliases:[],heard_as:[],statement:'',subjects:[],when:{valid_from:null,valid_to:null}};
 const g=(n,x)=>draft(o,key,n,x),w=d.when||{},subj=arr(g('subjects',d.subjects||[]));
 return `<div class="db-form" data-form="${esc(key)}">${base?fixed('种类',KNOW_KIND[d.kind]||d.kind):select('种类','kind',KNOW_KIND,g('kind',d.kind))}`+
  input('名称','name',g('name',d.name),'maxlength="40"')+input('别名（逗号隔开，可空）','aliases',g('aliases',(d.aliases||[]).join('，')))+
  input('常被听成（行话用，逗号隔开）','heard_as',g('heard_as',(d.heard_as||[]).join('，')))+area('内容','statement',g('statement',d.statement),'rows="3" maxlength="400"')+
  checks('涉及谁','subjects',withExtra(names(o),subj),subj)+
  `<div class="db-row">${input('从哪天起（可空）','valid_from',g('valid_from',toDateInput(w.valid_from)),'type="date"')}${input('到哪天（空表示仍然成立）','valid_to',g('valid_to',toDateInput(w.valid_to)),'type="date"')}</div>`+
  reasonBox(o,key)+buttons(!!base)+'</div>';
}
function editForm(c,doc,o){
 const key=`edit:${c}:${doc[COLL[c].key]}`;
 return `<h3>改${COLL[c].title}</h3>`+({people_cards:cardForm,people_notes:noteForm,memory_items:memoryForm,knowledge_items:knowledgeForm}[c])(key,doc,o);
}

// ---- views ----
export function overviewHtml(o){
 const n=c=>esc(o?.collections?.[c]?.total??0);
 return `<p class="db-counts">人物 <b>${n('people_cards')}</b> · 人物条目 <b>${n('people_notes')}</b> · 记忆 <b>${n('memory_items')}</b> · 知识 <b>${n('knowledge_items')}</b> · 声纹标注 <b>${esc(o?.voice_labels?.total??0)}</b> · 现有日常记忆 <b>${esc(o?.existing?.mind_memories??0)}</b></p><p class="note">${NOTICE}</p>`;
}
export function peopleHtml(items,o={}){
 const rows=sorted('people_cards',items,o);
 return `<section><div class="db-tools"><h2>人物</h2>${showOffSwitch(o)}</div>`+
  (rows.length?rows.map(p=>row('people_cards',p,`<p><a href="#person=${esc(p.person_id)}"><b>${esc(p.display)}</b></a> · ${esc(PEOPLE_KIND[p.kind]||p.kind)} · ${esc(p.notes??0)} 条</p>${isOff('people_cards',p)?'<p class="meta"><b>已停用</b></p>':''}`,o)).join('')
   :'<p class="alt">还没有人物。先加主人（种类选"主人"）。</p>')+
  `</section><section><h2>加一个人</h2>${cardForm('new:card',null,{...o,hasOwner:(items||[]).some(p=>p.person_id==='owner')})}</section>`;
}
function bulkHtml(pid,o){
 const key='bulk:'+pid,b=o.bulk;
 const text=draft(o,key,'text',b?b.failed.map(f=>f.line).join('\n'):'');
 return `<section class="db-bulk"><h3>一次加多条</h3><div class="db-form" data-form="${esc(key)}">${select('种类','kind',NOTE_KIND,draft(o,key,'kind',b?.kind||'trait'))}`+
  `<label>内容<textarea name="text" rows="6" placeholder="每行一条，最多 200 字">${esc(text)}</textarea></label><div class="db-actions"><button data-act="bulk">保存</button></div>`+
  (b?`<p class="db-result">存了 ${esc(b.saved)} 条${b.failed.length?`，${b.failed.length} 条没存上，留在框里：`:''}</p>${b.failed.length?`<ul class="db-failed">${b.failed.map(f=>`<li>${esc(f.line)} — ${esc(f.error)}</li>`).join('')}</ul>`:''}`:'')+
  '</div></section>';
}
export function personHtml(p,o={}){
 const c=p.card,pid=c.person_id,notes=sorted('people_notes',p.notes,o),mems=sorted('memory_items',p.memories,o),know=sorted('knowledge_items',p.knowledge,o);
 return `<section><h2>${esc(c.display)}</h2>${row('people_cards',c,bodyOf('people_cards',c,o),o)}</section>`+
  `<section><div class="db-tools"><h3>条目</h3>${showOffSwitch(o)}</div>${notes.length?notes.map(n=>row('people_notes',n,bodyOf('people_notes',n,o),o)).join(''):'<p class="alt">还没有条目。</p>'}</section>`+
  bulkHtml(pid,o)+
  `<section><h3>和他有关的记忆</h3>${mems.length?mems.map(m=>row('memory_items',m,bodyOf('memory_items',m,o),o)).join(''):'<p class="alt">没有。</p>'}</section>`+
  `<section><h3>和他有关的知识</h3>${know.length?know.map(k=>row('knowledge_items',k,bodyOf('knowledge_items',k,o),o)).join(''):'<p class="alt">没有。</p>'}</section>`;
}
function pager(page){
 const off=Number(page.offset)||0,lim=Number(page.limit)||20,total=Number(page.total)||0;
 return `<div class="db-pager"><button data-act="page" data-offset="${Math.max(0,off-lim)}"${off===0?' disabled':''}>上一页</button><span>${total?off+1:0}–${Math.min(off+lim,total)} / ${esc(total)}</span><button data-act="page" data-offset="${off+lim}"${off+lim>=total?' disabled':''}>下一页</button></div>`;
}
function filterTools(view,o,kinds,withPerson){
 const f=o.filters||{};
 const people={'':'所有人',owner:'主人',...Object.fromEntries((o.people||[]).filter(p=>p.person_id!=='owner').map(p=>[p.person_id,p.display]))};
 return `<div class="db-tools db-form" data-form="filters:${view}"><select data-filter="kind" name="kind" aria-label="种类">${opts({'':'全部种类',...kinds},f.kind||'')}</select>`+
  (withPerson?`<select data-filter="person" name="person" aria-label="涉及谁">${opts(people,f.person||'')}</select>`:'')+
  `<input name="q" value="${esc(f.q||'')}" maxlength="40" placeholder="搜索标题、内容、名称" aria-label="搜索"><button data-act="search">搜索</button>${showOffSwitch(o)}</div>`;
}
export function memoriesHtml(page,o={}){
 const rows=sorted('memory_items',page.items,o);
 return `<section><h2>记忆</h2>${filterTools('memories',o,MEM_KIND,true)}${rows.length?rows.map(m=>row('memory_items',m,bodyOf('memory_items',m,o),o)).join(''):'<p class="alt">没有符合的记忆。</p>'}${pager(page)}</section>`+
  `<section><h2>加一件事</h2>${memoryForm('new:memory',null,o)}</section>`;
}
export function knowledgeHtml(page,o={}){
 const rows=sorted('knowledge_items',page.items,o);
 return `<section><h2>知识</h2>${filterTools('knowledge',o,KNOW_KIND,false)}${rows.length?rows.map(k=>row('knowledge_items',k,bodyOf('knowledge_items',k,o),o)).join(''):'<p class="alt">没有符合的知识。</p>'}${pager(page)}</section>`+
  `<section><h2>加一条知识</h2>${knowledgeForm('new:knowledge',null,o)}</section>`;
}
function summary(x){
 const parts=[x.display,x.name,x.title,x.text,x.statement].filter(s=>typeof s==='string'&&s).map(esc);
 if(x.treatment)parts.push(`开玩笑：${esc(TEASE[x.treatment.tease]||x.treatment.tease)}；引用：${esc(QUOTE[x.treatment.quote]||x.treatment.quote)}`);
 if(x.aliases?.length)parts.push('也叫 '+x.aliases.map(esc).join('、'));
 if(x.when?.type)parts.push(whenLabel(x.when));
 return parts.join(' — ')||'—';
}
const reasonLabel=r=>r==='restore'?'回到旧版':r?esc(r):'没写原因';
export function historyHtml(item,o={}){
 const v=item.versions||[],head=v[0],orig=item.owner_original,c=o.c||collOf(head);
 const twoCols=orig||c==='people_notes'||c==='knowledge_items';
 let h='';
 if(twoCols){
  h+=`<div class="db-cols"><section><h3>主人原版</h3>${orig?`<p class="meta">第 ${esc(orig.version)} 版</p><p>${summary(orig)}</p>`:'<p class="alt">没有主人写过的版本。</p>'}</section>`+
   `<section><h3>当前版</h3>${head?`<p class="meta">第 ${esc(head.version)} 版${head.origin?' · '+esc(ORIGIN[head.origin]||head.origin):''}</p><p>${summary(head)}</p>`:''}</section></div>`;
  if(orig&&head&&head.version!==orig.version)h+=`<div class="db-actions"><button data-act="restore" data-version="${esc(orig.version)}">回到我的版本</button></div>`;
 }
 h+=`<h3>历次版本</h3><ol class="db-versions">${v.map(x=>`<li><p><b>第 ${esc(x.version)} 版</b> · ${esc(localTime(x.changed_at))} · ${x.actor==='owner'?'主人':esc(x.actor||'—')} · ${reasonLabel(x.reason)}${x.origin?' · '+esc(ORIGIN[x.origin]||x.origin):''}${x.status?' · '+esc(statusLabel(c,x.status)):''}</p><p>${summary(x)}</p>`+
  `${x!==head&&(!x.origin||x.origin==='owner')&&!(orig&&x.version===orig.version)?`<button data-act="restore" data-version="${esc(x.version)}">恢复这一版</button>`:''}</li>`).join('')}</ol>`;
 if(c==='knowledge_items'){
  const kids=item.children||[];
  h+=`<h3>挂在下面的记忆</h3>${kids.length?kids.map(m=>row('memory_items',m,bodyOf('memory_items',m,o),o)).join(''):'<p class="alt">还没有记忆挂在下面（在记忆的"关联的知识"里勾它）。</p>'}`;
 }
 return h;
}
function itemHtml(c,item,o){
 const head=item.versions[0];
 const back=c==='people_cards'?`#person=${head.person_id}`:c==='people_notes'?`#person=${head.person_id}`:c==='memory_items'?'#memories':'#knowledge';
 return `<p><a href="${esc(back)}">← 返回</a></p><h2>${COLL[c].title}的历史</h2>${row(c,head,bodyOf(c,head,o),{...o,noHistory:true})}${historyHtml(item,{...o,c})}`;
}
function existingHtml(page){
 return `<section><h2>现有日常记忆</h2><p class="note">只读，列前 10 条。编辑仍在观察台首页的「说话风格 / 场景 / 长期记忆」设置里。共 ${esc(page.total??0)} 条。</p>`+
  ((page.items||[]).length?page.items.map(m=>`<article class="db-item${m.status==='disabled'?' off':''}"><p><b>${esc(EXISTING[m.status]||m.status)}</b> · 第 ${esc(m.version)} 版</p><p>${esc(m.statement)}</p></article>`).join(''):'<p class="alt">还没有。</p>')+'</section>';
}
export function voiceLabelsHtml(o){
 return `<section><h2>声纹标注</h2><p>${esc(o?.voice_labels?.total??'—')} 条</p><p class="note">第 4 期的每日回顾页会写这里。</p>`+
  '<details><summary>结构说明</summary><ul><li>每条标一句话：指向录音里的会话、语音编号和采样位置，可以直接回到录音。</li><li>判断：是某人 / 不是 / 好几个人 / hello 自己 / 噪声 / 不确定。</li><li>一组一起标时展开成每句一条；只追加，同一句以最后一条为准。</li></ul></details></section>';
}

// ======================= mount =======================
const TABS=[['people','人物'],['memories','记忆'],['knowledge','知识'],['existing','现有日常记忆'],['voice','声纹标注']];
const TAB_OF={person:'people',people_cards:'people',people_notes:'people',memory_items:'memories',knowledge_items:'knowledge'};
const CREATE={'new:card':'people_cards','new:memory':'memory_items','new:knowledge':'knowledge_items'};
function readValues(box){
 const v={};
 for(const el of box.querySelectorAll('[name]')){
  if(el.type==='checkbox'){(v[el.name]??=[]);if(el.checked)v[el.name].push(el.value);}else v[el.name]=el.value;
 }
 return v;
}
export function mount(root,model=new LibraryModel(),win=globalThis){
 const st={route:parseHash(win.location?.hash),seq:0,overview:null,overviewError:'',data:null,error:'',choices:null,msg:'',editing:null,
  showOff:true,drafts:{},bulk:null,busy:false,filters:{memories:{kind:'',person:'',q:'',offset:0},knowledge:{kind:'',q:'',offset:0}}};
 const o=()=>({showOff:st.showOff,editing:st.editing,drafts:st.drafts,bulk:st.bulk,people:st.choices?.people||[],knowledge:st.choices?.knowledge||[],filters:st.filters[st.route.view]});
 function view(){
  if(st.error)return `<p class="error">读取失败：${esc(st.error)}</p><div class="db-actions"><button data-act="reload">再试一次</button></div>`;
  const r=st.route,d=st.data;
  if(r.view==='voice')return voiceLabelsHtml(st.overview);
  if(!d)return '<p class="alt">读取中…</p>';
  if(r.view==='people')return peopleHtml(d.items,o());
  if(r.view==='person')return `<p><a href="#people">← 人物</a></p>${personHtml(d,o())}`;
  if(r.view==='memories')return memoriesHtml(d,o());
  if(r.view==='knowledge')return knowledgeHtml(d,o());
  if(r.view==='item')return itemHtml(r.c,d,o());
  return existingHtml(d);
 }
 function paint(){
  const cur=TAB_OF[st.route.view==='item'?st.route.c:st.route.view]||st.route.view;
  root.innerHTML=`<div class="db-overview">${st.overview?overviewHtml(st.overview):`${st.overviewError?`<p class="error">读取失败：${esc(st.overviewError)}</p>`:'<p class="alt">读取中…</p>'}<p class="note">${NOTICE}</p>`}</div>`+
   `<nav class="db-tabs">${TABS.map(([v,l])=>`<a href="#${v}"${v===cur?' aria-current="page"':''}>${l}</a>`).join('')}</nav>`+
   `<p class="db-msg" role="status">${esc(st.msg)}</p><div class="db-view">${view()}</div>`;
 }
 async function loadOverview(){
  try{const r=await model.overview();if(r!==false){st.overview=r;st.overviewError='';}}catch(e){st.overviewError=e.message;}
  paint();
 }
 async function load(){
  const seq=++st.seq,r=st.route;st.data=null;st.error='';paint();
  let data,ch=false;
  try{
   if(r.view==='people')data=await model.people();
   else if(r.view==='person')[data,ch]=await Promise.all([model.person(r.id),model.choices()]);
   else if(r.view==='memories')[data,ch]=await Promise.all([model.memories(st.filters.memories),model.choices()]);
   else if(r.view==='knowledge')[data,ch]=await Promise.all([model.knowledge(st.filters.knowledge),model.choices()]);
   else if(r.view==='item')[data,ch]=await Promise.all([model.item(r.c,r.id),model.choices()]);
   else if(r.view==='existing')data=await model.existing();
   else data={};
  }catch(e){if(seq===st.seq){st.error=e.message;paint();}return;}
  if(seq!==st.seq||data===false)return;
  if(ch)st.choices=ch;
  st.data=data;paint();
 }
 function findDoc(c,id){
  const d=st.data||{};
  const pool=[d.card,...(d.notes||[]),...(d.memories||[]),...(d.knowledge||[]),...(d.items||[]),...(d.versions||[]).slice(0,1),...(d.children||[])];
  return pool.find(x=>x&&collOf(x)===c&&x[COLL[c].key]===id)||null;
 }
 // One write at a time. Success or 409 re-reads this view; other errors keep the form and its text.
 async function write(fn,draftKey){
  if(st.busy)return;st.busy=true;st.msg='保存中…';paint();
  let reread=true;
  try{st.msg=await fn();if(draftKey)delete st.drafts[draftKey];st.editing=null;}
  catch(e){
   if(e.status===409){st.msg=CONFLICT;if(draftKey)delete st.drafts[draftKey];st.editing=null;}
   else{st.msg='没有完成：'+e.message;reread=false;}
  }finally{st.busy=false;}
  if(reread)await Promise.all([load(),loadOverview()]);else paint();
 }
 function act(b){
  const name=b.dataset.act,art=b.closest('[data-c]'),box=b.closest('[data-form]');
  const c=art?.dataset.c||st.route.c,id=art?.dataset.id||st.route.id,values=box?readValues(box):{};
  const doc=c&&id?findDoc(c,id):null;
  if(name==='edit'&&doc){st.editing=`${c}:${id}`;paint();}
  else if(name==='cancel'){delete st.drafts['edit:'+st.editing];st.editing=null;paint();}
  else if(name==='history')win.location.hash=`item=${c}:${id}`;
  else if(name==='reload'){load();loadOverview();}
  else if(name==='search'){const f=st.filters[st.route.view];if(f){f.q=trim(values.q);f.offset=0;load();}}
  else if(name==='page'){const f=st.filters[st.route.view];if(f){f.offset=Number(b.dataset.offset)||0;load();}}
  else if(name==='save-edit'&&doc)write(async()=>{const r=await model.save(COLL[c].path,buildFields(c,values,doc),{id,expected_version:doc.version,reason:values.reason});return `已保存，第 ${r.version} 版`;},box.dataset.form);
  else if(name==='toggle'&&doc){const s=toggleStatus(c,doc),label=isOff(c,doc)?'重新启用':'停用';write(async()=>{const r=await model.setStatus(c,doc,s,label);return `已${label}，第 ${r.version} 版`;});}
  else if(name==='create'&&box&&CREATE[box.dataset.form]){
   const cc=CREATE[box.dataset.form];
   write(async()=>{const r=await model.save(COLL[cc].path,buildFields(cc,values),{reason:values.reason});if(cc==='people_cards'&&r.person_id)win.location.hash='person='+r.person_id;return '加上了';},box.dataset.form);
  }
  else if(name==='bulk'&&box)write(async()=>{const r=await model.addNotes(st.route.id,values.kind,values.text);st.bulk={...r,kind:values.kind};return `存了 ${r.saved} 条${r.failed.length?`，${r.failed.length} 条没存上`:''}`;},box.dataset.form);
  else if(name==='restore'&&st.data?.versions?.[0]){
   const head=st.data.versions[0],ver=Number(b.dataset.version);
   write(async()=>{const r=await model.restore(COLL[c].path,id,head.version,ver);return `已回到第 ${ver} 版的内容（新的第 ${r.version} 版）`;});
  }
 }
 const keep=t=>{const box=t.closest?.('[data-form]');if(box&&!box.dataset.form.startsWith('filters:'))st.drafts[box.dataset.form]=readValues(box);};
 root.addEventListener('click',e=>{const b=e.target.closest?.('button[data-act]');if(b&&!b.disabled)act(b);});
 root.addEventListener('input',e=>keep(e.target));
 root.addEventListener('change',e=>{
  const t=e.target;
  if(t.matches?.('[data-show-off]')){st.showOff=t.checked;paint();return;}
  const f=st.filters[st.route.view];
  if(t.dataset?.filter&&f){f[t.dataset.filter]=t.value;f.offset=0;load();return;}
  keep(t);
 });
 root.addEventListener('keydown',e=>{
  if(e.key!=='Enter'||e.target.name!=='q')return;
  const b=e.target.closest('[data-form]')?.querySelector('[data-act="search"]');if(b){e.preventDefault();act(b);}
 });
 win.addEventListener?.('hashchange',()=>{st.route=parseHash(win.location.hash);st.editing=null;st.bulk=null;load();});
 return Promise.all([loadOverview(),load()]);
}
// The page (db.html) has <main id="db-root">; tests have no document and mount by hand.
if(typeof document!=='undefined'){const r=document.getElementById('db-root');if(r)mount(r);}
