import audioop
import subprocess
import threading
import time
import wave
from collections import deque

from .defaults import DEFAULT_CHANNELS, DEFAULT_MIN_RMS, DEFAULT_SAMPLE_RATE

SAMPLE_WIDTH_BYTES = 2


class AudioChunk(object):
    def __init__(self, data, rms, captured_at, duration_seconds):
        self.data = data
        self.rms = rms
        self.captured_at = captured_at
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
                    self._chunks.append(chunk)
                    self._preroll.append(chunk)
                    while len(self._chunks) > self.max_chunks:
                        self._chunks.popleft()
                    while len(self._preroll) > self.preroll_chunks:
                        self._preroll.popleft()
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._error = exc
                self._condition.notify_all()

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
            if keep_preroll:
                self._chunks.extend(self._preroll)

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
            return self._chunks.popleft()

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
