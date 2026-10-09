"""Vercel serverless endpoint for retrieving public YouTube video tags."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def get_video_id(raw: str) -> str | None:
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
    return video_id if VIDEO_ID.fullmatch(video_id) else None


def tag_score(tag: str, title: str, description: str) -> int:
    words = lambda text: set(re.findall(r"[\w]+", text.lower()))
    tag_words = words(tag)
    if not tag_words:
        return 0
    title_match = len(tag_words & words(title)) / len(tag_words)
    description_match = len(tag_words & words(description[:4000])) / len(tag_words)
    exact_phrase = float(tag.lower() in title.lower())
    return max(0, min(100, round(35 + 45 * title_match + 15 * description_match + 5 * exact_phrase)))


class handler(BaseHTTPRequestHandler):
    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 4096:
                self._json(400, {"error": "Please provide a YouTube video URL."})
                return
            request = json.loads(self.rfile.read(size))
            raw_url = request.get("url", "") if isinstance(request, dict) else ""
            video_id = get_video_id(raw_url) if isinstance(raw_url, str) else None
            if not video_id:
                self._json(400, {"error": "Enter a valid YouTube video, Shorts, or youtu.be URL."})
                return

            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "socket_timeout": 12,
                "extractor_retries": 1,
            }
            url = f"https://www.youtube.com/watch?v={video_id}"
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                self._json(404, {"error": "YouTube did not return video information for that link."})
                return

            title = info.get("title") or "YouTube video"
            description = info.get("description") or ""
            tags = list(dict.fromkeys(
                item.strip() for item in (info.get("tags") or [])
                if isinstance(item, str) and item.strip()
            ))
            self._json(200, {
                "video": {
                    "id": video_id,
                    "title": title,
                    "channel": info.get("channel") or info.get("uploader") or "",
                    "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "duration": info.get("duration"),
                    "viewCount": info.get("view_count"),
                },
                "tags": [{"name": item, "score": tag_score(item, title, description)} for item in tags],
                "tagCount": len(tags),
            })
        except json.JSONDecodeError:
            self._json(400, {"error": "Request body must be valid JSON."})
        except Exception as exc:
            message = str(exc).lower()
            if "private" in message:
                error = "This video is private. Only publicly accessible videos can be inspected."
            elif any(word in message for word in ("not available", "unavailable", "removed")):
                error = "This video is unavailable or has been removed."
            elif "timeout" in message or "timed out" in message:
                error = "YouTube took too long to respond. Please try again."
            else:
                error = "Could not retrieve this video. It may be restricted, unavailable, or temporarily blocked by YouTube."
            print(f"YouTube extraction error: {exc}")
            self._json(502, {"error": error})
