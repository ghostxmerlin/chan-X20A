// Docking 重构计划 page: the relevance threshold demo. Illustrative scenes (not measured), scored with the starting weights
// shown on the page; at or above the threshold Dynames speaks, up to 20 below it only reacts (face / eyes), lower it
// only collects (notes, scene, memory). The pure parts are exported for tests.
export const WEIGHTS={name:60,follow:30,face:20,question:10,you:10,family:10,crosstalk:-30,far:-30};
export const SIGNAL_NAMES={name:'叫了名字',follow:'刚说完就接话',face:'正对着它',question:'问句',you:'说到你/它',family:'主人或家人',crosstalk:'几个人互相说',far:'像电视/远处'};
export const SCENES=[
 {text:'主人看着它：“Dynames，你看这个是什么？”',signals:['name','face','question','family']},
 {text:'它刚说完，主人接着问：“真的吗？”',signals:['follow','face','question','family']},
 {text:'有人路过：“这小机器人挺可爱。”',signals:['face','you']},
 {text:'主人对着电脑自言自语：“这板子怎么又坏了。”',signals:['face','family']},
 {text:'两个同事在旁边讨论电容型号',signals:['crosstalk']},
 {text:'电视里有人问：“你吃饭了吗？”',signals:['question','you','far']},
];
export const REACT_BAND=20;
export const score=signals=>Math.max(0,Math.min(100,signals.reduce((s,k)=>s+(WEIGHTS[k]||0),0)));
export function verdict(sc,th){
 if(sc>=th)return {key:'speak',label:'开口回答'};
 if(sc>=th-REACT_BAND)return {key:'react',label:'只用表情'};
 return {key:'collect',label:'只收集'};
}
const esc=t=>String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
export function scenesHtml(th){
 return SCENES.map(s=>{const sc=score(s.signals),v=verdict(sc,th);
  return `<article class="scene v-${v.key}"><p class="scene-text">${esc(s.text)}</p>`+
   `<p class="chips">${s.signals.map(k=>`<span class="chip ${WEIGHTS[k]<0?'neg':'pos'}">${esc(SIGNAL_NAMES[k])} ${WEIGHTS[k]>0?'+':'−'}${Math.abs(WEIGHTS[k])}</span>`).join('')}</p>`+
   `<div class="scene-foot"><span class="meter" aria-hidden="true"><i data-w="${sc}"></i><b data-l="${th}"></b></span>`+
   `<span class="score">${sc} 分</span><span class="verdict">${v.label}</span></div></article>`;}).join('');
}
export const bandNote=th=>`低于 ${Math.max(0,th-REACT_BAND)} 只收集，${Math.max(0,th-REACT_BAND)}–${th-1} 只用表情，${th} 以上开口`;

function mount(doc){
 const th=doc.getElementById('th'),out=doc.getElementById('th-out'),box=doc.getElementById('scenes'),note=doc.getElementById('band-note');
 const draw=()=>{const v=Number(th.value);out.textContent=String(v);note.textContent=bandNote(v);box.innerHTML=scenesHtml(v);
  box.querySelectorAll('[data-w]').forEach(e=>{e.style.width=e.dataset.w+'%';});
  box.querySelectorAll('[data-l]').forEach(e=>{e.style.left=e.dataset.l+'%';});};
 th.addEventListener('input',draw);draw();
}
if(typeof document!=='undefined'&&document.getElementById('scenes'))mount(document);
