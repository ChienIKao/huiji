"""轉錄：ffmpeg 混音 → Faster-Whisper-XXL（含 pyannote 分講者）→ OpenCC 轉繁體"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .config import Settings

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma", ".caf", ".aiff",
              ".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv"}


class TranscribeError(RuntimeError):
    pass


def _run(args: list[str], what: str) -> subprocess.CompletedProcess:
    r = subprocess.run(args, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace")[-600:]
        raise TranscribeError(f"{what}失敗（結束代碼 {r.returncode}）：{tail}")
    return r


def probe_duration(ffmpeg: str, path: Path) -> float:
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, creationflags=NO_WINDOW)
    m = re.search(rb"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        return 0.0
    h, mi, sec = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(sec)


def ensure_audio(s: Settings, folder: Path) -> Path:
    """產生播放用的 audio.m4a（麥克風與系統音混音，或從匯入的檔案取出聲音）"""
    audio = folder / "audio.m4a"
    if audio.exists():
        return audio
    tracks = [folder / n for n in ("mic.wav", "system.wav") if (folder / n).exists() and (folder / n).stat().st_size > 44]
    # 匯入的檔案；mkv 之類轉成 mp4 之後，原本的 source 會刪掉，改從 recording.mp4 取聲音
    imported = next(folder.glob("source.*"), None) or (
        folder / "recording.mp4" if (folder / "recording.mp4").exists() else None)
    if tracks:
        inputs = sum((["-i", str(t)] for t in tracks), [])
        if len(tracks) == 2:
            mix = ["-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[a]", "-map", "[a]"]
        else:
            mix = []
        src = inputs + mix
    elif imported:
        src = ["-i", str(imported), "-vn"]
    else:
        raise TranscribeError("找不到音檔")
    _run([s.ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", *src,
          "-ac", "1", "-c:a", "aac", "-b:a", "96k", str(audio)], "產生音檔")
    return audio


def prepare_audio(s: Settings, folder: Path) -> Path:
    """回傳轉錄用的 16 kHz 單聲道 work.wav"""
    work = folder / "work.wav"
    audio = ensure_audio(s, folder)
    _run([s.ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio),
          "-ac", "1", "-ar", "16000", str(work)], "轉換音檔")
    return work


SRT_BLOCK = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.*?)(?:\n\s*\n|\Z)", re.S)
SPEAKER = re.compile(r"^\[(SPEAKER_\d+)\]:\s*")
PROGRESS = re.compile(r"^\[(?:(\d+):)?(\d+):(\d+\.\d+) --> (?:(\d+):)?(\d+):(\d+\.\d+)\]")


def parse_srt(text: str) -> list[dict]:
    segments = []
    for m in SRT_BLOCK.finditer(text):
        g = m.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
        body = " ".join(line.strip() for line in g[8].strip().splitlines()).strip()
        speaker = None
        sm = SPEAKER.match(body)
        if sm:
            speaker = sm.group(1)
            body = body[sm.end():].strip()
        if body:
            segments.append({"start": round(start, 2), "end": round(end, 2), "speaker": speaker, "text": body})
    return segments


CJK = r"[㐀-鿿豈-﫿　-〿＀-￯]"
HALF_TO_FULL = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；"}


def fix_punctuation(text: str) -> str:
    """Whisper 在中文句子裡常用半形標點：緊鄰中文字的改成全形"""
    return re.sub(rf"(?<={CJK})\s*([,?!:;])\s*|\s*([,?!:;])\s*(?={CJK})",
                  lambda m: HALF_TO_FULL[m.group(1) or m.group(2)], text)


def merge_segments(segments: list[dict], max_gap: float = 2.0, max_chars: int = 160) -> list[dict]:
    """同一個人連續講的短句併成一段，逐字稿比較好讀"""
    merged: list[dict] = []
    for seg in segments:
        last = merged[-1] if merged else None
        if (last and last["speaker"] == seg["speaker"] and seg["start"] - last["end"] <= max_gap
                and len(last["text"]) + len(seg["text"]) <= max_chars):
            sep = " " if last["text"][-1:].isascii() and seg["text"][:1].isascii() else ""
            last["text"] += sep + seg["text"]
            last["end"] = seg["end"]
        else:
            merged.append(dict(seg))
    return merged


def to_traditional(s: Settings, texts: list[str]) -> list[str]:
    if not texts or not Path(s.opencc_exe).exists():
        return texts
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "in.txt", Path(d) / "out.txt"
        src.write_text("\n".join(t.replace("\n", " ") for t in texts), encoding="utf-8")
        _run([s.opencc_exe, "-c", s.opencc_config, "-i", str(src), "-o", str(dst)], "簡轉繁")
        out = dst.read_text(encoding="utf-8").split("\n")
    return out if len(out) == len(texts) else texts


def transcribe(s: Settings, folder: Path, duration: float, on_progress=None, glossary: list[str] | None = None,
               on_process=None) -> list[dict]:
    exe = Path(s.whisper_exe)
    if not exe.exists():
        raise TranscribeError(f"找不到 Faster-Whisper-XXL：{exe}")
    work = prepare_audio(s, folder)
    out_dir = folder / "whisper"
    out_dir.mkdir(exist_ok=True)
    args = [str(exe), str(work), "-m", s.whisper_model, "-o", str(out_dir), "-f", "srt",
            "--vad_filter", "true", "--condition_on_previous_text", "false", "--beep_off"]
    if s.whisper_language:
        args += ["-l", s.whisper_language]
    if s.multilingual:
        args += ["--multilingual", "true"]
    if glossary:
        args += ["--initial_prompt", "、".join(glossary)]
    if s.diarize:
        args += ["--diarize", "pyannote_v3.1"]
        if s.min_speakers:
            args += ["--min_speakers", str(s.min_speakers)]
        if s.max_speakers:
            args += ["--max_speakers", str(s.max_speakers)]

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            creationflags=NO_WINDOW, cwd=str(exe.parent))
    if on_process:
        on_process(proc)
    tail: list[str] = []
    assert proc.stdout
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        tail = (tail + [line])[-15:]
        if on_progress:
            m = PROGRESS.match(line)
            if m and duration > 0:
                end = int(m.group(4) or 0) * 3600 + int(m.group(5)) * 60 + float(m.group(6))
                on_progress(min(0.99, end / duration))
            elif "Diariz" in line:
                on_progress(-1)
    proc.wait()
    work.unlink(missing_ok=True)
    srt = out_dir / "work.srt"
    if proc.returncode != 0 or not srt.exists():
        raise TranscribeError(f"轉錄失敗（結束代碼 {proc.returncode}）：\n" + "\n".join(tail))

    segments = merge_segments(parse_srt(srt.read_text(encoding="utf-8")))
    converted = to_traditional(s, [seg["text"] for seg in segments])
    for seg, text in zip(segments, converted):
        seg["text"] = fix_punctuation(text)
    return segments


def fmt_time(seconds: float) -> str:
    t = max(0, int(seconds))
    if t >= 3600:
        return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
    return f"{t // 60:02d}:{t % 60:02d}"


def srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+")
CLAUSE_END = re.compile(r"(?<=[，、,：:])\s*")


def split_for_subtitles(segments: list[dict], max_chars: int = 36) -> list[dict]:
    """把合併過的長段落切回字幕長度：先依句號切，太長再依逗號切，時間依字數比例分配"""
    out = []
    for seg in segments:
        pieces = []
        for sentence in filter(None, SENTENCE_END.split(seg["text"])):
            if len(sentence) <= max_chars:
                pieces.append(sentence)
                continue
            buf = ""
            for clause in filter(None, CLAUSE_END.split(sentence)):
                if buf and len(buf) + len(clause) > max_chars:
                    pieces.append(buf)
                    buf = ""
                buf += clause
            if buf:
                pieces.append(buf)
        total = sum(len(p) for p in pieces) or 1
        t = seg["start"]
        span = seg["end"] - seg["start"]
        for p in pieces:
            end = t + span * len(p) / total
            out.append({"start": round(t, 2), "end": round(end, 2), "speaker": seg.get("speaker"), "text": p.strip()})
            t = end
    return out


def subtitle_segments(s: Settings, folder: Path, fallback: list[dict], edited: bool = False) -> list[dict]:
    """字幕用的短句：Whisper 原本的分句（逐字稿為了好讀會把同一人的句子合併，太長不適合當字幕）。
    使用者改過逐字稿時，以改過的內容為準，再切成字幕長度"""
    srt = folder / "whisper" / "work.srt"
    if edited or not srt.exists():
        return split_for_subtitles(fallback)
    segments = parse_srt(srt.read_text(encoding="utf-8"))
    for seg, text in zip(segments, to_traditional(s, [x["text"] for x in segments])):
        seg["text"] = fix_punctuation(text)
    return segments


def to_vtt(segments: list[dict], speakers: dict) -> str:
    """影片播放時的字幕（WebVTT）：說話者換人時才加名字，看起來比較清爽"""
    from .notes import speaker_name

    blocks = ["WEBVTT", ""]
    last = None
    for seg in segments:
        name = speaker_name(seg.get("speaker"), speakers)
        text = f"{name}：{seg['text']}" if name and seg.get("speaker") != last else seg["text"]
        last = seg.get("speaker")
        start = srt_time(seg["start"]).replace(",", ".")
        end = srt_time(max(seg["end"], seg["start"] + 0.5)).replace(",", ".")
        blocks += [f"{start} --> {end}", text, ""]
    return "\n".join(blocks)


def to_srt(segments: list[dict], speakers: dict, with_speaker: bool = True) -> str:
    from .notes import speaker_name

    blocks = []
    for i, seg in enumerate(segments, 1):
        name = speaker_name(seg.get("speaker"), speakers) if with_speaker else ""
        text = f"{name}：{seg['text']}" if name else seg["text"]
        blocks.append(f"{i}\n{srt_time(seg['start'])} --> {srt_time(seg['end'])}\n{text}\n")
    return "\n".join(blocks)
