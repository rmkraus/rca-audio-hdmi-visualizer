import json
import os
import time
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def iso_from_timestamp(timestamp):
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()


def timestamp_from_iso(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def write_state(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), str(path))


def set_metrics(result, request_count=0, requests_per_min=0.0, response_counts=None, last_request=None):
    result.shazam_request_count = int(request_count or 0)
    result.shazam_requests_per_min = float(requests_per_min or 0.0)
    counts = response_counts or {}
    result.shazam_response_count = int(sum(int(value or 0) for value in counts.values()))
    result.shazam_recognized_count = int(counts.get("recognized", 0) or 0)
    result.shazam_no_match_count = int(counts.get("no_match", 0) or 0)
    result.shazam_error_count = int(counts.get("error", 0) or 0)
    if last_request:
        result.shazam_last_request_id = str(last_request.get("id") or "")
        result.shazam_last_request_started_at = str(last_request.get("started_at") or "")
        result.shazam_last_response_at = str(last_request.get("response_at") or "")
        result.shazam_last_response_status = str(last_request.get("status") or "")
        result.shazam_last_request_duration_seconds = last_request.get("duration_seconds")
    return result


def format_log_fields(fields):
    parts = []
    for key in sorted(fields):
        value = fields[key]
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ")
        parts.append("%s=%s" % (key, text))
    return " | ".join(parts)


def log_shazam_request(event, **fields):
    suffix = format_log_fields(fields)
    if suffix:
        print("shazam | %s | %s" % (event, suffix), flush=True)
    else:
        print("shazam | %s" % event, flush=True)


def request_rate(request_times, window_seconds=60):
    now = time.time()
    while request_times and request_times[0] < now - float(window_seconds):
        request_times.pop(0)
    return float(len(request_times))


def progress_start_seconds(result, padding_seconds):
    if result.match_offset_seconds is None:
        return None
    try:
        delay = result.recognition_pipeline_delay_seconds
        if delay is None:
            delay = padding_seconds
        return float(result.match_offset_seconds) + float(result.duration or 0) + float(delay or 0)
    except (TypeError, ValueError):
        return None


def sleep_until_progress(result, target_percent, missing_duration_sleep=60, max_wait=150):
    if not result.track_duration_ms or result.progress_start_seconds is None:
        return int(missing_duration_sleep)
    track_seconds = float(result.track_duration_ms) / 1000.0
    target_seconds = track_seconds * (float(target_percent) / 100.0)
    sleep_for = max(0, int(round(target_seconds - float(result.progress_start_seconds))))
    if max_wait and max_wait > 0:
        sleep_for = min(sleep_for, int(max_wait))
    return sleep_for


def playback_recheck_timeout(result, progress_resume_percent, missing_duration_recheck, max_recheck_wait):
    return sleep_until_progress(
        result,
        progress_resume_percent,
        missing_duration_sleep=missing_duration_recheck,
        max_wait=max_recheck_wait,
    )


def wait_for_playback_stop(audio, timeout_seconds):
    if timeout_seconds is None:
        return audio.wait_for_silence(timeout=None)
    return audio.wait_for_silence(timeout=max(0, float(timeout_seconds)))


def log_result(result):
    print(
        "%s/%s: %s - %s provider=%s score=%.3f rms=%s reqs=%s rpm=%.1f %s"
        % (
            result.playback_status or "unknown",
            result.status,
            result.artist,
            result.title,
            result.provider,
            result.score,
            "%.1f" % result.rms if result.rms is not None else "",
            result.shazam_request_count,
            result.shazam_requests_per_min,
            result.message,
        ),
        flush=True,
    )
