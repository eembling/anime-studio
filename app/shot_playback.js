/* Clip previews share Studio's seconds-based synchronization and error handling. */
const SHOT_PLAYERS=new Set();
function clipSourceOffset(shot,take){
 if(take.stage!=='continue')return 0;
 const root=shot.takes.find(item=>item.id===shot.clip_take);
 const prior=[root,...(shot.cont||[]).slice(0,take.segment||0).map(id=>shot.takes.find(item=>item.id===id))].filter(Boolean);
 const seconds=prior.reduce((sum,item,index)=>sum+(EDIT_DURATIONS.get(item.url)||(item.frames&&item.fps?item.frames/item.fps:0))-(index?1/(item.fps||16):0),0);
 return seconds-1/(take.fps||16);
}
function bindShotPlayback(video,shot,take){
 if(video._shotPlayer)return;
 const audio=new Audio(video.dataset.audio);audio.preload='auto';
 const status=document.createElement('span');
 status.className='clip-playback-status';status.setAttribute('role','status');
 video.parentElement.append(status);
 let wanted=false,timer=null,ownPause=false,starting=false,internalSeek=false;
 const report=message=>{status.textContent=message};
 const stop=()=>{
  wanted=false;clearInterval(timer);timer=null;audio.pause();
 };
 const pauseVideo=()=>{
  if(!video.paused){ownPause=true;video.pause()}
 };
 const update=(force=false)=>{
  if(!video.isConnected){destroy();return}
  if(starting)return;
  if(video.error||audio.error){stop();pauseVideo();report('Could not load clip audio or video. Check the source file.');return}
  const source=clipSourceOffset(shot,take)+video.currentTime-(shot.dialogue_offset||0);
  if(source<0){audio.pause();audio._editHold=true}
  const drift=source-audio.currentTime;
  // Keep the audio decoder at its original rate. WebKit can stall its
  // clock when playbackRate changes; use audio as the steady playback clock.
  if(internalSeek&&video.seeking)return;
  if(!force&&wanted&&!video.paused&&!audio.paused&&!audio.seeking&&source>=0&&
     (!Number.isFinite(audio.duration)||source<audio.duration)&&Math.abs(drift)>.12){
   internalSeek=true;video.currentTime=Math.max(0,video.currentTime-drift);return;
  }
  const seekAudio=force||(video.paused&&Math.abs(drift)>.35);
  const duration=audio.duration;
  audio._editHold=source<0||(Number.isFinite(duration)&&(source>=duration||(audio.ended&&source>=duration-.08)));
  if(!audio._editHold&&seekAudio&&!audio.seeking&&Math.abs(drift)>(force ? .001 : .35))audio.currentTime=Math.max(0,source);
  const ready=syncEditMedia(video,video.currentTime,false,true)&&
    (audio._editHold||(!audio.seeking&&audio.readyState>=2));
  if(wanted&&!ready){audio.pause();pauseVideo();report('Buffering picture and dialogue…');return}
  report('');
  if(wanted&&(video.paused||(source>=0&&!audio._editHold&&audio.paused))){
   starting=true;
   const audioReady=source>=0&&!audio._editHold?audio.play():Promise.resolve();
   audioReady.then(async()=>{
    if(!wanted)return;
    if(source>=0&&!audio._editHold){
     // WebKit can resolve play() before its audio clock starts. Wait for
     // that clock, then align the picture before resuming it.
     const before=audio.currentTime,deadline=performance.now()+2000;
     while(wanted&&audio.currentTime<=before+.005&&!audio.ended){
      if(performance.now()>deadline)throw new Error('Dialogue clock did not start');
      await new Promise(resolve=>setTimeout(resolve,10));
     }
     if(!wanted)return;
     const target=audio.currentTime-clipSourceOffset(shot,take)+(shot.dialogue_offset||0);
     if(Number.isFinite(video.duration)&&target<video.duration&&Math.abs(video.currentTime-target)>.02){
      internalSeek=true;video.currentTime=Math.max(0,target);
     }
    }
    return video.paused?video.play():Promise.resolve();
   }).catch(error=>{
    if(error.name!=='AbortError'){stop();pauseVideo();if(!status.textContent)report('Playback could not start. Press Play to retry.')}
   }).finally(()=>starting=false);
  }
 };
 // Shared helper reports Studio failures only while its own transport is active.
 // Handle this player's rejection locally as well.
 const originalPlay=audio.play.bind(audio);
 audio.play=()=>originalPlay().catch(error=>{
  if(error.name!=='AbortError'){stop();pauseVideo();report('Dialogue could not play. Press Play to retry.')}
  throw error;
 });
 const play=()=>{
  if(wanted)return;
  for(const player of SHOT_PLAYERS)if(player.video!==video)player.pause();
  wanted=true;pauseVideo();update(true);
  if(!timer)timer=setInterval(update,30);
 };
 const pause=()=>{if(ownPause){ownPause=false;return}if(!video.paused)return;stop()};
 const seek=()=>{const force=!internalSeek;internalSeek=false;update(force)};
 const ended=()=>{stop();audio.pause()};
 const destroy=()=>{
  stop();video.removeEventListener('play',play);video.removeEventListener('pause',pause);
  video.removeEventListener('seeked',seek);video.removeEventListener('ended',ended);
  audio.removeAttribute('src');audio.load();status.remove();SHOT_PLAYERS.delete(player);
 };
 const player={video,pause:()=>{stop();pauseVideo()},destroy};
 SHOT_PLAYERS.add(player);video._shotPlayer=player;video._a=audio;
 video.addEventListener('play',play);video.addEventListener('pause',pause);
 video.addEventListener('seeked',seek);video.addEventListener('ended',ended);
}
new MutationObserver(()=>{
 for(const player of SHOT_PLAYERS)if(!player.video.isConnected)player.destroy();
}).observe(document.body,{childList:true,subtree:true});
