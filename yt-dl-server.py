#!/usr/bin/env python3
"""
Stepjens YouTube download relay.

The Stepjens Studio app runs inside the Android-x86 VM. WebView's JS
cannot download YouTube directly (no CORS, DRM/signature required), so
this small host-side server does the actual yt-dlp work:

    GET /info?url=<yt url>                -> JSON metadata (title, author, thumb)
    GET /download?url=<yt url>&format=audio|video   -> streams the file back

The app calls it at http://10.0.2.2:PORT (VirtualBox NAT gateway = host)
and saves the bytes via SaveBridge into /sdcard/Download/Stepjens.

Usage:
    python3 yt-dl-server.py [port] [download-dir]
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
DLDIR = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/Downloads/Stepjens"))

os.makedirs(DLDIR, exist_ok=True)
_LOCK = threading.Lock()

_YTDLP = None
def find_ytdlp():
    global _YTDLP
    if _YTDLP is None:
        for c in ("yt-dlp", "youtube-dl"):
            p = os.popen("command -v " + c).read().strip()
            if p:
                _YTDLP = p
                break
    return _YTDLP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parts = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parts.query)
        url = (qs.get("url") or [""])[0].strip()
        if not url:
            return self.send_json({"error": "missing url"}, 400)
        yt = find_ytdlp()
        if not yt:
            return self.send_json({"error": "yt-dlp not installed on host"}, 500)

        if parts.path.startswith("/info"):
            try:
                p = subprocess.run([yt, "--no-playlist", "--no-warnings", "-j", url],
                                   capture_output=True, text=True, timeout=120)
                if p.returncode != 0:
                    return self.send_json({"error": (p.stderr or "yt-dlp failed").strip()[:300]}, 422)
                data = json.loads(p.stdout.splitlines()[0])
                return self.send_json({
                    "title": data.get("title", "Untitled"),
                    "author": data.get("uploader") or data.get("channel") or "Unknown",
                    "thumbnail": data.get("thumbnail"),
                    "duration": data.get("duration"),
                    "webpage_url": data.get("webpage_url") or url,
                })
            except subprocess.TimeoutExpired:
                return self.send_json({"error": "yt-dlp timed out"}, 500)
            except Exception as e:
                return self.send_json({"error": str(e)[:300]}, 500)

        if parts.path.startswith("/download"):
            fmt = (qs.get("format") or ["audio"])[0]
            if fmt not in ("audio", "video"):
                fmt = "audio"
            tmpdir = tempfile.mkdtemp(prefix="stepjens-dl-")
            try:
                args = [yt, "--no-playlist", "--no-warnings", "--no-progress",
                        "-o", os.path.join(tmpdir, "%(title).60s.%(ext)s"),
                        "-f", "bestaudio[ext=m4a]/bestaudio/best" if fmt == "audio"
                        else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]
                if fmt == "audio":
                    args += ["-x", "--audio-format", "mp3", "--audio-quality", "5"]
                p = subprocess.run(args + [url], capture_output=True, text=True, timeout=900)
                if p.returncode != 0:
                    return self.send_json({"error": (p.stderr or "yt-dlp failed").strip()[:300]}, 422)
                files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)
                         if os.path.isfile(os.path.join(tmpdir, f)) and f != ".ytdlp"]
                if not files:
                    return self.send_json({"error": "no file produced"}, 422)
                path = max(files, key=os.path.getsize)
                ctype = "audio/mpeg" if path.lower().endswith(".mp3") else \
                        "video/mp4" if path.lower().endswith(".mp4") else "application/octet-stream"
                size = os.path.getsize(path)
                disp = urllib.parse.quote(os.path.basename(path))
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + disp)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(262144)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except subprocess.TimeoutExpired:
                return self.send_json({"error": "download timed out"}, 500)
            except Exception as e:
                return self.send_json({"error": str(e)[:300]}, 500)
            finally:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)

        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        # Upload relay: app can push project/media files to the host folder.
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self.send_json({"error": "empty body"}, 400)
        parts = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parts.query)
        name = (qs.get("name") or [""])[0].strip()
        if not name or "/" in name or "\\" in name:
            return self.send_json({"error": "bad name"}, 400)
        try:
            data = self.rfile.read(length)
            with _LOCK:
                with open(os.path.join(DLDIR, name), "wb") as f:
                    f.write(data)
            return self.send_json({"ok": True, "name": name,
                                   "size": len(data),
                                   "path": os.path.join(DLDIR, name)})
        except Exception as e:
            return self.send_json({"error": str(e)[:300]}, 500)


def main():
    print("Stepjens YT relay on %s:%s -> %s" % (HOST, PORT, DLDIR), flush=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
