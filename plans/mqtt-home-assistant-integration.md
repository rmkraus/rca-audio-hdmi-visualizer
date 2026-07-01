# MQTT Home Assistant Integration Plan

**Goal:** Add MQTT publishing and Home Assistant MQTT discovery for the RCA/HDMI now-playing appliance so Home Assistant can show playback state, recognized track metadata, signal/debug metrics, and service health.

**Scope:** Planning only. This plan describes a future implementation. It does not implement MQTT integration yet.

## Requirements

- Publish appliance state to MQTT for Home Assistant.
- Support Home Assistant MQTT discovery so entities appear automatically.
- Publish now-playing metadata:
  - track title
  - artist
  - album/record
  - provider
  - Shazam URL/message
  - playback status
  - recognition status
  - listening/backoff/ratelimit flags
  - progress/time fields when known
  - RMS and silence threshold
  - Shazam request counters
- Publish availability/health with MQTT Last Will and Testament.
- No remote control in v1; this is telemetry only.
- Keep MQTT optional and disabled by default.
- Do not store MQTT credentials in committed config.
- Reuse the existing `now-playing.json` state file instead of coupling directly to the recognition loop.

## Current context

The repo already has:

- `rca_visualizer/recognition.py` writing `/var/lib/rca-hdmi-visualizer/now-playing.json`.
- `rca_visualizer/web_server.py` exposing `/api/now-playing` and `/api/config`.
- `rca_visualizer/config.py` loading `/etc/rca-hdmi-visualizer.env` and secrets.
- `rca_visualizer/defaults.py` defining `DEFAULT_STATE_PATH` and `DEFAULT_MIN_RMS`.
- `systemd/rca-now-playing.service` for recognition.
- `systemd/rca-now-playing-web.service` for the web UI/API.
- `/etc/rca-hdmi-visualizer.env` for runtime config.
- `/etc/rca-hdmi-visualizer.secrets` for secret material.

The MQTT bridge should be an independent process that watches/polls the state file, transforms state to MQTT payloads, and publishes to a broker.

## Proposed architecture

Add a small Python MQTT publisher daemon:

```text
/var/lib/rca-hdmi-visualizer/now-playing.json
        │
        ▼
rca-mqtt-bridge.service
        │
        ├── publishes Home Assistant MQTT discovery config
        ├── publishes state topics under rca_hdmivisualizer/<device_id>/...
        └── publishes availability topic with retained online/offline state
```

Use `paho-mqtt` for MQTT because it is widely available via apt/pip and is small. On Raspberry Pi OS/Debian, prefer apt package when available:

```bash
sudo apt install -y python3-paho-mqtt
```

If not available on the target distro, install into the project venv or document a fallback.

## Topic layout

Use a stable base topic:

```text
rca_hdmivisualizer/<device_id>/
```

Default `device_id`:

```text
musicman
```

Recommended topics:

```text
rca_hdmivisualizer/<device_id>/availability
rca_hdmivisualizer/<device_id>/state
rca_hdmivisualizer/<device_id>/track/title
rca_hdmivisualizer/<device_id>/track/artist
rca_hdmivisualizer/<device_id>/track/album
rca_hdmivisualizer/<device_id>/track/provider
rca_hdmivisualizer/<device_id>/track/url
rca_hdmivisualizer/<device_id>/playback/status
rca_hdmivisualizer/<device_id>/recognition/status
rca_hdmivisualizer/<device_id>/recognition/listening
rca_hdmivisualizer/<device_id>/recognition/backing_off
rca_hdmivisualizer/<device_id>/recognition/ratelimit
rca_hdmivisualizer/<device_id>/audio/rms
rca_hdmivisualizer/<device_id>/audio/silence_threshold
rca_hdmivisualizer/<device_id>/progress/current_seconds
rca_hdmivisualizer/<device_id>/progress/total_seconds
rca_hdmivisualizer/<device_id>/progress/percent
rca_hdmivisualizer/<device_id>/shazam/requests_total
rca_hdmivisualizer/<device_id>/shazam/requests_per_min
```

Also publish one retained aggregate JSON payload:

```text
rca_hdmivisualizer/<device_id>/state
```

Example aggregate payload:

```json
{
  "status": "recognized",
  "playback_status": "playing",
  "listening": false,
  "backing_off": false,
  "ratelimit": false,
  "title": "Sweet Revenge",
  "artist": "John Prine",
  "album": "Sweet Revenge",
  "provider": "shazam",
  "url": "https://www.shazam.com/track/...",
  "rms": 186.7,
  "silence_threshold": 20.0,
  "progress_current_seconds": 141.0,
  "progress_total_seconds": 178.6,
  "progress_percent": 78.9,
  "shazam_request_count": 12,
  "shazam_requests_per_min": 2.0,
  "updated_at": "2026-07-01T15:18:47.979339+00:00"
}
```

## Home Assistant entity model

Use MQTT Discovery and create these entities:

### Sensors

- `sensor.<device_id>_track_title`
- `sensor.<device_id>_artist`
- `sensor.<device_id>_album`
- `sensor.<device_id>_provider`
- `sensor.<device_id>_recognition_status`
- `sensor.<device_id>_playback_status`
- `sensor.<device_id>_rms`
- `sensor.<device_id>_silence_threshold`
- `sensor.<device_id>_progress_percent`
- `sensor.<device_id>_shazam_requests_total`
- `sensor.<device_id>_shazam_requests_per_min`

### Binary sensors

- `binary_sensor.<device_id>_playing`
- `binary_sensor.<device_id>_listening`
- `binary_sensor.<device_id>_backing_off`
- `binary_sensor.<device_id>_rate_limited`

### Optional media player

Consider a `media_player` entity later, but skip in v1 unless there is clear value. A Home Assistant MQTT media player expects control topics and command semantics; this appliance has no remote-control scope. For v1, sensors/binary sensors are cleaner and safer.

## Discovery topic layout

Default Home Assistant discovery prefix:

```text
homeassistant
```

Discovery topics:

```text
homeassistant/sensor/<device_id>/track_title/config
homeassistant/sensor/<device_id>/artist/config
homeassistant/sensor/<device_id>/album/config
homeassistant/sensor/<device_id>/rms/config
homeassistant/binary_sensor/<device_id>/playing/config
...
```

Each discovery payload should include:

```json
{
  "name": "Track Title",
  "unique_id": "rca_hdmivisualizer_musicman_track_title",
  "state_topic": "rca_hdmivisualizer/musicman/state",
  "value_template": "{{ value_json.title }}",
  "availability_topic": "rca_hdmivisualizer/musicman/availability",
  "payload_available": "online",
  "payload_not_available": "offline",
  "device": {
    "identifiers": ["rca_hdmivisualizer_musicman"],
    "name": "RCA HDMI Visualizer musicman",
    "manufacturer": "Ryan Kraus",
    "model": "RCA HDMI Visualizer Appliance",
    "sw_version": "git:<short_sha>"
  }
}
```

Use retained discovery messages so Home Assistant can rediscover after restart.

## Configuration

Add to `config/rca-hdmi-visualizer.env.example`:

```env
# MQTT / Home Assistant telemetry publishing.
MQTT_ENABLED=false
MQTT_HOST=
MQTT_PORT=1883
MQTT_CLIENT_ID=rca-hdmi-visualizer
MQTT_BASE_TOPIC=rca_hdmivisualizer
MQTT_HOME_ASSISTANT_DISCOVERY=true
MQTT_HOME_ASSISTANT_PREFIX=homeassistant
MQTT_DEVICE_ID=musicman
MQTT_DEVICE_NAME=RCA HDMI Visualizer
MQTT_PUBLISH_INTERVAL_SECONDS=2
MQTT_RETAIN_STATE=true
MQTT_TLS=false
MQTT_USERNAME=
# MQTT_PASSWORD belongs in /etc/rca-hdmi-visualizer.secrets, not this file.
```

Add to `config/rca-hdmi-visualizer.secrets.example`:

```env
MQTT_PASSWORD=
```

## Files likely to change

Create:

- `rca_visualizer/mqtt_bridge.py`
- `scripts/rca-mqtt-bridge`
- `systemd/rca-mqtt-bridge.service`
- `docs/home-assistant-mqtt.md`

Modify:

- `config/rca-hdmi-visualizer.env.example`
- `config/rca-hdmi-visualizer.secrets.example`
- `scripts/install.sh`
- `README.md`
- possibly `rca_visualizer/defaults.py` if MQTT defaults should be centralized there

Optional tests:

- `tests/test_mqtt_bridge.py` if adding Python test tooling is acceptable
- otherwise add a lightweight script/smoke command documented in `docs/home-assistant-mqtt.md`

## Implementation plan

### Phase 1: MQTT payload model

Create `rca_visualizer/mqtt_bridge.py` with pure functions first:

- `load_state(path)`
- `derive_progress(state)`
- `build_aggregate_payload(state, config)`
- `build_discovery_payloads(config)`
- `bool_payload(value)`

Do not connect to MQTT in the first task; keep mapping testable.

Important behavior:

- Missing fields should map to empty string, `0`, `false`, or `None` consistently.
- If `track_duration_ms` is missing or zero, progress fields should be `null` or `0` based on Home Assistant entity needs.
- Use `recognized_at + progress_start_seconds + wall time` to derive current progress, matching frontend behavior.
- Preserve stopped/listening states even when track metadata is blank.

### Phase 2: MQTT client wrapper

Add a small publisher class:

```python
class MqttPublisher:
    def __init__(self, config): ...
    def connect(self): ...
    def publish_discovery(self): ...
    def publish_state(self, payload): ...
    def close(self): ...
```

Use Last Will and Testament:

```text
availability_topic = <base>/<device_id>/availability
will payload = offline
online payload = online
```

Publishing strategy:

- Discovery topics retained.
- Aggregate state retained if `MQTT_RETAIN_STATE=true`.
- Availability retained.
- Avoid per-field topics at first unless needed; discovery value templates can read from aggregate JSON.

### Phase 3: Daemon loop

Daemon should:

1. Load env/secrets via existing `RuntimeConfig.load()`.
2. Exit cleanly with message if `MQTT_ENABLED` is false.
3. Connect to MQTT broker.
4. Publish Home Assistant discovery once on startup if enabled.
5. Publish `online` availability.
6. Poll state file every `MQTT_PUBLISH_INTERVAL_SECONDS`.
7. Publish only when aggregate payload changes, plus a heartbeat every 60 seconds.
8. On shutdown, publish `offline`.

### Phase 4: Scripts and systemd

Create `scripts/rca-mqtt-bridge`:

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python3 -m rca_visualizer.mqtt_bridge "$@"
```

Create `systemd/rca-mqtt-bridge.service`:

```ini
[Unit]
Description=MQTT/Home Assistant telemetry bridge for RCA HDMI visualizer
After=network-online.target rca-now-playing.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-/etc/rca-hdmi-visualizer.env
EnvironmentFile=-/etc/rca-hdmi-visualizer.secrets
ExecStart=/usr/local/bin/rca-mqtt-bridge
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Service should be installed but not enabled unless `MQTT_ENABLED=true` or user explicitly enables it.

### Phase 5: Installer integration

Modify `scripts/install.sh`:

- Install `python3-paho-mqtt` where apt is available.
- Copy `scripts/rca-mqtt-bridge` to `/usr/local/bin/`.
- Copy `systemd/rca-mqtt-bridge.service` to `/etc/systemd/system/`.
- Ensure example config contains MQTT defaults for new installs.
- Do not overwrite existing `/etc/rca-hdmi-visualizer.env` or secrets.
- Optionally append missing MQTT keys to existing env during install, preserving values.

### Phase 6: Documentation

Create `docs/home-assistant-mqtt.md` with:

- Mosquitto/Home Assistant MQTT prerequisites.
- Example `/etc/rca-hdmi-visualizer.env` settings.
- Example `/etc/rca-hdmi-visualizer.secrets` settings.
- How to enable service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rca-mqtt-bridge.service
```

- How to verify with `mosquitto_sub`:

```bash
mosquitto_sub -h <broker> -t 'rca_hdmivisualizer/#' -v
mosquitto_sub -h <broker> -t 'homeassistant/#' -v | grep rca_hdmivisualizer
```

- How to remove/recreate discovery entities:
  - disable service
  - delete retained discovery topics if necessary
  - restart Home Assistant MQTT integration

## Validation plan

### Static checks

```bash
python3 -m compileall -q rca_visualizer
python3 -m py_compile rca_visualizer/mqtt_bridge.py
bash -n scripts/rca-mqtt-bridge scripts/install.sh
git diff --check
```

### Local dry-run test

Add a CLI flag:

```bash
python3 -m rca_visualizer.mqtt_bridge --dry-run --state tests/fixtures/now-playing-recognized.json
```

Expected:

- Prints aggregate state JSON.
- Prints discovery topic count.
- Does not connect to broker.

### Broker integration test

Using local Mosquitto:

```bash
docker run --rm -p 1883:1883 eclipse-mosquitto:2
MQTT_ENABLED=true MQTT_HOST=127.0.0.1 python3 -m rca_visualizer.mqtt_bridge --once --state /tmp/sample-now-playing.json
mosquitto_sub -h 127.0.0.1 -t 'rca_hdmivisualizer/#' -C 5 -v
```

If Docker is unavailable, test against Home Assistant Mosquitto add-on or a local `mosquitto` package.

### Target appliance verification

On the appliance:

```bash
sudo systemctl restart rca-mqtt-bridge.service
systemctl is-active rca-mqtt-bridge.service
journalctl -u rca-mqtt-bridge.service -n 100 --no-pager
```

From another machine:

```bash
mosquitto_sub -h <broker> -t 'rca_hdmivisualizer/#' -v
```

In Home Assistant:

- Confirm entities appear automatically.
- Confirm track title/artist/album update after recognition.
- Confirm `playing`, `listening`, `rate_limited`, and `backing_off` binary sensors reflect state.
- Confirm RMS and silence threshold match kiosk status bar.
- Confirm device availability goes offline when service is stopped.

## Security and privacy

- MQTT broker credentials must not be committed.
- Discovery/state payloads include listening metadata and track names; assume this is private home-network data.
- Do not expose MQTT broker to public internet.
- Prefer Home Assistant Mosquitto add-on or LAN/Tailscale broker.
- Use TLS only if broker is not trusted LAN/Tailscale; document but do not require for v1.

## Risks and mitigations

### Retained stale metadata

Risk: Home Assistant could show old track after appliance stops.

Mitigation:

- Publish blank title/artist/album when state says `stopped` and recognition has cleared fields.
- Publish retained aggregate state so HA catches up after restart.

### Discovery entity churn

Risk: Changing `unique_id` or topic layout creates duplicate entities.

Mitigation:

- Centralize `device_id`, `base_topic`, and `unique_id` generation.
- Do not change after release without migration notes.

### MQTT dependency availability

Risk: `python3-paho-mqtt` package name/version differs by distro.

Mitigation:

- Installer should try apt package first.
- Document pip/venv fallback only if apt unavailable.

### State file race

Risk: MQTT bridge reads while recognition daemon writes JSON.

Mitigation:

- `load_state` should catch JSON parse errors and retry next interval.
- Do not crash on transient bad reads.

### Too much MQTT traffic

Risk: Publishing every 2 seconds creates unnecessary retained writes.

Mitigation:

- Publish only on payload changes plus heartbeat.
- Use `MQTT_PUBLISH_INTERVAL_SECONDS` and `MQTT_HEARTBEAT_SECONDS`.

## Open questions

1. Should Home Assistant show the appliance as a `media_player` despite no remote control?
   - Recommendation: no for v1; use sensors/binary sensors.
2. Should MQTT expose Shazam URL as a sensor or an attribute?
   - Recommendation: include in aggregate state; create a sensor only if useful.
3. Should progress update every second over MQTT?
   - Recommendation: no; MQTT progress every 2–5 seconds is enough. The kiosk can update progress locally every second.
4. Should MQTT include album art if Shazam provides it later?
   - Recommendation: leave as future enhancement.

## Acceptance criteria

- MQTT bridge can be installed without enabling MQTT by default.
- When enabled and configured, it publishes availability and aggregate state.
- Home Assistant auto-discovers sensors and binary sensors.
- MQTT credentials remain outside git.
- Existing recognition, web UI, kiosk, and audio loopback behavior is unchanged.
- Documentation includes broker setup, env config, service enablement, and verification commands.
