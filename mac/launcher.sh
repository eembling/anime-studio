#!/bin/bash
# Anime Studio launcher (Contents/MacOS/AnimeStudio)
HERE="$(cd "$(dirname "$0")" && pwd)"
RES="$(cd "$HERE/../Resources" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
APPDIR="$(cd "$HERE/../../.." && pwd)"
export AS_DATA="${ANIME_STUDIO_DATA:-$APPDIR/AnimeStudio Data}"
export AS_PORT="${ANIME_STUDIO_PORT:-8700}"
export AS_SCRIPT="$RES/anime_studio.py"
mkdir -p "$AS_DATA"
LOG="$AS_DATA/launcher.log"
exec >>"$LOG" 2>&1
echo "===== $(date) launching from $HERE"

alert() { ( osascript -e "display alert \"Anime Studio\" message \"$1\"" >/dev/null 2>&1 & ) ; }

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

# Native window: if it has been built, use it. Otherwise build it in the BACKGROUND (first launch)
# and open the app in a browser-style window right now, so you never wait on the build.
BIN="$HERE/AnimeStudioWindow"
if [ -x "$BIN" ] && [ ! "$RES/main.swift" -nt "$BIN" ]; then
  # A server left over from a browser-window launch is stopped so the app window owns its own server
  if curl --noproxy "*" -s -m 1 "http://127.0.0.1:$AS_PORT/api/ping" >/dev/null 2>&1; then
    echo "stopping previous background server"
    curl --noproxy "*" -s -m 2 -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$AS_PORT/api/quit" >/dev/null 2>&1
    for i in $(seq 1 20); do curl --noproxy "*" -s -m 1 "http://127.0.0.1:$AS_PORT/api/ping" >/dev/null 2>&1 || break; sleep 0.25; done
  fi
  # Anything still holding the port (an older copy of the server) is stopped too
  OLD="$(lsof -ti tcp:"$AS_PORT" -sTCP:LISTEN 2>/dev/null)"
  if [ -n "$OLD" ]; then echo "killing old server on port $AS_PORT: $OLD"; kill $OLD 2>/dev/null; sleep 0.5; fi
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
  nohup "$PY" "$AS_SCRIPT" --host 127.0.0.1 --port "$AS_PORT" --data "$AS_DATA" >> "$AS_DATA/app.log" 2>&1 &
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
