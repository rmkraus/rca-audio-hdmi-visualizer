import asyncio
import json
import sys
import urllib.parse
import urllib.request

from shazamio import Shazam


def first_match_offset(data):
    matches = data.get("matches") if isinstance(data, dict) else []
    if not matches:
        return None
    try:
        return float(matches[0].get("offset"))
    except (TypeError, ValueError, AttributeError):
        return None


async def recognize(path):
    shazam = Shazam()
    if hasattr(shazam, "recognize"):
        return await shazam.recognize(path)
    return await shazam.recognize_song(path)


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


def track_to_result(data):
    track = data.get("track") if isinstance(data, dict) else None
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

    sections = track.get("sections") or []
    metadata = []
    for section in sections:
        metadata.extend(section.get("metadata") or [])
    album = ""
    for item in metadata:
        title = str(item.get("title") or "").lower()
        if title in {"album", "release"}:
            album = str(item.get("text") or "")
            break

    track_adam_id = str(track.get("trackadamid") or "")
    track_duration_ms = lookup_itunes_duration(track_adam_id)
    offset = first_match_offset(data)

    return {
        "status": "recognized",
        "title": str(track.get("title") or ""),
        "artist": str(track.get("subtitle") or ""),
        "album": album,
        "score": 1.0,
        "provider": "shazam",
        "acoustid": str(track.get("key") or ""),
        "musicbrainz_recording_id": "",
        "track_duration_ms": track_duration_ms,
        "match_offset_seconds": offset,
        "raw": {
            "key": str(track.get("key") or ""),
            "url": str(track.get("url") or ""),
            "trackadamid": track_adam_id,
            "isrc": str(track.get("isrc") or ""),
            "matches": data.get("matches") or [],
        },
        "message": str(track.get("url") or ""),
    }


async def main_async(path):
    data = await recognize(path)
    print(json.dumps(track_to_result(data), sort_keys=True))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: shazam_lookup.py AUDIO_FILE", file=sys.stderr)
        return 2
    asyncio.run(main_async(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
