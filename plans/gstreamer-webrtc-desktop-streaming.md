# GStreamer WebRTC Desktop Streaming Plan

**Goal:** Add a receive-only remote viewing path for the appliance: stream the local kiosk desktop video plus appliance audio over WebRTC, with no keyboard/mouse/control channel.

**Scope:** Planning only. This plan describes the implementation steps for a future PR. It does not implement the streaming service yet.

## Requirements

- Stream the appliance display as video.
- Stream appliance audio output/capture monitor as audio.
- Use WebRTC so a remote browser can attach to the stream.
- No remote control, no keyboard/mouse injection, no data-channel commands.
- Run locally as systemd services alongside the existing now-playing stack.
- Prefer LAN/Tailscale access first; avoid public exposure by default.
- Keep existing kiosk, recognition, web UI, and audio loopback behavior unchanged.

## Current context

The repo already has:

- `rca-now-playing.service` for recognition.
- `rca-now-playing-web.service` for the local HTTP UI/API.
- `rca-now-playing-kiosk.service` for Chromium kiosk display.
- `rca-audio-loopback.service` for RCA/USB audio to HDMI/audio output.
- `/etc/rca-hdmi-visualizer.env` as runtime configuration.

The new streaming feature should be additive and disabled by default until configured/tested.

## Proposed architecture

Use GStreamer on the appliance to capture:

- Video: X11 desktop via `ximagesrc` on `DISPLAY=:0` initially.
- Audio: PulseAudio/PipeWire monitor source via `pulsesrc` initially.

Encode:

- Video: VP8 first for maximum browser compatibility and easiest GStreamer setup.
- Audio: Opus.

Transport:

- Use GStreamer `webrtcbin` for WebRTC media transport.
- Add a small local Python signaling/web service that serves a viewer page and WebSocket signaling.
- Browser connects to `http://appliance:PORT/`, receives SDP/ICE, and plays the stream.

High-level processes:

```text
rca-stream-signaling.service
  Python HTTP/WebSocket signaling server + static viewer page

rca-desktop-webrtc.service
  GStreamer sender process connects to signaling server as producer

remote browser
  Opens viewer page, connects as viewer, receives audio/video only
```

## Recommended first implementation path

Use Python rather than shell-only `gst-launch`, because WebRTC needs signaling, SDP exchange, and ICE candidate handling.

Recommended implementation files:

- `rca_visualizer/streaming/__init__.py`
- `rca_visualizer/streaming/signaling.py`
- `rca_visualizer/streaming/gstreamer_sender.py`
- `rca_visualizer/streaming/web/index.html`
- `rca_visualizer/streaming/web/viewer.js`
- `scripts/rca-stream-signaling`
- `scripts/rca-desktop-webrtc`
- `systemd/rca-stream-signaling.service`
- `systemd/rca-desktop-webrtc.service`
- `docs/remote-streaming.md`

## Configuration

Add optional environment variables to `config/rca-hdmi-visualizer.env.example`:

```env
# Remote view-only desktop/audio streaming over WebRTC.
STREAMING_ENABLED=false
STREAMING_HOST=0.0.0.0
STREAMING_PORT=8876
STREAMING_SIGNALING_URL=ws://127.0.0.1:8876/ws
STREAMING_STUN_SERVER=stun://stun.l.google.com:19302
STREAMING_DISPLAY=:0
STREAMING_XAUTHORITY=
STREAMING_VIDEO_WIDTH=1280
STREAMING_VIDEO_HEIGHT=720
STREAMING_VIDEO_FRAMERATE=15
STREAMING_VIDEO_BITRATE_KBPS=1800
STREAMING_AUDIO_SOURCE=
STREAMING_AUDIO_BITRATE=96000
STREAMING_REQUIRE_TOKEN=true
STREAMING_VIEW_TOKEN=
```

Notes:

- Default to 720p/15fps to protect Pi/Jetson thermals.
- `STREAMING_AUDIO_SOURCE` should accept an explicit PulseAudio source or monitor source.
- If unset, sender should attempt to auto-detect the current default sink monitor.
- `STREAMING_VIEW_TOKEN` should live in `/etc/rca-hdmi-visualizer.secrets`, not committed defaults.

## GStreamer package dependencies

For Raspberry Pi OS / Debian Bookworm, likely packages:

```bash
sudo apt install -y \
  python3-gi \
  python3-gst-1.0 \
  python3-websockets \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-nice
```

Verify plugins:

```bash
gst-inspect-1.0 webrtcbin
gst-inspect-1.0 ximagesrc
gst-inspect-1.0 pulsesrc
gst-inspect-1.0 vp8enc
gst-inspect-1.0 opusenc
gst-inspect-1.0 rtpvp8pay
gst-inspect-1.0 rtpopuspay
```

## Sender pipeline design

Initial VP8/Opus pipeline shape:

```text
webrtcbin name=webrtc bundle-policy=max-bundle stun-server=$STREAMING_STUN_SERVER

ximagesrc display-name=$DISPLAY use-damage=false show-pointer=false
  ! video/x-raw,framerate=$FPS/1
  ! videoconvert
  ! videoscale
  ! video/x-raw,width=$WIDTH,height=$HEIGHT
  ! queue max-size-buffers=2 leaky=downstream
  ! vp8enc deadline=1 keyframe-max-dist=30 target-bitrate=$BITRATE
  ! rtpvp8pay picture-id-mode=15 pt=96
  ! application/x-rtp,media=video,encoding-name=VP8,payload=96
  ! webrtc.

pulsesrc device=$AUDIO_SOURCE
  ! audio/x-raw,channels=2,rate=48000
  ! audioconvert
  ! audioresample
  ! queue max-size-buffers=8 leaky=downstream
  ! opusenc bitrate=$AUDIO_BITRATE
  ! rtpopuspay pt=97
  ! application/x-rtp,media=audio,encoding-name=OPUS,payload=97
  ! webrtc.
```

Implementation detail: build this using PyGObject/GStreamer rather than raw `gst-launch`, so the sender can:

- connect `on-negotiation-needed`
- create SDP offers
- send ICE candidates
- receive browser SDP answers
- add browser ICE candidates
- reconnect after viewer disconnects
- stop streaming when no viewer is attached, if desired

## Signaling server design

Implement a minimal HTTP/WebSocket service:

- `GET /` serves viewer HTML.
- `GET /viewer.js` serves browser client JS.
- `GET /healthz` returns service state.
- `WS /ws` handles signaling.

Roles:

- sender process connects with `{"role":"producer"}`.
- browser connects with `{"role":"viewer", "token":"..."}`.
- signaling server relays SDP and ICE between exactly one producer and one or more viewers.

First version can support one viewer at a time to reduce complexity. Multi-viewer support can be a later phase.

Message examples:

```json
{"type":"hello","role":"producer"}
{"type":"hello","role":"viewer","token":"..."}
{"type":"offer","sdp":"..."}
{"type":"answer","sdp":"..."}
{"type":"ice","candidate":"...","sdpMLineIndex":0}
{"type":"viewer-disconnected"}
```

Security defaults:

- Bind to Tailscale/LAN only when possible.
- Require a token by default.
- No data channel for remote commands.
- Do not expose on public internet without HTTPS/auth/TURN review.

## Browser viewer design

`viewer.js` should:

- open WebSocket to `/ws`
- send viewer hello + token
- create `RTCPeerConnection`
- receive sender offer
- set remote description
- create/send answer
- relay ICE candidates
- attach remote tracks to a `<video autoplay playsinline controls>` element
- show connection state: waiting, connecting, connected, disconnected, failed

Viewer HTML should stay simple and local:

```html
<video id="stream" autoplay playsinline controls></video>
<div id="status">Waiting for stream...</div>
```

No UI for keyboard/mouse/control.

## Service ordering

Add two new services:

`rca-stream-signaling.service`:

```ini
[Unit]
Description=Remote WebRTC view-only signaling server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-/etc/rca-hdmi-visualizer.env
EnvironmentFile=-/etc/rca-hdmi-visualizer.secrets
ExecStart=/usr/local/bin/rca-stream-signaling --host ${STREAMING_HOST} --port ${STREAMING_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`rca-desktop-webrtc.service`:

```ini
[Unit]
Description=View-only desktop/audio WebRTC sender
After=rca-stream-signaling.service rca-now-playing-kiosk.service rca-audio-loopback.service
Wants=rca-stream-signaling.service

[Service]
Type=simple
EnvironmentFile=-/etc/rca-hdmi-visualizer.env
EnvironmentFile=-/etc/rca-hdmi-visualizer.secrets
ExecStart=/usr/local/bin/rca-desktop-webrtc
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
```

Guard startup on `STREAMING_ENABLED=true` either in the wrapper script or unit `ExecStartPre`.

## Install script updates

Modify `scripts/install.sh` to:

1. Install GStreamer/WebSocket dependencies only when platform supports apt.
2. Copy new scripts into `/usr/local/bin/`.
3. Copy new systemd unit files.
4. Add default streaming config keys if missing.
5. Do not enable streaming services by default unless `STREAMING_ENABLED=true`.

## Test plan

### Unit / static checks

Run locally:

```bash
python3 -m compileall -q rca_visualizer
bash -n scripts/rca-stream-signaling scripts/rca-desktop-webrtc scripts/install.sh
git diff --check
```

### Plugin readiness check

On target appliance:

```bash
gst-inspect-1.0 webrtcbin ximagesrc pulsesrc vp8enc opusenc rtpvp8pay rtpopuspay
```

Expected: all elements found.

### Signaling server smoke test

```bash
systemctl restart rca-stream-signaling.service
systemctl is-active rca-stream-signaling.service
curl -fsS http://127.0.0.1:8876/healthz
curl -fsS http://127.0.0.1:8876/ | grep -i video
```

### Sender smoke test

```bash
systemctl restart rca-desktop-webrtc.service
systemctl is-active rca-desktop-webrtc.service
journalctl -u rca-desktop-webrtc.service -n 100 --no-pager
```

Expected: sender connects to signaling server and waits for viewer.

### Browser verification

From laptop on same Tailscale/LAN:

```text
http://<appliance-tailscale-ip>:8876/?token=<token>
```

Verify:

- video appears within a few seconds
- audio plays
- no remote control is possible
- disconnect/reconnect works
- kiosk display itself is unaffected

### Performance verification

On target appliance while streaming:

```bash
top -b -n 1 | head -30
tegrastats  # Jetson only
vcgencmd measure_temp  # Pi only
```

Check:

- CPU stays acceptable at 720p/15fps.
- No thermal throttling.
- Recognition service still detects tracks.
- Kiosk UI stays smooth enough.

## Risks and mitigations

### Browser/Chromium load plus encoder load

Risk: Pi 5 may heat/throttle if kiosk + GStreamer encode are too heavy.

Mitigation:

- Default to 720p/15fps.
- Allow bitrate/framerate config.
- Consider H.264 hardware encode later if VP8 is too expensive.

### X11 vs Wayland

Risk: `ximagesrc` works on X11 but not Wayland.

Mitigation:

- Keep kiosk session on X11 for first version.
- Document X11 requirement.
- Future: PipeWire screen capture for Wayland.

### Audio source selection

Risk: wrong monitor source produces silence.

Mitigation:

- Add `STREAMING_AUDIO_SOURCE` config.
- Add discovery command docs:
  `pactl list short sources`
- Log selected source at sender startup.

### NAT traversal

Risk: WebRTC may fail outside LAN/Tailscale without TURN.

Mitigation:

- First target Tailscale/LAN.
- STUN only for phase 1.
- Add TURN config later if truly needed.

### Security

Risk: exposing live appliance audio/video.

Mitigation:

- Token required by default.
- Bind to Tailscale/LAN, not public WAN.
- No control data channel.
- Document firewall expectations.

## Open questions

1. Should the stream include HDMI/output audio monitor, raw line-in capture, or both?
   - Recommendation: stream HDMI/output monitor for remote viewing; recognition continues to use line-in capture.
2. Is one viewer enough?
   - Recommendation: one viewer for v1.
3. Should streaming start only when a viewer connects?
   - Recommendation: producer can idle connected; encoding should ideally start/stop with viewer in v2.
4. Should we support Pi 5 hardware H.264 encode immediately?
   - Recommendation: start VP8 for simplicity; optimize later if CPU is high.

## Implementation phases

### Phase 1: local viewer/signaling skeleton

- Add static viewer page and WebSocket signaling server.
- Verify browser can connect and show status.

### Phase 2: GStreamer sender with test sources

- Use `videotestsrc` and `audiotestsrc` first.
- Verify WebRTC attach works in browser.

### Phase 3: desktop/audio capture

- Replace test sources with `ximagesrc` and `pulsesrc`.
- Verify kiosk video and audio reach browser.

### Phase 4: systemd/install/config

- Add scripts, units, install integration, docs.
- Keep disabled by default.

### Phase 5: performance tuning

- Tune resolution/framerate/bitrate.
- Consider H.264 or lower frame rate if Pi overheats.

## Acceptance criteria

- A remote browser can connect to the appliance over LAN/Tailscale and view live desktop video plus audio.
- Remote page has no control path.
- Stream services can be disabled without affecting now-playing appliance services.
- Streaming defaults are conservative enough for Raspberry Pi 5.
- Documentation includes setup, config, verification, and troubleshooting.
