const{test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const{readFileSync}=require('node:fs');
const code=readFileSync(require('node:path').join(__dirname,'../app/editor.js'),'utf8');
function setup({videoDuration=4,audioDuration=6,videoReady=4,frames=64,fps=16,trim=0}={}){
 let now=0,video=null;const elements={},audios=[],timers=[];
 const media=(url,duration,readyState=4)=>({src:new URL(url,'http://local/').href,duration,readyState,currentTime:0,seeking:false,paused:true,playbackRate:1,plays:0,play(){this.plays++;this.paused=false;return Promise.resolve()},pause(){this.paused=true}});
 const monitor={set innerHTML(html){video=html.includes('<video')?media('/clip.mp4',videoDuration,videoReady):null},get innerHTML(){return''}};
 const takes=[{id:1,stage:'animate',url:'/clip.mp4',fps,frames},{id:2,stage:'continue',url:'/part.mp4',fps:16,frames:65}];
 const shot={id:1,seq:1,duration:6,trim_in:trim,kind:'motion',clip_take:1,cont:[],audio_url:'/dialogue.wav'};
 const ctx={S:{shots:[shot],settings:{out_fps:24},audio_cues:[]},document:{addEventListener(){}},$:s=>s==='#edit-monitor'?monitor:s==='#edit-monitor video'?video:elements[s]||(elements[s]={value:0,textContent:''}),take:id=>takes.find(t=>t.id===id),performance:{now:()=>now},setInterval:fn=>{timers.push(fn);return 1},clearInterval(){},Audio:class{constructor(url){Object.assign(this,media(url,audioDuration));audios.push(this)}},URL,location:{href:'http://local/'},audioTime:String,pad:String,toast(){},post:async()=>({ok:true})};
 vm.createContext(ctx);vm.runInContext(code,ctx);
 return{ctx,shot,takes,elements,audios,video:()=>video,time:()=>vm.runInContext('EDIT_TIME',ctx),playing:()=>vm.runInContext('EDIT_PLAYING',ctx),advance(ms){now+=ms;ctx.tickEdit()}};
}
test('16 fps source on a 24 fps timeline keeps real-time video and audio speed',()=>{
 const s=setup();s.ctx.playEdit();s.advance(1000);assert.equal(s.time(),1);assert.equal(s.video().currentTime,1);assert.equal(s.audios[0].currentTime,1);assert.equal(s.video().playbackRate,1);assert.equal(s.audios[0].playbackRate,1);
});
test('buffering pauses every track and freezes the shared timeline',()=>{
 const s=setup({videoReady:0});s.ctx.playEdit();s.advance(1000);assert.equal(s.time(),0);assert.equal(s.audios[0].paused,true);assert.equal(s.audios[0].plays,0);assert.match(s.elements['#edit-state'].textContent,/Buffering/);
 s.video().readyState=4;s.ctx.updateEdit(false);s.advance(1000);assert.equal(s.time(),1);assert.equal(s.audios[0].paused,false);
});
test('audio buffering also pauses video',()=>{const s=setup();s.ctx.playEdit();s.audios[0].readyState=2;s.ctx.updateEdit(false);s.advance(1000);assert.equal(s.time(),0);assert.equal(s.video().paused,true)});
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
test('paused frame stepping uses output fps without changing media speed',()=>{const s=setup();s.ctx.frameEdit(1);assert.equal(s.time(),1/24);assert.equal(s.video().currentTime,1/24);assert.equal(s.video().paused,true);assert.equal(s.audios[0].paused,true)});

test('an ended track is not restarted by sub-frame clock drift',()=>{const s=setup();s.ctx.playEdit();const audio=s.audios[0],plays=audio.plays;audio.ended=true;audio.currentTime=6;s.ctx.seekEdit(5.98);assert.equal(audio.paused,true);assert.equal(audio.plays,plays)});
test('a normal play/pause interruption does not stop the transport',async()=>{const s=setup();s.ctx.seekEdit(0);s.video().play=()=>Promise.reject({name:'AbortError'});s.ctx.playEdit();await new Promise(resolve=>setImmediate(resolve));assert.equal(s.playing(),true)});
