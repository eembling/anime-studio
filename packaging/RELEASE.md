# Anime Studio desktop downloads

Windows x64: run the Setup EXE to install for your current user. Python and FFmpeg/FFprobe are bundled. Windows 11 with Microsoft Edge WebView2 Runtime and .NET Framework 4.8 is required; install WebView2 from https://developer.microsoft.com/microsoft-edge/webview2/ if absent. User projects live under `%LOCALAPPDATA%\AnimeStudio` and uninstall preserves them. Automatic speech-alignment setup and temporary macOS voices are not available on Windows; manual dialogue timing, generated character voices through ComfyUI, and uploaded audio remain available.

macOS Apple silicon: extract the ZIP and move Anime Studio.app to Applications. Install Python 3.9+ and FFmpeg/FFprobe first, for example `brew install python ffmpeg`. These dependencies are not bundled in the macOS ZIP. The native Swift/WebKit window is precompiled for arm64; this is not a universal binary. Existing projects remain in Application Support.

Both downloads are unsigned (the macOS executable may have an ad-hoc architecture signature; it is not Developer ID signed or notarized). macOS Gatekeeper and Windows SmartScreen may require an explicit user decision. Follow your organization's installation policy; no security controls are changed by the installer.

ComfyUI and its generation models remain separate services. No models or personal project data are included. GPU generation quality is manual acceptance; CI verifies native startup, packaged resources, video/audio tooling and isolated mock workflows.
