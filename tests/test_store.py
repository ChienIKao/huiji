import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from huiji.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = Store(root / "meetings", root / "trash", root / "library.json")
        self.meeting = self.store.create(source="import")
        self.mid = self.meeting["id"]

    def test_concurrent_read_and_update(self):
        start = threading.Barrier(5)
        deadline = time.monotonic() + 1

        def write(index):
            start.wait()
            count = 0
            while time.monotonic() < deadline:
                count += 1
                self.store.update(self.mid, **{f"writer_{index}": count})
            return count

        def read():
            start.wait()
            while time.monotonic() < deadline:
                self.assertEqual(self.store.load(self.mid)["id"], self.mid)
                self.assertEqual([m["id"] for m in self.store.list()], [self.mid])

        with ThreadPoolExecutor(max_workers=5) as pool:
            writers = [pool.submit(write, i) for i in range(3)]
            readers = [pool.submit(read) for _ in range(2)]
            counts = [future.result(timeout=5) for future in writers]
            for future in readers:
                future.result(timeout=5)
        meeting = self.store.load(self.mid)
        for i, count in enumerate(counts):
            self.assertGreater(count, 0)
            self.assertEqual(meeting[f"writer_{i}"], count)

    def test_writes_retry_permission_error(self):
        original = Path.replace
        operations = [lambda: self.store.save(self.meeting),
                      lambda: self.store.update(self.mid, progress="測試中"),
                      lambda: self.store.write_json(self.mid, "transcript.json", [])]
        for operation in operations:
            with self.subTest(operation=operation), patch("huiji.store.time.sleep"), \
                    patch.object(Path, "replace", autospec=True) as replace:
                attempts = 0

                def retry(path, target):
                    nonlocal attempts
                    attempts += 1
                    if attempts < 3:
                        raise PermissionError("檔案使用中")
                    return original(path, target)

                replace.side_effect = retry
                operation()
                self.assertEqual(attempts, 3)
        self.assertEqual(self.store.load(self.mid)["progress"], "測試中")
        self.assertEqual(self.store.read_json(self.mid, "transcript.json"), [])

    def test_reads_retry_permission_error(self):
        self.store.write_json(self.mid, "transcript.json", [])
        original = Path.read_text
        for read in (lambda: self.store.load(self.mid),
                     lambda: self.store.read_json(self.mid, "transcript.json"), self.store.list):
            with self.subTest(read=read), patch("huiji.store.time.sleep"), \
                    patch.object(Path, "read_text", autospec=True) as read_text:
                attempts = 0

                def retry(path, *args, **kwargs):
                    nonlocal attempts
                    attempts += 1
                    if attempts < 3:
                        raise PermissionError("檔案使用中")
                    return original(path, *args, **kwargs)

                read_text.side_effect = retry
                self.assertIsNotNone(read())
                self.assertGreaterEqual(attempts, 3)

    def test_permission_error_eventually_propagates(self):
        with patch("huiji.store.time.sleep"), \
                patch.object(Path, "replace", side_effect=PermissionError("檔案使用中")) as replace:
            with self.assertRaises(PermissionError):
                self.store.update(self.mid, progress="測試中")
            self.assertEqual(replace.call_count, 20)
        self.assertEqual(self.store.load(self.mid)["progress"], "")
        with patch("huiji.store.time.sleep"), \
                patch.object(Path, "read_text", side_effect=PermissionError("檔案使用中")) as read:
            with self.assertRaises(PermissionError):
                self.store.load(self.mid)
            self.assertEqual(read.call_count, 20)


if __name__ == "__main__":
    unittest.main()
