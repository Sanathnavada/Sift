#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
export NOVNC_PORT="${NOVNC_PORT:-6080}"
export VNC_PORT="${VNC_PORT:-5900}"
export VNC_RESOLUTION="${VNC_RESOLUTION:-1920x1080x24}"
export NOVNC_WEB_DIR="${NOVNC_WEB_DIR:-/tmp/novnc-clean-web}"
export INSTAGRAM_BROWSER_WIDTH="${INSTAGRAM_BROWSER_WIDTH:-1920}"
export INSTAGRAM_BROWSER_HEIGHT="${INSTAGRAM_BROWSER_HEIGHT:-1080}"

_display_number="${DISPLAY#:}"
_home="${HOME:-/root}"

install_window_tools_if_possible() {
  if command -v xdotool >/dev/null 2>&1 && command -v wmctrl >/dev/null 2>&1; then
    return 0
  fi
  if [ "$(id -u)" != "0" ] || ! command -v apt-get >/dev/null 2>&1; then
    echo "Window tools xdotool/wmctrl are not installed; continuing with browser flags only."
    return 0
  fi
  echo "Installing lightweight X window tools for clean browser sizing..."
  (
    apt-get update &&
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xdotool wmctrl &&
    rm -rf /var/lib/apt/lists/*
  ) >/tmp/window-tools-install.log 2>&1 || {
    echo "Could not install xdotool/wmctrl; continuing without them. See /tmp/window-tools-install.log"
  }
}

install_window_tools_if_possible

mkdir -p /tmp/.X11-unix "$_home/.fluxbox" "$NOVNC_WEB_DIR"
rm -f "/tmp/.X${_display_number}-lock" "/tmp/.X11-unix/X${_display_number}"
rm -rf "$NOVNC_WEB_DIR"
mkdir -p "$NOVNC_WEB_DIR"
cp -a /usr/share/novnc/. "$NOVNC_WEB_DIR/"
if [ -f /app/novnc/vnc_clean.html ]; then
  cp /app/novnc/vnc_clean.html "$NOVNC_WEB_DIR/vnc_clean.html"
fi

cat > "$_home/.fluxbox/init" <<'FLUXBOX_INIT'
session.screen0.toolbar.visible: false
session.screen0.toolbar.autoHide: true
session.screen0.fullMaximization: true
session.screen0.maxOverToolbar: true
session.screen0.slit.autoHide: true
session.screen0.tab.placement: TopLeft
session.screen0.window.focus.alpha: 255
session.screen0.window.unfocus.alpha: 255
session.screen0.workspaceNames: Browser
session.screen0.strftimeFormat:
session.titlebar.left:
session.titlebar.right:
FLUXBOX_INIT

cat > "$_home/.fluxbox/apps" <<FLUXBOX_APPS
[app] (class=.*[Cc]hrome.*)
  [Position] (UPPERLEFT) {0 0}
  [Dimensions] {${INSTAGRAM_BROWSER_WIDTH} ${INSTAGRAM_BROWSER_HEIGHT}}
  [Deco] {NONE}
  [Maximized] {yes}
[end]
[app] (class=.*[Cc]hromium.*)
  [Position] (UPPERLEFT) {0 0}
  [Dimensions] {${INSTAGRAM_BROWSER_WIDTH} ${INSTAGRAM_BROWSER_HEIGHT}}
  [Deco] {NONE}
  [Maximized] {yes}
[end]
[app] (title=.*Instagram.*)
  [Position] (UPPERLEFT) {0 0}
  [Dimensions] {${INSTAGRAM_BROWSER_WIDTH} ${INSTAGRAM_BROWSER_HEIGHT}}
  [Deco] {NONE}
  [Maximized] {yes}
[end]
FLUXBOX_APPS

Xvfb "$DISPLAY" -screen 0 "$VNC_RESOLUTION" -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
sleep 0.5

if ! kill -0 "$!" >/dev/null 2>&1; then
  echo "Xvfb failed to start on DISPLAY=$DISPLAY" >&2
  cat /tmp/xvfb.log >&2 || true
  exit 1
fi

fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc \
  -display "$DISPLAY" \
  -forever \
  -shared \
  -nopw \
  -noxdamage \
  -xkb \
  -rfbport "$VNC_PORT" \
  >/tmp/x11vnc.log 2>&1 &
websockify \
  --web="$NOVNC_WEB_DIR" \
  "$NOVNC_PORT" \
  "localhost:$VNC_PORT" \
  >/tmp/novnc.log 2>&1 &

echo "Virtual browser stack started:"
echo "  DISPLAY=$DISPLAY"
echo "  noVNC=http://localhost:${NOVNC_PORT}/vnc_clean.html?autoconnect=true&resize=scale&quality=9&compression=0"
echo "  VNC_RESOLUTION=$VNC_RESOLUTION"
echo "  Browser target=${INSTAGRAM_BROWSER_WIDTH}x${INSTAGRAM_BROWSER_HEIGHT}"

exec python -m uvicorn app_node.main:app --host 0.0.0.0 --port 8000
