const{test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const{readFileSync}=require('node:fs');
const code=readFileSync(require('node:path').join(__dirname,'studio.js'),'utf8');
function setup(){
  const ctx={document:{addEventListener(){},querySelector:()=>null},setTimeout(){},clearTimeout(){},fetch:async()=>({ok:true,status:304,headers:{get:()=>null},json:async()=>({})}),console};
  vm.createContext(ctx);vm.runInContext(code,ctx);
  vm.runInContext('state={assets:[{id:"m",name:"Mika",kind:"character",refs:[]},{id:"r",name:"Roof",kind:"location",refs:[]}],chapters:[],servers:[]}',ctx);
  return ctx;
}
class Form{constructor(map){this.map=map}getAll(k){return this.map[k]||[]}}
test('balloon rows offer characters only and read back in order',()=>{
  const ctx=setup();
  const html=ctx.balloonEditor({balloons:[{id:"a".repeat(32),kind:"shout",speaker:"m",text:"Run!",off_panel:false}]});
  assert.match(html,/value="m" selected>Mika/);assert.doesNotMatch(html,/Roof/);assert.match(html,/value="shout" selected/);
  const values=ctx.balloonValues(new Form({"b-id":["a".repeat(32),""],"b-kind":["shout","caption"],"b-speaker":["m",""],"b-off":["in","in"],"b-text":["Run!","Later."]}));
  assert.equal(values.length,2);assert.equal(values[0].id,"a".repeat(32));assert.equal(values[1].id,undefined);assert.equal(values[1].kind,"caption");
});
test('balloon labels name the speaker or the lettering type',()=>{
  const ctx=setup();
  assert.equal(ctx.balloonWho({kind:"speech",speaker:"m",off_panel:true}),"Mika (off-panel)");
  assert.equal(ctx.balloonWho({kind:"sfx",speaker:""}),"SFX");
});
