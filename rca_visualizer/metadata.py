from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    try:
        from typing import TypedDict as _TypedDict
    except ImportError:
        from typing_extensions import TypedDict as _TypedDict

    class NowPlayingMetadata(_TypedDict, total=False):
        status: str
        playback_status: str
        listening: bool
        backing_off: bool
        ratelimit: bool
        shazam_request_count: int
        shazam_requests_per_min: float
        title: str
        artist: str
        album: str
        score: float
        provider: str
        recognized_at: str
        duration: int
        rms: Optional[float]
        acoustid: str
        musicbrainz_recording_id: str
        track_duration_ms: int
        match_offset_seconds: Optional[float]
        progress_start_seconds: Optional[float]
        progress_padding_seconds: float
        raw: Optional[Dict[str, Any]]
        message: str
else:
    NowPlayingMetadata = dict


def empty_metadata(status="stopped", playback_status="stopped"):
    return NowPlayingMetadata(
        status=status,
        playback_status=playback_status,
        listening=False,
        backing_off=False,
        ratelimit=False,
        shazam_request_count=0,
        shazam_requests_per_min=0.0,
        title="",
        artist="",
        album="",
        score=0.0,
        provider="shazam",
        recognized_at="",
        duration=0,
        rms=None,
        acoustid="",
        musicbrainz_recording_id="",
        track_duration_ms=0,
        match_offset_seconds=None,
        progress_start_seconds=None,
        progress_padding_seconds=0,
        raw=None,
        message="",
    )
