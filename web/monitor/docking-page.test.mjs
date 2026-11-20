// Owner 2026-11-20 (Providence, Docking only): the talk-show panel (让 hello 说一句: roles, 说一轮, 提前写 / 连续说, the
// chat card, voice try, last lines, remote signals) is gone from the main page; the robot's fixed recordings and the
// volume stay.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';

const read=f=>readFileSync(new URL(f,import.meta.url),'utf8');

test('the main page has no talk-show panel',()=>{
 const index=read('./public/index.html');
 for(const id of ['say-heading','say-buttons','say-msg','chat-stage','say-chat','say-round','say-prefetch','say-voices','say-utterances','say-signals'])
  assert.doesNotMatch(index,new RegExp(`id="${id}"`),id);
 assert.doesNotMatch(index,/让 hello 说一句/);
 assert.doesNotMatch(read('./public/app.js'),/say-ui/);
 assert.equal(existsSync(new URL('./public/say-ui.js',import.meta.url)),false);
 assert.doesNotMatch(read('./server.mjs'),/'\/say-ui\.js'/);
});

test('the fixed recordings and the volume stay',()=>{
 const index=read('./public/index.html');
 for(const id of ['voice-heading','voice-select','voice-play','voice-random','volume-input'])
  assert.match(index,new RegExp(`id="${id}"`),id);
});
