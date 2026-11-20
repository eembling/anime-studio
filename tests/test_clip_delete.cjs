const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const html=readFileSync(require('node:path').join(__dirname,'../app/ui.html'),'utf8');
const code=html.slice(html.indexOf('async function deleteClip('),html.indexOf('\nfunction delView('));
function setup({accept=true,ok=true,preview=21}={}){
 const calls=[],button={disabled:false};
 const shot={id:1,seq:3,keyframe_take:10,clip_take:20,final_take:40,cont:[30,31],takes:[{id:10,stage:'keyframe'},{id:20,stage:'animate'},{id:21,stage:'lipsync'},{id:30,stage:'continue',segment:0},{id:31,stage:'continue',segment:1},{id:32,stage:'continue',segment:0},{id:40,stage:'upscale'},{id:41,stage:'upscale'}]};
 const ctx={$:()=>null,S:{shots:[shot]},VIEW:preview?{1:preview}:{},pad:n=>String(n).padStart(3,'0'),confirm:m=>{calls.push(['confirm',m]);return accept},post:async(u,b)=>{calls.push(['post',u,b]);if(ok)shot.takes=shot.takes.filter(t=>t.id!==b.take_id);return{ok}},renderShots:()=>calls.push(['render']),toast:m=>calls.push(['toast',m])};
 vm.createContext(ctx);vm.runInContext(code,ctx);return{ctx,calls,button,shot};
}
test('cancel retains the clip and preview',async()=>{const{ctx,calls,button}=setup({accept:false});await ctx.deleteClip(1,21,button);assert.equal(calls.length,1);assert.match(calls[0][1],/Lip-sync take 2 from shot 003/);assert.equal(ctx.VIEW[1],21);assert.equal(button.disabled,false)});
test('deleting a lip-sync take targets exactly that take and previews its alternative',async()=>{const{ctx,calls,button}=setup();await ctx.deleteClip(1,21,button);assert.deepEqual(JSON.parse(JSON.stringify(calls.find(c=>c[0]==='post'))),['post','/api/take/delete',{take_id:21}]);assert.equal(ctx.VIEW[1],20);assert.equal(button.disabled,false)});
test('continuation fallback stays in the same segment',async()=>{const{ctx,calls,button}=setup({preview:30});await ctx.deleteClip(1,30,button);assert.equal(ctx.VIEW[1],32);assert.match(calls[0][1],/Part 2 take 1/);assert.match(calls[0][1],/currently used/)});
test('unpreviewed clip deletion keeps the existing preview',async()=>{const{ctx,button}=setup({preview:40});await ctx.deleteClip(1,21,button);assert.equal(ctx.VIEW[1],40)});
test('selected default upscale previews another upscale without adopting it',async()=>{const{ctx,button,shot}=setup({preview:null});await ctx.deleteClip(1,40,button);assert.equal(ctx.VIEW[1],41);assert.equal(shot.final_take,40)});
test('last take clears the deleted preview',async()=>{const{ctx,button,shot}=setup({preview:31});await ctx.deleteClip(1,31,button);assert.equal(ctx.VIEW[1],undefined);assert.ok(shot.takes.find(t=>t.id===30))});
test('failed deletion preserves draft and allows retry',async()=>{const{ctx,calls,button}=setup({ok:false});await ctx.deleteClip(1,21,button);assert.equal(ctx.VIEW[1],21);assert.equal(button.disabled,false);assert.equal(calls.some(c=>c[0]==='toast'),false)});
test('clip controls reject keyframes and takes belonging to another shot',async()=>{const{ctx,calls,button}=setup();await ctx.deleteClip(1,10,button);await ctx.deleteClip(2,20,button);assert.equal(calls.length,0)});
test('busy control cannot queue duplicate deletion',async()=>{const{ctx,calls,button}=setup();button.disabled=true;await ctx.deleteClip(1,21,button);assert.equal(calls.length,0)});

test('deleting a playing preview pauses it so the board can refresh',async()=>{const{ctx,button}=setup();let paused=0;ctx.$=selector=>{assert.equal(selector,'#cut-1 video');return{pause:()=>paused++}};await ctx.deleteClip(1,21,button);assert.equal(paused,1)});
test('shot cards render delete controls for every video stage and the preview',()=>{
 const{ctx,shot}=setup();let markup='';Object.assign(shot,{active:{},outdated:[],asset_ids:[],lines:[],kind:'motion',duration:7,covered:7});
 Object.assign(ctx,{FILTER:'all',sel:new Set(),prevApproved:{},same:()=>false,counts:()=>({}),stripHTML:()=>'',matches:()=>true,esc:x=>x||'',wfOK:()=>true,take:id=>shot.takes.find(t=>t.id===id),updateShotCards:html=>markup=html,document:{querySelectorAll:()=>[]},$:()=>({})});ctx.S.assets=[];ctx.S.settings={};
 vm.runInContext(html.slice(html.indexOf('function renderShotsContent(){'),html.indexOf('function view(sid,tid)')),ctx);ctx.renderShotsContent();
 for(const id of [20,21,30,31,32,40,41])assert.ok(markup.includes(`deleteClip(1,${id},this)`),`Missing delete control for ${id}`);
 assert.match(markup,/Delete previewed clip from shot 003/);assert.ok(!markup.includes('deleteClip(1,10,this)'));
});
