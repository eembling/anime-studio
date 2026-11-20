const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../app/shot_playback.js'),'utf8');
function fixture(){
 const events={},timers=[],statuses=[],sync=[];
 const video={isConnected:true,paused:true,currentTime:0,dataset:{audio:'/line.wav'},
  parentElement:{append:element=>statuses.push(element)},
  addEventListener:(event,fn)=>events[event]=fn,removeEventListener:event=>delete events[event],
  pause(){this.paused=true;events.pause?.()},
  play(){this.paused=false;events.play?.();return Promise.resolve()}};
 const audio={currentTime:0,paused:true,play(){this.paused=false;this.currentTime+=.01;return Promise.resolve()},pause(){this.paused=true},removeAttribute(){},load(){}};
 const ctx={Audio:class{constructor(){return audio}},document:{body:{},createElement:()=>({setAttribute(){},remove(){}})},
  MutationObserver:class{observe(){}},performance:{now:()=>0},setTimeout:fn=>{audio.currentTime+=.01;fn()},EDIT_DURATIONS:new Map(),
  setInterval:fn=>{timers.push(fn);return 1},clearInterval(){},
  syncEditMedia:(media,time,force)=>{sync.push({media,time,force});media.currentTime=time;return true},
  runEditMedia:media=>media.play().catch(()=>{})};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 return {ctx,video,audio,events,timers,statuses,sync};
}
test('continuation dialogue uses the preceding chain duration and duplicate frame',()=>{
 const {ctx}=fixture();
 const shot={clip_take:1,cont:[2],takes:[{id:1,frames:64,fps:16},{id:2,frames:65,fps:16}]};
 assert.equal(ctx.clipSourceOffset(shot,{stage:'continue',segment:1,fps:16}),7.9375);
});
test('an internal video resume does not force another synchronization seek',async()=>{
 const {ctx,video,timers,sync}=fixture();
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});
 video.play();
 await new Promise(resolve=>setImmediate(resolve));
 video.paused=true;
 sync.length=0;timers[0]();
 assert.equal(sync.length,2);
 assert.equal(sync[1].force,false);
});
test('audio playback rejection is visible and pauses the clip',async()=>{
 const {ctx,video,audio,statuses}=fixture();
 audio.play=()=>Promise.reject({name:'NotAllowedError'});
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});video.play();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(video.paused,true);
 assert.match(statuses[0].textContent,/Dialogue could not play/);
});
