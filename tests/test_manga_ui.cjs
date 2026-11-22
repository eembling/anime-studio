const{test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const{readFileSync}=require('node:fs');
const code=readFileSync(require('node:path').join(__dirname,'../app/manga.js'),'utf8');
function setup(dialogue){const elements={'#bubble-list':{innerHTML:''},'#f_dia':{value:dialogue||''}};const ctx={S:{settings:{lettering_mode:'space'},assets:[{id:1,name:'AIKO',kind:'character'},{id:2,name:'KENJI',kind:'character'},{id:3,name:'Rooftop',kind:'location'}]},
 $:k=>elements[k],esc:v=>String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'),toast:m=>elements.toast=m,confirm:()=>true,
 document:{querySelectorAll:()=>[{value:'1'},{value:'3'}]}};vm.createContext(ctx);vm.runInContext(code,ctx);return{ctx,elements}}

test('editor lists characters only and flags speakers not tagged in the shot',()=>{const{ctx,elements}=setup();
 const html=ctx.bubbleEditorHTML({bubbles:[{kind:'speech',character_id:2,text:'Hi',position:'auto'}]});assert.match(html,/leave space/);ctx.renderBubbles();
 const list=elements['#bubble-list'].innerHTML;assert.match(list,/KENJI \(not in shot\)/);assert.doesNotMatch(list,/Rooftop/);assert.match(list,/value="2" selected/)});

test('copy from dialogue maps speakers, delivery directions and unknown speakers',()=>{const{ctx,elements}=setup('AIKO (angry): You came after all.\nKENJI: I promised.\nBOSS: Who?');
 ctx.bubbleEditorHTML(null);ctx.bubblesFromDialogue();const b=ctx.bubblePayload();assert.equal(b.length,3);
 assert.equal(b[0].character_id,1);assert.equal(b[0].emotion,'angry');assert.equal(b[0].text,'You came after all.');assert.equal(b[1].character_id,2);assert.equal(b[2].character_id,null);
 assert.match(elements.toast,/Choose the speaker for 1 bubble/)});

test('narration and sound effects have no speaker controls and bubbles can be reordered',()=>{const{ctx,elements}=setup();ctx.bubbleEditorHTML({bubbles:[{kind:'speech',character_id:1,text:'A'},{kind:'sfx',text:'BAM'}]});
 ctx.renderBubbles();assert.equal((elements['#bubble-list'].innerHTML.match(/Who says it\?/g)||[]).length,1);ctx.moveBubble(1,-1);assert.equal(ctx.bubblePayload()[0].kind,'sfx');
 assert.match(ctx.bubbleChips({bubbles:ctx.bubblePayload()}),/SFX: BAM/)});
