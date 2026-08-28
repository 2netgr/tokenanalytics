// TokenAnalytics — native macOS shell.
//
// A tiny WKWebView app that boots the bundled backend (Python/FastAPI) and
// frontend (Node/Next standalone) on auto-picked free loopback ports, shows a
// splash while they warm up, then loads the dashboard in a clean native window.
// Quitting (or closing the window) tears both child processes down.
//
// Everything it needs lives inside the .app bundle under Contents/Resources:
//   runtime/python/bin/python3   self-contained CPython + backend deps
//   runtime/node                 self-contained node binary
//   app/backend/                 FastAPI source (main.py + modules)
//   app/frontend/                Next.js `output: standalone` (server.js + deps)

import Cocoa
import WebKit

// MARK: - Bundle resource paths

let resourceURL = Bundle.main.resourceURL!
let runtimeURL  = resourceURL.appendingPathComponent("runtime")
let appURL      = resourceURL.appendingPathComponent("app")
let pythonExec  = runtimeURL.appendingPathComponent("python/bin/python3")
let nodeExec    = runtimeURL.appendingPathComponent("node")
let backendDir  = appURL.appendingPathComponent("backend")
let frontendDir = appURL.appendingPathComponent("frontend")

// Bundle version (CFBundleShortVersionString), passed to the backend so its
// update check compares THIS build against the latest GitHub release.
let appVersion = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? ""

let logURL = FileManager.default
    .homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/TokenAnalytics", isDirectory: true)

// PIDs of the child processes from the most recent run. Read at the next launch
// to reap anything a non-graceful exit (Force Quit / crash) left orphaned.
// Keyed by the bundle's location so two copies of the app (e.g. /Applications
// and a mounted .dmg) never read — and reap — each other's children.
let pidFileURL = logURL.appendingPathComponent("running-\(bundleKey(resourceURL.path)).pids")

func bundleKey(_ path: String) -> String {
    var h: UInt64 = 14695981039346656037
    for b in path.utf8 { h = (h ^ UInt64(b)) &* 1099511628211 }
    return String(h, radix: 16)
}

// MARK: - Helpers

/// Ask the kernel for a currently-free loopback TCP port (bind to :0, read back
/// the assignment, release it). There is a tiny race between releasing and the
/// child re-binding, but on loopback that window is effectively never lost.
func freePort() -> UInt16 {
    // Retry the bind-to-:0 trick until the kernel hands back a real port. Every
    // step is checked (socket fd, bind, getsockname) so a transient failure can
    // never silently return 0 — passing `--port 0` to a child would make it bind
    // a random port we don't know, wedging the readiness poll for the full
    // timeout. On the (near-impossible) total failure we fall back to a fixed
    // high port rather than emit 0.
    for _ in 0..<25 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        if fd < 0 { continue }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        addr.sin_port = 0
        let bindOK = (withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }) == 0
        var port: UInt16 = 0
        if bindOK {
            var bound = sockaddr_in()
            var len = socklen_t(MemoryLayout<sockaddr_in>.size)
            let nameOK = (withUnsafeMutablePointer(to: &bound) { p in
                p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    getsockname(fd, $0, &len)
                }
            }) == 0
            if nameOK { port = UInt16(bigEndian: bound.sin_port) }
        }
        close(fd)
        if port != 0 { return port }
    }
    return 8765
}

/// Two distinct free ports (frontend + backend).
func twoFreePorts() -> (front: UInt16, api: UInt16) {
    let a = freePort()
    var b = freePort()
    var guardCount = 0
    while b == a && guardCount < 20 { b = freePort(); guardCount += 1 }
    return (a, b)
}

func launch(_ exec: URL, _ args: [String], cwd: URL, extraEnv: [String: String], logName: String) -> Process? {
    let p = Process()
    p.executableURL = exec
    p.arguments = args
    p.currentDirectoryURL = cwd
    var env = ProcessInfo.processInfo.environment
    // Strip any inherited Python/venv pollution so the bundled interpreter
    // always resolves its own stdlib + site-packages deterministically.
    for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
                "VIRTUAL_ENV", "CONDA_PREFIX"] {
        env.removeValue(forKey: key)
    }
    for (k, v) in extraEnv { env[k] = v }
    p.environment = env
    try? FileManager.default.createDirectory(at: logURL, withIntermediateDirectories: true)
    let logFile = logURL.appendingPathComponent(logName)
    FileManager.default.createFile(atPath: logFile.path, contents: nil)
    if let handle = try? FileHandle(forWritingTo: logFile) {
        p.standardOutput = handle
        p.standardError = handle
    }
    do { try p.run() } catch { return nil }
    return p
}

/// Poll until the server answers (any non-5xx), or give up after `timeout`.
func waitForServer(_ url: URL, timeout: TimeInterval) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        var req = URLRequest(url: url)
        req.timeoutInterval = 2
        req.cachePolicy = .reloadIgnoringLocalCacheData
        let task = URLSession.shared.dataTask(with: req) { _, resp, _ in
            if let h = resp as? HTTPURLResponse, h.statusCode < 500 { ok = true }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + 3)
        if ok { return true }
        Thread.sleep(forTimeInterval: 0.4)
    }
    return false
}

// MARK: - Splash / error screens (rendered in the same WKWebView)

func splashHTML(_ subtitle: String) -> String {
    return """
    <!doctype html><html><head><meta charset="utf-8"><style>
      :root { color-scheme: dark; }
      html,body{height:100%;margin:0}
      body{background:#0a0b0d;color:#e7e9ee;font:400 14px/1.5 -apple-system,SF Pro Text,Segoe UI,sans-serif;
           display:flex;align-items:center;justify-content:center;flex-direction:column;gap:22px;-webkit-user-select:none}
      .mark{font:600 22px/1 -apple-system,sans-serif;letter-spacing:-.02em;color:#fff}
      .mark span{color:#5b8def}
      .sub{color:#8b909b;font-size:13px;min-height:18px}
      .ring{width:34px;height:34px;border-radius:50%;border:3px solid #20242c;border-top-color:#5b8def;
            animation:spin 0.9s linear infinite}
      @keyframes spin{to{transform:rotate(360deg)}}
    </style></head><body>
      <div class="ring"></div>
      <div class="mark">Token<span>Analytics</span></div>
      <div class="sub">\(subtitle)</div>
    </body></html>
    """
}

let errorHTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  :root{color-scheme:dark}html,body{height:100%;margin:0}
  body{background:#0a0b0d;color:#e7e9ee;font:400 14px/1.6 -apple-system,sans-serif;
       display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px;text-align:center;padding:40px}
  .mark{font:600 20px/1 -apple-system,sans-serif}.mark span{color:#5b8def}
  .msg{color:#8b909b;max-width:420px}
  code{background:#16181d;padding:2px 6px;border-radius:5px;color:#cfd3da}
</style></head><body>
  <div class="mark">Token<span>Analytics</span></div>
  <div class="msg">The dashboard didn’t start in time. Logs are in
  <code>~/Library/Logs/TokenAnalytics</code>. Quit and reopen to try again.</div>
</body></html>
"""

// MARK: - App delegate

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var backend: Process?
    var frontend: Process?
    var didStop = false
    var signalSources: [DispatchSourceSignal] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        installSignalHandlers()

        let frame = NSRect(x: 0, y: 0, width: 1280, height: 860)
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "TokenAnalytics"
        window.minSize = NSSize(width: 880, height: 600)
        window.center()
        window.setFrameAutosaveName("TokenAnalyticsMainWindow")

        let cfg = WKWebViewConfiguration()
        webView = WKWebView(frame: frame, configuration: cfg)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.autoresizingMask = [.width, .height]
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        webView.loadHTMLString(splashHTML("Starting up…"), baseURL: nil)
        DispatchQueue.global(qos: .userInitiated).async { self.boot() }
    }

    func boot() {
        // Reap any python/node left over from a previous instance of THIS bundle
        // that didn't exit cleanly (e.g. a Force Quit), so leaks can't pile up
        // across relaunches. Matches only executables under our own runtime path.
        killStragglers()

        let ports = twoFreePorts()
        let front = ports.front
        let api = ports.api

        backend = launch(pythonExec,
                         ["main.py", "--port", "\(api)", "--host", "127.0.0.1"],
                         cwd: backendDir,
                         // PYTHONDONTWRITEBYTECODE: never rename a fresh .pyc into the
                         // signed .app bundle — that write hangs under launchd (the OS
                         // synchronously inspects writes inside a launched bundle). The
                         // build precompiles hash-based caches, so startup stays fast.
                         // TT_PACKAGED + TT_APP_VERSION: this is a bundled .app, not
                         // a git checkout, so the backend compares our bundle version
                         // against the latest GitHub release and shows a "download the
                         // new version" banner (it can't git-pull in place). This is
                         // the app's only outbound network call — a version check that
                         // sends no logs, sessions, or usage data. Users can turn it
                         // off in Settings → update check.
                         extraEnv: ["TT_API_PORT": "\(api)", "TT_HOST": "127.0.0.1",
                                    "PYTHONDONTWRITEBYTECODE": "1",
                                    "TT_PACKAGED": "1", "TT_APP_VERSION": appVersion],
                         logName: "backend.log")

        frontend = launch(nodeExec,
                          ["server.js"],
                          cwd: frontendDir,
                          extraEnv: ["PORT": "\(front)",
                                     "HOSTNAME": "127.0.0.1",
                                     "NEXT_PUBLIC_API_PORT": "\(api)"],
                          logName: "frontend.log")

        // Record both PIDs so the next launch can reap them if this one is
        // Force-Quit / crashes (the node frontend renames itself, so a path
        // pattern alone can't find it).
        writePidFile()

        // Poll the bare frontend for readiness; load it with `?apiport=<api>` so
        // the client learns the live backend port at runtime (NEXT_PUBLIC_API_PORT
        // is frozen at build time and would otherwise point at a wrong/fixed port).
        let pollURL = URL(string: "http://localhost:\(front)/")!
        let loadURL = URL(string: "http://localhost:\(front)/?apiport=\(api)")!
        let ready = waitForServer(pollURL, timeout: 90)
        DispatchQueue.main.async {
            if ready {
                self.webView.load(URLRequest(url: loadURL))
            } else {
                self.webView.loadHTMLString(errorHTML, baseURL: nil)
            }
        }
    }

    func buildMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About TokenAnalytics", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Hide TokenAnalytics", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit TokenAnalytics", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        // An Edit menu so copy/paste/select-all work inside the dashboard.
        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    func installSignalHandlers() {
        // applicationWillTerminate fires on a graceful quit but NOT on a bare
        // SIGTERM/SIGINT/SIGHUP (kill, logout, a parent killing us). Catch those
        // and reap the children. (SIGKILL can't be caught — killStragglers() on
        // the next launch covers a Force Quit.)
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in
                self?.stopChildren()
                exit(0)
            }
            src.resume()
            signalSources.append(src)
        }
    }

    func killStragglers() {
        // 1) Reap the python backend by path. Match the exact interpreter path THIS
        //    bundle spawns with: a previous run of the same bundle used the same
        //    string, so ps shows it verbatim. A generic "TokenAnalytics.app/…"
        //    marker would also kill the backend of another copy of the app.
        runPkill(pythonExec.path)
        // 2) The Next.js frontend renames its process to "next-server (vX)", so a
        //    path pattern can't find it. Use the PIDs recorded by the previous
        //    run, killing each only if it's still alive AND still looks like one
        //    of ours (guards against the PID having been reused by something else).
        if let text = try? String(contentsOf: pidFileURL, encoding: .utf8) {
            for line in text.split(separator: "\n") {
                guard let pid = Int32(line.trimmingCharacters(in: .whitespaces)), pid > 1 else { continue }
                if kill(pid, 0) != 0 { continue }                 // not alive
                if processLooksLikeOurs(pid) { kill(pid, SIGKILL) }
            }
        }
    }

    func runPkill(_ pattern: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        p.arguments = ["-f", pattern]
        try? p.run()
        p.waitUntilExit()
    }

    func processLooksLikeOurs(_ pid: Int32) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-p", "\(pid)", "-o", "command="]
        let pipe = Pipe()
        p.standardOutput = pipe
        try? p.run()
        p.waitUntilExit()
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        return out.contains("next-server")
            || out.contains(pythonExec.path)
            || out.contains(nodeExec.path)
    }

    func writePidFile() {
        let pids = [backend?.processIdentifier, frontend?.processIdentifier].compactMap { $0 }
        let text = pids.map { "\($0)" }.joined(separator: "\n")
        try? FileManager.default.createDirectory(at: logURL, withIntermediateDirectories: true)
        try? text.write(to: pidFileURL, atomically: true, encoding: .utf8)
    }

    // WKUIDelegate — target="_blank" / window.open links (GitHub, feedback,
    // "What's changed", …) have no tab to open into in a single-window shell, so
    // WebKit would silently drop them. Route them to the user's real browser.
    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // WKNavigationDelegate — keep the shell pinned to the local dashboard. Any
    // top-level navigation that isn't loopback opens in the system browser, so
    // the window can never wander off into a chromeless generic browser.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.allow); return }
        let host = url.host ?? ""
        let isLocal = host.isEmpty || host == "localhost" || host == "127.0.0.1" || host == "::1"
        let localScheme = ["about", "data", "blob", "file"].contains(url.scheme ?? "")
        if isLocal || localScheme {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) { stopChildren() }

    func stopChildren() {
        if didStop { return }
        didStop = true
        backend?.terminate()
        frontend?.terminate()
    }
}

// MARK: - Entry point

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
