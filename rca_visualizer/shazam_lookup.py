import asyncio
import json
import sys

from shazamio import Shazam


async def recognize(path):
    shazam = Shazam()
    if hasattr(shazam, "recognize"):
        return await shazam.recognize(path)
    return await shazam.recognize_song(path)


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

    return {
        "status": "recognized",
        "title": str(track.get("title") or ""),
        "artist": str(track.get("subtitle") or ""),
        "album": album,
        "score": 1.0,
        "provider": "shazam",
        "acoustid": str(track.get("key") or ""),
        "musicbrainz_recording_id": "",
        "raw": {
            "key": str(track.get("key") or ""),
            "url": str(track.get("url") or ""),
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
