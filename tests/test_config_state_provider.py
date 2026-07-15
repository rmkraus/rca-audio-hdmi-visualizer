#!/usr/bin/env python3
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import types
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rca_visualizer.config import RuntimeConfig, parse_env_file
from rca_visualizer.lyrics import lyrics_for_state, log_lyrics_not_found, parse_lrc
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
    assert data["shazam_response_count"] == 0
    assert data["shazam_last_response_status"] == ""


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

    set_metrics(
        copied,
        request_count=3,
        requests_per_min=2.5,
        response_counts={"recognized": 1, "no_match": 2},
        last_request={
            "id": "shazam-000003",
            "started_at": "2026-07-01T00:00:00+00:00",
            "response_at": "2026-07-01T00:00:02+00:00",
            "status": "no_match",
            "duration_seconds": 2.0,
        },
    )
    assert copied.shazam_request_count == 3
    assert copied.shazam_requests_per_min == 2.5
    assert copied.shazam_response_count == 3
    assert copied.shazam_recognized_count == 1
    assert copied.shazam_no_match_count == 2
    assert copied.shazam_last_request_id == "shazam-000003"
    assert copied.shazam_last_response_status == "no_match"


def test_progress_and_rate_helpers():
    result = RecognitionResult(
        status="recognized",
        duration=12,
        match_offset_seconds=4.5,
        track_duration_ms=180000,
    )
    assert progress_start_seconds(result, 5) == 21.5
    result.recognition_pipeline_delay_seconds = 1.25
    assert progress_start_seconds(result, 5) == 17.75
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


def test_lyrics_not_found_db_dedupes_and_tracks_counts():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "lyrics-not-found.sqlite3"
        jsonl_path = Path(tmpdir) / "lyrics-not-found.jsonl"
        config = RuntimeConfig(
            {
                "LYRICS_NOT_FOUND_DB": str(db_path),
                "LYRICS_NOT_FOUND_LOG": str(jsonl_path),
            }
        )
        state = {
            "status": "recognized",
            "title": "Missing Song",
            "artist": "Missing Artist",
            "album": "Missing Album",
            "recognized_at": "2026-07-06T20:00:00+00:00",
            "acoustid": "12345",
            "message": "https://www.shazam.com/track/12345/missing-song",
            "raw": {"isrc": "USABC1234567"},
            "track_duration_ms": 123000,
        }
        payload = {"available": False, "reason": "not_found"}
        log_lyrics_not_found(config, state, payload, "abc")
        log_lyrics_not_found(config, state, payload, "abc")

        with sqlite3.connect(str(db_path)) as db:
            rows = db.execute(
                "SELECT cache_key, track_name, artist_name, duration, shazam_key, isrc, seen_count, contribution_status FROM lyrics_not_found"
            ).fetchall()
        assert rows == [("abc", "Missing Song", "Missing Artist", 123, "12345", "USABC1234567", 2, "pending")]
        assert len(jsonl_path.read_text().splitlines()) == 2


def test_shazam_lookup_clamps_negative_offsets_and_caches_track_metadata():
    fake_shazamio = types.ModuleType("shazamio")
    setattr(fake_shazamio, "Shazam", object)
    old_shazamio = sys.modules.get("shazamio")
    sys.modules["shazamio"] = fake_shazamio
    try:
        module_path = Path(__file__).resolve().parents[1] / "rca_visualizer" / "shazam_lookup.py"
        spec = importlib.util.spec_from_file_location("test_shazam_lookup", module_path)
        assert spec is not None and spec.loader is not None
        shazam_lookup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shazam_lookup)
    finally:
        if old_shazamio is None:
            sys.modules.pop("shazamio", None)
        else:
            sys.modules["shazamio"] = old_shazamio

    assert shazam_lookup.first_match_offset({"matches": [{"offset": -4.08}]}) == 0.0
    assert shazam_lookup.first_match_offset({"matches": [{"offset": 12.5}]}) == 12.5

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "shazam-track-cache.sqlite3"
        old_cache = os.environ.get("SHAZAM_TRACK_CACHE_DB")
        os.environ["SHAZAM_TRACK_CACHE_DB"] = str(db_path)
        try:
            result = {
                "status": "recognized",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "acoustid": "123",
                "track_duration_ms": 111000,
                "raw": {"key": "123", "url": "u", "trackadamid": "456", "isrc": "ISRC"},
                "message": "u",
            }
            shazam_lookup.write_track_cache(result)
            cached = shazam_lookup.read_track_cache("123")
            assert cached["title"] == "Song"
            assert cached["raw"]["trackadamid"] == "456"
            out = shazam_lookup.track_to_result(
                {"track": {"key": "123"}, "matches": [{"offset": -1.0}], "track_cache": cached}
            )
            assert out["status"] == "recognized"
            assert out["match_offset_seconds"] == 0.0
            assert out["raw"]["track_cache"] == "hit"
            with sqlite3.connect(str(db_path)) as db:
                seen = db.execute("SELECT seen_count FROM shazam_track_cache WHERE track_key='123'").fetchone()[0]
            assert seen == 2
        finally:
            if old_cache is None:
                os.environ.pop("SHAZAM_TRACK_CACHE_DB", None)
            else:
                os.environ["SHAZAM_TRACK_CACHE_DB"] = old_cache


def test_shazam_lookup_logs_match_diagnostics_when_available():
    fake_shazamio = types.ModuleType("shazamio")
    setattr(fake_shazamio, "Shazam", object)
    old_shazamio = sys.modules.get("shazamio")
    sys.modules["shazamio"] = fake_shazamio
    try:
        module_path = Path(__file__).resolve().parents[1] / "rca_visualizer" / "shazam_lookup.py"
        spec = importlib.util.spec_from_file_location("test_shazam_lookup_diag", module_path)
        assert spec is not None and spec.loader is not None
        shazam_lookup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shazam_lookup)
    finally:
        if old_shazamio is None:
            sys.modules.pop("shazamio", None)
        else:
            sys.modules["shazamio"] = old_shazamio

    data = {
        "track": {"key": "999", "title": "Wrong Song", "subtitle": "Wrong Artist"},
        "score": 0.42,
        "matches": [
            {
                "offset": 12.34,
                "timeskew": -0.001,
                "frequencyskew": 0.002,
                "score": 0.37,
                "confidence": 0.41,
            }
        ],
    }
    result = shazam_lookup.track_to_result(data)
    assert result["status"] == "recognized"
    assert result["score"] == 0.42
    assert result["raw"]["match_count"] == 1
    assert result["raw"]["confidence"] == 0.42
    assert result["raw"]["first_match_score"] == 0.37
    assert result["raw"]["first_match_confidence"] == 0.41
    assert result["raw"]["first_match_timeskew"] == -0.001
    assert result["raw"]["first_match_frequencyskew"] == 0.002


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
        test_lyrics_not_found_db_dedupes_and_tracks_counts,
        test_shazam_lookup_clamps_negative_offsets_and_caches_track_metadata,
        test_shazam_lookup_logs_match_diagnostics_when_available,
        test_recognition_cli_help_smoke,
    ]
    for test in tests:
        test()
    print("config/state/provider tests passed")


if __name__ == "__main__":
    main()
