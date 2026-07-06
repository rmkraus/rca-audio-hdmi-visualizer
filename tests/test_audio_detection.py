#!/usr/bin/env python3
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rca_visualizer.audio_interface import AudioChunk, AudioInterface, AudioRecording
from rca_visualizer import detection as detection_module
from rca_visualizer.detection import DetectionLoop
from rca_visualizer.recognition_types import RecognitionResult


class FakeConfig(object):
    def __init__(self, values=None):
        self.values = values or {}

    def str(self, key, default=""):
        return self.values.get(key, default)

    def int(self, key, default):
        return int(self.values.get(key, default))

    def float(self, key, default):
        return float(self.values.get(key, default))

    def bool(self, key, default=False):
        return bool(self.values.get(key, default))


def make_audio(**kwargs):
    return AudioInterface(["printf", ""], sample_rate=10, channels=1, min_rms=5, chunk_seconds=0.5, **kwargs)


def test_audio_activity_events_are_gated_once():
    audio = make_audio(start_gate_seconds=1, stop_gate_seconds=1)

    audio._update_activity(10, 0.5)
    assert not audio.audio_started.is_set()
    audio._update_activity(10, 0.5)
    assert audio.audio_started.is_set()
    started = audio.wait_for_event(["started"], timeout=0)
    assert started is not None
    assert started.kind == "started"
    assert audio.wait_for_event(["started"], timeout=0) is None

    audio._update_activity(0, 0.5)
    assert not audio.audio_stopped.is_set()
    audio._update_activity(0, 0.5)
    assert audio.audio_stopped.is_set()
    stopped = audio.wait_for_event(["stopped"], timeout=0)
    assert stopped is not None
    assert stopped.kind == "stopped"
    assert audio.wait_for_event(["stopped"], timeout=0) is None


def test_clear_buffer_can_keep_preroll():
    audio = make_audio(start_gate_seconds=1, stop_gate_seconds=5, preroll_seconds=1)
    chunks = [AudioChunk(bytes([i, 0]) * 10, i, time.time(), 0.5) for i in range(4)]
    with audio._condition:
        for chunk in chunks:
            audio._chunks.append(chunk)
            audio._preroll.append(chunk)
            while len(audio._preroll) > audio.preroll_chunks:
                audio._preroll.popleft()
    audio.clear_buffer(keep_preroll=True)
    kept = []
    while True:
        chunk = audio.grab_chunk(timeout=0)
        if chunk is None:
            break
        kept.append(chunk)
    assert kept == chunks[-2:]


def test_record_wav_writes_valid_file():
    audio = make_audio(start_gate_seconds=1, stop_gate_seconds=5)
    chunks = [
        AudioChunk((b"\x10\x00") * 10, 16, 1001.0, 1.0),
        AudioChunk((b"\x20\x00") * 10, 32, 1002.0, 1.0),
    ]
    with audio._condition:
        audio._chunks.extend(chunks)
        audio._condition.notify_all()

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.wav"
        audio.record_wav(2, path)
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getframerate() == 10
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == 20
    assert audio.last_recording is not None
    assert audio.last_recording.duration_seconds == 2.0
    assert audio.last_recording.started_at == chunks[0].captured_at - chunks[0].duration_seconds
    assert audio.last_recording.stopped_at == chunks[-1].captured_at


def test_detection_loop_legacy_gate_fallback_and_overrides():
    legacy = DetectionLoop(FakeConfig({"RECOGNITION_AUDIO_GATE_SECONDS": 4}))
    assert legacy.audio_start_gate_seconds == 4
    assert legacy.audio_stop_gate_seconds == 4

    explicit = DetectionLoop(
        FakeConfig(
            {
                "RECOGNITION_AUDIO_GATE_SECONDS": 4,
                "RECOGNITION_AUDIO_START_GATE_SECONDS": 1,
                "RECOGNITION_AUDIO_STOP_GATE_SECONDS": 5,
            }
        )
    )
    assert explicit.audio_start_gate_seconds == 1
    assert explicit.audio_stop_gate_seconds == 5


def test_detection_loop_minimum_recheck_wait():
    loop = DetectionLoop(FakeConfig({"RECOGNITION_MIN_RECHECK_WAIT_SECONDS": 7}))
    result = RecognitionResult(status="recognized", track_duration_ms=180000, progress_start_seconds=200.0)
    assert loop._playback_recheck_timeout(result) == 7


class FakeDetectionAudio(object):
    def __init__(self, stop_after_timeouts=1):
        self.recorded = []
        self.silence_waits = []
        self.stop_after_timeouts = stop_after_timeouts

    def record_wav(self, seconds, output_path):
        self.recorded.append((seconds, output_path))
        self.last_recording = AudioRecording(1000.0, 1012.0, float(seconds))
        output_path.write_bytes(b"fake wav")

    def wait_for_silence(self, timeout=None):
        self.silence_waits.append(timeout)
        return len(self.silence_waits) >= self.stop_after_timeouts


def test_detection_loop_recognized_then_silence_clears_state():
    loop = DetectionLoop(FakeConfig({"RECOGNITION_MIN_RECHECK_WAIT_SECONDS": 1}))
    loop.audio = FakeDetectionAudio(stop_after_timeouts=1)
    loop.last_display_result = RecognitionResult(status="stopped", playback_status="stopped")
    written = []

    original_wav_stats = detection_module.wav_stats
    original_identify = detection_module.identify_with_shazam
    original_write_state = detection_module.write_state
    original_now_iso = detection_module.now_iso
    try:
        detection_module.wav_stats = lambda path: (100.0, 12.0)
        detection_module.identify_with_shazam = lambda path: RecognitionResult(
            status="recognized",
            title="Song",
            artist="Artist",
            track_duration_ms=180000,
            match_offset_seconds=10.0,
            recognized_at="1970-01-01T00:16:54+00:00",
        )
        detection_module.write_state = lambda path, result: written.append(result.to_dict())
        detection_module.now_iso = lambda: "2026-07-01T00:00:00+00:00"

        loop._playing_loop()
    finally:
        detection_module.wav_stats = original_wav_stats
        detection_module.identify_with_shazam = original_identify
        detection_module.write_state = original_write_state
        detection_module.now_iso = original_now_iso

    assert loop.shazam_request_count == 1
    assert loop.last_display_result.status == "stopped"
    assert loop.last_display_result.playback_status == "stopped"
    recognized_states = [state for state in written if state["status"] == "recognized" and state["title"] == "Song"]
    assert recognized_states
    recognized = recognized_states[0]
    assert recognized["recording_stopped_at"] == "1970-01-01T00:16:52+00:00"
    assert recognized["recognition_pipeline_delay_seconds"] == 2.0
    assert recognized["progress_start_seconds"] == 24.0
    assert written[-1]["status"] == "stopped"
    assert written[-1]["title"] == ""


def test_detection_loop_quiet_sample_does_not_call_shazam():
    loop = DetectionLoop(FakeConfig({}))
    loop.audio = FakeDetectionAudio(stop_after_timeouts=1)
    called = []

    original_wav_stats = detection_module.wav_stats
    original_identify = detection_module.identify_with_shazam
    original_write_state = detection_module.write_state
    try:
        detection_module.wav_stats = lambda path: (0.0, 12)

        def fail_identify(path):
            called.append(path)
            raise AssertionError("Shazam should not run for quiet samples")

        detection_module.identify_with_shazam = fail_identify
        detection_module.write_state = lambda path, result: None
        result, duration, rms = loop._record_and_identify_sample("playing")
    finally:
        detection_module.wav_stats = original_wav_stats
        detection_module.identify_with_shazam = original_identify
        detection_module.write_state = original_write_state

    assert result.status == "stopped"
    assert duration == 12
    assert rms == 0.0
    assert loop.shazam_request_count == 0
    assert called == []


def main():
    tests = [
        test_audio_activity_events_are_gated_once,
        test_clear_buffer_can_keep_preroll,
        test_record_wav_writes_valid_file,
        test_detection_loop_legacy_gate_fallback_and_overrides,
        test_detection_loop_minimum_recheck_wait,
        test_detection_loop_recognized_then_silence_clears_state,
        test_detection_loop_quiet_sample_does_not_call_shazam,
    ]
    for test in tests:
        test()
    print("audio/detection unit tests passed")


if __name__ == "__main__":
    main()
