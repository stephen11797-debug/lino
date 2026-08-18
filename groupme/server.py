#!/usr/bin/env python3
"""
GroupMe-style app server (Python stdlib only).

Run:
    python3 server.py            # port 8002
    python3 server.py 9000       # custom port

Features: groups, chat, voice messages, photos, live video calls
over WebRTC (works best on the same network / local machine).
"""
import base64
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = 8002
HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "media")
os.makedirs(MEDIA, exist_ok=True)

lock = threading.RLock()
users = {}                 # name -> True
groups = {}                # id -> {"name","members":set,"rev":int,"msgs":[],"call":id|None}
calls = {}                 # id -> {"group":gid,"active":bool,"signals":[],"seq":int}


def now():
    return int(time.time() * 1000)


def save_media(data_url, prefix):
    m = re.match(r"data:([\w/+-]+);base64,(.*)", data_url or "", re.S)
    if not m:
        return None
    mime, b64 = m.group(1), m.group(2)
    if "audio" in mime:
        ext = "webm" if "webm" in mime else "ogg" if "ogg" in mime else "m4a"
    elif "png" in mime:
        ext = "png"
    else:
        ext = "jpg"
    name = prefix + "_" + uuid.uuid4().hex + "." + ext
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    with open(os.path.join(MEDIA, name), "wb") as f:
        f.write(raw)
    return name


def find_html():
    cand = os.path.join(HERE, "app.html")
    return cand if os.path.isfile(cand) else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
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

    def send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return {}
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    def group_or(self, gid):
        g = groups.get(gid)
        if not g:
            self.send_json({"error": "group not found"}, 404)
            return None
        return g

    # ---------------- users & groups ----------------

    def api_login(self, body):
        name = str(body.get("name", "")).strip()
        if not name:
            return self.send_json({"error": "Name required"}, 400)
        with lock:
            users[name] = True
            mine = [{"id": gid, "name": g["name"], "members": sorted(g["members"]),
                     "call": g.get("call")}
                    for gid, g in groups.items() if name in g["members"]]
            return self.send_json({"ok": True, "groups": mine})

    def api_group_create(self, body):
        name = str(body.get("name", "")).strip()
        by = str(body.get("by", "")).strip()
        if not name:
            return self.send_json({"error": "Group name required"}, 400)
        with lock:
            gid = uuid.uuid4().hex[:8]
            g = {"name": name, "members": set(), "rev": 0, "msgs": [], "call": None}
            if by:
                users[by] = True
                g["members"].add(by)
            groups[gid] = g
            return self.send_json({"ok": True, "group": {"id": gid, "name": name,
                                   "members": sorted(g["members"]), "call": None}})

    def api_group_join(self, body, gid):
        name = str(body.get("name", "")).strip()
        with lock:
            g = self.group_or(gid)
            if not g:
                return
            users[name] = True
            g["members"].add(name)
            g["rev"] += 1
            g["msgs"].append({"id": uuid.uuid4().hex, "from": "system", "text":
                              name + " joined the group", "ts": now(), "media": None,
                              "mime": None, "dur": None})
            return self.send_json({"ok": True, "group": {"id": gid, "name": g["name"],
                                   "members": sorted(g["members"]), "call": g.get("call")}})

    def api_group_info(self, gid):
        with lock:
            g = self.group_or(gid)
            if not g:
                return
            return self.send_json({"id": gid, "name": g["name"],
                                   "members": sorted(g["members"]),
                                   "rev": g["rev"], "messages": g["msgs"],
                                   "call": g.get("call")})

    def api_group_poll(self, gid, qs):
        with lock:
            g = self.group_or(gid)
            if not g:
                return
            try:
                since = int(qs.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since = 0
            msgs = g["msgs"][since:] if since <= g["rev"] else []
            return self.send_json({"rev": g["rev"], "messages": msgs,
                                   "call": g.get("call")})

    def api_group_send(self, body, gid):
        frm = str(body.get("from", "")).strip()
        text = str(body.get("text", "")).strip()
        if not frm or not text:
            return self.send_json({"error": "from and text required"}, 400)
        with lock:
            g = self.group_or(gid)
            if not g:
                return
            g["rev"] += 1
            g["msgs"].append({"id": uuid.uuid4().hex, "from": frm, "text": text,
                              "ts": now(), "media": None, "mime": None, "dur": None})
            return self.send_json({"ok": True, "rev": g["rev"]})

    def api_group_media(self, body, gid, prefix):
        frm = str(body.get("from", "")).strip()
        data = body.get("data", "")
        mime = str(body.get("mime", ""))
        dur = body.get("dur", None)
        if not frm or not data:
            return self.send_json({"error": "from and data required"}, 400)
        with lock:
            g = self.group_or(gid)
            if not g:
                return
            fn = save_media(data, prefix)
            if not fn:
                return self.send_json({"error": "bad media"}, 400)
            g["rev"] += 1
            g["msgs"].append({"id": uuid.uuid4().hex, "from": frm, "text": "",
                              "ts": now(), "media": "/media/" + fn,
                              "mime": mime, "dur": dur})
            return self.send_json({"ok": True, "rev": g["rev"]})

    # ---------------- WebRTC call signaling ----------------

    def api_call_start(self, body):
        gid = str(body.get("group", "")).strip()
        frm = str(body.get("from", "")).strip()
        with lock:
            g = groups.get(gid)
            if not g:
                return self.send_json({"error": "group not found"}, 404)
            cid = uuid.uuid4().hex[:10]
            calls[cid] = {"group": gid, "active": True, "seq": 0, "signals": []}
            g["call"] = cid
            return self.send_json({"ok": True, "call_id": cid})

    def api_call_end(self, body):
        cid = str(body.get("call_id", "")).strip()
        with lock:
            c = calls.get(cid)
            if c:
                c["active"] = False
                g = groups.get(c["group"])
                if g and g.get("call") == cid:
                    g["call"] = None
            return self.send_json({"ok": True})

    def api_call_info(self, qs):
        cid = qs.get("call_id", [""])[0]
        with lock:
            c = calls.get(cid)
            if not c:
                return self.send_json({"active": False})
            return self.send_json({"active": c["active"], "group": c["group"]})

    def api_call_signal(self, body):
        cid = str(body.get("call_id", "")).strip()
        frm = str(body.get("from", "")).strip()
        typ = str(body.get("type", ""))
        to = body.get("to")  # None = broadcast
        if not cid or not frm or not typ:
            return self.send_json({"error": "call_id/from/type required"}, 400)
        with lock:
            c = calls.get(cid)
            if not c or not c["active"]:
                return self.send_json({"error": "call not active"}, 404)
            c["seq"] += 1
            c["signals"].append({"seq": c["seq"], "from": frm, "to": to,
                                 "type": typ, "sdp": body.get("sdp"),
                                 "candidate": body.get("candidate")})
            return self.send_json({"ok": True, "seq": c["seq"]})

    def api_call_poll(self, qs):
        cid = qs.get("call_id", [""])[0]
        user = qs.get("user", [""])[0]
        try:
            since = int(qs.get("since", ["0"])[0])
        except (TypeError, ValueError):
            since = 0
        with lock:
            c = calls.get(cid)
            if not c:
                return self.send_json({"active": False, "signals": [], "seq": 0})
            out = []
            for s in c["signals"]:
                if s["seq"] > since and s["from"] != user:
                    if s["to"] is None or s["to"] == user:
                        out.append(s)
            return self.send_json({"active": c["active"], "seq": c["seq"], "signals": out})

    # ---------------- dispatch ----------------

    def do_GET(self):
        parts = urlparse(self.path)
        path = parts.path
        if path in ("/", "/index.html", "/app.html"):
            html = find_html()
            if not html:
                return self.send_json({"error": "app.html missing"}, 500)
            return self.send_file(html, "text/html; charset=utf-8")
        if path.startswith("/media/"):
            name = path.split("/")[-1]
            if "/" in name or "\\" in name or not name:
                return self.send_json({"error": "bad"}, 400)
            ctype = ("audio/webm" if name.endswith(".webm")
                     else "audio/ogg" if name.endswith(".ogg")
                     else "audio/mp4" if name.endswith(".m4a")
                     else "image/jpeg" if name.endswith(".jpg")
                     else "image/png" if name.endswith(".png")
                     else "application/octet-stream")
            return self.send_file(os.path.join(MEDIA, name), ctype)
        if path == "/favicon.svg":
            return self.send_file(os.path.join(HERE, "favicon.svg"),
                                  "image/svg+xml")
        if path.startswith("/api/call/info"):
            return self.api_call_info(parts.query and {k: [v] for k, v in
                                     [kv.split("=", 1) for kv in parts.query.split("&")]} or {})
        if path.startswith("/api/call/poll"):
            qs = {}
            for kv in parts.query.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    qs.setdefault(k, []).append(v)
            return self.api_call_poll(qs)
        m = re.match(r"^/api/groups/([\w-]+)/poll$", path)
        if m:
            qs = {}
            for kv in parts.query.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    qs.setdefault(k, []).append(v)
            return self.api_group_poll(m.group(1), qs)
        m = re.match(r"^/api/groups/([\w-]+)$", path)
        if m:
            return self.api_group_info(m.group(1))
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        body = self.read_body()
        path = urlparse(self.path).path
        if path == "/api/login":
            return self.api_login(body)
        if path == "/api/groups":
            return self.api_group_create(body)
        if path == "/api/call/start":
            return self.api_call_start(body)
        if path == "/api/call/end":
            return self.api_call_end(body)
        if path == "/api/call/signal":
            return self.api_call_signal(body)
        m = re.match(r"^/api/groups/([\w-]+)/join$", path)
        if m:
            return self.api_group_join(body, m.group(1))
        m = re.match(r"^/api/groups/([\w-]+)/send$", path)
        if m:
            return self.api_group_send(body, m.group(1))
        m = re.match(r"^/api/groups/([\w-]+)/voice$", path)
        if m:
            return self.api_group_media(body, m.group(1), "voice")
        m = re.match(r"^/api/groups/([\w-]+)/photo$", path)
        if m:
            return self.api_group_media(body, m.group(1), "photo")
        return self.send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    global PORT, HOST
    args = sys.argv[1:]
    if len(args) >= 1:
        try:
            PORT = int(args[0])
        except ValueError:
            print("Invalid port, using 8002.")
    if len(args) >= 2:
        HOST = args[1]
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("GroupMe server running")
    print("  On this computer:  http://localhost:{0}".format(PORT))
    print("  From phone/tablet: http://<this-computer-ip>:{0}".format(PORT))
    print("  Stop with Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")


if __name__ == "__main__":
    main()
