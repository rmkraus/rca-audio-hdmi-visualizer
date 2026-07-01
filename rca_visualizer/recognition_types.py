from .metadata import empty_metadata


class RecognitionResult(object):
    def __init__(
        self,
        status,
        title="",
        artist="",
        album="",
        score=0.0,
        provider="shazam",
        recognized_at="",
        duration=0,
        acoustid="",
        musicbrainz_recording_id="",
        track_duration_ms=0,
        match_offset_seconds=None,
        progress_start_seconds=None,
        progress_padding_seconds=0,
        playback_status="",
        listening=False,
        backing_off=False,
        ratelimit=False,
        shazam_request_count=0,
        shazam_requests_per_min=0.0,
        rms=None,
        raw=None,
        message="",
    ):
        self.status = status
        self.title = title
        self.artist = artist
        self.album = album
        self.score = score
        self.provider = provider
        self.recognized_at = recognized_at
        self.duration = duration
        # Kept for backward-compatible state JSON. For Shazam this stores the
        # Shazam track key, not an AcoustID UUID.
        self.acoustid = acoustid
        self.musicbrainz_recording_id = musicbrainz_recording_id
        self.track_duration_ms = int(track_duration_ms or 0)
        self.match_offset_seconds = match_offset_seconds
        self.progress_start_seconds = progress_start_seconds
        self.progress_padding_seconds = progress_padding_seconds
        self.playback_status = playback_status
        self.listening = bool(listening)
        self.backing_off = bool(backing_off)
        self.ratelimit = bool(ratelimit)
        self.shazam_request_count = int(shazam_request_count or 0)
        self.shazam_requests_per_min = float(shazam_requests_per_min or 0.0)
        self.rms = rms
        self.raw = raw
        self.message = message

    def to_dict(self):
        metadata = empty_metadata(status=self.status, playback_status=self.playback_status)
        metadata.update(
            {
                "status": self.status,
                "playback_status": self.playback_status,
                "listening": self.listening,
                "backing_off": self.backing_off,
                "ratelimit": self.ratelimit,
                "shazam_request_count": self.shazam_request_count,
                "shazam_requests_per_min": self.shazam_requests_per_min,
                "title": self.title,
                "artist": self.artist,
                "album": self.album,
                "score": self.score,
                "provider": self.provider,
                "recognized_at": self.recognized_at,
                "duration": self.duration,
                "rms": self.rms,
                "acoustid": self.acoustid,
                "musicbrainz_recording_id": self.musicbrainz_recording_id,
                "track_duration_ms": self.track_duration_ms,
                "match_offset_seconds": self.match_offset_seconds,
                "progress_start_seconds": self.progress_start_seconds,
                "progress_padding_seconds": self.progress_padding_seconds,
                "raw": self.raw,
                "message": self.message,
            }
        )
        return metadata


def clear_track_fields(result):
    result.title = ""
    result.artist = ""
    result.album = ""
    result.track_duration_ms = 0
    result.progress_start_seconds = None
    result.match_offset_seconds = None
    return result


def copy_display_result(base, status, playback_status, listening=False, backing_off=False, ratelimit=False, message=""):
    base = base or RecognitionResult(status="waiting")
    return RecognitionResult(
        status=status,
        title=base.title,
        artist=base.artist,
        album=base.album,
        score=base.score,
        provider=base.provider,
        recognized_at=base.recognized_at,
        duration=base.duration,
        acoustid=base.acoustid,
        musicbrainz_recording_id=base.musicbrainz_recording_id,
        track_duration_ms=base.track_duration_ms,
        match_offset_seconds=base.match_offset_seconds,
        progress_start_seconds=base.progress_start_seconds,
        progress_padding_seconds=base.progress_padding_seconds,
        playback_status=playback_status,
        listening=listening,
        backing_off=backing_off,
        ratelimit=ratelimit,
        rms=base.rms,
        raw=base.raw,
        message=message or base.message,
    )
