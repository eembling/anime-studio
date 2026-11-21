#!/bin/bash
# Build "Anime Studio.app" from this repository (run on a Mac).
#   ./build_app.sh                 -> dist/Anime Studio.app
#   ./build_app.sh ~/Applications  -> ~/Applications/Anime Studio.app
# The native window is compiled now if the Xcode Command Line Tools are installed;
# otherwise the app compiles it in the background on first launch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="${1:-$ROOT/dist}"
APP="$DEST_DIR/Anime Studio.app"

mkdir -p "$DEST_DIR"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ROOT/mac/Info.plist" "$APP/Contents/"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$ROOT/mac/launcher.sh" "$APP/Contents/MacOS/AnimeStudio"
chmod 755 "$APP/Contents/MacOS/AnimeStudio"
cp "$ROOT/app/voice_video.py" "$ROOT/app/voice_video.js" "$ROOT/app/studio_audio.js" "$ROOT/app/clip_audio.py" "$ROOT/app/shot_previews.py" "$ROOT/app/export_runtime.py" "$ROOT/app/export_jobs.py" "$ROOT/app/audio_processing.py" "$ROOT/app/audio_mix.json" "$ROOT/app/mix_processor.js" "$ROOT/app/shot_playback.js" "$ROOT/app/timeline.py" "$ROOT/app/finishing.py" "$ROOT/app/finishing.js" "$ROOT/app/finishing.css" "$ROOT/app/anime_studio.py" "$ROOT/app/production.py" "$ROOT/app/creative.py" "$ROOT/app/creative.js" "$ROOT/app/creative.css" "$ROOT/app/settings.js" "$ROOT/app/settings.css" "$ROOT/app/continuations.py" "$ROOT/app/continuations.js" "$ROOT/app/workspace.js" "$ROOT/app/workspace.css" "$ROOT/app/workspace.py" "$ROOT/app/alignment.py" "$ROOT/app/alignment_worker.py" "$ROOT/app/job_snapshots.py" "$ROOT/app/storage_manager.py" "$ROOT/app/render_diagnostics.py" "$ROOT/mac/install_alignment.sh" "$ROOT/app/soundtrack.py" "$ROOT/app/render_queue.py" "$ROOT/app/project_storage.py" "$ROOT/app/studio_tools.py" "$ROOT/app/script_dialogue.py" "$ROOT/app/shot_voices.py" "$ROOT/app/shot_voices.js" "$ROOT/app/comfy_events.py" "$ROOT/app/editor.js" "$ROOT/app/production.js" "$ROOT/app/ui.html" "$ROOT/mac/main.swift" "$ROOT/mac/AppIcon.icns" "$APP/Contents/Resources/"

python3 - "$ROOT" "$APP/Contents/Resources/build_info.json" <<'PYINFO'
import datetime,json,pathlib,re,subprocess,sys
root=pathlib.Path(sys.argv[1])
def git(*args):
 try:return subprocess.check_output(['git',*args],cwd=root,stderr=subprocess.DEVNULL,text=True).strip()
 except Exception:return 'unknown'
version=re.search(r'^VERSION = "([^"]+)"',(root/'app/anime_studio.py').read_text(),re.M).group(1)
pathlib.Path(sys.argv[2]).write_text(json.dumps({'version':version,'commit':git('rev-parse','--short','HEAD'),'branch':git('branch','--show-current'),'dirty':bool(git('status','--porcelain')),'built':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
PYINFO

if [ "$(uname)" = "Darwin" ] && xcode-select -p >/dev/null 2>&1; then
  echo "Compiling the native window..."
  xcrun swiftc -O -target "$(uname -m)-apple-macos12.0" "$ROOT/mac/main.swift" \
    -o "$APP/Contents/MacOS/AnimeStudioWindow" -framework Cocoa -framework WebKit
  touch "$APP/Contents/MacOS/AnimeStudioWindow"
fi
touch "$APP"
echo "Built: $APP"
echo "Project settings and data load automatically from ~/Library/Application Support/AnimeStudio."
