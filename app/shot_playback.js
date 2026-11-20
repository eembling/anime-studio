/* Shots use one cached MP4 with picture and sound, never a second audio clock. */
const SHOT_PLAYERS=new Set();
function bindShotPlayback(video,shot,take){
 if(video._shotPlayer)return;
 const host=video.closest('.shot-preview');
 const button=host.querySelector('.shot-preview-play');
 const status=host.querySelector('.shot-preview-status');
 const sound=host.querySelector('.shot-preview-sound');
 const loop=host.querySelector('.shot-preview-loop');
 const download=host.querySelector('.shot-preview-download');
 let preparing=false,disposed=false,ready=false,requestId=0;
 const report=message=>{status.textContent=message};
 const play=async()=>{
  for(const player of SHOT_PLAYERS)if(player.video!==video)player.video.pause();
  video.muted=false;
  try{await video.play();playing()}
  catch(error){if(!disposed&&error.name!=='AbortError'){button.textContent='Play with sound';report('Preview ready. Press Play to start.')}}
 };
 const prepare=async()=>{
  if(preparing||disposed)return;
  if(ready){if(video.paused)await play();else video.pause();return}
  preparing=true;const id=++requestId;
  host.classList.add('preparing');button.disabled=true;button.textContent='Preparing…';
  report('Combining this take with its dialogue and timed sounds…');
  try{
   let retry=true;
   while(!disposed&&id===requestId){
    const response=await fetch('/api/shot/preview',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({shot_id:shot.id,take_id:take.id,retry})});
    if(!response.ok)throw new Error('The preview server could not be reached.');
    const result=await response.json();retry=false;
    if(disposed||id!==requestId)return;
    if(result.ok===false||result.status==='error')throw new Error(result.error||'Preview could not be prepared.');
    if(result.status==='ready'){
     ready=true;video.src=result.url;video.controls=true;video.muted=false;video.loop=loop.checked;
     video.load();download.href=result.url;download.hidden=false;
     sound.textContent=result.audio_label+(result.mixer_silenced?' · Mixer muted':'');
     host.classList.add('ready');await play();return;
    }
    report(result.status==='queued'?'Waiting for another shot preview…':'Combining picture and sound…');
    await new Promise(resolve=>setTimeout(resolve,500));
   }
  }catch(error){if(!disposed){report(error.message+' Press Retry.');button.textContent='Retry preview'}}
  finally{if(!disposed){preparing=false;host.classList.remove('preparing');button.disabled=false;if(ready)button.textContent=video.paused?'Play':'Pause'}}
 };
 const playing=()=>{button.textContent='Pause';for(const player of SHOT_PLAYERS)if(player.video!==video)player.video.pause();report(sound.textContent.includes('Mixer muted')?'Audio is muted in the mixer.':sound.textContent==='No audio assigned'?'Add dialogue or a timed sound to this shot.':'')};
 const failed=()=>{ready=false;video.controls=false;report('This preview could not play. Press Retry.');button.textContent='Retry preview'};
 const waiting=()=>{if(!video.paused)report('Loading preview…')};
 const setLoop=()=>{video.loop=loop.checked};
 const paused=()=>{if(ready&&!disposed){button.textContent=video.ended?'Replay':'Play';if(typeof renderShots==='function')Promise.resolve().then(()=>{if(!disposed&&typeof tab!=='undefined'&&tab==='shots')renderShots()})}};
 const suspend=()=>{requestId++;preparing=false;video.pause();host.classList.remove('preparing');button.disabled=false;if(!ready){button.textContent='Play with sound';report('Ready to prepare · original clip timing')}};
 const destroy=()=>{
  disposed=true;preparing=false;requestId++;video.pause();video.removeAttribute('src');video.load();
  button.removeEventListener('click',prepare);loop.removeEventListener('change',setLoop);
  video.removeEventListener('play',playing);video.removeEventListener('playing',playing);
  video.removeEventListener('pause',paused);video.removeEventListener('ended',paused);
  video.removeEventListener('error',failed);video.removeEventListener('waiting',waiting);SHOT_PLAYERS.delete(player);
 };
 const player={video,destroy,suspend,isPreparing:()=>preparing};video._shotPlayer=player;SHOT_PLAYERS.add(player);
 button.addEventListener('click',prepare);loop.addEventListener('change',setLoop);
 video.addEventListener('play',playing);video.addEventListener('playing',playing);
 video.addEventListener('pause',paused);video.addEventListener('ended',paused);
 video.addEventListener('error',failed);video.addEventListener('waiting',waiting);
}
new MutationObserver(()=>{
 for(const player of SHOT_PLAYERS)if(!player.video.isConnected)player.destroy();
}).observe(document.body,{childList:true,subtree:true});
