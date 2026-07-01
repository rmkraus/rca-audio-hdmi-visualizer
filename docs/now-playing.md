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
  -> 12-second WAV sample
  -> local RMS/silence gate
  -> Shazam-style snippet lookup when audio is present
  -> optional iTunes metadata lookup for track length
  -> /var/lib/rca-hdmi-visualizer/now-playing.json
  -> fullscreen overlay with progress bar
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

## Listening loop

Default settings:

```env
RECOGNITION_SAMPLE_SECONDS=12
RECOGNITION_MIN_RMS=150
RECOGNITION_SILENCE_WINDOWS_TO_STOP=3
RECOGNITION_NO_MATCH_LIMIT=3
RECOGNITION_NO_MATCH_BACKOFF_SECONDS=30
RECOGNITION_PROGRESS_RESUME_PERCENT=100
RECOGNITION_MAX_RECHECK_WAIT_SECONDS=150
RECOGNITION_MISSING_DURATION_RECHECK_SECONDS=60
RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS=5
```

Meaning:

- record a 12-second sample
- measure RMS locally before any network lookup
- if the sample is below `RECOGNITION_MIN_RMS`, skip Shazam
- after 3 quiet samples, write `status=stopped` so the overlay shows `Stopped`
- one good audio window switches `playback_status` back to `playing`
- while playing, send above-threshold samples to Shazam
- on a match, look up the iTunes track length, show the estimated progress bar,
  and wait until the progress estimate reaches 100% before checking again
- never wait more than 150 seconds between checks, even when the current track is
  longer than that
- if track length is unavailable, hide the progress bar and check again after 60
  seconds
- on no match, check the next sample immediately
- after 3 consecutive no-match samples, pause for 30 seconds

Shazam's match offset is the position where the captured snippet matched the
track. The overlay estimates current position as:

```text
match offset + sample length + 5 seconds + elapsed wall-clock time
```

The extra 5 seconds compensates for lookup/display latency and is configurable
with `RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS`.

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
- `silence`: sample RMS was below `RECOGNITION_MIN_RMS`.
- `stopped`: enough consecutive quiet samples occurred to consider playback stopped.
- `error`: recognizer setup/network/runtime error; check `message` and service logs.

## Logs

```bash
journalctl -u rca-now-playing.service -f
journalctl -u rca-now-playing-overlay.service -f
```

## Overlay behavior

The overlay shows recognized tracks and the `Stopped` state by default. To show
diagnostic states such as silence/no-match while testing:

```env
OVERLAY_SHOW_UNRECOGNIZED=true
```

Restart the overlay after changing it:

```bash
sudo systemctl restart rca-now-playing-overlay.service
```
