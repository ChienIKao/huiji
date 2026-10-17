"""找出哪個說話者是使用者本人：mic.wav 明顯比 system.wav 大聲的那個人

「麥克風＋電腦聲音」模式兩軌是分開存的，時間對齊。使用者講話時 system.wav 幾乎沒聲音；
別人講話時，用喇叭的話麥克風也會收到回音，但 system.wav 會大聲得多。
兩軌各自以自己講話時的音量為基準換算，再逐格比較。
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

FRAME_SECONDS = 0.1
FLOOR_DB = -60.0      # 相對於該軌講話音量，低於這個算靜音
ACTIVE_DB = -30.0     # 任一軌高於這個才算有人在講話
DOMINANT_DB = 10.0    # 麥克風要比電腦聲音大這麼多才算「是我在講」
MIN_SHARE = 0.6       # 這個人的講話時間至少這個比例是麥克風主導
MIN_LEAD = 0.3        # 而且要比第二名高出這麼多
MIN_SECONDS = 3.0     # 有效講話時間太短就不判斷
CHUNK_FRAMES = 1 << 16


def frame_db(path: Path, frame_seconds: float = FRAME_SECONDS) -> np.ndarray:
    """每格（預設 0.1 秒）的 RMS 音量（dBFS），多聲道先平均；分段讀，長錄音也不會吃太多記憶體"""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"只支援 16-bit WAV：{path}")
        channels, rate = w.getnchannels(), w.getframerate()
        hop = max(1, int(rate * frame_seconds))
        out: list[np.ndarray] = []
        rest = np.zeros(0, dtype=np.float32)
        while True:
            data = w.readframes(CHUNK_FRAMES)
            if not data:
                break
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
            samples = samples[: samples.size // channels * channels].reshape(-1, channels).mean(axis=1)
            samples = np.concatenate([rest, samples])
            n = samples.size // hop
            if n:
                frames = samples[: n * hop].reshape(n, hop)
                out.append(np.sqrt(np.mean(frames ** 2, axis=1)))
            rest = samples[n * hop:]
    rms = np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
    return 20 * np.log10(np.maximum(rms, 1e-7))


def _relative(db: np.ndarray) -> np.ndarray:
    """換算成相對於這一軌講話音量（大聲那幾格的 95 百分位）的 dB"""
    if db.size == 0:
        return db
    ref = float(np.percentile(db, 95))
    return np.maximum(db - ref, FLOOR_DB)


def mic_shares(mic: Path, system: Path, segments: list[dict]) -> dict[str, tuple[float, float]]:
    """每個說話者：(麥克風主導的比例, 有效講話秒數)"""
    m, s = _relative(frame_db(mic)), _relative(frame_db(system))
    n = min(m.size, s.size)
    m, s = m[:n], s[:n]
    active = (m > ACTIVE_DB) | (s > ACTIVE_DB)
    dominant = active & (m - s >= DOMINANT_DB)
    totals: dict[str, list[int]] = {}
    for seg in segments:
        spk = seg.get("speaker")
        if not spk:
            continue
        a, b = int(seg["start"] / FRAME_SECONDS), min(n, int(seg["end"] / FRAME_SECONDS))
        if b <= a:
            continue
        t = totals.setdefault(spk, [0, 0])
        t[0] += int(dominant[a:b].sum())
        t[1] += int(active[a:b].sum())
    return {spk: (dom / act if act else 0.0, act * FRAME_SECONDS) for spk, (dom, act) in totals.items()}


def detect_self(folder: Path, segments: list[dict]) -> str | None:
    """回傳使用者本人的說話者代號；沒有分軌或判斷不出來就回傳 None"""
    mic, system = folder / "mic.wav", folder / "system.wav"
    if not (mic.exists() and system.exists() and mic.stat().st_size > 44 and system.stat().st_size > 44):
        return None
    shares = sorted(((share, spk) for spk, (share, secs) in mic_shares(mic, system, segments).items()
                     if secs >= MIN_SECONDS), reverse=True)
    if not shares:
        return None
    best, spk = shares[0]
    second = shares[1][0] if len(shares) > 1 else 0.0
    if best >= MIN_SHARE and best - second >= MIN_LEAD:
        return spk
    return None
