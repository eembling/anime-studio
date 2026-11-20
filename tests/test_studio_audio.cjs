const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../app/studio_audio.js'),'utf8');
function fixture(){
 const sources=[],fetches=[],buses=new Map(['dialogue','music','ambience','sfx'].map(name=>[name,{gain:{name}}]));
 const context={currentTime:0,decodeAudioData:async()=>({duration:4,length:192000,numberOfChannels:2}),
  createBufferSource(){const node={starts:[],stops:0,playbackRate:{value:1},connect(){},disconnect(){},start(...args){this.starts.push(args)},stop(){this.stops++}};sources.push(node);return node},
  createGain(){return{gain:{setTargetAtTime(value){this.value=value},setValueAtTime(value){this.value=value}},connect(bus){this.bus=bus},disconnect(){}}}};
 const ctx={MIX_CONTEXT:context,MIX_BUSES:buses,S:{audio_cues:[{id:7,kind:'sfx'}]},fetch:async url=>{fetches.push(url);return{ok:true,arrayBuffer:async()=>new ArrayBuffer(8)}}};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 const sync=(tracks,playing=true,force=false)=>ctx.syncStudioAudio(tracks,playing,force);
 const warm=async tracks=>{sync(tracks,false);await new Promise(resolve=>setImmediate(resolve))};
 return {ctx,context,sources,fetches,sync,warm};
}
const voice=(time=0)=>({id:'dialogue',url:'/voice.wav',time,volume:1});
test('buffers load before playback, then sources share one mixer start time',async()=>{
 const f=fixture(),tracks=[voice(),{id:'cue7',url:'/bell.wav',time:.25,volume:.5}];
 assert.equal(f.sync(tracks),false);assert.equal(f.sources.length,0);
 await f.warm(tracks);assert.equal(f.sync(tracks),true);assert.equal(f.sources.length,2);
 assert.equal(f.sources[0].starts[0][0],f.sources[1].starts[0][0]);assert.equal(f.sources[1].starts[0][1],.25);
 assert.equal(f.sources[0].playbackRate.value,1);
});
test('steady 16 fps ticks never restart decoded dialogue',async()=>{
 const f=fixture();await f.warm([voice()]);f.sync([voice()]);
 for(let i=1;i<32;i++){f.context.currentTime=i/16;f.sync([voice(i/16)])}
 assert.equal(f.sources.length,1);assert.equal(f.fetches.length,1);assert.equal(f.sources[0].stops,0);
});
test('seeking replaces sources at the new offset and pause stops their output',async()=>{
 const f=fixture();await f.warm([voice()]);f.sync([voice()]);f.sync([voice(1)],true,true);
 assert.equal(f.sources[0].stops,1);assert.equal(f.sources[1].starts[0][1],1);
 f.ctx.stopStudioAudio();assert.equal(f.sources[1].stops,1);
});
test('looped cues wrap inside their trimmed source range without decoder seeks',async()=>{
 const f=fixture(),cue=time=>({id:'cue7',url:'/loop.wav',time,volume:1,loop:true,loopStart:.5,loopEnd:2.5});
 await f.warm([cue(2.4)]);f.sync([cue(2.4)]);f.context.currentTime=.2;f.sync([cue(.6)]);
 assert.equal(f.sources.length,1);assert.equal(f.sources[0].loopStart,.5);assert.equal(f.sources[0].loopEnd,2.5);
});
test('missing audio reports failure and a later manual retry can reload it',async()=>{
 const f=fixture();f.ctx.fetch=async()=>({ok:false});await f.warm([voice()]);
 assert.throws(()=>f.sync([voice()]),/could not be loaded/);assert.equal(f.sources.length,0);
 f.ctx.stopStudioAudio();f.ctx.fetch=async()=>({ok:true,arrayBuffer:async()=>new ArrayBuffer(8)});
 await f.warm([voice()]);assert.equal(f.sync([voice()]),true);
});
test('selected dialogue replacement cannot leave the prior performance playing',async()=>{
 const f=fixture();await f.warm([voice()]);f.sync([voice()]);const next={...voice(),url:'/new-voice.wav'};
 assert.equal(f.sync([next]),false);assert.equal(f.sources[0].stops,1);
 await f.warm([next]);f.sync([next]);assert.equal(f.sources.length,2);
});
test('audio past its end is silent instead of restarting from the beginning',async()=>{
 const f=fixture();await f.warm([voice()]);f.sync([voice()]);f.sync([voice(4.1)]);
 assert.equal(f.sources.length,1);assert.equal(f.sources[0].stops,1);
});

test('a loop boundary with normal clock jitter does not restart audio',async()=>{
 const f=fixture(),cue=time=>({id:'cue7',url:'/loop.wav',time,volume:1,loop:true,loopStart:.5,loopEnd:2.5});
 await f.warm([cue(2.499)]);f.sync([cue(2.499)]);f.context.currentTime=.005;f.sync([cue(.501)]);
 assert.equal(f.sources.length,1);assert.equal(f.sources[0].stops,0);
});
