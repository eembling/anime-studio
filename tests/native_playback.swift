import AppKit
import WebKit
final class Check: NSObject, WKNavigationDelegate {
 var web: WKWebView!
 var tries=0
 var projectCheck=false
 var returningToOriginal=false
 var window:NSWindow!
 func start() {
  let config=WKWebViewConfiguration()
  config.mediaTypesRequiringUserActionForPlayback=[]
  config.websiteDataStore = .nonPersistent()
  web=WKWebView(frame:NSRect(x:0,y:0,width:1200,height:900),configuration:config)
  web.navigationDelegate=self
  window=NSWindow(contentRect:NSRect(x:40,y:40,width:700,height:500),styleMask:[.titled],backing:.buffered,defer:false)
  window.level = .floating
  window.contentView=web
  window.title="Anime Studio playback check"
  window.makeKeyAndOrderFront(nil)
  NSApplication.shared.activate(ignoringOtherApps:true)
  web.load(URLRequest(url:URL(string:"http://127.0.0.1:8871")!))
 }
 func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
  if projectCheck { verifyProject() } else { poll() }
 }
 func poll(){
  web.evaluateJavaScript("typeof S!=='undefined' && !!S?.finishing && !!document.querySelector('#shotlist video')") { result,error in
   if (result as? Bool)==true { self.clip();return }
   self.tries+=1
   if self.tries>60 { print("FAIL native fixture did not become ready",String(describing:error));exit(1) }
   DispatchQueue.main.asyncAfter(deadline:.now()+0.2){self.poll()}
  }
 }
 func clip() {
  web.evaluateJavaScript("document.querySelector('.shot-preview-play').click();true") { _,error in
   if let error=error {print("FAIL clip start",error);exit(1)}
   self.prepared()
  }
 }
 func prepared(){
  web.evaluateJavaScript("!!document.querySelector('#shotlist video')?.src && document.querySelector('#shotlist video').readyState>=2") { result,error in
   if (result as? Bool) != true {
    self.tries+=1
    if self.tries>100 {print("FAIL preview preparation",String(describing:error));exit(1)}
    DispatchQueue.main.asyncAfter(deadline:.now()+0.2){self.prepared()};return
   }
   self.web.callAsyncJavaScript("window.probeContext=new AudioContext();window.probeMeter=probeContext.createAnalyser();probeContext.createMediaElementSource(document.querySelector('#shotlist video')).connect(probeMeter);probeMeter.connect(probeContext.destination);await probeContext.resume();window.audioPeak=()=>{const data=new Float32Array(probeMeter.fftSize);probeMeter.getFloatTimeDomainData(data);return Math.max(...data.map(Math.abs))};return true;",arguments:[:],in:nil,in:.page){result in
    if case .failure(let error)=result {print("FAIL audio meter",error);exit(1)}
    self.web.evaluateJavaScript("document.querySelector('#shotlist video').currentTime=0;document.querySelector('#shotlist video').play();true"){_,_ in
     DispatchQueue.main.asyncAfter(deadline:.now()+1.5){self.sample()}
    }
   }
  }
 }
 func sample(){
  web.evaluateJavaScript("JSON.stringify({time:document.querySelector('#shotlist video').currentTime,muted:document.querySelector('#shotlist video').muted,peak:audioPeak(),src:document.querySelector('#shotlist video').src,paused:document.querySelector('#shotlist video').paused})"){result,error in
   guard let text=result as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let time=state["time"] as? Double,time>0.5,let peak=state["peak"] as? Double,peak>0.001,(state["muted"] as? Bool)==false,(state["paused"] as? Bool)==false else {print("FAIL audible combined playback",String(describing:result),String(describing:error));exit(1)}
   print("Native combined MP4 has audible output",text);self.continuous(time)
  }
 }
 func continuous(_ start:Double){
  DispatchQueue.main.asyncAfter(deadline:.now()+1.5){
   self.web.evaluateJavaScript("JSON.stringify({time:document.querySelector('#shotlist video').currentTime,peak:audioPeak(),paused:document.querySelector('#shotlist video').paused})"){result,error in
    guard let text=result as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let time=state["time"] as? Double,time-start>1.1,let peak=state["peak"] as? Double,peak>0.001,(state["paused"] as? Bool)==false else {print("FAIL sustained playback",String(describing:result),String(describing:error));exit(1)}
    print("Native sustained audible playback passed",text);self.looped()
   }
  }
 }
 func looped(){
  DispatchQueue.main.asyncAfter(deadline:.now()+2){
   self.web.evaluateJavaScript("JSON.stringify({time:document.querySelector('#shotlist video').currentTime,peak:audioPeak(),paused:document.querySelector('#shotlist video').paused})"){result,error in
    guard let text=result as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let time=state["time"] as? Double,time>0.2,time<2,let peak=state["peak"] as? Double,peak>0.001,(state["paused"] as? Bool)==false else {print("FAIL looping playback",String(describing:result),String(describing:error));exit(1)}
    print("Native looping audible playback passed",text);self.seek()
   }
  }
 }
 func seek(){
  web.evaluateJavaScript("document.querySelector('#shotlist video').currentTime=2;true"){_,_ in
   DispatchQueue.main.asyncAfter(deadline:.now()+0.5){
    self.web.evaluateJavaScript("JSON.stringify({time:document.querySelector('#shotlist video').currentTime,peak:audioPeak()})"){result,error in
     guard let text=result as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let time=state["time"] as? Double,time>=2,time<3,let peak=state["peak"] as? Double,peak>0.001 else {print("FAIL seeking with audio",String(describing:result),String(describing:error));exit(1)}
     print("Native seeking retained audible output",text);self.studio()
    }
   }
  }
 }
 func studio(){
  web.callAsyncJavaScript("document.querySelector('#shotlist video').pause();go('studio');frameEdit(1);if(editFps()!==16||Math.abs(EDIT_TIME-1/16)>0.000001||+document.querySelector('#edit-playhead').step!==1/16)throw new Error('Studio is not using 16 fps frame timing');seekEdit(0);await startMixAudio();window.studioMeter=MIX_CONTEXT.createAnalyser();MIX_PROCESSOR.connect(studioMeter);window.studioPeak=()=>{const data=new Float32Array(studioMeter.fftSize);studioMeter.getFloatTimeDomainData(data);return Math.max(...data.map(Math.abs))};playEdit();return MIX_READY;",arguments:[:],in:nil,in:.page){result in
   switch result {
   case .failure(let error):print("FAIL native mixer",error);exit(1)
   case .success:
    DispatchQueue.main.asyncAfter(deadline:.now()+1.5){
     self.web.evaluateJavaScript("JSON.stringify({time:EDIT_TIME,playing:EDIT_PLAYING,ready:MIX_READY,state:document.querySelector('#edit-state').textContent,hidden:document.hidden,toast:document.querySelector('#toast').textContent,error:document.querySelector('#edit-monitor video')?.error?.message})"){value,error in
      guard let text=value as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],
            let time=state["time"] as? Double,time>0.7,(state["ready"] as? Bool)==true,(state["playing"] as? Bool)==true else {print("FAIL native Studio",String(describing:value),String(describing:error));exit(1)}
      print("Native 16 fps Studio startup passed",text);self.studioSustained(time)
     }
    }
   }
  }
 }
 func studioSustained(_ start:Double){
  let wallStart=ProcessInfo.processInfo.systemUptime
  DispatchQueue.main.asyncAfter(deadline:.now()+1.5){
   self.web.evaluateJavaScript("JSON.stringify({time:EDIT_TIME,video:document.querySelector('#edit-monitor video')?.currentTime,audio:(()=>{const node=STUDIO_AUDIO_SOURCES.get('dialogue');return node?node.offset+MIX_CONTEXT.currentTime-node.when:null})(),peak:studioPeak(),hidden:document.hidden,playing:EDIT_PLAYING,state:document.querySelector('#edit-state').textContent})"){value,error in
    let elapsed=ProcessInfo.processInfo.systemUptime-wallStart
    guard let text=value as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let time=state["time"] as? Double,time-start>1.1,abs((time-start)-elapsed)<0.25,let video=state["video"] as? Double,abs(time-video)<0.15,let audio=state["audio"] as? Double,abs(video-audio)<0.12,let peak=state["peak"] as? Double,peak>0.001,(state["playing"] as? Bool)==true else {print("FAIL sustained Studio picture/audio",String(describing:value),String(describing:error));exit(1)}
    print("Native sustained Studio playback and audible mixer passed",text);self.studioSeek()
   }
  }
 }

 func studioSeek(){
  web.evaluateJavaScript("seekEdit(1);true"){_,_ in
   DispatchQueue.main.asyncAfter(deadline:.now()+0.4){
    self.web.evaluateJavaScript("JSON.stringify({time:EDIT_TIME,video:document.querySelector('#edit-monitor video')?.currentTime,peak:studioPeak()})"){value,error in
     guard let text=value as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],let time=state["time"] as? Double,time>=1,time<1.8,let video=state["video"] as? Double,abs(time-video)<0.15,let peak=state["peak"] as? Double,peak>0.001 else {print("FAIL Studio seek",String(describing:value),String(describing:error));exit(1)}
     print("Native Studio seek retained audible output",text)
     self.web.evaluateJavaScript("stopEdit();true"){_,_ in
      DispatchQueue.main.asyncAfter(deadline:.now()+0.2){
       self.web.evaluateJavaScript("!EDIT_PLAYING && STUDIO_AUDIO_SOURCES.size===0 && studioPeak()<0.0001"){value,error in
        guard (value as? Bool)==true else {print("FAIL Studio pause",String(describing:value),String(describing:error));exit(1)}
        print("Native Studio pause stopped audio sources");self.soundEffects()
       }
      }
     }
    }
   }
  }
 }

 func soundEffects(){
  web.callAsyncJavaScript("""
   document.querySelector('[data-t="effects"]').click();
   document.querySelector('#t-effects button').click();
   const input=document.querySelector('#sound-file'),transfer=new DataTransfer();
   const blob=await (await fetch(S.shots[0].audio_url)).blob();
   transfer.items.add(new File([blob],'Fixture bell.wav',{type:'audio/wav'}));
   input.files=transfer.files;input.dispatchEvent(new Event('change'));
   await saveLibrarySound('',document.querySelector('#dlg .btn.pri'));
   const card=document.querySelector('#effect-results .sound-item');
   if(card?.querySelector('h3').textContent!=='Fixture bell')throw Error('Imported sound missing');
   card.querySelector('button').click();
   document.querySelector('[data-t="studio"]').click();
   for(const start of [0.625,1.875]){
    seekEdit(start);
    if(start===0.625)document.querySelector('#paste-effect').click();
    else document.dispatchEvent(new Event('paste',{cancelable:true}));
    for(let i=0;i<100&&!S.audio_cues.some(c=>c.kind==='sfx'&&c.start===start);i++)
     await new Promise(resolve=>setTimeout(resolve,50));
   }
   const cues=S.audio_cues.filter(c=>c.kind==='sfx'&&c.name==='Fixture bell');
   if(cues.length!==2||cues[0].path!==cues[1].path||
      !cues.every(c=>document.querySelector('#edit-track [data-cue="'+c.id+'"]')))
    throw Error('Repeated effect placement missing from timeline');
   return true;
   """,arguments:[:],in:nil,in:.page){result in
    switch result{
    case .success:print("Sound library import, copy and repeated Studio Effects placement passed");self.createProject()
    case .failure(let error):print("FAIL sound effects",error);exit(1)
    }
   }
 }

 func createProject(){
  projectCheck=true
  web.evaluateJavaScript("""
   document.querySelector('[data-t="projects"]').click();
   document.querySelector('#t-projects button').click();
   document.querySelector('#project-input').value='Native isolated project';
   document.querySelector('#project-form').requestSubmit();
   true
   """){_,error in
    if let error=error { print("FAIL project creation",error);exit(1) }
   }
 }
 func verifyProject(){
  let expected=returningToOriginal ? "S?.shots?.length===1&&S?.audio_cues?.length===2" :
     "S?.project_name==='Native isolated project'&&S?.shots?.length===0&&S?.audio_cues?.length===0"
  web.evaluateJavaScript(expected){value,error in
   if (value as? Bool) != true {
    if let error=error { print("FAIL project state",error);exit(1) }
    DispatchQueue.main.asyncAfter(deadline:.now()+0.15){self.verifyProject()}
    return
   }
   if self.returningToOriginal {
    print("Native Projects create, switch back, and isolated media state passed");exit(0)
   }
   self.returningToOriginal=true
   self.web.callAsyncJavaScript("""
    document.querySelector('[data-t="projects"]').click();
    for(let i=0;i<100;i++){
     const button=document.querySelector('[data-open-project]');
     if(button){button.click();return true}
     await new Promise(resolve=>setTimeout(resolve,50));
    }
    throw Error('Original project not found in catalog');
    """,arguments:[:],in:nil,in:.page){result in
    if case .failure(let error)=result {print("FAIL switch project",error);exit(1)}
   }
  }
 }

}
let app=NSApplication.shared
app.setActivationPolicy(.accessory)
let check=Check()
check.start()
DispatchQueue.main.asyncAfter(deadline:.now()+30){print("FAIL native timeout");exit(1)}
app.run()
