import sys
import tempfile
import threading
import time
from pathlib import Path

from .audio_interface import AudioInterface
from .recognition_provider import identify_with_shazam, wav_stats
from .recognition_state import (
    log_result,
    now_iso,
    progress_start_seconds,
    request_rate,
    set_metrics,
    sleep_until_progress,
    write_state,
)
from .recognition_types import RecognitionResult, clear_track_fields, copy_display_result
from .defaults import (
    DEFAULT_AUDIO_GATE_SECONDS,
    DEFAULT_AUDIO_PREROLL_SECONDS,
    DEFAULT_AUDIO_START_GATE_SECONDS,
    DEFAULT_AUDIO_STOP_GATE_SECONDS,
    DEFAULT_MAX_RECHECK_WAIT_SECONDS,
    DEFAULT_MIN_RMS,
    DEFAULT_MISSING_DURATION_RECHECK_SECONDS,
    DEFAULT_NO_MATCH_BACKOFF_SECONDS,
    DEFAULT_NO_MATCH_LIMIT,
    DEFAULT_MIN_RECHECK_WAIT_SECONDS,
    DEFAULT_PROGRESS_OFFSET_PADDING_SECONDS,
    DEFAULT_PROGRESS_RESUME_PERCENT,
    DEFAULT_RATELIMIT_BACKOFF_SECONDS,
    DEFAULT_RATELIMIT_REQUESTS_PER_MIN,
    DEFAULT_SAMPLE_SECONDS,
    DEFAULT_STATE_PATH,
)


class DetectionLoop(object):
    """Playback activity and Shazam detection state machine.

    Runs the voice-activation style loop:
    - wait while stopped until the audio interface reports gated audio start
    - record a 12s sample and run Shazam while playing
    - after successful recognition, wait for either gated silence or recheck timeout
    - on gated silence, clear metadata and return to stopped

    The loop owns its own worker thread when started with start(). Events expose
    whether the loop is currently waiting or actively detecting.
    """

    def __init__(self, config):
        self.config = config
        self.waiting = threading.Event()
        self.actively_detecting = threading.Event()
        self.stopped = threading.Event()
        self._stop_requested = threading.Event()
        self._thread = None
        self._error = None

        self.state_path = Path(config.str("NOW_PLAYING_STATE", DEFAULT_STATE_PATH))
        self.min_rms = config.float("RECOGNITION_MIN_RMS", DEFAULT_MIN_RMS)
        self.sample_seconds = config.int("RECOGNITION_SAMPLE_SECONDS", DEFAULT_SAMPLE_SECONDS)
        self.no_match_limit = config.int("RECOGNITION_NO_MATCH_LIMIT", DEFAULT_NO_MATCH_LIMIT)
        self.no_match_backoff = config.int("RECOGNITION_NO_MATCH_BACKOFF_SECONDS", DEFAULT_NO_MATCH_BACKOFF_SECONDS)
        self.ratelimit_threshold = config.float(
            "RECOGNITION_RATELIMIT_REQUESTS_PER_MIN",
            DEFAULT_RATELIMIT_REQUESTS_PER_MIN,
        )
        self.ratelimit_backoff = config.int("RECOGNITION_RATELIMIT_BACKOFF_SECONDS", DEFAULT_RATELIMIT_BACKOFF_SECONDS)
        self.progress_resume_percent = config.float(
            "RECOGNITION_PROGRESS_RESUME_PERCENT",
            DEFAULT_PROGRESS_RESUME_PERCENT,
        )
        self.max_recheck_wait = config.int("RECOGNITION_MAX_RECHECK_WAIT_SECONDS", DEFAULT_MAX_RECHECK_WAIT_SECONDS)
        self.missing_duration_recheck = config.int(
            "RECOGNITION_MISSING_DURATION_RECHECK_SECONDS",
            DEFAULT_MISSING_DURATION_RECHECK_SECONDS,
        )
        self.progress_padding = config.float(
            "RECOGNITION_PROGRESS_OFFSET_PADDING_SECONDS",
            DEFAULT_PROGRESS_OFFSET_PADDING_SECONDS,
        )
        self.min_recheck_wait = config.int("RECOGNITION_MIN_RECHECK_WAIT_SECONDS", DEFAULT_MIN_RECHECK_WAIT_SECONDS)
        self.audio_gate_seconds = config.float("RECOGNITION_AUDIO_GATE_SECONDS", DEFAULT_AUDIO_GATE_SECONDS)
        legacy_gate_present = hasattr(config, "values") and "RECOGNITION_AUDIO_GATE_SECONDS" in config.values
        start_gate_default = self.audio_gate_seconds if legacy_gate_present else DEFAULT_AUDIO_START_GATE_SECONDS
        stop_gate_default = self.audio_gate_seconds if legacy_gate_present else DEFAULT_AUDIO_STOP_GATE_SECONDS
        self.audio_start_gate_seconds = config.float(
            "RECOGNITION_AUDIO_START_GATE_SECONDS",
            start_gate_default,
        )
        self.audio_stop_gate_seconds = config.float(
            "RECOGNITION_AUDIO_STOP_GATE_SECONDS",
            stop_gate_default,
        )
        self.audio_preroll_seconds = config.float("RECOGNITION_AUDIO_PREROLL_SECONDS", DEFAULT_AUDIO_PREROLL_SECONDS)

        self.shazam_request_count = 0
        self.shazam_request_times = []
        self.last_display_result = None
        self.audio = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self.stopped.clear()
        self._error = None
        self._thread = threading.Thread(target=self._thread_main)
        self._thread.daemon = True
        self._thread.start()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout=timeout)
        if self._error is not None:
            raise self._error

    def stop(self):
        self._stop_requested.set()
        if self.audio is not None:
            self.audio.stop()

    def run_forever(self):
        self.stopped.clear()
        try:
            self._run_loop()
        finally:
            self.stopped.set()

    def _thread_main(self):
        try:
            self.run_forever()
        except Exception as exc:
            self._error = exc
            print("detection loop error: %s" % exc, file=sys.stderr, flush=True)

    def _set_waiting(self):
        self.actively_detecting.clear()
        self.waiting.set()

    def _set_detecting(self):
        self.waiting.clear()
        self.actively_detecting.set()

    def _set_idle(self):
        self.waiting.clear()
        self.actively_detecting.clear()

    def _run_loop(self):
        self.audio = AudioInterface.from_config(
            self.config,
            min_rms=self.min_rms,
            gate_seconds=self.audio_gate_seconds,
            start_gate_seconds=self.audio_start_gate_seconds,
            stop_gate_seconds=self.audio_stop_gate_seconds,
            preroll_seconds=self.audio_preroll_seconds,
        )
        self.audio.start()
        self.last_display_result = RecognitionResult(status="stopped", playback_status="stopped")

        try:
            while not self._stop_requested.is_set():
                stopped_result = RecognitionResult(
                    status="stopped",
                    playback_status="stopped",
                    recognized_at=self._now_iso(),
                    message="waiting for %.1f seconds of audio" % self.audio_start_gate_seconds,
                )
                set_metrics(stopped_result, self.shazam_request_count, request_rate(self.shazam_request_times))
                write_state(self.state_path, stopped_result)
                self.last_display_result = stopped_result

                self._set_waiting()
                if not self.audio.wait_for_audio(timeout=1.0):
                    continue
                self.audio.clear_buffer(keep_preroll=True)

                self._playing_loop()
        finally:
            self._set_idle()
            if self.audio is not None:
                self.audio.stop()

    def _playing_loop(self):
        no_match_count = 0
        playback_status = "playing"

        while not self._stop_requested.is_set():
            listening_status = "recognized" if self.last_display_result.status == "recognized" else "listening"
            listening_result = self._copy_display_result(
                self.last_display_result,
                status=listening_status,
                playback_status=playback_status,
                listening=True,
                message="recording %s second sample" % self.sample_seconds,
            )
            set_metrics(listening_result, self.shazam_request_count, request_rate(self.shazam_request_times))
            write_state(self.state_path, listening_result)

            self._set_detecting()
            try:
                result, duration, rms = self._record_and_identify_sample(playback_status)
            except Exception as exc:
                err = RecognitionResult(status="error", playback_status=playback_status, recognized_at=self._now_iso(), message=str(exc))
                set_metrics(err, self.shazam_request_count, request_rate(self.shazam_request_times))
                write_state(self.state_path, err)
                self.last_display_result = err
                print("recognition error: %s" % exc, file=sys.stderr, flush=True)
                self._set_waiting()
                if self._wait_for_silence_or_timeout(self.no_match_backoff):
                    break
                continue

            if result.status == "stopped":
                write_state(self.state_path, result)
                self.last_display_result = result
                self._log_result(result)
                break

            if result.shazam_requests_per_min > self.ratelimit_threshold:
                no_match_count = 0
                result.status = "ratelimit"
                result.ratelimit = True
                result.backing_off = True
                self._clear_track_fields(result)
                result.message = "RATELIMIT: %.1f Shazam requests/min > %.1f; backing off for %s seconds" % (
                    result.shazam_requests_per_min,
                    self.ratelimit_threshold,
                    self.ratelimit_backoff,
                )
                write_state(self.state_path, result)
                self.last_display_result = result
                self._log_result(result)
                self._set_waiting()
                if self._wait_for_silence_or_timeout(self.ratelimit_backoff):
                    break
                continue

            if result.status == "recognized":
                no_match_count = 0
                result.progress_padding_seconds = self.progress_padding
                result.progress_start_seconds = self._progress_start_seconds(result, self.progress_padding)
                write_state(self.state_path, result)
                self.last_display_result = result
                self._log_result(result)
                timeout = self._playback_recheck_timeout(result)
                self._set_waiting()
                if self._wait_for_silence_or_timeout(timeout):
                    break
                continue

            if result.status in {"no_match", "error"}:
                no_match_count += 1
                if self.last_display_result.status == "recognized":
                    kept = self._copy_display_result(
                        self.last_display_result,
                        status="recognized",
                        playback_status=playback_status,
                        listening=False,
                        backing_off=no_match_count >= self.no_match_limit,
                        message=result.message
                        or "bad Shazam response %s/%s; keeping previous song on screen"
                        % (no_match_count, self.no_match_limit),
                    )
                    kept.duration = duration
                    kept.rms = rms
                    set_metrics(kept, self.shazam_request_count, request_rate(self.shazam_request_times))
                    write_state(self.state_path, kept)
                    self.last_display_result = kept
                    self._log_result(kept)
                else:
                    self._clear_track_fields(result)
                    result.playback_status = playback_status
                    result.message = result.message or "bad Shazam response %s/%s" % (no_match_count, self.no_match_limit)
                    set_metrics(result, self.shazam_request_count, request_rate(self.shazam_request_times))
                    write_state(self.state_path, result)
                    self.last_display_result = result
                    self._log_result(result)

                if no_match_count >= self.no_match_limit:
                    self.last_display_result.backing_off = True
                    self.last_display_result.message = "backing off for %s seconds after %s bad Shazam responses" % (
                        self.no_match_backoff,
                        self.no_match_limit,
                    )
                    set_metrics(self.last_display_result, self.shazam_request_count, request_rate(self.shazam_request_times))
                    write_state(self.state_path, self.last_display_result)
                    no_match_count = 0
                    self._set_waiting()
                    if self._wait_for_silence_or_timeout(self.no_match_backoff):
                        break
                continue

            self._clear_track_fields(result)
            result.backing_off = True
            write_state(self.state_path, result)
            self.last_display_result = result
            self._log_result(result)
            self._set_waiting()
            if self._wait_for_silence_or_timeout(self.no_match_backoff):
                break

        stopped_result = RecognitionResult(
            status="stopped",
            playback_status="stopped",
            recognized_at=self._now_iso(),
            message="stopped after %.1f seconds of silence" % self.audio_stop_gate_seconds,
        )
        set_metrics(stopped_result, self.shazam_request_count, request_rate(self.shazam_request_times))
        write_state(self.state_path, stopped_result)
        self.last_display_result = stopped_result

    def _record_and_identify_sample(self, playback_status):
        with tempfile.TemporaryDirectory(prefix="rca-recognition-") as tmpdir:
            sample = Path(tmpdir) / "sample.wav"
            self.audio.record_wav(self.sample_seconds, sample)
            rms, duration = wav_stats(sample)
            if rms < self.min_rms:
                result = RecognitionResult(
                    status="stopped",
                    playback_status="stopped",
                    recognized_at=self._now_iso(),
                    duration=duration,
                    rms=rms,
                    message="stopped after quiet sample; RMS %.1f below threshold %.1f" % (rms, self.min_rms),
                )
                set_metrics(result, self.shazam_request_count, request_rate(self.shazam_request_times))
                return result, duration, rms

            self.shazam_request_count += 1
            self.shazam_request_times.append(time.time())
            result = identify_with_shazam(sample)
            result.duration = duration
            result.rms = rms
            result.playback_status = playback_status
            set_metrics(result, self.shazam_request_count, request_rate(self.shazam_request_times))
            return result, duration, rms

    def _wait_for_silence_or_timeout(self, timeout):
        self._set_waiting()
        return self.audio.wait_for_silence(timeout=max(0, float(timeout or 0)))

    def _playback_recheck_timeout(self, result):
        wait_for = sleep_until_progress(
            result,
            self.progress_resume_percent,
            missing_duration_sleep=self.missing_duration_recheck,
            max_wait=self.max_recheck_wait,
        )
        return max(int(self.min_recheck_wait), int(wait_for))

    @staticmethod
    def _copy_display_result(base, status, playback_status, listening=False, backing_off=False, ratelimit=False, message=""):
        return copy_display_result(
            base,
            status=status,
            playback_status=playback_status,
            listening=listening,
            backing_off=backing_off,
            ratelimit=ratelimit,
            message=message,
        )

    @staticmethod
    def _clear_track_fields(result):
        return clear_track_fields(result)

    @staticmethod
    def _progress_start_seconds(result, padding):
        return progress_start_seconds(result, padding)

    @staticmethod
    def _now_iso():
        return now_iso()

    @staticmethod
    def _log_result(result):
        return log_result(result)
