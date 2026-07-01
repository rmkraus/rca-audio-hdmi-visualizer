import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BOTH, CENTER, Canvas, Tk, font, ttk

from .config import RuntimeConfig


class OverlayState:
    def __init__(
        self,
        status="waiting",
        playback_status="",
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


class NowPlayingOverlay:
    def __init__(self, config):
        self.config = config
        self.state_path = Path(config.str("NOW_PLAYING_STATE", "/var/lib/rca-hdmi-visualizer/now-playing.json"))
        self.poll_ms = config.int("OVERLAY_POLL_MSEC", 1000)
        self.show_unrecognized = config.bool("OVERLAY_SHOW_UNRECOGNIZED", False)

        self.root = Tk()
        self.root.title("Now Playing")
        self.root.configure(bg="black")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        if not self.show_unrecognized:
            self.root.withdraw()
        try:
            self.root.attributes("-alpha", config.float("OVERLAY_ALPHA", 0.78))
        except Exception:
            pass
        self.root.bind("<Escape>", lambda _event: self.root.destroy())
        self.root.bind("q", lambda _event: self.root.destroy())

        width = max(self.root.winfo_screenwidth(), 1280)
        title_size = max(42, min(92, width // 18))
        artist_size = max(28, min(58, width // 28))
        meta_size = max(18, min(34, width // 48))

        self.title_font = font.Font(family="DejaVu Sans", size=title_size, weight="bold")
        self.artist_font = font.Font(family="DejaVu Sans", size=artist_size, weight="normal")
        self.meta_font = font.Font(family="DejaVu Sans", size=meta_size, weight="normal")

        style = ttk.Style()
        style.configure("Overlay.TFrame", background="black")
        style.configure("Title.TLabel", background="black", foreground="white")
        style.configure("Artist.TLabel", background="black", foreground="#e6e6e6")
        style.configure("Meta.TLabel", background="black", foreground="#b8b8b8")

        self.frame = ttk.Frame(self.root, style="Overlay.TFrame", padding=48)
        self.frame.pack(fill=BOTH, expand=True)

        self.title_label = ttk.Label(
            self.frame,
            text="Listening…",
            style="Title.TLabel",
            font=self.title_font,
            anchor=CENTER,
            justify=CENTER,
            wraplength=int(width * 0.86),
        )
        self.artist_label = ttk.Label(
            self.frame,
            text="Waiting for a recognized record",
            style="Artist.TLabel",
            font=self.artist_font,
            anchor=CENTER,
            justify=CENTER,
            wraplength=int(width * 0.86),
        )
        self.meta_label = ttk.Label(
            self.frame,
            text="",
            style="Meta.TLabel",
            font=self.meta_font,
            anchor=CENTER,
            justify=CENTER,
            wraplength=int(width * 0.86),
        )
        self.progress_canvas = Canvas(
            self.frame,
            width=int(width * 0.68),
            height=26,
            bg="#202020",
            bd=0,
            highlightthickness=0,
        )
        self.time_label = ttk.Label(
            self.frame,
            text="",
            style="Meta.TLabel",
            font=self.meta_font,
            anchor=CENTER,
            justify=CENTER,
        )

        self.title_label.place(relx=0.5, rely=0.36, anchor=CENTER)
        self.artist_label.place(relx=0.5, rely=0.49, anchor=CENTER)
        self.meta_label.place(relx=0.5, rely=0.58, anchor=CENTER)
        self.progress_canvas.place(relx=0.5, rely=0.68, anchor=CENTER)
        self.time_label.place(relx=0.5, rely=0.73, anchor=CENTER)

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
        if not state.track_duration_ms or state.progress_start_seconds is None:
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
        self.progress_canvas.create_rectangle(0, 0, width, height, fill="#202020", outline="#505050")
        if current is None or not total:
            self.progress_canvas.place_forget()
            self.time_label.place_forget()
            return
        filled = int(width * min(1.0, max(0.0, current / total)))
        self.progress_canvas.create_rectangle(0, 0, filled, height, fill="#33d6c3", outline="")
        self.progress_canvas.place(relx=0.5, rely=0.68, anchor=CENTER)
        self.time_label.configure(text="%s / %s" % (format_time(current), format_time(total)))
        self.time_label.place(relx=0.5, rely=0.73, anchor=CENTER)

    def hide_progress(self):
        self.progress_canvas.place_forget()
        self.time_label.place_forget()

    def update_labels(self):
        state = self.load_state()
        if state.status == "recognized" and state.title:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.title_label.configure(text=state.title)
            self.artist_label.configure(text=state.artist or "Unknown artist")
            meta_parts = []
            if state.album:
                meta_parts.append(state.album)
            if state.score:
                meta_parts.append("%s score %.2f" % (state.provider.title() or "Recognition", state.score))
            self.meta_label.configure(text="  •  ".join(meta_parts))
            self.draw_progress(state)
        elif state.status == "stopped":
            self.root.deiconify()
            self.root.lift()
            self.title_label.configure(text="Stopped")
            self.artist_label.configure(text="No audio detected")
            self.meta_label.configure(text=state.message)
            self.hide_progress()
        elif self.show_unrecognized:
            self.root.deiconify()
            self.root.lift()
            self.title_label.configure(text="Listening…")
            if state.status == "silence":
                self.artist_label.configure(text="No audio detected")
            elif state.status == "low_score":
                self.artist_label.configure(text="Possible match below confidence threshold")
            elif state.status == "error":
                self.artist_label.configure(text="Recognition error")
            elif state.status == "no_match":
                self.artist_label.configure(text="No match yet")
            else:
                self.artist_label.configure(text="Waiting for a recognized record")
            self.meta_label.configure(text=state.message)
            self.hide_progress()
        else:
            self.title_label.configure(text="")
            self.artist_label.configure(text="")
            self.meta_label.configure(text="")
            self.hide_progress()
            self.root.withdraw()
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
