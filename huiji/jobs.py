"""背景工作：一次處理一場會議，依序轉錄 → AI 整理"""
from __future__ import annotations

import queue
import threading
import traceback

from . import notes, transcribe
from .config import Settings
from .store import Store


class Jobs:
    def __init__(self, store: Store, settings_getter):
        self.store = store
        self.settings: callable = settings_getter
        self.queue: queue.Queue = queue.Queue()
        self.current: str | None = None
        self.proc = None
        self.cancelled = False
        threading.Thread(target=self._worker, daemon=True).start()

    def enqueue(self, mid: str, retranscribe: bool = False, renotes: bool = False) -> None:
        if retranscribe:
            (self.store.folder(mid) / "transcript.json").unlink(missing_ok=True)
        if retranscribe or renotes:
            (self.store.folder(mid) / "notes.json").unlink(missing_ok=True)
        self.store.update(mid, status="queued", progress="排隊中", error="")
        self.queue.put(mid)

    def cancel(self, mid: str) -> None:
        if self.current == mid and self.proc:
            self.cancelled = True
            self.proc.kill()

    def _set_proc(self, proc) -> None:
        self.proc = proc

    def _worker(self) -> None:
        while True:
            mid = self.queue.get()
            self.current, self.cancelled = mid, False
            try:
                self._process(mid)
            except Exception as e:  # noqa: BLE001  要把任何錯誤顯示在介面上
                if not self.store.folder(mid).exists():
                    continue
                if self.cancelled:
                    self.store.update(mid, status="recorded", progress="", error="已取消")
                else:
                    traceback.print_exc()
                    self.store.update(mid, status="error", progress="", error=str(e))
            finally:
                self.current, self.proc = None, None

    def _process(self, mid: str) -> None:
        s: Settings = self.settings()
        folder = self.store.folder(mid)
        meeting = self.store.load(mid)

        segments = self.store.read_json(mid, "transcript.json")
        if segments is None:
            self.store.update(mid, status="transcribing", progress="準備音檔…")
            duration = meeting.get("duration") or 0
            if not duration:
                src = folder / "audio.m4a" if (folder / "audio.m4a").exists() else next(folder.glob("source.*"), None)
                duration = transcribe.probe_duration(s, src) if src else 0
                self.store.update(mid, duration=duration)

            def progress(p: float) -> None:
                text = "分辨說話者…" if p < 0 else f"轉錄中 {int(p * 100)}%"
                self.store.update(mid, progress=text)

            self.store.update(mid, progress="載入模型…")
            segments = transcribe.transcribe(s, folder, duration, progress, s.glossary, self._set_proc)
            self.store.write_json(mid, "transcript.json", segments)
            meeting = self.store.load(mid)
            (folder / "transcript.srt").write_text(transcribe.to_srt(segments, meeting.get("speakers", {})),
                                                  encoding="utf-8")

        if s.llm_backend == "none" or not segments:
            self.store.update(mid, status="done", progress="")
            return
        if self.store.read_json(mid, "notes.json") is None:
            self.store.update(mid, status="summarizing", progress=f"AI 整理中（{s.llm_backend}）…")
            meeting = self.store.load(mid)
            prompt = notes.build_prompt(meeting, segments, s.glossary)
            result = notes.generate(s.llm_backend, s.llm_model, prompt, self._set_proc)
            self.store.write_json(mid, "notes.json", result)
            if not meeting.get("title_locked") and result.get("title"):
                self.store.update(mid, title=result["title"])
        self.store.update(mid, status="done", progress="")

    def revise(self, mid: str, answers: list[dict]) -> None:
        """依使用者對待確認題目的回答修正紀錄（在呼叫端的執行緒執行）"""
        s: Settings = self.settings()
        meeting = self.store.load(mid)
        segments = self.store.read_json(mid, "transcript.json") or []
        current = self.store.read_json(mid, "notes.json") or {}
        prompt = notes.build_revise_prompt(meeting, segments, s.glossary, current, answers)
        self.store.update(mid, status="summarizing", progress="依回答修正中…")
        try:
            result = notes.generate(s.llm_backend if s.llm_backend != "none" else "claude", s.llm_model, prompt)
            self.store.write_json(mid, "notes.json", result)
            if not meeting.get("title_locked") and result.get("title"):
                self.store.update(mid, title=result["title"])
            self.store.update(mid, status="done", progress="")
        except Exception as e:  # noqa: BLE001
            self.store.update(mid, status="done", progress="", error=f"修正失敗：{e}")
            raise
