// Response emotion of each clip (config/voice-emotions.json), shown in the picker.
const VOICE_EMOTIONS={happy:'开心',sad:'低落',angry:'生气',fearful:'害怕',disgusted:'嫌弃',surprised:'惊讶',neutral:'平静',unknown:'未确定'};
const stages={idle:'等待播放',waiting:'等待 ASR 的 OK 结束',starting:'准备播放',playing:'播放中',draining:'收尾中',completed:'播放完成',failed:'播放失败',uploading:'其他音频上传中'};
export class VoicePanel {
  constructor(doc,send=async body=>{
    const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
    if(!r.ok)throw new Error(await r.text());return r.json();
  }) {
    this.doc=doc;this.send=send;this.pending=false;this.catalog='';
    this.el('voice-random').addEventListener('click',()=>this.play(0));
    this.el('voice-play').addEventListener('click',()=>this.play(Number(this.el('voice-select').value)));
    this.el('volume-input').addEventListener('input',()=>{this.el('volume-value').textContent=`${this.el('volume-input').value} · 松手后应用`;});
    this.el('volume-input').addEventListener('change',()=>this.setVolume(Number(this.el('volume-input').value)));
  }
  el(id){return this.doc.getElementById(id);}
  async play(id){
    if(!this.ready || this.pending || !Number.isInteger(id) || id<0 || id>(this.state?.voiceLibrary?.count||40))return;
    const boot=this.state.boot;this.pending=true;this.el('voice-command').textContent='正在发送…';this.render(this.state,this.fresh);
    try{const result=await this.send({action:'voice',clip_id:id,boot});this.el('voice-command').textContent=this.state.boot===boot?result.message:'机器人已重启，请核对当前状态';}
    catch(e){this.el('voice-command').textContent=e.name==='TimeoutError'?'结果未确认；不会自动重发':e.message;}
    finally{this.pending=false;this.render(this.state,this.fresh);}
  }
  async setVolume(value){
    if(!this.fresh||this.volumePending||!Number.isInteger(value))return;
    const boot=this.state.boot;this.volumePending=true;this.render(this.state,this.fresh);
    try{const result=await this.send({action:'volume',value,boot});this.el('voice-command').textContent=this.state.boot===boot?`音量已设为 ${result.volume??value}，下一次播放生效`:'机器人已重启，请核对当前状态';}
    catch(e){this.el('voice-command').textContent=e.name==='TimeoutError'?'音量结果未确认；不会自动重发':e.message;}
    finally{this.volumePending=false;this.render(this.state,this.fresh);}
  }
  render(state,fresh){
    this.state=state;this.fresh=fresh;
    const p=state?.telemetry?.playback||{},clips=state?.voiceLibrary?.clips||[];
    const volumeSupported=!!fresh&&Number.isInteger(p.volume_min);
    this.el('volume-input').disabled=!volumeSupported||!!this.volumePending;
    if(volumeSupported&&!this.volumePending&&this.doc.activeElement!==this.el('volume-input'))this.el('volume-input').value=String(p.volume);
    const sources={web:'网页',asr:'语音指令',saved:'开机读取已保存值',default:'默认'};
    this.el('volume-value').textContent=!fresh?'—':volumeSupported?`${p.volume} · ${sources[p.volume_source]||p.volume_source}${p.volume_saved===false?' · 未保存':''}`:'当前固件不支持';
    const supported=state?.capabilities?.voice_library==='supported';
    const key=JSON.stringify(clips);
    if(key!==this.catalog){
      const selected=this.el('voice-select').value;this.catalog=key;
      const options=clips.map(c=>{const o=this.doc.createElement('option');o.value=String(c.id);o.textContent=`${String(c.id).padStart(2,'0')} · ${VOICE_EMOTIONS[c.emotion]?'['+VOICE_EMOTIONS[c.emotion]+'] ':''}${c.text}（${c.title}）`;return o;});
      this.el('voice-select').replaceChildren(...options);
      if(clips.some(c=>String(c.id)===selected))this.el('voice-select').value=selected;
    }
    this.ready=!!fresh && supported && !!state?.telemetry?.health?.audio_ready && !!state?.telemetry?.playback && !p.busy && clips.length>0;
    for(const id of ['voice-play','voice-random','voice-select'])this.el(id).disabled=!this.ready || this.pending;
    this.el('voice-count').textContent=supported?`${clips.length} 条 · ${state?.voiceLibrary?.storage==='sd'?'SD 卡播放':'本体播放'}`:'等待新固件';
    this.el('voice-status').textContent=!fresh?'连接或状态已过期':!supported?'当前固件未安装台词库':p.state==='failed'&&['asset_missing','asset_invalid','storage_unavailable'].includes(p.error_stage)?'SD 语音缺失、损坏或卡不可用，请安装资源':stages[p.state]||'等待播放';
    this.el('voice-current').textContent=!fresh?'—':p.voice_text?`${p.voice_text} · ${p.voice_title||''}`:p.busy?'其他音频':'尚未播放台词';
    const trigger=state?.capabilities?.interaction_modes==='supported'||Object.hasOwn(p,'asr_next_received')?'next':'open';
    const source={asr_next:'ASR next',asr_open:'ASR open',hearing_emotion:'听觉情绪'}[p.source]||'网页';
    this.el('voice-trigger').textContent=`ASR ${trigger} · +800ms`;
    this.el('voice-progress').textContent=fresh && p.voice_clip_id?`${source} · ${(Number(p.submitted_bytes||0)/48000).toFixed(1)} / ${(Number(p.expected_bytes||0)/48000).toFixed(1)} 秒${p.error?' · 播放失败，请重试':''}`:`网页点播或说 ${trigger}，每次播放一条`;
    const counts=['received','accepted','dropped'].map(key=>Number.isInteger(p[`asr_${trigger}_${key}`])?p[`asr_${trigger}_${key}`]:'—');
    this.el('voice-asr-counts').textContent=fresh?`${trigger} 收到 / 接收 / 丢弃：${counts.join(' / ')}`:'ASR 计数：—';
    this.el('voice-asr-counts').title='本次开机累计：收到指令 / 接收播放 / 忙碌或冷却时丢弃。接收计数不证明实际声音播放成功。';
  }
}
