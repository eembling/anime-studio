const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../app/shot_playback.js'),'utf8');
function fixture(){
 const events={},timers=[],statuses=[],sync=[];
 const video={isConnected:true,paused:true,currentTime:0,dataset:{audio:'/line.wav'},
  parentElement:{append:element=>statuses.push(element)},
  addEventListener:(event,fn)=>events[event]=fn,removeEventListener:event=>delete events[event],
  pause(){this.paused=true;events.pause?.()},
  play(){this.paused=false;events.play?.();return Promise.resolve()}};
 const audio={currentTime:0,readyState:4,playbackRate:1,paused:true,play(){this.paused=false;this.currentTime+=.01;return Promise.resolve()},pause(){this.paused=true},removeAttribute(){},load(){}};
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
 assert.equal(sync.length,1);
 assert.equal(sync[0].force,false);
});
test('audio playback rejection is visible and pauses the clip',async()=>{
 const {ctx,video,audio,statuses}=fixture();
 audio.play=()=>Promise.reject({name:'NotAllowedError'});
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});video.play();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(video.paused,true);
 assert.match(statuses[0].textContent,/Dialogue could not play/);
});

test('steady playback does not restart audio or seek picture on transport ticks',async()=>{
 const {ctx,video,audio,timers}=fixture();
 let plays=0,original=audio.play;
 audio.play=function(){plays++;return original.call(this)};
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});
 video.play();
 await new Promise(resolve=>setImmediate(resolve));
 const initial=plays;
 for(let i=0;i<10;i++){video.currentTime+=.03;audio.currentTime=video.currentTime-.04;timers[0]()}
 assert.equal(plays,initial);
 assert.equal(video.paused,false);
 assert.equal(audio.playbackRate,1);
});

test('startup picture alignment does not force an audio seek on seeked',async()=>{
 const {ctx,video,audio,events}=fixture();
 video.duration=4;
 audio.play=function(){this.paused=false;this.currentTime+=.1;return Promise.resolve()};
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});video.play();
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(video.currentTime>.02);
 audio.currentTime=video.currentTime+.01;
 const before=audio.currentTime;events.seeked();
 assert.equal(audio.currentTime,before);
 events.seeked();
 assert.equal(audio.currentTime,video.currentTime);
});

test('large clock drift aligns picture without seeking or changing the audio rate',async()=>{
 const {ctx,video,audio,events,timers,statuses}=fixture();
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});video.play();
 await new Promise(resolve=>setImmediate(resolve));
 video.currentTime=1;audio.currentTime=.8;timers[0]();
 assert.equal(video.currentTime,.8);
 assert.equal(audio.currentTime,.8);
 assert.equal(audio.playbackRate,1);
 events.seeked();
 assert.equal(video.paused,false);
 assert.equal(statuses[0].textContent,'');
});

test('a delayed pause event does not stop already resumed playback',async()=>{
 const {ctx,video,audio,events,timers}=fixture();
 ctx.bindShotPlayback(video,{dialogue_offset:0},{stage:'animate'});video.play();
 await new Promise(resolve=>setImmediate(resolve));
 events.pause();
 video.currentTime=1;audio.currentTime=1;timers[0]();
 assert.equal(audio.paused,false);
});
