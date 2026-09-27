const assert=require('node:assert/strict');
const {startReason}=require('../portal/connection-state.js');
const ready={connected:true,online:true,ready:true,selected:true,operation:'fixed'};
assert.equal(startReason(ready),'','Fixed mode must not require route preview or confirmation');
for(const state of [{connected:false},{online:false},{ready:false},{selected:false},{active:true},{needsClear:true},{waiting:true},{busy:true}])assert.ok(startReason({...ready,...state}),JSON.stringify(state));
assert.ok(startReason({...ready,operation:'route',supported:true}));
assert.equal(startReason({...ready,operation:'route',supported:true,preview:true,confirmed:true}),'');
console.log('Connection/start gating checks passed');
