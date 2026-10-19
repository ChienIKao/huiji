"""會議資料：每場會議一個資料夾，裡面放 meeting.json、音檔、逐字稿、會議紀錄。

<資料夾>/<日期 標題>/
  meeting.json      id、標題、日期、狀態、說話者名稱、筆記、重點標記
  mic.wav           麥克風（錄音時才有）
  system.wav        系統音（錄音時才有）
  audio.m4a         混音後的音檔，播放用
  recording.mp4     螢幕錄影＋聲音（有錄螢幕時）
  transcript.json   逐字稿 [{start, end, speaker, text}]
  transcript.srt
  notes.json        AI 整理的會議紀錄

會議預設放在設定的資料夾裡；錄音前也可以選別的資料夾，那些位置記在 library.json。
資料夾名稱跟著標題改（AI 取好標題、或使用者改標題時），會議的 id 不變。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from . import recorder

_lock = threading.RLock()
INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _read_json(path: Path):
    with _lock:
        for attempt in range(20):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)


def _write_json(path: Path, data) -> None:
    with _lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(20):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                # Windows 上其他程序讀檔時可能暫時無法取代，等檔案釋放再試。
                if attempt == 19:
                    raise
                time.sleep(0.05)


def folder_name(date: str, title: str) -> str:
    # 冒號在資料夾名稱裡不能用，換成全形的（「10:30 會議」→「10：30 會議」），其他不能用的字元換成空白
    safe = INVALID.sub(" ", title.replace(":", "：")).strip().rstrip(". ")[:60].strip()
    return f"{date} {safe}" if safe else date


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while (candidate := path.with_name(f"{path.name} ({i})")).exists():
        i += 1
    return candidate


class Store:
    def __init__(self, meetings_dir: Path, trash_dir: Path, library_file: Path):
        self.root = meetings_dir
        self.trash = trash_dir
        self.library_file = library_file
        self.root.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, Path] = {}
        self._scan()

    # ---- 會議資料夾的位置 ----
    def _external(self) -> list[str]:
        """不在預設資料夾裡的會議（錄音前選了別的位置）"""
        try:
            return _read_json(self.library_file).get("folders", [])
        except (OSError, ValueError):
            return []

    def _save_external(self, folders: list[str]) -> None:
        self.library_file.parent.mkdir(parents=True, exist_ok=True)
        self.library_file.write_text(json.dumps({"folders": sorted(set(folders))}, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def _set_external(self, old: Path | None, new: Path | None) -> None:
        folders = [f for f in self._external() if old is None or Path(f) != old]
        if new is not None and new.parent != self.root:
            folders.append(str(new))
        self._save_external(folders)

    def _scan(self) -> None:
        paths: dict[str, Path] = {}
        candidates = [d for d in self.root.iterdir() if d.is_dir()] + [Path(f) for f in self._external()]
        for d in candidates:
            f = d / "meeting.json"
            try:
                paths[_read_json(f)["id"]] = d
            except (OSError, ValueError, KeyError):
                continue
        self._paths = paths

    def folder(self, mid: str) -> Path:
        path = self._paths.get(mid)
        if path is None or not path.exists():
            self._scan()
            path = self._paths.get(mid)
        if path is None:
            raise KeyError(f"找不到會議 {mid}")
        return path

    # ---- 建立 ----
    def create(self, title: str = "", source: str = "record", mode: str = "both", parent: Path | None = None) -> dict:
        now = datetime.now()
        mid = now.strftime("%Y%m%d-%H%M%S")
        while mid in self._paths:  # 同一秒建立兩場（例如一次匯入多個檔案）
            mid += "x"
        display = title or now.strftime("%H:%M 會議")
        parent = Path(parent) if parent else self.root
        parent.mkdir(parents=True, exist_ok=True)
        path = _unique(parent / folder_name(now.strftime("%Y-%m-%d"), display))
        path.mkdir()
        self._paths[mid] = path
        if parent != self.root:
            self._set_external(None, path)
        meeting = {
            "id": mid,
            "title": display,
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

    def rename_folder(self, mid: str) -> bool:
        """資料夾名稱跟著標題改；檔案正在使用（例如正在播放）時改不了，下次啟動再試"""
        try:
            m = self.load(mid)
            cur = self.folder(mid)
        except (KeyError, OSError, ValueError):
            return False
        want = folder_name(m["date"], m["title"])
        if cur.name == want or re.fullmatch(re.escape(want) + r" \(\d+\)", cur.name):
            return True
        new = _unique(cur.parent / want)
        try:
            cur.rename(new)
        except OSError:
            return False
        self._paths[mid] = new
        if cur.parent != self.root:
            self._set_external(cur, new)
        return True

    def rename_all(self, skip: set[str] = frozenset()) -> None:
        for mid in list(self._paths):
            if mid not in skip:
                self.rename_folder(mid)

    # ---- 讀寫 ----
    def load(self, mid: str) -> dict:
        with _lock:
            return _read_json(self.folder(mid) / "meeting.json")

    def save(self, meeting: dict) -> None:
        with _lock:
            path = self.folder(meeting["id"]) / "meeting.json"
            _write_json(path, meeting)

    def update(self, mid: str, **fields) -> dict:
        with _lock:
            path = self.folder(mid) / "meeting.json"
            meeting = _read_json(path)
            meeting.update(fields)
            _write_json(path, meeting)
            return meeting

    def read_json(self, mid: str, name: str):
        with _lock:
            path = self.folder(mid) / name
            if not path.exists():
                return None
            return _read_json(path)

    def write_json(self, mid: str, name: str, data) -> None:
        with _lock:
            _write_json(self.folder(mid) / name, data)

    # ---- 列表與搜尋 ----
    def list(self) -> list[dict]:
        with _lock:
            self._scan()
            meetings = []
            for mid in self._paths:
                try:
                    meetings.append(self.load(mid))
                except (OSError, ValueError, KeyError):
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
    def _trash_folders(self) -> dict[str, Path]:
        out = {}
        if self.trash.exists():
            for d in self.trash.iterdir():
                try:
                    out[_read_json(d / "meeting.json")["id"]] = d
                except (OSError, ValueError, KeyError):
                    continue
        return out

    def delete(self, mid: str) -> None:
        cur = self.folder(mid)
        self.update(mid, trash_origin=str(cur))
        self.trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(cur), str(_unique(self.trash / cur.name)))
        self._paths.pop(mid, None)
        if cur.parent != self.root:
            self._set_external(cur, None)

    def trash_list(self) -> list[dict]:
        out = []
        for d in self._trash_folders().values():
            out.append(_read_json(d / "meeting.json"))
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def restore(self, mid: str) -> None:
        src = self._trash_folders()[mid]
        meeting = _read_json(src / "meeting.json")
        origin = Path(meeting.get("trash_origin") or self.root / src.name)
        if not origin.parent.exists():
            origin = self.root / origin.name
        target = _unique(origin)
        shutil.move(str(src), str(target))
        self._paths[mid] = target
        if target.parent != self.root:
            self._set_external(None, target)

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
