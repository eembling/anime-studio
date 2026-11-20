const{test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const{readFileSync}=require('node:fs');
const html=readFileSync(require('node:path').join(__dirname,'../app/ui.html'),'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
const source=script.slice(script.indexOf('/* ---------- render queue ---------- */'),script.indexOf('/* ---------- assemble ---------- */'));
function setup(){
 const elements={};const ctx={esc:s=>String(s??''),fmtT:n=>String(n)+'s',pad:n=>String(n).padStart(3,'0'),
  $:s=>elements[s]||(elements[s]={innerHTML:'',textContent:'',hidden:false}),document:{activeElement:null},
  S:{instances:[{id:1,name:'GPU'}],shots:[{id:1,seq:1}],assets:[],job_counts:{},render_queue:{active:[],queued:[],completed:[],attention:[]}}};
 vm.createContext(ctx);vm.runInContext(source,ctx);return{ctx,elements};
}
test('active and queued renders go into separate sections from completed and failed',()=>{
 const{ctx,elements}=setup();const base={stage:'keyframe',shot_id:1,priority:0};
 ctx.S.render_queue={active:[{...base,id:1,status:'running',estimated_progress:50,remaining_seconds:30,elapsed_seconds:30}],
  queued:[{...base,id:2,status:'pending',estimated_start_seconds:30,estimated_finish_seconds:90}],
  completed:[{...base,id:3,status:'done',elapsed_seconds:60}],attention:[{...base,id:4,status:'error',error:'Failed'}]};
 ctx.renderQueue();assert.match(elements['#queueactive'].innerHTML,/Estimated 50%/);assert.match(elements['#queuequeued'].innerHTML,/finish in about 90s/);
 assert.match(elements['#queuecompleted'].innerHTML,/Complete · 100%/);assert.doesNotMatch(elements['#queuecompleted'].innerHTML,/#4/);
 assert.equal(elements['#queueattentionsection'].hidden,false);
});
test('unknown progress is indeterminate and never labelled as measured percent',()=>{
 const{ctx}=setup();const bar=ctx.queueProgress({status:'running',estimated_progress:null},'Test');
 assert.match(bar,/<progress/);assert.doesNotMatch(bar,/value=/);assert.match(bar,/progress unknown/);
});
test('queued priority field is preserved while typing while active renders still update',()=>{
 const{ctx,elements}=setup();ctx.$('#queuequeued').innerHTML='editing priority';
 ctx.document.activeElement={closest:s=>s==='#queuequeued input'};ctx.renderQueue();
 assert.equal(elements['#queuequeued'].innerHTML,'editing priority');assert.match(elements['#queueactive'].innerHTML,/No active/);
});
test('active render lists exact model assets beneath its progress bar',()=>{
 const{ctx,elements}=setup();ctx.S.render_queue.active=[{id:8,stage:'animate',shot_id:1,status:'running',estimated_progress:25,models:[{kind:'Diffusion',name:'wan_high.safetensors'},{kind:'LoRA',name:'anime.safetensors'}],models_source:'submitted'}];ctx.renderQueue();const html=elements['#queueactive'].innerHTML;assert.match(html,/Diffusion:.*wan_high.safetensors/);assert.match(html,/LoRA:.*anime.safetensors/);assert.ok(html.indexOf('queue-models')>html.indexOf('</progress>'));
});
test('model names are escaped and captured workflow fallback is labelled',()=>{
 const{ctx}=setup();ctx.esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');const html=ctx.queueModels({status:'running',models:[{kind:'Model',name:'<script>model</script>'}],models_source:'captured'});assert.ok(!html.includes('<script>'));assert.match(html,/&lt;script&gt;/);assert.match(html,/From captured workflow/);
});
test('older active renders show unavailable details rather than a guessed model',()=>{const{ctx}=setup();assert.match(ctx.queueModels({status:'running',models:[],models_source:'unknown'}),/unavailable/);assert.equal(ctx.queueModels({status:'done',models:[{name:'model'}]}),'')});
