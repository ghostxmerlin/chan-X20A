import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/looks-ui.js',import.meta.url)).toString('base64'));

test('burst status: running, last burst, none', ()=>{
 const t=new Date(2026,10,15,20,10,41).getTime();
 assert.match(ui.burstStatusHtml({running:{looks:7,since:t,last_at:t+14000},bursts:[]},t+15000),/正在看：已看 7 张，开始于 20:10:41（15 秒前）/);
 const last=ui.burstStatusHtml({running:null,bursts:[{at:t,duration_s:131.1,looks:52,latency_p50_ms:1470,cycle_mean_ms:2375,failures:{look_TimeoutError:1},cost_usd:0.0020727,reason:'elapsed'}]});
 assert.match(last,/131.1 秒，看了 52 张，识图中位 1.5 秒，每张约 2.4 秒，失败 1 次，\$0.0021；结束：2 分钟到了/);
 assert.match(ui.burstStatusHtml({running:null,bursts:[]}),/说“注意看”/);
 assert.match(ui.burstStatusHtml(null),/读不到/);
});

test('looks: photo slot per look, escaped text, failures shown', ()=>{
 const t=new Date(2026,10,15,20,10,43).getTime();
 const html=ui.looksHtml([{lid:'b1-1',at:t,text:'<b>主人</b>对着镜头',latency_ms:1102,has_frame:true},{lid:'b1-2',at:t+2000,text:null,error:'TimeoutError',has_frame:false}]);
 assert.match(html,/<img data-lid="b1-1"/);
 assert.match(html,/&lt;b&gt;主人/);
 assert.match(html,/识图 1.1 秒/);
 assert.match(html,/没认出来（TimeoutError）/);
 assert.match(html,/没有照片/);
 assert.equal(ui.looksHtml([]),'');
});

test('the mind proxy lets the looks paths through, lid strictly', async ()=>{
 const {MindProxy}=await import(new URL('./mind.mjs',import.meta.url));
 assert.equal(MindProxy.check('/looks?limit=60'),'/looks?limit=60');
 assert.equal(MindProxy.check('/looks/frame?lid=b1794744641722-12'),'/looks/frame?lid=b1794744641722-12');
 assert.equal(MindProxy.check('/looks/frame?lid=../x'),null);
 assert.equal(MindProxy.check('/looks/frame?lid=b1-2&lid=b1-3'),null);
});
