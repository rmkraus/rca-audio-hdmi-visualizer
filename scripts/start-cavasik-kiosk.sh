#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-/etc/rca-hdmi-visualizer.env}
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

VISUALIZER_USER=${VISUALIZER_USER:-pi}
CAVASIK_APP_ID=${CAVASIK_APP_ID:-io.github.TheWisker.Cavasik}
# Optional full command override, e.g. VISUALIZER_COMMAND="xterm -fullscreen -e cava".
# When empty, the launcher runs Cavasik via Flatpak.
VISUALIZER_COMMAND=${VISUALIZER_COMMAND:-}
WINDOW_MATCH=${WINDOW_MATCH:-Cavasik}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}

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

fullscreen_window() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT))
  local window_id=""

  while (( SECONDS < deadline )); do
    window_id=$(run_as_user wmctrl -lx | awk -v pat="${WINDOW_MATCH,,}" 'tolower($0) ~ pat { print $1; exit }')
    if [[ -n "$window_id" ]]; then
      run_as_user wmctrl -ir "$window_id" -b add,fullscreen || true
      run_as_user xdotool windowactivate "$window_id" || true
      run_as_user xdotool key F11 || true
      return 0
    fi
    sleep 1
  done

  echo "Cavasik started, but no window matching '$WINDOW_MATCH' was found to fullscreen" >&2
  return 0
}

wait_for_session

if [[ -n "$VISUALIZER_COMMAND" ]]; then
  run_as_user bash -lc "$VISUALIZER_COMMAND" &
else
  run_as_user flatpak run "$CAVASIK_APP_ID" &
fi
app_pid=$!

fullscreen_window &
fullscreen_pid=$!

wait "$fullscreen_pid" || true
wait "$app_pid"
