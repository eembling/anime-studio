// UI logic checks without touching a browser, process, or running app.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const html=readFileSync(require('node:path').join(__dirname,'../app/ui.html'),'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
const start=script.indexOf('/* Timed episode soundtrack;');
const source=script.slice(start,script.indexOf('/* ---------- settings ---------- */',start));
function setup(){
 const elements={audioexport:{value:'7'},audiotimeline:{},audiocues:{},audioexportnote:{},usemusic:{checked:false},mvol:{value:'0.25'}};
 const sent=[];
 const ctx={S:{shots:[],audio_exports:[{id:7,title:'Episode',seconds:10,clean:1,url:'/episode.mp4'}],audio_cues:[]},
  $:s=>elements[s.slice(1)],same:()=>false,esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  post:async(u,b)=>{sent.push({u,b});return{ok:true}},toast:()=>{}};
 vm.createContext(ctx);vm.runInContext(source,ctx);return{ctx,elements,sent};
}
test('soundtrack handlers are in the executable script rather than CSS',()=>{
 assert.ok(start>=0);assert.ok(!html.split('<style>')[1].split('</style>')[0].includes('function renderSoundtrack'));
});
test('time display rounds across minute/hour boundaries to valid timecodes',()=>{
 const{ctx}=setup();assert.equal(ctx.audioTime(59.9999),'01:00.000');assert.equal(ctx.audioTime(3599.9999),'1:00:00.000');
});
test('timeline shows enabled cues at their episode positions and warns about late cues',()=>{
 const{ctx,elements}=setup();
 ctx.S.audio_cues=[{id:1,name:'Bell',kind:'sfx',start:2,duration:1,enabled:1,volume:1,url:'/bell.wav'},
                   {id:2,name:'Late',kind:'sfx',start:20,duration:1,enabled:1,volume:1,url:'/late.wav'}];
 ctx.renderSoundtrack();assert.match(elements.audiotimeline.innerHTML,/left:20%/);assert.match(elements.audiotimeline.innerHTML,/width:10%/);
 assert.match(elements.audiocues.innerHTML,/Starts after episode end/);assert.match(elements.audioexportnote.textContent,/dialogue-only master/);
});
test('soundtrack export targets the chosen rendered episode',async()=>{
 const{ctx,sent}=setup();ctx.exportSoundtrack();await Promise.resolve();
 assert.equal(sent[0].u,'/api/audio/remix');assert.equal(sent[0].b.export_id,7);assert.equal(sent[0].b.use_music,false);
});
