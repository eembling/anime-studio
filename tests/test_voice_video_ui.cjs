const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../app/voice_video.js'),'utf8');
function setup({failSave=false,failSecond=false}={}){
 const elements={},calls=[];let html='',closed=false;
 const ctx={S:{shots:[{id:1,kind:'motion',takes:[],voice_direction:'Speak calmly',voice_quality:'standard'}],workflows:{lipsync:{name:'wan22_s2v_14b_fp8'}}},Math,Number,esc:s=>String(s??''),confirm:()=>true,$:key=>elements[key]||(elements[key]={value:'',style:{},disabled:false,textContent:''}),modal:(s)=>{html=s},closeModal:()=>{closed=true},toast:()=>{},post:async(url,body)=>{calls.push({url,body});if(url==='/api/shot/save')return failSave?{ok:false,error:'Save failed'}:{ok:true};if(failSecond&&calls.filter(c=>c.url==='/api/generate').length===2)return{ok:false,error:'Server unavailable'};return{ok:true,queued:1}}};
 vm.createContext(ctx);vm.runInContext(code,ctx);ctx.voiceVideo(1);elements['#voice-framing'].value='source';elements['#voice-performance'].value='Speak calmly';elements['#voice-quality'].value='standard';elements['#voice-seed'].value='123';
 return{ctx,elements,calls,html:()=>html,closed:()=>closed};
}
test('comparison queues two immutable candidates with the same seed',async()=>{
 const t=setup();t.elements['#voice-quality'].value='compare';await t.elements['#voice-generate'].onclick();
 const jobs=t.calls.filter(c=>c.url==='/api/generate');assert.equal(jobs.length,2);assert.equal(jobs[0].body.seed,123);assert.equal(jobs[1].body.seed,123);assert.deepEqual(jobs.map(j=>j.body.voice_quality),['standard','fine']);assert.ok(t.closed());
});
test('failed settings save queues no render',async()=>{const t=setup({failSave:true});await t.elements['#voice-generate'].onclick();assert.equal(t.calls.length,1);assert.equal(t.elements['#voice-status'].textContent,'Save failed');assert.equal(t.closed(),false);assert.equal(t.elements['#voice-generate'].disabled,false)});
test('partial comparison failure reports the already queued candidate',async()=>{const t=setup({failSecond:true});t.elements['#voice-quality'].value='compare';await t.elements['#voice-generate'].onclick();assert.match(t.elements['#voice-status'].textContent,/1 candidate already queued.*Server unavailable/);assert.equal(t.closed(),false)});
test('invalid seed fails before saving or generating',async()=>{const t=setup();t.elements['#voice-seed'].value='1.5';await t.elements['#voice-generate'].onclick();assert.equal(t.calls.length,0);assert.match(t.elements['#voice-status'].textContent,/whole-number seed/)});
test('existing clip UI explains workflow requirements and retains its mode',async()=>{const t=setup();t.ctx.voiceVideo(1,'existing');assert.match(t.html(),/cannot edit an existing clip/);assert.match(t.html(),/id="voice-quality" disabled/);t.elements['#voice-seed'].value='123';await t.elements['#voice-generate'].onclick();assert.equal(t.calls[0].body.lip_sync_mode,'existing');assert.equal(t.calls[1].body.stage,'lipsync')});
