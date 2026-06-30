#!/usr/bin/env bash
set -euo pipefail

APP_ID=${CAVASIK_APP_ID:-io.github.TheWisker.Cavasik}
INSTALL=false
RUN_TEST=false

usage() {
  cat <<USAGE
Usage: $0 [--install] [--run-test]

Checks whether Cavasik is available through Flatpak on this machine.

Options:
  --install   Install Cavasik from Flathub if it is available.
  --run-test  Try launching Cavasik briefly after install/availability checks.

Environment:
  CAVASIK_APP_ID=$APP_ID
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install) INSTALL=true ;;
    --run-test) RUN_TEST=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

arch=$(dpkg --print-architecture 2>/dev/null || uname -m)
flatpak_version=$(flatpak --version 2>/dev/null || true)

printf 'Architecture: %s\n' "$arch"
printf 'Flatpak: %s\n' "${flatpak_version:-not installed}"

if ! command -v flatpak >/dev/null 2>&1; then
  echo "Flatpak is not installed. Install with: sudo apt install flatpak" >&2
  exit 1
fi

if ! flatpak remotes 2>/dev/null | awk '{print $1}' | grep -qx flathub; then
  echo "Flathub remote is missing; adding it system-wide."
  flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
fi

if flatpak info "$APP_ID" >/dev/null 2>&1; then
  echo "Cavasik is already installed: $APP_ID"
else
  echo "Checking Flathub availability for $APP_ID ..."
  if ! flatpak remote-ls --app --columns=application flathub 2>/dev/null | grep -qx "$APP_ID"; then
    echo "Cavasik was not found on Flathub for this Flatpak/architecture combination." >&2
    echo "On Jetson Nano, this usually means the old JetPack/Ubuntu Flatpak stack needs updating or Cavasik needs an alternate install path." >&2
    exit 1
  fi
  echo "Cavasik is available from Flathub."

  if [[ "$INSTALL" == true ]]; then
    flatpak install -y flathub "$APP_ID"
  else
    echo "Not installing. Re-run with --install to install it."
  fi
fi

if [[ "$RUN_TEST" == true ]]; then
  if ! flatpak info "$APP_ID" >/dev/null 2>&1; then
    echo "Cannot run-test because $APP_ID is not installed." >&2
    exit 1
  fi
  echo "Launching $APP_ID for 10 seconds. This requires a working graphical session."
  timeout 10s flatpak run "$APP_ID" || status=$?
  status=${status:-0}
  if [[ "$status" -eq 124 ]]; then
    echo "Launch test reached timeout; that usually means the app started and kept running."
  elif [[ "$status" -ne 0 ]]; then
    echo "Launch test failed with exit status $status." >&2
    exit "$status"
  else
    echo "Launch test exited normally."
  fi
fi

echo "Cavasik check complete."
