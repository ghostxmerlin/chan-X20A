import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

function harness(){
 const elements=new Map(),sources=[],observers=[],requests=[],windowEvents={};
 const element=id=>{
  if(!elements.has(id))elements.set(id,{id,textContent:'',style:{},open:false,dataset:{},classList:{toggle(){}},listeners:{},
   addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
   append(){},replaceChildren(){},matches(){return false;},hasAttribute(){return false;}});
  return elements.get(id);
 };
 const context={console,Date,Promise,URL:{revokeObjectURL(){},createObjectURL(){return 'blob:preview';}},Node:{TEXT_NODE:3},
  Image:class{async decode(){}},localStorage:{getItem(){return null;},setItem(){}},setInterval(){},
  document:{hidden:false,getElementById:element,querySelector:()=>element('dashboard'),querySelectorAll:()=>[],createElement:()=>element(Symbol())},
  window:{addEventListener:(name,fn)=>{windowEvents[name]=fn;}},
  MutationObserver:class{constructor(fn){this.fn=fn;this.targets=[];observers.push(this);}observe(el){this.targets.push(el);}},
  EventSource:class{constructor(url){this.url=url;sources.push(this);}close(){this.closed=true;}},
  fetch:async(url,options)=>{requests.push({url,options});return {ok:true,headers:{get:()=>1},blob:async()=>({})};},
  renderM12(){},renderThermal(){}};
 for(const name of ['ControlPanel','InteractionPanel','VoicePanel','EyesPanel','HearingPanel','ServoTuningPanel','WifiPanel','M12LibraryPanel','SwitchBar','SayPanel','SpeechSettings','SceneMinutesPanel'])context[name]=class{render(){}};
 const script=readFileSync(new URL('./public/app.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'');
 vm.runInNewContext(script,context);
 const send=(source=sources.at(-1),telemetry={health:{camera_enabled:false}})=>source.onmessage({data:JSON.stringify({online:true,fresh:{camera:true,telemetry:true},telemetry,frame:{seq:1,bytes:500},events:[],receivedAt:Date.now()})});
 return {element,sources,requests,send,windowEvents,
  click:id=>element(id).listeners.click(),
  dialog:(id,open)=>{const el=element(id);el.open=open;for(const observer of observers)if(observer.targets.includes(el))observer.fn([]);}};
}

test('browser preview starts off, never sends a control, and hides frames immediately when stopped',async()=>{
 const h=harness();assert.equal(h.sources[0].url,'/api/events?camera=0&servo=0');assert.equal(h.element('camera-toggle').textContent,'开始传输');
 h.send();await new Promise(r=>setImmediate(r));assert.equal(h.requests.length,0);
 h.click('camera-toggle');assert.equal(h.sources[0].closed,true);assert.equal(h.sources.at(-1).url,'/api/events?camera=1&servo=0');
 h.send();await new Promise(r=>setImmediate(r));assert.equal(h.element('camera').hidden,false);assert.equal(h.element('camera-toggle').textContent,'停止传输');
 const previous=h.sources.at(-1);h.click('camera-toggle');assert.equal(h.element('camera').hidden,true);assert.equal(h.element('camera').src,undefined);
 assert.equal(h.sources.at(-1).url,'/api/events?camera=0&servo=0');h.send(previous);await new Promise(r=>setImmediate(r));
 assert.equal(h.element('camera').hidden,true);assert.ok(h.requests.every(r=>r.url==='/api/frame'&&!r.options?.method));
});

test('servo diagnostics follow only the two open dialogs and respect pause and page lifecycle',()=>{
 const h=harness();h.dialog('wifi-config',true);assert.equal(h.sources.length,1);
 h.dialog('servo-tuning',true);assert.equal(h.sources.at(-1).url,'/api/events?camera=0&servo=1');
 const count=h.sources.length;h.dialog('servo-settings',true);h.dialog('servo-tuning',false);assert.equal(h.sources.length,count);
 h.dialog('servo-settings',false);assert.equal(h.sources.at(-1).url,'/api/events?camera=0&servo=0');
 h.click('pause');const paused=h.sources.length;h.dialog('servo-settings',true);assert.equal(h.sources.length,paused);
 h.click('pause');assert.equal(h.sources.at(-1).url,'/api/events?camera=0&servo=1');
 h.windowEvents.pagehide();const hidden=h.sources.length;h.dialog('servo-settings',false);assert.equal(h.sources.length,hidden);
 h.windowEvents.pageshow({persisted:true});assert.equal(h.sources.at(-1).url,'/api/events?camera=0&servo=0');
});

test('a timed turn and out-of-range feedback cannot look like a measured 180 degree arrival',()=>{
 const h=harness();h.send(undefined,{remote:{motor:{yaw:{valid:true,angle_deg:180},yaw_turn:{busy:true,state:'rotating'}}}});
 assert.equal(h.element('yaw').textContent,'旋转中');
 h.send(undefined,{remote:{motor:{yaw:{valid:true,angle_deg:180},yaw_turn:{busy:false,state:'estimated_complete'}}}});
 assert.equal(h.element('yaw').textContent,'超出定位范围');
 h.send(undefined,{remote:{motor:{yaw:{valid:true,angle_deg:45},yaw_turn:{busy:false}}}});assert.equal(h.element('yaw').textContent,'45.0°');
});
