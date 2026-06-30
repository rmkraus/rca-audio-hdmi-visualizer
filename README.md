# RCA Audio to HDMI Visualizer Appliance

A small Linux appliance for converting analog RCA audio into an HDMI visualizer display.

The intended hardware path is:

- RCA stereo source
- USB audio interface for capture
- Raspberry Pi or Jetson Nano HDMI output to a TV/projector/capture chain
- Full-screen [Cavasik](https://github.com/TheWisker/Cavasik) audio visualizer
- Live audio monitor from USB capture input to HDMI audio output
- Shazam-style now-playing recognition for the vinyl feed
- Fullscreen now-playing overlay displayed above the visualization

This repository contains an installer and systemd units to make the box boot directly into the visualizer experience:

- automatic graphical login through LightDM
- Cavasik launched full screen/kiosk style
- PipeWire/PulseAudio audio loopback from USB capture to HDMI output
- unattended security upgrades enabled
- unattended upgrade window set to 4:00 AM
- optional Jetson Nano 10W/`jetson_clocks` performance service
- optional now-playing recognition daemon using a Shazam-style snippet recognizer
- Tailscale installed and `tailscaled` enabled for manual login/configuration

## Target OS and hardware

Recommended options:

- **Jetson Nano Developer Kit** running official JetPack 4.6.x / L4T Ubuntu 18.04. This should have much more headroom than a Raspberry Pi 3 for 1080p visualization, but the biggest risk is whether the current Cavasik Flatpak will install and run on the older Ubuntu/Flatpak stack.
- **Raspberry Pi OS 64-bit with desktop** on a Raspberry Pi 4 or Pi 5.

Raspberry Pi 3 can output 1080p, but full-screen Cavasik at 1080p may be marginal. Use 720p or a minimal desktop if it stutters.

## Quick start

On a freshly installed supported desktop image:

```bash
sudo apt update
sudo apt install -y git

git clone https://github.com/rmkraus/rca-audio-hdmi-visualizer.git
cd rca-audio-hdmi-visualizer
sudo ./scripts/install.sh
sudo reboot
```

Force a platform if auto-detection is wrong:

```bash
sudo ./scripts/install.sh --platform jetson-nano
sudo ./scripts/install.sh --platform raspberry-pi
sudo ./scripts/install.sh --platform generic
```

Tailscale is installed by the installer, but it is not authenticated automatically. After install, log in manually on the node:

```bash
sudo tailscale up
```

After reboot, the system should log in as the configured desktop user, start the graphical session, launch Cavasik full screen, and start audio loopback from the USB audio interface to HDMI.

## Jetson Nano notes

The Jetson Nano is likely the best old-board candidate for 1080p Cavasik because it has 4GB RAM and an NVIDIA Maxwell GPU. Use a proper **5V/4A barrel-jack power supply**, cooling, and 10W mode.

The installer adds `jetson-performance.service` on Jetson Nano. It runs:

```bash
nvpmodel -m 0
jetson_clocks
```

### Cavasik risk check

Yes — Cavasik availability is the main risk on Jetson Nano. The official Nano software stack is old, and current Flathub runtimes may or may not work cleanly with its Flatpak version.

Before committing the box build, run:

```bash
./scripts/check-cavasik.sh
```

To install and test launch from a graphical session:

```bash
./scripts/check-cavasik.sh --install --run-test
```

If Cavasik is not available from Flathub on the Nano, options are:

- update Flatpak from a newer repo/backport, then rerun `scripts/check-cavasik.sh --install`
- install the rest of this appliance with `sudo ./scripts/install.sh --platform jetson-nano --assume-cavasik`, then install Cavasik manually later
- install with `--skip-cavasik` and replace the visualizer command later
- use a different visualizer, such as terminal `cava` in a fullscreen terminal, as a fallback

## Now playing recognition

Recognition is disabled by default. To use it, enable `RECOGNITION_ENABLED=true` in `/etc/rca-hdmi-visualizer.env` and restart the recognition services.

```bash
sudo nano /etc/rca-hdmi-visualizer.env
sudo systemctl restart rca-now-playing.service rca-now-playing-overlay.service
```

One-shot test:

```bash
sudo rca-now-playing identify-once
jq . /var/lib/rca-hdmi-visualizer/now-playing.json
```

The default conservative sample length is 12 seconds. See `docs/now-playing.md` for tuning sample length, polling interval, silence detection, and overlay opacity.

## Hardware notes

- Use a USB audio interface with stereo line input. Many cheap dongles expose mic input only; those can clip or sum the signal incorrectly.
- RCA output is line-level. If your interface has gain controls, start low and raise until Cavasik responds without clipping.
- HDMI audio output must be selected as the default output, or explicitly configured in `/etc/rca-hdmi-visualizer.env`.
- If the RCA source hums, use a USB interface with isolation or add an RCA ground-loop isolator.

## Configuration

The installer creates `/etc/rca-hdmi-visualizer.env` from `config/rca-hdmi-visualizer.env.example`.

Useful settings:

- `VISUALIZER_USER`: desktop user that runs the GUI and user audio session.
- `PLATFORM`: detected install platform, such as `jetson-nano`, `raspberry-pi`, or `generic`.
- `SOURCE_MATCH`: case-insensitive text used to identify the USB capture source.
- `SINK_MATCH`: case-insensitive text used to identify the HDMI output sink.
- `LOOPBACK_LATENCY_MSEC`: requested PipeWire/PulseAudio loopback latency.
- `CAVASIK_APP_ID`: Flatpak app ID, normally `io.github.TheWisker.Cavasik`.
- `VISUALIZER_COMMAND`: optional command override if you need to launch a non-Flatpak build of Cavasik or a fallback visualizer.
- `RECOGNITION_ENABLED`: enables/disables now-playing recognition.
- `RECOGNITION_SAMPLE_SECONDS`: sample length sent to the recognizer; 12 seconds is the default, 15-20 can improve difficult passages.
- `RECOGNITION_INTERVAL_SECONDS`: delay between attempts after no match.
- `RECOGNITION_COOLDOWN_SECONDS`: delay after recognizing the same track again.
- `RECOGNITION_MIN_RMS`: silence gate; samples quieter than this skip lookup.
- `OVERLAY_ALPHA`: fullscreen overlay opacity.

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
- `rca-now-playing.service`: records short audio samples and identifies them with the Shazam-style recognizer.
- `rca-now-playing-overlay.service`: shows recognized now-playing metadata in a fullscreen always-on-top overlay.
- `jetson-performance.service`: Jetson Nano only; enables 10W mode and max clocks.

Useful commands:

```bash
systemctl status rca-cavasik-kiosk.service
systemctl status rca-audio-loopback.service
systemctl status rca-now-playing.service
systemctl status rca-now-playing-overlay.service
systemctl status jetson-performance.service
journalctl -u rca-cavasik-kiosk.service -f
journalctl -u rca-audio-loopback.service -f
journalctl -u rca-now-playing.service -f
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
- This setup expects X11 because the fullscreen helper uses X11 window-management tools.
- Flatpak apps can sometimes need first-run configuration. Launch Cavasik manually once if you want to customize colors/modes before making the box appliance-like.
- Recognition uses an unofficial Shazam-style recognizer. Keep polling conservative and expect the upstream endpoint/library to change without notice.
