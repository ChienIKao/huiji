"""本機 HTTP 伺服器：提供介面檔案，以及支援 Range 的音檔（播放器拖曳進度需要）"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

UI_DIR = Path(__file__).parent / "ui"
MEDIA_NAME = re.compile(r"^/media/([\w-]+)/(audio\.m4a|recording\.mp4|source\.(?:mp4|m4v|mov|webm))$")
MEDIA_TYPES = {".m4a": "audio/mp4", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
               ".webm": "video/webm"}


def start(folder_of, dev_api=None, port: int = 0, subtitles_of=None) -> int:
    """dev_api：開發模式才傳入，讓一般瀏覽器可以用 POST /api/<方法> 呼叫介面 API"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            name = urlparse(self.path).path.removeprefix("/api/")
            if dev_api is None or name.startswith("_") or not hasattr(dev_api, name):
                return self.send_error(404)
            args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
            try:
                body = {"result": getattr(dev_api, name)(*args)}
            except Exception as e:  # noqa: BLE001
                body = {"error": str(e)}
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            sub = re.match(r"^/media/([\w-]+)/subtitles\.vtt$", path)
            if sub and subtitles_of:
                try:
                    data = subtitles_of(sub.group(1)).encode("utf-8")
                except (KeyError, OSError, ValueError):
                    return self.send_error(404)
                self.send_response(200)
                self.send_header("Content-Type", "text/vtt; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            m = MEDIA_NAME.match(path)
            if m:
                try:
                    file = folder_of(m.group(1)) / m.group(2)
                except KeyError:
                    return self.send_error(404)
            else:
                file = (UI_DIR / (path.lstrip("/") or "index.html")).resolve()
                if UI_DIR.resolve() not in file.parents:
                    return self.send_error(404)
            if not file.is_file():
                return self.send_error(404)
            self._send_file(file)

        def _send_file(self, file: Path):
            size = file.stat().st_size
            ctype = MEDIA_TYPES.get(file.suffix) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if rng and (rng.group(1) or rng.group(2)):
                if rng.group(1):
                    start = int(rng.group(1))
                    end = int(rng.group(2)) if rng.group(2) else size - 1
                else:
                    start = max(0, size - int(rng.group(2)))
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(file, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                try:
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (ConnectionError, OSError):
                    pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]
