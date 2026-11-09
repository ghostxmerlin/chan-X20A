import test from 'node:test';
import assert from 'node:assert/strict';
import { WifiPanel } from './public/wifi-ui.js';
function harness() {
 const elements=new Map();
 const make=id=>({id,textContent:'',value:'',checked:id==='wifi-preferred',disabled:false,children:[],dataset:{},className:'',listeners:{},addEventListener(e,fn){this.listeners[e]=fn;},append(...nodes){this.children.push(...nodes);},replaceChildren(){this.children=[];},focus(){}});
 const doc={getElementById(id){if(!elements.has(id))elements.set(id,make(id));return elements.get(id);},createElement(tag){return make(tag);}};
 const calls=[];const ui=new WifiPanel(doc,async body=>{calls.push(body);return {message:'ok'};});
 const s={boot:'a',capabilities:{web_control:'supported'},telemetry:{wifi:{connected:true,ssid:'phone-example',rssi_dbm:-40,ip:'10.0.0.5',status:'connected',profile_limit:6,factory_count:1,profiles:[{ssid:'phone-example',preferred:true},{ssid:'home-example',preferred:false}],scan:[{ssid:'office-example',rssi_dbm:-60,secure:true,saved:false}],scanning:false,scan_age_ms:3000}}};
 return {ui,s,calls,el:id=>doc.getElementById(id)};
}
test('renders profiles and scan results, sends only explicit actions, validates the add form',async()=>{
 const {ui,s,calls,el}=harness();ui.render(s,true);
 assert.equal(el('wifi-add').disabled,true);await el('wifi-add').listeners.click();assert.equal(calls.length,0);
 s.capabilities.wifi_profiles='supported';ui.render(s,true);
 assert.equal(el('wifi-profiles').children.length,2);assert.match(el('wifi-profiles').children[0].children[0].textContent,/★ phone-example/);
 assert.equal(el('wifi-scan-list').children.length,1);assert.match(el('wifi-status').textContent,/已连接 phone-example/);
 el('wifi-ssid').value='office-example';el('wifi-password').value='1234';await el('wifi-add').listeners.click();assert.equal(calls.length,0);assert.match(el('wifi-command').textContent,/8–63/);
 el('wifi-password').value='placeholder-9';await el('wifi-add').listeners.click();
 assert.deepEqual(calls,[{action:'wifi_add',boot:'a',ssid:'office-example',password:'placeholder-9',preferred:true}]);assert.equal(el('wifi-password').value,'');
 await el('wifi-scan').listeners.click();assert.deepEqual(calls.at(-1),{action:'wifi_scan',boot:'a'});
 await el('wifi-profiles').listeners.click({target:{closest:()=>({dataset:{op:'remove',ssid:'home-example'}})}});assert.deepEqual(calls.at(-1),{action:'wifi_remove',boot:'a',ssid:'home-example'});
 await el('wifi-profiles').listeners.click({target:{closest:()=>({dataset:{op:'prefer',ssid:'home-example'}})}});assert.deepEqual(calls.at(-1),{action:'wifi_prefer',boot:'a',ssid:'home-example'});
 await el('wifi-scan-list').listeners.click({target:{closest:()=>({dataset:{ssid:'office-example'}})}});assert.equal(el('wifi-ssid').value,'office-example');
 ui.render(s,false);await el('wifi-reconnect').listeners.click();assert.equal(calls.length,4);
});
