import audioop
import subprocess
import threading
import time
import wave
from collections import deque

from .defaults import DEFAULT_CHANNELS, DEFAULT_MIN_RMS, DEFAULT_SAMPLE_RATE

SAMPLE_WIDTH_BYTES = 2
FULL_SCALE_16_BIT = 32768.0


def rms_to_dbfs(rms):
    if rms is None or float(rms) <= 0:
        return -120.0
    # audioop.rms returns sample amplitude for signed 16-bit PCM.
    import math

    return 20.0 * math.log10(min(float(rms), FULL_SCALE_16_BIT) / FULL_SCALE_16_BIT)


class AudioChunk(object):
    def __init__(self, data, rms, captured_at, duration_seconds):
        self.data = data
        self.rms = rms
        self.captured_at = captured_at
        self.duration_seconds = duration_seconds


class AudioRecording(object):
    def __init__(self, started_at, stopped_at, duration_seconds):
        self.started_at = started_at
        self.stopped_at = stopped_at
        self.duration_seconds = duration_seconds


class AudioActivityEvent(object):
    def __init__(self, kind, at, rms, gate_seconds):
        self.kind = kind
        self.at = at
        self.rms = rms
        self.gate_seconds = gate_seconds


class AudioInterface(object):
    """Continuous audio capture and activity detector.

    The interface owns one long-running parec stream. A reader thread pulls fixed
    chunks, computes RMS, stores chunks in a rolling buffer, and emits start/stop
    activity events after configurable consecutive audio/silence gates.
    """

    def __init__(
        self,
        parec_cmd,
        sample_rate=DEFAULT_SAMPLE_RATE,
        channels=DEFAULT_CHANNELS,
        min_rms=DEFAULT_MIN_RMS,
        gate_seconds=5.0,
        start_gate_seconds=None,
        stop_gate_seconds=None,
        preroll_seconds=2.0,
        chunk_seconds=0.5,
        max_queue_seconds=90.0,
    ):
        self.parec_cmd = list(parec_cmd)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.min_rms = float(min_rms)
        self.start_gate_seconds = float(start_gate_seconds if start_gate_seconds is not None else gate_seconds)
        self.stop_gate_seconds = float(stop_gate_seconds if stop_gate_seconds is not None else gate_seconds)
        self.preroll_seconds = float(preroll_seconds)
        self.chunk_seconds = float(chunk_seconds)
        self.bytes_per_second = self.sample_rate * self.channels * SAMPLE_WIDTH_BYTES
        self.chunk_bytes = max(1, int(self.bytes_per_second * self.chunk_seconds))
        self.max_chunks = max(1, int(float(max_queue_seconds) / self.chunk_seconds))
        self.preroll_chunks = max(0, int(round(self.preroll_seconds / self.chunk_seconds)))

        self.audio_started = threading.Event()
        self.audio_stopped = threading.Event()
        self.audio_stopped.set()

        self._chunks = deque()
        self._chunk_bytes_total = 0
        self._preroll = deque()
        self._events = deque()
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread = None
        self._process = None
        self._error = None
        self._consecutive_audio = 0.0
        self._consecutive_silence = 0.0
        self._is_audio_active = False
        self.last_recording = None

    @classmethod
    def from_config(
        cls,
        config,
        min_rms=None,
        gate_seconds=5.0,
        start_gate_seconds=None,
        stop_gate_seconds=None,
        preroll_seconds=2.0,
        chunk_seconds=0.5,
    ):
        user = config.str("VISUALIZER_USER", "")
        source = config.str("RECOGNITION_SOURCE", "") or get_audio_device(
            "source", config.str("SOURCE_MATCH", "usb"), user
        )
        rate = config.int("RECOGNITION_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)
        channels = config.int("RECOGNITION_CHANNELS", DEFAULT_CHANNELS)
        threshold = DEFAULT_MIN_RMS if min_rms is None else min_rms
        parec_cmd = [
            "parec",
            "--device=%s" % source,
            "--format=s16le",
            "--rate=%s" % int(rate),
            "--channels=%s" % int(channels),
        ]
        if user:
            parec_cmd = ["runuser", "-u", user, "--"] + user_runtime_env_args(user) + parec_cmd
        return cls(
            parec_cmd,
            sample_rate=rate,
            channels=channels,
            min_rms=threshold,
            gate_seconds=gate_seconds,
            start_gate_seconds=start_gate_seconds,
            stop_gate_seconds=stop_gate_seconds,
            preroll_seconds=preroll_seconds,
            chunk_seconds=chunk_seconds,
        )

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        with self._condition:
            self._stop_event.clear()
            self._error = None
            self._chunks.clear()
            self._chunk_bytes_total = 0
            self._preroll.clear()
            self._events.clear()
            self._consecutive_audio = 0.0
            self._consecutive_silence = 0.0
            self._is_audio_active = False
            self.audio_started.clear()
            self.audio_stopped.set()
        self._process = subprocess.Popen(
            self.parec_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._thread = threading.Thread(target=self._reader_loop)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except TypeError:
                self._process.wait()
            except subprocess.TimeoutExpired:
                self._process.kill()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=5)
        with self._condition:
            self._thread = None
            self._process = None

    def _reader_loop(self):
        try:
            while not self._stop_event.is_set():
                stdout = self._process.stdout
                if stdout is None:
                    raise RuntimeError("audio stream stdout is unavailable")
                data = stdout.read(self.chunk_bytes)
                if not data:
                    raise RuntimeError("audio stream ended")
                duration = float(len(data)) / float(self.bytes_per_second or 1)
                rms = audioop.rms(data, SAMPLE_WIDTH_BYTES)
                chunk = AudioChunk(data, rms, time.time(), duration)
                with self._condition:
                    self._update_activity_locked(rms, duration)
                    self._append_chunk_locked(chunk)
                    self._preroll.append(chunk)
                    while len(self._preroll) > self.preroll_chunks:
                        self._preroll.popleft()
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._error = exc
                self._condition.notify_all()

    def _append_chunk_locked(self, chunk):
        self._chunks.append(chunk)
        self._chunk_bytes_total += len(chunk.data)
        max_bytes = max(1, int(self.bytes_per_second * self.max_chunks * self.chunk_seconds))
        while self._chunks and self._chunk_bytes_total > max_bytes:
            removed = self._chunks.popleft()
            self._chunk_bytes_total -= len(removed.data)

    def _emit_event_locked(self, kind, rms, gate_seconds):
        event = AudioActivityEvent(kind, time.time(), rms, gate_seconds)
        self._events.append(event)

    def _update_activity_locked(self, rms, duration):
        if float(rms) >= self.min_rms:
            self._consecutive_audio += duration
            self._consecutive_silence = 0.0
            if (not self._is_audio_active) and self._consecutive_audio >= self.start_gate_seconds:
                self._is_audio_active = True
                self.audio_started.set()
                self.audio_stopped.clear()
                self._emit_event_locked("started", rms, self.start_gate_seconds)
        else:
            self._consecutive_silence += duration
            self._consecutive_audio = 0.0
            if self._is_audio_active and self._consecutive_silence >= self.stop_gate_seconds:
                self._is_audio_active = False
                self.audio_stopped.set()
                self.audio_started.clear()
                self._emit_event_locked("stopped", rms, self.stop_gate_seconds)

    # Backward-compatible test hook. Production updates are condition-locked.
    def _update_activity(self, rms, duration):
        with self._condition:
            self._update_activity_locked(rms, duration)
            self._condition.notify_all()

    def raise_if_failed(self):
        if self._error is not None:
            raise RuntimeError(str(self._error))

    def clear_buffer(self, keep_preroll=False):
        with self._condition:
            self._chunks.clear()
            self._chunk_bytes_total = 0
            if keep_preroll:
                for chunk in self._preroll:
                    self._append_chunk_locked(chunk)

    def grab_chunk(self, timeout=None):
        deadline = None if timeout is None else time.time() + float(timeout)
        with self._condition:
            while not self._chunks:
                self.raise_if_failed()
                if deadline is None:
                    self._condition.wait(0.5)
                else:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    self._condition.wait(min(0.5, remaining))
            chunk = self._chunks.popleft()
            self._chunk_bytes_total -= len(chunk.data)
            return chunk

    def wait_for_event(self, kinds=None, timeout=None):
        if kinds is not None:
            kinds = set(kinds)
        deadline = None if timeout is None else time.time() + float(timeout)
        with self._condition:
            while True:
                self.raise_if_failed()
                for index, event in enumerate(list(self._events)):
                    if kinds is None or event.kind in kinds:
                        self._events.rotate(-index)
                        found = self._events.popleft()
                        self._events.rotate(index)
                        return found
                if deadline is None:
                    self._condition.wait(0.5)
                else:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    self._condition.wait(min(0.5, remaining))

    def wait_for_audio(self, timeout=None):
        if self.audio_started.is_set():
            return True
        event = self.wait_for_event(kinds=["started"], timeout=timeout)
        return event is not None or self.audio_started.is_set()

    def wait_for_silence(self, timeout=None):
        if self.audio_stopped.is_set():
            return True
        event = self.wait_for_event(kinds=["stopped"], timeout=timeout)
        return event is not None or self.audio_stopped.is_set()

    def snapshot_wav(self, seconds, output_path, timeout=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        target_bytes = max(1, int(float(seconds) * self.bytes_per_second))
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        chunks = []
        total_bytes = 0

        with self._condition:
            while True:
                self.raise_if_failed()
                chunks = list(self._chunks)
                total_bytes = sum(len(chunk.data) for chunk in chunks)
                if total_bytes >= target_bytes:
                    break
                if deadline is None:
                    self._condition.wait(0.5)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(min(0.5, remaining))

        selected = []
        selected_bytes = 0
        for chunk in reversed(chunks):
            selected.append(chunk)
            selected_bytes += len(chunk.data)
            if selected_bytes >= target_bytes:
                break
        selected.reverse()
        data = b"".join(chunk.data for chunk in selected)
        if len(data) > target_bytes:
            data = data[-target_bytes:]
        duration = float(len(data)) / float(self.bytes_per_second or 1)
        rms = audioop.rms(data, SAMPLE_WIDTH_BYTES) if data else 0
        stopped_at = selected[-1].captured_at if selected else time.time()
        started_at = stopped_at - duration
        self.last_recording = AudioRecording(started_at, stopped_at, duration)

        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav.setframerate(self.sample_rate)
            wav.writeframes(data)
        return output_path, duration, rms

    def record_wav(self, seconds, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + float(seconds)
        chunks = []
        captured = 0.0
        while captured < float(seconds):
            timeout = max(0.5, deadline - time.monotonic() + 2.0)
            chunk = self.grab_chunk(timeout=timeout)
            if chunk is None:
                raise RuntimeError("timed out waiting for audio chunk")
            chunks.append(chunk)
            captured += chunk.duration_seconds

        if chunks:
            stopped_at = max(chunk.captured_at for chunk in chunks)
            started_at = min(chunk.captured_at - chunk.duration_seconds for chunk in chunks)
            self.last_recording = AudioRecording(started_at, stopped_at, captured)
        else:
            now = time.time()
            self.last_recording = AudioRecording(now, now, 0.0)

        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav.setframerate(self.sample_rate)
            wav.writeframes(b"".join(chunk.data for chunk in chunks))
        return output_path


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
