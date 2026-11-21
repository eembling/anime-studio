const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/finishing.js'),'utf8');
const config=JSON.parse(fs.readFileSync(path.join(__dirname,'../app/audio_mix.json')));
function setup(){
 const elements={},frames=[];
 class Node{
  constructor(){this.gain={setTargetAtTime:value=>this.volume=value};this.pan={setTargetAtTime:value=>this.balance=value}}
  connect(){return this}disconnect(){}
 }
 const mixer=Object.fromEntries(['dialogue','music','ambience','sfx'].map(name=>[name,{volume:1,pan:0,mute:false,solo:false}]));
 const ctx={S:{finishing:{mixer:{...mixer,duck:true},audio_config:config},audio_cues:[]},
  EDIT_PLAYING:true,EDIT_AUDIOS:new Map(),AudioWorkletNode:class extends Node{constructor(){super();this.port={postMessage(){}}}},
  AudioContext:class{
   constructor(){this.currentTime=0;this.destination={};this.audioWorklet={addModule:async()=>{}}}
   resume(){return Promise.resolve()}
   createMediaElementSource(){return new Node()}
   createGain(){return new Node()}createStereoPanner(){return new Node()}
  },
  $:selector=>elements[selector]||(elements[selector]={}),requestAnimationFrame:fn=>{frames.push(fn);return frames.length},
  toast(){},WeakMap,Map,Float32Array,structuredClone};
 vm.createContext(ctx);vm.runInContext(source,ctx);return {ctx,elements,frames};
}
test('live tracks connect to summed buses and apply solo/pan once per bus',async()=>{
 const {ctx}=setup();await ctx.startMixAudio();
 const audio={paused:false};ctx.wireMixAudio(audio,'bed',2);
 ctx.S.finishing.mixer.dialogue.solo=true;ctx.S.finishing.mixer.dialogue.pan=-.5;
 ctx.measureMixAudio();
 assert.equal(vm.runInContext("MIX_BUSES.get('music').gain.volume",ctx),0);
 assert.equal(vm.runInContext("MIX_BUSES.get('dialogue').pan.balance",ctx),-.5);
});
test('worklet meters the combined bus and uses the shared RMS duck envelope',()=>{
 let Processor;
 const messages=[];
 class Base{constructor(){this.port={postMessage:data=>messages.push(data)}}}
 const ctx={AudioWorkletProcessor:Base,sampleRate:48000,Math,registerProcessor:(_,type)=>Processor=type};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/mix_processor.js'),'utf8'),ctx);
 const processor=new Processor({processorOptions:config});
 processor.port.onmessage({data:{duck:true}});
 const make=value=>[new Float32Array(480).fill(value),new Float32Array(480).fill(value)];
 const output=[make(0)];
 for(let i=0;i<10;i++)processor.process([make(.2),make(.4),make(0),make(.8)],output);
 assert.ok(messages.at(-1).peaks[3]>.79);
 assert.ok(output[0][0][0]<=.95);
 assert.ok(processor.target<.3);
 assert.ok(messages.at(-1).peaks[1]<.2);
});
