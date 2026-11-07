const phases={legacy:'原首页',loading:'加载中',idle:'眼睛待机',playing:'表情播放中',suspended:'已暂停',error:'资源错误'};
const results={unknown:'尚无请求',accepted:'已接收',presenting:'显示层已提交',completed:'已完成',cancelled:'已取消',expired:'已过期',failed:'失败'};
const errors={storage_unavailable:'存储卡不可用',no_memory:'内存不足',asset_missing:'请先安装动画资源',asset_invalid:'动画资源校验失败',io_error:'读卡失败',page_hidden:'已切页或熄屏',reaction:'爱心／叉号优先',voice_ended:'台词结束',superseded:'已被新请求替换',expired:'首帧超时'};
export class EyesPanel {
  constructor(doc,send=async body=>{
    const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(7000)});
    if(!r.ok)throw Error(await r.text());return r.json();
  },uuid=()=>crypto.randomUUID()) {
    this.doc=doc;this.send=send;this.uuid=uuid;this.pending=false;this.catalog='';
    this.el('eyes-enable').addEventListener('click',()=>this.act('configure',{enabled:true,style:this.el('eyes-style').value}));
    this.el('eyes-disable').addEventListener('click',()=>this.act('configure',{enabled:false,style:this.el('eyes-style').value}));
    this.el('eyes-play').addEventListener('click',()=>this.act('play',{state:this.el('eyes-state').value}));
    this.el('eyes-stop').addEventListener('click',()=>this.act('stop'));
  }
  el(id){return this.doc.getElementById(id);}
  async act(op,fields={}) {
    if(!this.ready||this.pending||(op==='play'&&!this.canPlay))return;
    const boot=this.state.boot;this.pending=true;this.el('eyes-command').textContent='正在发送…';this.render(this.state,this.fresh);
    try {
      const r=await this.send({action:`eyes_${op}`,boot,request_id:this.uuid(),...fields});
      this.el('eyes-command').textContent=this.state.boot===boot?(r.message||'已接收；执行状态见下方'):'机器人已重启，请核对新状态';
    } catch(e) {this.el('eyes-command').textContent=e.name==='TimeoutError'?'结果未知，不会自动重发':e.message;}
    finally {this.pending=false;this.render(this.state,this.fresh);}
  }
  render(state,fresh) {
    this.state=state;this.fresh=fresh;const s=state?.telemetry?.eyes||{},c=state?.eyesCatalog||{styles:[],states:[]};
    const supported=state?.capabilities?.eyes==='supported';
    const key=JSON.stringify(c);
    if(key!==this.catalog) {
      this.catalog=key;
      for(const [id,items] of [['eyes-style',c.styles||[]],['eyes-state',c.states||[]]]) {
        const previous=this.el(id).value;
        this.el(id).replaceChildren(...items.map(item=>{const o=this.doc.createElement('option');o.value=item.id;o.textContent=item.label;return o;}));
        if(items.some(i=>i.id===previous))this.el(id).value=previous;
      }
    }
    if(this.boot!==state?.boot) {this.boot=state?.boot;if(c.styles?.some(i=>i.id===s.selected_style))this.el('eyes-style').value=s.selected_style;}
    this.ready=!!fresh&&supported;this.canPlay=this.ready&&s.enabled&&s.storage_ready&&s.memory_ready&&s.phase!=='loading'&&s.phase!=='suspended';
    for(const id of ['eyes-enable','eyes-disable','eyes-stop','eyes-style','eyes-state'])this.el(id).disabled=!this.ready||this.pending;
    this.el('eyes-enable').disabled=!this.ready||this.pending||!s.storage_ready||!s.memory_ready;
    this.el('eyes-play').disabled=!this.canPlay||this.pending;
    this.el('eyes-status').textContent=!fresh?'连接或状态已过期':!supported?'等待支持眼睛的新固件':!s.storage_ready?'存储卡未就绪':phases[s.phase]||'等待状态';
    this.el('eyes-current').textContent=fresh&&supported?`${c.styles?.find(i=>i.id===s.selected_style)?.label||s.selected_style||'—'} · ${c.states?.find(i=>i.id===s.state)?.label||'待机'} · ${s.source==='voice'?'ASR 台词':s.source==='manual'?'网页手动':'待机'}`:'—';
    const r=s.last_request||{};
    this.el('eyes-result').textContent=fresh&&supported?`${results[r.status]||'尚无请求'}${r.error?' · '+(errors[r.error]||r.error):''}${s.last_error?' · '+(errors[s.last_error]||s.last_error):''} · 提交 ${s.submitted_seq||0} / 渲染 ${s.rendered_seq||0}`:'—';
  }
}
