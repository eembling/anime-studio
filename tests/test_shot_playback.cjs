const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../app/shot_playback.js'),'utf8');
function fixture(results=[{ok:true,status:'ready',url:'/media/cache/combined.mp4',audio_label:'Dialogue'}]){
 const nodes={};
 for(const name of ['play','status','sound','loop','download'])nodes[name]={textContent:'',checked:true,hidden:true,events:{},addEventListener(e,fn){this.events[e]=fn},removeEventListener(e){delete this.events[e]}};
 const classes=new Set(),host={querySelector:s=>nodes[s.replace('.shot-preview-','')],classList:{add:s=>classes.add(s),remove:s=>classes.delete(s)}};
 let plays=0,requests=[];
 const video={isConnected:true,paused:true,muted:true,events:{},closest:()=>host,load(){},pause(){this.paused=true;this.events?.pause?.()},removeAttribute(){},play(){plays++;this.paused=false;this.events.play?.();return Promise.resolve()},addEventListener(e,fn){this.events[e]=fn},removeEventListener(e){delete this.events[e]}};
 const ctx={Audio:class{constructor(){throw Error('Separate audio decoder must never be created')}},document:{body:{}},MutationObserver:class{observe(){}},
 setTimeout:fn=>fn(),fetch:async(url,options)=>{requests.push(JSON.parse(options.body));return{ok:true,json:async()=>results.shift()}}};
 vm.createContext(ctx);vm.runInContext(code,ctx);ctx.bindShotPlayback(video,{id:1},{id:2});
 return {ctx,video,nodes,requests,classes,plays:()=>plays};
}
test('prepares and plays one unmuted file with native controls and sound',async()=>{
 const f=fixture();await f.nodes.play.events.click();
 assert.equal(f.video.src,'/media/cache/combined.mp4');assert.equal(f.video.controls,true);
 assert.equal(f.video.muted,false);assert.equal(f.video.loop,true);assert.equal(f.plays(),1);
 assert.equal(f.nodes.sound.textContent,'Dialogue');assert.equal(f.nodes.download.href,f.video.src);
 assert.equal(f.requests[0].take_id,2);assert.equal(f.nodes.play.disabled,false);
});
test('queued preparation polls without starting silent raw video',async()=>{
 const f=fixture([{ok:true,status:'queued'},{ok:true,status:'preparing'},{ok:true,status:'ready',url:'/combined.mp4',audio_label:'Dialogue + timed sounds'}]);
 await f.nodes.play.events.click();assert.equal(f.requests.length,3);assert.equal(f.requests[1].retry,false);
 assert.equal(f.plays(),1);assert.equal(f.nodes.sound.textContent,'Dialogue + timed sounds');
});
test('preparation errors stay visible and expose retry',async()=>{
 const f=fixture([{ok:true,status:'error',error:'Dialogue file is missing'}]);await f.nodes.play.events.click();
 assert.equal(f.plays(),0);assert.match(f.nodes.status.textContent,/Dialogue file is missing/);
 assert.equal(f.nodes.play.textContent,'Retry preview');assert.equal(f.nodes.play.disabled,false);
});
test('replay uses the combined asset and loop control belongs to that asset',async()=>{
 const f=fixture();await f.nodes.play.events.click();await f.nodes.play.events.click();
 assert.equal(f.requests.length,1);assert.equal(f.video.paused,true);
 await f.nodes.play.events.click();assert.equal(f.plays(),2);
 f.nodes.loop.checked=false;f.nodes.loop.events.change();assert.equal(f.video.loop,false);
});
test('another shot pauses the previous combined player',async()=>{
 const f=fixture();await f.nodes.play.events.click();
 const other={paused:false,pause(){this.paused=true;this.events?.pause?.()}};vm.runInContext('SHOT_PLAYERS',f.ctx).add({video:other});
 f.video.events.play();assert.equal(other.paused,true);
});
test('blocked playback exposes a manual play action without losing the prepared file',async()=>{
 const f=fixture();f.video.play=()=>Promise.reject({name:'NotAllowedError'});await f.nodes.play.events.click();
 assert.match(f.nodes.status.textContent,/Press Play/);assert.equal(f.video.src,'/media/cache/combined.mp4');
});
test('disposed preparation cannot attach media or start playback',async()=>{
 const f=fixture();let reply;f.ctx.fetch=()=>new Promise(resolve=>reply=resolve);
 const pending=f.nodes.play.events.click();f.video._shotPlayer.destroy();
 reply({ok:true,json:async()=>({ok:true,status:'ready',url:'/late.mp4'})});await pending;
 assert.equal(f.plays(),0);assert.equal(f.video.src,undefined);
});

test('leaving Shots prevents background preparation from starting audio',async()=>{
 const f=fixture();let reply;f.ctx.fetch=()=>new Promise(resolve=>reply=resolve);
 const pending=f.nodes.play.events.click();f.video._shotPlayer.suspend();
 reply({ok:true,json:async()=>({ok:true,status:'ready',url:'/late.mp4'})});await pending;
 assert.equal(f.plays(),0);assert.equal(f.video._shotPlayer.isPreparing(),false);assert.equal(f.nodes.play.disabled,false);
});
