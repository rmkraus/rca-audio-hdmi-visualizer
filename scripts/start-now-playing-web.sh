#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-/etc/rca-hdmi-visualizer.env}
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

VISUALIZER_USER=${VISUALIZER_USER:-pi}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}
NOW_PLAYING_WEB_URL=${NOW_PLAYING_WEB_URL:-http://127.0.0.1:8765/}

uid=$(id -u "$VISUALIZER_USER")
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
export DISPLAY=${DISPLAY:-:0}
if [[ -z "${XAUTHORITY:-}" ]]; then
  if [[ -f "$XDG_RUNTIME_DIR/gdm/Xauthority" ]]; then
    export XAUTHORITY="$XDG_RUNTIME_DIR/gdm/Xauthority"
  elif [[ -f "/home/$VISUALIZER_USER/.Xauthority" ]]; then
    export XAUTHORITY="/home/$VISUALIZER_USER/.Xauthority"
  fi
fi

run_as_user() {
  runuser -u "$VISUALIZER_USER" -- env \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
    DISPLAY="$DISPLAY" \
    XAUTHORITY="${XAUTHORITY:-}" \
    "$@"
}

wait_for_session() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT))
  while (( SECONDS < deadline )); do
    if [[ -S "$XDG_RUNTIME_DIR/bus" ]] && run_as_user xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for graphical session for $VISUALIZER_USER on $DISPLAY" >&2
  return 1
}

find_browser() {
  command -v chromium-browser || command -v chromium || command -v google-chrome || command -v x-www-browser || command -v firefox
}

fullscreen_browser_window() {
  local deadline=$((SECONDS + 20))
  while (( SECONDS < deadline )); do
    if command -v wmctrl >/dev/null 2>&1; then
      # Unity can leave kiosk windows below the top panel/launcher after boot.
      # Force the Chromium window to the real screen bounds and fullscreen state.
      if run_as_user wmctrl -r "Now Playing" -b add,fullscreen >/dev/null 2>&1; then
        run_as_user wmctrl -r "Now Playing" -e 0,0,0,1920,1080 >/dev/null 2>&1 || true
        return 0
      fi
    fi
    if command -v xdotool >/dev/null 2>&1; then
      local window_id
      window_id=$(run_as_user xdotool search --onlyvisible --class chromium 2>/dev/null | head -n 1 || true)
      if [[ -n "$window_id" ]]; then
        run_as_user xdotool windowactivate "$window_id" >/dev/null 2>&1 || true
        run_as_user xdotool windowsize "$window_id" 100% 100% >/dev/null 2>&1 || true
        run_as_user xdotool windowmove "$window_id" 0 0 >/dev/null 2>&1 || true
        return 0
      fi
    fi
    sleep 1
  done
  return 0
}

wait_for_session
browser=$(find_browser)
run_as_user "$browser" \
  --kiosk \
  --start-fullscreen \
  --window-position=0,0 \
  --window-size=1920,1080 \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --autoplay-policy=no-user-gesture-required \
  "$NOW_PLAYING_WEB_URL" &
browser_pid=$!
fullscreen_browser_window
wait "$browser_pid"
