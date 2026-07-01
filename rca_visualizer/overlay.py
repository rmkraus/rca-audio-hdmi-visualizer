import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BOTH, Canvas, Tk, font, ttk

from .config import RuntimeConfig


class OverlayState:
    def __init__(
        self,
        status="waiting",
        playback_status="",
        listening=False,
        backing_off=False,
        shazam_request_count=0,
        shazam_requests_per_min=0.0,
        title="",
        artist="",
        album="",
        score=0.0,
        recognized_at="",
        provider="",
        message="",
        track_duration_ms=0,
        progress_start_seconds=None,
        match_offset_seconds=None,
    ):
        self.status = status
        self.playback_status = playback_status
        self.listening = bool(listening)
        self.backing_off = bool(backing_off)
        self.shazam_request_count = int(shazam_request_count or 0)
        self.shazam_requests_per_min = float(shazam_requests_per_min or 0.0)
        self.title = title
        self.artist = artist
        self.album = album
        self.score = score
        self.provider = provider
        self.recognized_at = recognized_at
        self.message = message
        self.track_duration_ms = int(track_duration_ms or 0)
        self.progress_start_seconds = progress_start_seconds
        self.match_offset_seconds = match_offset_seconds


def parse_iso(value):
    if not value:
        return None
    try:
        # Python 3.6 compatibility: datetime.fromisoformat is unavailable on
        # JetPack's system Python. State timestamps are UTC ISO strings like
        # 2026-07-01T03:11:39.912934+00:00.
        text = value.replace("Z", "+00:00")
        text = text.rsplit("+", 1)[0]
        if "." in text:
            parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f")
        else:
            parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_time(seconds):
    try:
        seconds = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        seconds = 0
    return "%d:%02d" % (seconds // 60, seconds % 60)


def status_text(state):
    parts = []
    if state.playback_status == "playing":
        parts.append("Playing")
    elif state.playback_status == "stopped" or state.status == "stopped":
        parts.append("Stopped")
    if state.listening or state.status == "listening":
        parts.append("Listening")
    if state.backing_off or state.status == "backing_off":
        parts.append("Backing Off")
    return " + ".join(parts)


class NowPlayingOverlay:
    def __init__(self, config):
        self.config = config
        self.state_path = Path(config.str("NOW_PLAYING_STATE", "/var/lib/rca-hdmi-visualizer/now-playing.json"))
        self.poll_ms = config.int("OVERLAY_POLL_MSEC", 1000)

        self.root = Tk()
        self.root.title("Now Playing")
        self.root.configure(bg="black")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", config.float("OVERLAY_ALPHA", 1.0))
        except Exception:
            pass
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.bind("q", lambda _event: self.root.destroy())

        width = max(self.root.winfo_screenwidth(), 1280)
        self.normal_font = font.Font(family="DejaVu Sans", size=28, weight="normal")
        self.line_height = 44
        self.left = 64
        self.top = 56

        style = ttk.Style()
        style.configure("Overlay.TFrame", background="black")
        style.configure("Text.TLabel", background="black", foreground="white")

        self.frame = ttk.Frame(self.root, style="Overlay.TFrame", padding=0)
        self.frame.pack(fill=BOTH, expand=True)

        self.labels = []
        for i in range(5):
            label = ttk.Label(
                self.frame,
                text="",
                style="Text.TLabel",
                font=self.normal_font,
                anchor="w",
                justify="left",
                wraplength=int(width * 0.85),
            )
            label.place(x=self.left, y=self.top + i * self.line_height)
            self.labels.append(label)

        self.progress_canvas = Canvas(
            self.frame,
            width=int(width * 0.55),
            height=26,
            bg="#202020",
            bd=0,
            highlightthickness=0,
        )
        self.time_label = ttk.Label(
            self.frame,
            text="",
            style="Text.TLabel",
            font=self.normal_font,
            anchor="w",
            justify="left",
        )
        self.progress_y = self.top + 6 * self.line_height

    def load_state(self):
        if not self.state_path.exists():
            return OverlayState(message="No recognition state yet")
        try:
            data = json.loads(self.state_path.read_text())
        except Exception as exc:
            return OverlayState(status="error", message=str(exc))
        return OverlayState(
            status=str(data.get("status") or "waiting"),
            playback_status=str(data.get("playback_status") or ""),
            listening=bool(data.get("listening") or False),
            backing_off=bool(data.get("backing_off") or False),
            shazam_request_count=int(data.get("shazam_request_count") or 0),
            shazam_requests_per_min=float(data.get("shazam_requests_per_min") or 0.0),
            title=str(data.get("title") or ""),
            artist=str(data.get("artist") or ""),
            album=str(data.get("album") or ""),
            score=float(data.get("score") or 0.0),
            provider=str(data.get("provider") or ""),
            recognized_at=str(data.get("recognized_at") or ""),
            message=str(data.get("message") or ""),
            track_duration_ms=int(data.get("track_duration_ms") or 0),
            progress_start_seconds=data.get("progress_start_seconds"),
            match_offset_seconds=data.get("match_offset_seconds"),
        )

    def current_progress(self, state):
        if state.status != "recognized" or not state.track_duration_ms or state.progress_start_seconds is None:
            return None, None
        recognized_at = parse_iso(state.recognized_at)
        elapsed = 0.0
        if recognized_at is not None:
            elapsed = (datetime.now(timezone.utc) - recognized_at).total_seconds()
        total = float(state.track_duration_ms) / 1000.0
        current = min(total, max(0.0, float(state.progress_start_seconds) + elapsed))
        return current, total

    def draw_progress(self, state):
        current, total = self.current_progress(state)
        self.progress_canvas.delete("all")
        width = int(float(self.progress_canvas.cget("width")))
        height = int(float(self.progress_canvas.cget("height")))
        if current is None or not total:
            self.progress_canvas.place_forget()
            self.time_label.place_forget()
            return
        self.progress_canvas.create_rectangle(0, 0, width, height, fill="#202020", outline="#505050")
        filled = int(width * min(1.0, max(0.0, current / total)))
        self.progress_canvas.create_rectangle(0, 0, filled, height, fill="#33d6c3", outline="")
        self.progress_canvas.place(x=self.left, y=self.progress_y)
        self.time_label.configure(text="%s / %s" % (format_time(current), format_time(total)))
        self.time_label.place(x=self.left, y=self.progress_y + 38)

    def update_labels(self):
        state = self.load_state()
        good_track = state.status == "recognized"
        lines = [
            "Track: %s" % (state.title if good_track else ""),
            "Artist: %s" % (state.artist if good_track else ""),
            "Album: %s" % (state.album if good_track else ""),
            "Status: %s" % status_text(state),
            "Shazam Requests: %s reqs, %.1f reqs/m" % (state.shazam_request_count, state.shazam_requests_per_min),
        ]
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        for label, text in zip(self.labels, lines):
            label.configure(text=text)
        self.draw_progress(state)
        self.root.after(self.poll_ms, self.update_labels)

    def run(self):
        self.root.after(100, self.update_labels)
        self.root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fullscreen now-playing overlay")
    parser.add_argument("--state", default="", help="Override now-playing JSON state path")
    args = parser.parse_args(argv)

    config = RuntimeConfig.load()
    if args.state:
        config.values["NOW_PLAYING_STATE"] = args.state
    NowPlayingOverlay(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
