const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/finishing.js'),'utf8');
function setup(){
 const calls=[],elements={},frames=[];
 const nodes=[];
 class Node{
  constructor(){this.gain={setTargetAtTime:value=>this.volume=value};this.pan={setTargetAtTime:value=>this.balance=value};this.fftSize=256}
  connect(){return this}
  getFloatTimeDomainData(samples){samples.fill(this.signal||0)}
 }
 const mixer=Object.fromEntries(['dialogue','music','ambience','sfx'].map(name=>[name,{volume:1,pan:0,mute:false,solo:false}]));
 const ctx={S:{finishing:{mixer:{...mixer,duck:true},library:[]},audio_cues:[]},
  EDIT_PLAYING:true,EDIT_AUDIOS:new Map(),
  AudioContext:class{
   constructor(){this.currentTime=0;this.destination={}}
   resume(){return Promise.resolve()}
   createMediaElementSource(){return new Node()}
   createGain(){return new Node()}
   createStereoPanner(){return new Node()}
   createAnalyser(){const node=new Node();nodes.push(node);return node}
  },
  $:selector=>elements[selector]||(elements[selector]={}),requestAnimationFrame:fn=>{frames.push(fn);return frames.length},
  toast:message=>calls.push(message),WeakMap,Map,Float32Array,structuredClone};
 vm.createContext(ctx);vm.runInContext(source,ctx);
 return {ctx,nodes,elements,frames};
}
test('live mixer wires each media element once, ducks music and respects solo/pan',()=>{
 const {ctx,nodes,elements}=setup();
 const voice={paused:false},music={paused:false};
 ctx.EDIT_AUDIOS.set('dialogue',voice);ctx.EDIT_AUDIOS.set('bed',music);
 ctx.startMixAudio();ctx.wireMixAudio(voice,'dialogue');ctx.wireMixAudio(music,'bed');
 nodes[0].signal=.2;
 ctx.measureMixAudio();
 const output=vm.runInContext("MIX_ACTIVE.get('bed').gain.volume",ctx);
 assert.equal(output,.18);
 assert.equal(elements['#mix-meter-dialogue'].value,.20000000298023224);
 ctx.wireMixAudio(voice,'dialogue');assert.equal(nodes.length,2);
 ctx.S.finishing.mixer.dialogue.solo=true;ctx.S.finishing.mixer.dialogue.pan=-.5;
 ctx.measureMixAudio();
 assert.equal(vm.runInContext("MIX_ACTIVE.get('bed').gain.volume",ctx),0);
 assert.equal(vm.runInContext("MIX_ACTIVE.get('dialogue').pan.balance",ctx),-.5);
});
