"""錄音：麥克風與系統音（WASAPI loopback）分成兩軌各自寫成 WAV。

系統沒有聲音在播放時，WASAPI loopback 不會送任何資料進來，
所以系統音那一軌依實際經過的時間補靜音，讓兩軌長度和時間點對得上。
"""
from __future__ import annotations

import struct
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pyaudiowpatch as pa

# PortAudio 的初始化／結束不能同時在兩個執行緒進行（會直接當掉），一律經過這兩個函式
_pa_lock = threading.Lock()


def _open_pa() -> pa.PyAudio:
    with _pa_lock:
        return pa.PyAudio()


def _close_pa(p: pa.PyAudio) -> None:
    with _pa_lock:
        p.terminate()

CHUNK_SECONDS = 0.05


class Track:
    def __init__(self, path: Path, rate: int, channels: int):
        self.rate = rate
        self.channels = channels
        self.frames = 0
        self.level = 0.0
        self.wav = wave.open(str(path), "wb")
        self.wav.setnchannels(channels)
        self.wav.setsampwidth(2)
        self.wav.setframerate(rate)

    def write(self, data: bytes) -> None:
        self.wav.writeframes(data)
        self.frames += len(data) // (2 * self.channels)

    def pad_to(self, frames: int) -> None:
        missing = frames - self.frames
        if missing > 0:
            self.write(b"\x00" * (missing * 2 * self.channels))

    def close(self) -> None:
        self.wav.close()


def _level(data: bytes) -> float:
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768
    # 轉成 0–1 的音量條：-60 dB 到 0 dB
    db = 20 * float(np.log10(max(rms, 1e-6)))
    return max(0.0, min(1.0, (db + 60) / 60))


def list_devices() -> dict:
    p = _open_pa()
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        mics, loopbacks = [], []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"]:
                continue
            if d.get("isLoopbackDevice"):
                loopbacks.append({"index": i, "name": d["name"].replace(" [Loopback]", "")})
            elif d["maxInputChannels"] > 0:
                mics.append({"index": i, "name": d["name"]})
        return {"mics": mics, "loopbacks": loopbacks}
    finally:
        _close_pa(p)


class Recorder:
    """mode: both | mic | system"""

    def __init__(self, folder: Path, mode: str = "both", mic_device: int = -1, loopback_device: int = -1):
        self.folder = folder
        self.mode = mode
        self.mic_device = mic_device
        self.loopback_device = loopback_device
        self.p = None
        self.streams = []
        self.mic: Track | None = None
        self.system: Track | None = None
        self.lock = threading.Lock()
        self.started_at = 0.0
        self.paused_total = 0.0
        self.paused_at: float | None = None

    # ---- 時間 ----
    @property
    def paused(self) -> bool:
        return self.paused_at is not None

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        now = self.paused_at or time.monotonic()
        return now - self.started_at - self.paused_total

    # ---- 控制 ----
    def start(self) -> None:
        self.p = _open_pa()
        wasapi = self.p.get_host_api_info_by_type(pa.paWASAPI)
        if self.mode in ("both", "mic"):
            info = self.p.get_device_info_by_index(
                self.mic_device if self.mic_device >= 0 else wasapi["defaultInputDevice"])
            self.mic = self._open(info, self.folder / "mic.wav", self._on_mic)
        if self.mode in ("both", "system"):
            info = self._loopback_info(wasapi)
            self.system = self._open(info, self.folder / "system.wav", self._on_system)
        self.started_at = time.monotonic()
        for s in self.streams:
            s.start_stream()

    def _loopback_info(self, wasapi) -> dict:
        if self.loopback_device >= 0:
            return self.p.get_device_info_by_index(self.loopback_device)
        speakers = self.p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        for d in self.p.get_loopback_device_info_generator():
            if speakers["name"] in d["name"]:
                return d
        raise RuntimeError(f"找不到「{speakers['name']}」的系統音錄音裝置")

    def _open(self, info: dict, path: Path, callback) -> Track:
        rate = int(info["defaultSampleRate"])
        channels = max(1, min(2, int(info["maxInputChannels"])))
        track = Track(path, rate, channels)
        stream = self.p.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                             input_device_index=info["index"], frames_per_buffer=int(rate * CHUNK_SECONDS),
                             stream_callback=callback)
        self.streams.append(stream)
        return track

    def pause(self) -> None:
        with self.lock:
            if self.paused_at is None:
                self.paused_at = time.monotonic()

    def resume(self) -> None:
        with self.lock:
            if self.paused_at is not None:
                self.paused_total += time.monotonic() - self.paused_at
                self.paused_at = None

    def stop(self) -> float:
        duration = self.elapsed()
        for s in self.streams:
            s.stop_stream()
            s.close()
        with self.lock:
            for track in (self.mic, self.system):
                if track:
                    track.pad_to(int(duration * track.rate))
                    track.close()
        if self.p:
            _close_pa(self.p)
        return duration

    def levels(self) -> dict:
        return {"mic": self.mic.level if self.mic else None,
                "system": self.system.level if self.system else None}

    # ---- 回呼（在 PortAudio 的執行緒） ----
    def _on_mic(self, data, frames, time_info, status):
        with self.lock:
            if self.paused_at is None and self.mic:
                self.mic.write(data)
                self.mic.level = _level(data)
            elif self.mic:
                self.mic.level = 0.0
        return (None, pa.paContinue)

    def _on_system(self, data, frames, time_info, status):
        with self.lock:
            if self.paused_at is None and self.system:
                # 前面沒聲音的那段補成靜音（容許 0.2 秒的緩衝誤差）
                expected = int(self.elapsed() * self.system.rate) - frames
                if expected - self.system.frames > self.system.rate * 0.2:
                    self.system.pad_to(expected)
                self.system.write(data)
                self.system.level = _level(data)
        return (None, pa.paContinue)


def repair_wav(path: Path) -> None:
    """程式當掉時 WAV 檔頭的長度沒寫進去：依檔案大小補回來"""
    if not path.exists() or path.stat().st_size < 44:
        return
    size = path.stat().st_size
    with open(path, "r+b") as f:
        header = f.read(44)
        if header[:4] != b"RIFF" or header[36:40] != b"data":
            return
        f.seek(4)
        f.write(struct.pack("<I", size - 8))
        f.seek(40)
        f.write(struct.pack("<I", size - 44))


class LevelMonitor:
    """還沒開始錄音時的音量條：開啟麥克風與系統音，只算音量、不寫檔"""

    def __init__(self, mic_device: int = -1, loopback_device: int = -1):
        self.mic_device = mic_device
        self.loopback_device = loopback_device
        self.p = None
        self.streams = []
        self._levels = {"mic": 0.0, "system": 0.0}
        self._seen = {"mic": 0.0, "system": 0.0}

    def start(self) -> None:
        self.p = _open_pa()
        wasapi = self.p.get_host_api_info_by_type(pa.paWASAPI)
        try:
            mic = self.p.get_device_info_by_index(
                self.mic_device if self.mic_device >= 0 else wasapi["defaultInputDevice"])
            self._open(mic, "mic")
        except (OSError, IOError):
            self._levels["mic"] = None
        try:
            rec = Recorder.__new__(Recorder)
            rec.p, rec.loopback_device = self.p, self.loopback_device
            self._open(rec._loopback_info(wasapi), "system")
        except (OSError, IOError, RuntimeError):
            self._levels["system"] = None
        for s in self.streams:
            s.start_stream()

    def _open(self, info: dict, key: str) -> None:
        rate = int(info["defaultSampleRate"])
        channels = max(1, min(2, int(info["maxInputChannels"])))

        def callback(data, frames, time_info, status):
            self._levels[key] = _level(data)
            self._seen[key] = time.monotonic()
            return (None, pa.paContinue)

        self.streams.append(self.p.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                                        input_device_index=info["index"], frames_per_buffer=int(rate * CHUNK_SECONDS),
                                        stream_callback=callback))

    def levels(self) -> dict:
        now = time.monotonic()
        # 系統沒有聲音在播放時 loopback 不會送資料進來：一段時間沒收到就當作 0
        return {k: (None if v is None else (v if now - self._seen[k] < 0.3 else 0.0)) for k, v in self._levels.items()}

    def stop(self) -> None:
        for s in self.streams:
            try:
                s.stop_stream()
                s.close()
            except OSError:
                pass
        self.streams = []
        if self.p:
            _close_pa(self.p)
            self.p = None
