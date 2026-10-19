import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from huiji.config import Settings
from huiji.jobs import Jobs
from huiji.store import Store


class WorkerQueue(queue.Queue):
    def get(self):
        mid = super().get()
        if mid is None:
            raise SystemExit
        return mid


class JobsTests(unittest.TestCase):
    def make_jobs(self, store, settings=None):
        with patch("huiji.jobs.threading.Thread.start"):
            return Jobs(store, lambda: settings or Settings(llm_backend="none"))

    def test_worker_continues_after_error_update_fails(self):
        self.check_worker_continues()

    def test_worker_continues_after_folder_lookup_fails(self):
        for error in (KeyError("找不到會議"), PermissionError("檔案使用中")):
            with self.subTest(error=error):
                self.check_worker_continues(folder_error=error)

    def test_worker_continues_when_log_is_unwritable(self):
        self.check_worker_continues(log_error=OSError("紀錄檔無法寫入"))

    def check_worker_continues(self, folder_error=None, log_error=None):
        store = Mock()
        store.update.side_effect = PermissionError("檔案使用中")
        if folder_error:
            store.folder.side_effect = folder_error
        jobs = self.make_jobs(store)
        jobs.queue = WorkerQueue()
        processed = threading.Event()

        def process(mid):
            if mid == "失敗會議":
                raise RuntimeError("轉錄失敗")
            processed.set()

        jobs._process = process
        worker = threading.Thread(target=jobs._worker, daemon=True)
        with patch("huiji.jobs.traceback.print_exc"), patch("builtins.print", side_effect=log_error):
            worker.start()
            try:
                jobs.queue.put("失敗會議")
                jobs.queue.put("下一場會議")
                self.assertTrue(processed.wait(2), "工作執行緒未處理下一場會議")
                self.assertTrue(worker.is_alive())
            finally:
                jobs.queue.put(None)
                worker.join(2)

    def test_reuses_newer_srt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "meetings", root / "trash", root / "library.json")
            mid = store.create(source="import")["id"]
            store.update(mid, duration=10)
            folder = store.folder(mid)
            audio = folder / "audio.m4a"
            audio.touch()
            srt = folder / "whisper" / "work.srt"
            srt.parent.mkdir()
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n[SPEAKER_00]: 测试,\n\n"
                           "2\n00:00:01,000 --> 00:00:02,000\n[SPEAKER_00]: 你好!\n", encoding="utf-8")
            os.utime(audio, (100, 100))
            os.utime(srt, (200, 200))
            jobs = self.make_jobs(store)
            with patch("huiji.jobs.transcribe.transcribe", return_value=[]) as run, \
                    patch("huiji.transcribe.to_traditional", return_value=["測試,你好!"]) as convert, \
                    patch.object(jobs, "_name_self") as name_self:
                jobs._process(mid)
            run.assert_not_called()
            convert.assert_called_once_with(jobs.settings(), ["测试,你好!"])
            name_self.assert_called_once()
            segments = store.read_json(mid, "transcript.json")
            self.assertEqual(segments[0]["text"], "測試，你好！")
            self.assertEqual(len(segments), 1)
            self.assertTrue((folder / "transcript.srt").exists())
            self.assertEqual(store.load(mid)["status"], "done")

    def test_progress_only_writes_changed_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "meetings", root / "trash", root / "library.json")
            mid = store.create(source="import")["id"]
            store.update(mid, duration=10)
            (store.folder(mid) / "source.mkv").touch()
            jobs = self.make_jobs(store)

            def convert(ffmpeg, source, target, length, progress, on_process):
                for p in (0.411, 0.412, 0.42, 0.421):
                    progress(p)

            def transcribe(settings, folder, duration, progress, glossary, on_process):
                for p in (0.411, 0.412, 0.42, 0.421, -1, -1):
                    progress(p)
                return []

            with patch("huiji.jobs.screen.needs_conversion", return_value=True), \
                    patch("huiji.jobs.screen.make_playable", side_effect=convert), \
                    patch("huiji.jobs.transcribe.probe_duration", return_value=10), \
                    patch("huiji.jobs.transcribe.transcribe", side_effect=transcribe), \
                    patch.object(store, "update", wraps=store.update) as update:
                jobs._process(mid)
            texts = [call.kwargs.get("progress") for call in update.call_args_list]
            for text in ("轉換影片 41%", "轉換影片 42%", "轉錄中 41%", "轉錄中 42%", "分辨說話者…"):
                self.assertEqual(texts.count(text), 1)

    def test_retranscribe_removes_cached_srt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "meetings", root / "trash", root / "library.json")
            mid = store.create(source="import")["id"]
            folder = store.folder(mid)
            (folder / "whisper").mkdir()
            for name in ("transcript.json", "notes.json", "whisper/work.srt"):
                (folder / name).write_text("[]", encoding="utf-8")
            jobs = self.make_jobs(store)
            jobs.enqueue(mid, retranscribe=True)
            for name in ("transcript.json", "notes.json", "whisper/work.srt"):
                self.assertFalse((folder / name).exists())
            self.assertEqual(jobs.queue.get_nowait(), mid)

    def test_transcribes_when_cached_srt_is_not_newer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / "meetings", root / "trash", root / "library.json")
            mid = store.create(source="import")["id"]
            store.update(mid, duration=10)
            folder = store.folder(mid)
            audio = folder / "audio.m4a"
            audio.touch()
            os.utime(audio, (200, 200))
            srt = folder / "whisper" / "work.srt"
            srt.parent.mkdir()
            srt.touch()
            jobs = self.make_jobs(store)
            for modified in (100, 200):
                with self.subTest(modified=modified):
                    os.utime(srt, (modified, modified))
                    with patch("huiji.jobs.transcribe.transcribe", return_value=[]) as run:
                        jobs._process(mid)
                    run.assert_called_once()
                    (folder / "transcript.json").unlink()


if __name__ == "__main__":
    unittest.main()
