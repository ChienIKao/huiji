"""會議資料：每場會議一個資料夾，裡面放 meeting.json、音檔、逐字稿、會議紀錄。

meetings/<id>/
  meeting.json      標題、日期、狀態、說話者名稱、筆記、重點標記
  mic.wav           麥克風（錄音時才有）
  system.wav        系統音（錄音時才有）
  audio.m4a         混音後的音檔，播放用
  transcript.json   逐字稿 [{start, end, speaker, text}]
  transcript.srt
  notes.json        AI 整理的會議紀錄
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from . import recorder

_lock = threading.Lock()


class Store:
    def __init__(self, meetings_dir: Path, trash_dir: Path):
        self.root = meetings_dir
        self.trash = trash_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, mid: str) -> Path:
        return self.root / mid

    # ---- 建立 ----
    def create(self, title: str = "", source: str = "record", mode: str = "both") -> dict:
        now = datetime.now()
        mid = now.strftime("%Y%m%d-%H%M%S")
        self.folder(mid).mkdir(parents=True, exist_ok=False)
        meeting = {
            "id": mid,
            "title": title or now.strftime("%m/%d %H:%M 會議"),
            "title_locked": bool(title),
            "date": now.strftime("%Y-%m-%d"),
            "created_at": now.isoformat(timespec="seconds"),
            "duration": 0,
            "status": "recording" if source == "record" else "recorded",
            "progress": "",
            "error": "",
            "source": source,
            "mode": mode,
            "template": "general",
            "speakers": {},
            "user_notes": "",
            "bookmarks": [],
        }
        self.save(meeting)
        return meeting

    # ---- 讀寫 ----
    def load(self, mid: str) -> dict:
        return json.loads((self.folder(mid) / "meeting.json").read_text(encoding="utf-8"))

    def save(self, meeting: dict) -> None:
        with _lock:
            path = self.folder(meeting["id"]) / "meeting.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(meeting, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    def update(self, mid: str, **fields) -> dict:
        with _lock:
            path = self.folder(mid) / "meeting.json"
            meeting = json.loads(path.read_text(encoding="utf-8"))
            meeting.update(fields)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(meeting, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            return meeting

    def read_json(self, mid: str, name: str):
        path = self.folder(mid) / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, mid: str, name: str, data) -> None:
        path = self.folder(mid) / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # ---- 列表與搜尋 ----
    def list(self) -> list[dict]:
        meetings = []
        for d in self.root.iterdir():
            if (d / "meeting.json").exists():
                try:
                    meetings.append(self.load(d.name))
                except (OSError, ValueError):
                    continue
        meetings.sort(key=lambda m: m["created_at"], reverse=True)
        return meetings

    def search(self, query: str) -> list[str]:
        """回傳標題、筆記、逐字稿或會議紀錄含有 query 的會議 id"""
        q = query.lower()
        hits = []
        for m in self.list():
            texts = [m["title"], m.get("user_notes", "")]
            for name in ("transcript.json", "notes.json"):
                data = self.read_json(m["id"], name)
                if data:
                    texts.append(json.dumps(data, ensure_ascii=False))
            if any(q in t.lower() for t in texts):
                hits.append(m["id"])
        return hits

    # ---- 垃圾桶 ----
    def delete(self, mid: str) -> None:
        self.trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.folder(mid)), str(self.trash / mid))

    def trash_list(self) -> list[dict]:
        if not self.trash.exists():
            return []
        out = []
        for d in self.trash.iterdir():
            f = d / "meeting.json"
            if f.exists():
                out.append(json.loads(f.read_text(encoding="utf-8")))
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def restore(self, mid: str) -> None:
        shutil.move(str(self.trash / mid), str(self.folder(mid)))

    def empty_trash(self) -> None:
        if self.trash.exists():
            shutil.rmtree(self.trash)

    # ---- 啟動時處理上次沒結束的工作 ----
    def recover(self) -> list[str]:
        """錄音中當掉的修好 WAV 檔頭；轉錄、整理到一半的改回可重試的狀態。回傳要重新排入佇列的 id"""
        pending = []
        for m in self.list():
            if m["status"] == "recording":
                for name in ("mic.wav", "system.wav"):
                    recorder.repair_wav(self.folder(m["id"]) / name)
                self.update(m["id"], status="recorded", progress="")
                pending.append(m["id"])
            elif m["status"] in ("queued", "transcribing", "summarizing") or (
                    m["status"] == "recorded" and not m.get("error")):
                self.update(m["id"], status="recorded", progress="")
                pending.append(m["id"])
        return pending
