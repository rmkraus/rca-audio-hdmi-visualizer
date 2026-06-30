#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE=/etc/rca-hdmi-visualizer.env
DEFAULT_USER=${SUDO_USER:-}
ASSUME_CAVASIK=false
SKIP_CAVASIK=false
PLATFORM=auto

usage() {
  cat <<USAGE
Usage: sudo $0 [--platform auto|raspberry-pi|jetson-nano|generic] [--assume-cavasik] [--skip-cavasik]

Installs the RCA audio to HDMI visualizer appliance.

Options:
  --platform         Select platform behavior. Default: auto.
  --assume-cavasik  Do not fail install if Cavasik cannot be found on Flathub.
                    The kiosk service will be installed but may restart until Cavasik is installed.
  --skip-cavasik    Skip Cavasik Flatpak install entirely.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM=${2:-}
      shift
      ;;
    --assume-cavasik)
      ASSUME_CAVASIK=true
      ;;
    --skip-cavasik)
      SKIP_CAVASIK=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

case "$PLATFORM" in
  auto|raspberry-pi|jetson-nano|generic) ;;
  *) echo "Unsupported platform '$PLATFORM'" >&2; exit 2 ;;
esac

detect_platform() {
  if [[ -r /proc/device-tree/model ]]; then
    local model
    model=$(tr -d '\0' </proc/device-tree/model)
    case "$model" in
      *Raspberry\ Pi*) echo raspberry-pi; return ;;
      *NVIDIA\ Jetson\ Nano*|*Jetson\ Nano*) echo jetson-nano; return ;;
    esac
  fi
  if [[ -r /etc/nv_tegra_release ]]; then
    echo jetson-nano
    return
  fi
  echo generic
}

if [[ "$PLATFORM" == auto ]]; then
  PLATFORM=$(detect_platform)
fi

if [[ -z "$DEFAULT_USER" || "$DEFAULT_USER" == root ]]; then
  DEFAULT_USER=$(getent passwd 1000 | cut -d: -f1 || true)
fi

if [[ -z "$DEFAULT_USER" ]] || ! id "$DEFAULT_USER" >/dev/null 2>&1; then
  echo "Could not determine desktop user. Create one first or edit $ENV_FILE after install." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

APT_PACKAGES=(
  flatpak
  unattended-upgrades
  apt-listchanges
  lightdm
  x11-utils
  wmctrl
  xdotool
  pulseaudio-utils
  curl
  ca-certificates
  ffmpeg
  libchromaprint-tools
  python3-tk
)

case "$PLATFORM" in
  raspberry-pi)
    APT_PACKAGES+=(xscreensaver)
    ;;
  jetson-nano)
    # Jetson performance tools are normally preinstalled by JetPack/L4T.
    # Do not add them as apt dependencies; package names vary by image.
    ;;
esac

apt-get update
apt-get install -y "${APT_PACKAGES[@]}"

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled.service || systemctl enable --now tailscaled || true

if ! flatpak remotes 2>/dev/null | awk '{print $1}' | grep -qx flathub; then
  flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
fi

if [[ "$SKIP_CAVASIK" == false ]]; then
  if "$REPO_DIR/scripts/check-cavasik.sh" --install; then
    echo "Cavasik installed/available."
  elif [[ "$ASSUME_CAVASIK" == true ]]; then
    echo "WARNING: Cavasik Flatpak install failed; continuing because --assume-cavasik was supplied." >&2
  else
    cat >&2 <<'EOF'
Cavasik could not be installed from Flathub on this machine.

This is the main Jetson Nano risk: old JetPack/Ubuntu images may ship an old
Flatpak stack that cannot consume current Flathub runtimes/apps.

Try one of these:
  - update Flatpak from a newer repo/backport, then rerun this installer
  - run scripts/check-cavasik.sh --install after Flatpak is updated
  - rerun this installer with --assume-cavasik to install the rest anyway
  - rerun with --skip-cavasik if you plan to install/launch Cavasik another way
EOF
    exit 1
  fi
fi

install -m 0755 "$REPO_DIR/scripts/start-cavasik-kiosk.sh" /usr/local/bin/start-cavasik-kiosk
install -m 0755 "$REPO_DIR/scripts/start-audio-loopback.sh" /usr/local/bin/start-audio-loopback
install -m 0755 "$REPO_DIR/scripts/start-now-playing-overlay.sh" /usr/local/bin/start-now-playing-overlay
install -m 0755 "$REPO_DIR/scripts/check-cavasik.sh" /usr/local/bin/check-cavasik
install -m 0755 "$REPO_DIR/scripts/rca-now-playing" /usr/local/bin/rca-now-playing
install -m 0755 "$REPO_DIR/scripts/rca-now-playing-overlay" /usr/local/bin/rca-now-playing-overlay
install -m 0755 "$REPO_DIR/scripts/rca-simple-visualizer" /usr/local/bin/rca-simple-visualizer
mkdir -p /opt/rca-hdmi-visualizer
cp -a "$REPO_DIR/rca_visualizer" /opt/rca-hdmi-visualizer/
install -m 0644 "$REPO_DIR/systemd/rca-cavasik-kiosk.service" /etc/systemd/system/rca-cavasik-kiosk.service
install -m 0644 "$REPO_DIR/systemd/rca-audio-loopback.service" /etc/systemd/system/rca-audio-loopback.service
install -m 0644 "$REPO_DIR/systemd/rca-now-playing.service" /etc/systemd/system/rca-now-playing.service
install -m 0644 "$REPO_DIR/systemd/rca-now-playing-overlay.service" /etc/systemd/system/rca-now-playing-overlay.service

if [[ "$PLATFORM" == jetson-nano ]]; then
  install -m 0644 "$REPO_DIR/systemd/jetson-performance.service" /etc/systemd/system/jetson-performance.service
fi

if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0644 "$REPO_DIR/config/rca-hdmi-visualizer.env.example" "$ENV_FILE"
  sed -i "s/^VISUALIZER_USER=.*/VISUALIZER_USER=$DEFAULT_USER/" "$ENV_FILE"
  sed -i "s/^PLATFORM=.*/PLATFORM=$PLATFORM/" "$ENV_FILE"
fi

SECRETS_FILE=/etc/rca-hdmi-visualizer.secrets
if [[ ! -f "$SECRETS_FILE" ]]; then
  install -m 0600 "$REPO_DIR/config/rca-hdmi-visualizer.secrets.example" "$SECRETS_FILE"
fi

mkdir -p /var/lib/rca-hdmi-visualizer
chown "$DEFAULT_USER:$DEFAULT_USER" /var/lib/rca-hdmi-visualizer

mkdir -p /etc/lightdm/lightdm.conf.d
SESSION_NAME=LXDE-pi
if [[ "$PLATFORM" == jetson-nano ]]; then
  SESSION_NAME=ubuntu
elif [[ -d /usr/share/xsessions ]]; then
  if [[ ! -f /usr/share/xsessions/${SESSION_NAME}.desktop ]]; then
    SESSION_NAME=$(find /usr/share/xsessions -maxdepth 1 -name '*.desktop' -printf '%f\n' | sed 's/\.desktop$//' | head -n 1 || true)
    SESSION_NAME=${SESSION_NAME:-LXDE-pi}
  fi
fi

cat >/etc/lightdm/lightdm.conf.d/60-rca-hdmi-visualizer.conf <<LIGHTDM
[Seat:*]
autologin-user=$DEFAULT_USER
autologin-user-timeout=0
user-session=$SESSION_NAME
xserver-command=X -s 0 -dpms
LIGHTDM

USER_HOME=$(getent passwd "$DEFAULT_USER" | cut -d: -f6)
if [[ "$PLATFORM" == raspberry-pi ]]; then
  mkdir -p "$USER_HOME/.config/lxsession/LXDE-pi"
  cat >"$USER_HOME/.config/lxsession/LXDE-pi/autostart" <<'AUTOSTART'
@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@xscreensaver -no-splash
@xset s off
@xset -dpms
@xset s noblank
AUTOSTART
  chown -R "$DEFAULT_USER:$DEFAULT_USER" "$USER_HOME/.config/lxsession"
else
  mkdir -p "$USER_HOME/.config/autostart"
  cat >"$USER_HOME/.config/autostart/rca-disable-blanking.desktop" <<'AUTOSTART'
[Desktop Entry]
Type=Application
Name=Disable display blanking
Exec=sh -c 'xset s off; xset -dpms; xset s noblank'
X-GNOME-Autostart-enabled=true
AUTOSTART
  chown -R "$DEFAULT_USER:$DEFAULT_USER" "$USER_HOME/.config/autostart"
fi

loginctl enable-linger "$DEFAULT_USER" || true

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT_AUTO'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT_AUTO

cat >/etc/apt/apt.conf.d/51rca-hdmi-unattended-upgrades <<'APT_UNATTENDED'
Unattended-Upgrade::Origins-Pattern {
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
        "origin=Ubuntu,codename=${distro_codename}";
        "origin=Ubuntu,codename=${distro_codename},label=Ubuntu";
        "origin=Raspberry Pi Foundation,codename=${distro_codename}";
        "origin=Raspbian,codename=${distro_codename},label=Raspbian-Security";
};
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-New-Unused-Dependencies "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
APT_UNATTENDED

mkdir -p /etc/systemd/system/apt-daily.timer.d /etc/systemd/system/apt-daily-upgrade.timer.d
cat >/etc/systemd/system/apt-daily.timer.d/override.conf <<'APT_TIMER'
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:30
RandomizedDelaySec=15m
Persistent=true
APT_TIMER

cat >/etc/systemd/system/apt-daily-upgrade.timer.d/override.conf <<'APT_UPGRADE_TIMER'
[Timer]
OnCalendar=
OnCalendar=*-*-* 04:00
RandomizedDelaySec=15m
Persistent=true
APT_UPGRADE_TIMER

systemctl daemon-reload
systemctl enable lightdm.service || true
systemctl enable apt-daily.timer apt-daily-upgrade.timer
systemctl enable rca-cavasik-kiosk.service rca-audio-loopback.service rca-now-playing.service rca-now-playing-overlay.service
if [[ "$PLATFORM" == jetson-nano ]]; then
  systemctl enable jetson-performance.service
fi

cat <<EOF
Install complete.

Platform: $PLATFORM
Config file: $ENV_FILE
Desktop user: $DEFAULT_USER
LightDM session: $SESSION_NAME

Recommended next steps:
  1. Log into Tailscale manually: sudo tailscale up
  2. Add your AcoustID client key to /etc/rca-hdmi-visualizer.secrets
  3. Enable recognition with RECOGNITION_ENABLED=true in $ENV_FILE
  4. Reboot: sudo reboot
  5. If audio routing is wrong, edit SOURCE_MATCH/SINK_MATCH in $ENV_FILE
  6. Check services with: systemctl status rca-cavasik-kiosk rca-audio-loopback rca-now-playing rca-now-playing-overlay
  7. Check Cavasik availability with: check-cavasik --run-test
EOF
