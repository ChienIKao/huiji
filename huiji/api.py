"""介面呼叫的 API（pywebview js_api）：每個公開方法都可以從 JS 以 pywebview.api.xxx() 呼叫"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.request
import webbrowser
from dataclasses import asdict
from pathlib import Path

import webview

from . import __version__, config, notes, recorder, screen, transcribe
from .jobs import Jobs
from .store import Store


class Api:
    def __init__(self):
        self._settings = config.load()
        self._store = Store(self._settings.meetings_dir, self._settings.trash_dir, self._settings.library_file)
        self._jobs = Jobs(self._store, lambda: self._settings)
        self._recorder: recorder.Recorder | None = None
        self._screen: screen.ScreenRecorder | None = None
        self._recording_id: str | None = None
        self._meter: recorder.LevelMonitor | None = None
        self._meter_lock = threading.Lock()
        self._window = None
        self._mini = None
        self._url = ""
        pending = self._store.recover()
        # 上次沒改成的資料夾名稱（例如當時檔案正在播放）趁啟動時補改
        self._store.rename_all(skip=set(pending))
        for mid in pending:
            self._jobs.enqueue(mid)

    def _attach(self, window, url: str = "") -> None:
        self._window = window
        self._url = url

    def _folder(self, mid: str) -> Path:
        return self._store.folder(mid)

    # ---- 狀態（介面每 0.5 秒輪詢） ----
    def state(self) -> dict:
        rec = None
        if self._recorder:
            rec = {"id": self._recording_id, "elapsed": self._recorder.elapsed(),
                   "paused": self._recorder.paused, "levels": self._recorder.levels(),
                   "screen": bool(self._screen),
                   "screen_ok": not self._screen or self._recorder.paused or self._screen.running()}
        meter = self._meter.levels() if self._meter else None
        return {"recording": rec, "job": self._jobs.current, "meter": meter, "mini": self._mini is not None}

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
        video = self._video_file(mid)
        return {
            "meeting": self._store.load(mid),
            "transcript": self._store.read_json(mid, "transcript.json"),
            "notes": self._store.read_json(mid, "notes.json"),
            "has_audio": (self._store.folder(mid) / "audio.m4a").exists(),
            "video": video.name if video else None,
        }

    def _video_file(self, mid: str) -> Path | None:
        """可以在介面播放的影片：自己錄的 recording.mp4，或匯入的 mp4／mov／webm"""
        folder = self._store.folder(mid)
        if (folder / "recording.mp4").exists():
            return folder / "recording.mp4"
        return next((p for p in folder.glob("source.*") if p.suffix in (".mp4", ".m4v", ".mov", ".webm")), None)

    def update_meeting(self, mid: str, fields: dict) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("title", "template", "user_notes", "speakers")}
        if "title" in allowed:
            allowed["title_locked"] = True
        meeting = self._store.update(mid, **allowed)
        if "title" in allowed and mid != self._recording_id and mid != self._jobs.current:
            self._store.rename_folder(mid)
        if "speakers" in allowed:
            segments = self._store.read_json(mid, "transcript.json")
            if segments:
                (self._store.folder(mid) / "transcript.srt").write_text(
                    transcribe.to_srt(segments, meeting["speakers"]), encoding="utf-8")
        return meeting

    def save_notes(self, mid: str, data: dict) -> None:
        self._store.write_json(mid, "notes.json", data)

    def save_transcript(self, mid: str, segments: list[dict]) -> None:
        """使用者修改過的逐字稿（改錯字、改某一句的說話者）"""
        clean = [{"start": float(x["start"]), "end": float(x["end"]), "speaker": x.get("speaker") or None,
                  "text": str(x["text"]).strip()} for x in segments if str(x.get("text", "")).strip()]
        self._store.write_json(mid, "transcript.json", clean)
        meeting = self._store.update(mid, transcript_edited=True)
        (self._store.folder(mid) / "transcript.srt").write_text(
            transcribe.to_srt(clean, meeting["speakers"]), encoding="utf-8")

    def templates(self) -> list[dict]:
        return [{"id": k, "name": v["name"], "description": v["description"]} for k, v in notes.TEMPLATES.items()]

    # ---- 錄音 ----
    def start_recording(self, mode: str = "both", record_screen: bool = False, parent: str = "") -> dict:
        if self._recorder:
            raise RuntimeError("已經在錄音了")
        self.stop_meter()
        s = self._settings
        if parent and not Path(parent).is_dir():
            raise RuntimeError(f"找不到資料夾：{parent}")
        meeting = self._store.create(mode=mode, parent=Path(parent) if parent else None)
        folder = self._store.folder(meeting["id"])
        rec = recorder.Recorder(folder, mode, s.mic_device, s.loopback_device)
        scr = None
        try:
            if record_screen:
                scr = screen.ScreenRecorder(s.ffmpeg_exe, folder, s.screen_source, s.screen_fps)
            rec.start()
            if scr:
                scr.start_segment(rec.elapsed())
        except Exception:
            if rec.started_at:
                rec.stop()
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._recorder, self._screen, self._recording_id = rec, scr, meeting["id"]
        if scr:
            meeting = self._store.update(meeting["id"], video_segments=scr.segments)
        return meeting

    def pause_recording(self) -> None:
        if self._recorder:
            self._recorder.pause()
            if self._screen:
                self._screen.stop_segment(self._recorder.elapsed())
                self._store.update(self._recording_id, video_segments=self._screen.segments)

    def resume_recording(self) -> None:
        if self._recorder:
            if self._screen:
                self._screen.start_segment(self._recorder.elapsed())
                self._store.update(self._recording_id, video_segments=self._screen.segments)
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
        rec, scr, mid = self._recorder, self._screen, self._recording_id
        self._recorder, self._screen, self._recording_id = None, None, None
        if scr:
            if not rec.paused:
                scr.stop_segment(rec.elapsed())
            self._store.update(mid, video_segments=scr.segments)
        duration = rec.stop()
        self._store.update(mid, status="recorded", duration=duration)
        if process:
            self._jobs.enqueue(mid)
        self.close_mini()
        return mid

    # ---- 錄音前的音量條 ----
    def start_meter(self) -> None:
        with self._meter_lock:
            if self._meter or self._recorder:
                return
            m = recorder.LevelMonitor(self._settings.mic_device, self._settings.loopback_device)
            try:
                m.start()
            except Exception:  # noqa: BLE001  音量條失敗不影響其他功能
                m.stop()
                return
            self._meter = m

    def stop_meter(self) -> None:
        with self._meter_lock:
            if self._meter:
                self._meter.stop()
                self._meter = None

    # ---- 錄影來源 ----
    def screen_sources(self) -> dict:
        return screen.list_sources()

    def source_thumbnail(self, source: dict, max_width: int = 480) -> str | None:
        try:
            return screen.thumbnail(source, max_width)
        except Exception:  # noqa: BLE001  視窗剛好關掉之類的
            return None

    def set_screen_source(self, source: dict) -> str:
        keep = {k: source[k] for k in ("kind", "index", "hwnd", "title", "exe") if k in source}
        self.save_settings({"screen_source": keep})
        return screen.describe(keep)

    # ---- 錄音中的小視窗 ----
    def show_mini(self) -> None:
        if self._mini or not self._url:
            return
        work = webview.screens[0] if webview.screens else None
        x = (work.width - 400) if work else None
        self._mini = webview.create_window("會記・錄音中", f"{self._url}?mini=1", js_api=self, width=380, height=60,
                                           x=x, y=40 if work else None, resizable=False, frameless=True,
                                           easy_drag=True, on_top=True, background_color="#232323")
        self._mini.events.closed += lambda: setattr(self, "_mini", None)
        if self._window:
            self._window.minimize()

    def close_mini(self) -> None:
        mini, self._mini = self._mini, None
        if mini:
            mini.destroy()
            if self._window:
                self._window.restore()
                self._window.show()

    # ---- 存放位置 ----
    def default_record_dir(self) -> str:
        return str(self._settings.meetings_dir)

    def pick_folder(self, start: str = "") -> str | None:
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                            directory=start or str(self._settings.meetings_dir))
        if not r:
            return None
        return r if isinstance(r, str) else r[0]

    # ---- 新版本 ----
    def check_update(self) -> dict | None:
        if not self._settings.check_updates:
            return None
        try:
            req = urllib.request.Request("https://api.github.com/repos/ChienIKao/huiji/releases/latest",
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "Huiji"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.load(r)
        except (OSError, ValueError):
            return None
        latest = data.get("tag_name", "").lstrip("v")

        def ver(v: str) -> tuple:
            return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

        if latest and ver(latest) > ver(__version__):
            return {"version": latest, "url": data.get("html_url", "")}
        return None

    def open_url(self, url: str) -> None:
        if url.startswith("https://github.com/ChienIKao/huiji"):
            webbrowser.open(url)

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
        if kind in ("audio", "video"):
            src = self._store.folder(mid) / "audio.m4a" if kind == "audio" else self._video_file(mid)
            target = self._save_dialog(f"{safe}{src.suffix}")
            if target:
                shutil.copy2(src, target)
            return target
        if kind in ("srt", "srt_plain"):
            subs = transcribe.subtitle_segments(self._settings, self._store.folder(mid), segs,
                                                edited=bool(m.get("transcript_edited")))
            content = transcribe.to_srt(subs, m["speakers"], with_speaker=kind == "srt")
            name = f"{safe}.srt"
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

    def open_data_dir(self) -> None:
        self._settings.meetings_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self._settings.data_dir)  # noqa: S606

    def meeting_folder(self, mid: str) -> str:
        return str(self._store.folder(mid))

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
        return {"settings": asdict(self._settings), "devices": recorder.list_devices(), "version": __version__,
                "monitors": screen.list_monitors(), "screen_source_name": screen.describe(self._settings.screen_source),
                "backends": notes.available_backends()}

    def save_settings(self, data: dict) -> dict:
        old_dir = self._settings.data_dir
        for k, v in data.items():
            if hasattr(self._settings, k):
                setattr(self._settings, k, v)
        config.save(self._settings)
        if self._settings.data_dir != old_dir:
            self._store = Store(self._settings.meetings_dir, self._settings.trash_dir, self._settings.library_file)
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
