"use strict";
const $ = id => document.getElementById(id);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const sections = {
  Plan:[["Script","脚本","Break a chapter script into pages and panels."],["Cast","キャラ表","Keep character models, locations and props consistent."],["Reference library","資料","Collect visual references and choose the active image for each cast entry."]],
  Produce:[["Pages","原稿","Edit panel direction, lettering and page breaks."],["Production","制作","Generate panel artwork and choose a take for each panel."],["Render queue","進行","Track panel illustrations and character sheets."]],
  Finish:[["Studio","編集","Proof the panel order, lettering and composition."],["Assemble","製本","Export finished pages and collect them into a chapter."]],
  Workspace:[["Settings","設定","Configure ComfyUI and the defaults for new chapter plans."]]
};
const views = Object.values(sections).flat();
let settingsTab="Project", settingsDraft={}, health={};
let state, tab="Script", pageIndex=0, timer, serial=0;
const chapter = () => state.chapters.find(c=>c.id===state.active);
const page = () => chapter()?.pages[pageIndex];
const img = (name,alt) => '<img src="/art/'+esc(name)+'" alt="'+esc(alt)+'">';
const button = (name,action,klass="",id="") => '<button type="button" class="btn '+klass+'" data-action="'+action+'" data-id="'+esc(id)+'">'+esc(name)+'</button>';
const option = (val,label,current) => '<option value="'+esc(val)+'" '+(val===current?'selected':'')+'>'+esc(label)+'</option>';
const field = (label,name,value="",type="text") => '<label>'+label+'<input type="'+type+'" name="'+name+'" value="'+esc(value)+'"></label>';
const area = (label,name,value="",cls="") => '<label>'+label+'<textarea name="'+name+'" class="'+cls+'">'+esc(value)+'</textarea></label>';
const select = (label,name,items,value) => '<label>'+label+'<select name="'+name+'">'+items.map(x=>option(x[0],x[1],value)).join("")+'</select></label>';
const LAYOUT_CHOICES=[["feature","Feature panel"],["grid","Balanced grid"],["strips","Horizontal strips"],["three","Rows of three"],["tall","Tall side panel"],["splash","Splash page with insets"]];
const PAGE_PRESETS=[["web","Web / screen (1200 × 1800 px)"],["b5","B5 tankōbon (182 × 257 mm)"],["a5","A5 (148 × 210 mm)"],["us","US comic (6.625 × 10.25 in)"]];
const BALLOON_KINDS=[["speech","Speech"],["shout","Shout"],["whisper","Whisper"],["thought","Thought"],["caption","Caption"],["sfx","Sound effect"]];
function balloonWho(b){return b.kind==="caption"?"Caption":b.kind==="sfx"?"SFX":(state.assets.find(a=>a.id===b.speaker)?.name||"?")+(b.off_panel?" (off-panel)":"")}
function balloonRow(b={kind:"speech",speaker:"",text:"",off_panel:false}){
  const chars=state.assets.filter(a=>a.kind==="character");
  return '<fieldset class="balloon-row"><legend>Balloon</legend><input type="hidden" name="b-id" value="'+esc(b.id||"")+'">'+
    select("Style","b-kind",BALLOON_KINDS,b.kind)+
    '<label>Speaker<select name="b-speaker"><option value="">None (caption or sound effect)</option>'+chars.map(a=>option(a.id,a.name,b.speaker)).join("")+'</select></label>'+
    select("Shown","b-off",[["in","Speaker drawn in this panel"],["off","Speaker off-panel"]],b.off_panel?"off":"in")+
    '<label class="wide">Words<textarea name="b-text" rows="2">'+esc(b.text)+'</textarea></label>'+
    '<div class="row">'+button("Move earlier","balloon-up")+button("Remove","balloon-remove","danger")+'</div></fieldset>';
}
function balloonEditor(item){
  return '<fieldset class="balloons"><legend>Speech balloons, in reading order</legend><p class="muted">Tag who says each line. A speaker who is drawn in the panel is added to its cast.</p><div id="balloon-rows">'+
    (item.balloons||[]).map(balloonRow).join("")+'</div>'+button("Add balloon","balloon-add")+'</fieldset>';
}
function balloonValues(f){
  const ids=f.getAll("b-id"),kinds=f.getAll("b-kind"),speakers=f.getAll("b-speaker"),offs=f.getAll("b-off"),texts=f.getAll("b-text");
  return kinds.map((kind,i)=>({...(ids[i]?{id:ids[i]}:{}),kind,speaker:speakers[i],off_panel:offs[i]==="off",text:texts[i]}));
}
function notify(text){clearTimeout(timer);$("notice").textContent=text;timer=setTimeout(()=>$("notice").textContent="",6500)}
async function request(url,options){
  const r=await fetch(url,options);
  if(!r.ok){
    if(r.headers.get("content-type")?.includes("application/json"))throw Error((await r.json()).error);
    const doc=new DOMParser().parseFromString(await r.text(),"text/html");
    throw Error(doc.querySelector("p")?.textContent.replace(/^Message: |\.$/g,"")||r.statusText);
  }
  return r;
}
async function post(op,data){
  ++serial;
  state=await (await request("/api/"+op,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)})).json();
}
function empty(text,action="go",name="Open Script",id="Script"){
  return '<div class="card empty"><h2>'+esc(text)+'</h2>'+button(name,action,"pri",id)+'</div>';
}
function pagebar(){
  return '<div class="row pagebar"><label>Chapter<select id="chapter-select">'+state.chapters.map(c=>option(c.id,c.title+" · plan "+(state.chapters.indexOf(c)+1),state.active)).join("")+'</select></label><label>Page<select id="page-select">'+chapter().pages.map((p,i)=>option(i,"Page "+(i+1)+" · "+p.scene,pageIndex)).join("")+'</select></label></div>';
}
function script(){
  return '<div class="split"><form class="card" id="script-form"><h2>Chapter manuscript</h2>'+field("Chapter title","title",state.title)+area("Script","script",state.script,"script")+
    '<label>Import a script<input type="file" id="script-file" accept=".md,.txt,text/plain,text/markdown"></label>'+select("Maximum panels per page","limit",[1,2,3,4,5,6,7,8,9].map(n=>[n,String(n)]),state.limit)+
    '<div class="row">'+button("Plan pages","plan","pri")+button("Save draft","draft")+button("Planning prompt","planning-brief")+'</div></form><div class="stack"><div class="card"><h2>Page breakdown</h2><p>Start a scene with INT., EXT. or a Markdown heading. Each new scene begins a page.</p><p>Write action on its own line. A speaker’s next line becomes that panel’s lettering. Further dialogue gets a new panel.</p><p>Use PAGE for a page turn and PANEL 1 to group a detailed brief. Keyframe, Camera, Direction, Lighting and Cast fields feed the illustration prompts. Use CAPTION: or SFX: for other lettering.</p><div class="page-rule"><b>Script example</b><p class="caption">EXT. STATION — NIGHT\nRain pools on the empty platform.\nMika: The last train is late.\nPAGE\nA headlight splits the dark.\nSFX: RUMBLE</p></div></div>'+
    (state.chapters.length?'<div class="card"><h2>Saved plans</h2>'+state.chapters.map((c,i)=>'<p>'+button(c.title+" · Plan "+(i+1),"open-chapter","",c.id)+' '+button("View source","source","",c.id)+'</p>').join("")+'</div>':'')+'</div></div>';
}
function references(a){
  const groups = [...new Set(a.refs.map(r=>r.collection||"Model references"))];
  return groups.map(group=>'<div class="tag">'+esc(group)+'</div><div class="thumbs">'+a.refs.filter(r=>(r.collection||"Model references")===group).map(r=>'<button data-action="use-ref" data-id="'+a.id+'" data-ref="'+r.id+'" class="'+(r.id===a.selected?'selected':'')+'">'+img(r.id,r.label)+'<span>'+esc(r.label)+(r.id===a.selected?' ✓':'')+'</span></button>').join("")+'</div>').join("");
}
function cast(){
  const library=tab==="Reference library";
  return '<div class="toolbar">'+button("New cast entry","edit-asset","pri")+button("Import from Anime Studio","import-cast")+'</div>'+(state.assets.length?'<div class="models">'+state.assets.map(a=>'<article><div class="art">'+(a.selected?img(a.selected,a.name):"No reference image yet")+'</div><span class="tag">'+esc(a.kind)+'</span><h2>'+esc(a.name)+'</h2><p>'+esc(a.description)+'</p>'+references(a)+'<div class="row">'+button("Add reference","add-ref","",a.id)+(library?"":button("Edit entry","edit-asset","",a.id))+(a.kind==="character"&&a.selected?button("Make turnaround sheet","sheet","",a.id):"")+'</div></article>').join("")+'</div>':'<div class="card"><h2>No cast entries yet</h2><p>Plan a script to create its speaking cast, or add an entry with a name, description and reference image.</p></div>');
}
function draftProof(){
  return '<img class="proof" alt="Page '+(pageIndex+1)+' composition and lettering" src="/proof/'+chapter().id+'/'+page().id+'?v='+serial+'">';
}
function pages(){
  const p=page(), production=tab==="Production";
  return pagebar()+'<div class="split"><div class="stack"><div class="card activepage"><span class="tag">Page '+(pageIndex+1)+'</span><h2>'+esc(p.scene)+'</h2><div class="row">'+(production?button("Generate missing panel art","render","pri"):button("Page layout","layout"))+button("Page direction","page-direction")+button("Preview prompts","preview-prompts")+'</div></div>'+
    p.panels.map((item,i)=>'<article><div class="row between"><h3>Panel '+(i+1)+'</h3><span class="tag">'+(item.selected?"Art selected":item.takes.length?"Choose a take":"Planned")+'</span></div><p>'+esc(item.prompt)+'</p>'+
      ((item.balloons||[]).length?'<ul class="balloon-list" aria-label="Balloons">'+item.balloons.map(b=>'<li class="balloon-chip '+esc(b.kind)+'"><b>'+esc(balloonWho(b))+'</b> '+esc(b.text)+'</li>').join("")+'</ul>':'')+
      (item.cast.length?'<p>Cast · '+item.cast.map(k=>esc(state.assets.find(a=>a.id===k)?.name)).join(", ")+'</p>':'')+
      (production?'<div class="thumbs">'+item.takes.map((t,n)=>'<button class="'+(t===item.selected?'selected':'')+'" data-action="take" data-id="'+item.id+'" data-take="'+t+'">'+img(t,"Art take "+(n+1))+'<span>Take '+(n+1)+(t===item.selected?' ✓':'')+'</span></button>').join("")+'</div>':'')+
      (i>1&&item.camera&&item.camera===p.panels[i-1].camera&&item.camera===p.panels[i-2].camera?'<p class="warn">Same framing as the two panels before it. Vary the shot to keep the page moving.</p>':'')+
      (item.camera?'<p class="muted">Camera · '+esc(item.camera)+(item.camera_suggested?' (suggested)':'')+'</p>':'')+
      '<div class="row">'+button("Edit panel","edit-panel","",item.id)+(production?button("New take","render","pri",item.id)+(item.takes.length?button("Delete takes…","delete-takes","danger",item.id):""):(i?button("Start new page here","split","",item.id):""))+'</div></article>').join("")+
    '</div><div class="card"><h2>Page proof</h2>'+draftProof()+'</div></div>';
}
let queueData=null;
const queueSections=[
  ["active","Active renders","Active","No active renders.","run"],
  ["queued","Pending renders","Pending","Nothing pending.",""],
  ["completed","Completed renders","Completed","No completed renders yet.",""],
  ["attention","Failed and canceled","Failed / canceled","","err"]
];
function queueTime(seconds){
  const n=Math.max(0,Math.round(seconds));
  return n<60?n+"s":Math.floor(n/60)+"m "+n%60+"s";
}
function queueMeter(j){
  if(j.status==="error"||j.status==="cancelled")return "";
  let text, value, max=100, done=false;
  if(j.status==="done"){text="Complete · 100%";value=100;done=true}
  else if(j.status==="pending"){text="Pending · awaiting dispatch";value=0}
  else {
    const steps=/^Sampler (\d+)\/(\d+)$/.exec(j.progress);
    if(steps&&Number(steps[2])>0){
      max=Number(steps[2]);value=Math.min(max,Number(steps[1]));
      text="Current sampler · "+value+"/"+max+" steps";
    }else{
      value=j.estimated_progress;
      text=value==null?"Active · collecting progress":"Estimated "+value+"%";
    }
  }
  return '<progress class="queue-progress'+(done?' done':'')+'" max="'+max+'" '+(value==null?'':'value="'+value+'"')+' aria-label="'+esc(j.label+" · "+text)+'"></progress><span class="'+(done?'ok-t':'muted')+'">'+esc(text)+'</span>';
}
function queueCard(j){
  const states={running:["Active","running"],pending:["Pending","pending"],done:["Completed","done"],error:["Failed","error"],cancelled:["Canceled","cancelled"]};
  const [label,style]=states[j.status];
  let timing="";
  if(j.status==="running"){
    if(j.elapsed_seconds!=null)timing=queueTime(j.elapsed_seconds)+" elapsed";
    if(j.remaining_seconds!=null)timing+=" · about "+queueTime(j.remaining_seconds)+" remaining";
    if(j.overdue)timing+=" · taking longer than typical";
  }else if(j.status==="pending"){
    timing=j.estimated_finish_seconds!=null?"Estimated finish in "+queueTime(j.estimated_finish_seconds):j.wait_reason;
  }else if(j.elapsed_seconds!=null)timing="Render time "+queueTime(j.elapsed_seconds);
  const server=j.server_name||j.instance_id||j.estimated_server_id;
  return '<article class="queue-job"><div class="queue-job-head"><b>'+esc(j.label)+'</b><span class="muted">'+(j.stage==="sheet"?"Turnaround sheet":"Panel artwork")+(j.queue_position?" · Queue position "+j.queue_position:"")+'</span><span class="pill p-'+style+'">'+label+'</span></div>'+
    '<div class="queue-job-body"><div class="queue-meter">'+queueMeter(j)+
    (j.progress&&!j.progress.startsWith("Sampler ")?'<div class="muted">'+esc(j.progress)+'</div>':'')+
    (timing?'<div class="muted">'+esc(timing)+'</div>':'')+
    (server?'<div class="muted">'+(j.status==="pending"?"Expected server · ":"Server · ")+esc(server)+'</div>':'')+
    (j.models.length?'<div class="queue-models"><b>Models</b>'+j.models.map(m=>'<span>'+esc(m.kind)+" · "+esc(m.name)+'</span>').join("")+'</div>':'')+
    (j.error?'<p class="error">'+esc(j.error)+'</p>':'')+
    '<details data-inputs="'+esc(j.job_id)+'"><summary>Render inputs</summary><p class="caption">'+esc(j.prompt)+'</p><p>Avoid · '+esc(j.negative||"—")+'</p><p>'+j.width+' × '+j.height+' · '+esc(j.workflow_name)+'</p></details></div>'+
    (j.status==="pending"?'<div class="queue-actions">'+button("Cancel waiting render","cancel","",j.job_id)+'</div>':
     j.status==="running"?'<div class="queue-actions">'+(j.stopping?'<span class="muted">Stopping…</span>':button("Stop render","cancel","danger",j.job_id))+'</div>':
     j.status==="error"&&j.can_retry?'<div class="queue-actions">'+button("Retry download","retry-job","pri",j.job_id)+'</div>':
     j.status==='done'&&j.stage==='panel'&&j.panel?'<div class="queue-actions">'+button('Manage panel takes','queue-takes','',j.job_id)+'</div>':'')+'</div></article>';
}
function queue(){
  if(!queueData)return '<p class="muted" role="status">Loading render queue…</p>';
  const stats=queueSections.map(([key,,label,,cls])=>'<div class="qs '+cls+'"><b class="'+(key==="completed"?'ok-t':'')+'">'+queueData[key].length+'</b><span>'+label+'</span></div>').join("");
  const sections=queueSections.map(([key,title,,empty])=>{
    const jobs=queueData[key];
    if(key==="attention"&&!jobs.length)return "";
    return '<section class="queue-section" aria-labelledby="queue-'+key+'"><h2 id="queue-'+key+'">'+title+'</h2>'+(jobs.length?jobs.map(queueCard).join(""):'<p class="muted">'+empty+'</p>')+'</section>';
  });
  return '<div class="qstats">'+stats+'</div>'+sections.join("");
}
let queueRequest=0;
async function refreshQueue(){
  const version=++queueRequest;
  const next=await (await request("/queue")).json();
  if(tab!=="Render queue"||version!==queueRequest||JSON.stringify(next)===JSON.stringify(queueData))return;
  const desk=$("desk"),open=new Set([...desk.querySelectorAll("details[open]")].map(d=>d.dataset.inputs));
  const focus=document.activeElement;
  const focusId=focus?.closest("[data-inputs]")?.dataset.inputs;
  const cancelId=focus?.dataset.action==="cancel"?focus.dataset.id:null;
  queueData=next;desk.innerHTML=queue();
  for(const detail of desk.querySelectorAll("details")){
    detail.open=open.has(detail.dataset.inputs);
    if(detail.dataset.inputs===focusId)detail.querySelector("summary").focus({preventScroll:true});
  }
  if(cancelId)for(const el of desk.querySelectorAll('[data-action="cancel"]'))if(el.dataset.id===cancelId)el.focus({preventScroll:true});
}
function finish(){
  const c=chapter(),p=page(),ready=x=>x.panels.every(v=>v.selected);
  return pagebar()+'<div class="split"><div class="card">'+(tab==="Studio"?'<div class="proof-wrap" id="proof-wrap">'+draftProof()+'<div class="proof-overlay" id="proof-overlay" aria-label="Drag balloons, tails and row gutters"></div></div><p class="muted">Drag a balloon or its tail tip to move it. Drag a row gutter to resize rows.</p><div id="proof-warnings" role="status"></div><div class="row">'+button("Reset balloon positions","reset-balloons")+button("Reset row heights","reset-rows")+'</div>':draftProof())+'</div><div class="card"><h2>'+esc(c.title)+'</h2><p>Page '+(pageIndex+1)+' of '+c.pages.length+' · '+(c.direction==="rtl"?"Right to left":"Left to right")+'</p><p>'+c.pages.filter(ready).length+' of '+c.pages.length+' pages have artwork selected for every panel.</p><div class="stack">'+
    (tab==="Studio"?button("Edit layout","layout")+button("Edit panels","go","","Pages"):
      (ready(p)?'<a class="btn" download href="/png/'+c.id+'/'+p.id+'">Download page PNG</a>':'')+(c.pages.every(ready)?'<a class="btn pri" download href="/cbz/'+c.id+'">Download chapter CBZ</a><a class="btn" download href="/pdf/'+c.id+'">Download print PDF</a>':button("Choose panel art","go","pri","Production")))+'</div></div></div>';
}
const directions=[["rtl","Right to left"],["ltr","Left to right"]];
function settings(){
  const tabs=["Project","Generation","ComfyUI servers","Workflows"];
  const val=k=>settingsDraft[k]??state[k];
  let content;
  if(settingsTab==="ComfyUI servers"){
    content='<div class="toolbar">'+button("Add server","server-edit","pri")+button("Check connections","server-check")+button("Copy servers from Anime Studio","anime-servers")+'</div>'+state.servers.map(v=>{
      const status=health[v.url];
      return '<article class="settings-card"><div class="settings-object-heading"><h3>'+esc(v.name)+'</h3><b>'+(!v.enabled?'Disabled':status?(status.ok?'Connected':'Connection problem'):'Unchecked')+'</b></div><p>'+esc(v.url)+'</p><div class="settings-tags">'+v.roles.map(r=>'<span>'+(r==="panel"?"Panel artwork":"Turnaround sheets")+'</span>').join("")+'</div><p>'+esc(status?.info||"")+'</p>'+button("Edit server","server-edit","",v.id)+'</article>';
    }).join("");
  }else if(settingsTab==="Workflows"){
    content='<article class="settings-card"><h3>'+esc(state.workflow?.name||"Qwen Image Edit 2509")+'</h3><p>Image workflow for panels and turnaround sheets. Use a ComfyUI API export with a positive prompt and a SaveImage output. Tag reference inputs REF1, REF2, and so on in picture order. Manga Studio groups overflow references into sheets. NEGATIVE and SIZE identify the other optional inputs.</p><div class="row">'+button("Import API workflow","workflow-import","pri")+button("Use Anime Studio image workflow","anime-workflow")+'<a class="btn" href="/workflow.json" download>Export API workflow</a>'+(state.workflow?button("Use built-in starter","workflow-reset"):"")+'</div></article>';
  }else{
    const yesNo=[["true","Yes"],["false","No"]],flag=k=>String(val(k)===true||val(k)==="true");
    const controls=settingsTab==="Project"?area("Visual style","style",val("style"))+area("Avoid in artwork","negative",val("negative"))+select("Reading direction for new plans","direction",directions,val("direction"))+
      select("Page size","page_preset",PAGE_PRESETS,val("page_preset"))+select("Print resolution","dpi",[["300","300 dpi"],["600","600 dpi (line art)"]],String(val("dpi")))+select("Add 3 mm bleed to exports","bleed",yesNo,flag("bleed"))+
      select("Artwork tone","tone",[["color","Colour, as rendered"],["gray","Greyscale"],["screentone","Black and white with screentone dots"]],val("tone"))+
      select("Letter in capitals","lettering_caps",yesNo,flag("lettering_caps"))+select("Vertical Japanese, Chinese and Korean lettering","vertical_cjk",yesNo,flag("vertical_cjk")):
      [["panel_size","Panel longest edge (px)"],["sheet_width","Turnaround width (px)"],["sheet_height","Turnaround height (px)"],["timeout","Render timeout (seconds)"]].map(([k,title])=>field(title,k,val(k),"number")).join("");
    content='<form class="settings-card" id="settings-form">'+controls+'<div class="row">'+button("Save changes","settings","pri")+button("Discard changes","settings-discard")+(settingsTab==="Generation"?button("Use Anime Studio image sizes and timeout","anime-generation"):"")+'</div></form>';
  }
  return '<div class="settings-layout"><nav class="settings-nav" aria-label="Settings sections">'+tabs.map(k=>button(k,"settings-tab",k===settingsTab?"selected":"",k)).join("")+'</nav><section class="settings-content"><header class="settings-heading"><h2>'+esc(settingsTab)+'</h2></header>'+content+'</section></div>';

}
function paint(){
  const c=chapter();
  if(c)pageIndex=Math.min(pageIndex,c.pages.length-1);
  $("nav").innerHTML=Object.entries(sections).map(([group,items])=>'<div class="nav-group">'+group+'</div>'+items.map(([name,jp])=>'<button data-action="go" data-id="'+name+'" '+(name===tab?'class="on" aria-current="page"':'')+'><span class="en">'+name+'</span><span class="jp">'+jp+'</span></button>').join("")).join("");
  $("title").textContent=tab;$("sub").textContent=views.find(v=>v[0]===tab)[2];$("current").textContent=c?.title||"Local project";
  $("stats").innerHTML=[[c?.pages.length||0,"Pages"],[state.assets.length,"Cast entries"]].map(([n,label])=>'<div class="stat"><b>'+n+'</b><span>'+label+'</span></div>').join("");
  const renderers={"Script":script,"Cast":cast,"Reference library":cast,"Render queue":queue,"Settings":settings};
  $("desk").innerHTML=renderers[tab]?renderers[tab]():!c?empty("Plan a chapter to start making pages"):["Pages","Production"].includes(tab)?pages():finish();
  if(tab==="Studio"&&c)proofEditor().catch(e=>notify(e.message));
}
let geometryRequest=0;
async function proofEditor(){
  const c=chapter(),p=page(),ticket=++geometryRequest;
  const geo=await (await request("/api/geometry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chapter:c.id,page:p.id})})).json();
  const layer=$("proof-overlay");
  if(!layer||ticket!==geometryRequest)return;
  const pct=v=>(v*100).toFixed(3)+"%";
  let html="";
  for(const panel of geo.panels)for(const b of panel.balloons){
    html+='<div class="drag-balloon" role="button" tabindex="0" aria-label="Move balloon" data-kind="balloon" data-panel="'+panel.id+'" data-balloon="'+b.id+'" style="left:'+pct(b.cx-b.w/2)+';top:'+pct(b.cy-b.h/2)+';width:'+pct(b.w)+';height:'+pct(b.h)+'"></div>';
    if(!["caption","sfx"].includes(b.kind))html+='<div class="drag-tail" role="button" tabindex="0" aria-label="Move balloon tail" data-kind="tail" data-panel="'+panel.id+'" data-balloon="'+b.id+'" style="left:'+pct(b.tail[0])+';top:'+pct(b.tail[1])+'"></div>';
  }
  geo.gutters.forEach((y,i)=>{html+='<div class="drag-gutter" role="separator" aria-label="Resize rows" data-kind="gutter" data-index="'+i+'" style="top:'+pct(y)+'"></div>'});
  layer.innerHTML=html;
  const notes=$("proof-warnings");
  if(notes)notes.innerHTML=geo.warnings.map(w=>'<p class="warn">'+esc((state.assets.find(a=>a.id===w.speaker)?.name||"A speaker")+" · "+w.text)+'</p>').join("");
  const boxes=Object.fromEntries(geo.panels.map(x=>[x.id,x.box]));
  layer.onpointerdown=e=>{
    const handle=e.target.closest("[data-kind]");if(!handle)return;
    e.preventDefault();handle.setPointerCapture(e.pointerId);
    const rect=layer.getBoundingClientRect(),start=[e.clientX,e.clientY],origin=[handle.offsetLeft,handle.offsetTop];
    handle.onpointermove=m=>{
      if(handle.dataset.kind!=="gutter")handle.style.left=(origin[0]+m.clientX-start[0])+"px";
      handle.style.top=(origin[1]+m.clientY-start[1])+"px";
    };
    handle.onpointerup=async u=>{
      handle.onpointermove=handle.onpointerup=null;
      const fx=(u.clientX-rect.left)/rect.width,fy=(u.clientY-rect.top)/rect.height;
      if(Math.hypot(u.clientX-start[0],u.clientY-start[1])<3)return;
      try{
        const ctx={chapter:c.id,page:p.id};
        if(handle.dataset.kind==="gutter"){
          const rows=[...new Map(geo.panels.map(x=>[x.box[1].toFixed(4),x.box])).values()].sort((a,b)=>a[1]-b[1]);
          const i=Number(handle.dataset.index),heights=rows.map(r=>r[3]);
          const pair=heights[i]+heights[i+1],top=rows[i][1],gap=rows[i+1][1]-(rows[i][1]+rows[i][3]);
          heights[i]=Math.min(Math.max(fy-top-gap/2,pair*.15),pair*.85);heights[i+1]=pair-heights[i];
          const mean=heights.reduce((a,b)=>a+b,0)/heights.length;
          await post("row-weights",{...ctx,weights:heights.map(h=>Math.min(5,Math.max(.2,h/mean)))});
        }else{
          const [bx,by,bw,bh]=boxes[handle.dataset.panel];
          let x=fx,y=fy;
          if(handle.dataset.kind==="balloon"){const r=handle.getBoundingClientRect();x=(r.left+r.width/2-rect.left)/rect.width;y=(r.top+r.height/2-rect.top)/rect.height}
          const px=(x-bx)/bw,py=(y-by)/bh,fields=handle.dataset.kind==="tail"?{tail_x:px,tail_y:py}:{x:px,y:py};
          await post("balloon-move",{...ctx,panel:handle.dataset.panel,balloon:handle.dataset.balloon,...fields});
        }
        paint();
      }catch(err){notify(err.message);paint()}
    };
  };
}
function modal(title,html,save,submitLabel="Save"){
  const d=$("modal");
  d.innerHTML='<form><h2>'+esc(title)+'</h2>'+html+'<p class="error" role="alert"></p><div class="row"><button class="btn pri">'+esc(submitLabel)+'</button>'+button("Close","close")+'</div></form>';
  d.querySelector("form").onsubmit=async e=>{
    e.preventDefault();
    const submit=e.target.querySelector(".pri");submit.disabled=true;
    try{const onSaved=await save(new FormData(e.target));d.close();paint();if(typeof onSaved==="function")onSaved()}catch(err){d.querySelector(".error").textContent=err.message}finally{submit.disabled=false}
  };d.showModal();
}
const values = f=>Object.fromEntries(f);
const refField='<label>Reference image<input type="file" name="upload" accept="image/png,image/jpeg,image/webp"></label>';
async function picture(file){
  if(!file?.size)return "";
  if(file.size>22*1024*1024)throw Error("Choose an image under 22 MB.");
  return new Promise((ok,fail)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(",")[1]);r.onerror=()=>fail(Error("Cannot read image."));r.readAsDataURL(file)});
}
function go(name){tab=name;if(name==="Render queue")queueData=null;paint();if(name==="Render queue")refreshQueue().catch(e=>notify(e.message));document.querySelector("main").scrollTop=0}
async function act(el){
  const action=el.dataset.action,id=el.dataset.id,c=chapter(),p=page(),ctx={chapter:c?.id,page:p?.id};
  if(action==="go"){if(tab==="Script" && id!=="Script")await post("draft",values(new FormData($("script-form"))));go(id);return}
  if(action==="close")return $("modal").close();
  if(action==="balloon-add"){const rows=$("balloon-rows");if(rows.children.length>=12)throw Error("Use at most 12 balloons in one panel.");rows.insertAdjacentHTML("beforeend",balloonRow());rows.lastElementChild.querySelector("textarea").focus();return}
  if(action==="balloon-remove"){el.closest(".balloon-row").remove();return}
  if(action==="balloon-up"){const row=el.closest(".balloon-row");if(row.previousElementSibling)row.parentNode.insertBefore(row,row.previousElementSibling);return}
  if(action==="retry-job"){await post("retry",{id});await refreshQueue();notify("Downloading the finished artwork again.");return}
  if(action==="reset-balloons"){for(const panel of p.panels)for(const b of panel.balloons||[])if(b.x!=null||b.tail_x!=null)await post("balloon-move",{...ctx,panel:panel.id,balloon:b.id,x:null,y:null,tail_x:null,tail_y:null});paint();return}
  if(action==="reset-rows"){await post("row-weights",{...ctx,weights:null});paint();return}
  if(action==="queue-takes"){
    const job=queueData.completed.find(j=>j.job_id===id);
    if(!job)return;
    await post("chapter",{id:job.chapter});
    const index=chapter().pages.findIndex(pg=>pg.panels.some(panel=>panel.id===job.panel));
    if(index<0)throw Error("The rendered panel is no longer in this chapter.");
    pageIndex=index;go("Production");
    const card=document.querySelector('[data-action="edit-panel"][data-id="'+job.panel+'"]')?.closest("article");
    card?.scrollIntoView({block:"center"});
    return;
  }
  if(action==="delete-takes"){
    const panel=p.panels.find(item=>item.id===id);
    const choices=panel.takes.map((name,i)=>'<label class="take-choice"><input type="checkbox" name="takes" value="'+esc(name)+'">'+img(name,"Take "+(i+1))+'<span>Take '+(i+1)+(name===panel.selected?' · Selected artwork':'')+'</span></label>').join("");
    const help='<p>Choose the takes to permanently delete. Deleting the selected artwork leaves this panel without an export image until you choose another take.</p>';
    modal("Delete takes · Page "+(pageIndex+1)+", panel "+(p.panels.indexOf(panel)+1),help+button("Select all except current artwork","mark-old")+'<div class="take-choices">'+choices+'</div>',async f=>{
      const takes=f.getAll("takes");
      await post("delete-takes",{...ctx,panel:id,takes,selected:panel.selected});
      notify(takes.length+" take"+(takes.length===1?"":"s")+" deleted.");
    },"Delete selected takes");
    const d=$("modal"), boxes=[...d.querySelectorAll('input[name="takes"]')],submit=d.querySelector("button.pri");
    const update=()=>{const count=boxes.filter(box=>box.checked).length;submit.disabled=!count;submit.textContent=count?"Delete "+count+" take"+(count===1?"":"s"):"Delete selected takes"};
    d.querySelector("form").onchange=update;
    d.querySelector('[data-action="mark-old"]').onclick=e=>{e.stopPropagation();boxes.forEach(box=>box.checked=box.value!==panel.selected);update()};
    update();return;
  }
  if(action==="import-cast"){
    return modal("Import from Anime Studio",'<p>Choose the ZIP exported from Anime Studio’s Cast screen. Matching entries keep their panel links, filled-in settings and active reference. New references and missing details are added.</p><label>Cast library<input type="file" name="library" accept=".zip,application/zip" required></label>',async f=>{
      const file=f.get("library");
      if(!file?.size || file.size>256*1024*1024)throw Error("Choose a cast library up to 256 MB.");
      ++serial;
      const result=await (await request("/api/cast/import",{method:"POST",headers:{"Content-Type":"application/zip"},body:file})).json();
      state=result.state;
      return ()=>{
        const d=$("modal"), counts=result.counts;
        d.innerHTML='<h2>Cast import complete</h2><p>'+counts.added+' added · '+counts.updated+' updated · '+counts.unchanged+' already present</p>'+
          (result.warnings.length?'<ul>'+result.warnings.map(w=>'<li>'+esc(w)+'</li>').join("")+'</ul>':'')+button("Done","close","pri");
        d.showModal();
      };
    },"Import library");
  }
  if(action==="settings-tab"){settingsTab=id;paint();return}
  if(action==="settings-discard"){settingsDraft={};paint();return}
  if(action==="anime-generation"||action==="anime-workflow"){
    await post(action,{});
    if(action==="anime-generation")for(const k of ["panel_size","sheet_width","sheet_height","timeout"])delete settingsDraft[k];
    paint();notify("Copied Anime Studio image settings.");return;
  }
  if(action==="planning-brief"){
    const f=new FormData($("script-form"));
    const brief=["Adapt this script into a manga chapter with up to "+f.get("limit")+" panels per page. Use explicit PAGE and PANEL headings. Preserve the dialogue and story order.",
      "Start each scene with INT. or EXT. Put Lighting and Continuity under the scene heading. For each panel, write Keyframe: for one drawable moment, Camera: for framing, Direction: for composition and expression, and Cast: for visible characters. Write spoken lines as Name: dialogue. Use CAPTION: and SFX: for printed lettering.",
      "Visual style: "+state.style,"Available cast and locations: "+state.assets.map(a=>a.name+" ("+a.kind+")").join(", "),f.get("title"),f.get("script")].join("\n\n");
    const d=$("modal");d.innerHTML='<h2>Page planning prompt</h2><p>Copy this brief into your writing tool. Paste its page and panel outline into Script, then choose Plan pages.</p><textarea id="planning-text" class="script" readonly>'+esc(brief)+'</textarea><div class="row">'+button("Copy brief","copy-brief","pri")+button("Done","close")+'</div>';d.showModal();return;
  }
  if(action==="copy-brief"){
    const text=$("planning-text");text.select();
    if(!document.execCommand("copy"))await navigator.clipboard.writeText(text.value);
    notify("Planning brief copied.");return;
  }
  if(action==="server-check"){
    const result=await (await request("/api/check-servers",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})).json();
    health=Object.fromEntries(state.servers.filter(v=>result[v.id]).map(v=>[v.url,result[v.id]]));paint();return;
  }
  if(action==="anime-servers"){await post(action,{});paint();return}
  if(action==="server-edit"){
    const v=state.servers.find(x=>x.id===id)||{name:"",url:"http://",roles:["panel","sheet"],enabled:true};
    const check=(k,label,on)=>'<label><input type="checkbox" name="'+k+'" '+(on?'checked':'')+'> '+label+'</label>';
    const controls=field("Server name","name",v.name)+field("ComfyUI address","url",v.url)+'<fieldset><legend>Assigned tasks</legend>'+check("panel","Panel artwork",v.roles.includes("panel"))+check("sheet","Turnaround sheets",v.roles.includes("sheet"))+'</fieldset>'+check("enabled","Enabled",v.enabled)+(id?button("Remove server","server-remove","",id):"");
    return modal("ComfyUI server",controls,async f=>post("server-save",{id,name:f.get("name"),url:f.get("url"),roles:["panel","sheet"].filter(k=>f.has(k)),enabled:f.has("enabled")}));
  }
  if(action==="server-remove"){await post(action,{id});$("modal").close();paint();return}
  if(action==="workflow-import")return modal("Import image workflow",'<label>API workflow JSON<input type="file" name="graph" accept=".json" required></label>',async f=>{const file=f.get("graph");await post("workflow",{name:file.name,graph:JSON.parse(await file.text())})},"Import workflow");
  if(action==="workflow-reset"){await post("workflow",{graph:null});paint();return}
  if(action==="page-direction")return modal("Page "+(pageIndex+1)+" direction",area("Page composition and dramatic intent","direction_notes",p.direction_notes)+area("Lighting and atmosphere","lighting",p.lighting)+area("Continuity notes for all panels","continuity",p.continuity),f=>post(action,{...ctx,...values(f)}));
  if(action==="preview-prompts"){
    const items=await (await request("/api/preview-prompts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(ctx)})).json();
    const d=$("modal");
    d.innerHTML='<h2>Page '+(pageIndex+1)+' render prompts</h2>'+items.map(x=>'<article><h3>Panel '+x.panel+' · '+x.width+' × '+x.height+'</h3><p class="caption">'+esc(x.prompt)+'</p><p>Avoid · '+esc(x.negative)+'</p><p>'+esc(x.loras.map(([name,strength])=>"LoRA "+name+" × "+strength).join(" · "))+'</p><div class="thumbs">'+x.ref_groups.map((group,i)=>'<div><b>Picture '+(i+1)+'</b><div class="thumbs">'+group.map((r,j)=>img(r,"Picture "+(i+1)+", reference "+(j+1))).join("")+'</div></div>').join("")+'</div></article>').join("")+button("Done","close");
    d.showModal();return;
  }
  if(action==="source"){
    const saved=state.chapters.find(v=>v.id===id);
    const d=$("modal");
    d.innerHTML='<h2>'+esc(saved.title)+'</h2><p class="caption">'+esc(saved.script)+'</p><div class="row">'+button("Replace draft with this source","restore-script","",id)+button("Close","close")+'</div>';
    d.showModal();return;
  }
  if(action==="restore-script"){await post(action,{id});$("modal").close();paint();return}
  if(action==="edit-asset"){
    const a=state.assets.find(v=>v.id===id)||{};
    return modal(a.name||"New cast entry",field("Name, as written in the script","name",a.name)+select("Kind","kind",["character","location","prop"].map(v=>[v,v]),a.kind)+area("Appearance and distinctive features","description",a.description)+field("Character LoRA file (optional)","lora",a.lora)+field("LoRA strength","strength",a.strength??1)+refField,async f=>{
      const {upload,...data}=values(f);await post("asset",{...data,id,image:await picture(upload),label:upload.name});
    });
  }
  if(action==="add-ref")return modal("Add reference",field("Collection","collection","Model references")+field("Reference label","label")+refField,async f=>{const image=await picture(f.get("upload"));if(!image)throw Error("Choose an image.");await post("reference",{id,label:f.get("label"),collection:f.get("collection"),image})});
  if(action==="edit-panel"){
    const item=p.panels.find(v=>v.id===id);
    const savedRefs=new Set((item.extra_refs||[]).map(pair=>JSON.stringify(pair)));
    const library=state.assets.flatMap(a=>a.refs.map(r=>{
      const pair=JSON.stringify([a.id,r.id]);
      return '<label><input type="checkbox" name="extra-ref" value="'+esc(pair)+'" '+(savedRefs.has(pair)?'checked':'')+'>'+esc(a.name+" · "+(r.collection||"References")+" · "+r.label)+'</label>';
    })).join("");
    return modal("Page "+(pageIndex+1)+" · Panel "+(p.panels.indexOf(item)+1),area("Illustration direction","prompt",item.prompt)+field("Camera and framing","camera",item.camera)+area("Director notes","director",item.director)+'<label><input type="checkbox" name="previous" '+(item.previous?'checked':'')+'> Use selected artwork from the preceding panel</label>'+balloonEditor(item)+select("First balloon","treatment",[["auto","Where reading starts ("+(c.direction==="rtl"?"upper right":"upper left")+")"],["upper-right","Upper right"],["upper-left","Upper left"]],item.treatment==="caption"?"auto":item.treatment||"auto")+'<fieldset><legend>Cast and references</legend>'+state.assets.map(a=>'<label><input type="checkbox" name="cast" value="'+a.id+'" '+(item.cast.includes(a.id)?"checked":"")+'>'+esc(a.name)+'</label>').join("")+'</fieldset>'+(library?'<details><summary>Additional library references</summary><fieldset><legend>Images for this panel</legend>'+library+'</fieldset></details>':'')+'<details><summary>Original script beat</summary><p class="caption">'+esc(item.source)+'</p></details>',f=>post("panel",{...ctx,panel:id,prompt:f.get("prompt"),camera:f.get("camera"),director:f.get("director"),previous:f.has("previous"),balloons:balloonValues(f),treatment:f.get("treatment"),cast:f.getAll("cast"),extra_refs:f.getAll("extra-ref").map(v=>JSON.parse(v))}));
  }
  if(action==="layout")return modal("Page layout",select("Panel arrangement","layout",LAYOUT_CHOICES,p.layout)+select("Chapter reading direction","direction",directions,c.direction),f=>post("layout",{...ctx,...values(f)}));
  if(action==="plan"){
    const draft=values(new FormData($("script-form")));
    const pages=await (await request("/api/plan-preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(draft)})).json();
    const review=pages.map((pg,i)=>'<article><label><input type="checkbox" name="indices" value="'+i+'" checked> Page '+(i+1)+' · '+esc(pg.scene)+'</label>'+pg.panels.map((p,n)=>'<details><summary>Panel '+(n+1)+' · '+esc(p.prompt)+'</summary><p>'+esc([pg.direction_notes,pg.lighting,pg.continuity,p.camera,p.director,p.names.join(", ")].filter(Boolean).join(" · "))+'</p><p class="caption">'+esc(p.lettering)+'</p></details>').join("")+'</article>').join("");
    return modal("Review pages from script",review,async f=>{await post("plan",{...draft,indices:f.getAll("indices").map(Number)});pageIndex=0;tab="Pages"},"Create selected pages");
  }
  if(action==="draft"){await post(action,values(new FormData($("script-form"))));notify("Script draft saved.");
  }else if(action==="settings"){await post(action,{...settingsDraft,...values(new FormData($("settings-form")))});settingsDraft={};notify("Settings saved.")}
  else if(action==="open-chapter"){await post("chapter",{id});pageIndex=0;go("Pages")}
  else if(action==="use-ref")await post("use-reference",{id,ref:el.dataset.ref});
  else if(action==="cancel"){await post(action,{id});await refreshQueue();return}
  else if(action==="sheet")await post(action,{id});
  else await post(action,{...ctx,panel:id,take:el.dataset.take});
  if(action==="sheet"||action==="render")notify("Artwork queued. Track progress in Render queue.");
  paint();
}
document.addEventListener("click",async e=>{
  const el=e.target.closest("[data-action]");if(!el)return;
  el.disabled=true;
  try{await act(el)}catch(err){notify(err.message)}finally{el.disabled=false}
});
document.addEventListener("input",e=>{if(e.target.closest("#settings-form"))settingsDraft[e.target.name]=e.target.value});
document.addEventListener("change",async e=>{
  try{
    if(e.target.id==="script-file"&&e.target.files[0])$("script-form").elements.script.value=await e.target.files[0].text();
    if(e.target.id==="page-select"){pageIndex=Number(e.target.value);paint()}
    if(e.target.id==="chapter-select"){await post("chapter",{id:e.target.value});pageIndex=0;paint()}
  }catch(err){notify(err.message)}
});
let stateTag=null;
async function refresh(){
  if(tab==="Render queue")return refreshQueue();
  const version=serial;
  const response=await fetch("/state",{headers:stateTag&&state?{"If-None-Match":stateTag}:{}});
  if(response.status===304)return;
  if(!response.ok)throw Error(response.statusText);
  stateTag=response.headers.get("ETag");
  const next=await response.json();
  if(version!==serial || $("modal").open)return;
  if(!state||(["Production","Render queue","Cast","Reference library","Studio","Assemble"].includes(tab)&&JSON.stringify(state)!==JSON.stringify(next))){
    state=next;serial++;paint();
  }
}
refresh().catch(e=>notify(e.message));
async function pollWorkspace(){
  try{await refresh()}catch(e){notify(e.message)}
  setTimeout(pollWorkspace,tab==="Render queue"?1000:4000);
}
setTimeout(pollWorkspace,1000);
