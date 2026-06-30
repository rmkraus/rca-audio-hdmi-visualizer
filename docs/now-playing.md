# Now-playing recognition

The appliance can identify records from the same USB audio capture source used by
the visualizer. Recognition is designed for live vinyl snippets and uses a
Shazam-style recognizer from a short local WAV sample.

Raw audio is recorded locally under a temporary directory and sent only to the
recognition client for the lookup. The persistent state written for the overlay
is `/var/lib/rca-hdmi-visualizer/now-playing.json`.

## Data flow

```text
USB audio source
  -> short WAV sample
  -> Shazam-style snippet lookup
  -> /var/lib/rca-hdmi-visualizer/now-playing.json
  -> fullscreen overlay
```

## Enable recognition

Edit the appliance config:

```bash
sudo nano /etc/rca-hdmi-visualizer.env
```

Set:

```env
RECOGNITION_ENABLED=true
```

Then restart:

```bash
sudo systemctl restart rca-now-playing.service rca-now-playing-overlay.service
```

## Recommended timing

Default conservative settings:

```env
RECOGNITION_SAMPLE_SECONDS=12
RECOGNITION_INTERVAL_SECONDS=180
RECOGNITION_COOLDOWN_SECONDS=30
RECOGNITION_MIN_RMS=150
RECOGNITION_KEEP_LAST_ON_MISS=true
```

Meaning:

- record a 12-second sample
- if not recognized, wait 3 minutes before trying again
- if the same track is recognized again, wait 10 minutes before trying again
- skip network lookup when the sample is below the RMS silence threshold

Shorter samples can work with the Shazam-style backend, but reliability depends
on the exact passage. Use 12 seconds as the starting point. If recognition is
spotty, try 15-20 seconds before increasing polling frequency.

## Source selection

If the wrong source is used, inspect PulseAudio source names:

```bash
sudo -u "$USER" XDG_RUNTIME_DIR=/run/user/$(id -u "$USER") pactl list short sources
```

Then set the exact source:

```env
RECOGNITION_SOURCE=alsa_input.usb-IK_Multimedia_iRig_HD_X_0502161-02.analog-mono
RECOGNITION_CHANNELS=1
```

Restart recognition after changing the source:

```bash
sudo systemctl restart rca-now-playing.service
```

## One-shot test

With music playing:

```bash
sudo rca-now-playing identify-once
jq . /var/lib/rca-hdmi-visualizer/now-playing.json
```

Useful status values:

- `recognized`: song/artist found and overlay should show it.
- `no_match`: audio was present, but the recognizer did not find a match.
- `silence`: sample RMS was below `RECOGNITION_MIN_RMS`; check input level/source.
- `error`: recognizer setup/network/runtime error; check `message` and service logs.

## Logs

```bash
journalctl -u rca-now-playing.service -f
journalctl -u rca-now-playing-overlay.service -f
```

## Overlay behavior

The overlay shows only recognized tracks by default. To show diagnostic states
such as silence/no-match while testing:

```env
OVERLAY_SHOW_UNRECOGNIZED=true
```

Restart the overlay after changing it:

```bash
sudo systemctl restart rca-now-playing-overlay.service
```
