"""視窗與螢幕擷取：列出可以錄的視窗、截圖，以及錄影時持續擷取某個視窗或整個螢幕。

視窗用 PrintWindow（PW_RENDERFULLCONTENT），Chrome、Teams、Zoom 這類用 GPU 繪製的視窗也擷取得到，
視窗被其他視窗蓋住時也照錄。ffmpeg 的 gdigrab 擷取這類視窗只會得到黑畫面，所以不用它。

整個螢幕用 BitBlt 擷取、自己把滑鼠游標畫上去。gdigrab 擷取時帶 CAPTUREBLT，
Windows 每擷取一張就把游標藏起來再顯示，錄影時滑鼠會一直閃，所以也不用它。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


def _sig(fn, restype, *argtypes):
    """宣告參數與回傳型別：沒宣告的話 ctypes 會把 64 位元的 HDC／HBITMAP 截成 32 位元，程式會當掉"""
    fn.restype = restype
    fn.argtypes = argtypes


_sig(user32.GetWindowDC, wt.HDC, wt.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
_sig(user32.PrintWindow, wt.BOOL, wt.HWND, wt.HDC, wt.UINT)
_sig(user32.IsWindow, wt.BOOL, wt.HWND)
_sig(user32.IsIconic, wt.BOOL, wt.HWND)
_sig(user32.IsWindowVisible, wt.BOOL, wt.HWND)
_sig(user32.GetWindowRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
_sig(user32.GetWindowTextLengthW, ctypes.c_int, wt.HWND)
_sig(user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
_sig(user32.GetWindowLongW, ctypes.c_long, wt.HWND, ctypes.c_int)
_sig(user32.GetWindow, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.GetDC, wt.HDC, wt.HWND)
_sig(user32.GetCursorInfo, wt.BOOL, ctypes.c_void_p)
_sig(user32.GetIconInfo, wt.BOOL, wt.HICON, ctypes.c_void_p)
_sig(user32.DrawIconEx, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, wt.HICON, ctypes.c_int, ctypes.c_int, wt.UINT,
     wt.HBRUSH, wt.UINT)
_sig(gdi32.BitBlt, wt.BOOL, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
     ctypes.c_int, wt.DWORD)
_sig(gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
_sig(gdi32.CreateCompatibleBitmap, wt.HBITMAP, wt.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_sig(gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
_sig(gdi32.DeleteDC, wt.BOOL, wt.HDC)
_sig(gdi32.GetDIBits, ctypes.c_int, wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT)
_sig(kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_sig(kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
_sig(kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
_sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)

PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020
CURSOR_SHOWING = 1
DI_NORMAL = 3
WS_EX_TOOLWINDOW = 0x80
GWL_EXSTYLE = -20
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9


def dpi_aware_thread() -> None:
    """讓目前這個執行緒拿到的座標是實際像素（不受 Windows 縮放比例影響）"""
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        pass


class _CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


class _IconInfo(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _exe(hwnd) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_rect(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _cloaked(hwnd) -> bool:
    value = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return value.value != 0


def list_windows() -> list[dict]:
    """可以錄的視窗：看得到、有標題、不是工具列小視窗，也不是會記自己"""
    dpi_aware_thread()
    me = os.getpid()
    out: list[dict] = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        title = _title(hwnd)
        if not title or title == "Program Manager" or _pid(hwnd) == me:
            return True
        _x, _y, w, h = window_rect(hwnd)
        if w < 160 or h < 100:
            return True
        out.append({"hwnd": int(hwnd), "title": title, "exe": _exe(hwnd), "width": w, "height": h,
                    "minimized": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(callback), 0)
    return out


def find_window(source: dict) -> int | None:
    """依上次選的視窗找回它：先用 hwnd，視窗重開過就用程式名稱＋標題，再不行就只用程式名稱"""
    hwnd = source.get("hwnd")
    if hwnd and user32.IsWindow(hwnd) and _exe(hwnd) == source.get("exe"):
        return hwnd
    windows = list_windows()
    for w in windows:
        if w["exe"] == source.get("exe") and w["title"] == source.get("title"):
            return w["hwnd"]
    for w in windows:
        if w["exe"] and w["exe"] == source.get("exe"):
            return w["hwnd"]
    return None


class _Grabber:
    """重複擷取同一個視窗時共用 DC 和點陣圖"""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.size = (0, 0)
        self.hdc = self.mdc = self.bmp = None

    def _alloc(self, w: int, h: int) -> None:
        self.release()
        self.hdc = user32.GetWindowDC(self.hwnd)
        self.mdc = gdi32.CreateCompatibleDC(self.hdc)
        self.bmp = gdi32.CreateCompatibleBitmap(self.hdc, w, h)
        gdi32.SelectObject(self.mdc, self.bmp)
        self.buf = ctypes.create_string_buffer(w * h * 4)
        self.info = _BitmapInfo(ctypes.sizeof(_BitmapInfo), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.size = (w, h)

    def grab(self) -> tuple[bytes, tuple[int, int]] | None:
        """回傳 BGRA 位元組與大小；視窗縮到最小或已經關閉時回傳 None"""
        if not user32.IsWindow(self.hwnd) or user32.IsIconic(self.hwnd):
            return None
        _x, _y, w, h = window_rect(self.hwnd)
        if w <= 0 or h <= 0:
            return None
        if (w, h) != self.size:
            self._alloc(w, h)
        if not user32.PrintWindow(self.hwnd, self.mdc, PW_RENDERFULLCONTENT):
            return None
        gdi32.GetDIBits(self.mdc, self.bmp, 0, h, self.buf, ctypes.byref(self.info), 0)
        return self.buf.raw, (w, h)

    def release(self) -> None:
        if self.bmp:
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mdc)
            user32.ReleaseDC(self.hwnd, self.hdc)
        self.hdc = self.mdc = self.bmp = None
        self.size = (0, 0)


class _ScreenGrabber:
    """擷取螢幕上的一塊區域（實際像素座標），並畫上滑鼠游標"""

    def __init__(self, x: int, y: int, w: int, h: int):
        self.x, self.y, self.size = x, y, (w, h)
        self.hdc = user32.GetDC(None)
        self.mdc = gdi32.CreateCompatibleDC(self.hdc)
        self.bmp = gdi32.CreateCompatibleBitmap(self.hdc, w, h)
        gdi32.SelectObject(self.mdc, self.bmp)
        self.buf = ctypes.create_string_buffer(w * h * 4)
        self.info = _BitmapInfo(ctypes.sizeof(_BitmapInfo), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)

    def _draw_cursor(self) -> None:
        ci = _CursorInfo(ctypes.sizeof(_CursorInfo))
        if not user32.GetCursorInfo(ctypes.byref(ci)) or not ci.flags & CURSOR_SHOWING or not ci.hCursor:
            return
        ii = _IconInfo()
        if not user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
            return
        for b in (ii.hbmMask, ii.hbmColor):
            if b:
                gdi32.DeleteObject(b)
        user32.DrawIconEx(self.mdc, ci.ptScreenPos.x - ii.xHotspot - self.x, ci.ptScreenPos.y - ii.yHotspot - self.y,
                          ci.hCursor, 0, 0, 0, None, DI_NORMAL)

    def grab(self) -> tuple[bytes, tuple[int, int]] | None:
        w, h = self.size
        if not gdi32.BitBlt(self.mdc, 0, 0, w, h, self.hdc, self.x, self.y, SRCCOPY):
            return None  # 例如鎖定畫面時
        self._draw_cursor()
        gdi32.GetDIBits(self.mdc, self.bmp, 0, h, self.buf, ctypes.byref(self.info), 0)
        return self.buf.raw, self.size

    def release(self) -> None:
        if self.bmp:
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mdc)
            user32.ReleaseDC(None, self.hdc)
        self.hdc = self.mdc = self.bmp = None


def snapshot(hwnd: int) -> Image.Image | None:
    dpi_aware_thread()
    g = _Grabber(hwnd)
    try:
        r = g.grab()
        if not r:
            return None
        data, size = r
        return Image.frombuffer("RGBA", size, data, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        g.release()


class _FrameRecorder:
    """把擷取到的畫面以固定影格率送進 ffmpeg 的 stdin（rawvideo）。

    影格數跟著實際經過的時間走：擷取慢了就重複上一張補上，影片長度才會跟錄音對得上。
    畫面大小跟一開始不同時縮放成一開始的大小；擷取不到時停在最後一張畫面。
    子類別要先設好 self.size，再呼叫 _start()。
    """

    size: tuple[int, int]

    def _grabber(self):
        raise NotImplementedError

    def _start(self, ffmpeg: str, fps: int, encoder: list[str], out: Path, log: Path) -> None:
        self.fps = fps
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{self.size[0]}x{self.size[1]}",
                "-framerate", str(fps), "-i", "-", *encoder, "-pix_fmt", "yuv420p", str(out)]
        with open(log, "ab") as f:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=f,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _frame(self, grabber: _Grabber, last: bytes | None) -> bytes:
        r = grabber.grab()
        if not r:
            return last or b"\x00" * (self.size[0] * self.size[1] * 4)
        data, size = r
        if size == self.size:
            return data
        img = Image.frombuffer("RGBA", size, data, "raw", "RGBA", 0, 1).resize(self.size)
        return img.tobytes()

    def _run(self) -> None:
        dpi_aware_thread()
        grabber = self._grabber()
        start = time.perf_counter()
        written = 0
        last = None
        try:
            while not self._stop.is_set():
                last = self._frame(grabber, last)
                due = int((time.perf_counter() - start) * self.fps) + 1
                while written < due:
                    self.proc.stdin.write(last)
                    written += 1
                next_at = start + written / self.fps
                self._stop.wait(max(0.0, next_at - time.perf_counter()))
        except (BrokenPipeError, OSError):
            pass
        finally:
            grabber.release()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def running(self) -> bool:
        return self.proc.poll() is None and self._thread.is_alive()


class WindowRecorder(_FrameRecorder):
    """持續擷取一個視窗：視窗縮到最小或關掉時停在最後一張畫面"""

    def __init__(self, ffmpeg: str, hwnd: int, fps: int, encoder: list[str], out: Path, log: Path):
        dpi_aware_thread()
        self.hwnd = hwnd
        _x, _y, w, h = window_rect(hwnd)
        self.size = (max(2, w - w % 2), max(2, h - h % 2))
        self._start(ffmpeg, fps, encoder, out, log)

    def _grabber(self):
        return _Grabber(self.hwnd)


class MonitorRecorder(_FrameRecorder):
    """持續擷取整個螢幕（monitor 是 screen.list_monitors() 的一項，實際像素座標），含滑鼠游標"""

    def __init__(self, ffmpeg: str, monitor: dict, fps: int, encoder: list[str], out: Path, log: Path):
        self.monitor = monitor
        w, h = monitor["width"], monitor["height"]
        self.size = (max(2, w - w % 2), max(2, h - h % 2))
        self._start(ffmpeg, fps, encoder, out, log)

    def _grabber(self):
        return _ScreenGrabber(self.monitor["x"], self.monitor["y"], *self.size)
