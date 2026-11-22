/* Speech-bubble tagging for a shot (panel): who says what, balloon style and position, before generation. */
const BUBBLE_KINDS=[['speech','Speech'],['shout','Shout'],['whisper','Whisper'],['thought','Thought'],['narration','Narration box'],['sfx','Sound effect']];
const BUBBLE_POSITIONS=[['auto','Auto (reading order)'],['top-right','Top right'],['top','Top'],['top-left','Top left'],['right','Right'],['left','Left'],['bottom-right','Bottom right'],['bottom','Bottom'],['bottom-left','Bottom left']];
const BUBBLE_SPEAKERLESS=['narration','sfx'];
let BUBBLE_DRAFT=null;

function bubbleCharacters(){return S.assets.filter(a=>a.kind==='character')}
function bubbleTagged(){return new Set([...document.querySelectorAll('.picks input:checked')].map(x=>+x.value))}
function bubbleOptions(list,value){return list.map(([v,l])=>`<option value="${esc(v)}" ${String(v)===String(value)?'selected':''}>${esc(l)}</option>`).join('')}

function bubbleEditorHTML(shot){
 BUBBLE_DRAFT=(shot&&shot.bubbles||[]).map(b=>({...b}));
 return `<section class="bubbles" aria-labelledby="bubbles-h"><div class="bubbles-h"><h3 id="bubbles-h">Speech bubbles</h3>
 <span class="muted">Tag who says what before generating. Speakers are added to the shot’s cast unless marked off-panel.</span></div>
 <div id="bubble-list"></div><div class="toolbar" style="margin:6px 0 0">
 <button class="btn sm" type="button" onclick="addBubble()">Add bubble</button>
 <button class="btn sm" type="button" onclick="bubblesFromDialogue()">Copy from dialogue</button>
 <span class="muted" style="font-size:12px">Lettering: ${esc(({off:'off (acting only)',space:'leave space',render:'drawn by the model'})[S.settings.lettering_mode]||'off')} · change in Settings → Project</span></div></section>`}

function renderBubbles(){const box=$('#bubble-list');if(!box||!BUBBLE_DRAFT)return;const chars=bubbleCharacters(),tagged=bubbleTagged();
 box.innerHTML=BUBBLE_DRAFT.length?BUBBLE_DRAFT.map((b,i)=>{const speakerless=BUBBLE_SPEAKERLESS.includes(b.kind);
  const who=speakerless?'':`<select aria-label="Bubble ${i+1} speaker" onchange="bubbleSet(${i},'character_id',this.value?+this.value:null)"><option value="">Who says it?</option>${chars.map(a=>`<option value="${a.id}" ${a.id===b.character_id?'selected':''}>${esc(a.name)}${tagged.has(a.id)?'':' (not in shot)'}</option>`).join('')}</select>`;
  return `<div class="bubble-row ${esc(b.kind)}"><span class="bubble-no">${i+1}</span>
  <select aria-label="Bubble ${i+1} style" onchange="bubbleSet(${i},'kind',this.value);renderBubbles()">${bubbleOptions(BUBBLE_KINDS,b.kind)}</select>${who}
  <select aria-label="Bubble ${i+1} position" onchange="bubbleSet(${i},'position',this.value)">${bubbleOptions(BUBBLE_POSITIONS,b.position||'auto')}</select>
  <textarea aria-label="Bubble ${i+1} words" placeholder="${b.kind==='sfx'?'BAM':b.kind==='narration'?'That evening…':'What they say'}" oninput="bubbleSet(${i},'text',this.value)">${esc(b.text||'')}</textarea>
  ${speakerless?'':`<input aria-label="Bubble ${i+1} expression" placeholder="Expression (angry, teasing…)" value="${esc(b.emotion||'')}" oninput="bubbleSet(${i},'emotion',this.value)">
  <label class="check" title="The speaker is heard but not drawn in this panel"><input type="checkbox" ${b.off_panel?'checked':''} onchange="bubbleSet(${i},'off_panel',this.checked)"> Off-panel</label>`}
  <span class="bubble-acts"><button class="btn sm ghost" type="button" ${i?'':'disabled'} onclick="moveBubble(${i},-1)" aria-label="Move bubble ${i+1} earlier">↑</button><button class="btn sm danger" type="button" onclick="BUBBLE_DRAFT.splice(${i},1);renderBubbles()" aria-label="Remove bubble ${i+1}">Remove</button></span></div>`}).join('')
  :'<p class="muted" style="font-size:13px">No bubbles tagged. Without them, dialogue lines are used as speech for acting.</p>'}

function bubbleSet(i,key,value){if(BUBBLE_DRAFT&&BUBBLE_DRAFT[i])BUBBLE_DRAFT[i][key]=value}
function addBubble(){if(BUBBLE_DRAFT.length>=12)return toast('Use at most 12 bubbles in one panel.',1);const tagged=bubbleTagged(),first=bubbleCharacters().find(a=>tagged.has(a.id));BUBBLE_DRAFT.push({kind:'speech',character_id:first?first.id:null,text:'',position:'auto',emotion:'',off_panel:false});renderBubbles()}
function moveBubble(i,d){const j=i+d;if(j<0||j>=BUBBLE_DRAFT.length)return;[BUBBLE_DRAFT[i],BUBBLE_DRAFT[j]]=[BUBBLE_DRAFT[j],BUBBLE_DRAFT[i]];renderBubbles()}
function bubblesFromDialogue(){const text=$('#f_dia').value,byName=Object.fromEntries(bubbleCharacters().map(a=>[a.name.trim().toLowerCase(),a]));const tagged=bubbleTagged();
 const lone=bubbleCharacters().filter(a=>tagged.has(a.id));const rows=text.split('\n').map(l=>l.trim()).filter(Boolean).map(line=>{const m=line.match(/^([^:(]{1,50}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$/);
  let speaker=m?m[1].replace(/\*\*|__/g,'').trim():'',words=m?m[3]:line,emotion=m&&m[2]?m[2]:'';const dir=words.match(/^\(([^)]{1,60})\)\s*(.+)$/);if(dir){emotion=dir[1];words=dir[2]}
  const a=speaker?byName[speaker.toLowerCase()]:lone.length===1?lone[0]:null;return {kind:'speech',character_id:a?a.id:null,text:words,position:'auto',emotion,off_panel:false}});
 if(!rows.length)return toast('Write dialogue first, e.g. AIKO: line',1);
 if(BUBBLE_DRAFT.length&&!confirm('Replace the tagged bubbles with the dialogue lines?'))return;BUBBLE_DRAFT=rows.slice(0,12);renderBubbles();
 const missing=rows.filter(r=>!r.character_id).length;if(missing)toast(`Choose the speaker for ${missing} bubble${missing>1?'s':''}`,1)}
function bubblePayload(){return BUBBLE_DRAFT?BUBBLE_DRAFT.map(b=>({...b})):undefined}

function bubbleChips(s){if(!(s.bubbles||[]).length)return '';const name=id=>(S.assets.find(a=>a.id===id)||{}).name||'?';
 return `<div class="bubble-chips" aria-label="Speech bubbles">${s.bubbles.map(b=>`<span class="bubble-chip ${esc(b.kind)}" title="${esc(b.text)}">${BUBBLE_SPEAKERLESS.includes(b.kind)?(b.kind==='sfx'?'SFX':'Caption'):esc(name(b.character_id))}${b.off_panel?' (off)':''}: ${esc(b.text.slice(0,40))}${b.text.length>40?'…':''}</span>`).join('')}</div>`}
