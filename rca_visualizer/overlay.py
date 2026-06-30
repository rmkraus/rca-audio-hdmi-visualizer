import argparse
import json
from pathlib import Path
from tkinter import BOTH, CENTER, Tk, font, ttk

from .config import RuntimeConfig


class OverlayState:
    def __init__(
        self,
        status="waiting",
        title="",
        artist="",
        album="",
        score=0.0,
        recognized_at="",
        message="",
    ):
        self.status = status
        self.title = title
        self.artist = artist
        self.album = album
        self.score = score
        self.recognized_at = recognized_at
        self.message = message


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

        self.title_label.place(relx=0.5, rely=0.42, anchor=CENTER)
        self.artist_label.place(relx=0.5, rely=0.55, anchor=CENTER)
        self.meta_label.place(relx=0.5, rely=0.66, anchor=CENTER)

    def load_state(self):
        if not self.state_path.exists():
            return OverlayState(message="No recognition state yet")
        try:
            data = json.loads(self.state_path.read_text())
        except Exception as exc:
            return OverlayState(status="error", message=str(exc))
        return OverlayState(
            status=str(data.get("status") or "waiting"),
            title=str(data.get("title") or ""),
            artist=str(data.get("artist") or ""),
            album=str(data.get("album") or ""),
            score=float(data.get("score") or 0.0),
            recognized_at=str(data.get("recognized_at") or ""),
            message=str(data.get("message") or ""),
        )

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
                meta_parts.append("AcoustID score %.2f" % state.score)
            self.meta_label.configure(text="  •  ".join(meta_parts))
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
            else:
                self.artist_label.configure(text="Waiting for a recognized record")
            self.meta_label.configure(text=state.message)
        else:
            self.title_label.configure(text="")
            self.artist_label.configure(text="")
            self.meta_label.configure(text="")
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
