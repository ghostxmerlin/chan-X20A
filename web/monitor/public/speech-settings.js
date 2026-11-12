const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const scenes={office:'办公室',home:'家里',friends:'朋友聚会'};
const states={candidate:'待积累',known:'已认识',review:'待复核',disabled:'已停用'};
const copy=x=>JSON.parse(JSON.stringify(x));
export async function request(path,body){
 const r=await fetch('/api/mind'+path,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(30000)});
 const data=await r.json();if(!r.ok)throw Error(data.error||'请求失败');return data;
}
export class SpeechSettingsModel{
 constructor(req=request){this.request=req;this.loadSeq=0;this.pageSeq=0;this.page=null;}
 async load(){const seq=++this.loadSeq;const cfg=await this.request('/speech/settings');if(seq!==this.loadSeq)return false;this.accept(cfg);return true;}
 accept(cfg){this.config=cfg;this.draft=copy({speech:cfg.speech,memory:cfg.memory});for(const [k,v] of Object.entries(cfg.environments))this.draft.speech.profiles[k].environment=v;}
 async save(){const seq=this.loadSeq;const cfg=await this.request('/speech/settings',{...copy(this.draft),expected_version:this.config.version});if(seq!==this.loadSeq)return false;this.accept(cfg);++this.pageSeq;return cfg;}
 async memories(offset=0,status=''){const seq=++this.pageSeq;const p=await this.request(`/memories?offset=${offset}&limit=10${status?'&status='+encodeURIComponent(status):''}`);if(seq!==this.pageSeq)return false;this.page=p;return true;}
 audition(role){return this.request('/role/try',{role});}
}
export function memoriesHtml(page,scene){
 if(!page)return '<p>读取中…</p>';
 return `<p class="note">${esc(scene)} · ${page.total} 条认识（只列当前已保存的场景）</p>`+(page.items.length?page.items.map(m=>{
  const metrics={...m,...m.latest_evidence};
  return `<article class="memory-item" data-memory="${esc(m.memory_id)}"><p><b>${esc(states[m.status]||m.status)}</b> · 第 ${m.version} 版${m.manual?' · 手动确认':''}</p><p>${esc(m.statement)}</p>
   <p class="note">${esc(m.kind==='routine'?`有观察 ${metrics.observed_days||0} 天 · 发生 ${metrics.activity_days||0} 天`:`支持 ${metrics.support_days||0} 天 / ${metrics.support_windows||0} 个窗口`)}</p>
   <div class="control-buttons"><button data-memory-action="confirm">确认</button><button data-memory-action="correct">纠正</button><button data-memory-action="disable">停用</button><button data-memory-action="history">历史 / 恢复</button></div><div data-memory-detail></div></article>`;
 }).join(''):'<p class="note">还没有形成认识。需要多个独立窗口的证据，不会把一次观察当成习惯。</p>')+
 `<div class="memory-pagination"><button data-memory-page="${Math.max(0,page.offset-10)}" ${page.offset===0?'disabled':''}>上一页</button><span>${page.total?Math.min(page.offset+1,page.total):0}–${Math.min(page.offset+10,page.total)} / ${page.total}</span><button data-memory-page="${page.offset+10}" ${page.offset+10>=page.total?'disabled':''}>下一页</button></div>`;
}
const reasons={latest_speech:'跟上刚听到的话',notes_agree:'纪要与刚听到的一致',notes_conflict:'纪要有冲突，以刚听到为准',new_topic:'话题已变化',special_visual:'画面有明显变化',emotion_boost:'重复情绪标签伴随声音变化',routine_background:'日常情况降为背景',uncertain_speech:'识别不确定',no_new_topic:'没有新话题'};
export function explanationText(row){
 const a=row.attention||{},c=row.context||{};
 return [`关注：${a.focus||'旧版记录，没有注意力诊断'}`,`依据：${(a.reason_codes||[]).map(x=>reasons[x]||x).join('、')}`,
 `场景：${scenes[c.scope?.id]||c.scope?.id||'—'}`,...(c.evidence||[]).map(e=>`${(a.evidence_ids||[]).includes(e.id)?'采用':'背景'} · ${e.kind||''}：${e.text||e.summary||e.activity||JSON.stringify(e)}`),
 ...(c.memories||[]).map(m=>`已有认识：${m.statement}`),...(c.warnings||[]),`发音指令：${row.instruction||'—'}`].join('\n');
}
function options(items,selected){return Object.entries(items).map(([v,label])=>`<option value="${esc(v)}" ${v===selected?'selected':''}>${esc(label)}</option>`).join('');}

export class SpeechSettings{
 constructor(doc){
  this.doc=doc;this.root=doc.getElementById('speech-settings');if(!this.root)return;
  this.model=new SpeechSettingsModel();this.role='comfort';this.busy=false;this.historySeq=0;
  doc.getElementById('speech-settings-open').addEventListener('click',async()=>{
   doc.getElementById('speech-settings-dialog').showModal();this.root.textContent='读取中…';
   try{if(await this.model.load()){this.render();await this.pages();}}catch(e){this.root.textContent='读取失败：'+e.message;}
  });
  this.root.addEventListener('change',e=>{
   if(e.target.name==='scene'){this.capture(this.profile);this.model.draft.speech.profile=e.target.value;this.render();}
   if(e.target.name==='role'){this.capture(this.profile);this.role=e.target.value;this.render();}
   if(e.target.name==='memory-status')this.pages(0);
  });
  this.root.addEventListener('click',e=>this.click(e));
  let explainSeq=0;
  doc.addEventListener('click',async e=>{
   const b=e.target.closest('[data-say-explain]');if(!b)return;
   const seq=++explainSeq,out=doc.getElementById('say-explanation');out.textContent='读取中…';doc.getElementById('say-explanation-dialog').showModal();
   try{const row=await this.model.request('/say/explain?uid='+encodeURIComponent(b.dataset.sayExplain));if(seq===explainSeq)out.textContent=explanationText(row);}
   catch(err){if(seq===explainSeq)out.textContent='读取失败：'+err.message;}
  });
 }
 msg(text){this.root.querySelector('[data-speech-msg]').textContent=text;}
 capture(profile){
  const get=n=>this.root.querySelector(`[name="${n}"]`),d=this.model.draft;
  const p=d.speech.profiles[profile];p.environment=get('environment').value;
  for(const key of ['topics','avoid'])p[key]=get(key).value.split('\n').map(x=>x.trim()).filter(Boolean);
  d.memory={learning:get('learning').checked,apply:get('apply').checked};
  const override={dialect:get('dialect').value,instruction:get('delivery').value};
  if(get('rate').value!=='')override.rate=Number(get('rate').value);
  d.speech.roles[this.role]=override;
 }
 render(){
  const m=this.model,d=m.draft;this.profile=d.speech.profile;const p=d.speech.profiles[this.profile];
  const role=m.config.roles.find(r=>r.id===this.role)||m.config.roles[0];this.role=role.id;
  const r={...role,...d.speech.roles[this.role]};
  this.root.innerHTML=`<p class="note">第 ${m.config.version} 版 · 手动点击角色才开口。切换场景后保存，近期输入与长期认识会按场景隔离。</p>
   <div class="speech-grid"><label>场景<select name="scene">${options(scenes,this.profile)}</select></label><label class="check-label"><input name="learning" type="checkbox" ${d.memory.learning?'checked':''}>后台学习记忆</label><label class="check-label"><input name="apply" type="checkbox" ${d.memory.apply?'checked':''}>说话时参考记忆</label></div>
   <label>这个场景的背景<textarea name="environment" rows="3" maxlength="3000">${esc(p.environment)}</textarea></label>
   <div class="speech-grid"><label>优先笑点（每行一项）<textarea name="topics" rows="5">${esc(p.topics.join('\n'))}</textarea></label><label>避开的话题（每行一项）<textarea name="avoid" rows="5">${esc(p.avoid.join('\n'))}</textarea></label></div>
   <h3>角色与说法</h3><div class="speech-grid"><label>角色<select name="role">${options(Object.fromEntries(m.config.roles.map(x=>[x.id,x.button])),this.role)}</select></label><label>方言<select name="dialect">${options(Object.fromEntries(['普通话','上海话','四川话','武汉话'].map(x=>[x,x])),r.dialect)}</select></label><label>语速（空白沿用音色）<input name="rate" type="number" min="0.5" max="2" step="0.05" value="${esc(r.rate??'')}"></label></div>
   <label>说话方式（保留角色本色）<textarea name="delivery" rows="3" maxlength="400">${esc(d.speech.roles[this.role]?.instruction??role.delivery??'')}</textarea></label>
   <div class="control-buttons"><button data-speech-save>保存全部设置</button><button data-role-try>试听已保存设置</button></div><p data-speech-msg role="status">武汉话先按湖北武汉话指令尝试；口音以实际试听为准。</p>
   <details class="memory-section"><summary>长期认识与更新历史</summary><p class="note">日常习惯积累后作为背景；固定时段规律还需要多天有效观察。纠正与停用会保留历史。</p><label>筛选<select name="memory-status">${options({'':'全部',...states},this.status||'')}</select></label><div data-memories>${memoriesHtml(m.page,scenes[m.config.speech.profile])}</div></details>`;
 }
 async pages(offset=0){
  this.status=this.root.querySelector('[name="memory-status"]')?.value||'';
  try{if(await this.model.memories(offset,this.status)){++this.historySeq;this.root.querySelector('[data-memories]').innerHTML=memoriesHtml(this.model.page,scenes[this.model.config.speech.profile]);}}catch(e){this.msg('读取认识失败：'+e.message);}
 }
 async history(item,row,offset=0){
  const seq=++this.historySeq;
  const p=await this.model.request(`/memory/history?id=${encodeURIComponent(row.memory_id)}&offset=${offset}&limit=10`);
  if(seq!==this.historySeq||!item.isConnected)return;
  item.querySelector('[data-memory-detail]').innerHTML=p.items.map(v=>`<p>第 ${v.version} 版 · ${esc(v.actor)} · ${esc(v.reason)} · ${esc(states[v.status]||v.status)}<br>${esc(v.statement)} <button data-memory-action="restore" data-version="${v.version}">恢复此版本</button></p>`).join('')+
   `<div class="control-buttons"><button data-history-page="${Math.max(0,offset-10)}" ${offset===0?'disabled':''}>较新</button><button data-history-page="${offset+10}" ${offset+10>=p.total?'disabled':''}>更早</button></div>`;
 }
 async click(ev){
  const b=ev.target.closest('button');if(!b||this.busy)return;
  const relevant=b.hasAttribute('data-speech-save')||b.hasAttribute('data-role-try')||b.hasAttribute('data-memory-page')||b.hasAttribute('data-memory-action')||b.hasAttribute('data-history-page');if(!relevant)return;
  this.busy=true;b.disabled=true;
  const fields=[...this.root.querySelectorAll('input,select,textarea')];fields.forEach(x=>{x.disabled=true;});
  try{
   if(b.hasAttribute('data-speech-save')){this.capture(this.profile);if(await this.model.save()){this.render();this.msg('已保存，下一次开口使用新设置。');await this.pages();}}
   else if(b.hasAttribute('data-role-try')){this.msg('试听已保存的角色设置…');await this.model.audition(this.role);this.msg('已发送试听。未保存的修改不参与本次试听。');}
   else if(b.hasAttribute('data-memory-page'))await this.pages(Number(b.dataset.memoryPage));
   else{
    const item=b.closest('[data-memory]'),row=this.model.page.items.find(x=>x.memory_id===item.dataset.memory);
    if(b.hasAttribute('data-history-page'))await this.history(item,row,Number(b.dataset.historyPage));
    else if(b.dataset.memoryAction==='history')await this.history(item,row);
    else if(b.dataset.memoryAction==='correct')item.querySelector('[data-memory-detail]').innerHTML=`<label>纠正后的认识<textarea data-correction maxlength="160">${esc(row.statement)}</textarea></label><button data-memory-action="save-correction">保存纠正</button>`;
    else{
     const action=b.dataset.memoryAction;
     await this.model.request('/memory/edit',{id:row.memory_id,expected_version:row.version,action:action==='save-correction'?'correct':action,
      ...(action==='save-correction'?{statement:item.querySelector('[data-correction]').value}:{}),...(action==='restore'?{restore_version:Number(b.dataset.version)}:{})});
     await this.pages(this.model.page.offset);this.msg('认识已更新，历史已保留。');
    }
   }
  }catch(e){this.msg('没有完成：'+e.message);}finally{this.busy=false;if(b.isConnected)b.disabled=false;fields.forEach(x=>{x.disabled=false;});}
 }
}
