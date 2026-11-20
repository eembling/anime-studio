/* Audio finishing controls use the existing dialogs and page layout. */
const MIX_NAMES={dialogue:'Dialogue',music:'Music',ambience:'Ambience',sfx:'Effects'};
let MIX_DRAFT=null, MIX_CONTEXT=null, MIX_FRAME=null;
const MIX_NODES=new WeakMap();
const MIX_ACTIVE=new Map();

function finishingPost(action,body={}){
 return post('/api/finishing',{action,...body});
}
function startMixAudio(){
 if(!MIX_CONTEXT)MIX_CONTEXT=new AudioContext();
 if(!MIX_FRAME)measureMixAudio();
 MIX_CONTEXT.resume().catch(()=>toast('Audio mixer could not start.',true));
}
function effectiveMixer(){
 return MIX_DRAFT||S.finishing?.mixer;
}
function wireMixAudio(audio,id,volume=1){
 if(!MIX_CONTEXT||!effectiveMixer())return;
 let node=MIX_NODES.get(audio);
 if(!node){
  const source=MIX_CONTEXT.createMediaElementSource(audio),gain=MIX_CONTEXT.createGain();
  const pan=MIX_CONTEXT.createStereoPanner(),meter=MIX_CONTEXT.createAnalyser();
  meter.fftSize=256;
  const cueGain=MIX_CONTEXT.createGain();
  source.connect(cueGain).connect(gain).connect(pan).connect(meter).connect(MIX_CONTEXT.destination);
  node={gain,pan,meter,cueGain,samples:new Float32Array(meter.fftSize),audio};
  MIX_NODES.set(audio,node);
 }
 node.cueGain.gain.setTargetAtTime(Math.max(0,volume),MIX_CONTEXT.currentTime,.01);
 node.bus=id==='dialogue'||id.startsWith('dialogue-')?'dialogue':id==='bed'?'music':
   (S.audio_cues.find(cue=>'cue'+cue.id===id)?.kind||'sfx');
 MIX_ACTIVE.set(id,node);
 if(!MIX_FRAME)measureMixAudio();
 return true;
}
function measureMixAudio(){
 const mixer=effectiveMixer();
 if(!mixer||!MIX_CONTEXT){MIX_FRAME=null;return}
 const peaks=Object.fromEntries(Object.keys(MIX_NAMES).map(bus=>[bus,0]));
 for(const[id,node]of MIX_ACTIVE){
  if(node.audio.paused||!EDIT_AUDIOS.has(id)){MIX_ACTIVE.delete(id);continue}
  node.meter.getFloatTimeDomainData(node.samples);
  const peak=Math.max(...node.samples.map(Math.abs));
  peaks[node.bus]=Math.max(peaks[node.bus],peak);
 }
 const solo=Object.keys(MIX_NAMES).some(bus=>mixer[bus].solo);
 for(const node of MIX_ACTIVE.values()){
  const bus=mixer[node.bus],muted=bus.mute||(solo&&!bus.solo);
  const duck=node.bus==='music'&&mixer.duck&&peaks.dialogue>.025?.18:1;
  node.gain.gain.setTargetAtTime(muted?0:bus.volume*duck,MIX_CONTEXT.currentTime,duck<1?.015:.35);
  node.pan.pan.setTargetAtTime(bus.pan,MIX_CONTEXT.currentTime,.015);
 }
 for(const bus of Object.keys(MIX_NAMES)){
  const meter=$('#mix-meter-'+bus),level=$('#mix-level-'+bus);
  if(meter)meter.value=peaks[bus];
  if(level)level.textContent=peaks[bus]>0?Math.max(-60,20*Math.log10(peaks[bus])).toFixed(1)+' dBFS':'−∞ dBFS';
 }
 MIX_FRAME=EDIT_PLAYING||MIX_DRAFT?requestAnimationFrame(measureMixAudio):null;
}
function openAudioMixer(){
 MIX_DRAFT=structuredClone(S.finishing.mixer);
 modal(`<div class="dlg-h"><h2>Audio mixer</h2></div><div class="dlg-b">
 <p>Play Studio to monitor levels. Controls preview immediately. Save to apply them to rendered previews and exports.</p>
 <div class="mix-buses">${Object.entries(MIX_NAMES).map(([bus,name])=>{
  const value=MIX_DRAFT[bus];
  return `<section><h3>${name}</h3><label for="mix-volume-${bus}">Gain</label><input id="mix-volume-${bus}" type="range" min="0" max="4" step=".01" value="${value.volume}" oninput="MIX_DRAFT['${bus}'].volume=+this.value;$('#mix-value-${bus}').textContent=this.value"><output id="mix-value-${bus}">${value.volume}</output>
  <label for="mix-pan-${bus}">Pan · left / center / right</label><input id="mix-pan-${bus}" type="range" min="-1" max="1" step=".01" value="${value.pan}" oninput="MIX_DRAFT['${bus}'].pan=+this.value">
  <label class="check"><input type="checkbox" ${value.mute?'checked':''} onchange="MIX_DRAFT['${bus}'].mute=this.checked">Mute</label>
  <label class="check"><input type="checkbox" ${value.solo?'checked':''} onchange="MIX_DRAFT['${bus}'].solo=this.checked">Solo</label>
  <meter id="mix-meter-${bus}" min="0" max="1" high=".89" optimum=".3" aria-label="${name} live peak"></meter><output id="mix-level-${bus}">−∞ dBFS</output></section>`;
 }).join('')}</div>
 <label class="check"><input id="mix-duck" type="checkbox" ${MIX_DRAFT.duck?'checked':''} onchange="MIX_DRAFT.duck=this.checked">Automatically duck music under dialogue</label>
 <button class="btn" onclick="if(tab!=='studio')go('studio');playEdit()">Play / pause Studio</button></div><div class="dlg-f"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn pri" onclick="saveAudioMixer()">Save mixer</button></div>`,
 {onClose:()=>{MIX_DRAFT=null}});
}
async function saveAudioMixer(){
 const result=await finishingPost('mixer',{mixer:MIX_DRAFT});
 if(result.ok!==false)closeModal();
}
function openShotFinishing(id){
 const shot=S.shots.find(shot=>shot.id===id);if(!shot)return;
 modal(`<div class="dlg-h"><h2>Shot ${pad(shot.seq)} · Picture and dialogue</h2></div><div class="dlg-b">
 <label class="f" for="finish-transition">Transition into the next shot</label><select id="finish-transition"><option value="cut">Cut</option><option value="dissolve">Dissolve</option><option value="black">Fade through black</option></select>
 <label class="f" for="finish-seconds">Transition duration (seconds)</label><input id="finish-seconds" type="number" min=".05" max="5" step=".05" value="${shot.transition_seconds||.5}">
 <p>Dissolves reveal the next picture over the outgoing final frame. Shot lengths and cue positions stay fixed.</p>
 <label class="f" for="finish-offset">Dialogue start offset (seconds)</label><input id="finish-offset" type="number" min="-30" max="30" step=".01" value="${shot.dialogue_offset||0}">
 <p>A negative offset starts the dialogue before the picture cut (J-cut). A positive offset starts it later.</p>
 <label class="f" for="finish-tail">Allow dialogue beyond the shot end (seconds)</label><input id="finish-tail" type="number" min="0" max="30" step=".01" value="${shot.dialogue_tail||0}">
 <p>Uses the remaining recorded audio as an L-cut into the following shot.</p>
 <label class="f" for="finish-fade">Dialogue fade in / fade out (seconds)</label><input id="finish-fade" type="number" min="0" max="5" step=".01" value="${shot.dialogue_fade||0}">
 <p>Overlap adjacent dialogue with these offsets and fades to create an audio crossfade. Use a rendered scene preview to check final timing.</p>
 </div><div class="dlg-f"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn pri" onclick="saveShotFinishing(${id})">Save</button></div>`);
 $('#finish-transition').value=shot.transition||'cut';
}
async function saveShotFinishing(id){
 const result=await finishingPost('shot',{shot_id:id,transition:$('#finish-transition').value,seconds:+$('#finish-seconds').value,
  offset:+$('#finish-offset').value,tail:+$('#finish-tail').value,fade:+$('#finish-fade').value});
 if(result.ok!==false)closeModal();
}
function finishingRenderOptions(){
 return {use_music:$('#usemusic').checked,music_volume:+$('#mvol').value,use_timed:$('#usetimed').checked};
}
async function renderFinishedShot(id){
 const result=await finishingPost('render',{shot_id:id,...finishingRenderOptions()});
 if(result.ok!==false){go('assemble');toast(result.cached?'Using cached shot export.':'Rendering shot with sound.')}
}
function renderCurrentShot(){
 const item=shotAt(EDIT_TIME);
 if(item)renderFinishedShot(item.shot.id);
}
async function renderFinishedScene(scene=null){
 const item=shotAt(EDIT_TIME);
 if(scene===null&&!item)return toast('Choose a shot in the scene first.',true);
 const result=await finishingPost('render',{scene:scene??item.shot.scene,...finishingRenderOptions()});
 if(result.ok!==false){go('assemble');toast(result.cached?'Using cached scene preview.':'Rendering scene picture and sound.')}
}
let SOUND_FILTER='', SOUND_CATEGORY='';
function stopLibraryAudio(){
 document.querySelectorAll('#sound-results audio').forEach(audio=>audio.pause());
}
function openSoundLibrary(){
 stopLibraryAudio();
 modal(`<div class="dlg-h"><h2>Sound library</h2></div><div class="dlg-b">
 <div class="toolbar"><label for="sound-search">Search sounds</label><input id="sound-search" type="search" value="${esc(SOUND_FILTER)}" oninput="SOUND_FILTER=this.value;renderSoundLibrary()">
 <label for="sound-category">Category</label><select id="sound-category" onchange="SOUND_CATEGORY=this.value;renderSoundLibrary()"></select><button class="btn" onclick="editLibrarySound()">Import sound</button></div>
 <div id="sound-results"></div></div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button></div>`,{onClose:stopLibraryAudio});
 const categories=[...new Set(S.finishing.library.map(sound=>sound.category))];
 $('#sound-category').innerHTML='<option value="">All categories</option>'+categories.map(category=>`<option>${esc(category)}</option>`).join('');
 $('#sound-category').value=SOUND_CATEGORY;renderSoundLibrary();
}
function renderSoundLibrary(){
 const query=SOUND_FILTER.trim().toLowerCase();
 const rows=S.finishing.library.filter(sound=>(!SOUND_CATEGORY||sound.category===SOUND_CATEGORY)&&
  [sound.name,sound.tags,sound.category].some(value=>value.toLowerCase().includes(query)));
 $('#sound-results').innerHTML=rows.map(sound=>`<article class="sound-item"><h3>${esc(sound.name)}</h3><p>${esc(sound.category)} · ${esc(sound.tags)} · ${sound.seconds.toFixed(2)}s</p>
 <audio controls preload="none" src="${esc(sound.url)}"></audio><div class="toolbar"><button class="btn" onclick="useLibrarySound('${sound.id}')">Add to episode</button><button class="btn" onclick="editLibrarySound('${sound.id}')">Edit tags</button>
 <button class="btn danger" onclick="deleteLibrarySound('${sound.id}')">Remove from library</button></div></article>`).join('')||'<p>No matching sounds.</p>';
}
function editLibrarySound(id){
 stopLibraryAudio();
 const sound=S.finishing.library.find(sound=>sound.id===id)||{};
 modal(`<div class="dlg-h"><h2>${id?'Edit sound':'Import sound'}</h2></div><div class="dlg-b">
 <label class="f" for="sound-file">Audio file${id?' (optional replacement)':''}</label><input id="sound-file" type="file" accept="audio/*">
 <label class="f" for="sound-name">Name</label><input id="sound-name" value="${esc(sound.name||'')}">
 <label class="f" for="sound-kind">Category</label><input id="sound-kind" value="${esc(sound.category||'Effects')}">
 <label class="f" for="sound-tags">Tags, separated by commas</label><input id="sound-tags" value="${esc(sound.tags||'')}">
 </div><div class="dlg-f"><button class="btn" onclick="openSoundLibrary()">Back</button><button class="btn pri" onclick="saveLibrarySound('${id||''}',this)">Save sound</button></div>`);
}
async function saveLibrarySound(id,button){
 button.disabled=true;
 try{
  const file=$('#sound-file').files[0];
  const result=await finishingPost('library_save',{id:id||null,name:$('#sound-name').value,category:$('#sound-kind').value,
   tags:$('#sound-tags').value,...(file?{file:await readFile(file)}:{})});
  if(result.ok!==false)openSoundLibrary();
 }finally{button.disabled=false}
}
async function deleteLibrarySound(id){
 if(!confirm('Remove this sound from the library? Existing cues retain their audio.'))return;
 const result=await finishingPost('library_delete',{id});
 if(result.ok!==false)renderSoundLibrary();
}
function useLibrarySound(id){
 stopLibraryAudio();
 const sound=S.finishing.library.find(sound=>sound.id===id);
 if(!sound)return;
 editAudioCue(null,sound);
}
