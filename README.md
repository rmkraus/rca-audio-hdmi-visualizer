# Raspberry Pi RCA Audio to HDMI Visualizer

A Raspberry Pi appliance for converting analog RCA audio into an HDMI visualizer display.

The intended hardware path is:

- RCA stereo source
- USB audio interface connected to the Raspberry Pi for capture
- Raspberry Pi HDMI output to a TV/projector/capture chain
- Full-screen [Cavasik](https://github.com/TheWisker/Cavasik) audio visualizer
- Live audio monitor from USB capture input to HDMI audio output

This repository contains an installer and systemd units to make the Pi boot directly into the visualizer box experience:

- automatic graphical login
- Cavasik launched full screen/kiosk style
- PipeWire/PulseAudio audio loopback from USB capture to HDMI output
- unattended security upgrades enabled
- unattended upgrade window set to 4:00 AM

## Target OS

Recommended: **Raspberry Pi OS 64-bit with desktop** on a Raspberry Pi 4 or Pi 5.

Cavasik is installed from Flathub. Flathub currently publishes `io.github.TheWisker.Cavasik` for `aarch64`, which is the Raspberry Pi OS 64-bit architecture.

## Quick start

On a freshly installed Raspberry Pi OS desktop image:

```bash
sudo apt update
sudo apt install -y git

git clone https://github.com/rmkraus/rca-audio-hdmi-visualizer.git
cd rca-audio-hdmi-visualizer
sudo ./scripts/install.sh
sudo reboot
```

After reboot, the Pi should log in as the configured desktop user, start the graphical session, launch Cavasik full screen, and start audio loopback from the USB audio interface to HDMI.

## Hardware notes

- Use a USB audio interface with stereo line input. Many cheap dongles expose mic input only; those can clip or sum the signal incorrectly.
- RCA output is line-level. If your interface has gain controls, start low and raise until Cavasik responds without clipping.
- HDMI audio output must be selected as the default output, or explicitly configured in `/etc/rca-hdmi-visualizer.env`.
- If the RCA source hums, use a USB interface with isolation or add an RCA ground-loop isolator.

## Configuration

The installer creates `/etc/rca-hdmi-visualizer.env` from `config/rca-hdmi-visualizer.env.example`.

Useful settings:

- `VISUALIZER_USER`: desktop user that runs the GUI and user audio session.
- `SOURCE_MATCH`: case-insensitive text used to identify the USB capture source.
- `SINK_MATCH`: case-insensitive text used to identify the HDMI output sink.
- `LOOPBACK_LATENCY_MSEC`: requested PipeWire/PulseAudio loopback latency.
- `CAVASIK_APP_ID`: Flatpak app ID, normally `io.github.TheWisker.Cavasik`.

To inspect audio device names after install:

```bash
sudo -u "$USER" pactl list short sources
sudo -u "$USER" pactl list short sinks
```

Then edit:

```bash
sudo nano /etc/rca-hdmi-visualizer.env
sudo systemctl restart rca-audio-loopback.service
```

## Services

System services installed by this repo:

- `rca-cavasik-kiosk.service`: waits for the desktop session and launches Cavasik under the configured user.
- `rca-audio-loopback.service`: creates an audio loopback from the selected USB capture source to HDMI output.

Useful commands:

```bash
systemctl status rca-cavasik-kiosk.service
systemctl status rca-audio-loopback.service
journalctl -u rca-cavasik-kiosk.service -f
journalctl -u rca-audio-loopback.service -f
```

## Updates

The installer configures unattended-upgrades and apt timers so package updates run around **4:00 AM**.

Files installed:

- `/etc/apt/apt.conf.d/20auto-upgrades`
- `/etc/apt/apt.conf.d/51rca-hdmi-unattended-upgrades`
- systemd timer drop-ins for `apt-daily.timer` and `apt-daily-upgrade.timer`

Check timers with:

```bash
systemctl list-timers 'apt-daily*'
```

## Caveats

- The kiosk launcher uses `wmctrl` and `xdotool` to make the Cavasik window full screen. If Cavasik changes its window title/class, adjust `WINDOW_MATCH` in `/etc/rca-hdmi-visualizer.env`.
- On some Raspberry Pi OS releases, the display manager may be Wayland instead of X11. This setup forces X11 because the fullscreen helper uses X11 window-management tools.
- Flatpak apps can sometimes need first-run configuration. Launch Cavasik manually once if you want to customize colors/modes before making the box appliance-like.
