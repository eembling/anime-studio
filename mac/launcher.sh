#!/bin/bash
# Anime Studio launcher (Contents/MacOS/AnimeStudio)
HERE="$(cd "$(dirname "$0")" && pwd)"
RES="$(cd "$HERE/../Resources" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
APPDIR="$(cd "$HERE/../../.." && pwd)"
# App-owned settings/logs live in Application Support, not a protected Documents folder.
CONF_DIR="$HOME/Library/Application Support/AnimeStudio"
mkdir -p "$CONF_DIR"
LOG="$CONF_DIR/launcher.log"
exec >>"$LOG" 2>&1
echo "===== $(date) launching from $HERE"
export AS_PORT="${ANIME_STUDIO_PORT:-8700}"
export AS_SCRIPT="$RES/anime_studio.py"
alert() { ( osascript - "$1" <<'APPLESCRIPT'
on run argv
  display alert "Anime Studio" message (item 1 of argv)
end run
APPLESCRIPT
) >/dev/null 2>&1 & }

HAVE_CLT=0
xcode-select -p >/dev/null 2>&1 && HAVE_CLT=1
echo "command line tools: $HAVE_CLT"

# Python 3.9+ (skip Apple's /usr/bin/python3 stub when the Command Line Tools are missing - it only shows an install popup)
PY=""
for c in /opt/homebrew/bin/python3 "$HOME/.homebrew/bin/python3" /usr/local/bin/python3 /usr/bin/python3; do
  [ "$c" = /usr/bin/python3 ] && [ "$HAVE_CLT" = 0 ] && continue
  if [ -x "$c" ] && "$c" -c 'import sys,sqlite3; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
echo "python: ${PY:-none}"
if [ -z "$PY" ]; then
  alert "Anime Studio needs Python 3. Open Terminal and run:  brew install python   then open Anime Studio again."
  exit 1
fi
export AS_PYTHON="$PY"
command -v ffmpeg >/dev/null || echo "WARNING: ffmpeg not found - brew install ffmpeg"

# Resolve the remembered app-owned project, or safely copy a legacy project once.
# The original stays untouched; the helper changes the preference only after validation.
STORAGE_ARGS=(--config-dir "$CONF_DIR" --app-dir "$APPDIR")
if [ -n "${ANIME_STUDIO_DATA:-}" ]; then STORAGE_ARGS+=(--override "$ANIME_STUDIO_DATA"); fi
if ! AS_DATA="$("$PY" "$RES/project_storage.py" "${STORAGE_ARGS[@]}")"; then
  alert "Could not load your saved project. See $LOG. Your original project was not replaced."
  exit 1
fi
export AS_DATA
export ANIME_STUDIO_CONFIG="$CONF_DIR"
if ! AS_PORT="$("$PY" "$RES/project_storage.py" --config-dir "$CONF_DIR" --override "$AS_DATA" --controller-port "$AS_PORT")"; then
  alert "Could not select a local port for this project."
  exit 1
fi
export AS_PORT
echo "project data: $AS_DATA"
BIN="$HERE/AnimeStudioWindow"
if [ -x "$BIN" ] && [ ! "$RES/main.swift" -nt "$BIN" ]; then
  echo "starting native window"
  exec "$BIN"
fi
if [ "$HAVE_CLT" = 1 ] && ! pgrep -f "swiftc.*AnimeStudioWindow" >/dev/null 2>&1; then
  echo "building native window in the background (see build.log)"
  ( xcrun swiftc -O -target "$(uname -m)-apple-macos12.0" "$RES/main.swift" -o "$BIN.tmp" -framework Cocoa -framework WebKit \
      > "$AS_DATA/build.log" 2>&1 && mv -f "$BIN.tmp" "$BIN" && echo "build OK $(date)" >> "$AS_DATA/build.log" ) &
  disown || true
fi

# Fallback: server in the background + a browser window
if ! curl --noproxy "*" -s -m 1 "http://127.0.0.1:$AS_PORT/api/ping" >/dev/null 2>&1; then
  echo "starting server"
  nohup "$PY" "$AS_SCRIPT" --projects --host 127.0.0.1 --port "$AS_PORT" --data "$AS_DATA" >> "$AS_DATA/app.log" 2>&1 &
  for i in $(seq 1 60); do curl --noproxy "*" -s -m 1 "http://127.0.0.1:$AS_PORT/api/ping" >/dev/null 2>&1 && break; sleep 0.5; done
fi
if ! curl --noproxy "*" -s -m 1 "http://127.0.0.1:$AS_PORT/api/ping" >/dev/null 2>&1; then
  echo "server did not start"; tail -20 "$AS_DATA/app.log"
  alert "The Anime Studio server did not start. Details: $AS_DATA/app.log"
  exit 1
fi
URL="http://127.0.0.1:$AS_PORT/"
for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
  if [ -d "/Applications/$b.app" ]; then
    echo "opening $b app window"
    open -na "$b" --args --app="$URL" --user-data-dir="$AS_DATA/.window-profile"
    exit 0
  fi
done
echo "opening default browser"
open "$URL"
