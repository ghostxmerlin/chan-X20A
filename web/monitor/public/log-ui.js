// 日志 page: hello's run/audit log through mind's read-only /api/mind/log* (server/mind/api.py over
// server/common/hello_log_query.py; record format docs/hello-log.md).
// Layout: categories on the left (what hello was doing: 听 看 想 说 记 控 连 系, counted over the records read), a
// timeline grouped by minute in the middle (Chinese name of each kind, summary under it), the opened record on the
// right. An opened model call shows its prompt: the system prompt as a head line plus only what changed since the same
// kind's previous call, the input as messages, the output pretty-printed. Heartbeats are hidden unless asked.
// The pure functions are exported for log-ui.test.mjs.
export const SVCS=['ear','mind','eye','notes','memory','tts'];
export const STALE_MS=120000;
const escText=t=>String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const esc=t=>escText(t).replace(/"/g,'&quot;');
const two=n=>String(n).padStart(2,'0');
// Beijing time (+08:00, no DST) everywhere, like the server's months and days, whatever zone the Mac is in.
const BJ_MS=8*3600000;
const bj=ms=>new Date(Number(ms)+BJ_MS);                                      // read with the getUTC* getters
export const hms=ms=>{const d=bj(ms);return [d.getUTCHours(),d.getUTCMinutes(),d.getUTCSeconds()].map(two).join(':');};
export const hm=ms=>hms(ms).slice(0,5);
export const today=(ms=Date.now())=>{const d=bj(ms);return `${d.getUTCFullYear()}-${two(d.getUTCMonth()+1)}-${two(d.getUTCDate())}`;};
const full=ms=>`${today(ms)} ${hms(ms)}`;
// <input type="datetime-local"> values read and written as Beijing wall time.
export const bjInput=ms=>{const d=bj(ms);return `${today(ms)}T${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;};
export const parseBj=v=>{const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(v||'');
 return m?Date.UTC(+m[1],m[2]-1,+m[3],+m[4],+m[5])-BJ_MS:NaN;};

// {from, to, kind ("call." = prefix), svc, trace, cost (true: only with a cost), before, limit} -> the query string.
export function buildQuery({from,to,kind,svc,trace,cost,before,limit=200}={}){
 const q=[];
 const add=(k,v)=>{if(v!==undefined&&v!==null&&v!=='')q.push(k+'='+encodeURIComponent(v));};
 add('from',from);add('to',to);add('kind',kind);add('svc',svc);add('trace',trace);
 if(cost)q.push('cost=1');
 add('before',before);add('limit',limit);
 return q.join('&');
}

const SIGNS={CNY:'¥',USD:'$'};
export function fmtCost(cost){
 if(!cost)return '';
 if(cost.amount===null||cost.amount===undefined)return '未知';
 const a=Number(cost.amount);
 const n=(a===0||a>=0.0001?a.toFixed(4):a.toFixed(8)).replace(/\.?0+$/,'');
 return (SIGNS[cost.cur]??((cost.cur||'?')+' '))+n;
}
export function fmtMs(ms){
 const n=Number(ms);
 if(!Number.isFinite(n))return '';
 if(n<1000)return `${n} 毫秒`;
 if(n<60000)return `${(n/1000).toFixed(1)} 秒`;
 return `${Math.floor(n/60000)} 分 ${Math.round(n%60000/1000)} 秒`;
}
function ago(ms){
 const s=Math.max(0,Math.round(ms/1000));
 if(s<60)return `${s} 秒前`;
 if(s<3600)return `${Math.floor(s/60)} 分钟前`;
 if(s<86400)return `${Math.floor(s/3600)} 小时前`;
 return `${Math.floor(s/86400)} 天前`;
}

// ---- what kind of thing a record is -------------------------------------------------------------------------------
// Grouped by what hello was doing; each group one strong colour and one character.
export const CATS=[
 {key:'hear',glyph:'听',name:'听',hint:'识别、听悟、纪要'},
 {key:'see',glyph:'看',name:'看',hint:'画面、识图、M12 事件'},
 {key:'think',glyph:'想',name:'想',hint:'情景判断、分段'},
 {key:'speak',glyph:'说',name:'说',hint:'围观、聊天、语音'},
 {key:'mem',glyph:'记',name:'记',hint:'记忆'},
 {key:'ctl',glyph:'控',name:'控',hint:'开关、模式、预设'},
 {key:'link',glyph:'连',name:'连',hint:'机器人连接、在线'},
 {key:'sys',glyph:'系',name:'系',hint:'服务启停、心跳'},
];
const CAT_BY_KEY=Object.fromEntries(CATS.map(c=>[c.key,c]));
const CAT_RULES=[
 ['speak',/^(call\.realtime|call\.tts|call\.chat|call\.say|gate\.|filler\.|trigger\.(greet|hush))/],
 ['hear',/^(heard\.|call\.tingwu|call\.notes)/],
 ['see',/^(call\.eye|call\.look|trigger\.burst|seen\.)/],
 ['mem',/^call\.memory/],
 ['think',/^call\./],
 ['ctl',/^(switch|mode|preset|billing|budget)\./],
 ['link',/^(link|robot)\./],
];
export function category(kind){
 const k=String(kind||'');
 for(const [c,re] of CAT_RULES)if(re.test(k))return c;
 return 'sys';
}
export const KIND_LABELS={
 'heard.asr':'听到一句话','heard.keyword':'听到关键词','heard.called':'被叫了','call.tingwu':'听悟转写','call.notes':'写听觉纪要',
 'call.eye':'看这一分钟的画面','call.look':'识别一张照片','trigger.burst':'视觉爆发','trigger.task':'查询任务','trigger.length':'回答长短','seen.m12':'M12 看到',
 'call.fusion':'判断当前情景','call.segment':'划分情景段','call.attention':'判断注意力','call.llm':'模型调用',
 'call.chat':'聊天回答','call.say':'想一句话','call.say.speak':'说一句','call.say.line':'写脱口秀台词','call.say.huyu':'写兜底的一句',
 'call.say.judge':'脱口秀挑台词','call.say.check':'开口前复核',
 'call.memory':'整理记忆','call.memory.extract':'提取记忆','call.memory.backfill':'补录记忆',
 'call.realtime.session':'围观会话','call.realtime.context':'更新围观说明','call.realtime.turn':'围观回答','call.realtime.search':'联网搜索',
 'call.tts':'合成语音','gate.realtime':'回答放不放','gate.rescue':'救回一句「嗯」','filler.play':'播垫话','trigger.greet':'打招呼','trigger.hush':'被叫闭嘴',
 'switch.set':'切换开关','switch.pause':'暂停付费','switch.resume':'恢复付费','budget.warn':'花费预警','mode.input':'模式输入','mode.change':'换模式','preset.apply':'应用预设','billing.set':'计费开关',
 'link.open':'连上','link.close':'断开','link.upstream':'上游连接','robot.state':'机器人状态',
 'svc.start':'服务启动','svc.stop':'服务停止','svc.beat':'心跳','svc.error':'服务出错',
};
const LINKS={mouth:'嘴',signal:'信号',vision:'视觉',hearing:'听觉'};
const PARTS={mode:'模式',hearing:'听觉',m12:'M12',vision:'视觉'};
// seen.m12 (m12d 0.2.0 events, 2026-11-17): extra.ev → what it saw
const M12_EV={wake:'待机唤醒',cover:'捂住镜头',uncover:'拿开遮挡',light:'灯光变化',dark:'变暗',bright:'变亮',enter:'有人来了',
 leave:'有人走了',near:'靠近',far:'离远',look:'脸朝着我',look_end:'脸转开',nod:'点头',shake:'摇头',tilt:'歪头',smile:'笑了'};
export function kindTitle(r){
 const base=KIND_LABELS[r.kind]||r.kind;
 if(r.kind==='link.open'||r.kind==='link.close')return `${base}${LINKS[r.extra?.link]?'：'+LINKS[r.extra.link]:r.extra?.link?'：'+r.extra.link:''}`;
 if(r.kind==='link.upstream'&&r.extra?.to)return `${base}：${r.extra.to}`;
 if(r.kind==='robot.state'&&r.extra?.part)return `${base}：${PARTS[r.extra.part]||r.extra.part}`;
 if(r.kind==='seen.m12'&&r.extra?.ev)return `${base}：${r.extra.ev==='light'&&typeof r.extra.on==='boolean'?(r.extra.on?'开灯':'关灯'):M12_EV[r.extra.ev]||r.extra.ev}`;
 return base;
}
// One character per kind, so kinds of the same colour still look different.
export const KIND_GLYPHS={
 'heard.asr':'听','heard.keyword':'键','heard.called':'叫','call.tingwu':'悟','call.notes':'纪',
 'call.eye':'看','call.look':'图','trigger.burst':'爆','trigger.task':'查','trigger.length':'长','seen.m12':'见',
 'call.fusion':'景','call.segment':'段','call.attention':'注','call.llm':'模',
 'call.chat':'聊','call.say':'拟','call.say.speak':'句','call.say.line':'台','call.say.huyu':'兜','call.say.judge':'评','call.say.check':'核',
 'call.realtime.session':'会','call.realtime.context':'令','call.realtime.turn':'答','call.realtime.search':'搜',
 'call.tts':'声','gate.realtime':'闸','gate.rescue':'救','filler.play':'垫','trigger.greet':'招','trigger.hush':'静',
 'call.memory':'记','call.memory.extract':'提','call.memory.backfill':'补',
 'switch.set':'开','switch.pause':'暂','switch.resume':'续','budget.warn':'警','mode.input':'输','mode.change':'换','preset.apply':'预','billing.set':'费',
 'link.open':'连','link.close':'断','link.upstream':'上','robot.state':'机',
 'svc.start':'启','svc.stop':'停','svc.beat':'跳','svc.error':'错',
};
export const kindGlyph=kind=>KIND_GLYPHS[kind]||CAT_BY_KEY[category(kind)].glyph;
// Solid: a model call whose prompt can be read; light: an event.
const badge=(cat,cls='')=>`<span class="badge ${cls}" aria-hidden="true">${CAT_BY_KEY[cat].glyph}</span>`;
const kindBadge=(r,cls='')=>`<span class="badge${isPrompt(r)?'':' soft'}${cls?' '+cls:''}" aria-hidden="true">${kindGlyph(r.kind)}</span>`;

// ---- timeline ------------------------------------------------------------------------------------------------------
export function rowHtml(r,sel=false){
 const cat=category(r.kind),bad=r.ok===false;
 const sum=String(r.sum||'').replace(/\s*\n\s*/g,' ／ ');
 return `<div class="ev cat-${cat}${bad?' bad':''}${sel?' sel':''}" data-id="${esc(r._id)}" role="button" tabindex="0" title="${esc(full(r.at))}">`+
  kindBadge(r)+
  `<div class="ev-main"><div class="ev-title"><b>${escText(kindTitle(r))}</b>`+
  `<span class="tag">${escText(r.svc)}</span>${r.model?`<span class="tag model">${escText(r.model)}</span>`:''}${bad?'<span class="tag fail">失败</span>':''}</div>`+
  `<div class="ev-sum">${escText(sum)}${bad&&r.err?` <span class="err">${escText(r.err)}</span>`:''}</div></div>`+
  `<div class="ev-side"><span class="ev-time">${hms(r.at)}</span>${r.cost?`<span class="ev-cost">${escText(fmtCost(r.cost))}</span>`:''}</div></div>`;
}
// Rows (newest first) with a heading at every new minute.
export function listHtml(rows,selId){
 let last='';
 return rows.map(r=>{const m=hm(r.at),head=m!==last?`<div class="minute">${m}</div>`:'';last=m;return head+rowHtml(r,r._id===selId);}).join('');
}
export function countCats(rows){
 const n=Object.fromEntries(CATS.map(c=>[c.key,0]));
 for(const r of rows)n[category(r.kind)]++;
 return n;
}
export function catsHtml(counts,sel,total,kinds=''){
 return `<button class="cat all" data-cat="" aria-pressed="${!sel}"><span class="badge all" aria-hidden="true">全</span><span class="cat-name">全部</span><span class="count">${total}</span></button>`+
  CATS.map(c=>`<button class="cat cat-${c.key}" data-cat="${c.key}" aria-pressed="${sel===c.key}"${counts[c.key]?'':' data-empty'}>`+
   `${badge(c.key)}<span class="cat-name">${c.name}<small>${c.hint}</small></span><span class="count">${counts[c.key]||0}</span></button>`+(sel===c.key?kinds:'')).join('');
}
// The kinds of one category among the rows, most frequent first: [{kind, n, prompt}].
export function kindCounts(rows,cat){
 const m=new Map();
 for(const r of rows){if(category(r.kind)!==cat)continue;const k=m.get(r.kind)||{kind:r.kind,n:0,prompt:false};k.n++;k.prompt||=isPrompt(r);m.set(r.kind,k);}
 return [...m.values()].sort((a,b)=>b.n-a.n||a.kind.localeCompare(b.kind));
}
export function kindsHtml(kinds,cat,sel){
 if(!cat||!kinds.length)return '';
 return `<div class="kinds cat-${cat}" role="group" aria-label="${CAT_BY_KEY[cat].name}里的类型">`+kinds.map(k=>
  `<button class="kind" data-kind="${esc(k.kind)}" aria-pressed="${sel===k.kind}"><span class="badge${k.prompt?'':' soft'}" aria-hidden="true">${kindGlyph(k.kind)}</span>`+
  `<span class="kind-name">${escText(KIND_LABELS[k.kind]||k.kind)}</span><span class="count">${k.n}</span></button>`).join('')+'</div>';
}

export function chainHtml(trace,rows,selId){
 return `<div class="chain-head"><div><b>链路 ${escText(trace)}</b><span class="muted">${rows.length} 条，从早到晚</span></div><button class="btn" data-close-chain>回到全部记录</button></div>`+
  (rows.length?rows.map(r=>rowHtml(r,r._id===selId)).join(''):'<p class="empty">这条链路前后一天内没有记录</p>');
}

// ---- overview strip -----------------------------------------------------------------------------------------------
// {svc: {at, kind, run} | null} from /log/last -> the services whose last record is over 2 minutes old (or none).
export const staleSvcs=(last,now)=>SVCS.filter(s=>!last?.[s]||now-last[s].at>STALE_MS);
export function lastSeenHtml(last,now,live){
 const stale=new Set(live?staleSvcs(last,now):[]);
 return SVCS.map(s=>{
  const e=last?.[s];
  const text=!e?`${s} 30 天内没有记录`:stale.has(s)?`${s} ${ago(now-e.at)}`:s;
  const title=e?`${ago(now-e.at)}，最后一条：${full(e.at)} ${e.kind}${e.run?`（run ${e.run}）`:''}`:'30 天内没有这个服务的记录';
  return `<span class="svc-last${stale.has(s)?' stale':''}" title="${esc(title)}">${escText(text)}</span>`;
 }).join('');
}
const WHY={no_beat:'没有心跳',seq_jump:'序号跳号',no_stop:'被杀/重启'};
export const GAPS_MAX_MS=48*3600000;             // mind answers /log/gaps for at most 48 h
export const gapsFrom=(from,to)=>Math.max(from,to-GAPS_MAX_MS);
const round3=x=>Math.round(x*1000)/1000;
// Gaps placed on [from, to): left / width in percent, clipped to the window.
export function gapSegments(gaps,from,to){
 const span=Math.max(1,to-from),pct=ms=>Math.min(100,Math.max(0,(ms-from)/span*100));
 return (gaps||[]).map(g=>{const l=pct(g.from_ms),r=pct(g.to_ms);
  return {svc:g.svc,why:g.why,run:g.run,from_ms:g.from_ms,to_ms:g.to_ms,left:round3(l),width:round3(Math.max(0,r-l)),
   label:`${g.svc} ${WHY[g.why]||g.why}${g.lost>0?`（丢 ${g.lost} 条）`:''} ${hms(g.from_ms)}–${hms(g.to_ms)}`};});
}
export function gapsHtml(gaps,from,to){
 const seg=gapSegments(gaps,from,to);
 if(!seg.length)return '';
 const rows=SVCS.concat([...new Set(seg.map(s=>s.svc))].filter(s=>!SVCS.includes(s))).filter(s=>seg.some(g=>g.svc===s)).map(s=>
  `<div class="gaprow"><span class="svc">${escText(s)}</span><div class="gapbar">`+
  seg.filter(g=>g.svc===s).map(g=>`<i class="gap ${esc(g.why)}" data-l="${g.left}" data-w="${g.width}" title="${esc(g.label)}"></i>`).join('')+
  '</div></div>');
 return rows.join('')+'<ul class="gaplist">'+seg.map(g=>`<li class="gap-item">${escText(g.label)}${g.run?` <span class="muted">run ${escText(g.run)}</span>`:''}</li>`).join('')+'</ul>';
}
export const ledgerTotal=l=>Object.entries(l?.total||{}).map(([cur,amount])=>fmtCost({amount,cur})).join('  ')||'没有花费';
export function ledgerHtml(l){
 if(!l)return '<p class="muted">读不到总账</p>';
 return `<div class="ledger-total"><b>${escText(l.day)} 总账</b> ${escText(ledgerTotal(l))}`+
  `${l.unknown_n?` <span class="warn">含未知 ${l.unknown_n} 笔</span>`:''}</div>`+
  ((l.by||[]).length?'<table><tr><th>服务</th><th>模型</th><th>金额</th><th>笔数</th></tr>'+l.by.map(b=>
   `<tr><td>${escText(b.svc)}</td><td>${escText(b.model||'—')}</td><td>${escText(fmtCost({amount:b.amount,cur:b.cur}))}${b.unknown_n?` <span class="warn">+${b.unknown_n} 笔未知</span>`:''}</td><td>${b.n}</td></tr>`).join('')+'</table>':'');
}

// ---- detail panel -------------------------------------------------------------------------------------------------
const traceButton=(trace,at,label=`看整条链路 ${trace}`)=>`<button class="btn ghost" data-trace="${esc(trace)}" data-at="${Number(at)||0}">${escText(label)}</button>`;
const HEARD_KINDS=new Set(['call.realtime.turn','gate.realtime']);
const tryJson=s=>{if(typeof s!=='string'||!/^\s*[[{"]/.test(s))return undefined;try{return JSON.parse(s);}catch{return undefined;}};
const prose=t=>`<div class="prose">${escText(t)}</div>`;
const PROMPT_PARTS={system:'系统提示词',input:'输入',output:'输出'};
export const isPrompt=r=>!!r?.model&&!!r.body&&Object.keys(PROMPT_PARTS).some(n=>typeof r.body[n]==='string');
const BODY_LABELS={params:'参数（schema、工具、采样）',system:'系统提示词',input:'输入全文',output:'输出全文'};

// Provider usage (OpenAI-style prompt/completion or input/output tokens, or plain counters) in a few words.
// 千问判定 (owner 2026-11-18): today's 围观 gate.realtime rows -> how often the realtime model refused. ambient =
// smart_turn_v2 judged a sentence not for hello (no reply); refused_busy = it refused one of hello's own replies
// (greeting / rescue / answer) while someone spoke; accepted = it answered (then our gate played or muted it).
export function qwenSummary(rows){
 const s={accepted:0,played:0,muted:0,ambient:0,busy:0,recent:[]};
 for(const r of rows||[]){
  const x=r.extra||{},v=x.verdict||'';
  if(v==='ambient'){s.ambient++;s.recent.push({at:r.at,what:'不是跟我说',text:x.heard||''});}
  else if(v==='refused_busy'){s.busy++;s.recent.push({at:r.at,what:'拒绝主动回复',text:x.kind||''});}
  else if(x.smart_turn==='accepted'){s.accepted++;if(v==='play')s.played++;else if(v.startsWith('muted_'))s.muted++;}
 }
 s.recent.sort((a,b)=>b.at-a.at);s.recent=s.recent.slice(0,10);
 return s;
}
export const qwenShort=s=>`不是跟我说 ${s.ambient} · 拒回 ${s.busy}`;
export function qwenHtml(s){
 const judged=s.accepted+s.ambient;
 const pct=judged?Math.round(s.ambient*100/judged)+'%':'—';
 const rows=s.recent.map(r=>`<tr><td>${hms(r.at)}</td><td>${escText(r.what)}</td><td>${escText(r.text||'')}</td></tr>`).join('');
 return `<div class="ledger-total"><b>今天 围观 · 千问判定</b> 判过 ${judged} 句，判「不是跟我说」${s.ambient} 句（${pct}）</div>`+
  `<table><tr><th>千问接下</th><td>${s.accepted}（我们放行 ${s.played}，拦下 ${s.muted}）</td></tr>`+
  `<tr><th>千问判不是跟我说</th><td>${s.ambient}（机器人：蓝色方块 + 不是跟我说的）</td></tr>`+
  `<tr><th>千问拒绝主动回复</th><td>${s.busy}（有人在说话；机器人：等你说完）</td></tr></table>`+
  (rows?`<table class="qwen-recent"><tr><th>时间</th><th>拒绝</th><th>听到的 / 哪种回复</th></tr>${rows}</table>`:'<p class="muted">今天还没有拒绝。</p>');
}
export function tokensText(u){
 if(!u||typeof u!=='object')return '';
 const inp=u.prompt_tokens??u.input_tokens,out=u.completion_tokens??u.output_tokens;
 const pd=u.prompt_tokens_details||u.input_tokens_details||{},cd=u.completion_tokens_details||u.output_tokens_details||{};
 const sub=a=>{const s=a.filter(Boolean);return s.length?`（${s.join('，')}）`:'';};
 const a=[];
 if(inp!=null)a.push(`入 ${inp}${sub([pd.cached_tokens?`缓存 ${pd.cached_tokens}`:'',pd.image_tokens?`图 ${pd.image_tokens}`:'',pd.audio_tokens?`音 ${pd.audio_tokens}`:''])}`);
 if(out!=null)a.push(`出 ${out}${sub([cd.reasoning_tokens?`思考 ${cd.reasoning_tokens}`:'',cd.audio_tokens?`音 ${cd.audio_tokens}`:''])}`);
 if(a.length)return a.join('  ');
 return Object.entries(u).filter(([,v])=>typeof v==='number').map(([k,v])=>`${k} ${v}`).join('  ');
}
const isStamp=v=>typeof v==='number'&&v>1.6e12&&v<2.2e12;
function valHtml(v,depth=0){
 if(v===null||v===undefined)return '<span class="muted">—</span>';
 if(isStamp(v))return `<span title="${esc(full(v))}">${hms(v)}</span>`;
 if(typeof v==='boolean')return v?'是':'<b class="err">否</b>';
 if(typeof v!=='object')return escText(v);
 if(depth>0||Array.isArray(v))return `<code>${escText(JSON.stringify(v))}</code>`;
 return '<dl class="sub">'+Object.entries(v).map(([k,x])=>`<dt>${escText(k)}</dt><dd>${valHtml(x,1)}</dd>`).join('')+'</dl>';
}
const FIELD_LABELS={sum:'摘要',ok:'成功',err:'错误',ms:'耗时',by:'谁触发',mode:'当时模式',ref:'指向',extra:'附加',model:'模型',usage:'用量',key:'密钥',cost:'花费'};
const NOT_FIELDS=new Set(['_id','at','svc','kind','run','seq','body','trace']);
export function fieldsHtml(r,skip=[]){
 const out=Object.entries(r).filter(([k])=>!NOT_FIELDS.has(k)&&!skip.includes(k)).map(([k,v])=>{
  const val=k==='ms'?escText(fmtMs(v)):k==='cost'?escText(fmtCost(v)):k==='usage'?escText(tokensText(v)):k==='sum'?`<span class="pre">${escText(v)}</span>`:valHtml(v);
  return `<dt>${escText(FIELD_LABELS[k]||k)}</dt><dd>${val}</dd>`;}).join('');
 return out?`<dl class="fields">${out}</dl>`:'';
}
function statsHtml(r){
 const s=[['模型',r.model],['耗时',r.ms!=null&&fmtMs(r.ms)],['Token',r.usage&&tokensText(r.usage)],['花费',r.cost&&fmtCost(r.cost)]].filter(([,v])=>v);
 return s.length?`<dl class="stats">${s.map(([k,v])=>`<div${k==='Token'?' class="wide"':k==='模型'&&String(v).length>14?' class="mid"':''}><dt>${k}</dt><dd>${escText(v)}</dd></div>`).join('')}</dl>`:'';
}
const lazyFold=(name,id,at)=>`<details class="fold"><summary data-lazy="${esc(id)}" data-at="${at}" data-name="${esc(name)}">${escText(BODY_LABELS[name]||name)}</summary><div class="lazy-box"></div></details>`;

// The opened record. Model calls: tab 提示词 (system / input / output, filled by mountLog; other bodies folded), tab 字段,
// tab 原始记录. Other records: tab 详情 (fields, bodies folded), tab 原始记录.
export function detailHtml(r){
 const cat=category(r.kind),at=Number(r.at)||0;
 const heard=HEARD_KINDS.has(r.kind)&&Array.isArray(r.extra?.heard)?r.extra.heard:null;
 const parts=Object.entries(r.body||{}).filter(([,id])=>typeof id==='string');
 const prompt=isPrompt(r);
 const main=prompt?Object.keys(PROMPT_PARTS).filter(n=>typeof r.body[n]==='string'):[];
 const rest=parts.filter(([n])=>!main.includes(n));
 const skip=['model','ms','usage','cost'];
 const tabs=prompt?[['prompt','提示词'],['fields','字段'],['raw','原始记录']]:[['fields','详情'],['raw','原始记录']];
 const panes={
  prompt:main.map(n=>`<section class="pp pp-${n}" data-load="${n}" data-body-id="${esc(r.body[n])}" data-at="${at}"><h3>${PROMPT_PARTS[n]}</h3><div class="pp-body"><p class="muted">读取中…</p></div></section>`).join('')+
   rest.map(([n,id])=>lazyFold(n,id,at)).join(''),
  fields:(fieldsHtml(r,prompt?[...skip,'sum']:skip)||'<p class="muted">没有别的字段</p>')+(prompt?'':rest.map(([n,id])=>lazyFold(n,id,at)).join('')),
  raw:`<pre class="envelope">${escText(JSON.stringify(r,null,2))}</pre>`,
 };
 return `<div class="dp cat-${cat}" data-id="${esc(r._id)}">`+
  `<header class="dp-head">${kindBadge(r,'lg')}<div class="dp-title"><h2>${escText(kindTitle(r))}</h2>`+
  `<p><span>${escText(r.kind)}</span><span>${escText(r.svc)}</span><span>${escText(full(r.at))}</span></p></div>`+
  `<button class="icon" data-close-detail aria-label="关闭详情">✕</button></header>`+
  (r.ok===false?`<div class="banner">失败${r.err?'：'+escText(r.err):''}</div>`:'')+
  statsHtml(r)+
  (r.trace?`<div class="dp-actions">${traceButton(r.trace,r.at)}</div>`:'')+
  (heard?`<section class="heard-box"><h3>ear 听到的候选 <span class="muted">按时间找的，不是确定的归属</span></h3>`+(heard.length?'<ul class="heard">'+heard.map(h=>
   `<li><span class="muted">${h.at?hms(h.at):''}</span> 「${escText(h.text)}」${h.trace?traceButton(h.trace,h.at,'链路'):'<span class="muted">（没有链路）</span>'}</li>`).join('')+'</ul>':'<p class="muted">没有候选</p>')+'</section>':'')+
  `<nav class="tabs" role="tablist">${tabs.map(([k,l],i)=>`<button role="tab" data-tab="${k}" aria-selected="${i===0}">${l}</button>`).join('')}</nav>`+
  tabs.map(([k],i)=>`<div class="pane" data-pane="${k}"${i?' hidden':''}>${panes[k]}</div>`).join('')+
  '</div>';
}
export const EMPTY_DETAIL='<div class="dp-empty"><p>点一条记录，在这里看它的详情。</p><p class="muted">模型调用直接显示提示词：系统提示词只列出和上一次不同的地方。打开后用 ↑ ↓ 键换上一条、下一条。</p></div>';

// ---- prompt bodies -------------------------------------------------------------------------------------------------
// system bodies are a JSON list of system message texts (mind), or the instructions text itself (realtime, tts).
export function systemText(raw){
 const v=tryJson(raw);
 if(Array.isArray(v)&&v.every(x=>typeof x==='string'))return v.join('\n\n');
 return typeof v==='string'?v:String(raw??'');
}
export function promptHead(text,n=80){
 const line=String(text||'').split('\n').find(l=>l.trim())||'';
 const c=[...line.trim()];
 return c.length>n?c.slice(0,n).join('')+'…':c.join('');
}
// Line diff (LCS between the common head and tail): [{t: '=' | '-' | '+', s}].
export function lineDiff(a,b){
 let p=0;while(p<a.length&&p<b.length&&a[p]===b[p])p++;
 let q=0;while(q<a.length-p&&q<b.length-p&&a[a.length-1-q]===b[b.length-1-q])q++;
 const A=a.slice(p,a.length-q),B=b.slice(p,b.length-q),n=A.length,m=B.length,mid=[];
 if(n*m>4e6){A.forEach(s=>mid.push({t:'-',s}));B.forEach(s=>mid.push({t:'+',s}));}
 else{
  const L=Array.from({length:n+1},()=>new Uint32Array(m+1));
  for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)L[i][j]=A[i]===B[j]?L[i+1][j+1]+1:Math.max(L[i+1][j],L[i][j+1]);
  let i=0,j=0;
  while(i<n&&j<m){if(A[i]===B[j]){mid.push({t:'=',s:A[i]});i++;j++;}else if(L[i+1][j]>=L[i][j+1])mid.push({t:'-',s:A[i++]});else mid.push({t:'+',s:B[j++]});}
  while(i<n)mid.push({t:'-',s:A[i++]});while(j<m)mid.push({t:'+',s:B[j++]});
 }
 return [...a.slice(0,p).map(s=>({t:'=',s})),...mid,...a.slice(a.length-q).map(s=>({t:'=',s}))];
}
// A changed line next to its old version: the differing middle marked.
function inlineMark(a,b){
 const A=[...a],B=[...b];
 let p=0;while(p<A.length&&p<B.length&&A[p]===B[p])p++;
 let q=0;while(q<A.length-p&&q<B.length-p&&A[A.length-1-q]===B[B.length-1-q])q++;
 if(p+q===0)return [escText(a),escText(b)];
 const part=X=>{const mid=X.slice(p,X.length-q).join('');return escText(X.slice(0,p).join(''))+(mid?`<mark>${escText(mid)}</mark>`:'')+escText(X.slice(X.length-q).join(''));};
 return [part(A),part(B)];
}
// Only the changes, with `ctx` unchanged lines around each; longer unchanged runs fold into one "未变 N 行".
export function diffHtml(ops,ctx=1){
 const out=[],line=(cls,h)=>out.push(`<div class="ln ${cls}">${h||'&nbsp;'}</div>`);
 const skip=n=>out.push(`<div class="ln skip">未变 ${n} 行</div>`);
 let i=0;
 while(i<ops.length){
  if(ops[i].t==='='){
   let j=i;while(j<ops.length&&ops[j].t==='=')j++;
   const run=ops.slice(i,j),head=i===0?0:ctx,tail=j===ops.length?0:ctx;
   if(run.length<=head+tail+1)run.forEach(o=>line('ctx',escText(o.s)));
   else{run.slice(0,head).forEach(o=>line('ctx',escText(o.s)));skip(run.length-head-tail);run.slice(run.length-tail).forEach(o=>line('ctx',escText(o.s)));}
   i=j;continue;
  }
  let j=i;while(j<ops.length&&ops[j].t!=='=')j++;
  const del=ops.slice(i,j).filter(o=>o.t==='-').map(o=>o.s),add=ops.slice(i,j).filter(o=>o.t==='+').map(o=>o.s);
  const dh=del.map(escText),ah=add.map(escText);
  for(let k=0;k<Math.min(del.length,add.length);k++)[dh[k],ah[k]]=inlineMark(del[k],add[k]);
  dh.forEach(h=>line('del',h));ah.forEach(h=>line('add',h));
  i=j;
 }
 return `<div class="diff">${out.join('')}</div>`;
}
// prev: {id, at, text} of the same kind's previous call, null when there is none, undefined when it could not be read.
export function systemHtml(text,id,prev){
 let tag,diff='';
 if(prev===undefined)tag='<span class="pill">读不到上一次，没法比较</span>';
 else if(!prev)tag='<span class="pill">7 天内没有上一次同类调用</span>';
 else if(prev.id===id||prev.text===text)tag=`<span class="pill same">和上一次（${hms(prev.at)}）一样</span>`;
 else{
  const ops=lineDiff(String(prev.text).split('\n'),text.split('\n'));
  const d=ops.filter(o=>o.t==='-').length,a=ops.filter(o=>o.t==='+').length;
  tag=`<span class="pill changed">比上一次（${hms(prev.at)}）删 ${d} 行，加 ${a} 行</span>`;
  diff=diffHtml(ops);
 }
 return `<div class="sys-line">${tag}<span class="hash" title="${esc(id)}">#${escText(String(id||'').slice(0,8))}</span></div>`+
  `<p class="sys-head">${escText(promptHead(text))}</p>`+diff+
  `<details class="fold"><summary>全文（${[...text].length} 字）</summary>${prose(text)}</details>`;
}
const ROLES={system:'系统',user:'用户',assistant:'助手',tool:'工具结果'};
const INPUT_KEYS={heard:'模型听到',ear:'ear 听到',prompt:'ear 发的提示'};
const imgBytes=u=>{const m=/\[image omitted, (\d+) bytes\]/.exec(u||'');return m?Number(m[1]):0;};
function partsHtml(content){
 if(typeof content==='string')return prose(content);
 if(!Array.isArray(content))return jsonHtml(content);
 const out=[];let imgs=0,bytes=0;
 const flush=()=>{if(imgs){out.push(`<span class="media">图片 ×${imgs}${bytes?`（${Math.round(bytes/1024)} KB）`:''}</span>`);imgs=0;bytes=0;}};
 for(const p of content){
  if(p?.type==='image_url'||p?.type==='image'){imgs++;bytes+=imgBytes(p.image_url?.url??p.image_url);continue;}
  flush();
  if(p?.type==='text'||typeof p?.text==='string')out.push(prose(p.text));
  else if(p?.type==='input_audio')out.push('<span class="media">音频</span>');
  else out.push(jsonHtml(p));
 }
 flush();
 return out.join('');
}
const msgHtml=(role,label,inner)=>`<div class="msg role-${esc(role)}"><span class="role">${escText(label)}</span>${inner}</div>`;
// input bodies: a list of chat messages (mind), {heard, ear, prompt} (realtime turn), or plain text (tts, heard).
export function messagesHtml(raw){
 const v=tryJson(raw);
 if(Array.isArray(v)&&v.every(m=>m&&typeof m==='object'&&'role' in m))
  return v.map(m=>msgHtml(m.role,ROLES[m.role]||m.role,(m.content!=null?partsHtml(m.content):'')+(m.tool_calls?toolCallsHtml(m.tool_calls):''))).join('');
 if(v&&typeof v==='object'&&!Array.isArray(v))
  return Object.entries(v).map(([k,x])=>msgHtml('user',INPUT_KEYS[k]||k,typeof x==='string'?prose(x):Array.isArray(x)&&x.every(s=>typeof s==='string')?prose(x.join('\n')):jsonHtml(x))).join('')||'<p class="muted">空</p>';
 return v===undefined?prose(raw):jsonHtml(v);
}
// JSON pretty-printed, keys and strings told apart.
export function jsonHtml(v){
 if(typeof v==='string')return prose(v);
 const h=escText(JSON.stringify(v,null,2)).replace(/("(?:[^"\\\n]|\\.)*")(\s*:)?/g,(m,s,colon)=>colon?`<span class="jk">${s}</span>${colon}`:`<span class="js">${s}</span>`);
 return `<pre class="json">${h}</pre>`;
}
function toolCallsHtml(calls){
 return calls.map(c=>{const f=c.function||{},args=tryJson(f.arguments);
  return msgHtml('tool',`调用 ${f.name||'?'}`,args===undefined?prose(f.arguments??''):jsonHtml(args));}).join('');
}
// output bodies: reply text, a JSON object (response_format), or tool_calls.
export function outputHtml(raw){
 const v=tryJson(raw);
 if(Array.isArray(v)&&v.length&&v.every(c=>c?.function))return toolCallsHtml(v);
 if(v!==undefined&&typeof v==='object')return jsonHtml(v);
 return prose(raw);
}
export const bodyHtml=raw=>{const v=tryJson(raw);return v!==undefined&&typeof v==='object'?jsonHtml(v):prose(raw);};
// The previous call to compare a system prompt with: same service and kind; the realtime kinds share one instructions.
export const promptGroup=kind=>String(kind).startsWith('call.realtime.')?'call.realtime.':kind;

// ---- the page -----------------------------------------------------------------------------------------------------
const RANGES={'15m':15*60000,'1h':3600000,'6h':6*3600000,'24h':86400000};
export const LAST_POLL_MS=60000;                 // mind caches /log/last for 60 s
export const PAGE_LIMIT=1000;                    // hello_log_query.MAX_LIMIT; categories filter what was read
const PREV_BACK_MS=7*86400000-60000;             // /log answers at most 7 days
const BODY_CACHE=60;
const AUTO_MIN=30,AUTO_PAGES=5;                // a picked category reads up to 5 more pages to show 30
export function mountLog(doc,{fetchJson=async p=>{const r=await fetch('/api/mind'+p,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);return r.json();},now=Date.now,
 every=(f,ms)=>setInterval(f,ms),stopEvery=t=>clearInterval(t)}={}){
 const $=id=>doc.getElementById(id);
 const byId=new Map(),bodies=new Map();
 let range='1h',cat='',kind='',autoPages=0,win=null,lastId=null,more=false,read=[],chainRows=null,selId=null,timer=null;
 const status=(t,bad=false)=>{const s=$('log-status');s.textContent=t;s.classList.toggle('error',bad);};
 const live=()=>range!=='custom';
 const shownBase=()=>$('log-beats').checked?read:read.filter(r=>r.kind!=='svc.beat');
 const shown=()=>{const b=shownBase();return kind?b.filter(r=>r.kind===kind):cat?b.filter(r=>category(r.kind)===cat):b;};
 function place(root){root.querySelectorAll('[data-l]').forEach(e=>{e.style.left=e.dataset.l+'%';e.style.width=e.dataset.w+'%';});}
 function filters(){
  let from,to;
  if(live()){to=now();from=to-RANGES[range];}
  else{from=parseBj($('log-from').value);to=parseBj($('log-to').value);}
  return {from,to,svc:$('log-svc').value,trace:$('log-trace').value.trim(),cost:$('log-cost').checked};
 }
 function render(){
  const base=shownBase(),rows=shown();
  $('log-cats').innerHTML=catsHtml(countCats(base),cat,base.length,kindsHtml(kindCounts(base,cat),cat,kind));
  $('log-list').innerHTML=rows.length?listHtml(rows,selId):`<p class="empty">${!read.length?'这段时间没有记录':more?'已读的记录里没有这一类，点下面「读更早的」':'这段时间里没有这一类记录'}</p>`;
  $('log-more').hidden=!more;
  if(win){const beats=read.length-base.length;
   status(`${full(win.from).slice(5,16)} – ${full(win.to).slice(5,16)}　已读 ${read.length} 条${beats?`，${beats} 条心跳没显示`:''}`);}
 }
 async function getBody(at,id){
  if(bodies.has(id))return bodies.get(id);
  const t=(await fetchJson(`/log/body?at=${at}&id=${id}`)).text;
  bodies.set(id,t);if(bodies.size>BODY_CACHE)bodies.delete(bodies.keys().next().value);
  return t;
 }
 async function last(){
  try{const d=await fetchJson('/log/last');$('log-svcs').innerHTML=lastSeenHtml(d.svcs,d.now,live());}
  catch(e){$('log-svcs').innerHTML=`<span class="error">读不到各服务的最后记录：${escText(e.message)}</span>`;}
 }
 async function gaps(){
  const n=$('log-gaps-n'),btn=$('log-gaps-btn');
  try{const from=gapsFrom(win.from,win.to),d=await fetchJson(`/log/gaps?from=${from}&to=${win.to}`);
   const note=from>win.from?'<p class="muted">只看这段时间的最后 48 小时</p>':'';
   const html=gapsHtml(d.gaps,d.from,d.to);$('log-gaps').innerHTML=note+(html||'<p class="muted">这段时间没有断档</p>');place($('log-gaps'));
   const k=(d.gaps||[]).length;n.textContent=k?`${k} 处`:'没有';btn.classList.toggle('alarm',k>0);}
  catch(e){$('log-gaps').innerHTML=`<p class="error">读不到断档：${escText(e.message)}</p>`;n.textContent='读不到';btn.classList.add('alarm');}
 }
 async function ledger(){
  try{const l=await fetchJson('/log/ledger?day='+encodeURIComponent($('log-day').value||today(now())));
   $('log-ledger-box').innerHTML=ledgerHtml(l);if(($('log-day').value||today(now()))===today(now()))$('log-cost-total').textContent=ledgerTotal(l);}
  catch(e){$('log-ledger-box').innerHTML=`<p class="error">读不到总账：${escText(e.message)}</p>`;$('log-cost-total').textContent='读不到';}
 }
 async function page(next){
  if(!next){const f=filters();if(!(f.from<f.to)){status('时间范围不对',true);return;}win=f;lastId=null;read=[];more=false;autoPages=0;}
  status('读取中…');
  try{
   const d=await fetchJson('/log?'+buildQuery({...win,before:next?lastId:undefined,limit:PAGE_LIMIT}));
   if(!next)win={...win,from:d.from,to:d.to};
   d.rows.forEach(r=>byId.set(r._id,r));
   read=read.concat(d.rows);more=!!d.more;
   lastId=d.rows.length?d.rows.at(-1)._id:lastId;
   render();
   fillCategory();
  }catch(e){status('读取失败：'+e.message,true);}
 }
 // A category picked and few of it among what was read: read earlier pages by itself, at most AUTO_PAGES of them.
 function fillCategory(){if(cat&&more&&shown().length<AUTO_MIN&&autoPages<AUTO_PAGES){autoPages++;page(true);}}
 async function qwen(){
  const t=now(),from=parseBj(`${today(t)}T00:00`);
  try{const d=await fetchJson('/log?'+buildQuery({from,to:t,kind:'gate.realtime',limit:1000}));
   const s=qwenSummary(Array.isArray(d)?d:d.rows);$('log-qwen-n').textContent=qwenShort(s);$('log-qwen-box').innerHTML=qwenHtml(s);}
  catch(e){$('log-qwen-n').textContent='读不到';$('log-qwen-box').innerHTML=`<p class="error">读不到千问判定：${escText(e.message)}</p>`;}
 }
 async function refresh(){await page(false);gaps();last();ledger();qwen();}
 async function chain(trace,at){
  const box=$('log-chain');box.hidden=false;$('log-list').hidden=true;$('log-more').hidden=true;
  box.innerHTML='<p class="empty">读取链路…</p>';
  try{const d=await fetchJson(`/log/chain?trace=${encodeURIComponent(trace)}&at=${at}`);d.rows.forEach(r=>byId.set(r._id,r));
   chainRows={trace,rows:d.rows};box.innerHTML=chainHtml(trace,d.rows,selId);}
  catch(e){box.innerHTML=`<p class="error">读不到链路：${escText(e.message)}</p><button class="btn" data-close-chain>回到全部记录</button>`;}
  box.scrollIntoView?.({block:'start'});
 }
 function closeChain(){chainRows=null;$('log-chain').hidden=true;$('log-chain').innerHTML='';$('log-list').hidden=false;$('log-more').hidden=!more;}
 async function prevSystem(r){
  const d=await fetchJson('/log?'+buildQuery({from:r.at-PREV_BACK_MS,to:r.at,kind:promptGroup(r.kind),svc:r.svc,limit:20}));
  const p=d.rows.find(x=>x._id!==r._id&&typeof x.body?.system==='string');
  if(!p)return null;
  return {id:p.body.system,at:p.at,text:p.body.system===r.body.system?null:systemText(await getBody(p.at,p.body.system))};
 }
 function fill(el,r){
  el.querySelectorAll('[data-load]').forEach(async sec=>{
   const box=sec.querySelector('.pp-body'),{load:name,bodyId:id,at}=sec.dataset;
   try{
    const raw=await getBody(at,id);
    if(name==='system'){
     const text=systemText(raw);let prev;
     try{prev=await prevSystem(r);}catch{prev=undefined;}
     box.innerHTML=systemHtml(text,id,prev);
    }else box.innerHTML=name==='input'?messagesHtml(raw):outputHtml(raw);
   }catch(e){box.innerHTML=`<p class="error">读不到：${escText(e.message)}</p>`;}
  });
 }
 function open(id,el){
  const r=byId.get(id);if(!r)return;
  selId=id;
  doc.querySelectorAll?.('.ev.sel').forEach(x=>x.classList.remove('sel'));
  el?.classList.add('sel');
  const pane=$('log-detail');pane.innerHTML=detailHtml(r);pane.scrollTop=0;
  doc.body?.classList.add('detail-open');
  fill(pane.firstElementChild,r);
 }
 function closeDetail(){selId=null;doc.querySelectorAll?.('.ev.sel').forEach(x=>x.classList.remove('sel'));$('log-detail').innerHTML=EMPTY_DETAIL;doc.body?.classList.remove('detail-open');}
 function step(dir){
  const cur=doc.querySelector?.('.ev.sel');if(!cur)return;
  let n=dir>0?cur.nextElementSibling:cur.previousElementSibling;
  while(n&&!n.classList.contains('ev'))n=dir>0?n.nextElementSibling:n.previousElementSibling;
  if(n){open(n.dataset.id,n);n.focus({preventScroll:true});n.scrollIntoView({block:'nearest'});}
 }
 async function lazy(sum){
  if(sum.dataset.loaded)return;
  sum.dataset.loaded='1';
  const box=sum.nextElementSibling;box.innerHTML='<p class="muted">读取中…</p>';
  try{box.innerHTML=bodyHtml(await getBody(sum.dataset.at,sum.dataset.lazy));}
  catch(e){box.innerHTML=`<p class="error">读不到正文：${escText(e.message)}</p>`;delete sum.dataset.loaded;}
 }
 function setRange(r){
  range=r;
  doc.querySelectorAll?.('[data-range]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.range===r)));
  $('log-custom').hidden=live();
  if(live())refresh();
 }
 const toggleDrawer=(btn,id)=>{const d=$(id),openNow=d.hidden;d.hidden=!openNow;btn.setAttribute('aria-expanded',String(openNow));};
 doc.addEventListener('click',e=>{
  const t=e.target.closest?.('[data-trace],[data-lazy],[data-cat],[data-kind],[data-range],[data-tab],[data-close-chain],[data-close-detail],.ev');
  if(!t)return;
  if(t.dataset.trace){chain(t.dataset.trace,t.dataset.at);return;}
  if(t.dataset.lazy){lazy(t);return;}
  if(t.hasAttribute('data-cat')){cat=t.dataset.cat||'';kind='';autoPages=0;render();fillCategory();return;}
  if(t.dataset.kind){kind=kind===t.dataset.kind?'':t.dataset.kind;render();return;}
  if(t.dataset.range){setRange(t.dataset.range);return;}
  if(t.dataset.tab){const dp=t.closest('.dp');dp.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b===t)));
   dp.querySelectorAll('[data-pane]').forEach(p=>{p.hidden=p.dataset.pane!==t.dataset.tab;});return;}
  if(t.hasAttribute('data-close-chain')){closeChain();return;}
  if(t.hasAttribute('data-close-detail')){closeDetail();return;}
  open(t.dataset.id,t);
 });
 doc.addEventListener('keydown',e=>{
  const tag=e.target?.tagName;if(tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA')return;
  if((e.key==='Enter'||e.key===' ')&&e.target.classList?.contains('ev')){e.preventDefault();open(e.target.dataset.id,e.target);return;}
  if(e.key==='ArrowDown'||e.key==='j'){if(selId){e.preventDefault();step(1);}return;}
  if(e.key==='ArrowUp'||e.key==='k'){if(selId){e.preventDefault();step(-1);}return;}
  if(e.key==='Escape'&&selId)closeDetail();
 });
 for(const id of ['log-svc','log-cost'])$(id).addEventListener('change',()=>page(false));
 $('log-beats').addEventListener('change',render);
 $('log-trace').addEventListener('keydown',e=>{if(e.key==='Enter')page(false);});
 $('log-refresh').addEventListener('click',()=>{closeChain();refresh();});
 $('log-apply').addEventListener('click',refresh);
 $('log-more').addEventListener('click',()=>page(true));
 $('log-gaps-btn').addEventListener('click',()=>toggleDrawer($('log-gaps-btn'),'log-gaps'));
 $('log-cost-btn').addEventListener('click',()=>toggleDrawer($('log-cost-btn'),'log-ledger'));
 $('log-qwen-btn').addEventListener('click',()=>toggleDrawer($('log-qwen-btn'),'log-qwen'));
 $('log-day').value=today(now());$('log-day').addEventListener('change',ledger);
 $('log-to').value=bjInput(now());$('log-from').value=bjInput(now()-3600000);
 $('log-detail').innerHTML=EMPTY_DETAIL;
 refresh();
 if(timer!==null)stopEvery(timer);
 timer=every(()=>{if(live())last();},LAST_POLL_MS);
 return {refresh};
}

if(typeof document!=='undefined'&&document.getElementById('log-list'))mountLog(document);
