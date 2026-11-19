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
cp "$ROOT/app/anime_studio.py" "$ROOT/app/production.py" "$ROOT/app/production.js" "$ROOT/app/ui.html" "$ROOT/mac/main.swift" "$ROOT/mac/AppIcon.icns" "$APP/Contents/Resources/"

if [ "$(uname)" = "Darwin" ] && xcode-select -p >/dev/null 2>&1; then
  echo "Compiling the native window..."
  xcrun swiftc -O -target "$(uname -m)-apple-macos12.0" "$ROOT/mac/main.swift" \
    -o "$APP/Contents/MacOS/AnimeStudioWindow" -framework Cocoa -framework WebKit
  touch "$APP/Contents/MacOS/AnimeStudioWindow"
fi
touch "$APP"
echo "Built: $APP"
echo "Project data is kept next to the app, in '$(basename "$DEST_DIR")/AnimeStudio Data'."
