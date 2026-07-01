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
        self.rms = rms
        self.raw = raw
        self.message = message

    def to_dict(self):
        return {
            "status": self.status,
            "playback_status": self.playback_status,
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
    rate = config.int("RECOGNITION_SAMPLE_RATE", 44100)
    channels = config.int("RECOGNITION_CHANNELS", 2)

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


def sleep_until_progress(result, target_percent):
    if not result.track_duration_ms or result.progress_start_seconds is None:
        return 0
    track_seconds = float(result.track_duration_ms) / 1000.0
    target_seconds = track_seconds * (float(target_percent) / 100.0)
    return max(0, int(round(target_seconds - float(result.progress_start_seconds))))


def identify_once(config):
    seconds = config.int("RECOGNITION_SAMPLE_SECONDS", 12)
    min_rms = config.float("RECOGNITION_MIN_RMS", 150.0)
    padding = config.float("RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS", 5.0)

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
        if result.status == "recognized":
            result.progress_padding_seconds = padding
            result.progress_start_seconds = progress_start_seconds(result, padding)
        return result


def daemon(config):
    state_path = Path(config.str("NOW_PLAYING_STATE", "/var/lib/rca-hdmi-visualizer/now-playing.json"))
    enabled = config.bool("RECOGNITION_ENABLED", False)
    min_rms = config.float("RECOGNITION_MIN_RMS", 150.0)
    sample_seconds = config.int("RECOGNITION_SAMPLE_SECONDS", 12)
    silence_limit = config.int("RECOGNITION_SILENCE_WINDOWS_TO_STOP", 3)
    no_match_limit = config.int("RECOGNITION_NO_MATCH_LIMIT", 3)
    no_match_backoff = config.int("RECOGNITION_NO_MATCH_BACKOFF_SECONDS", 60)
    progress_resume_percent = config.float("RECOGNITION_PROGRESS_RESUME_PERCENT", 95.0)
    progress_padding = config.float("RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS", 5.0)

    silence_count = 0
    no_match_count = 0
    playback_status = "stopped"

    if not enabled:
        print("Recognition disabled. Set RECOGNITION_ENABLED=true to enable.", flush=True)
        while True:
            time.sleep(3600)

    while True:
        sleep_for = 0
        try:
            with tempfile.TemporaryDirectory(prefix="rca-recognition-") as tmpdir:
                sample = Path(tmpdir) / "sample.wav"
                record_sample(config, sample, sample_seconds)
                rms, duration = wav_stats(sample)

                if rms < min_rms:
                    silence_count += 1
                    no_match_count = 0
                    if silence_count >= silence_limit:
                        playback_status = "stopped"
                        result = RecognitionResult(
                            status="stopped",
                            playback_status="stopped",
                            recognized_at=now_iso(),
                            duration=duration,
                            rms=rms,
                            message="stopped after %s quiet samples; RMS %.1f below threshold %.1f"
                            % (silence_count, rms, min_rms),
                        )
                    else:
                        result = RecognitionResult(
                            status="silence",
                            playback_status=playback_status,
                            recognized_at=now_iso(),
                            duration=duration,
                            rms=rms,
                            message="quiet sample %s/%s; RMS %.1f below threshold %.1f"
                            % (silence_count, silence_limit, rms, min_rms),
                        )
                    write_state(state_path, result)
                else:
                    silence_count = 0
                    playback_status = "playing"
                    result = identify_with_shazam(sample)
                    result.duration = duration
                    result.rms = rms
                    result.playback_status = playback_status
                    if result.status == "recognized":
                        no_match_count = 0
                        result.progress_padding_seconds = progress_padding
                        result.progress_start_seconds = progress_start_seconds(result, progress_padding)
                        write_state(state_path, result)
                        sleep_for = sleep_until_progress(result, progress_resume_percent)
                    elif result.status == "no_match":
                        no_match_count += 1
                        result.playback_status = playback_status
                        result.message = result.message or "no Shazam match %s/%s" % (no_match_count, no_match_limit)
                        write_state(state_path, result)
                        if no_match_count >= no_match_limit:
                            sleep_for = no_match_backoff
                            no_match_count = 0
                    else:
                        write_state(state_path, result)
                        sleep_for = no_match_backoff

            print(
                "%s/%s: %s - %s provider=%s score=%.3f rms=%s %s" % (
                    result.playback_status or "unknown",
                    result.status,
                    result.artist,
                    result.title,
                    result.provider,
                    result.score,
                    "%.1f" % result.rms if result.rms is not None else "",
                    result.message,
                ),
                flush=True,
            )
        except Exception as exc:
            err = RecognitionResult(status="error", playback_status=playback_status, recognized_at=now_iso(), message=str(exc))
            write_state(state_path, err)
            print("recognition error: %s" % exc, file=sys.stderr, flush=True)
            sleep_for = no_match_backoff
        if sleep_for > 0:
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
        state_path = Path(config.str("NOW_PLAYING_STATE", "/var/lib/rca-hdmi-visualizer/now-playing.json"))
        write_state(state_path, result)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.status in {"recognized", "no_match", "silence", "stopped"} else 1

    daemon(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
