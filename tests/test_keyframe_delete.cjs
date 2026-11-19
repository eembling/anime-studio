// Exercise the real UI handler without a browser, network, or a running app.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const html = readFileSync(require('node:path').join(__dirname,'../app/ui.html'),'utf8');
const source = html.slice(html.indexOf('async function deleteKeyframe('),html.indexOf('\nfunction delView('));
function setup({accept=true,ok=true,preview=11}={}){
 const calls=[],button={disabled:false};
 const shot={id:1,seq:3,keyframe_take:11,clip_take:20,final_take:null,takes:[{id:11,stage:'keyframe'},{id:12,stage:'keyframe'},{id:20,stage:'animate'}]};
 const ctx={S:{shots:[shot]},VIEW:{1:preview},pad:n=>String(n).padStart(3,'0'),
  confirm:msg=>{calls.push(['confirm',msg]);return accept},
  post:async(u,b)=>{calls.push(['post',u,b]);if(ok)shot.takes=shot.takes.filter(t=>t.id!==b.take_id);return {ok}},
  renderShots:()=>calls.push(['render']),toast:msg=>calls.push(['toast',msg])};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 return {ctx,calls,button,shot};
}
test('cancel does not submit a deletion',async()=>{
 const {ctx,calls,button}=setup({accept:false});await ctx.deleteKeyframe(1,11,button);
 assert.equal(calls.length,1);assert.equal(button.disabled,false);
 assert.match(calls[0][1],/K1 from shot 003/);assert.match(calls[0][1],/currently used/);
});
test('delete targets the exact keyframe and previews the next without approving it',async()=>{
 const {ctx,calls,button}=setup();await ctx.deleteKeyframe(1,11,button);
 const sent=calls.filter(x=>x[0]==='post');assert.equal(sent.length,1);
 assert.equal(sent[0][1],'/api/take/delete');assert.equal(sent[0][2].take_id,11);
 assert.equal(ctx.VIEW[1],12);assert.equal(button.disabled,false);
});
test('deleting an unpreviewed thumbnail preserves the clip preview',async()=>{
 const {ctx,button}=setup({preview:20});await ctx.deleteKeyframe(1,11,button);assert.equal(ctx.VIEW[1],20);
});
test('failed request retains preview and enables retry',async()=>{
 const {ctx,calls,button}=setup({ok:false});await ctx.deleteKeyframe(1,11,button);
 assert.equal(ctx.VIEW[1],11);assert.equal(button.disabled,false);
 assert.equal(calls.some(x=>x[0]==='toast'),false);
});
test('non-keyframe take cannot be deleted by keyframe controls',async()=>{
 const {ctx,calls,button}=setup();await ctx.deleteKeyframe(1,20,button);assert.equal(calls.length,0);
});
