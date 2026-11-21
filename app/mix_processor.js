/* The same block RMS and envelope are implemented by audio_processing.py. */
class StudioMixer extends AudioWorkletProcessor{
 constructor(options){
  super();this.config=options.processorOptions;this.count=0;this.energy=0;
  this.previous=1;this.target=1;this.peaks=[0,0,0,0];
  this.duck=false;this.blocks=0;
  this.port.onmessage=event=>{this.duck=!!event.data.duck};
 }
 process(inputs,outputs){
  const output=outputs[0],config=this.config;
  const size=Math.max(1,Math.round(sampleRate*config.block_seconds));
  for(let i=0;i<output[0].length;i++){
   const gain=this.duck?this.previous+(this.target-this.previous)*this.count/size:1;
   for(let channel=0;channel<2;channel++){
    let sum=0;
    for(let bus=0;bus<4;bus++){
     let sample=inputs[bus]?.[channel]?.[i]??inputs[bus]?.[0]?.[i]??0;
     if(bus===0)this.energy+=sample*sample/2;
     if(bus===1)sample*=gain;
     this.peaks[bus]=Math.max(this.peaks[bus],Math.abs(sample));sum+=sample;
    }
    output[channel][i]=Math.max(-config.limit,Math.min(config.limit,sum));
   }
   if(++this.count===size){
    const rms=Math.sqrt(this.energy/size);
    const desired=rms>config.threshold?Math.pow(config.threshold/rms,1-1/config.ratio):1;
    this.previous=this.target;
    const tau=desired<this.target?config.attack:config.release;
    this.target=desired+(this.target-desired)*Math.exp(-config.block_seconds/tau);
    this.count=0;this.energy=0;
    if(++this.blocks%5===0){this.port.postMessage({peaks:this.peaks});this.peaks=[0,0,0,0]}
   }
  }
  return true;
 }
}
registerProcessor('studio-mixer',StudioMixer);
