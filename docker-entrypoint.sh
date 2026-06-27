#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"
export VNC_PORT="${VNC_PORT:-5900}"
export VNC_RESOLUTION="${VNC_RESOLUTION:-1280x900x24}"

Xvfb "$DISPLAY" -screen 0 "$VNC_RESOLUTION" -ac +extension GLX +render -noreset &
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport "$VNC_PORT" >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ "$NOVNC_PORT" "localhost:$VNC_PORT" >/tmp/novnc.log 2>&1 &

exec python -m uvicorn app_node.main:app --host 0.0.0.0 --port 8000
