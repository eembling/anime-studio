const{test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const{readFileSync}=require('node:fs');
const code=readFileSync(require('node:path').join(__dirname,'../app/editor.js'),'utf8');
function setup({videoDuration=4,audioDuration=6,videoReady=4,frames=64,fps=16,trim=0}={}){
 let now=0,video=null;const elements={},audios=[],timers=[],intervals=[];
 const media=(url,duration,readyState=4)=>({src:new URL(url,'http://local/').href,duration,readyState,currentTime:0,seeking:false,paused:true,playbackRate:1,plays:0,play(){this.plays++;this.paused=false;return Promise.resolve()},pause(){this.paused=true}});
 const monitor={set innerHTML(html){video=html.includes('<video')?media('/clip.mp4',videoDuration,videoReady):null},get innerHTML(){return''}};
 const takes=[{id:1,stage:'animate',url:'/clip.mp4',fps,frames},{id:2,stage:'continue',url:'/part.mp4',fps:16,frames:65}];
 const shot={id:1,seq:1,duration:6,trim_in:trim,kind:'motion',clip_take:1,cont:[],audio_url:'/dialogue.wav'};
 const ctx={S:{shots:[shot],settings:{out_fps:16},audio_cues:[]},document:{addEventListener(){}},$:s=>s==='#edit-monitor'?monitor:s==='#edit-monitor video'?video:elements[s]||(elements[s]={value:0,textContent:''}),take:id=>takes.find(t=>t.id===id),performance:{now:()=>now},setInterval:(fn,ms)=>{timers.push(fn);intervals.push(ms);return 1},clearInterval(){},Audio:class{constructor(url){Object.assign(this,media(url,audioDuration));audios.push(this)}},URL,location:{href:'http://local/'},audioTime:String,pad:String,toast(){},post:async()=>({ok:true})};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 return{ctx,shot,takes,elements,audios,intervals,video:()=>video,time:()=>vm.runInContext('EDIT_TIME',ctx),playing:()=>vm.runInContext('EDIT_PLAYING',ctx),advance(ms){now+=ms;ctx.tickEdit()}};
}
test('16 fps source on a 16 fps timeline keeps real-time video and audio speed',()=>{
 const s=setup();s.ctx.playEdit();s.advance(1000);assert.equal(s.time(),1);assert.equal(s.video().currentTime,1);assert.equal(s.audios[0].currentTime,1);assert.equal(s.video().playbackRate,1);assert.equal(s.audios[0].playbackRate,1);
});
test('buffering pauses every track and freezes the shared timeline',()=>{
 const s=setup({videoReady:0});s.ctx.playEdit();s.advance(1000);assert.equal(s.time(),0);assert.equal(s.audios[0].paused,true);assert.equal(s.audios[0].plays,0);assert.match(s.elements['#edit-state'].textContent,/Buffering/);
 s.video().readyState=4;s.ctx.updateEdit(false);s.advance(1000);assert.equal(s.time(),1);assert.equal(s.audios[0].paused,false);
});
test('audio buffering also pauses video',()=>{const s=setup();s.ctx.playEdit();s.audios[0].readyState=1;s.ctx.updateEdit(false);s.advance(1000);assert.equal(s.time(),0);assert.equal(s.video().paused,true)});
test('short dialogue remains silent after its end rather than replaying',()=>{
 const s=setup({audioDuration:1});s.ctx.playEdit();const audio=s.audios[0],plays=audio.plays;s.ctx.seekEdit(2);assert.equal(audio.currentTime,1);assert.equal(audio.paused,true);s.advance(100);assert.equal(audio.plays,plays);
});
test('short video holds its last frame while dialogue continues',()=>{const s=setup({videoDuration:2});s.ctx.playEdit();s.ctx.seekEdit(3);assert.equal(s.video().paused,true);assert.equal(s.video().currentTime,1.999);assert.equal(s.audios[0].paused,false);s.advance(100);assert.equal(s.video().currentTime,1.999)});
test('source trim applies the same seconds to dialogue and video',()=>{const s=setup({trim:1});s.ctx.playEdit();assert.equal(s.video().currentTime,1);assert.equal(s.audios[0].currentTime,1);s.advance(500);assert.equal(s.video().currentTime,1.5);assert.equal(s.audios[0].currentTime,1.5)});
test('actual encoded duration overrides stale workflow fps at continuation boundary',()=>{
 const s=setup({frames:96,fps:16,videoDuration:4});s.shot.cont=[2];s.ctx.seekEdit(0);
 const result=s.ctx.editSourceAt(s.shot,4.5);assert.equal(result.selected.id,2);assert.equal(result.offset,.5+1/16);
});
test('continuation skips exactly one repeated source frame',()=>{const s=setup();s.shot.cont=[2];const result=s.ctx.editSourceAt(s.shot,4);assert.equal(result.selected.id,2);assert.equal(result.offset,1/16)});
test('loadedmetadata uses the current seek position instead of a stale closure',()=>{const s=setup({videoReady:0});s.ctx.seekEdit(1);const callback=s.video().onloadedmetadata;s.ctx.seekEdit(2);s.video().readyState=4;callback();assert.equal(s.video().currentTime,2);assert.equal(s.audios[0].currentTime,2)});
test('ending the episode stops tracks and replay starts at zero',()=>{const s=setup();s.ctx.playEdit();s.advance(7000);assert.equal(s.time(),6);assert.equal(s.playing(),false);assert.equal(s.video().paused,true);assert.equal(s.audios[0].paused,true);s.ctx.playEdit();assert.equal(s.time(),0);assert.equal(s.playing(),true)});
test('paused frame stepping uses output fps without changing media speed',()=>{const s=setup();s.ctx.frameEdit(1);assert.equal(s.time(),1/16);assert.equal(s.video().currentTime,1/16);assert.equal(s.video().paused,true);assert.equal(s.audios[0].paused,true)});

test('an ended track is not restarted by sub-frame clock drift',()=>{const s=setup();s.ctx.playEdit();const audio=s.audios[0],plays=audio.plays;audio.ended=true;audio.currentTime=6;s.ctx.seekEdit(5.98);assert.equal(audio.paused,true);assert.equal(audio.plays,plays)});
test('a normal play/pause interruption does not stop the transport',async()=>{const s=setup();s.ctx.seekEdit(0);s.video().play=()=>Promise.reject({name:'AbortError'});s.ctx.playEdit();await new Promise(resolve=>setImmediate(resolve));assert.equal(s.playing(),true)});

test('current-data-only media starts loading without advancing the timeline before play resolves',async()=>{
 const s=setup({videoReady:2});s.ctx.seekEdit(0);const video=s.video();
 let resolvePlay;
 video.play=()=>{video.paused=false;return new Promise(resolve=>{resolvePlay=resolve})};
 s.ctx.playEdit();assert.equal(video.paused,false);
 s.advance(200);assert.equal(s.time(),0);
 resolvePlay();await new Promise(resolve=>setImmediate(resolve));s.ctx.updateEdit(false);
 s.advance(30);assert.equal(s.time(),.03);
});
test('forced updates do not restart a seek at the same position',()=>{const s=setup();s.ctx.seekEdit(0);let writes=0,position=0;Object.defineProperty(s.video(),'currentTime',{get:()=>position,set:v=>{writes++;position=v;s.video().seeking=true}});s.ctx.updateEdit(true);assert.equal(writes,0);s.ctx.playEdit();assert.equal(writes,0);assert.equal(s.video().paused,false)});
test('an in-flight seek is allowed to finish before another correction',()=>{const s=setup();s.ctx.seekEdit(0);const video=s.video();let writes=0;Object.defineProperty(video,'currentTime',{get:()=>0,set:()=>writes++});video.seeking=true;s.ctx.seekEdit(1);s.ctx.updateEdit(true);assert.equal(writes,0);video.seeking=false;s.ctx.updateEdit(true);assert.equal(writes,1)});
test('an undecodable track stops the transport with an actionable error',()=>{const s=setup();s.ctx.playEdit();s.video().error={code:3};s.ctx.updateEdit(false);assert.equal(s.playing(),false);assert.match(s.elements['#edit-state'].textContent,/could not be decoded/);assert.equal(s.audios[0].paused,true)});

test('Studio uses a 16 fps transport cadence and frame timecode',()=>{const s=setup();s.ctx.playEdit();assert.equal(s.intervals[0],62.5);assert.equal(s.ctx.editTimecode(1.5),'00:00:01:08');assert.equal(s.ctx.editTimecode(1),'00:00:01:00')});
test('frame stepping from a subframe lands on the next real 16 fps frame',()=>{const s=setup();s.ctx.seekEdit(.03);s.ctx.frameEdit(1);assert.equal(s.time(),1/16)});

test('a stopped or abandoned audio initialization cannot start Studio later',async()=>{
 const s=setup();let complete;s.ctx.MIX_READY=false;s.ctx.startMixAudio=()=>new Promise(resolve=>complete=resolve);
 s.ctx.playEdit();s.ctx.stopEdit();s.ctx.MIX_READY=true;complete();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(s.playing(),false);
});
test('Studio resumes its suspended audio context before restarting transport',async()=>{
 const s=setup();s.ctx.MIX_READY=true;s.ctx.MIX_CONTEXT={state:'suspended'};
 s.ctx.startMixAudio=async()=>{s.ctx.MIX_CONTEXT.state='running'};
 s.ctx.playEdit();await new Promise(resolve=>setImmediate(resolve));assert.equal(s.playing(),true);
});

test('paused Studio seeks do not create separate dialogue decoders before mixer startup',()=>{
 const s=setup();s.ctx.syncStudioAudio=()=>true;s.ctx.MIX_READY=false;s.ctx.seekEdit(1);
 assert.equal(s.audios.length,0);assert.equal(s.video().currentTime,1);
});
