const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {startReason}=require('../portal/connection-state.js');
const source=fs.readFileSync(require.resolve('../portal/app.js'),'utf8');
const send=source.slice(source.indexOf('async function send('),source.indexOf("$('scan').onclick"));
async function scenario({clearFails=false,scanFails=false,needsClear=false,ready=true}={}){
 const calls=[],notes=[];
 const context=vm.createContext({busy:false,mode:'local',device:{ready:true,active:true},controls(){},note(...args){notes.push(args)},
  async api(path){calls.push(path);if(path==='/api/clear'&&clearFails)throw Error('clear failed');if(path==='/api/scan'){if(scanFails)throw Error('scan failed');return {ready,message:'device checked'}}return {message:'cleared'}},
  async pollOnce(){context.device.active=false;context.device.needs_clear=needsClear;return true}
 });
 vm.runInContext(send,context);await context.send('clear');
 const reason=startReason({connected:true,online:true,ready:context.device.ready,active:context.device.active,needsClear:context.device.needs_clear,busy:context.busy,selected:true,operation:'fixed'});
 if(!clearFails&&!scanFails&&!needsClear&&ready){assert.equal(reason,'');await context.send('fixed',{latitude:21,longitude:111,seconds:60});assert.ok(calls.includes('/api/fixed'))}else assert.ok(reason);
 assert.equal(calls.includes('/api/scan'),!clearFails&&!needsClear);
 assert.equal(context.busy,false);
 if(scanFails)assert.ok(notes.some(n=>n[0].includes('恢复指令已完成')));
}
(async()=>{await scenario();await scenario({clearFails:true});await scenario({scanFails:true});await scenario({needsClear:true});await scenario({ready:false});console.log('Early restore and restart regressions passed')})().catch(e=>{console.error(e);process.exitCode=1});
