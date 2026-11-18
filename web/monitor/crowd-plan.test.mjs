import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const ui=await import('data:text/javascript;base64,'+readFileSync(new URL('./public/crowd-plan.js',import.meta.url)).toString('base64'));

test('relevance demo: score clamps to 0..100; speak at the threshold, react up to 20 below, else collect', ()=>{
 assert.equal(ui.score(['name','face','question','family']),100);
 assert.equal(ui.score(['crosstalk']),0);
 assert.equal(ui.verdict(50,50).key,'speak');
 assert.equal(ui.verdict(30,50).key,'react');
 assert.equal(ui.verdict(29,50).key,'collect');
 assert.equal(ui.bandNote(50),'低于 30 只收集，30–49 只用表情，50 以上开口');
 const html=ui.scenesHtml(50);
 assert.equal((html.match(/class="scene /g)||[]).length,ui.SCENES.length);
 assert.match(html,/v-speak/);assert.match(html,/v-collect/);
});
