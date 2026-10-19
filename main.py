import ctypes
import faulthandler
import sys
from ctypes import wintypes

from huiji import config, server
from huiji.api import Api

_instance_mutex = None


def _redirect_output() -> None:
    """打包後一律寫入紀錄，避免無主控台時遺失背景工作的錯誤。"""
    if getattr(sys, "frozen", False):
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.APP_DIR / "huiji.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = log
        faulthandler.enable(log)


def _single_instance() -> bool:
    global _instance_mutex
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.GetLastError.restype = wintypes.DWORD
    # 保留控制代碼直到程序結束，讓後續啟動仍能辨認既有實例。
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\HuijiSingleInstance")
    error = kernel32.GetLastError()
    if not _instance_mutex:
        raise ctypes.WinError(error)
    if error != 183:
        return True

    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    hwnd = user32.FindWindowW(None, "會記 Huiji")
    if hwnd:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    return False


def main() -> None:
    _redirect_output()
    if "--dev" not in sys.argv and not _single_instance():
        return
    api = Api()

    if "--dev" in sys.argv:
        # 開發模式：不開視窗，用瀏覽器開 http://127.0.0.1:8765/?dev
        import time
        port = server.start(api._folder, dev_api=api, port=8765, subtitles_of=api._subtitles)
        print(f"http://127.0.0.1:{port}/?dev", flush=True)
        while True:
            time.sleep(3600)

    import webview

    port = server.start(api._folder, subtitles_of=api._subtitles)
    url = f"http://127.0.0.1:{port}/"
    window = webview.create_window("會記 Huiji", url, js_api=api, text_select=True,
                                   width=1280, height=820, min_size=(900, 600), background_color="#202020")
    api._attach(window, url)

    def on_closing():
        # 關閉視窗時還在錄音：先把檔案寫完，下次開啟會自動接著轉錄
        if api._recorder:
            api.stop_recording(process=False)
        api.stop_meter()
        if api._mini:
            api._mini.destroy()

    window.events.closing += on_closing
    webview.start(debug="--debug" in sys.argv, private_mode=False)


if __name__ == "__main__":
    main()
