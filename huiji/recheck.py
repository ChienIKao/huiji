"""短句的說話者再確認：規則改寫自 Shiyin 的 SpeakerRecheck。"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import config, speakers

MODEL_URL = ("https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/"
             "0743f301363dec56491a490f6d6cbc9d67f9a3bf/"
             "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")
MODEL_SIZE = 39_593_761
MODEL_SHA256 = "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
MIN_SECONDS = 0.5
MAX_SECONDS = 4.0
VOICE_SECONDS = 60.0
MARGIN = 0.25
RATE = 16000
Embed = Callable[[float, float], np.ndarray | None]


@dataclass
class Result:
    speakers: list[str | None]
    checked: int = 0
    changed: int = 0


def _unit(vector) -> np.ndarray | None:
    if vector is None:
        return None
    vector = np.asarray(vector, dtype=np.float64).ravel()
    norm = float(np.linalg.norm(vector))
    return vector / norm if vector.size and np.isfinite(norm) and norm > 0 else None


def _embed(embed: Embed, start: float, end: float) -> np.ndarray | None:
    try:
        vector = _unit(embed(start, end))
        if vector is None:
            print(f"短句聲紋無法使用（{start:.3f}～{end:.3f}）", file=sys.stderr)
        return vector
    except Exception as e:
        print(f"短句聲紋計算失敗（{start:.3f}～{end:.3f}）：{e!r}", file=sys.stderr)
        return None


def build_voices(segments: list[dict], embed: Embed) -> dict[str, np.ndarray]:
    """長句優先取滿 60 秒，避免短句的誤分主導整個人的聲紋。"""
    grouped: dict[str, list[dict]] = {}
    for seg in segments:
        if seg.get("speaker") and seg["end"] - seg["start"] >= MIN_SECONDS:
            grouped.setdefault(seg["speaker"], []).append(seg)
    voices = {}
    for spk, items in grouped.items():
        remaining = VOICE_SECONDS
        weighted = None
        for seg in sorted(items, key=lambda s: s["end"] - s["start"], reverse=True):
            seconds = min(remaining, seg["end"] - seg["start"])
            if seconds < MIN_SECONDS:
                break
            vector = _embed(embed, seg["start"], seg["start"] + seconds)
            if vector is None:
                continue
            weighted = vector * seconds if weighted is None else weighted + vector * seconds
            remaining -= seconds
        voice = _unit(weighted)
        if voice is not None:
            voices[spk] = voice
    return voices


def refine(segments: list[dict], voices: dict[str, np.ndarray], embed: Embed,
           mic_shares: dict[str, float] | None = None) -> Result:
    result = Result([seg.get("speaker") for seg in segments])
    if len(voices) < 2:
        return result
    order = sorted(voices, key=lambda spk: int(spk.rsplit("_", 1)[1]))
    for i, seg in enumerate(segments):
        current = seg.get("speaker")
        if current not in voices or not MIN_SECONDS <= seg["end"] - seg["start"] <= MAX_SECONDS:
            continue
        result.checked += 1
        vector = _embed(embed, seg["start"], seg["end"])
        if vector is None:
            continue
        best = max((spk for spk in order if spk != current), key=lambda spk: float(vector @ voices[spk]))
        if float(vector @ voices[best]) - float(vector @ voices[current]) < MARGIN:
            continue
        shares = mic_shares or {}
        if speakers._side(shares.get(current)) * speakers._side(shares.get(best)) < 0:
            continue
        result.speakers[i] = best
        result.changed += 1
    return result


def recheck(segments: list[dict], embed: Embed, mic_shares: dict[str, float] | None = None) -> Result:
    return refine(segments, build_voices(segments, embed), embed, mic_shares)


def _valid_model(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256


def ensure_model() -> Path:
    path = config.APP_DIR / "models" / "eres2net.onnx"
    if _valid_model(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="eres2net-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(MODEL_URL, timeout=30) as response:
            size = 0
            while chunk := response.read(1 << 20):
                size += len(chunk)
                if size > MODEL_SIZE:
                    raise ValueError("聲紋模型大小不符")
                output.write(chunk)
        if not _valid_model(temporary):
            raise ValueError("聲紋模型大小或 SHA-256 不符")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def audio_embed(work: Path) -> Embed:
    import sherpa_onnx

    model = ensure_model()
    settings = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(model), num_threads=2, provider="cpu")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(settings)
    with wave.open(str(work), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("短句重新確認需要 16 kHz 單聲道 16-bit WAV")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768

    def embed(start: float, end: float) -> np.ndarray | None:
        clip = samples[max(0, int(start * RATE)):max(0, int(end * RATE))]
        if clip.size < int(MIN_SECONDS * RATE):
            return None
        stream = extractor.create_stream()
        stream.accept_waveform(sample_rate=RATE, waveform=clip)
        stream.input_finished()
        if not extractor.is_ready(stream):
            print(f"短句聲紋資料不足（{start:.3f}～{end:.3f}）", file=sys.stderr)
            return None
        return extractor.compute(stream)

    return embed


def _srt_entries(text: str) -> tuple[list[dict], list[tuple[int, int] | None]]:
    from .transcribe import SRT_BLOCK, SPEAKER

    segments, spans = [], []
    for match in SRT_BLOCK.finditer(text):
        g = match.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
        tag = SPEAKER.match(g[8])
        segments.append({"start": start, "end": end, "speaker": tag.group(1) if tag else None})
        spans.append((match.start(9) + tag.start(1), match.start(9) + tag.end(1)) if tag else None)
    return segments, spans


def relabel_srt(text: str, labels: list[str | None]) -> str:
    segments, spans = _srt_entries(text)
    if len(labels) != len(segments):
        raise ValueError("字幕區塊與說話者數量不符")
    # 倒著替換位置，前面區塊的偏移才不會改變。
    for seg, span, label in reversed(list(zip(segments, spans, labels))):
        if span and label and label != seg["speaker"]:
            text = text[:span[0]] + label + text[span[1]:]
    return text


def recheck_speakers(folder: Path, srt: Path, work: Path) -> Result | None:
    """聲紋服務失敗時保留原稿，轉錄仍可完成。"""
    from . import selfvoice

    try:
        text = srt.read_bytes().decode("utf-8")
        segments, _ = _srt_entries(text)
        if len({seg["speaker"] for seg in segments if seg["speaker"]}) < 2:
            return Result([seg["speaker"] for seg in segments])
        shares = None
        mic, system = folder / "mic.wav", folder / "system.wav"
        if mic.exists() and system.exists():
            shares = {k: v[0] for k, v in selfvoice.mic_shares(mic, system, segments).items()}
        result = recheck(segments, audio_embed(work), shares)
        if result.changed:
            # 寫完才取代原稿，磁碟寫入失敗時也能沿用原本字幕。
            fd, name = tempfile.mkstemp(prefix="recheck-", suffix=".tmp", dir=srt.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(relabel_srt(text, result.speakers).encode("utf-8"))
                temporary.replace(srt)
            finally:
                temporary.unlink(missing_ok=True)
        return result
    except Exception as e:
        print(f"短句說話者重新確認失敗：{e!r}", file=sys.stderr)
        return None
