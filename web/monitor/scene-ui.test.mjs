import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/scene-ui.js',import.meta.url)).toString('base64'));

test('scene minutes: newest first, time, escaped text, carried minutes marked', ()=>{
 const m=new Date(2026,10,13,15,4,0).getTime();
 const html=ui.sceneMinutesHtml([{minute:m-60000,text:'主人打字',summary:'安静',source:'model'},{minute:m,text:'<b>两人讨论</b>',summary:'讨论芯片',source:'carried'}]);
 assert.ok(html.indexOf('15:04')<html.indexOf('15:03'));
 assert.match(html,/&lt;b&gt;两人讨论/);
 assert.match(html,/沿用/);
 assert.doesNotMatch(html,/style="/);
 assert.match(ui.sceneMinutesHtml([]),/还没有情景/);
});
test('page: eyes controls live in settings, the dashboard has the scene panel', ()=>{
 const html=readFileSync(new URL('./public/index.html',import.meta.url),'utf8');
 const settings=html.slice(html.indexOf('<dialog id="monitor-settings"'),html.indexOf('</dialog>',html.indexOf('<dialog id="monitor-settings"')));
 for(const id of ['eyes-style','eyes-enable','eyes-state','eyes-play','eyes-stop','eyes-status'])assert.match(settings,new RegExp(`id="${id}"`),id);
 assert.doesNotMatch(html,/class="panel eyes-panel"/);
 assert.match(html,/class="panel scene-panel"/);
 assert.match(html,/id="scene-minutes"/);
});
