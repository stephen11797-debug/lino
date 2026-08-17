#!/usr/bin/env python3
"""
Voice Assistant remote relay (server).

Runs on a Windows, Linux, or Mac computer with no extra packages
(python stdlib only). Start it, then open the app from any device
on the same network (including Android phones) to talk together.

Usage:
    python3 server.py            # serve on port 8000, all interfaces
    python3 server.py 9000       # custom port
    python3 server.py 9000 192.168.1.50   # custom port and host
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "0.0.0.0"
PORT = 8000

messages = []          # list of {"type": "user"|"ai", "text": str}
rev = 0                # monotonic message revision
cond = threading.Condition()


def find_html():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "voice.html")
    return cand if os.path.isfile(cand) else None


def broadcast(entry):
    global rev
    with cond:
        messages.append(entry)
        rev += 1
        cond.notify_all()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def serve_html(self):
        html = find_html()
        if not html:
            self.send_json({"error": "voice.html not found next to server.py"}, 500)
            return
        try:
            with open(html, "rb") as f:
                data = f.read()
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def api_state(self):
        with cond:
            self.send_json({"rev": rev, "messages": messages})

    def api_poll(self):
        qs = parse_qs(urlparse(self.path).query)
        try:
            after = int(qs.get("since", ["0"])[0])
        except ValueError:
            after = 0
        deadline = time.time() + 25
        with cond:
            while rev == after and time.time() < deadline:
                cond.wait(deadline - time.time())
            new_msgs = messages[after:] if after <= rev else []
            self.send_json({"rev": rev, "messages": new_msgs})

    def api_send(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            payload = json.loads(raw or "{}")
            text = str(payload.get("text", "")).strip()
            kind = payload.get("type", "user")
            name = str(payload.get("name", "")).strip() or None
            client = str(payload.get("client", "")).strip() or None
        except Exception:
            self.send_json({"error": "bad body"}, 400)
            return
        if not text:
            self.send_json({"error": "empty"}, 400)
            return
        broadcast({"type": kind, "text": text, "name": name, "client": client})
        with cond:
            self.send_json({"rev": rev, "ok": True})

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.serve_html()
        elif path == "/api/state":
            self.api_state()
        elif path == "/api/poll":
            self.api_poll()
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/send":
            self.api_send()
        else:
            self.send_json({"error": "not found"}, 404)


def main():
    global HOST, PORT
    args = sys.argv[1:]
    if len(args) >= 1:
        try:
            PORT = int(args[0])
        except ValueError:
            print("Invalid port, using 8000.")
    if len(args) >= 2:
        HOST = args[1]
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Voice Assistant server running")
    print("  On this computer:  http://localhost:{0}".format(PORT))
    print("  From phone/tablet: http://<this-computer-ip>:{0}".format(PORT))
    print("  Stop with Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")


if __name__ == "__main__":
    main()