"""螢幕錄影：整個螢幕用 ffmpeg gdigrab 擷取，單一視窗用 wincap（PrintWindow）擷取；硬體編碼（NVENC／Quick Sync）優先。

暫停時結束目前的片段、繼續時開新的片段（video_000.mkv、video_001.mkv…），
錄完後依各片段對應的錄音長度串接，再跟混音後的 audio.m4a 合成 recording.mp4。
片段用 mkv：程式當掉時已寫入的部分還讀得到。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import io
import re
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageGrab

from . import wincap
from .transcribe import NO_WINDOW, probe_duration

_encoder_cache: dict[str, list[str]] = {}
_encoder_lock = threading.Lock()

ENCODERS = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "30", "-b:v", "0"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "28"]),
    ("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-tune", "stillimage"]),
]


# ---- 會議錄影程式結束時一併結束 ffmpeg ----
class _JobLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JobLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_job = None


def _kill_with_parent(proc: subprocess.Popen) -> None:
    """把 ffmpeg 放進 Job Object：會記結束（包括當掉）時，Windows 會自動結束它，不會在背景繼續錄影"""
    global _job
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wt.HANDLE
    k32.OpenProcess.restype = wt.HANDLE
    if _job is None:
        job = k32.CreateJobObjectW(None, None)
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(wt.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        _job = job
    handle = k32.OpenProcess(0x0101, False, proc.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if handle:
        k32.AssignProcessToJobObject(wt.HANDLE(_job), wt.HANDLE(handle))
        k32.CloseHandle(wt.HANDLE(handle))


# ---- 螢幕列表 ----
class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


def list_monitors() -> list[dict]:
    """回傳實際像素座標（不受 Windows 縮放比例影響）的螢幕清單，主螢幕排第一"""
    user32 = ctypes.windll.user32
    # 只在這個執行緒暫時切換成 per-monitor DPI aware，才拿得到實際像素
    old = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        pass
    monitors: list[dict] = []

    def callback(hmon, _hdc, _rect, _data):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        monitors.append({"x": r.left, "y": r.top, "width": r.right - r.left, "height": r.bottom - r.top,
                         "primary": bool(info.dwFlags & 1), "device": info.szDevice})
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)(callback)
    user32.EnumDisplayMonitors(None, None, proc, 0)
    if old:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
    monitors.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    for i, m in enumerate(monitors):
        m["index"] = i
        m["name"] = f"螢幕 {i + 1}{'（主螢幕）' if m['primary'] else ''}　{m['width']}×{m['height']}"
    return monitors


def list_sources() -> dict:
    return {"monitors": list_monitors(), "windows": wincap.list_windows()}


def describe(source: dict) -> str:
    if source.get("kind") == "window":
        return f"視窗：{source.get('title') or source.get('exe')}"
    monitors = list_monitors()
    i = source.get("index", 0)
    return monitors[i]["name"] if 0 <= i < len(monitors) else monitors[0]["name"]


def thumbnail(source: dict, max_width: int = 480) -> str | None:
    """來源目前的畫面（JPEG data URL），給選擇來源時預覽用"""
    if source.get("kind") == "window":
        hwnd = wincap.find_window(source)
        img = wincap.snapshot(hwnd) if hwnd else None
    else:
        monitors = list_monitors()
        i = source.get("index", 0)
        m = monitors[i] if 0 <= i < len(monitors) else monitors[0]
        img = ImageGrab.grab(bbox=(m["x"], m["y"], m["x"] + m["width"], m["y"] + m["height"]), all_screens=True)
    if img is None:
        return None
    img = img.convert("RGB")
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pick_encoder(ffmpeg: str) -> list[str]:
    """試著用小畫面編碼一次，挑第一個能用的編碼器"""
    with _encoder_lock:
        if ffmpeg in _encoder_cache:
            return _encoder_cache[ffmpeg]
        for _name, args in ENCODERS:
            r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                "-i", "color=s=640x360:d=0.2", *args, "-f", "null", "-"],
                               capture_output=True, creationflags=NO_WINDOW)
            if r.returncode == 0:
                _encoder_cache[ffmpeg] = args
                return args
        raise RuntimeError("找不到可用的 H.264 編碼器")


# ---- 錄影 ----
class ScreenRecorder:
    """source：{"kind": "monitor", "index": 0} 或 {"kind": "window", "hwnd": …, "title": …, "exe": …}"""

    def __init__(self, ffmpeg: str, folder: Path, source: dict, fps: int = 15):
        self.ffmpeg = ffmpeg
        self.folder = folder
        self.fps = fps
        self.encoder = pick_encoder(ffmpeg)
        self.hwnd = None
        self.monitor = None
        if source.get("kind") == "window":
            self.hwnd = wincap.find_window(source)
            if not self.hwnd:
                raise RuntimeError(f"找不到要錄的視窗「{source.get('title') or source.get('exe')}」，請重新選擇錄影來源")
        else:
            monitors = list_monitors()
            i = source.get("index", 0)
            self.monitor = monitors[i] if 0 <= i < len(monitors) else monitors[0]
        self.proc: subprocess.Popen | None = None
        self.window: wincap.WindowRecorder | None = None
        self.segments: list[dict] = []  # [{"file": "video_000.mkv", "duration": 秒}]
        self._segment_start = 0.0

    def start_segment(self, elapsed: float) -> None:
        name = f"video_{len(self.segments):03d}.mkv"
        self.segments.append({"file": name, "duration": None})
        self._segment_start = elapsed
        if self.hwnd:
            self.window = wincap.WindowRecorder(self.ffmpeg, self.hwnd, self.fps, self.encoder,
                                                self.folder / name, self.folder / "video.log")
            _kill_with_parent(self.window.proc)
            return
        m = self.monitor
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "gdigrab", "-framerate", str(self.fps), "-draw_mouse", "1",
                "-offset_x", str(m["x"]), "-offset_y", str(m["y"]),
                "-video_size", f"{m['width']}x{m['height']}", "-i", "desktop",
                "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2", *self.encoder, "-pix_fmt", "yuv420p",
                str(self.folder / name)]
        with open(self.folder / "video.log", "ab") as log:
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                                         creationflags=NO_WINDOW)
        _kill_with_parent(self.proc)

    def stop_segment(self, elapsed: float) -> None:
        if self.window:
            self.window.stop()
            self.window = None
            self.segments[-1]["duration"] = round(elapsed - self._segment_start, 3)
            return
        if not self.proc:
            return
        proc, self.proc = self.proc, None
        try:
            proc.communicate(b"q", timeout=15)  # 讓 ffmpeg 正常收尾，檔案才完整
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self.segments[-1]["duration"] = round(elapsed - self._segment_start, 3)

    def running(self) -> bool:
        if self.window:
            return self.window.running()
        return self.proc is not None and self.proc.poll() is None


# ---- 匯入的影片轉成播放器能播的 mp4 ----
# 內建播放器（WebView2）能直接播 mp4／m4v／mov／webm；OBS 預設的 mkv 等格式要先轉
NEEDS_CONVERSION = {".mkv", ".avi", ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".vob"}


def probe_streams(ffmpeg: str, path: Path) -> tuple[str | None, str | None]:
    """回傳（影像編碼, 聲音編碼），沒有的話是 None。例如 ("h264", "aac")"""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, creationflags=NO_WINDOW)
    info = r.stderr.decode("utf-8", "replace")
    video = re.search(r"Stream #\S+.*?: Video: (\w+)", info)
    audio = re.search(r"Stream #\S+.*?: Audio: (\w+)", info)
    # 封面圖（attached pic）不算影片
    if video and "attached pic" in info[video.start():info.find("\n", video.start())]:
        video = None
    return (video.group(1) if video else None), (audio.group(1) if audio else None)


def needs_conversion(ffmpeg: str, path: Path) -> bool:
    return path.suffix.lower() in NEEDS_CONVERSION and probe_streams(ffmpeg, path)[0] is not None


def make_playable(ffmpeg: str, src: Path, out: Path, duration: float, on_progress=None, on_process=None) -> None:
    """轉成 mp4：H.264 影像直接搬過去（不重新編碼，很快、畫質不變），其他編碼才重新壓縮；聲音轉成 AAC"""
    video, audio = probe_streams(ffmpeg, src)
    vargs = ["-c:v", "copy"] if video == "h264" else [*pick_encoder(ffmpeg), "-pix_fmt", "yuv420p"]
    aargs = ["-c:a", "aac", "-b:a", "160k"] if audio else ["-an"]
    tmp = out.with_name(out.stem + ".tmp.mp4")
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
            *vargs, *aargs, "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(tmp)]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    if on_process:
        on_process(proc)
    _kill_with_parent(proc)
    assert proc.stdout
    for raw in proc.stdout:
        line = raw.decode("ascii", "replace").strip()
        if on_progress and duration > 0 and line.startswith("out_time_us="):
            try:
                on_progress(min(0.99, int(line.split("=", 1)[1]) / 1e6 / duration))
            except ValueError:
                pass
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("影片轉檔失敗：" + err[-400:])
    tmp.replace(out)


def finalize(ffmpeg: str, folder: Path, segments: list[dict]) -> Path:
    """把各片段依錄音長度串接，加上 audio.m4a，輸出 recording.mp4（不重新編碼）"""
    parts = [s for s in segments if (folder / s["file"]).exists() and (folder / s["file"]).stat().st_size > 0]
    if not parts:
        raise RuntimeError("找不到錄影片段")
    lines = []
    for s in parts:
        # 片段長度以錄音長度為準：每段誤差不會累積到下一段（當掉時沒有記錄長度，就用影片本身的長度）
        duration = s.get("duration") or probe_duration(ffmpeg, folder / s["file"])
        lines += [f"file '{s['file']}'", f"duration {duration:.3f}"]
    concat = folder / "video_concat.txt"
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = folder / "recording.mp4"
    tmp = folder / "recording.tmp.mp4"
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
    if (folder / "audio.m4a").exists():
        args += ["-i", str(folder / "audio.m4a"), "-map", "0:v", "-map", "1:a"]
    args += ["-c", "copy", "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, cwd=str(folder))
    if r.returncode != 0:
        raise RuntimeError("合成影片失敗：" + r.stderr.decode("utf-8", "replace")[-400:])
    tmp.replace(out)
    concat.unlink(missing_ok=True)
    for s in parts:
        (folder / s["file"]).unlink(missing_ok=True)
    (folder / "video.log").unlink(missing_ok=True)
    return out

