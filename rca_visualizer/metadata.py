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
        shazam_response_count: int
        shazam_recognized_count: int
        shazam_no_match_count: int
        shazam_error_count: int
        shazam_last_request_id: str
        shazam_last_request_started_at: str
        shazam_last_response_at: str
        shazam_last_response_status: str
        shazam_last_request_duration_seconds: Optional[float]
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
        recording_started_at: str
        recording_stopped_at: str
        recognition_pipeline_delay_seconds: Optional[float]
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
        shazam_response_count=0,
        shazam_recognized_count=0,
        shazam_no_match_count=0,
        shazam_error_count=0,
        shazam_last_request_id="",
        shazam_last_request_started_at="",
        shazam_last_response_at="",
        shazam_last_response_status="",
        shazam_last_request_duration_seconds=None,
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
        recording_started_at="",
        recording_stopped_at="",
        recognition_pipeline_delay_seconds=None,
        raw=None,
        message="",
    )
