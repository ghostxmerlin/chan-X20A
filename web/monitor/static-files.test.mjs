import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
// 2026-11-13: a module imported by the page but missing from server.mjs's file list broke the whole page.
test('every module the page imports is served by the monitor', ()=>{
 const server=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 for(const f of ['app.js','say-ui.js','scene-ui.js','db-ui.js','mode-ui.js']){
  const src=readFileSync(new URL('./public/'+f,import.meta.url),'utf8');
  for(const [,p] of src.matchAll(/from\s+'(\/[\w.-]+\.js)'/g))assert.ok(server.includes(`['${p}',`),`${f} imports ${p} but server.mjs does not serve it`);
 }
});
test('every local script, stylesheet and page a page links to is served by the monitor', ()=>{
 const server=readFileSync(new URL('./server.mjs',import.meta.url),'utf8');
 for(const f of ['index.html','mind.html','db.html']){
  const src=readFileSync(new URL('./public/'+f,import.meta.url),'utf8');
  for(const [,p] of src.matchAll(/(?:src|href)="(\/[\w.-]+\.(?:js|css|html))"/g))assert.ok(server.includes(`['${p}',`),`${f} links ${p} but server.mjs does not serve it`);
 }
});
