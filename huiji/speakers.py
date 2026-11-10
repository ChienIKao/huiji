"""說話者分群後處理：pyannote 常把同一個人切成好幾個說話者，用每個說話者的聲紋向量把同一人併回來

Faster-Whisper-XXL 加上 --return_embeddings 會替每個說話者輸出一個 256 維的聲紋向量。
- 兩個說話者的聲紋相似度（cosine）夠高就視為同一人，從最像的一對開始併
- 講話很少的說話者（零碎片段）門檻放低，併到最像的人
- 分軌錄音時，麥克風主導（我）和電腦聲音主導（別人）的說話者不會被併在一起
最後依第一次出現的順序重新編號 SPEAKER_00、SPEAKER_01…
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

MERGE_SIMILARITY = 0.6   # 聲紋相似度高於這個就當同一人
MINOR_SECONDS = 8.0      # 總講話時間少於這個算零碎的說話者
MINOR_SIMILARITY = 0.45  # 零碎的說話者用比較低的門檻併到最像的人
MIC_SELF = 0.6           # 麥克風主導比例高於這個是「我」
MIC_OTHER = 0.3          # 低於這個是別人

EMBEDDING_FILE = re.compile(r"_(SPEAKER_\d+)\.txt$")
SRT_SPEAKER = re.compile(r"\[(SPEAKER_\d+)\]:")


def load_embeddings(folder: Path, pattern: str = "*_SPEAKER_*.txt") -> dict[str, np.ndarray]:
    out = {}
    for f in folder.glob(pattern):
        m = EMBEDDING_FILE.search(f.name)
        if not m:
            continue
        try:
            v = np.loadtxt(f, dtype=np.float64).ravel()
        except ValueError:
            continue
        norm = float(np.linalg.norm(v))
        if v.size and norm > 0:
            out[m.group(1)] = v / norm
    return out


def _side(share: float | None) -> int:
    """1 = 我（麥克風），-1 = 別人（電腦聲音），0 = 不確定"""
    if share is None:
        return 0
    return 1 if share >= MIC_SELF else -1 if share <= MIC_OTHER else 0


def merge_map(segments: list[dict], embeddings: dict[str, np.ndarray],
              mic_shares: dict[str, float] | None = None) -> dict[str, str]:
    """回傳 {原本的說話者: 合併後重新編號的說話者}"""
    order: list[str] = []
    seconds: dict[str, float] = {}
    for seg in segments:
        spk = seg.get("speaker")
        if not spk:
            continue
        if spk not in seconds:
            order.append(spk)
            seconds[spk] = 0.0
        seconds[spk] += max(0.0, seg["end"] - seg["start"])

    # 每一群：成員、聲紋（依講話時間加權平均）、總秒數、我／別人
    groups = {spk: {"members": [spk], "vec": embeddings.get(spk), "secs": seconds[spk],
                    "side": _side((mic_shares or {}).get(spk))} for spk in order}

    def similarity(a: dict, b: dict) -> float:
        if a["vec"] is None or b["vec"] is None or a["side"] * b["side"] < 0:
            return -1.0
        return float(a["vec"] @ b["vec"])

    while True:
        best = None
        keys = list(groups)
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                a, b = groups[ka], groups[kb]
                sim = similarity(a, b)
                need = MINOR_SIMILARITY if min(a["secs"], b["secs"]) < MINOR_SECONDS else MERGE_SIMILARITY
                if sim >= need and (best is None or sim > best[0]):
                    best = (sim, ka, kb)
        if not best:
            break
        _, ka, kb = best
        a, b = groups[ka], groups.pop(kb)
        vec = a["vec"] * a["secs"] + b["vec"] * b["secs"]
        a["vec"] = vec / (np.linalg.norm(vec) or 1.0)
        a["members"] += b["members"]
        a["secs"] += b["secs"]
        a["side"] = a["side"] or b["side"]

    owner = {m: k for k, g in groups.items() for m in g["members"]}
    renumber: dict[str, str] = {}
    for spk in order:
        renumber.setdefault(owner[spk], f"SPEAKER_{len(renumber):02d}")
    return {spk: renumber[owner[spk]] for spk in order}


def relabel_srt(text: str, mapping: dict[str, str]) -> str:
    return SRT_SPEAKER.sub(lambda m: f"[{mapping.get(m.group(1), m.group(1))}]:", text)
