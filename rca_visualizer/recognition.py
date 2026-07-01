import argparse
import json
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from .audio_interface import get_audio_device, user_runtime_env_args
from .config import RuntimeConfig
from .defaults import (
    DEFAULT_CHANNELS,
    DEFAULT_MIN_RMS,
    DEFAULT_PROGRESS_OFFSET_PADDING_SECONDS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_SECONDS,
    DEFAULT_STATE_PATH,
)
from .recognition_provider import identify_with_shazam, wav_stats
from .recognition_state import playback_recheck_timeout, progress_start_seconds, write_state
from .recognition_types import RecognitionResult


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


def identify_once(config):
    from .recognition_state import now_iso

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
    enabled = config.bool("RECOGNITION_ENABLED", False)
    if not enabled:
        print("Recognition disabled. Set RECOGNITION_ENABLED=true to enable.", flush=True)
        while True:
            time.sleep(3600)

    from .detection import DetectionLoop

    DetectionLoop(config).run_forever()


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

# Backward-compatible aliases for external imports from older installs/tests.
from .recognition_provider import run, wav_stats  # noqa: E402,F401
from .recognition_state import (  # noqa: E402,F401
    log_result,
    now_iso,
    request_rate,
    set_metrics,
    sleep_until_progress,
    wait_for_playback_stop,
)
from .recognition_types import clear_track_fields, copy_display_result  # noqa: E402,F401
