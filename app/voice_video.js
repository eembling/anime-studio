function voiceVideo(sid,mode='keyframe'){
 const shot=S.shots.find(s=>s.id===sid);if(!shot)return;
 const reference=shot.takes.find(t=>t.id===shot.keyframe_take),existing=mode==='existing';let busy=false,dirty=false;
 const savedCrop=shot.voice_crop?JSON.parse(shot.voice_crop):{x:50,y:100/9,size:55};
 const cropFields=[['x','Horizontal position',0,100],['y','Vertical position',0,100],['size','Crop size',25,90]];
 const assigned=(S.creative?.assignments||[]).some(a=>a.shot_id===sid&&a.stage===(existing||shot.kind==='still'?'lipsync':'animate'));
 const sampling=!existing&&!assigned&&S.workflows?.lipsync?.name==='wan22_s2v_14b_fp8';
 modal(`<div class="dlg-h"><h2>${existing?'Lip-sync existing clip':'Animate with voice'}</h2></div><div class="dlg-b">
 <p>${existing?'Uses the selected clip and dialogue. Requires an imported, tested workflow with connected VIDEO_IN and AUDIO_IN. The built-in Wan S2V starter cannot edit an existing clip.':'Generates a new performance from the selected keyframe and character dialogue. Picture and voice are saved together at the model’s timing.'}</p>
 ${!existing?`<p class="muted">Small faces in wide shots limit readable mouth movement. Choose a close-up keyframe for best control, or review the crop below.</p><label class="f" for="voice-framing">Dialogue framing</label><select id="voice-framing"><option value="source">Keep reference framing</option><option value="closeup" ${shot.voice_framing==='closeup'?'selected':''}>Dialogue close-up</option></select>${reference?`<div style="position:relative;max-width:480px;margin:12px 0"><img src="${esc(reference.url)}" alt="Selected dialogue reference" style="display:block;width:100%"><div id="voice-crop" aria-label="Close-up crop boundary" style="position:absolute;left:22.5%;top:5%;width:55%;height:55%;border:2px solid currentColor;display:none;pointer-events:none;box-sizing:border-box"></div></div><div id="voice-crop-controls" class="g3">
 ${cropFields.map(([key,label,min,max])=>`<label for="voice-crop-${key}">${label}<input id="voice-crop-${key}" type="range" min="${min}" max="${max}" step="0.1" value="${savedCrop[key]}"></label>`).join('')}
 </div>`:''}`:''}
 <label class="f" for="voice-performance">Speaking direction</label><textarea id="voice-performance">${esc(shot.voice_direction??'The character speaks naturally with clear mouth articulation, restrained head movement, and consistent facial features.')}</textarea>
 <label class="f" for="voice-quality">Sampling preset</label><select id="voice-quality" ${sampling?'':'disabled'}>${sampling?`<option value="standard">Standard · 20 steps</option><option value="fine" ${shot.voice_quality==='fine'?'selected':''}>Experimental · 30 steps</option><option value="compare">Compare 20 and 30 steps · same seed</option>`:'<option value="standard">Use workflow sampling settings</option>'}</select>
 <p class="muted">Sampling presets apply to the built-in Wan S2V workflow. Versioned recipes and imported workflows retain their own sampler settings. Higher steps take longer and do not guarantee better articulation. FP8 and 16 fps are retained.</p>
 <label class="f" for="voice-seed">Seed for candidates</label><input id="voice-seed" type="number" min="1" max="281474976710656" step="1" value="${Math.floor(Math.random()*1000000000)+1}">
 <p id="voice-status" role="status" aria-live="polite">${existing?'Select a compatible workflow in Settings before generating.':'Review framing and performance before generating.'}</p></div>
 <div class="dlg-f"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn pri" id="voice-generate">Generate candidate</button></div>`,{beforeClose:()=>!busy&&(!dirty||confirm('Discard unsaved voice animation settings?'))});
 const framing=$('#voice-framing');
 const cropValues=()=>Object.fromEntries(cropFields.map(([key])=>[key,+$('#voice-crop-'+key).value]));
 const syncCrop=()=>{
  if(!reference||existing)return;
  const visible=framing.value==='closeup', overlay=$('#voice-crop');
  $('#voice-crop-controls').hidden=!visible;
  overlay.style.display=visible?'block':'none';
  const {x,y,size}=cropValues();
  Object.assign(overlay.style,{left:(x*(1-size/100))+'%',top:(y*(1-size/100))+'%',width:size+'%',height:size+'%'});
 };
 syncCrop();
 const fields=['voice-framing','voice-performance','voice-quality','voice-seed'];
 if(reference&&!existing)fields.push(...cropFields.map(([key])=>'voice-crop-'+key));
 for(const id of fields)if($('#'+id))$('#'+id).oninput=()=>{dirty=true;syncCrop()};
 $('#voice-generate').onclick=async()=>{
  const seed=+$('#voice-seed').value;if(!Number.isSafeInteger(seed)||seed<1||seed>281474976710656){$('#voice-status').textContent='Enter a whole-number seed between 1 and 281474976710656.';return}
  const quality=sampling?$('#voice-quality').value:'standard',qualities=quality==='compare'?['standard','fine']:[quality];busy=true;$('#voice-generate').disabled=true;
  try{
   const saved=await post('/api/shot/save',{id:sid,lip_sync_mode:mode,voice_framing:framing?.value||shot.voice_framing||'source',...(reference&&!existing&&framing.value==='closeup'?{voice_crop:cropValues()}:{}),voice_direction:$('#voice-performance').value,voice_quality:quality==='compare'?'standard':quality});
   if(saved.ok===false){$('#voice-status').textContent=saved.error||'Could not save voice settings.';return}dirty=false;
   let queued=0;for(const preset of qualities){const result=await post('/api/generate',{stage:existing||shot.kind==='still'?'lipsync':'animate',shot_ids:[sid],takes:1,seed,voice_quality:preset,require_voice_animation:true});queued+=result.queued||0;if(result.ok===false||!result.queued){$('#voice-status').textContent=(queued?`${queued} candidate already queued. `:'')+(result.error||result.skipped?.join(' ')||'No candidate queued.');return}}
   busy=false;closeModal();toast(`Queued ${queued} voice animation candidate${queued===1?'':'s'}${quality==='compare'?' using the same seed; compare the resulting takes.':'.'}`);
  }catch(error){$('#voice-status').textContent=error.message||'Could not queue voice animation.'}finally{busy=false;if($('#voice-generate'))$('#voice-generate').disabled=false}
 };
}
