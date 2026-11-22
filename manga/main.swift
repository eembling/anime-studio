import Cocoa
import WebKit

final class MangaStudio: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    let web = WKWebView()
    let server = Process()
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1260, height: 840),
                          styleMask: [.titled, .closable, .resizable, .miniaturizable],
                          backing: .buffered, defer: false)
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        for (name, entries) in [("Manga Studio", [("Quit Manga Studio", "terminate:", "q")]),
                                ("Edit", [("Undo","undo:","z"),("Cut","cut:","x"),("Copy","copy:","c"),("Paste","paste:","v"),("Select All","selectAll:","a")])] {
            let sub = NSMenu(title: name)
            for (title, action, key) in entries {
                sub.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
            }
            menu.addItem(withTitle: name, action: nil, keyEquivalent: "").submenu = sub
        }
        NSApp.mainMenu = menu
        web.navigationDelegate = self
        web.uiDelegate = self
        window.contentView = web
        window.title = "Manga Studio"
        window.center()
        window.setFrameAutosaveName("MangaStudioWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        guard let resources = Bundle.main.resourceURL else { return }
        do {
            let python = try String(contentsOf: resources.appendingPathComponent("python-path")).trimmingCharacters(in: .whitespacesAndNewlines)
            server.executableURL = URL(fileURLWithPath: python)
            server.arguments = [resources.appendingPathComponent("manga/studio.py").path, "--port", "0"]
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONPATH"] = resources.appendingPathComponent("vendor").path
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            server.environment = environment
            let pipe = Pipe()
            server.standardOutput = pipe
            try server.run()
            DispatchQueue.global().async {
                let data = pipe.fileHandleForReading.availableData
                DispatchQueue.main.async {
                    guard let address = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
                          address.hasPrefix("http://127.0.0.1:"), let url = URL(string: address) else {
                        self.failure("The local server did not start. Verify the build’s Python runtime is installed.")
                        return
                    }
                    self.web.load(URLRequest(url: url))
                }
            }
        } catch { failure(error.localizedDescription) }
    }
    func failure(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Manga Studio could not start"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        if server.isRunning { server.terminate() }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.url : nil) }
    }
}
let app = NSApplication.shared
let delegate = MangaStudio()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
