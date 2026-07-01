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
        data = await shazam.recognize(path)
    else:
        data = await shazam.recognize_song(path)

    track = data.get("track") if isinstance(data, dict) else None
    track_key = str((track or {}).get("key") or "")
    if track_key:
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
