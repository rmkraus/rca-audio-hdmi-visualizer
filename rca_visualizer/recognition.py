import argparse
import json
import os
import subprocess
import sys
import tempfile
import shlex
import time
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

from .config import RuntimeConfig

ACOUSTID_LOOKUP_URL = "https://api.acoustid.org/v2/lookup"


class RecognitionResult:
    def __init__(
        self,
        status,
        title="",
        artist="",
        album="",
        score=0.0,
        provider="acoustid",
        recognized_at="",
        duration=0,
        acoustid="",
        musicbrainz_recording_id="",
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
        self.acoustid = acoustid
        self.musicbrainz_recording_id = musicbrainz_recording_id
        self.raw = raw
        self.message = message

    def to_dict(self):
        return {
            "status": self.status,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "score": self.score,
            "provider": self.provider,
            "recognized_at": self.recognized_at,
            "duration": self.duration,
            "acoustid": self.acoustid,
            "musicbrainz_recording_id": self.musicbrainz_recording_id,
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

    env_prefix = ""
    if user:
        env_prefix = " ".join(shlex.quote(part) for part in user_runtime_env_args(user)) + " "
    cmd = (
        "timeout %ss %sparec --device=%s --format=s16le --rate=%s --channels=%s | "
        "ffmpeg -hide_banner -loglevel error -y -f s16le -ar %s -ac %s -i pipe:0 %s"
        % (
            int(seconds),
            env_prefix,
            shlex.quote(source),
            int(rate),
            int(channels),
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


def wav_rms(path):
    import audioop

    with wave.open(str(path), "rb") as wav:
        width = wav.getsampwidth()
        total_rms = 0.0
        chunks = 0
        while True:
            data = wav.readframes(44100)
            if not data:
                break
            total_rms += audioop.rms(data, width)
            chunks += 1
    return total_rms / max(chunks, 1)


def fingerprint(path):
    result = run(["fpcalc", "-json", str(path)], timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "fpcalc failed")
    return json.loads(result.stdout)


def lookup_acoustid(client_key, fp, duration, timeout=30):
    payload = urllib.parse.urlencode(
        {
            "client": client_key,
            "duration": str(duration),
            "fingerprint": fp,
            "meta": "recordings+releasegroups+compress",
            "format": "json",
        }
    ).encode()
    request = urllib.request.Request(
        ACOUSTID_LOOKUP_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def best_result(data, min_score):
    results = data.get("results") or []
    if not results:
        return RecognitionResult(status="no_match", recognized_at=now_iso(), raw=data)

    best = max(results, key=lambda item: float(item.get("score") or 0.0))
    score = float(best.get("score") or 0.0)
    recordings = best.get("recordings") or []
    recording = recordings[0] if recordings else {}
    artists = recording.get("artists") or []
    releasegroups = recording.get("releasegroups") or []

    artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
    album = releasegroups[0].get("title", "") if releasegroups else ""
    title = recording.get("title", "")

    status = "recognized" if score >= min_score and title else "low_score"
    return RecognitionResult(
        status=status,
        title=title,
        artist=artist,
        album=album,
        score=score,
        recognized_at=now_iso(),
        acoustid=best.get("id", ""),
        musicbrainz_recording_id=recording.get("id", ""),
        raw=data,
    )


def write_state(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), str(path))


def identify_once(config):
    key = config.str("ACOUSTID_CLIENT_KEY")
    if not key:
        raise RuntimeError("ACOUSTID_CLIENT_KEY is missing; set it in /etc/rca-hdmi-visualizer.secrets")

    seconds = config.int("RECOGNITION_SAMPLE_SECONDS", 45)
    min_rms = config.float("RECOGNITION_MIN_RMS", 150.0)
    min_score = config.float("RECOGNITION_MIN_SCORE", 0.80)

    with tempfile.TemporaryDirectory(prefix="rca-recognition-") as tmpdir:
        sample = Path(tmpdir) / "sample.wav"
        record_sample(config, sample, seconds)
        rms = wav_rms(sample)
        if rms < min_rms:
            return RecognitionResult(
                status="silence",
                recognized_at=now_iso(),
                message="sample RMS %.1f below threshold %.1f" % (rms, min_rms),
            )

        fp_data = fingerprint(sample)
        data = lookup_acoustid(key, fp_data["fingerprint"], int(fp_data["duration"]))
        result = best_result(data, min_score)
        values = result.to_dict()
        values["duration"] = int(fp_data["duration"])
        return RecognitionResult(**values)


def daemon(config):
    state_path = Path(config.str("NOW_PLAYING_STATE", "/var/lib/rca-hdmi-visualizer/now-playing.json"))
    interval = config.int("RECOGNITION_INTERVAL_SECONDS", 30)
    cooldown = config.int("RECOGNITION_COOLDOWN_SECONDS", 75)
    keep_last_on_miss = config.bool("RECOGNITION_KEEP_LAST_ON_MISS", True)
    enabled = config.bool("RECOGNITION_ENABLED", False)
    last_track_key = ""

    if not enabled:
        print("Recognition disabled. Set RECOGNITION_ENABLED=true to enable.", flush=True)
        while True:
            time.sleep(3600)

    while True:
        try:
            result = identify_once(config)
            track_key = (result.artist + "\0" + result.title).lower()
            print(
                "%s: %s - %s score=%.3f %s" % (
                    result.status,
                    result.artist,
                    result.title,
                    result.score,
                    result.message,
                ),
                flush=True,
            )
            if result.status == "recognized":
                write_state(state_path, result)
                sleep_for = cooldown if track_key == last_track_key else interval
                last_track_key = track_key
            else:
                if not keep_last_on_miss or not state_path.exists():
                    write_state(state_path, result)
                sleep_for = interval
        except Exception as exc:
            err = RecognitionResult(status="error", recognized_at=now_iso(), message=str(exc))
            write_state(state_path, err)
            print("recognition error: %s" % exc, file=sys.stderr, flush=True)
            sleep_for = interval
        time.sleep(max(5, sleep_for))


def main(argv=None):
    parser = argparse.ArgumentParser(description="AcoustID now-playing recognizer")
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
        return 0 if result.status in {"recognized", "low_score", "no_match", "silence"} else 1

    daemon(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
