# Build Notes

## Signal flow

```text
RCA source
  -> stereo RCA to interface cable
  -> USB audio interface line input
  -> Linux appliance
       -> PipeWire/PulseAudio module-loopback to HDMI sink
       -> Cavasik Flatpak visualizing system audio
  -> HDMI display / AV receiver / capture device
```

## Platform selection

`scripts/install.sh` auto-detects:

- Raspberry Pi from `/proc/device-tree/model`
- Jetson Nano from `/proc/device-tree/model` or `/etc/nv_tegra_release`
- otherwise generic Linux desktop

Override with:

```bash
sudo ./scripts/install.sh --platform jetson-nano
sudo ./scripts/install.sh --platform raspberry-pi
sudo ./scripts/install.sh --platform generic
```

## Jetson Nano build notes

Use the official JetPack 4.6.x image unless you already have a known-good newer community image. The official image is old, but it includes NVIDIA's display/GPU stack.

Recommended before install:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Use the barrel jack with a 5V/4A supply, add a fan/heatsink, and leave the board in 10W mode. The installer enables `jetson-performance.service`, which runs `nvpmodel -m 0` and `jetson_clocks` at boot when those tools exist.

### Cavasik viability

The main Jetson risk is not performance; it is Flatpak/Cavasik compatibility with the older Ubuntu 18.04-era base.

Check without installing:

```bash
./scripts/check-cavasik.sh
```

Install and briefly launch-test from inside a graphical session:

```bash
./scripts/check-cavasik.sh --install --run-test
```

A `--run-test` timeout is usually good: it means the app launched and kept running until the test killed it.

If the check fails before install, try updating Flatpak. If Cavasik still cannot be installed, continue with the rest of the appliance using:

```bash
sudo ./scripts/install.sh --platform jetson-nano --assume-cavasik
```

Then install or build Cavasik manually later, or replace the visualizer command.

## Raspberry Pi settings

Use Raspberry Pi OS 64-bit with desktop. For Pi 3, consider 720p if 1080p stutters.

Enable predictable HDMI output in `/boot/firmware/config.txt` if your display is slow to wake:

```ini
hdmi_force_hotplug=1
hdmi_drive=2
```

For a fixed 1080p output, add the mode appropriate for your display. Example:

```ini
hdmi_group=1
hdmi_mode=16
```

## Choosing audio devices

The loopback script uses `pactl` and chooses devices by matching text against names/descriptions.

List sources:

```bash
pactl list short sources
pactl list sources | grep -E 'Name:|Description:'
```

List sinks:

```bash
pactl list short sinks
pactl list sinks | grep -E 'Name:|Description:'
```

Then edit `/etc/rca-hdmi-visualizer.env`:

```env
SOURCE_MATCH=USB
SINK_MATCH=HDMI
```

Set either value empty to use the current default source/sink.

## First-run Cavasik configuration

Cavasik stores settings in the desktop user's Flatpak config. To customize the visual style:

```bash
flatpak run io.github.TheWisker.Cavasik
```

Configure drawing mode, colors, smoothing, and sensitivity, then close it. The kiosk service will use the same saved settings on later boots.
