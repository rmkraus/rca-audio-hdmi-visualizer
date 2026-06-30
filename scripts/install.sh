#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE=/etc/rca-hdmi-visualizer.env
DEFAULT_USER=${SUDO_USER:-pi}

if [[ "$DEFAULT_USER" == "root" ]]; then
  DEFAULT_USER=pi
fi

if ! id "$DEFAULT_USER" >/dev/null 2>&1; then
  DEFAULT_USER=$(getent passwd 1000 | cut -d: -f1 || true)
fi

if [[ -z "$DEFAULT_USER" ]] || ! id "$DEFAULT_USER" >/dev/null 2>&1; then
  echo "Could not determine desktop user. Create one first or edit $ENV_FILE after install." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  flatpak \
  unattended-upgrades \
  apt-listchanges \
  lightdm \
  x11-utils \
  wmctrl \
  xdotool \
  pulseaudio-utils

if ! flatpak remote-list --columns=name | grep -qx flathub; then
  flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
fi

flatpak install -y flathub io.github.TheWisker.Cavasik

install -m 0755 "$REPO_DIR/scripts/start-cavasik-kiosk.sh" /usr/local/bin/start-cavasik-kiosk
install -m 0755 "$REPO_DIR/scripts/start-audio-loopback.sh" /usr/local/bin/start-audio-loopback
install -m 0644 "$REPO_DIR/systemd/rca-cavasik-kiosk.service" /etc/systemd/system/rca-cavasik-kiosk.service
install -m 0644 "$REPO_DIR/systemd/rca-audio-loopback.service" /etc/systemd/system/rca-audio-loopback.service

if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0644 "$REPO_DIR/config/rca-hdmi-visualizer.env.example" "$ENV_FILE"
  sed -i "s/^VISUALIZER_USER=.*/VISUALIZER_USER=$DEFAULT_USER/" "$ENV_FILE"
fi

# Raspberry Pi OS desktop normally uses LightDM. Configure autologin and prefer X11,
# because fullscreen control relies on wmctrl/xdotool.
mkdir -p /etc/lightdm/lightdm.conf.d
cat >/etc/lightdm/lightdm.conf.d/60-rca-hdmi-visualizer.conf <<LIGHTDM
[Seat:*]
autologin-user=$DEFAULT_USER
autologin-user-timeout=0
user-session=LXDE-pi
xserver-command=X -s 0 -dpms
LIGHTDM

# Disable blanking in the user's LXDE session when that config exists.
USER_HOME=$(getent passwd "$DEFAULT_USER" | cut -d: -f6)
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

# Keep the user's audio/session bus alive enough for boot-time system services.
loginctl enable-linger "$DEFAULT_USER" || true

# Enable unattended upgrades and run upgrade timer around 04:00.
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT_AUTO'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT_AUTO

cat >/etc/apt/apt.conf.d/51rca-hdmi-unattended-upgrades <<'APT_UNATTENDED'
Unattended-Upgrade::Origins-Pattern {
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
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
systemctl enable rca-cavasik-kiosk.service rca-audio-loopback.service

cat <<EOF
Install complete.

Config file: $ENV_FILE
Desktop user: $DEFAULT_USER

Recommended next steps:
  1. Reboot: sudo reboot
  2. If audio routing is wrong, edit SOURCE_MATCH/SINK_MATCH in $ENV_FILE
  3. Check services with: systemctl status rca-cavasik-kiosk rca-audio-loopback
EOF
