# Android ShazamKit Appliance Migration Plan

**Goal:** Preserve a future path to rebuild the RCA/HDMI now-playing appliance as an Android/LineageOS-based kiosk app so it can use Apple's official ShazamKit catalog recognition instead of unofficial Shazam-compatible Python clients.

**Scope:** Planning only. This plan documents a fallback/optional migration path if Linux-local Shazam recognition becomes unreliable, ethically undesirable, or materially worse than official ShazamKit. It does not implement the Android app yet.

## Why consider this

The current Linux appliance works and should remain the main path for the Raspberry Pi 5 + HiFiBerry DAC2 ADC Pro hardware. However, official Shazam catalog recognition is exposed through ShazamKit on Apple platforms and Android, not as a general Linux/server REST API.

An Android rebuild could provide:

- official ShazamKit recognition against Shazam's music catalog
- no dependency on unofficial `shazamio`/reverse-engineered clients
- an all-in-one fullscreen kiosk app
- in-app audio pass-through from input to output
- native Android app lifecycle/kiosk controls

## Current context

Current appliance stack:

- Raspberry Pi 5 target hardware is planned.
- HiFiBerry DAC2 ADC Pro was purchased for high-quality stereo line input/output.
- Current deployed implementation is Linux/Python/browser-based:
  - `rca_visualizer/recognition.py`
  - `rca_visualizer/shazam_lookup.py`
  - `rca_visualizer/web/`
  - systemd services under `systemd/`
- Current recognizer uses local Shazam-style matching and writes:
  - `/var/lib/rca-hdmi-visualizer/now-playing.json`
- Current UI expects normalized now-playing state fields such as:
  - `status`
  - `playback_status`
  - `title`
  - `artist`
  - `album`
  - `track_duration_ms`
  - `match_offset_seconds`
  - `progress_start_seconds`
  - `rms`
  - `provider`
  - `message`

## Key decision

Do not switch to Android immediately.

Keep the main implementation on Raspberry Pi OS unless one of these happens:

1. local Shazam recognition breaks frequently,
2. unofficial recognition becomes unacceptable,
3. official ShazamKit recognition becomes a project priority,
4. LineageOS proves it can reliably capture the chosen line-input hardware,
5. Android app reliability/kiosk behavior proves better than Linux for this appliance.

## Proposed Android architecture

```text
Android / LineageOS on Raspberry Pi 5
        │
        ▼
Fullscreen Android kiosk app
        │
        ├── Audio input capture
        │     ├── preferred: Android AudioRecord
        │     └── fallback: native tinyalsa/ALSA capture if AudioRecord cannot see the HAT
        │
        ├── Audio pass-through
        │     └── AudioTrack output to HDMI / USB / DAC
        │
        ├── RMS/silence gate
        │
        ├── ShazamKit Android SDK
        │     ├── SignatureGenerator or StreamingSession
        │     └── ShazamCatalog via Apple Developer token
        │
        ├── normalized NowPlayingState model
        │
        ├── fullscreen Vestaboard-style UI
        │
        └── optional MQTT/Home Assistant publisher
```

## Platform options

### Option A: LineageOS/KonstaKANG on Raspberry Pi 5

Pros:

- Runs on the Pi 5 already purchased.
- Supports Android apps and likely ShazamKit SDK integration.
- Existing KonstaKANG Pi 5 builds list HDMI, USB audio, hardware graphics, USB, Wi-Fi, and Ethernet support.
- Keeps appliance hardware compact.

Cons:

- Unofficial build, unsupported by LineageOS team.
- Personal/non-commercial licensing caveats in KonstaKANG builds.
- HiFiBerry DAC2 ADC Pro capture support is uncertain.
- Android audio HAL/policy may not expose HAT ADC input to apps.
- Custom patches may be needed and would create maintenance burden.

### Option B: Android device/TV box + USB audio interface

Pros:

- Much more likely to expose USB audio input through Android APIs.
- Less kernel/HAT-specific work.
- Official ShazamKit Android path still applies.

Cons:

- Does not use the purchased Pi 5 + HiFiBerry hardware well.
- Less integrated hi-fi appliance feel.
- Hardware quality/boot/kiosk behavior varies by vendor.

### Option C: Stay Linux and use official non-Shazam recognition vendor

Pros:

- Keeps Raspberry Pi OS and HiFiBerry path.
- Avoids Android rebuild.
- Could use APIs designed for server/Linux integrations, e.g. AudD or ACRCloud.

Cons:

- Not Shazam's catalog.
- May cost more or match live vinyl differently.
- Another vendor dependency.

## Known risk: HiFiBerry ADC on Android

The central risk is whether Android can capture from the HiFiBerry DAC2 ADC Pro.

Possible outcomes:

### Best case

Android exposes the HiFiBerry input via `AudioRecord`.

Then the Android app can directly:

- capture stereo PCM,
- pass stereo through to output,
- downmix to mono for ShazamKit,
- compute RMS,
- update the kiosk UI.

### Medium case

The Linux/Android kernel sees the HiFiBerry ALSA capture device, but Android does not expose it through normal audio APIs.

Potential fixes:

- patch Android audio policy XML,
- patch mixer paths XML,
- add a product/vendor overlay,
- route the ALSA capture device as a line-in/microphone source,
- or bypass `AudioRecord` and capture with native tinyalsa.

### Hard case

The kernel/device tree does not bring up the HiFiBerry ADC at all.

Potential fixes:

- add/enable `dtoverlay` support,
- enable I2S,
- add missing `.dtbo`,
- rebuild the Android kernel with required codec/machine drivers,
- backport Raspberry Pi OS HiFiBerry driver support.

### Hardest case

ALSA capture works only with custom native access, and SELinux/app permissions block it.

Potential fixes:

- install the app as privileged/system app,
- use Magisk/root for the appliance,
- adjust SELinux policy if building a custom image,
- keep SELinux permissive if the chosen LineageOS build already uses permissive mode.

## Audio pass-through design

If input and output are accessible, the app should perform pass-through in process:

```text
stereo input
  ├── stereo PCM → AudioTrack output
  └── downmixed mono PCM → ShazamKit + RMS gate
```

Expected implementation:

- use `AudioRecord` for input when possible,
- use `AudioTrack` for output,
- run capture/playback on a high-priority thread,
- use a small ring buffer to reduce glitches,
- keep ShazamKit processing separate so recognition latency does not block audio output.

Rough latency expectations:

- best case: 20-60 ms,
- typical generic Android/Pi case: 80-200 ms,
- unacceptable case: chunky HAL/buffer path causing audible delay or dropouts.

For casual vinyl listening, modest latency is acceptable. For DJ/live-monitoring use, it may not be.

## ShazamKit integration requirements

The Android app will need:

- Apple Developer account access.
- ShazamKit Android SDK `.aar`.
- ShazamKit service/media identifier configured in Apple developer portal.
- Private key for developer token generation.
- A `DeveloperTokenProvider` implementation.
- `RECORD_AUDIO` permission.
- Network access for Shazam catalog matching.

Audio format expected by ShazamKit Android:

- PCM 16-bit mono,
- supported sample rates include common rates such as 44.1kHz and 48kHz,
- use `SignatureGenerator` for sample-based matching or `StreamingSession` for live recognition.

## UI approach

Start with the least risky UI approach:

1. Build native Android app shell in Kotlin.
2. Use a WebView to render a port of the current Vestaboard UI initially.
3. Pass normalized `NowPlayingState` from Kotlin to WebView via JavaScript bridge.
4. If WebView performance or styling becomes a problem, rebuild the display in Jetpack Compose.

This preserves current visual design while moving audio/recognition into Android.

## Normalized state model

Define an Android/Kotlin equivalent of the current JSON state:

```kotlin
data class NowPlayingState(
    val status: String,
    val playbackStatus: String,
    val listening: Boolean,
    val backingOff: Boolean,
    val ratelimit: Boolean,
    val title: String,
    val artist: String,
    val album: String,
    val provider: String,
    val recognizedAt: Instant?,
    val durationSeconds: Int,
    val rms: Double?,
    val score: Double,
    val trackDurationMs: Long,
    val matchOffsetSeconds: Double?,
    val progressStartSeconds: Double?,
    val progressPaddingSeconds: Double,
    val message: String,
    val raw: Map<String, Any?>
)
```

The Android app should preserve the semantics the current UI depends on:

- `recognized` means display track metadata.
- `stopped` means silence/no playback.
- `listening` should not blank current song during rechecks.
- progress should continue locally between recognition attempts.
- no-match during mid-track recheck should keep the previous recognized song visible when reasonable.

## Feasibility spike plan

Do not build the full app first. Run a focused spike when hardware is available.

### Spike 1: Boot and basic LineageOS viability

- Flash latest suitable KonstaKANG LineageOS/Android TV build for Pi 5 to a spare SD card.
- Boot Pi 5 with HDMI display, keyboard/mouse, Ethernet/Wi-Fi.
- Enable ADB/SSH if available.
- Verify stable boot, display resolution, hardware graphics, and thermal behavior.

Success criteria:

- Pi 5 boots reliably.
- HDMI display works at target resolution.
- ADB or another developer access path works.
- UI is stable for at least 30 minutes.

### Spike 2: HiFiBerry kernel visibility

Using `adb shell`, check:

```sh
cat /proc/asound/cards
cat /proc/asound/pcm
ls /dev/snd
```

Success criteria:

- HiFiBerry card appears.
- A capture PCM device appears.

If this fails, inspect kernel logs:

```sh
dmesg | grep -i -E 'hifi|snd|audio|i2s|pcm|codec|bcm'
```

### Spike 3: Capture audio without Android APIs

If ALSA devices exist, test native capture with available tools:

```sh
tinycap /sdcard/hifiberry-test.wav -D <card> -d <device> -r 48000 -b 16 -c 2 -T 5
```

If `tinycap` is unavailable, install/use a small NDK test binary or check whether `arecord`/alsa-utils exists in the build.

Success criteria:

- 5-second capture file is created.
- Playback or file analysis confirms real line input, not silence.

### Spike 4: Android `AudioRecord` visibility

Build a minimal Android test app that:

- lists `AudioManager.getDevices(AudioManager.GET_DEVICES_INPUTS)`,
- opens `AudioRecord`,
- records 5 seconds,
- displays RMS in real time,
- saves a WAV for inspection.

Success criteria:

- app sees an input device corresponding to HiFiBerry or generic line-in/mic,
- RMS responds to RCA input,
- saved WAV contains clean audio.

### Spike 5: Audio pass-through

Extend the test app to:

- capture stereo input,
- write it to `AudioTrack`,
- measure subjective latency/glitches,
- keep screen awake/fullscreen.

Success criteria:

- stable audio output for at least 30 minutes,
- no obvious dropouts,
- latency acceptable for casual listening.

### Spike 6: ShazamKit recognition

Add ShazamKit SDK and Apple developer token support.

Test:

- generate signature from a 5-second mono downmix,
- match against Shazam catalog,
- inspect returned metadata and match timing fields,
- test live vinyl snippets.

Success criteria:

- recognizes known tracks from live line input,
- returns title/artist reliably,
- returns enough timing/duration metadata for progress display or a workable fallback.

## Implementation phases if spike succeeds

### Phase 1: Minimal Android recognizer app

- Kotlin app project.
- Fullscreen Activity.
- Audio capture + RMS display.
- ShazamKit recognition button/manual trigger.
- Display raw match result.

### Phase 2: Appliance recognition loop

- Silence gate.
- 5-second sample recognition loop.
- No-match/backoff logic.
- Mid-track recheck logic.
- Progress estimate logic.
- Normalized state model.

### Phase 3: Audio pass-through

- Continuous input-to-output loop.
- Stereo output path.
- Mono downmix path for ShazamKit.
- Buffer and latency tuning.
- Output device selection if Android exposes multiple devices.

### Phase 4: Kiosk UI

- Port existing Vestaboard display to WebView or Jetpack Compose.
- Keep timer/progress local updates.
- Preserve bottom `PLAY / STOP / LISTEN` lamps.
- Preserve RMS/threshold status line.
- Keep no-blank behavior during rechecks.

### Phase 5: Appliance boot behavior

- Autostart app after boot.
- Keep screen awake.
- Lock task / kiosk mode if practical.
- Disable sleep/blanking.
- Recovery/ADB access path.
- Optional watchdog/restart strategy.

### Phase 6: Optional integrations

- MQTT/Home Assistant telemetry from Android app.
- Local HTTP endpoint for debugging if useful.
- Export logs/state for troubleshooting.

## Files likely to be created in a future Android branch

Possible repository layout:

```text
android-shazamkit-appliance/
  settings.gradle.kts
  build.gradle.kts
  app/
    build.gradle.kts
    src/main/AndroidManifest.xml
    src/main/java/com/rmkraus/rcavisualizer/MainActivity.kt
    src/main/java/com/rmkraus/rcavisualizer/audio/AudioLoop.kt
    src/main/java/com/rmkraus/rcavisualizer/audio/RmsMeter.kt
    src/main/java/com/rmkraus/rcavisualizer/recognition/ShazamKitRecognizer.kt
    src/main/java/com/rmkraus/rcavisualizer/state/NowPlayingState.kt
    src/main/java/com/rmkraus/rcavisualizer/ui/KioskWebView.kt
    src/main/assets/web/
      index.html
      app.js
      styles.css
```

Keep this separate from the current Linux app until the spike proves viability.

## Validation checklist for a real migration

- [ ] LineageOS boots reliably on Pi 5.
- [ ] HDMI display works fullscreen at target resolution.
- [ ] App can autostart into kiosk mode.
- [ ] HiFiBerry ADC capture works, or a USB audio replacement is accepted.
- [ ] Audio pass-through is stable for at least 2 hours.
- [ ] Recognition works with 5-second live vinyl snippets.
- [ ] Recognition does not require switching to the Shazam app/window.
- [ ] ShazamKit metadata includes enough fields for title/artist/album/progress needs.
- [ ] App can keep current song visible during rechecks.
- [ ] Thermal behavior is acceptable in the target case.
- [ ] Android build/update/recovery process is documented.

## Risks and tradeoffs

- HiFiBerry ADC input may not be available through Android `AudioRecord`.
- Native tinyalsa capture may need privileged app/root/SELinux work.
- Custom Android kernel/audio HAL patches may be too much maintenance.
- KonstaKANG builds are unofficial and not commercial-use licensed by default.
- Android background microphone rules complicate hidden/background operation, though foreground kiosk use should be fine.
- Raspberry Pi OS remains a better-supported path for the HiFiBerry board.
- ShazamKit is official but requires Apple Developer setup and token handling.
- WebView/Android rendering performance should be tested; native Compose may be needed later.

## Recommendation

Treat Android/ShazamKit as a fallback plan, not the primary path.

When the Pi 5 arrives:

1. Build the Raspberry Pi OS + HiFiBerry appliance first.
2. If local Shazam recognition becomes problematic, run the LineageOS feasibility spikes on a spare SD card.
3. Only commit to the Android rewrite if HiFiBerry capture and ShazamKit recognition both work reliably.

If HiFiBerry capture fails on Android and official ShazamKit remains important, the simplest Android hardware pivot is a class-compliant USB stereo audio interface rather than custom Android HAT support.
