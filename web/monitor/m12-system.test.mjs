import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { M12System, parseSystemLine, sshCommand } from './m12-system.mjs';
import { Monitor } from './core.mjs';
const binding={host:'192.0.2.10',user:'m5stack',host_key_alias:'m12-test',password:'private-test-password'};
const line=(boot='a',up=10,temp='60')=>`STAT ${boot} ${up} ${temp} 1400000 performance audio_fft up`;
test('M12 stats parse independently; invalid temperature never becomes zero or old value',()=>{
 assert.equal(parseSystemLine(line()).celsius,60);
 assert.equal(parseSystemLine(line('a',10,'missing')).celsius,null);
 assert.equal(parseSystemLine(line('a',10,'999')).celsius,null);
 assert.equal(parseSystemLine('arbitrary SSH banner'),null);
});
test('temperature expires, duplicate uptime does not refresh it, and reboot clears history',()=>{
 const s=new M12System(binding);s.accept(parseSystemLine(line()),1000);
 s.accept(parseSystemLine(line()),5000);assert.equal(s.snapshot(7001).temperature.valid,false);
 s.accept(parseSystemLine(line('a',11,'missing')),8000);assert.equal(s.snapshot(8001).temperature.celsius,null);
 s.accept(parseSystemLine(line('b',1,'62')),9000);assert.equal(s.history.length,1);assert.equal(s.snapshot(9001).temperature.celsius,62);
 s.stop();assert.equal(s.snapshot(9002).temperature.valid,false);
});
test('SSH pins the known device and never puts a password in argv; stop prevents late updates',()=>{
 const command=sshCommand(binding,'/tmp/test-root');assert.ok(!command.args.join(' ').includes(binding.password));
 assert.ok(command.args.includes('StrictHostKeyChecking=yes'));assert.ok(command.args.includes('HostKeyAlias=m12-test'));
 const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
 const s=new M12System(binding,{root:'/tmp/test-root',spawn:()=>child});s.start();s.stop();
 child.stdout.write(line()+'\n');assert.equal(s.snapshot().temperature.valid,false);assert.equal(s.child,null);
});
test('M12 temperature remains visible when only the StackChan link is down',()=>{
 const system=new M12System(binding);system.accept(parseSystemLine(line()),1000);
 const monitor=new Monitor({},system);const s=monitor.snapshot(1100);
 assert.equal(s.online,false);assert.equal(s.m12.system.connected,true);assert.equal(s.m12.system.temperature.celsius,60);
 assert.ok(!JSON.stringify(s).includes(binding.password));
});
