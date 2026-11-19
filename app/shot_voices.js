/* The shot's saved words and explicit character are the source of voice generation. */
let SHOT_VOICE_EDIT=null;
function voiceCharacters(){return S.assets.filter(a=>a.kind==='character')}
function chosenVoice(characterId){const character=voiceCharacters().find(a=>a.id===+characterId),clips=character?.clips||[],reference=clips.find(c=>c.is_ref)||clips[0];return{character,reference,ready:!!reference&&reference.available!==false}}
function editShotVoices(sid){
 const shot=S.shots.find(s=>s.id===sid);if(!shot)return;
 const suggested=!(shot.lines||[]).length&&(shot.voice_suggestion||[]).length;
 const rows=suggested?shot.voice_suggestion.map(line=>({...line,line_id:null,character_id:voiceCharacters().find(a=>a.name.trim().toLowerCase()===line.speaker.trim().toLowerCase())?.id||null,takes:[],pending:[]})):(shot.lines||[]).map(l=>({...l}));
 SHOT_VOICE_EDIT={id:sid,token:shot.voice_token,rows,file:shot.audio_mode==='file'};
 modal(`<div class="dlg-h"><h2>Dialogue & character voices · Shot ${pad(shot.seq)}</h2></div><div class="dlg-b"><p>Choose who says each line, then generate that character saying these exact words. The reference recording defines the voice; it is not matched to the words.</p>${suggested?'<p class="warnbox">Dialogue was found in this shot’s prompt. Review the words and character choices below, then save or generate to add it as dialogue. Your picture prompt stays intact.</p>':''}${SHOT_VOICE_EDIT.file?'<label class="check"><input id="sv-replace" type="checkbox"> Replace this shot’s whole-audio override with dialogue voices</label>':''}<div id="sv-rows"></div><button class="btn" id="sv-add" onclick="addShotVoiceRow()">Add dialogue line</button><label class="f" for="sv-takes">Takes per line</label><select id="sv-takes"><option value="1">1 take</option><option value="2">2 takes</option><option value="3">3 takes</option><option value="4">4 takes</option></select><p id="sv-result" role="status"></p><p class="muted">Changing words, character or delivery clears the selected audio for that line. Existing takes stay available. The first new take is applied automatically unless you choose another while it renders. Save/generate uses everything entered here.</p></div><div class="dlg-f"><button class="btn" onclick="closeModal();SHOT_VOICE_EDIT=null">Close</button><button class="btn" id="sv-save" onclick="submitShotVoices([])">Save dialogue</button><button class="btn pri" id="sv-generate" onclick="submitShotVoices('all')">Save & generate character voices</button></div>`);
 renderShotVoiceRows();
}
function captureShotVoiceRows(){if(!SHOT_VOICE_EDIT)return;for(let i=0;i<SHOT_VOICE_EDIT.rows.length;i++){const row=SHOT_VOICE_EDIT.rows[i];if(!$('#sv-text-'+i))continue;row.text=$('#sv-text-'+i).value;row.character_id=+$('#sv-character-'+i).value||null;row.direction=$('#sv-direction-'+i).value}}
function renderShotVoiceRows(){
 $('#sv-rows').innerHTML=SHOT_VOICE_EDIT.rows.map((line,i)=>{const{character,reference,ready}=chosenVoice(line.character_id);return `<div class="wf" data-voice-row="${i}"><label class="f" for="sv-character-${i}">Character for line ${i+1}</label><select id="sv-character-${i}" onchange="shotVoiceCharacterChanged(${i})"><option value="">Choose a character…</option>${voiceCharacters().map(a=>`<option value="${a.id}" ${a.id===line.character_id?'selected':''}>${esc(a.name)}</option>`).join('')}</select><p class="${ready?'muted':'err'}" id="sv-reference-${i}">${!character?'Choose the character whose voice should speak this line.':ready?'Reference voice: '+esc(reference.name||character.name):esc(character.name)+' needs a reference voice. Add or design one in Cast.'}</p><label class="f" for="sv-text-${i}">Spoken words</label><textarea id="sv-text-${i}" placeholder="Words to speak; no speaker label needed">${esc(line.text||'')}</textarea><label class="f" for="sv-direction-${i}">Delivery (optional)</label><input id="sv-direction-${i}" value="${esc(line.direction||'')}" placeholder="For example: quietly, reassuring"><div class="toolbar"><button class="btn sm" onclick="submitShotVoices([${i}])" ${ready?'':'disabled'}>Save & generate this line</button><button class="btn sm" onclick="removeShotVoiceRow(${i})">Remove line</button>${line.url?`<audio controls preload="none" src="${line.url}" aria-label="Current audio for line ${i+1}"></audio>`:''}</div><div class="toolbar" id="sv-results-${i}">${shotVoiceTakeResults(line,i)}</div></div>`}).join('')||'<p>Add dialogue, choose its character, then generate the voice here.</p>';
}
function shotVoiceCharacterChanged(index){captureShotVoiceRows();const row=SHOT_VOICE_EDIT.rows[index];row.audio=null;row.url=null;row.src=null;row.takes=[];row.pending=[];renderShotVoiceRows()}
function addShotVoiceRow(){captureShotVoiceRows();SHOT_VOICE_EDIT.rows.push({text:'',speaker:'',character_id:null,direction:'',line_id:null,takes:[],pending:[]});renderShotVoiceRows()}
function removeShotVoiceRow(index){captureShotVoiceRows();SHOT_VOICE_EDIT.rows.splice(index,1);renderShotVoiceRows()}
async function submitShotVoices(requested){
 if(!SHOT_VOICE_EDIT)return;captureShotVoiceRows();
 const draft=SHOT_VOICE_EDIT;
 if(draft.rows.some(row=>!row.text.trim()))return toast('Enter spoken words or remove the empty line.',true);
 const generate=requested==='all'?draft.rows.map((_,i)=>i):requested;
 if(generate.some(i=>!chosenVoice(draft.rows[i].character_id).ready))return toast('Choose a character with a reference voice for every line you want to generate.',true);
 const body={shot_id:draft.id,token:draft.token,lines:draft.rows.map(row=>({line_id:row.line_id,character_id:row.character_id,text:row.text,direction:row.direction})),generate,takes:+$('#sv-takes').value,replace_file:!!$('#sv-replace')?.checked};
 const buttons=[...document.querySelectorAll('#dlg button')];buttons.forEach(b=>b.disabled=true);
 try{const result=await post('/api/shot/voices',body);if(result.ok===false&&!result.dialogue_saved)return;
  const shot=S.shots.find(s=>s.id===draft.id);if(shot){draft.token=shot.voice_token;draft.rows=shot.lines.map(l=>({...l}));draft.file=shot.audio_mode==='file';renderShotVoiceRows()}
  const message=result.ok===false?'Dialogue saved. '+(result.error||'Voice generation could not start.'):generate.length?`Saved dialogue and queued ${result.queued||0} character voice take(s). ${(result.skipped||[]).join(' ')}`:'Dialogue and character choices saved.';
  $('#sv-result').textContent=message;toast(message,result.ok===false||generate.length&&!result.queued);
 }finally{buttons.forEach(b=>b.disabled=false)}
}
async function useShotVoiceTake(index,takeId){
 captureShotVoiceRows();const draft=SHOT_VOICE_EDIT,shot=S.shots.find(s=>s.id===draft.id),saved=shot?.lines[index],row=draft.rows[index];
 if(!saved||row.text!==saved.text||row.character_id!==saved.character_id||row.direction!==(saved.direction||''))return toast('Save the dialogue and character choices before selecting a take.',true);
 const result=await post('/api/line/set',{shot_id:draft.id,index,src:'take',take_id:takeId,line_id:row.line_id});
 if(result.ok!==false){draft.rows=S.shots.find(s=>s.id===draft.id).lines.map(l=>({...l}));renderShotVoiceRows();$('#sv-result').textContent='Selected take is now used by this shot’s dialogue track.'}
}

function shotVoiceTakeResults(line,index){return (line.takes||[]).map((take,n)=>`<span><audio controls preload="none" src="${take.url}" aria-label="Preview line ${index+1} take ${n+1}" style="width:180px;height:30px"></audio><button class="btn sm" onclick="useShotVoiceTake(${index},${take.id})">${take.path===line.audio?'Selected':'Use take '+(n+1)}${take.provenance==='outdated'?' · older inputs':''}</button></span>`).join('')+(line.pending?.length?`<span class="muted">${line.pending.length} voice take(s) ${line.pending.includes('running')?'generating':'queued'}. Results appear here automatically.</span>`:'')}
function refreshShotVoiceResults(){
 const draft=SHOT_VOICE_EDIT;if(!draft||!$('#sv-rows'))return;captureShotVoiceRows();
 const shot=S.shots.find(s=>s.id===draft.id);if(!shot)return;
 draft.rows.forEach((row,index)=>{const saved=shot.lines.find(l=>l.line_id===row.line_id);if(!saved||saved.text!==row.text||saved.character_id!==row.character_id||saved.direction!==row.direction)return;for(const key of ['takes','pending','audio','url','src'])row[key]=saved[key];const box=$('#sv-results-'+index);if(box){const html=shotVoiceTakeResults(row,index);if(box.innerHTML!==html)box.innerHTML=html}})
}
