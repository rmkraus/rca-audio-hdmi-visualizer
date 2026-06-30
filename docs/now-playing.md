# Now Playing Recognition

This appliance can identify records from the same USB audio capture source used by the visualizer. The first implementation uses Chromaprint/`fpcalc` locally and the free non-commercial AcoustID lookup API.

## Signal path

```text
RCA vinyl playback
  -> USB audio interface
     -> audio loopback to HDMI
     -> Cavasik visualizer
     -> rca-now-playing daemon
          -> record 45s WAV sample
          -> fpcalc Chromaprint fingerprint
          -> AcoustID lookup
          -> /var/lib/rca-hdmi-visualizer/now-playing.json
     -> fullscreen overlay window above Cavasik
```

## Why AcoustID should be plausible for vinyl here

Chromaprint is less phone-across-the-room/Shazam-like than ACRCloud, but this box has a clean line-level path: good vinyl source, RCA into USB interface, and no microphone room noise. That makes longer samples practical.

Start with:

```env
RECOGNITION_SAMPLE_SECONDS=45
RECOGNITION_MIN_SCORE=0.80
```

If matches are inconsistent, try:

```env
RECOGNITION_SAMPLE_SECONDS=60
RECOGNITION_MIN_SCORE=0.70
```

If false positives happen, raise `RECOGNITION_MIN_SCORE` toward `0.90`.

## Enable recognition

Register a non-commercial AcoustID app and get a client key:

https://acoustid.org/new-application

Put the key in the root-only secrets file:

```bash
sudo nano /etc/rca-hdmi-visualizer.secrets
```

```env
ACOUSTID_CLIENT_KEY=your_client_key_here
```

Enable recognition:

```bash
sudo nano /etc/rca-hdmi-visualizer.env
```

```env
RECOGNITION_ENABLED=true
```

Restart services:

```bash
sudo systemctl restart rca-now-playing.service rca-now-playing-overlay.service
```

## One-shot test

From the appliance:

```bash
sudo rca-now-playing identify-once
```

It records one sample, fingerprints it, queries AcoustID, writes the state JSON, and prints the result.

Inspect current state:

```bash
jq . /var/lib/rca-hdmi-visualizer/now-playing.json
```

## Daemon behavior

`rca-now-playing.service` loops forever:

1. records `RECOGNITION_SAMPLE_SECONDS` from the configured capture source
2. skips lookup if RMS is below `RECOGNITION_MIN_RMS`
3. runs `fpcalc`
4. sends duration/fingerprint to AcoustID
5. writes `NOW_PLAYING_STATE` when a track is recognized
6. sleeps `RECOGNITION_INTERVAL_SECONDS`, or `RECOGNITION_COOLDOWN_SECONDS` after confirming the same song

By default `RECOGNITION_KEEP_LAST_ON_MISS=true`, so a temporary low-score/no-match sample does not clear the current overlay while a record side is still playing.

The default request rate is very low: roughly one lookup every 30-75 seconds while audio is present.

## Overlay behavior

`rca-now-playing-overlay.service` runs a fullscreen Tk window above Cavasik. It is hidden until a recognized track is available by default. Set this for debugging:

```env
OVERLAY_SHOW_UNRECOGNIZED=true
```

The overlay is slightly transparent by default:

```env
OVERLAY_ALPHA=0.78
```

Lower values show more of the visualization behind the text. Higher values make metadata more readable.

## Audio source selection

By default the recognizer uses `SOURCE_MATCH`, the same matching rule as the audio loopback. You can override it:

```env
RECOGNITION_SOURCE=alsa_input.usb-Your_Interface.analog-stereo
```

List sources:

```bash
pactl list short sources
pactl list sources | grep -E 'Name:|Description:'
```

## Troubleshooting

### The service says recognition is disabled

Set:

```env
RECOGNITION_ENABLED=true
```

then restart `rca-now-playing.service`.

### Missing client key

Set `ACOUSTID_CLIENT_KEY` in `/etc/rca-hdmi-visualizer.secrets`.

### Silence detected even while music plays

Lower the gate:

```env
RECOGNITION_MIN_RMS=75
```

or verify `SOURCE_MATCH`/`RECOGNITION_SOURCE` points to the USB capture source.

### Low score / no match

Try longer samples:

```env
RECOGNITION_SAMPLE_SECONDS=60
```

For vinyl, avoid sampling the lead-in/lead-out and verify the record is playing at the correct speed.

### Overlay is not visible

Check:

```bash
systemctl status rca-now-playing-overlay.service
journalctl -u rca-now-playing-overlay.service -f
```

For debugging, set:

```env
OVERLAY_SHOW_UNRECOGNIZED=true
```

Then restart the overlay service.
