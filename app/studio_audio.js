/* Decode Studio audio once and schedule it on the mixer clock, without media-element seeks. */
const STUDIO_AUDIO_BUFFERS=new Map(),STUDIO_AUDIO_SOURCES=new Map();
let STUDIO_AUDIO_BYTES=0;
function studioAudioBus(id){return id==='dialogue'||id.startsWith('dialogue-')?'dialogue':id==='bed'?'music':S.audio_cues.find(cue=>'cue'+cue.id===id)?.kind||'sfx'}
function stopStudioAudio(id){
 if(id===undefined)for(const[url,entry]of STUDIO_AUDIO_BUFFERS)if(entry.error)STUDIO_AUDIO_BUFFERS.delete(url);
 const ids=id===undefined?[...STUDIO_AUDIO_SOURCES.keys()]:[id];
 for(const key of ids){const node=STUDIO_AUDIO_SOURCES.get(key);if(!node)continue;node.source.onended=null;try{node.source.stop()}catch{}node.source.disconnect();node.gain.disconnect();STUDIO_AUDIO_SOURCES.delete(key)}
}
function studioAudioBuffer(url){
 let entry=STUDIO_AUDIO_BUFFERS.get(url);if(entry)return entry;
 entry={buffer:null,error:null,bytes:0};STUDIO_AUDIO_BUFFERS.set(url,entry);
 entry.promise=(async()=>{
  const response=await fetch(url);if(!response.ok)throw new Error('Audio file could not be loaded.');
  entry.buffer=await MIX_CONTEXT.decodeAudioData(await response.arrayBuffer());
  entry.bytes=entry.buffer.length*entry.buffer.numberOfChannels*4;STUDIO_AUDIO_BYTES+=entry.bytes;
  // Keep active buffers; evict old unused decodes above a 128 MiB cache budget.
  const active=new Set([...STUDIO_AUDIO_SOURCES.values()].map(node=>node.url));
  for(const[key,value]of STUDIO_AUDIO_BUFFERS){if(STUDIO_AUDIO_BYTES<=128*1024*1024)break;if(key!==url&&value.buffer&&!active.has(key)){STUDIO_AUDIO_BYTES-=value.bytes;STUDIO_AUDIO_BUFFERS.delete(key)}}
 })().catch(error=>{entry.error=error}).finally(()=>{if(typeof updateEdit==='function')updateEdit(false)});
 return entry;
}
function syncStudioAudio(tracks,playing,force=false){
 const entries=tracks.map(track=>studioAudioBuffer(track.url));
 const failed=entries.find(entry=>entry.error);if(failed)throw failed.error;
 if(entries.some(entry=>!entry.buffer)){stopStudioAudio();return false}
 const active=new Set(tracks.map(track=>track.id));
 for(const id of STUDIO_AUDIO_SOURCES.keys())if(!active.has(id))stopStudioAudio(id);
 if(!playing){stopStudioAudio();return true}
 const now=MIX_CONTEXT.currentTime,when=now+.005;
 tracks.forEach((track,index)=>{
  const buffer=entries[index].buffer;
  let offset=Math.max(0,track.time),end=buffer.duration,start=0;
  if(track.loop){start=track.loopStart||0;end=Math.min(buffer.duration,track.loopEnd||buffer.duration);offset=start+Math.max(0,offset-start)%Math.max(.001,end-start)}
  if(offset>=buffer.duration){stopStudioAudio(track.id);return}
  const old=STUDIO_AUDIO_SOURCES.get(track.id),bus=studioAudioBus(track.id);
  // Clock corrections are infrequent; never restart healthy sources each tick.
  let expected=old?old.offset+Math.max(0,now-old.when):offset;
  if(old&&track.loop)expected=start+Math.max(0,expected-start)%Math.max(.001,end-start);
  let drift=Math.abs(expected-offset);
  if(track.loop)drift=Math.min(drift,Math.abs(end-start-drift));
  let volume=track.volume;
  if(track.dialogueFade){const length=Math.min(track.length,buffer.duration-track.sourceIn);volume=Math.min(1,track.elapsed/track.dialogueFade,(length-track.elapsed)/track.dialogueFade)}
  if(old&&!force&&old.url===track.url&&old.bus===bus&&drift<.2){old.gain.gain.setTargetAtTime(Math.max(0,volume),now,.01);return}
  stopStudioAudio(track.id);
  const source=MIX_CONTEXT.createBufferSource(),gain=MIX_CONTEXT.createGain();source.buffer=buffer;
  source.loop=!!track.loop;source.loopStart=start;source.loopEnd=end;
  source.connect(gain);gain.connect(MIX_BUSES.get(bus).gain);gain.gain.setValueAtTime(Math.max(0,volume),now);
  const node={source,gain,url:track.url,bus,when,offset};STUDIO_AUDIO_SOURCES.set(track.id,node);
  source.onended=()=>{if(STUDIO_AUDIO_SOURCES.get(track.id)===node){source.disconnect();gain.disconnect();STUDIO_AUDIO_SOURCES.delete(track.id)}};
  source.start(when,offset);
 });
 return true;
}
