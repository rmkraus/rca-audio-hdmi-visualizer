import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_LYRICS_CACHE_DIR = "/var/lib/rca-hdmi-visualizer/lyrics-cache"
DEFAULT_LYRICS_TIMEOUT_SECONDS = 12.0
DEFAULT_LYRICS_NEGATIVE_CACHE_SECONDS = 86400
DEFAULT_LYRICS_USER_AGENT = "rca-hdmi-visualizer/1.0 (personal now-playing kiosk)"
LRCLIB_BASE_URL = "https://lrclib.net"
LRC_LINE_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\](.*)")


def is_disabled(value):
    return str(value or "").strip().lower() in {"", "0", "false", "no", "off", "disabled", "none"}


def normalize_text(value):
    return " ".join(str(value or "").strip().lower().split())


def track_duration_seconds(state):
    for key in ("track_duration_ms", "duration"):
        raw = state.get(key)
        try:
            value = float(raw or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            continue
        if key == "track_duration_ms":
            return int(round(value / 1000.0))
        return int(round(value))
    return 0


def cache_key(state):
    parts = [
        normalize_text(state.get("artist")),
        normalize_text(state.get("title")),
        normalize_text(state.get("album")),
        str(track_duration_seconds(state) or ""),
    ]
    raw = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def empty_lyrics(reason="not_available", cache="none", **extra):
    payload = {
        "available": False,
        "synced": False,
        "source": "lrclib",
        "cache": cache,
        "reason": reason,
        "lines": [],
    }
    payload.update(extra)
    return payload


def parse_lrc(text):
    lines = []
    for raw in str(text or "").splitlines():
        matches = list(LRC_LINE_RE.finditer(raw))
        if not matches:
            continue
        lyric_text = matches[-1].group(4).strip()
        for match in matches:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            fraction = match.group(3) or "0"
            # LRCLIB commonly uses centiseconds, but handle 1-3 digit fractions.
            frac_seconds = int(fraction) / (10 ** len(fraction))
            lines.append({"time": minutes * 60 + seconds + frac_seconds, "text": lyric_text})
    lines.sort(key=lambda item: item["time"])
    return lines


def lrclib_request(path, params, timeout, user_agent):
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "", 0)})
    url = LRCLIB_BASE_URL + path + ("?" + query if query else "")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def best_search_result(results):
    for item in results or []:
        if item.get("syncedLyrics"):
            return item
    return (results or [None])[0]


def fetch_lrclib_lyrics(state, timeout=DEFAULT_LYRICS_TIMEOUT_SECONDS, user_agent=DEFAULT_LYRICS_USER_AGENT):
    title = state.get("title") or ""
    artist = state.get("artist") or ""
    album = state.get("album") or ""
    duration = track_duration_seconds(state)
    if not title or not artist:
        return empty_lyrics("missing_track_metadata")

    exact_params = {"track_name": title, "artist_name": artist}
    if album:
        exact_params["album_name"] = album
    if duration:
        exact_params["duration"] = duration

    try:
        data = lrclib_request("/api/get", exact_params, timeout, user_agent)
    except Exception:
        data = None

    if not isinstance(data, dict) or not data.get("syncedLyrics"):
        try:
            results = lrclib_request(
                "/api/search",
                {"track_name": title, "artist_name": artist},
                timeout,
                user_agent,
            )
            data = best_search_result(results if isinstance(results, list) else [])
        except Exception:
            data = None

    if not isinstance(data, dict):
        return empty_lyrics("not_found", trackName=title, artistName=artist, albumName=album, duration=duration)

    synced = data.get("syncedLyrics") or ""
    lines = parse_lrc(synced)
    if not lines:
        return empty_lyrics(
            "no_synced_lyrics",
            trackName=data.get("trackName") or title,
            artistName=data.get("artistName") or artist,
            albumName=data.get("albumName") or album,
            duration=data.get("duration") or duration,
        )

    return {
        "available": True,
        "synced": True,
        "source": "lrclib",
        "cache": "miss",
        "id": data.get("id"),
        "trackName": data.get("trackName") or title,
        "artistName": data.get("artistName") or artist,
        "albumName": data.get("albumName") or album,
        "duration": data.get("duration") or duration,
        "offset_seconds": 0.0,
        "lines": lines,
    }


def read_cache(path, negative_ttl):
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    if not payload.get("available"):
        age = time.time() - float(payload.get("cached_at", 0) or 0)
        if age > float(negative_ttl):
            return None
    payload["cache"] = "hit"
    return payload


def write_cache(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    cached = dict(payload)
    cached["cached_at"] = time.time()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cached, sort_keys=True, indent=2) + "\n")
    tmp.replace(path)


def log_lyrics_not_found(config, state, payload, key):
    log_path = config.str("LYRICS_NOT_FOUND_LOG", "")
    if is_disabled(log_path):
        return
    record = {
        "logged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": "lrclib",
        "reason": payload.get("reason") or "not_found",
        "trackName": payload.get("trackName") or state.get("title") or "",
        "artistName": payload.get("artistName") or state.get("artist") or "",
        "albumName": payload.get("albumName") or state.get("album") or "",
        "duration": payload.get("duration") or track_duration_seconds(state),
        "cache_key": key,
    }
    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def lyrics_for_state(state, config):
    if not config.bool("LYRICS_ENABLED", False):
        return empty_lyrics("disabled")
    if str(config.str("LYRICS_PROVIDER", "lrclib") or "lrclib").lower() != "lrclib":
        return empty_lyrics("unsupported_provider")
    if state.get("status") != "recognized":
        return empty_lyrics("not_recognized")

    key = cache_key(state)
    cache_dir = Path(config.str("LYRICS_CACHE_DIR", DEFAULT_LYRICS_CACHE_DIR))
    cache_path = cache_dir / (key + ".json")
    negative_ttl = config.int("LYRICS_NEGATIVE_CACHE_SECONDS", DEFAULT_LYRICS_NEGATIVE_CACHE_SECONDS)
    cached = read_cache(cache_path, negative_ttl)
    if cached is not None:
        offset = config.float("LYRICS_OFFSET_SECONDS", 0.0)
        cached["offset_seconds"] = offset
        return cached

    timeout = config.float("LYRICS_TIMEOUT_SECONDS", DEFAULT_LYRICS_TIMEOUT_SECONDS)
    user_agent = config.str("LYRICS_USER_AGENT", DEFAULT_LYRICS_USER_AGENT)
    payload = fetch_lrclib_lyrics(state, timeout=timeout, user_agent=user_agent)
    payload["offset_seconds"] = config.float("LYRICS_OFFSET_SECONDS", 0.0)
    write_cache(cache_path, payload)
    if not payload.get("available"):
        log_lyrics_not_found(config, state, payload, key)
    return payload
