import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main


class MainTests(unittest.TestCase):
    def test_existing_instance_is_activated_before_api_creation(self):
        windll = Mock()
        windll.kernel32.CreateMutexW.return_value = 123
        windll.kernel32.GetLastError.return_value = 183
        windll.user32.FindWindowW.return_value = 456
        with patch.object(main.ctypes, "windll", windll), \
                patch.object(main, "_instance_mutex", None), \
                patch.object(main, "_redirect_output"), \
                patch.object(main, "Api") as api, patch.object(sys, "argv", ["Huiji.exe"]):
            main.main()
            api.assert_not_called()
            self.assertEqual(main._instance_mutex, 123)
        windll.kernel32.CreateMutexW.assert_called_once_with(None, False, "Local\\HuijiSingleInstance")
        windll.user32.FindWindowW.assert_called_once_with(None, "會記 Huiji")
        windll.user32.ShowWindow.assert_called_once_with(456, 9)
        windll.user32.SetForegroundWindow.assert_called_once_with(456)

    def test_first_instance_keeps_mutex_handle(self):
        windll = Mock()
        windll.kernel32.CreateMutexW.return_value = 123
        windll.kernel32.GetLastError.return_value = 0
        with patch.object(main.ctypes, "windll", windll), patch.object(main, "_instance_mutex", None):
            self.assertTrue(main._single_instance())
            self.assertEqual(main._instance_mutex, 123)
        windll.user32.FindWindowW.assert_not_called()

    def test_dev_skips_mutex(self):
        with patch.object(main, "_single_instance") as single, \
                patch.object(main, "_redirect_output"), \
                patch.object(main, "Api") as api, \
                patch.object(main.server, "start", return_value=8765), \
                patch.object(sys, "argv", ["main.py", "--dev"]), \
                patch("time.sleep", side_effect=KeyboardInterrupt), patch("builtins.print"):
            with self.assertRaises(KeyboardInterrupt):
                main.main()
            single.assert_not_called()
            api.assert_called_once()

    def test_frozen_output_is_appended_and_fault_handler_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Huiji"
            for i in range(2):
                with patch.object(main.config, "APP_DIR", root), \
                        patch.object(sys, "frozen", True, create=True), \
                        patch.object(sys, "stdout", Mock()), patch.object(sys, "stderr", Mock()), \
                        patch.object(main.faulthandler, "enable") as enable:
                    main._redirect_output()
                    log = sys.stdout
                    try:
                        self.assertIs(sys.stderr, log)
                        self.assertEqual(log.encoding, "utf-8")
                        self.assertTrue(log.line_buffering)
                        enable.assert_called_once_with(log)
                        print(f"測試紀錄 {i}")
                    finally:
                        log.close()
            self.assertEqual((root / "huiji.log").read_text(encoding="utf-8"), "測試紀錄 0\n測試紀錄 1\n")

    def test_source_output_is_preserved(self):
        with patch.object(sys, "frozen", False, create=True), \
                patch.object(main.faulthandler, "enable") as enable:
            stdout, stderr = sys.stdout, sys.stderr
            main._redirect_output()
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)
            enable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
