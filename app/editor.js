/* Episode editing, scene cleanup, recovery, series library and delivery. */
let EDIT_TIME=0,EDIT_PLAYING=false,EDIT_CLOCK=0,EDIT_TIMER=null,EDIT_DRAG=null,EDIT_MEDIA_KEY='',EDIT_AUDIOS=new Map(),EDIT_DURATIONS=new Map(),EDIT_WAITING=false;
const editorPost=(tool,data={})=>post('/api/editor',{tool,...data});
const editFps=()=>+S.settings.out_fps||16;
function editTimecode(seconds){const fps=editFps(),frame=Math.max(0,Math.floor(seconds*fps+1e-6));return [Math.floor(frame/(fps*3600)),Math.floor(frame/(fps*60))%60,Math.floor(frame/fps)%60,frame%fps].map(value=>String(value).padStart(2,'0')).join(':')}
const editDuration=shot=>shot.timeline_duration??shot.duration;
const shotAt=time=>{let start=0;for(const shot of S.shots){if(time<start+editDuration(shot))return{shot,start};start+=editDuration(shot)}return null};
function renderStudio(){
 const state=S.editor;if(!state)return;
 if(!same('editor-episodes',[state.episodes,S.settings.active_episode]))$('#episode-list').innerHTML=state.episodes.map(e=>`<div class="row"><b>${esc(e.title)}</b>${e.id===S.settings.active_episode?'<span class="pill">Current</span>':`<button class="btn sm" onclick="openEpisode('${e.id}')">Open episode</button>`}</div>`).join('')||'<p>Save the current episode to start your series library.</p>';
 if(!same('editor-history',state.history))$('#edit-history').innerHTML=state.history.map(h=>`<div class="row"><span>${esc(new Date(h.created*1000).toLocaleString())} · ${esc(h.label)}</span><button class="btn sm" onclick="restoreHistory('${h.id}')">Restore</button></div>`).join('');
 if(!same('editor-timeline',[S.shots.map(s=>[s.id,s.seq,s.duration,s.trim_in,s.keyframe_take,s.clip_take,s.final_take]),S.audio_cues,editFps()])){
  const total=S.shots.reduce((n,s)=>n+editDuration(s),0);$('#edit-playhead').max=total;$('#edit-playhead').step=1/editFps();
  let start=0;$('#edit-track').innerHTML=S.shots.map(s=>{const at=start;start+=editDuration(s);return `<button class="timeline-shot btn" draggable="true" data-shot="${s.id}" style="flex:0 0 ${Math.max(85,editDuration(s)*18)}px" onclick="seekEdit(${at})"><b>Shot ${pad(s.seq)}</b><span>${esc(s.scene)}</span><small>${audioTime(s.duration)}</small></button>`}).join('');
  if(typeof renderTimelineTracks==='function')renderTimelineTracks();
  for(const node of document.querySelectorAll('[data-shot]')){node.ondragstart=e=>{if(e.target.closest?.('[data-trim]')){e.preventDefault();return}EDIT_DRAG=+node.dataset.shot;e.dataTransfer.setData('text/plain',node.dataset.shot)};node.ondragover=e=>e.preventDefault();node.ondrop=e=>{e.preventDefault();const target=+node.dataset.shot;if(!EDIT_DRAG||target===EDIT_DRAG)return;const ids=S.shots.map(s=>s.id).filter(id=>id!==EDIT_DRAG);ids.splice(ids.indexOf(target),0,EDIT_DRAG);editorPost('timeline',{action:'reorder',ids});EDIT_DRAG=null}}
  $('#edit-cues').innerHTML=(S.audio_cues||[]).map(c=>`<button class="btn sm" onclick="seekEdit(${c.start});editAudioCue(${c.id})">${esc(c.name)} · ${audioTime(c.start)}${c.anchor_shot?' · follows shot':''}${c.anchor_missing?' · anchor missing':''}</button>`).join('');
 }
 $('#edit-state').textContent=`${editTimecode(EDIT_TIME)} / ${editTimecode(S.shots.reduce((n,s)=>n+editDuration(s),0))} · ${editFps()} fps`;
}
function stopEdit(){EDIT_PLAYING=false;clearInterval(EDIT_TIMER);EDIT_TIMER=null;const v=$('#edit-monitor video');if(v)v.pause();for(const a of EDIT_AUDIOS.values())a.pause();$('#edit-play').textContent='Play episode'}
function seekEdit(value){EDIT_TIME=Math.max(0,Math.min(+value,S.shots.reduce((n,s)=>n+editDuration(s),0)));EDIT_CLOCK=performance.now();updateEdit(true)}
function frameEdit(direction){stopEdit();const fps=editFps();seekEdit((Math.round(EDIT_TIME*fps)+direction)/fps)}
function playEdit(){
 if(EDIT_PLAYING)return stopEdit();
 const total=S.shots.reduce((n,s)=>n+editDuration(s),0);if(!total)return;
 if(EDIT_TIME>=total)seekEdit(0);
 if(typeof startMixAudio==='function'&&!MIX_READY){
  $('#edit-play').textContent='Starting audio…';
  startMixAudio().then(()=>playEdit()).catch(error=>{stopEdit();toast('Audio could not start: '+error.message,true)});
  return;
 }
 if(typeof measureMixAudio==='function'&&!MIX_FRAME)measureMixAudio();
 EDIT_PLAYING=true;EDIT_CLOCK=performance.now();$('#edit-play').textContent='Pause';updateEdit(true);
 EDIT_TIMER=setInterval(tickEdit,1000/editFps());
}
function tickEdit(){
 const now=performance.now(),elapsed=(now-EDIT_CLOCK)/1000;EDIT_CLOCK=now;
 if(!EDIT_WAITING)EDIT_TIME+=elapsed;
 const total=S.shots.reduce((n,s)=>n+editDuration(s),0);
 if(EDIT_TIME>=total){EDIT_TIME=total;stopEdit();$('#edit-playhead').value=total;$('#edit-state').textContent=editTimecode(total)+' · '+editFps()+' fps';return}
 updateEdit(false);
}
function editSourceAt(s,source){
 const root=take(s.final_take)||take(s.clip_take);if(!root)return{selected:null,offset:source};
 const chain=root.stage==='upscale'?[root]:[root,...(s.cont||[]).map(take).filter(Boolean)];let offset=source;
 for(let i=0;i<chain.length;i++){
  const t=chain[i],duration=EDIT_DURATIONS.get(t.url)||(t.frames&&t.fps?t.frames/t.fps:5);
  // Continuations repeat the source's final frame. Browser duration is authoritative
  // once loaded; output fps only controls frame stepping, never playback speed.
  const frame=t.frames?duration/t.frames:1/(+t.fps||16),skip=i?frame:0,length=Math.max(0,duration-skip);
  if(offset<length||i===chain.length-1)return{selected:t,offset:Math.max(0,offset+skip)};
  offset-=length;
 }
}
function syncEditMedia(media,time,force,video=false){
 if(!media.readyState)return false;
 const duration=media.duration,finite=Number.isFinite(duration),ended=finite&&(time>=duration||(media.ended&&time>=duration-.08));
 const target=Math.max(0,finite?Math.min(time,Math.max(0,duration-(video ? .001 : 0))):time);
 media.playbackRate=1;
 // Reassigning even the same currentTime starts a new asynchronous WebKit seek.
 // During a buffer wait the clock is frozen: let that seek finish instead of
 // resetting it on every metadata/transport update.
 const drift=Math.abs(media.currentTime-target);
 if(!media.seeking&&drift>(force ? .001 : .08))media.currentTime=target;
 media._editHold=ended;
 // HAVE_CURRENT_DATA is enough to start; some local WebKit media only
 // fetch their next buffer after play(). Waiting for HAVE_FUTURE_DATA first
 // can deadlock a paused audio/video pair indefinitely.
 return ended||(!media.seeking&&media.readyState>=2);
}
function runEditMedia(media,playing){
 if(!playing||media._editHold){media.pause();return}
 if(media.paused&&!media._editStarting){
  media._editStarting=true;
  media.play().catch(error=>{if(error?.name!=='AbortError'&&EDIT_PLAYING){stopEdit();toast(error?.name==='NotAllowedError'?'Playback was blocked by the browser. Reopen the updated Mac app and press Play episode.':'Playback could not start: '+(error?.message||error?.name||'unknown media error'),true)}}).finally(()=>media._editStarting=false);
 }
}
function editMediaError(media,label){
 if(!media.error)return false;
 stopEdit();const detail=({1:'loading was interrupted',2:'the file could not be loaded',3:'the file could not be decoded',4:'the format is unsupported'})[media.error.code]||'unknown media error';
 $('#edit-state').textContent=label+': '+detail;
 if(!media._editErrorShown){media._editErrorShown=true;toast(label+': '+detail+'. Check that the generated file is available and playable.',true)}
 return true;
}
function updateEdit(force){
 const item=shotAt(EDIT_TIME);if(!item){stopEdit();return}const s=item.shot,local=EDIT_TIME-item.start,source=local+(s.trim_in||0);
 const {selected,offset}=editSourceAt(s,source);
 const image=!selected||s.kind==='still'&&!['lipsync','upscale'].includes(selected.stage),key=image?'img:'+s.keyframe_take:'video:'+selected.id;
 if(key!==EDIT_MEDIA_KEY){const old=$('#edit-monitor video');if(old){old.onloadedmetadata=null;old.onerror=null;old.pause()}EDIT_MEDIA_KEY=key;$('#edit-monitor').innerHTML=image?(take(s.keyframe_take)?`<img src="${take(s.keyframe_take).url}" alt="Shot ${pad(s.seq)}">`:'<p>Missing picture</p>'):`<video src="${selected.url}" muted playsinline preload="auto" aria-label="Shot ${pad(s.seq)} preview"></video>`;force=true}
 const media=[],video=$('#edit-monitor video');let ready=true;
 if(video){
  video.onerror=()=>editMediaError(video,'Shot '+pad(s.seq)+' video');
  if(editMediaError(video,'Shot '+pad(s.seq)+' video'))return;
  if(Number.isFinite(video.duration)&&video.duration>0){EDIT_DURATIONS.set(selected.url,video.duration)}
  video.onloadedmetadata=()=>{if($('#edit-monitor video')===video){EDIT_DURATIONS.set(selected.url,video.duration);updateEdit(true)}};
  // Recalculate after loading actual duration, including a continuation boundary.
  const current=editSourceAt(s,source);
  if(current.selected.id!==selected.id)return updateEdit(force);
  ready=syncEditMedia(video,current.offset,force,true)&&ready;media.push(video);
 }
 const tracks=[];
 let dialogueStart=0;
 for(const shot of S.shots){
  const offset=+(shot.dialogue_offset||0),tail=+(shot.dialogue_tail||0),fade=+(shot.dialogue_fade||0);
  const start=dialogueStart+offset,elapsed=EDIT_TIME-start,length=editDuration(shot)+tail;
  if(shot.audio_url&&elapsed>=0&&elapsed<length){
   let volume=1;if(fade)volume=Math.min(1,elapsed/fade,(length-elapsed)/fade);
   tracks.push({id:offset||tail||fade?'dialogue-'+shot.id:'dialogue',url:shot.audio_url,time:elapsed+(shot.trim_in||0),volume,dialogueFade:fade,elapsed,length,sourceIn:shot.trim_in||0});
  }
  dialogueStart+=editDuration(shot);
 }
 for(const cue of S.audio_cues||[]){if(!cue.enabled||EDIT_TIME<cue.start||EDIT_TIME>=cue.start+cue.duration)continue;let t=EDIT_TIME-cue.start,volume=cue.volume;if(cue.fade_in)volume*=Math.min(1,t/cue.fade_in);if(cue.fade_out)volume*=Math.min(1,(cue.duration-t)/cue.fade_out);t+=cue.offset;if(cue.loop)t=cue.offset+(t-cue.offset)%Math.max(.001,cue.source_seconds-cue.offset);tracks.push({id:'cue'+cue.id,url:cue.url,time:t,volume})}
 if(S.music_url&&$('#usemusic').checked)tracks.push({id:'bed',url:S.music_url,time:EDIT_TIME,volume:+$('#mvol').value,loop:true});
 const active=new Set();for(const t of tracks){
  active.add(t.id);let a=EDIT_AUDIOS.get(t.id);if(!a||a.src!==new URL(t.url,location.href).href){if(a){a.onloadedmetadata=null;a.onerror=null;a.pause()}a=new Audio(t.url);a.preload='auto';EDIT_AUDIOS.set(t.id,a)}
  a.onerror=()=>editMediaError(a,t.id==='dialogue'?'Shot '+pad(s.seq)+' dialogue':'Audio track');
  if(editMediaError(a,t.id==='dialogue'?'Shot '+pad(s.seq)+' dialogue':'Audio track'))return;
  if(t.dialogueFade&&Number.isFinite(a.duration)){
   const length=Math.min(t.length,a.duration-t.sourceIn);
   t.volume=Math.min(1,t.elapsed/t.dialogueFade,(length-t.elapsed)/t.dialogueFade);
  }
  const mixed=typeof wireMixAudio==='function'&&wireMixAudio(a,t.id,t.volume);
  a.volume=mixed?1:Math.min(1,Math.max(0,t.volume));a.loop=!!t.loop;
  a.onloadedmetadata=()=>{if(EDIT_AUDIOS.get(t.id)===a)updateEdit(true)};
  let desired=t.time;if(t.loop&&Number.isFinite(a.duration)&&a.duration>0)desired%=a.duration;
  ready=syncEditMedia(a,desired,force)&&ready;media.push(a);
 }
 for(const[id,a]of EDIT_AUDIOS)if(!active.has(id)){a.onloadedmetadata=null;a.onerror=null;a.pause()}
 for(const element of media)runEditMedia(element,EDIT_PLAYING&&ready);
 // A track can have its current frame buffered while play() is still waiting
 // for enough data to start. Keep the clock still without cancelling play().
 EDIT_WAITING=!ready||media.some(element=>element._editStarting&&element.readyState<3);
 const cursor=$('#timeline-cursor');if(cursor?.style)cursor.style.left=(100+EDIT_TIME*(typeof TIMELINE_ZOOM==='undefined'?30:TIMELINE_ZOOM))+'px';
 $('#edit-playhead').value=EDIT_TIME;$('#edit-state').textContent=`${editTimecode(EDIT_TIME)} · ${editFps()} fps · Shot ${pad(s.seq)} · source ${audioTime(source)}${EDIT_PLAYING&&EDIT_WAITING?' · Buffering…':''}`;
}

function editTimelineShot(){const item=shotAt(EDIT_TIME);if(!item)return toast('Add shots first',true);const s=item.shot;modal(`<div class="dlg-h"><h2>Trim shot ${pad(s.seq)}</h2></div><div class="dlg-b"><label class="f">Source in (seconds)</label><input id="et-in" type="number" min="0" step="${1/editFps()}" value="${s.trim_in||0}"><label class="f">Timeline duration (seconds)</label><input id="et-duration" type="number" min="0.5" step="${1/editFps()}" value="${s.duration}"><p>Edits affect episode assembly and preview. Source takes stay intact. Later shots and shot-anchored cues ripple automatically.</p></div><div class="dlg-f"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn pri" id="et-save">Save trim</button></div>`);$('#et-save').onclick=async()=>{const r=await editorPost('timeline',{action:'trim',id:s.id,trim_in:+$('#et-in').value,duration:+$('#et-duration').value});if(r.ok!==false){closeModal();EDIT_MEDIA_KEY='';seekEdit(EDIT_TIME)}}}
async function splitEdit(){const item=shotAt(EDIT_TIME);if(!item)return;stopEdit();if(!confirm('Split this shot at the playhead? Both parts keep their original source takes.'))return;await editorPost('timeline',{action:'split',id:item.shot.id,at:EDIT_TIME-item.start});EDIT_MEDIA_KEY=''}
async function sceneCleanup(mode){
 const preview=await editorPost('scene',{scene:PROD_SCENE,mode});if(preview.ok===false)return;
 const description=mode==='clips'?'Remove generated animation, continuation, lip-sync and upscale clips. Keep keyframes, script and voice audio.':'Reset generation prompts, cast links, camera, duration, retiming and continuity; remove selected/generated keyframes, video and voice takes. Keep the shot rows, dialogue, scene names and saved script.';
 modal(`<div class="dlg-h"><h2>${mode==='clips'?'Delete scene clips':'Reset scene settings'}</h2></div><div class="dlg-b"><h3>${esc(PROD_SCENE||'Untitled scene')}</h3><p>${description}</p><p>${preview.shots} shots · ${preview.clips} generated takes · ${preview.voices} voice takes.</p><p>A history checkpoint preserves the previous scene and its media. Existing exports remain available.</p>${preview.busy.length?'<p class="err">This scene has active or queued jobs. Finish or cancel them before clearing.</p>':''}</div><div class="dlg-f"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn danger" id="scene-clear" ${preview.busy.length?'disabled':''}>${mode==='clips'?'Delete scene clips':'Reset scene'}</button></div>`);
 $('#scene-clear').onclick=async()=>{const r=await editorPost('scene',{scene:PROD_SCENE,mode,apply:true,token:preview.token});if(r.ok!==false){VIEW={};sel.clear();closeModal();toast('Scene cleared. Restore it from Studio → History.')}};
}
function undoEdit(){const h=S.editor.history[0];if(h)restoreHistory(h.id)}
async function checkpointEdit(){const label=prompt('Checkpoint name','Before revisions');if(label)editorPost('history',{label})}
async function restoreHistory(id){stopEdit();if(confirm('Restore this checkpoint? The current state will be saved first. Finish or cancel queued renders before restoring.')){await editorPost('history',{action:'restore',id});VIEW={};sel.clear();EDIT_MEDIA_KEY='';seekEdit(0)}}
async function episodeAction(action){stopEdit();const title=prompt(action==='new'?'New episode title':'Duplicate episode title',action==='new'?'New episode':S.settings.title+' copy');if(title){const r=await editorPost('library',{action,title});if(r.ok!==false){VIEW={};sel.clear();EDIT_MEDIA_KEY='';seekEdit(0)}}}
async function openEpisode(id){stopEdit();if(confirm('Save this episode and open the selected episode? Cast and reference voices are shared.')){const r=await editorPost('library',{action:'open',id});if(r.ok!==false){VIEW={};sel.clear();EDIT_MEDIA_KEY='';seekEdit(0)}}}
async function backupProject(){const r=await editorPost('backup');if(r.ok!==false)$('#project-download').innerHTML=`<a class="btn" href="${r.url}" download>Download portable project archive</a>`}
function importProject(){if(!confirm('Import a project archive? A checkpoint saves the current episode first. Finish or cancel renders before importing.'))return;pick('.zip',async file=>{const r=await editorPost('import',{file});if(r.ok!==false){VIEW={};EDIT_MEDIA_KEY='';toast('Project archive imported')}})}
function editContinuity(){
 const c=S.editor.continuity[PROD_SCENE]||{lighting:'',notes:'',variants:[]};
 modal(`<div class="dlg-h"><h2>Scene continuity · ${esc(PROD_SCENE)}</h2></div><div class="dlg-b"><label class="f">Lighting and time of day</label><input id="b-light" value="${esc(c.lighting)}"><label class="f">Scene rules: outfits, props, location and color</label><textarea id="b-notes">${esc(c.notes)}</textarea><p>Choose up to three cast/location reference overrides. Notes and references are used in keyframe generation and invalidate earlier selections when changed.</p>${S.assets.map(a=>{const v=c.variants.find(v=>v.asset_id===a.id);const paths=[a.image,...a.sheets.map(t=>t.path)].filter(Boolean);if(v&&v.image&&!paths.includes(v.image))paths.push(v.image);return `<div class="wf"><label class="check"><input type="checkbox" id="bv-${a.id}" ${v?'checked':''}>${esc(a.name)}</label><label class="f">Reference variant</label><select id="bi-${a.id}">${paths.map((p,i)=>`<option value="${esc(p)}" ${p===v?.image?'selected':''}>${i===0?'Current reference':'Sheet '+i}</option>`).join('')}</select><label class="f">Outfit / expression / prop notes</label><input id="bn-${a.id}" value="${esc(v?.notes||'')}"></div>`}).join('')}</div><div class="dlg-f"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn pri" id="b-save">Save continuity</button></div>`);
 $('#b-save').onclick=async()=>{const r=await editorPost('continuity',{scene:PROD_SCENE,lighting:$('#b-light').value,notes:$('#b-notes').value,variants:S.assets.filter(a=>$('#bv-'+a.id).checked).map(a=>({asset_id:a.id,image:$('#bi-'+a.id).value,notes:$('#bn-'+a.id).value}))});if(r.ok!==false)closeModal()};
}
function compareSceneTakes(){const shots=sceneShots();modal(`<div class="dlg-h"><h2>Scene take comparison</h2></div><div class="dlg-b">${shots.map(s=>`<h3>Shot ${pad(s.seq)}</h3><div class="g2">${s.takes.map(t=>`<div><p>${esc(t.stage)} · take ${t.id} · seed ${t.seed}</p>${t.stage==='keyframe'?`<img style="width:100%" src="${t.url}" alt="Take ${t.id}">`:`<video style="width:100%" controls muted src="${t.url}" preload="none"></video>`}<button class="btn sm" onclick="post('/api/approve',{take_id:${t.id}})">Use this take</button></div>`).join('')}</div>`).join('')}</div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button></div>`)}
async function runQuality(){const r=await editorPost('qc');if(r.ok===false)return;$('#delivery-qc').innerHTML=r.issues.length?r.issues.map(x=>`<div class="row"><button class="btn sm" onclick="jump(${x.id})">Shot ${pad(x.seq)}</button><span class="err">${esc(x.problems.join(' · '))}</span></div>`).join(''):'<p>All picture, audio and selection checks passed.</p>'}
async function editSubtitles(regenerate=false){const r=await editorPost('subtitles',{regenerate});if(r.ok===false)return;modal(`<div class="dlg-h"><h2>Episode subtitles</h2></div><div class="dlg-b"><p>Times are episode seconds. Check timings against the final picture before delivery.</p>${r.rows.map((x,i)=>`<div class="wf"><div class="g2"><label>Start<input data-sub="start" data-row="${i}" type="number" step="0.001" value="${x.start}"></label><label>End<input data-sub="end" data-row="${i}" type="number" step="0.001" value="${x.end}"></label></div><textarea data-sub="text" data-row="${i}">${esc(x.text)}</textarea><button class="btn sm" onclick="this.parentNode.remove()">Remove caption</button></div>`).join('')}<button class="btn sm" onclick="addSubtitleRow()">Add caption at playhead</button><button class="btn sm" onclick="editSubtitles(true)">Regenerate from dialogue</button><div id="subtitle-download"></div></div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button><button class="btn pri" id="sub-save">Save and export SRT</button></div>`);$('#sub-save').onclick=async()=>{const rows=[...document.querySelectorAll('[data-sub="start"]')].map(el=>{const i=el.dataset.row;return{start:+el.value,end:+$(`[data-sub="end"][data-row="${i}"]`).value,text:$(`[data-sub="text"][data-row="${i}"]`).value}});const result=await editorPost('subtitles',{rows,save:true,export:true});if(result.ok!==false)$('#subtitle-download').innerHTML=`<a class="btn" download href="${result.url}">Download subtitles</a>`}}
async function exportStems(){
 const id=+$('#audioexport').value;
 if(!id)return toast('Choose a rendered episode in Assemble first',true);
 const result=await editorPost('delivery',{id,music_volume:+$('#mvol').value});
 if(result.ok!==false)toast('Stem export queued. Progress appears in Assemble.');
}

async function drawWaveform(){const item=shotAt(EDIT_TIME);if(!item||!item.shot.audio_url)return toast('This shot has no dialogue track',true);try{const context=new AudioContext(),buffer=await context.decodeAudioData(await(await fetch(item.shot.audio_url)).arrayBuffer()),canvas=$('#edit-waveform'),g=canvas.getContext('2d'),data=buffer.getChannelData(0);g.clearRect(0,0,canvas.width,canvas.height);g.strokeStyle=getComputedStyle(document.body).color;g.beginPath();for(let x=0;x<canvas.width;x++){let peak=0;const a=Math.floor(x*data.length/canvas.width),b=Math.floor((x+1)*data.length/canvas.width);for(let i=a;i<b;i++)peak=Math.max(peak,Math.abs(data[i]));g.moveTo(x,45-peak*43);g.lineTo(x,45+peak*43)}g.stroke();canvas.setAttribute('aria-label',`Dialogue waveform for shot ${item.shot.seq}; source duration ${buffer.duration.toFixed(2)} seconds`);await context.close()}catch(e){toast('Could not decode this audio waveform',true)}}
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopEdit()});

let EXTRA_SUBTITLE_ROW=0;
function addSubtitleRow(){const key='extra'+(++EXTRA_SUBTITLE_ROW),row=document.createElement('div');row.className='wf';row.innerHTML=`<div class="g2"><label>Start<input data-sub="start" data-row="${key}" type="number" step="0.001" value="${EDIT_TIME}"></label><label>End<input data-sub="end" data-row="${key}" type="number" step="0.001" value="${Math.min(EDIT_TIME+2,S.shots.reduce((n,s)=>n+editDuration(s),0))}"></label></div><textarea data-sub="text" data-row="${key}" aria-label="New caption text"></textarea><button class="btn sm" onclick="this.parentNode.remove()">Remove caption</button>`;$('#subtitle-download').before(row)}
