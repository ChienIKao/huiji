"""介面呼叫的 API（pywebview js_api）：每個公開方法都可以從 JS 以 pywebview.api.xxx() 呼叫"""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict
from pathlib import Path

import webview

from . import config, notes, recorder, transcribe
from .jobs import Jobs
from .store import Store


class Api:
    def __init__(self):
        self._settings = config.load()
        self._store = Store(self._settings.meetings_dir, self._settings.trash_dir)
        self._jobs = Jobs(self._store, lambda: self._settings)
        self._recorder: recorder.Recorder | None = None
        self._recording_id: str | None = None
        self._window = None
        for mid in self._store.recover():
            self._jobs.enqueue(mid)

    def _attach(self, window) -> None:
        self._window = window

    def _meetings_dir(self) -> Path:
        return self._settings.meetings_dir

    # ---- 狀態（介面每 0.5 秒輪詢） ----
    def state(self) -> dict:
        rec = None
        if self._recorder:
            rec = {"id": self._recording_id, "elapsed": self._recorder.elapsed(),
                   "paused": self._recorder.paused, "levels": self._recorder.levels()}
        return {"recording": rec, "job": self._jobs.current}

    # ---- 會議列表 ----
    def list_meetings(self, query: str = "") -> list[dict]:
        meetings = self._store.list()
        if query.strip():
            hits = set(self._store.search(query.strip()))
            meetings = [m for m in meetings if m["id"] in hits]
        for m in meetings:
            n = self._store.read_json(m["id"], "notes.json")
            m["review_count"] = len(n.get("reviewItems", [])) if n else 0
        return meetings

    def get_meeting(self, mid: str) -> dict:
        return {
            "meeting": self._store.load(mid),
            "transcript": self._store.read_json(mid, "transcript.json"),
            "notes": self._store.read_json(mid, "notes.json"),
            "has_audio": (self._store.folder(mid) / "audio.m4a").exists(),
        }

    def update_meeting(self, mid: str, fields: dict) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("title", "template", "user_notes", "speakers")}
        if "title" in allowed:
            allowed["title_locked"] = True
        meeting = self._store.update(mid, **allowed)
        if "speakers" in allowed:
            segments = self._store.read_json(mid, "transcript.json")
            if segments:
                (self._store.folder(mid) / "transcript.srt").write_text(
                    transcribe.to_srt(segments, meeting["speakers"]), encoding="utf-8")
        return meeting

    def save_notes(self, mid: str, data: dict) -> None:
        self._store.write_json(mid, "notes.json", data)

    def templates(self) -> list[dict]:
        return [{"id": k, "name": v["name"], "description": v["description"]} for k, v in notes.TEMPLATES.items()]

    # ---- 錄音 ----
    def start_recording(self, mode: str = "both") -> dict:
        if self._recorder:
            raise RuntimeError("已經在錄音了")
        meeting = self._store.create(mode=mode)
        rec = recorder.Recorder(self._store.folder(meeting["id"]), mode,
                                self._settings.mic_device, self._settings.loopback_device)
        try:
            rec.start()
        except Exception:
            shutil.rmtree(self._store.folder(meeting["id"]), ignore_errors=True)
            raise
        self._recorder, self._recording_id = rec, meeting["id"]
        return meeting

    def pause_recording(self) -> None:
        if self._recorder:
            self._recorder.pause()

    def resume_recording(self) -> None:
        if self._recorder:
            self._recorder.resume()

    def add_bookmark(self, label: str = "") -> float:
        if not self._recorder:
            return 0
        t = self._recorder.elapsed()
        m = self._store.load(self._recording_id)
        self._store.update(self._recording_id, bookmarks=m["bookmarks"] + [{"time": t, "label": label}])
        return t

    def stop_recording(self, process: bool = True) -> str | None:
        if not self._recorder:
            return None
        rec, mid = self._recorder, self._recording_id
        self._recorder, self._recording_id = None, None
        duration = rec.stop()
        self._store.update(mid, status="recorded", duration=duration)
        if process:
            self._jobs.enqueue(mid)
        return mid

    # ---- 匯入 ----
    def import_files(self) -> list[str]:
        exts = ";".join("*" + e for e in sorted(transcribe.AUDIO_EXTS))
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                                file_types=(f"音訊與影片 ({exts})", "所有檔案 (*.*)"))
        ids = []
        for p in paths or []:
            src = Path(p)
            meeting = self._store.create(title=src.stem, source="import")
            shutil.copy2(src, self._store.folder(meeting["id"]) / ("source" + src.suffix.lower()))
            self._jobs.enqueue(meeting["id"])
            ids.append(meeting["id"])
        return ids

    # ---- 處理 ----
    def retranscribe(self, mid: str) -> None:
        self._jobs.enqueue(mid, retranscribe=True)

    def regenerate_notes(self, mid: str) -> None:
        self._jobs.enqueue(mid, renotes=True)

    def process(self, mid: str) -> None:
        self._jobs.enqueue(mid)

    def cancel(self, mid: str) -> None:
        self._jobs.cancel(mid)

    def answer_review(self, mid: str, answers: list[dict]) -> None:
        self._jobs.revise(mid, answers)

    # ---- 匯出 ----
    def export(self, mid: str, kind: str) -> str | None:
        data = self.get_meeting(mid)
        m, segs = data["meeting"], data["transcript"] or []
        safe = "".join(c for c in m["title"] if c not in '\\/:*?"<>|').strip() or mid
        if kind == "audio":
            src = self._store.folder(mid) / "audio.m4a"
            target = self._save_dialog(f"{safe}.m4a")
            if target:
                shutil.copy2(src, target)
            return target
        if kind == "srt":
            content, name = transcribe.to_srt(segs, m["speakers"]), f"{safe}.srt"
        elif kind == "transcript":
            content, name = notes.to_markdown(m, None, segs, True), f"{safe} 逐字稿.md"
        else:
            content, name = notes.to_markdown(m, data["notes"], segs, kind == "all"), f"{safe}.md"
        target = self._save_dialog(name)
        if target:
            Path(target).write_text(content, encoding="utf-8")
        return target

    def markdown(self, mid: str, include_transcript: bool = False) -> str:
        data = self.get_meeting(mid)
        return notes.to_markdown(data["meeting"], data["notes"], data["transcript"], include_transcript)

    def _save_dialog(self, name: str) -> str | None:
        r = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
        if not r:
            return None
        return r if isinstance(r, str) else r[0]

    def open_folder(self, mid: str) -> None:
        os.startfile(self._store.folder(mid))  # noqa: S606

    # ---- 垃圾桶 ----
    def delete_meeting(self, mid: str) -> None:
        if mid == self._recording_id:
            raise RuntimeError("錄音中的會議不能刪除")
        self._jobs.cancel(mid)
        self._store.delete(mid)

    def trash(self) -> list[dict]:
        return self._store.trash_list()

    def restore_meeting(self, mid: str) -> None:
        self._store.restore(mid)

    def empty_trash(self) -> None:
        self._store.empty_trash()

    # ---- 設定 ----
    def get_settings(self) -> dict:
        return {"settings": asdict(self._settings), "devices": recorder.list_devices(),
                "backends": notes.available_backends()}

    def save_settings(self, data: dict) -> dict:
        old_dir = self._settings.data_dir
        for k, v in data.items():
            if hasattr(self._settings, k):
                setattr(self._settings, k, v)
        config.save(self._settings)
        if self._settings.data_dir != old_dir:
            self._store = Store(self._settings.meetings_dir, self._settings.trash_dir)
            self._jobs.store = self._store
        return asdict(self._settings)

    def check_environment(self) -> dict:
        s = self._settings
        return {
            "whisper": Path(s.whisper_exe).exists(),
            "opencc": Path(s.opencc_exe).exists(),
            "ffmpeg": bool(shutil.which(s.ffmpeg_exe) or Path(s.ffmpeg_exe).exists()),
            **notes.available_backends(),
        }
