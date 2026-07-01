#!/usr/bin/env python3
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rca_visualizer.audio_interface import AudioChunk, AudioInterface
from rca_visualizer.detection import DetectionLoop
from rca_visualizer.recognition import RecognitionResult


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
    with audio._condition:
        audio._chunks.append(AudioChunk((b"\x10\x00") * 10, 16, time.time(), 1.0))
        audio._chunks.append(AudioChunk((b"\x20\x00") * 10, 32, time.time(), 1.0))
        audio._condition.notify_all()

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.wav"
        audio.record_wav(2, path)
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getframerate() == 10
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == 20


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


def main():
    tests = [
        test_audio_activity_events_are_gated_once,
        test_clear_buffer_can_keep_preroll,
        test_record_wav_writes_valid_file,
        test_detection_loop_legacy_gate_fallback_and_overrides,
        test_detection_loop_minimum_recheck_wait,
    ]
    for test in tests:
        test()
    print("audio/detection unit tests passed")


if __name__ == "__main__":
    main()
