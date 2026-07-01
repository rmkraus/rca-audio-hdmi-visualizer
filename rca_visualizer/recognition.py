import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from .config import RuntimeConfig
from .defaults import (
    DEFAULT_CHANNELS,
    DEFAULT_MAX_RECHECK_WAIT_SECONDS,
    DEFAULT_MIN_RMS,
    DEFAULT_MISSING_DURATION_RECHECK_SECONDS,
    DEFAULT_NO_MATCH_BACKOFF_SECONDS,
    DEFAULT_NO_MATCH_LIMIT,
    DEFAULT_PROGRESS_OFFSET_PADDING_SECONDS,
    DEFAULT_PROGRESS_RESUME_PERCENT,
    DEFAULT_RATELIMIT_BACKOFF_SECONDS,
    DEFAULT_RATELIMIT_REQUESTS_PER_MIN,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_SECONDS,
    DEFAULT_STATE_PATH,
)

SHAZAM_LOOKUP_SCRIPT = "/opt/rca-hdmi-visualizer/rca_visualizer/shazam_lookup.py"
SHAZAM_VENV_PYTHON = "/opt/rca-hdmi-visualizer/shazam-venv/bin/python"


class RecognitionResult:
    def __init__(
        self,
        status,
        title="",
        artist="",
        album="",
        score=0.0,
        provider="shazam",
        recognized_at="",
        duration=0,
        acoustid="",
        musicbrainz_recording_id="",
        track_duration_ms=0,
        match_offset_seconds=None,
        progress_start_seconds=None,
        progress_padding_seconds=0,
        playback_status="",
        listening=False,
        backing_off=False,
        ratelimit=False,
        shazam_request_count=0,
        shazam_requests_per_min=0.0,
        rms=None,
        raw=None,
        message="",
    ):
        self.status = status
        self.title = title
        self.artist = artist
        self.album = album
        self.score = score
        self.provider = provider
        self.recognized_at = recognized_at
        self.duration = duration
        # Kept for backward-compatible state JSON. For Shazam this stores the
        # Shazam track key, not an AcoustID UUID.
        self.acoustid = acoustid
        self.musicbrainz_recording_id = musicbrainz_recording_id
        self.track_duration_ms = int(track_duration_ms or 0)
        self.match_offset_seconds = match_offset_seconds
        self.progress_start_seconds = progress_start_seconds
        self.progress_padding_seconds = progress_padding_seconds
        self.playback_status = playback_status
        self.listening = bool(listening)
        self.backing_off = bool(backing_off)
        self.ratelimit = bool(ratelimit)
        self.shazam_request_count = int(shazam_request_count or 0)
        self.shazam_requests_per_min = float(shazam_requests_per_min or 0.0)
        self.rms = rms
        self.raw = raw
        self.message = message

    def to_dict(self):
        return {
            "status": self.status,
            "playback_status": self.playback_status,
            "listening": self.listening,
            "backing_off": self.backing_off,
            "ratelimit": self.ratelimit,
            "shazam_request_count": self.shazam_request_count,
            "shazam_requests_per_min": self.shazam_requests_per_min,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "score": self.score,
            "provider": self.provider,
            "recognized_at": self.recognized_at,
            "duration": self.duration,
            "rms": self.rms,
            "acoustid": self.acoustid,
            "musicbrainz_recording_id": self.musicbrainz_recording_id,
            "track_duration_ms": self.track_duration_ms,
            "match_offset_seconds": self.match_offset_seconds,
            "progress_start_seconds": self.progress_start_seconds,
            "progress_padding_seconds": self.progress_padding_seconds,
            "raw": self.raw,
            "message": self.message,
        }


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def run(cmd, timeout=None):
    return subprocess.run(
        cmd,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def user_runtime_env_args(user):
    if not user:
        return []
    uid_result = run(["id", "-u", user])
    if uid_result.returncode != 0:
        raise RuntimeError(uid_result.stderr.strip() or "could not resolve uid for %s" % user)
    runtime_dir = "/run/user/%s" % uid_result.stdout.strip()
    return [
        "env",
        "XDG_RUNTIME_DIR=%s" % runtime_dir,
        "DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus" % runtime_dir,
    ]


def pactl_user_args(user):
    if not user:
        return ["pactl"]
    return ["runuser", "-u", user, "--"] + user_runtime_env_args(user) + ["pactl"]


def get_audio_device(kind, match, user):
    assert kind in {"source", "sink"}
    default_cmd = pactl_user_args(user) + ["get-default-%s" % kind]
    default = run(default_cmd).stdout.strip()
    if not match:
        return default

    list_kind = "sources" if kind == "source" else "sinks"
    result = run(pactl_user_args(user) + ["list", list_kind])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "pactl list %s failed" % list_kind)

    current_name = ""
    needle = match.lower()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            current_name = stripped.split(None, 1)[1]
        elif stripped.startswith("Description:") and current_name:
            desc = stripped.split(None, 1)[1]
            if needle in (current_name + " " + desc).lower():
                return current_name
    return default


def record_sample(config, output_path, seconds):
    user = config.str("VISUALIZER_USER", "")
    source = config.str("RECOGNITION_SOURCE", "") or get_audio_device(
        "source", config.str("SOURCE_MATCH", "usb"), user
    )
    rate = config.int("RECOGNITION_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)
    channels = config.int("RECOGNITION_CHANNELS", DEFAULT_CHANNELS)

    parec_cmd = [
        "parec",
        "--device=%s" % source,
        "--format=s16le",
        "--rate=%s" % int(rate),
        "--channels=%s" % int(channels),
    ]
    if user:
        # The recognizer service runs as root, but PulseAudio belongs to the
        # desktop user. Run parec as that user; exporting XDG_RUNTIME_DIR alone
        # as root connects to the wrong/denied PulseAudio context and records
        # silence.
        parec_cmd = ["runuser", "-u", user, "--"] + user_runtime_env_args(user) + parec_cmd
    cmd = (
        "timeout %ss %s | "
        "ffmpeg -hide_banner -loglevel error -y -f s16le -ar %s -ac %s -i pipe:0 %s"
        % (
            int(seconds),
            " ".join(shlex.quote(part) for part in parec_cmd),
            int(rate),
            int(channels),
            shlex.quote(str(output_path)),
        )
    )
    result = subprocess.run(
        ["bash", "-lc", cmd],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "audio sample recording failed")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("audio sample was empty")
    return output_path


def wav_stats(path):
    import audioop

    with wave.open(str(path), "rb") as wav:
        width = wav.getsampwidth()
        frames = wav.getnframes()
        rate = wav.getframerate()
        total_rms = 0.0
        chunks = 0
        while True:
            data = wav.readframes(rate)
            if not data:
                break
            total_rms += audioop.rms(data, width)
            chunks += 1
    return total_rms / max(chunks, 1), int(round(float(frames) / float(rate or 1)))


def write_state(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), str(path))


def set_metrics(result, request_count=0, requests_per_min=0.0):
    result.shazam_request_count = int(request_count or 0)
    result.shazam_requests_per_min = float(requests_per_min or 0.0)
    return result


def request_rate(request_times, window_seconds=60):
    now = time.time()
    while request_times and request_times[0] < now - float(window_seconds):
        request_times.pop(0)
    return float(len(request_times))


def clear_track_fields(result):
    result.title = ""
    result.artist = ""
    result.album = ""
    result.track_duration_ms = 0
    result.progress_start_seconds = None
    result.match_offset_seconds = None
    return result


def copy_display_result(base, status, playback_status, listening=False, backing_off=False, ratelimit=False, message=""):
    base = base or RecognitionResult(status="waiting")
    return RecognitionResult(
        status=status,
        title=base.title,
        artist=base.artist,
        album=base.album,
        score=base.score,
        provider=base.provider,
        recognized_at=base.recognized_at,
        duration=base.duration,
        acoustid=base.acoustid,
        musicbrainz_recording_id=base.musicbrainz_recording_id,
        track_duration_ms=base.track_duration_ms,
        match_offset_seconds=base.match_offset_seconds,
        progress_start_seconds=base.progress_start_seconds,
        progress_padding_seconds=base.progress_padding_seconds,
        playback_status=playback_status,
        listening=listening,
        backing_off=backing_off,
        ratelimit=ratelimit,
        rms=base.rms,
        raw=base.raw,
        message=message or base.message,
    )


def identify_with_shazam(path):
    if not Path(SHAZAM_VENV_PYTHON).exists() or not Path(SHAZAM_LOOKUP_SCRIPT).exists():
        return RecognitionResult(
            status="error",
            provider="shazam",
            recognized_at=now_iso(),
            message="Shazam recognizer is not installed",
        )
    result = run([SHAZAM_VENV_PYTHON, SHAZAM_LOOKUP_SCRIPT, str(path)], timeout=90)
    if result.returncode != 0:
        return RecognitionResult(
            status="error",
            provider="shazam",
            recognized_at=now_iso(),
            message=(result.stderr.strip() or result.stdout.strip() or "Shazam lookup failed"),
        )
    data = json.loads(result.stdout)
    data["recognized_at"] = now_iso()
    return RecognitionResult(**data)


def progress_start_seconds(result, padding_seconds):
    if result.match_offset_seconds is None:
        return None
    try:
        return float(result.match_offset_seconds) + float(result.duration or 0) + float(padding_seconds)
    except (TypeError, ValueError):
        return None


def sleep_until_progress(result, target_percent, missing_duration_sleep=60, max_wait=150):
    if not result.track_duration_ms or result.progress_start_seconds is None:
        return int(missing_duration_sleep)
    track_seconds = float(result.track_duration_ms) / 1000.0
    target_seconds = track_seconds * (float(target_percent) / 100.0)
    sleep_for = max(0, int(round(target_seconds - float(result.progress_start_seconds))))
    if max_wait and max_wait > 0:
        sleep_for = min(sleep_for, int(max_wait))
    return sleep_for


def identify_once(config):
    seconds = config.int("RECOGNITION_SAMPLE_SECONDS", DEFAULT_SAMPLE_SECONDS)
    min_rms = config.float("RECOGNITION_MIN_RMS", DEFAULT_MIN_RMS)
    padding = config.float("RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS", DEFAULT_PROGRESS_OFFSET_PADDING_SECONDS)

    with tempfile.TemporaryDirectory(prefix="rca-recognition-") as tmpdir:
        sample = Path(tmpdir) / "sample.wav"
        record_sample(config, sample, seconds)
        rms, duration = wav_stats(sample)
        if rms < min_rms:
            return RecognitionResult(
                status="silence",
                playback_status="stopped",
                recognized_at=now_iso(),
                duration=duration,
                rms=rms,
                message="sample RMS %.1f below threshold %.1f" % (rms, min_rms),
            )

        result = identify_with_shazam(sample)
        result.duration = duration
        result.rms = rms
        result.playback_status = "playing"
        result.shazam_request_count = 1
        result.shazam_requests_per_min = 1.0
        if result.status == "recognized":
            result.progress_padding_seconds = padding
            result.progress_start_seconds = progress_start_seconds(result, padding)
        return result


def daemon(config):
    state_path = Path(config.str("NOW_PLAYING_STATE", DEFAULT_STATE_PATH))
    enabled = config.bool("RECOGNITION_ENABLED", False)
    min_rms = config.float("RECOGNITION_MIN_RMS", DEFAULT_MIN_RMS)
    sample_seconds = config.int("RECOGNITION_SAMPLE_SECONDS", DEFAULT_SAMPLE_SECONDS)
    no_match_limit = config.int("RECOGNITION_NO_MATCH_LIMIT", DEFAULT_NO_MATCH_LIMIT)
    no_match_backoff = config.int("RECOGNITION_NO_MATCH_BACKOFF_SECONDS", DEFAULT_NO_MATCH_BACKOFF_SECONDS)
    ratelimit_threshold = config.float("RECOGNITION_RATELIMIT_REQUESTS_PER_MIN", DEFAULT_RATELIMIT_REQUESTS_PER_MIN)
    ratelimit_backoff = config.int("RECOGNITION_RATELIMIT_BACKOFF_SECONDS", DEFAULT_RATELIMIT_BACKOFF_SECONDS)
    progress_resume_percent = config.float("RECOGNITION_PROGRESS_RESUME_PERCENT", DEFAULT_PROGRESS_RESUME_PERCENT)
    max_recheck_wait = config.int("RECOGNITION_MAX_RECHECK_WAIT_SECONDS", DEFAULT_MAX_RECHECK_WAIT_SECONDS)
    missing_duration_recheck = config.int("RECOGNITION_MISSING_DURATION_RECHECK_SECONDS", DEFAULT_MISSING_DURATION_RECHECK_SECONDS)
    progress_padding = config.float("RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS", DEFAULT_PROGRESS_OFFSET_PADDING_SECONDS)

    no_match_count = 0
    playback_status = "stopped"
    last_display_result = RecognitionResult(status="waiting", playback_status=playback_status)
    shazam_request_count = 0
    shazam_request_times = []

    if not enabled:
        print("Recognition disabled. Set RECOGNITION_ENABLED=true to enable.", flush=True)
        while True:
            time.sleep(3600)

    while True:
        sleep_for = 0
        try:
            # During scheduled/forced rechecks, keep the recognized status and
            # existing progress fields on screen while the new sample records.
            # The UI still shows "Listening" from the flag below, but the timer
            # can keep advancing from the prior recognized_at/progress state.
            listening_status = "recognized" if last_display_result.status == "recognized" else "listening"
            listening_result = copy_display_result(
                last_display_result,
                status=listening_status,
                playback_status=playback_status,
                listening=True,
                message="recording %s second sample" % sample_seconds,
            )
            set_metrics(listening_result, shazam_request_count, request_rate(shazam_request_times))
            write_state(state_path, listening_result)

            with tempfile.TemporaryDirectory(prefix="rca-recognition-") as tmpdir:
                sample = Path(tmpdir) / "sample.wav"
                record_sample(config, sample, sample_seconds)
                rms, duration = wav_stats(sample)

                if rms < min_rms:
                    no_match_count = 0
                    playback_status = "stopped"
                    result = RecognitionResult(
                        status="stopped",
                        playback_status="stopped",
                        recognized_at=now_iso(),
                        duration=duration,
                        rms=rms,
                        message="stopped after quiet sample; RMS %.1f below threshold %.1f"
                        % (rms, min_rms),
                    )
                    set_metrics(result, shazam_request_count, request_rate(shazam_request_times))
                    write_state(state_path, result)
                    last_display_result = result
                else:
                    playback_status = "playing"
                    shazam_request_count += 1
                    shazam_request_times.append(time.time())
                    result = identify_with_shazam(sample)
                    result.duration = duration
                    result.rms = rms
                    result.playback_status = playback_status
                    set_metrics(result, shazam_request_count, request_rate(shazam_request_times))
                    if result.shazam_requests_per_min > ratelimit_threshold:
                        result.status = "ratelimit"
                        result.ratelimit = True
                        result.backing_off = True
                        clear_track_fields(result)
                        result.message = "RATELIMIT: %.1f Shazam requests/min > %.1f; backing off for %s seconds" % (
                            result.shazam_requests_per_min,
                            ratelimit_threshold,
                            ratelimit_backoff,
                        )
                        write_state(state_path, result)
                        last_display_result = result
                        sleep_for = ratelimit_backoff
                        no_match_count = 0
                    elif result.status == "recognized":
                        no_match_count = 0
                        result.progress_padding_seconds = progress_padding
                        result.progress_start_seconds = progress_start_seconds(result, progress_padding)
                        write_state(state_path, result)
                        last_display_result = result
                        sleep_for = sleep_until_progress(
                            result,
                            progress_resume_percent,
                            missing_duration_sleep=missing_duration_recheck,
                            max_wait=max_recheck_wait,
                        )
                    elif result.status in {"no_match", "error"}:
                        no_match_count += 1
                        if last_display_result.status == "recognized":
                            # A forced mid-track recheck can occasionally miss even while
                            # the current song is still playing. Keep the visible song and
                            # progress estimate instead of blinking the board blank; the
                            # status line still reports the bad response/backoff.
                            prior = last_display_result
                            kept = copy_display_result(
                                prior,
                                status="recognized",
                                playback_status=playback_status,
                                listening=False,
                                backing_off=no_match_count >= no_match_limit,
                                message=result.message
                                or "bad Shazam response %s/%s; keeping previous song on screen"
                                % (no_match_count, no_match_limit),
                            )
                            kept.duration = duration
                            kept.rms = rms
                            if no_match_count >= no_match_limit:
                                kept.message = "backing off for %s seconds after %s bad Shazam responses; keeping previous song on screen" % (
                                    no_match_backoff,
                                    no_match_limit,
                                )
                                sleep_for = no_match_backoff
                                no_match_count = 0
                            set_metrics(kept, shazam_request_count, request_rate(shazam_request_times))
                            write_state(state_path, kept)
                            last_display_result = kept
                        else:
                            # Bad Shazam responses should not leave stale track data
                            # on screen when there is no previous recognized track to keep.
                            clear_track_fields(result)
                            result.playback_status = playback_status
                            result.message = result.message or "bad Shazam response %s/%s" % (no_match_count, no_match_limit)
                            if no_match_count >= no_match_limit:
                                result.status = "backing_off"
                                result.backing_off = True
                                result.message = "backing off for %s seconds after %s bad Shazam responses" % (
                                    no_match_backoff,
                                    no_match_limit,
                                )
                                sleep_for = no_match_backoff
                                no_match_count = 0
                            write_state(state_path, result)
                            last_display_result = result
                    else:
                        clear_track_fields(result)
                        result.backing_off = True
                        write_state(state_path, result)
                        last_display_result = result
                        sleep_for = no_match_backoff

            print(
                "%s/%s: %s - %s provider=%s score=%.3f rms=%s reqs=%s rpm=%.1f %s" % (
                    result.playback_status or "unknown",
                    result.status,
                    result.artist,
                    result.title,
                    result.provider,
                    result.score,
                    "%.1f" % result.rms if result.rms is not None else "",
                    result.shazam_request_count,
                    result.shazam_requests_per_min,
                    result.message,
                ),
                flush=True,
            )
        except Exception as exc:
            err = RecognitionResult(status="error", playback_status=playback_status, recognized_at=now_iso(), message=str(exc))
            set_metrics(err, shazam_request_count, request_rate(shazam_request_times))
            write_state(state_path, err)
            last_display_result = err
            print("recognition error: %s" % exc, file=sys.stderr, flush=True)
            sleep_for = no_match_backoff
        if sleep_for > 0:
            if last_display_result.status in {"backing_off", "ratelimit", "error"}:
                last_display_result.backing_off = True
                if last_display_result.status == "ratelimit":
                    last_display_result.ratelimit = True
                set_metrics(last_display_result, shazam_request_count, request_rate(shazam_request_times))
                write_state(state_path, last_display_result)
            time.sleep(max(5, sleep_for))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Shazam now-playing recognizer")
    parser.add_argument("command", choices=["identify-once", "daemon"])
    parser.add_argument("--state", default="", help="Override now-playing JSON state path")
    args = parser.parse_args(argv)

    config = RuntimeConfig.load()
    if args.state:
        config.values["NOW_PLAYING_STATE"] = args.state

    if args.command == "identify-once":
        result = identify_once(config)
        state_path = Path(config.str("NOW_PLAYING_STATE", DEFAULT_STATE_PATH))
        write_state(state_path, result)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.status in {"recognized", "no_match", "silence", "stopped", "backing_off", "ratelimit"} else 1

    daemon(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
