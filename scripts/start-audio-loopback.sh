#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-/etc/rca-hdmi-visualizer.env}
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

VISUALIZER_USER=${VISUALIZER_USER:-pi}
SOURCE_MATCH=${SOURCE_MATCH:-usb}
SINK_MATCH=${SINK_MATCH:-hdmi}
LOOPBACK_LATENCY_MSEC=${LOOPBACK_LATENCY_MSEC:-25}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}

uid=$(id -u "$VISUALIZER_USER")
export XDG_RUNTIME_DIR="/run/user/$uid"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

pactl_user() {
  runuser -u "$VISUALIZER_USER" -- env \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
    pactl "$@"
}

wait_for_audio() {
  local deadline=$((SECONDS + STARTUP_TIMEOUT))
  while (( SECONDS < deadline )); do
    if [[ -S "$XDG_RUNTIME_DIR/bus" ]] && pactl_user info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for user audio server for $VISUALIZER_USER" >&2
  return 1
}

pick_device() {
  local kind=$1
  local match=$2
  local default_name="" list_cmd short_cmd

  if [[ "$kind" == "source" ]]; then
    list_cmd=(list sources)
    short_cmd=(list short sources)
    default_name=$(pactl_user get-default-source 2>/dev/null || true)
  else
    list_cmd=(list sinks)
    short_cmd=(list short sinks)
    default_name=$(pactl_user get-default-sink 2>/dev/null || true)
  fi

  if [[ -n "$match" ]]; then
    local matched_name
    matched_name=$(pactl_user "${list_cmd[@]}" | awk -v pat="${match,,}" '
      /^Source #|^Sink #/ { name=""; desc="" }
      /^[[:space:]]*Name:/ { name=$2 }
      /^[[:space:]]*Description:/ {
        desc=substr($0, index($0, $2));
        hay=tolower(name " " desc);
        if (hay ~ pat && name != "") { print name; exit }
      }
    ')
    if [[ -n "$matched_name" ]]; then
      printf '%s
' "$matched_name"
      return 0
    fi
    matched_name=$(pactl_user "${short_cmd[@]}" | awk -v pat="${match,,}" 'tolower($0) ~ pat { print $2; exit }')
    if [[ -n "$matched_name" ]]; then
      printf '%s
' "$matched_name"
      return 0
    fi
  fi

  if [[ -n "$default_name" ]]; then
    printf '%s\n' "$default_name"
    return 0
  fi

  pactl_user "${short_cmd[@]}" | awk 'NR == 1 { print $2; exit }'
}

cleanup_existing_loopbacks() {
  pactl_user list short modules |
    awk '/module-loopback/ && /rca-hdmi-visualizer/ { print $1 }' |
    while read -r module_id; do
      [[ -n "$module_id" ]] && pactl_user unload-module "$module_id" || true
    done
}

wait_for_audio

source_name=$(pick_device source "$SOURCE_MATCH")
sink_name=$(pick_device sink "$SINK_MATCH")

if [[ -z "$source_name" ]]; then
  echo "Could not find capture source matching SOURCE_MATCH='$SOURCE_MATCH'" >&2
  pactl_user list short sources >&2 || true
  exit 1
fi

if [[ -z "$sink_name" ]]; then
  echo "Could not find output sink matching SINK_MATCH='$SINK_MATCH'" >&2
  pactl_user list short sinks >&2 || true
  exit 1
fi

echo "Creating audio loopback: source=$source_name sink=$sink_name latency=${LOOPBACK_LATENCY_MSEC}ms"
cleanup_existing_loopbacks

module_id=$(pactl_user load-module module-loopback \
  source="$source_name" \
  sink="$sink_name" \
  latency_msec="$LOOPBACK_LATENCY_MSEC" \
  sink_input_properties="application.name=rca-hdmi-visualizer")

echo "Loaded module-loopback id=$module_id"

# Keep the service alive so systemd restart/stop semantics unload the module cleanly.
trap 'pactl_user unload-module "$module_id" || true' EXIT INT TERM
while true; do
  sleep 3600 &
  wait $!
done
