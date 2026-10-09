#!/usr/bin/env python3
"""Small local web server and YouTube public metadata API for VidTagra."""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8000
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
RATE_LOCK = threading.Lock()
REQUEST_TIMES: dict[str, list[float]] = {}


def video_id_from_url(raw: str) -> str | None:
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            video_id = parts[1]
        else:
            return None
    else:
        return None
    return video_id if ID_PATTERN.fullmatch(video_id) else None


def relevance(tag: str, title: str, description: str) -> int:
    """Explainable lexical relevance estimate; not YouTube ranking data."""
    tokenize = lambda text: set(re.findall(r"[\w]+", text.lower()))
    tag_words = tokenize(tag)
    title_words = tokenize(title)
    description_words = tokenize(description[:4000])
    if not tag_words:
        return 0
    title_match = len(tag_words & title_words) / len(tag_words)
    description_match = len(tag_words & description_words) / len(tag_words)
    exact_phrase = 1.0 if tag.lower() in title.lower() else 0.0
    score = 35 + 45 * title_match + 15 * description_match + 5 * exact_phrase
    return max(0, min(100, round(score)))


class Handler(BaseHTTPRequestHandler):
    server_version = "VidTagra/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def send_json(self, status: int, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {"ok": True, "service": "VidTagra"})
            return
        if path not in {"/", "/index.html"}:
            self.send_error(404, "Not found")
            return
        body = (ROOT / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/inspect":
            self.send_json(404, {"error": "Endpoint not found."})
            return
        ip = self.client_address[0]
        now = time.time()
        with RATE_LOCK:
            recent = [stamp for stamp in REQUEST_TIMES.get(ip, []) if now - stamp < 60]
            if len(recent) >= 12:
                self.send_json(429, {"error": "Too many requests. Please wait a minute and try again."})
                return
            REQUEST_TIMES[ip] = recent + [now]
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 4096:
                self.send_json(400, {"error": "Please provide a YouTube video URL."})
                return
            body = json.loads(self.rfile.read(size))
            raw_url = body.get("url", "") if isinstance(body, dict) else ""
            video_id = video_id_from_url(raw_url) if isinstance(raw_url, str) else None
            if not video_id:
                self.send_json(400, {"error": "Enter a valid YouTube video, Shorts, or youtu.be URL."})
                return
            canonical_url = f"https://www.youtube.com/watch?v={video_id}"
            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "socket_timeout": 12,
                "extractor_retries": 1,
                "ignoreerrors": False,
            }
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(canonical_url, download=False)
            if not info:
                self.send_json(404, {"error": "YouTube did not return video information for that link."})
                return
            tags = list(dict.fromkeys(tag.strip() for tag in (info.get("tags") or []) if isinstance(tag, str) and tag.strip()))
            title = info.get("title") or "YouTube video"
            description = info.get("description") or ""
            response = {
                "video": {
                    "id": video_id,
                    "title": title,
                    "channel": info.get("channel") or info.get("uploader") or "",
                    "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "duration": info.get("duration"),
                    "viewCount": info.get("view_count"),
                },
                "tags": [{"name": tag, "score": relevance(tag, title, description)} for tag in tags],
                "tagCount": len(tags),
            }
            self.send_json(200, response)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Request body must be valid JSON."})
        except Exception as exc:
            message = str(exc).lower()
            if "private video" in message or "private" in message:
                error = "This video is private. Only publicly accessible videos can be inspected."
            elif "not available" in message or "unavailable" in message or "removed" in message:
                error = "This video is unavailable or has been removed."
            elif "timed out" in message or "timeout" in message:
                error = "YouTube took too long to respond. Please try again."
            else:
                error = "Could not retrieve this video. It may be restricted, unavailable, or temporarily blocked by YouTube."
            print(f"YouTube extraction error: {exc}")
            self.send_json(502, {"error": error})


if __name__ == "__main__":
    print(f"VidTagra running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
