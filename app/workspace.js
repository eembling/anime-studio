/* Crash recovery, local speech timing, visual timeline and reviewed disk maintenance. */
const DraftStore={
 warned:false,
 key(type,id){return ['anime-studio-draft-v1',S?.project_id||'project',S?.settings?.active_episode||'episode',type,id].join(':')},
 read(type,id){try{const raw=localStorage.getItem(this.key(type,id));return raw?JSON.parse(raw):null}catch{return null}},
 save(type,id,value,base){try{localStorage.setItem(this.key(type,id),JSON.stringify({value,base,updated:Date.now()}));this.warned=false;return true}catch{if(!this.warned){toast('This device could not save a recovery draft. Save your edits to the project before closing.',true);this.warned=true}return false}},
 clear(type,id){try{localStorage.removeItem(this.key(type,id))}catch{}},
 banner(type,id,restore,discard){const d=this.read(type,id);return d?`<div class="draft-recovery" role="status"><span>A local draft from ${esc(new Date(d.updated).toLocaleString())} is available. Restore it to review before saving.</span><button class="btn sm" onclick="${restore}">Restore draft</button><button class="btn sm ghost" onclick="${discard}">Discard saved draft</button></div>`:''}
};
function persistSettingsDraft(){const base=Object.fromEntries(Object.keys(SETTINGS_DRAFT).map(k=>[k,S.settings[k]]));if(Object.keys(SETTINGS_DRAFT).length)DraftStore.save('settings','project',SETTINGS_DRAFT,base);else DraftStore.clear('settings','project')}
function settingsDraftBanner(){return !Object.keys(SETTINGS_DRAFT).length?DraftStore.banner('settings','project','restoreSettingsDraft()','discardSavedSettingsDraft()'):''}
function restoreSettingsDraft(){const d=DraftStore.read('settings','project');if(!d)return;SETTINGS_DRAFT={...d.value};delete SIG.settings;renderSettings();settingsStatus(Object.entries(d.base||{}).some(([k,v])=>String(S.settings[k])!==String(v))?'Project settings changed since this draft. Review restored values before saving.':'Recovered draft. Review values, then Save changes.');}
function discardSavedSettingsDraft(){if(confirm('Discard this saved settings draft?')){DraftStore.clear('settings','project');delete SIG.settings;renderSettings()}}
function persistDialogueDraft(){const d=SHOT_VOICE_EDIT;if(!d)return;if(shotVoiceSignature(d.rows)!==d.baseline)DraftStore.save('dialogue',d.id,{rows:d.rows.map(r=>({line_id:r.line_id,character_id:r.character_id,speaker:r.speaker,text:r.text,direction:r.direction})),active:d.active},d.token)}
function dialogueDraftBanner(sid){return DraftStore.banner('dialogue',sid,'restoreDialogueDraft()','discardSavedDialogueDraft()')}
function restoreDialogueDraft(){const d=SHOT_VOICE_EDIT,saved=DraftStore.read('dialogue',d.id);if(!saved)return;const current=S.shots.find(s=>s.id===d.id)?.lines||[],ids=new Set(current.map(l=>l.line_id)),characters=new Set((S.assets||[]).filter(a=>a.kind==='character').map(a=>a.id));d.rows=saved.value.rows.map(r=>({...r,line_id:ids.has(r.line_id)?r.line_id:null,character_id:characters.has(r.character_id)?r.character_id:null}));d.active=Math.min(saved.value.active||0,Math.max(0,d.rows.length-1));renderShotVoiceRows();shotVoiceMessage(saved.base!==d.token?'Saved dialogue changed since this draft. Review every restored line before saving.':'Recovered local draft. Save dialogue to apply it.');}
function discardSavedDialogueDraft(){if(confirm('Discard this saved dialogue draft?')){DraftStore.clear('dialogue',SHOT_VOICE_EDIT.id);$('#dialogue-draft-recovery').innerHTML=''}}
function persistContinuationDraft(){const e=CONTINUATION_EDITOR;if(e)DraftStore.save('section',e.sid+'-'+e.segment,{prompt:$('#section-prompt').value,camera:$('#section-camera').value},e.source_token)}
function continuationDraftBanner(sid,segment){return DraftStore.banner('section',sid+'-'+segment,'restoreContinuationDraft()','discardSavedContinuationDraft()')}
function restoreContinuationDraft(){const e=CONTINUATION_EDITOR,d=DraftStore.read('section',e.sid+'-'+e.segment);if(!d)return;$('#section-prompt').value=d.value.prompt;$('#section-camera').value=d.value.camera;e.dirty=true;$('#section-status').textContent=d.base!==e.source_token?'The boundary or timing changed. Review this recovered direction against the current frame before saving.':'Recovered draft. Save direction to apply it.';}
function discardSavedContinuationDraft(){const e=CONTINUATION_EDITOR;if(confirm('Discard this saved section draft?')){DraftStore.clear('section',e.sid+'-'+e.segment);$('#section-draft-recovery').innerHTML=''}}
function showBuildInfo(){const b=S.build||{};modal(`<div class="dlg-h"><h2>Anime Studio ${esc(S.version)}</h2></div><div class="dlg-b"><dl class="build-info"><dt>Version</dt><dd>${esc(S.version)}</dd><dt>Source commit</dt><dd>${esc(b.commit||'Development')}${b.dirty?' · local changes':''}</dd><dt>Branch</dt><dd>${esc(b.branch||'Local source')}</dd><dt>Built</dt><dd>${b.built?esc(new Date(b.built).toLocaleString()):'Running from source'}</dd></dl><p>Rebuilding the Mac app does not update the ComfyUI server.</p></div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button></div>`)}
function openRenderRepair(action,sid){if(action==='shots'){if(S.shots.some(s=>s.id===sid))jump(sid);else go('shots')}else{go('settings');settingsSection(action==='servers'?'servers':action==='generation'?'generation':'workflows')}}
async function repeatRender(id){if(!confirm('Generate another candidate using this render’s captured inputs and seed? This queues new GPU work.'))return;const r=await post('/api/jobs',{action:'repeat',ids:[id]});if(r.ok!==false)toast('Queued a new candidate from captured inputs')}
function alignmentToolsHTML(){const a=S.alignment||{},setup=a.setup||{};return `<div class="settings-card"><h3>Local speech alignment</h3><p>Find word timings from the selected dialogue recording. Recognition runs on this Mac; review the suggestions before saving them.</p><p class="muted">Setup installs a local Python speech engine and downloads the multilingual base model (about 150 MB). Your audio is not uploaded. ${esc(setup.message||'')}</p><button class="btn" ${setup.status==='running'?'disabled':''} onclick="setupAlignment(this)">${setup.status==='running'?'Setting up…':a.ready?'Repair alignment setup':'Set up local alignment'}</button></div>`}
async function setupAlignment(button){if(!confirm('Install the local speech engine and download its base model to this Mac? Dialogue audio stays local.'))return;button.disabled=true;const r=await post('/api/alignment',{action:'setup'});if(r.ok!==false)toast('Local alignment setup started. Its status appears here.');else button.disabled=false}
async function startSpeechAlignment(button){if(!PERFORMANCE||PERFORMANCE.dirty)return creativeMessage('Save current performance edits before aligning.',true);button.disabled=true;try{const r=await post('/api/alignment',{action:'start',shot_id:PERFORMANCE.sid,line_id:PERFORMANCE.line_id,token:PERFORMANCE.token});if(r.ok!==false)creativeMessage('Local speech alignment started. The current timings are unchanged.')}finally{button.disabled=false}}
function refreshSpeechAlignment(){if(!PERFORMANCE||!$('#speech-alignment'))return;const job=(S.alignment?.jobs||[]).find(j=>j.shot_id===PERFORMANCE.sid&&j.line_id===PERFORMANCE.line_id);const root=$('#speech-alignment');if(!job){root.innerHTML='';return}if(job.status==='running'){root.textContent='Aligning spoken words locally…';return}if(job.status==='error'){root.textContent=job.error;return}const r=job.result,valid=job.token===PERFORMANCE.token;root.innerHTML=`<p>${valid?'Speech timing ready for review.':'Performance changed since this alignment; run it again.'} Matched ${(r.coverage*100).toFixed(0)}% of script characters.</p>${r.usable&&valid?'<button class="btn sm" onclick="applySpeechAlignment()">Use speech timing suggestions</button>':'<p class="warn-t">Some recognized words do not match the script confidently. Review the transcript and use manual/imported timing.</p>'}<details><summary>Recognized words and confidence</summary><p>${esc(r.transcript||'No speech recognized.')}</p>${(r.words||[]).map(w=>`<span class="alignment-word ${w.matched?'':'err'}">${esc(w.text)} · ${w.start==null?'unmatched':w.start.toFixed(2)+'–'+w.end.toFixed(2)+'s'} · ${Math.round(w.confidence*100)}%</span>`).join('')}</details>`}
function applySpeechAlignment(){const job=(S.alignment?.jobs||[]).find(j=>j.shot_id===PERFORMANCE.sid&&j.line_id===PERFORMANCE.line_id);if(!job||job.token!==PERFORMANCE.token||!job.result.usable)return;if(PERFORMANCE.dirty&&!confirm('Replace the unsaved word timings with speech-alignment suggestions? Other performance fields stay as entered.'))return;importPerformanceWords(job.result.words);PERFORMANCE.source='speech';$('#performance-source').textContent='Speech-based timing suggestions. Listen and save after review.';}

let TIMELINE_ZOOM=30,TIMELINE_WAVES=new Map();
function timelinePositions(){let at=0;return S.shots.map(s=>{const row={shot:s,start:at};at+=s.duration;return row})}
function renderTimelineTracks(){const total=S.shots.reduce((n,s)=>n+s.duration,0),width=Math.max(700,total*TIMELINE_ZOOM),shots=timelinePositions();let ticks='';for(let t=0;t<=total;t+=TIMELINE_ZOOM<25?5:1)ticks+=`<span style="left:${t*TIMELINE_ZOOM}px">${audioTime(t)}</span>`;
 $('#edit-track').innerHTML=`<div class="timeline-canvas" style="width:${width+100}px"><div id="timeline-cursor" class="timeline-playhead" style="left:${100+EDIT_TIME*TIMELINE_ZOOM}px"></div><div class="timeline-row timeline-ruler"><b>Time</b><div style="width:${width}px" onclick="seekTimelineEvent(event,this)">${ticks}</div></div><div class="timeline-row"><b>Picture</b><div class="timeline-lane" style="width:${width}px">${shots.map(({shot:s,start})=>{const k=take(s.keyframe_take);const held=typeof studioHeldFrom==='function'?studioHeldFrom(s):null;return `<article class="timeline-clip" draggable="true" data-shot="${s.id}" style="left:${start*TIMELINE_ZOOM}px;width:${s.duration*TIMELINE_ZOOM}px" onclick="seekTimelineEvent(event,this,${start})"><button class="timeline-trim start" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();seekEdit(${start});editTimelineShot()}" data-trim="start" data-trim-shot="${s.id}" aria-label="Drag start of shot ${pad(s.seq)}; use Trim current shot for keyboard editing"></button><div class="timeline-hold" id="timeline-hold-${s.id}" ${held===null?'hidden':''} style="left:${held===null?0:100*held/s.duration}%" title="${held===null?'':'Final frame holds from '+held.toFixed(2)+' seconds'}"></div>${k?`<img src="${esc(k.url)}" alt="">`:''}<span><b>Shot ${pad(s.seq)}</b><small>${esc(s.scene)} · ${s.duration.toFixed(2)}s</small></span><button class="timeline-trim end" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();seekEdit(${start});editTimelineShot()}" data-trim="end" data-trim-shot="${s.id}" aria-label="Drag end of shot ${pad(s.seq)}; use Trim current shot for keyboard editing"></button></article>`}).join('')}</div></div><div class="timeline-row"><b>Dialogue</b><div class="timeline-lane" style="width:${width}px">${shots.map(({shot:s,start})=>`<button class="timeline-audio ${s.audio_url?'':'missing'}" style="left:${start*TIMELINE_ZOOM}px;width:${s.duration*TIMELINE_ZOOM}px" onclick="seekTimelineEvent(event,this,${start})" aria-label="Scrub dialogue in shot ${pad(s.seq)}"><span id="timeline-wave-${s.id}">${timelineWaveHTML(s)}</span><small>${s.audio_url?'Dialogue':'No dialogue'}</small></button>`).join('')}</div></div>${['music','sfx'].map(kind=>`<div class="timeline-row"><b>${kind==='music'?'Music':'Effects'}</b><div class="timeline-lane" style="width:${width}px;height:${Math.max(65,(S.audio_cues||[]).filter(c=>c.kind===kind).length*32+10)}px">${(S.audio_cues||[]).filter(c=>c.kind===kind).map((c,index)=>`<button class="timeline-cue ${c.enabled?'':'disabled'}" data-cue="${c.id}" style="top:${5+index*32}px;left:${c.start*TIMELINE_ZOOM}px;width:${Math.max(20,c.duration*TIMELINE_ZOOM)}px" title="Drag to move; double-click to edit" onclick="if(event.detail===0)editAudioCue(${c.id})" ondblclick="editAudioCue(${c.id})">${esc(c.name)}</button>`).join('')}</div></div>`).join('')}</div>`;
 document.querySelectorAll('[data-trim-shot]').forEach(handle=>handle.onpointerdown=e=>beginTimelineTrim(e,handle));document.querySelectorAll('[data-cue]').forEach(cue=>cue.onpointerdown=e=>beginCueMove(e,cue));
}
function updateStudioHoldMarker(shot){
 const marker=$('#timeline-hold-'+shot.id),hold=studioHeldFrom(shot);
 if(!marker)return;
 marker.hidden=hold===null;
 if(hold!==null){marker.style.left=100*hold/shot.duration+'%';marker.title='Final frame holds from '+hold.toFixed(2)+' seconds'}
}
function seekTimelineEvent(event,node,start=0){if(event.target.closest('[data-trim]'))return;stopEdit();seekEdit(start+(event.detail===0?0:Math.max(0,event.clientX-node.getBoundingClientRect().left))/TIMELINE_ZOOM)}
function timelineWaveHTML(shot){
 const waveform=TIMELINE_WAVES.get(shot.audio_url);
 if(!waveform?.seconds||!waveform.peaks.length)return '';
 const density=waveform.peaks.length/waveform.seconds;
 const start=(shot.trim_in||0)*density,width=shot.duration*density;
 const first=Math.floor(start),last=Math.min(waveform.peaks.length,Math.ceil(start+width));
 const paths=waveform.peaks.slice(first,last).map((peak,index)=>{
  const x=first+index+.5-start;
  return `<path d="M${x} ${25-peak*23}V${25+peak*23}"/>`;
 });
 return `<svg viewBox="0 0 ${width} 50" preserveAspectRatio="none" aria-hidden="true">${paths.join('')}</svg>`;
}
async function loadTimelineWaves(){for(const s of S.shots.filter(s=>s.audio_url)){if(!TIMELINE_WAVES.has(s.audio_url)){const r=await post('/api/workspace',{action:'waveform',shot_id:s.id});if(r.ok===false)continue;TIMELINE_WAVES.set(s.audio_url,r)}const box=$('#timeline-wave-'+s.id);if(box)box.innerHTML=timelineWaveHTML(s)}toast('Dialogue waveforms loaded. Click a track to scrub.')}
function timelineTrimValues(shot,edge,delta){if(edge==='start'){const d=Math.max(-(shot.trim_in||0),Math.min(shot.duration-.5,delta));return{trim_in:(shot.trim_in||0)+d,duration:shot.duration-d}}return{trim_in:shot.trim_in||0,duration:Math.max(.5,Math.min(3600,shot.duration+delta))}}
function beginTimelineTrim(event,handle){event.preventDefault();event.stopPropagation();stopEdit();const s=S.shots.find(s=>s.id===+handle.dataset.trimShot),origin=event.clientX,clip=handle.parentElement;let values={trim_in:s.trim_in||0,duration:s.duration};handle.setPointerCapture(event.pointerId);handle.onpointermove=e=>{values=timelineTrimValues(s,handle.dataset.trim,(e.clientX-origin)/TIMELINE_ZOOM);clip.style.width=values.duration*TIMELINE_ZOOM+'px';clip.querySelector('small').textContent=values.duration.toFixed(2)+'s · release to save'};handle.onpointerup=async()=>{handle.onpointermove=handle.onpointerup=null;const r=await editorPost('timeline',{action:'trim',id:s.id,...values});delete SIG['editor-timeline'];renderStudio();if(r.ok!==false)seekEdit(EDIT_TIME)};handle.onpointercancel=()=>{handle.onpointermove=handle.onpointerup=null;delete SIG['editor-timeline'];renderStudio()}}
async function moveTimelineCue(id,start){const cue=S.audio_cues.find(c=>c.id===id);if(!cue)return;const values={...cue,start:Math.max(0,start)};if(cue.anchor_shot){const anchor=timelinePositions().find(x=>x.shot.id===cue.anchor_shot);values.anchor_offset=Math.max(0,values.start-(anchor?.start||0))}return post('/api/audio/cue/save',values)}
function beginCueMove(event,node){
 if(event.button!==0||event.detail>1)return;
 stopEdit();
 const cue=S.audio_cues.find(item=>item.id===+node.dataset.cue),origin=event.clientX;
 if(!cue)return;
 let start=cue.start,moved=false;
 node.setPointerCapture(event.pointerId);
 const reset=()=>{node.onpointermove=node.onpointerup=node.onpointercancel=null};
 const refresh=()=>{delete SIG['editor-timeline'];renderStudio()};
 node.onpointermove=move=>{
  const pixels=move.clientX-origin;
  if(!moved&&Math.abs(pixels)<4)return;
  moved=true;start=Math.max(0,cue.start+pixels/TIMELINE_ZOOM);
  node.style.left=start*TIMELINE_ZOOM+'px';
 };
 node.onpointerup=async()=>{
  reset();
  if(!moved)return;
  await moveTimelineCue(cue.id,start);refresh();
 };
 node.onpointercancel=()=>{reset();if(moved)refresh()};
}
async function previewCut(){stopEdit();const item=shotAt(EDIT_TIME);if(!item)return toast('Choose a shot first',true);const r=await post('/api/workspace',{action:'cut',shot_id:item.shot.id});if(r.ok===false)return;modal(`<div class="dlg-h"><h2>Cut · Shot ${pad(r.left_seq)} → ${pad(r.right_seq)}</h2></div><div class="dlg-b"><div class="cut-preview">${[['Last outgoing source frame',r.left],['First incoming source frame',r.right]].map(([label,url])=>`<figure>${url?`<img src="${esc(url)}" alt="${label}">`:'<p>Missing selected picture</p>'}<figcaption>${label}</figcaption></figure>`).join('')}</div><p>Source frames reflect trim positions and continuation boundaries. Review the assembled result for camera effects and final retiming.</p></div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button></div>`)}

let STORAGE_REVIEW=null;
const storageBytes=n=>n>=1024**3?(n/1024**3).toFixed(2)+' GB':n>=1024**2?(n/1024**2).toFixed(1)+' MB':(n/1024).toFixed(1)+' KB';
async function openStorageManager(){const r=await post('/api/storage',{action:'scan'});if(r.ok===false)return;STORAGE_REVIEW=r;const total=r.groups.reduce((n,g)=>n+g.bytes,0);modal(`<div class="dlg-h"><h2>Project storage · ${storageBytes(total)}</h2></div><div class="dlg-b"><p>${esc(r.protected_note)}</p><div class="storage-groups">${r.groups.map(g=>`<div><b>${g.name}</b><span>${storageBytes(g.bytes)} · ${g.files} files</span><progress value="${g.bytes}" max="${Math.max(1,total)}" aria-label="${g.name} disk usage"></progress></div>`).join('')}</div><h3>Review cleanup candidates</h3><p>Cache can be rebuilt. Unused files have no references in the current project, retained queue records, saved episodes or recovery history.</p><div class="storage-list">${r.candidates.map((f,i)=>`<label class="check"><input type="checkbox" data-storage-path="${i}" ${f.category==='cache'?'checked':''}><span>${esc(f.path)}<small>${f.category==='cache'?'Rebuildable cache':'Unused media'} · ${storageBytes(f.bytes)}</small></span></label>`).join('')||'<p>No unused files found.</p>'}</div><p id="storage-status" role="status"></p><h3>Cleanup trash</h3><p>Moving files here is reversible and does not free disk space yet. Permanent deletion frees space and cannot be undone.</p>${r.batches.map(b=>`<div class="storage-batch"><span>${esc(new Date(b.created*1000).toLocaleString())} · ${b.files} files · ${storageBytes(b.bytes)}</span><button class="btn sm" onclick="restoreStorageBatch('${b.id}',this)">Restore</button><button class="btn sm danger" onclick="purgeStorageBatch('${b.id}')">Review permanent deletion…</button></div>`).join('')||'<p class="muted">No cleanup batches.</p>'}</div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button><button class="btn pri" id="storage-clean" ${r.candidates.length?'':'disabled'} onclick="applyStorageCleanup(this)">Move reviewed files to trash</button></div>`,{className:'storage-dlg'})}
async function applyStorageCleanup(button){const paths=[...document.querySelectorAll('[data-storage-path]:checked')].map(e=>STORAGE_REVIEW.candidates[+e.dataset.storagePath].path);if(!paths.length)return $('#storage-status').textContent='Select files to move to cleanup trash.';button.disabled=true;try{const r=await post('/api/storage',{action:'cleanup',token:STORAGE_REVIEW.token,paths});if(r.ok!==false){closeModal();await openStorageManager();toast('Moved '+r.files+' files to recoverable cleanup trash.')}}finally{button.disabled=false}}
async function restoreStorageBatch(id,button){button.disabled=true;try{const r=await post('/api/storage',{action:'restore',id});if(r.ok!==false){closeModal();await openStorageManager();toast('Cleanup batch restored')}}finally{button.disabled=false}}
async function purgeStorageBatch(id){const r=await post('/api/storage',{action:'review_purge',id});if(r.ok===false)return;if(!confirm('Permanently delete '+r.files+' reviewed trash files ('+storageBytes(r.bytes)+')? This cannot be undone. Referenced project media, history and exports stay protected.'))return;const result=await post('/api/storage',{action:'purge',id,token:r.token,confirm:true});if(result.ok!==false){closeModal();await openStorageManager();toast('Freed '+storageBytes(result.freed_bytes))}}

let INSPECTED_SHOT=null, SHOT_BROWSER_EPISODE=null;
function shotInScope(shot){
 const scene=$('#shot-scene')?.value, query=($('#shot-search')?.value||'').trim().toLowerCase();
 return (!scene||scene==='#all'||shot.scene===JSON.parse(scene))&&
   (!query||[pad(shot.seq),String(shot.seq),shot.scene,shot.dialogue,shot.keyframe_prompt].some(value=>String(value||'').toLowerCase().includes(query)));
}
function changeShotScope(){
 $('#shotlist video')?.pause();
 sel.clear();$('#selall').checked=false;renderShots();
}
function selectInspectorShot(id,reveal=false){
 $('#shotlist video')?.pause();
 if(reveal){
  $('#shot-search').value='';$('#shot-scene').value='#all';
  sel.clear();$('#selall').checked=false;
 }
 INSPECTED_SHOT=id;
 renderShots();
 $('#shot-open-'+id)?.focus({preventScroll:true});
 if(reveal)$('#shot-open-'+id)?.scrollIntoView({block:'nearest'});
}
function renderShotBrowser(shots){
 const list=$('#shot-browser-list'),scene=$('#shot-scene');
 if(!list||!scene)return shots;
 const episode=S.settings.active_episode||'current';
 if(SHOT_BROWSER_EPISODE!==episode){
  INSPECTED_SHOT=null;SHOT_BROWSER_EPISODE=episode;
  $('#shot-search').value='';scene.value='#all';
  shots=S.shots.filter(matches);
 }
 const scenes=[...new Set(S.shots.map(shot=>shot.scene||''))];
 if(!same('shot-browser-scenes',scenes)){
  const previous=scene.value;
  scene.innerHTML='<option value="#all">All scenes</option>'+scenes.map(name=>`<option value="${esc(JSON.stringify(name))}">${esc(name||'Untitled scene')}</option>`).join('');
  if(scenes.some(name=>JSON.stringify(name)===previous))scene.value=previous;
  shots=S.shots.filter(matches);
 }
 if(!shots.some(shot=>shot.id===INSPECTED_SHOT))INSPECTED_SHOT=shots[0]?.id??null;
 const visible=shots.map(shot=>[shot.id,shot.seq,shot.scene,needs(shot),missingLines(shot),sel.has(shot.id)]);
 if(!same('shot-browser-list',[visible,INSPECTED_SHOT])){
  const focused=document.activeElement?.id;
  list.innerHTML=shots.map(shot=>`<div class="shot-browser-row">
   <input id="shot-select-${shot.id}" type="checkbox" aria-label="Include shot ${pad(shot.seq)} in bulk actions" ${sel.has(shot.id)?'checked':''} onchange="tog(${shot.id},this.checked)">
   <button class="btn shot-open" id="shot-open-${shot.id}" aria-current="${shot.id===INSPECTED_SHOT?'true':'false'}" onclick="selectInspectorShot(${shot.id})"><b>${pad(shot.seq)} · ${esc(shot.scene||'Untitled scene')}</b><small>${({kf:'Needs keyframe',clip:'Needs animation',done:'Picture ready'})[needs(shot)]||'Review picture'}${missingLines(shot)?' · Needs dialogue':''}</small></button>
  </div>`).join('')||'<p class="muted">No matching shots</p>';
  if(focused?.startsWith('shot-open-')||focused?.startsWith('shot-select-'))document.getElementById(focused)?.focus({preventScroll:true});
 }
 return shots.filter(shot=>shot.id===INSPECTED_SHOT);
}
function shotNextAction(shot,preview,selected){
 let action,label;
 if(preview&&!selected){action=`approve(${preview.id})`;label='Use previewed take'}
 else if(!shot.keyframe_take){action=`gen('keyframe',[${shot.id}])`;label='Generate keyframe'}
 else if(missingLines(shot)){action=`editShotVoices(${shot.id})`;label='Prepare dialogue'}
 else if(shot.kind==='motion'&&!shot.clip_take){action=`gen('animate',[${shot.id}])`;label='Animate shot'}
 else if(shot.kind==='motion'&&shot.remaining>.25){action=`openContinuation(${shot.id})`;label='Continue animation'}
 else{action=`openStudioShot(${shot.id})`;label='Review in Studio'}
 return `<div class="shot-next"><button class="btn pri" onclick="${action}">${label}</button></div>`;
}

function queueResults(job){
 const shot=S.shots.find(item=>item.id===job.shot_id);
 const asset=S.assets.find(item=>item.id===job.asset_id);
 if(job.stage==='voice'||job.stage==='voice_design'){
  if(job.stage==='voice_design')return (asset?.clips||[]).filter(clip=>clip.path.includes('_designed_'+job.id+'_')).map(clip=>({...clip,kind:'audio',selected:!!clip.is_ref,asset:asset.id}));
  return (shot?.lines||[]).flatMap((line,index)=>(line.takes||[]).filter(take=>take.job_id===job.id).map(take=>({...take,kind:'audio',selected:take.path===line.audio,line:index,shot:shot.id})));
 }
 const takes=job.stage==='sheet'?asset?.sheets:shot?.takes;
 return (takes||[]).filter(take=>take.job_id===job.id).map(take=>({
  ...take,kind:['keyframe','sheet'].includes(take.stage)?'image':'video',
  selected:shot?[shot.keyframe_take,shot.clip_take,shot.final_take,...(shot.cont||[])].includes(take.id):take.path===asset.image,
  shot:shot?.id,asset:asset?.id,
 }));
}
function queueResultActions(job){
 return queueResults(job).length?`<button class="btn sm pri" onclick="reviewQueueResult(${job.id})">Review result</button>`:'';
}
function renderQueueGroups(jobs,card){
 const groups=new Map();
 for(const job of jobs){
  const shot=S.shots.find(item=>item.id===job.shot_id),asset=S.assets.find(item=>item.id===job.asset_id);
  const key=shot?'shot'+shot.id:asset?'cast'+asset.id:'other';
  if(!groups.has(key))groups.set(key,{shot,title:shot?`${shot.scene||'Untitled scene'} · Shot ${pad(shot.seq)}`:asset?.name||'Other renders',jobs:[]});
  groups.get(key).jobs.push(job);
 }
 return [...groups.values()].map(group=>`<section><h3 class="queue-group">${esc(group.title)} ${group.shot?`<button class="btn sm ghost" onclick="jump(${group.shot.id})">Open shot</button>`:''}</h3>${group.jobs.map(card).join('')}</section>`).join('');
}
function queueReviewTake(shotId,takeId,line){
 closeModal();
 if(line!==undefined){editShotVoices(shotId,line);return}
 VIEW[shotId]=takeId;jump(shotId);
}
function reviewQueueResult(id){
 const job=S.jobs.find(item=>item.id===id);
 const results=job?queueResults(job):[];
 if(!results.length){toast('This result no longer has a saved take.',true);return}
 if(results.length===1&&results[0].shot&&results[0].kind!=='audio'){
  const take=results[0];VIEW[take.shot]=take.id;jump(take.shot);return;
 }
 modal(`<div class="dlg-h"><h2>Render results</h2></div><div class="dlg-b queue-results">${results.map((take,index)=>`<section><h3>Take ${index+1} · ${take.selected?'Selected':'Candidate'}</h3>${take.kind==='image'?`<img src="${esc(take.url)}" alt="Generated take ${index+1}">`:take.kind==='audio'?`<audio src="${esc(take.url)}" controls preload="none"></audio>`:`<video src="${esc(take.url)}" controls playsinline preload="metadata"></video>`}${take.shot?`<button class="btn sm" onclick="queueReviewTake(${take.shot},${take.id}${take.line===undefined?'':','+take.line})">${take.kind==='audio'?'Open dialogue':'Open take in Shots'}</button>`:take.asset?`<button class="btn sm" onclick="closeModal();editAsset(${take.asset})">Open character</button>`:''}</section>`).join('')}</div><div class="dlg-f"><button class="btn" onclick="closeModal()">Close</button></div>`);
}
