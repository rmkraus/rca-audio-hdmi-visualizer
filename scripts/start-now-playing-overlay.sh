#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-/etc/rca-hdmi-visualizer.env}
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

VISUALIZER_USER=${VISUALIZER_USER:-pi}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}
OVERLAY_WINDOW_MATCH=${OVERLAY_WINDOW_MATCH:-Now Playing}

uid=$(id -u "$VISUALIZER_USER")
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
export DISPLAY=${DISPLAY:-:0}

run_as_user() {
  runuser -u "$VISUALIZER_USER" -- env \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
    DISPLAY="$DISPLAY" \
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

keep_on_top() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT))
  local window_id=""
  while (( SECONDS < deadline )); do
    window_id=$(run_as_user wmctrl -lx | awk -v pat="${OVERLAY_WINDOW_MATCH,,}" 'tolower($0) ~ pat { print $1; exit }')
    if [[ -n "$window_id" ]]; then
      run_as_user wmctrl -ir "$window_id" -b add,above,fullscreen || true
      run_as_user xdotool windowactivate "$window_id" || true
      return 0
    fi
    sleep 1
  done
  echo "Overlay started, but no window matching '$OVERLAY_WINDOW_MATCH' was found" >&2
}

wait_for_session
run_as_user /usr/local/bin/rca-now-playing-overlay &
overlay_pid=$!
keep_on_top &
wait "$overlay_pid"
