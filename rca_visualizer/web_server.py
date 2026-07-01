import argparse
import json
import mimetypes
import posixpath
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import unquote, urlparse

from .config import RuntimeConfig

WEB_DIR = Path(__file__).resolve().parent / "web"


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class NowPlayingHandler(SimpleHTTPRequestHandler):
    state_path = Path("/var/lib/rca-hdmi-visualizer/now-playing.json")

    def translate_path(self, path):
        """Serve files from WEB_DIR without depending on the process cwd.

        The installer replaces /opt/rca-hdmi-visualizer atomically. If the web
        service keeps running from the old deleted cwd, Python 3.6's default
        SimpleHTTPRequestHandler raises FileNotFoundError on every static
        request. Resolve paths from WEB_DIR directly instead.
        """
        path = urlparse(path).path
        path = posixpath.normpath(unquote(path))
        parts = [part for part in path.split("/") if part and part not in {".", ".."}]
        resolved = WEB_DIR
        for part in parts:
            resolved = resolved / part
        return str(resolved)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/now-playing":
            self.serve_state()
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def serve_state(self):
        if self.state_path.exists():
            try:
                payload = json.loads(self.state_path.read_text())
            except Exception as exc:
                payload = {"status": "error", "message": str(exc)}
        else:
            payload = {"status": "waiting", "message": "No now-playing state yet"}
        body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local now-playing web frontend server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", default="", help="Override now-playing JSON state path")
    args = parser.parse_args(argv)

    mimetypes.add_type("text/javascript", ".js")
    config = RuntimeConfig.load()
    state = args.state or config.str("NOW_PLAYING_STATE", "/var/lib/rca-hdmi-visualizer/now-playing.json")
    NowPlayingHandler.state_path = Path(state)

    server = ThreadingHTTPServer((args.host, args.port), NowPlayingHandler)
    print("Serving now-playing UI at http://%s:%s/" % (args.host, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
