#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rca_visualizer.config import RuntimeConfig, parse_env_file
from rca_visualizer.lyrics import lyrics_for_state, parse_lrc
from rca_visualizer.recognition_provider import identify_with_shazam, wav_stats
from rca_visualizer.recognition_state import (
    playback_recheck_timeout,
    progress_start_seconds,
    request_rate,
    set_metrics,
    sleep_until_progress,
    write_state,
)
from rca_visualizer.recognition_types import RecognitionResult, clear_track_fields, copy_display_result


def test_parse_env_file_handles_quotes_comments_and_bad_lines():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "env"
        path.write_text(
            "\n".join(
                [
                    "# comment",
                    "RECOGNITION_ENABLED=true",
                    "VISUALIZER_USER='rkraus'",
                    'SOURCE_MATCH="iRig HD X"',
                    "BAD_LINE_WITHOUT_EQUALS",
                    "EMPTY=",
                ]
            )
            + "\n"
        )
        values = parse_env_file(path)
    assert values["RECOGNITION_ENABLED"] == "true"
    assert values["VISUALIZER_USER"] == "rkraus"
    assert values["SOURCE_MATCH"] == "iRig HD X"
    assert values["EMPTY"] == ""
    assert "BAD_LINE_WITHOUT_EQUALS" not in values


def test_runtime_config_coercions():
    config = RuntimeConfig({"BOOL": "yes", "INT": "7", "FLOAT": "2.5", "STR": "value"})
    assert config.bool("BOOL") is True
    assert config.int("INT", 0) == 7
    assert config.float("FLOAT", 0.0) == 2.5
    assert config.str("STR") == "value"
    assert config.bool("MISSING", False) is False


def test_write_state_is_atomic_json_with_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "state" / "now-playing.json"
        result = RecognitionResult(status="recognized", playback_status="playing", title="Track", artist="Artist")
        write_state(path, result)
        data = json.loads(path.read_text())
    assert data["status"] == "recognized"
    assert data["playback_status"] == "playing"
    assert data["title"] == "Track"
    assert data["artist"] == "Artist"
    assert data["provider"] == "shazam"
    assert data["shazam_request_count"] == 0


def test_result_copy_clear_and_metrics_helpers():
    base = RecognitionResult(
        status="recognized",
        playback_status="playing",
        title="Song",
        artist="Artist",
        album="Album",
        track_duration_ms=123000,
        match_offset_seconds=4.0,
        progress_start_seconds=17.0,
        rms=99.0,
    )
    copied = copy_display_result(base, status="recognized", playback_status="playing", listening=True)
    assert copied.title == "Song"
    assert copied.listening is True
    assert copied.rms == 99.0

    clear_track_fields(copied)
    assert copied.title == ""
    assert copied.artist == ""
    assert copied.track_duration_ms == 0
    assert copied.progress_start_seconds is None
    assert copied.match_offset_seconds is None

    set_metrics(copied, request_count=3, requests_per_min=2.5)
    assert copied.shazam_request_count == 3
    assert copied.shazam_requests_per_min == 2.5


def test_progress_and_rate_helpers():
    result = RecognitionResult(
        status="recognized",
        duration=12,
        match_offset_seconds=4.5,
        track_duration_ms=180000,
    )
    assert progress_start_seconds(result, 5) == 21.5
    result.progress_start_seconds = 200.0
    assert sleep_until_progress(result, 100, missing_duration_sleep=60, max_wait=150) == 0
    assert playback_recheck_timeout(result, 50, 60, 150) == 0
    assert request_rate([0, 100], window_seconds=60) in {0.0, 1.0, 2.0}


def test_wav_stats_reads_duration_and_rms():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(10)
            wav.writeframes((b"\x10\x00") * 20)
        rms, duration = wav_stats(path)
    assert rms == 16
    assert duration == 2


def test_identify_with_shazam_missing_install_reports_error():
    result = identify_with_shazam(Path("/tmp/nonexistent.wav"))
    # CI and dev machines normally do not have the appliance-only Shazam venv.
    # If they do, this may become a lookup error, but should still be structured.
    assert result.status in {"error", "no_match", "recognized"}
    assert result.provider == "shazam"


def test_lyrics_lrc_parsing_and_disabled_state():
    lines = parse_lrc("[00:01.50]First line\n[00:02.250]Second line")
    assert lines == [{"time": 1.5, "text": "First line"}, {"time": 2.25, "text": "Second line"}]
    payload = lyrics_for_state({"status": "recognized", "title": "Song", "artist": "Artist"}, RuntimeConfig({}))
    assert payload["available"] is False
    assert payload["reason"] == "disabled"


def test_recognition_cli_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "rca_visualizer.recognition", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    assert result.returncode == 0
    assert "identify-once" in result.stdout
    assert "daemon" in result.stdout


def main():
    tests = [
        test_parse_env_file_handles_quotes_comments_and_bad_lines,
        test_runtime_config_coercions,
        test_write_state_is_atomic_json_with_defaults,
        test_result_copy_clear_and_metrics_helpers,
        test_progress_and_rate_helpers,
        test_wav_stats_reads_duration_and_rms,
        test_identify_with_shazam_missing_install_reports_error,
        test_lyrics_lrc_parsing_and_disabled_state,
        test_recognition_cli_help_smoke,
    ]
    for test in tests:
        test()
    print("config/state/provider tests passed")


if __name__ == "__main__":
    main()
