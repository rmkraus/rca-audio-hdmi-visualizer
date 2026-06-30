# Build Notes

## Signal flow

```text
RCA source
  -> stereo RCA to interface cable
  -> USB audio interface line input
  -> Raspberry Pi
       -> PipeWire/PulseAudio module-loopback to HDMI sink
       -> Cavasik Flatpak visualizing system audio
  -> HDMI display / AV receiver / capture device
```

## Recommended Raspberry Pi settings

Use Raspberry Pi OS 64-bit with desktop and enable predictable HDMI output in `/boot/firmware/config.txt` if your display is slow to wake:

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
