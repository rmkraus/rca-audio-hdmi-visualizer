import asyncio
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

from shazamio import Shazam


DEFAULT_TRACK_CACHE_DB = "/var/lib/rca-hdmi-visualizer/shazam-track-cache.sqlite3"


def first_match_offset(data):
    matches = data.get("matches") if isinstance(data, dict) else []
    if not matches:
        return None
    try:
        return max(0.0, float(matches[0].get("offset")))
    except (TypeError, ValueError, AttributeError):
        return None


def is_disabled(value):
    return str(value or "").strip().lower() in {"", "0", "false", "no", "off", "disabled", "none"}


def track_cache_path():
    return os.environ.get("SHAZAM_TRACK_CACHE_DB", DEFAULT_TRACK_CACHE_DB)


def cache_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_track_cache(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS shazam_track_cache (
            track_key TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            artist TEXT NOT NULL DEFAULT '',
            album TEXT NOT NULL DEFAULT '',
            shazam_url TEXT NOT NULL DEFAULT '',
            trackadamid TEXT NOT NULL DEFAULT '',
            isrc TEXT NOT NULL DEFAULT '',
            track_duration_ms INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1
        )
        """
    )


def read_track_cache(track_key):
    cache_db = track_cache_path()
    if not track_key or is_disabled(cache_db):
        return None
    try:
        with sqlite3.connect(cache_db, timeout=10) as db:
            ensure_track_cache(db)
            row = db.execute(
                "SELECT payload_json FROM shazam_track_cache WHERE track_key=?",
                (str(track_key),),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE shazam_track_cache SET last_seen_at=?, seen_count=seen_count + 1 WHERE track_key=?",
                (cache_now(), str(track_key)),
            )
            payload = json.loads(row[0])
            payload["cache"] = "hit"
            return payload
    except Exception:
        return None


def write_track_cache(result):
    track_key = str(result.get("acoustid") or (result.get("raw") or {}).get("key") or "")
    cache_db = track_cache_path()
    if result.get("status") != "recognized" or not track_key or is_disabled(cache_db):
        return
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    if raw.get("track_cache") == "hit":
        return
    payload = {
        "cache": "miss",
        "title": result.get("title") or "",
        "artist": result.get("artist") or "",
        "album": result.get("album") or "",
        "acoustid": track_key,
        "track_duration_ms": int(result.get("track_duration_ms") or 0),
        "raw": {
            "key": track_key,
            "url": raw.get("url") or result.get("message") or "",
            "trackadamid": raw.get("trackadamid") or "",
            "isrc": raw.get("isrc") or "",
        },
        "message": result.get("message") or raw.get("url") or "",
    }
    now = cache_now()
    try:
        path = os.path.dirname(cache_db)
        if path:
            os.makedirs(path, exist_ok=True)
        with sqlite3.connect(cache_db, timeout=10) as db:
            ensure_track_cache(db)
            db.execute(
                """
                INSERT INTO shazam_track_cache (
                    track_key, title, artist, album, shazam_url, trackadamid, isrc,
                    track_duration_ms, payload_json, first_seen_at, last_seen_at, seen_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(track_key) DO UPDATE SET
                    title=excluded.title,
                    artist=excluded.artist,
                    album=excluded.album,
                    shazam_url=excluded.shazam_url,
                    trackadamid=excluded.trackadamid,
                    isrc=excluded.isrc,
                    track_duration_ms=excluded.track_duration_ms,
                    payload_json=excluded.payload_json,
                    last_seen_at=excluded.last_seen_at,
                    seen_count=shazam_track_cache.seen_count + 1
                """,
                (
                    track_key,
                    payload["title"],
                    payload["artist"],
                    payload["album"],
                    payload["raw"].get("url") or "",
                    payload["raw"].get("trackadamid") or "",
                    payload["raw"].get("isrc") or "",
                    payload["track_duration_ms"],
                    json.dumps(payload, sort_keys=True),
                    now,
                    now,
                ),
            )
    except Exception:
        pass


async def recognize(path):
    shazam = Shazam()
    if hasattr(shazam, "recognize"):
        data = await shazam.recognize(path)
    else:
        data = await shazam.recognize_song(path)

    track = data.get("track") if isinstance(data, dict) else None
    track_key = str((track or {}).get("key") or "")
    if track_key:
        cached = read_track_cache(track_key)
        if cached:
            data["track_cache"] = cached
            return data
        try:
            about = await shazam.track_about(int(track_key))
            if isinstance(about, dict):
                data["track_about"] = about
        except Exception:
            pass
    return data


def lookup_itunes_duration(track_adam_id):
    if not track_adam_id:
        return 0
    url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode({"id": str(track_adam_id)})
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return 0
    for item in payload.get("results") or []:
        try:
            return int(item.get("trackTimeMillis") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def metadata_album(track):
    sections = track.get("sections") or []
    metadata = []
    for section in sections:
        metadata.extend(section.get("metadata") or [])
    for item in metadata:
        title = str(item.get("title") or "").lower()
        if title in {"album", "release"}:
            return str(item.get("text") or "")
    return ""


def first_nonempty(*values):
    for value in values:
        if value:
            return str(value)
    return ""


def track_to_result(data):
    track = data.get("track") if isinstance(data, dict) else None
    about = data.get("track_about") if isinstance(data, dict) else None
    if not isinstance(about, dict):
        about = {}
    cached = data.get("track_cache") if isinstance(data, dict) else None
    if isinstance(cached, dict) and track:
        raw = cached.get("raw") if isinstance(cached.get("raw"), dict) else {}
        offset = first_match_offset(data)
        return {
            "status": "recognized",
            "title": cached.get("title") or "",
            "artist": cached.get("artist") or "",
            "album": cached.get("album") or "",
            "score": 1.0,
            "provider": "shazam",
            "acoustid": str(cached.get("acoustid") or raw.get("key") or (track or {}).get("key") or ""),
            "musicbrainz_recording_id": "",
            "track_duration_ms": int(cached.get("track_duration_ms") or 0),
            "match_offset_seconds": offset,
            "raw": {
                "key": str(cached.get("acoustid") or raw.get("key") or (track or {}).get("key") or ""),
                "url": raw.get("url") or cached.get("message") or "",
                "trackadamid": raw.get("trackadamid") or "",
                "isrc": raw.get("isrc") or "",
                "matches": data.get("matches") or [],
                "track_cache": "hit",
            },
            "message": cached.get("message") or raw.get("url") or "",
        }
    if not track:
        return {
            "status": "no_match",
            "title": "",
            "artist": "",
            "album": "",
            "score": 0.0,
            "provider": "shazam",
            "raw": data,
            "message": "",
        }

    album = first_nonempty(metadata_album(track), metadata_album(about))
    track_adam_id = first_nonempty(track.get("trackadamid"), about.get("trackadamid"))
    track_duration_ms = lookup_itunes_duration(track_adam_id)
    offset = first_match_offset(data)

    return {
        "status": "recognized",
        "title": first_nonempty(track.get("title"), about.get("title")),
        "artist": first_nonempty(track.get("subtitle"), about.get("subtitle")),
        "album": album,
        "score": 1.0,
        "provider": "shazam",
        "acoustid": str(track.get("key") or ""),
        "musicbrainz_recording_id": "",
        "track_duration_ms": track_duration_ms,
        "match_offset_seconds": offset,
        "raw": {
            "key": str(track.get("key") or ""),
            "url": first_nonempty(track.get("url"), about.get("url")),
            "trackadamid": track_adam_id,
            "isrc": first_nonempty(track.get("isrc"), about.get("isrc")),
            "matches": data.get("matches") or [],
        },
        "message": first_nonempty(track.get("url"), about.get("url")),
    }


async def main_async(path):
    data = await recognize(path)
    result = track_to_result(data)
    write_track_cache(result)
    print(json.dumps(result, sort_keys=True))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: shazam_lookup.py AUDIO_FILE", file=sys.stderr)
        return 2
    asyncio.run(main_async(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
