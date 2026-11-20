import AppKit
import WebKit
final class Check: NSObject, WKNavigationDelegate {
 var web: WKWebView!
 var tries=0
 var window:NSWindow!
 func start() {
  let config=WKWebViewConfiguration()
  config.mediaTypesRequiringUserActionForPlayback=[]
  config.websiteDataStore = .nonPersistent()
  web=WKWebView(frame:NSRect(x:0,y:0,width:1200,height:900),configuration:config)
  web.navigationDelegate=self
  window=NSWindow(contentRect:NSRect(x:40,y:40,width:700,height:500),styleMask:[.titled],backing:.buffered,defer:false)
  window.contentView=web
  window.title="Anime Studio playback check"
  window.makeKeyAndOrderFront(nil)
  NSApplication.shared.activate(ignoringOtherApps:true)
  web.load(URLRequest(url:URL(string:"http://127.0.0.1:8871")!))
 }
 func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { poll() }
 func poll(){
  web.evaluateJavaScript("typeof S!=='undefined' && !!S?.finishing && !!document.querySelector('#shotlist video')") { result,error in
   if (result as? Bool)==true { self.clip();return }
   self.tries+=1
   if self.tries>60 { print("FAIL native fixture did not become ready",String(describing:error));exit(1) }
   DispatchQueue.main.asyncAfter(deadline:.now()+0.2){self.poll()}
  }
 }
 func clip() {
  web.evaluateJavaScript("document.querySelector('#shotlist video').play(); true") { _,error in
   if let error=error {print("FAIL clip start",error);exit(1)}
   DispatchQueue.main.asyncAfter(deadline:.now()+1.5){
    self.web.evaluateJavaScript("JSON.stringify({video:document.querySelector('#shotlist video').currentTime,vReady:document.querySelector('#shotlist video').readyState,aReady:document.querySelector('#shotlist video')._a?.readyState,vNet:document.querySelector('#shotlist video').networkState,aNet:document.querySelector('#shotlist video')._a?.networkState,aError:document.querySelector('#shotlist video')._a?.error?.message,audio:document.querySelector('#shotlist video')._a?.currentTime,paused:document.querySelector('#shotlist video')._a?.paused,status:document.querySelector('.clip-playback-status')?.textContent})"){result,error in
     guard let text=result as? String,let bytes=text.data(using:.utf8),let value=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],
           let video=value["video"] as? Double,let audio=value["audio"] as? Double,video>0.2,audio>0.2,abs(video-audio)<0.2 else {print("FAIL native clip",String(describing:result),String(describing:error));exit(1)}
     print("Native clip synchronization passed",text);self.continuous(video)
    }
   }
  }
 }
 func continuous(_ start:Double){
  DispatchQueue.main.asyncAfter(deadline:.now()+1.5){
   self.web.evaluateJavaScript("JSON.stringify({time:document.querySelector('#shotlist video').currentTime,paused:document.querySelector('#shotlist video').paused,status:document.querySelector('.clip-playback-status')?.textContent})"){result,error in
    guard let text=result as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],
          let time=state["time"] as? Double,time-start>1.1,(state["paused"] as? Bool)==false,(state["status"] as? String)=="" else {print("FAIL sustained playback",String(describing:result),String(describing:error));exit(1)}
    print("Native sustained playback passed",text);self.looped()
   }
  }
 }
 func looped(){
  DispatchQueue.main.asyncAfter(deadline:.now()+2){
   self.web.evaluateJavaScript("JSON.stringify({video:document.querySelector('#shotlist video').currentTime,audio:document.querySelector('#shotlist video')._a?.currentTime,paused:document.querySelector('#shotlist video').paused,status:document.querySelector('.clip-playback-status')?.textContent})"){result,error in
    guard let text=result as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],
          let video=state["video"] as? Double,let audio=state["audio"] as? Double,video>0.2,video<2,abs(video-audio)<0.2,(state["paused"] as? Bool)==false,(state["status"] as? String)=="" else {print("FAIL looping playback",String(describing:result),String(describing:error));exit(1)}
    print("Native looping playback passed",text);self.studio()
   }
  }
 }
 func studio(){
  web.callAsyncJavaScript("document.querySelector('#shotlist video').pause();go('studio');seekEdit(0);await startMixAudio();playEdit();return MIX_READY;",arguments:[:],in:nil,in:.page){result in
   switch result {
   case .failure(let error):print("FAIL native mixer",error);exit(1)
   case .success:
    DispatchQueue.main.asyncAfter(deadline:.now()+1.5){
     self.web.evaluateJavaScript("JSON.stringify({time:EDIT_TIME,playing:EDIT_PLAYING,ready:MIX_READY,state:document.querySelector('#edit-state').textContent,hidden:document.hidden,toast:document.querySelector('#toast').textContent,error:document.querySelector('#edit-monitor video')?.error?.message})"){value,error in
      guard let text=value as? String,let bytes=text.data(using:.utf8),let state=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any],
            let time=state["time"] as? Double,time>0.2,(state["ready"] as? Bool)==true,(state["playing"] as? Bool)==true else {print("FAIL native Studio",String(describing:value),String(describing:error));exit(1)}
      print("Native Studio and AudioWorklet passed",text);exit(0)
     }
    }
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
