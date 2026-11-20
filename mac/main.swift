// Anime Studio - native macOS window around the local Anime Studio server.
// Built automatically on first launch by the app's launcher (needs Xcode Command Line Tools).
import Cocoa
import WebKit

func databaseAccessDenied(_ message: String) -> Bool {
    let text = message.lowercased()
    return text.contains("anime_studio_data_access_denied") ||
        (text.contains("sqlite") && (text.contains("authorization denied") || text.contains("permission denied") || text.contains("operation not permitted")))
}

func htmlEscaped(_ text: String) -> String {
    text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var lastDownload: URL?
    let env = ProcessInfo.processInfo.environment
    lazy var port: String = env["AS_PORT"] ?? "8700"
    var selectedDataDir: String?
    var dataDir: String { selectedDataDir ?? env["AS_DATA"] ?? (NSHomeDirectory() + "/Library/Application Support/AnimeStudio/Project Data") }
    var startupGeneration = 0
    var startupFailed = false
    var serverReady = false
    var quitting = false
    var folderPanelOpen = false
    var grantedFolder: URL?
    let configDirectory = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/AnimeStudio")

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        restoreFolderAccess()
        let config = WKWebViewConfiguration()
        // Studio starts the next dialogue/cue after an asynchronous media load.
        // The transport still requires Play, but those later tracks must not
        // require a separate WebKit user gesture at each shot boundary.
        config.mediaTypesRequiringUserActionForPlayback = []
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1500, height: 950),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Anime Studio"
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("AnimeStudioMainWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        webView.loadHTMLString("<body style='background:#0f1115;color:#8b93a3;font:15px -apple-system;padding:40px'>Starting Anime Studio...</body>", baseURL: nil)
        startServer()
    }

    func restoreFolderAccess() {
        // Managed app-owned storage never resolves or requests access to the old Documents bookmark.
        if URL(fileURLWithPath: dataDir).standardizedFileURL.path.hasPrefix(configDirectory.standardizedFileURL.path + "/") { return }
        let bookmark = configDirectory.appendingPathComponent("project-folder.bookmark")
        guard let data = try? Data(contentsOf: bookmark) else { return }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: data, options: [.withSecurityScope], relativeTo: nil, bookmarkDataIsStale: &stale),
              url.standardizedFileURL.path == URL(fileURLWithPath: dataDir).standardizedFileURL.path else { return }
        if url.startAccessingSecurityScopedResource() { grantedFolder = url }
    }

    func startServer() {
        startupGeneration += 1
        let generation = startupGeneration
        startupFailed = false
        serverReady = false
        webView.loadHTMLString("<body style='font:15px -apple-system;padding:40px'>Starting Anime Studio…</body>", baseURL: nil)
        guard let py = env["AS_PYTHON"], let script = env["AS_SCRIPT"] else {
            startupFailure("The Python launcher configuration is missing. Open Anime Studio.app rather than the native executable.", accessDenied: false)
            return
        }
        let logURL = URL(fileURLWithPath: dataDir).appendingPathComponent("app.log")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = [script, "--host", "127.0.0.1", "--port", port, "--data", dataDir]
        do {
            try FileManager.default.createDirectory(atPath: dataDir, withIntermediateDirectories: true)
            try Data().write(to: logURL)
            let handle = try FileHandle(forWritingTo: logURL)
            p.standardOutput = handle
            p.standardError = handle
            p.terminationHandler = { [weak self] process in
                DispatchQueue.main.async {
                    guard let self = self, !self.quitting, generation == self.startupGeneration, !self.startupFailed else { return }
                    let log = (try? String(contentsOf: logURL, encoding: .utf8)) ?? ""
                    self.startupFailure(log.isEmpty ? "The local server exited (status \(process.terminationStatus))." : String(log.suffix(6000)),
                                        accessDenied: databaseAccessDenied(log))
                }
            }
            server = p
            try p.run()
            try? handle.close()
            waitAndLoad(tries: 0, generation: generation)
        } catch {
            startupFailure("Cannot start the local server: \(error.localizedDescription)",
                           accessDenied: (error as NSError).domain == NSCocoaErrorDomain && [NSFileReadNoPermissionError, NSFileWriteNoPermissionError].contains((error as NSError).code))
        }
    }

    func waitAndLoad(tries: Int, generation: Int) {
        guard generation == startupGeneration, !startupFailed, !quitting else { return }
        let ping = URL(string: "http://127.0.0.1:\(port)/api/ping")!
        URLSession.shared.dataTask(with: ping) { data, response, _ in
            DispatchQueue.main.async {
                guard generation == self.startupGeneration, !self.startupFailed, !self.quitting else { return }
                let json = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                if (response as? HTTPURLResponse)?.statusCode == 200, json?["app"] as? String == "anime-studio", self.server?.isRunning == true {
                    self.serverReady = true
                    self.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
                } else if tries < 120 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitAndLoad(tries: tries + 1, generation: generation) }
                } else {
                    self.startupFailure("The local server did not become ready. See \(self.dataDir)/app.log.", accessDenied: false)
                }
            }
        }.resume()
    }

    func startupFailure(_ details: String, accessDenied: Bool) {
        guard !startupFailed, !quitting else { return }
        startupFailed = true
        let heading = accessDenied ? "Allow access to your project folder" : "Anime Studio server did not start"
        let explanation = accessDenied ? "macOS blocked access to the project database. Choose the existing AnimeStudio Data folder to grant this app access. Your project will not be moved or replaced." : "The local server stopped. Check the error below or open the log."
        webView.loadHTMLString("<body style='font:15px -apple-system;padding:40px'><h2>\(heading)</h2><p>\(explanation)</p><p>Use Anime Studio → Choose Project Folder… to retry.</p><pre style='white-space:pre-wrap'>\(htmlEscaped(details))</pre></body>", baseURL: nil)
        let alert = NSAlert()
        alert.messageText = heading
        alert.informativeText = explanation + "\n\nProject: " + dataDir
        alert.addButton(withTitle: accessDenied ? "Choose Project Folder…" : "Open Log")
        alert.addButton(withTitle: "Close")
        alert.beginSheetModal(for: window) { response in
            if response == .alertFirstButtonReturn {
                if accessDenied { self.chooseProjectFolder() }
                else { NSWorkspace.shared.open(URL(fileURLWithPath: self.dataDir).appendingPathComponent("app.log")) }
            }
        }
    }

    @objc func chooseProjectFolderAction(_ sender: Any?) { chooseProjectFolder() }

    func chooseProjectFolder() {
        guard !folderPanelOpen else { return }
        let panel = NSOpenPanel()
        panel.title = "Allow Anime Studio to open your project"
        panel.message = "Select your existing AnimeStudio Data folder. It must contain studio.db. Your project will stay in place."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false
        panel.directoryURL = URL(fileURLWithPath: dataDir)
        panel.prompt = "Use Project Folder"
        folderPanelOpen = true
        panel.beginSheetModal(for: window) { response in
            self.folderPanelOpen = false
            guard response == .OK, let url = panel.url else { return }
            guard FileManager.default.fileExists(atPath: url.appendingPathComponent("studio.db").path) else {
                let alert = NSAlert()
                alert.messageText = "This is not an existing Anime Studio project"
                alert.informativeText = "Choose the folder that contains studio.db. No project files were changed."
                alert.beginSheetModal(for: self.window)
                return
            }
            // A folder switch may also be requested while the current server is healthy.
            self.startupGeneration += 1
            if self.server?.isRunning == true {
                self.server?.terminate()
                self.server?.waitUntilExit()
            }
            self.grantedFolder?.stopAccessingSecurityScopedResource()
            self.grantedFolder = nil
            if url.startAccessingSecurityScopedResource() { self.grantedFolder = url }
            self.selectedDataDir = url.path
            do {
                try FileManager.default.createDirectory(at: self.configDirectory, withIntermediateDirectories: true)
                try url.path.write(to: self.configDirectory.appendingPathComponent("data-folder"), atomically: true, encoding: .utf8)
                if let bookmark = try? url.bookmarkData(options: [.withSecurityScope], includingResourceValuesForKeys: nil, relativeTo: nil) {
                    try bookmark.write(to: self.configDirectory.appendingPathComponent("project-folder.bookmark"), options: .atomic)
                }
            } catch { NSLog("Anime Studio: could not remember folder: \(error)") }
            self.startServer()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        server?.terminate()
        grantedFolder?.stopAccessingSecurityScopedResource()
    }

    @objc func reload(_ sender: Any?) { webView.reload() }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Anime Studio", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(withTitle: "Choose Project Folder…", action: #selector(chooseProjectFolderAction(_:)), keyEquivalent: "o")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Anime Studio", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Anime Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let view = NSMenu(title: "View")
        let r = view.addItem(withTitle: "Reload", action: #selector(reload(_:)), keyEquivalent: "r")
        r.target = self
        view.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
            .keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = view

        let winItem = NSMenuItem()
        main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winItem.submenu = win
        NSApp.windowsMenu = win
        NSApp.mainMenu = main
    }

    // MARK: file pickers and JS dialogs
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    // MARK: downloads (shot list CSV, workflows, episode copies) -> ~/Downloads
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let r = navigationResponse.response as? HTTPURLResponse,
           let cd = r.value(forHTTPHeaderField: "Content-Disposition"), cd.lowercased().contains("attachment") {
            decisionHandler(.download)
            return
        }
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var dest = dir.appendingPathComponent(suggestedFilename)
        var i = 2
        while FileManager.default.fileExists(atPath: dest.path) {
            dest = dir.appendingPathComponent("\(i)-\(suggestedFilename)")
            i += 1
        }
        lastDownload = dest
        completionHandler(dest)
    }

    func downloadDidFinish(_ download: WKDownload) {
        if let u = lastDownload { NSWorkspace.shared.activateFileViewerSelecting([u]) }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
