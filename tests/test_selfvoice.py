"""用合成的兩軌錄音測試「我」的判斷：.venv\\Scripts\\python -m unittest discover tests"""
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from huiji import selfvoice

RATE = 48000


def voice(seconds: float, freq: float, seed: int) -> np.ndarray:
    """像講話的聲音：音節般起伏的諧波"""
    t = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * freq * k * t) / k for k in (1, 2, 3))
    syllables = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t + seed)
    return 0.25 * tone * syllables


def write(path: Path, mono: np.ndarray, channels: int = 2) -> None:
    pcm = (np.clip(mono, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.repeat(pcm, channels).tobytes())


# (開始, 結束, 說話者)；SPEAKER_01 是使用者，從麥克風進來
TURNS = [(0, 4, "SPEAKER_00"), (4, 8, "SPEAKER_01"), (8, 12, "SPEAKER_02"),
         (12, 16, "SPEAKER_01"), (16, 20, "SPEAKER_00"), (20, 24, "SPEAKER_01")]
FREQ = {"SPEAKER_00": 140, "SPEAKER_01": 180, "SPEAKER_02": 220}


def make_meeting(folder: Path, echo: float, mic_gain: float = 1.0) -> list[dict]:
    rng = np.random.default_rng(0)
    total = int(24 * RATE)
    mic, system = np.zeros(total), np.zeros(total)
    for i, (a, b, spk) in enumerate(TURNS):
        sig = voice(b - a, FREQ[spk], i)
        if spk == "SPEAKER_01":
            mic[a * RATE:b * RATE] += sig * mic_gain
        else:
            system[a * RATE:b * RATE] += sig
    mic += echo * system                       # 用喇叭時，麥克風收到別人的聲音
    mic += rng.normal(0, 0.002, total)         # 環境底噪
    write(folder / "mic.wav", mic, channels=1)
    write(folder / "system.wav", system, channels=2)
    return [{"start": a, "end": b, "speaker": spk, "text": "…"} for a, b, spk in TURNS]


class DetectSelf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_headphones(self):
        segments = make_meeting(self.folder, echo=0.0)
        self.assertEqual(selfvoice.detect_self(self.folder, segments), "SPEAKER_01")

    def test_speakers_with_echo_and_quiet_mic(self):
        segments = make_meeting(self.folder, echo=0.3, mic_gain=0.3)
        self.assertEqual(selfvoice.detect_self(self.folder, segments), "SPEAKER_01")

    def test_shares(self):
        segments = make_meeting(self.folder, echo=0.3)
        shares = selfvoice.mic_shares(self.folder / "mic.wav", self.folder / "system.wav", segments)
        self.assertGreater(shares["SPEAKER_01"][0], 0.9)
        self.assertLess(shares["SPEAKER_00"][0], 0.1)
        self.assertLess(shares["SPEAKER_02"][0], 0.1)

    def test_ambiguous(self):
        """每個說話者都一半麥克風、一半電腦聲音（分講者分錯）：判斷不出來就不命名"""
        segments = make_meeting(self.folder, echo=0.3)
        for i, seg in enumerate(segments):
            seg["speaker"] = f"SPEAKER_0{i // 2}"
        self.assertIsNone(selfvoice.detect_self(self.folder, segments))

    def test_two_mic_speakers(self):
        """兩個人都從麥克風進來（例如兩人同在一間）：分不出哪個是使用者，不命名"""
        segments = make_meeting(self.folder, echo=0.0)
        segments[5]["speaker"] = "SPEAKER_03"
        self.assertIsNone(selfvoice.detect_self(self.folder, segments))

    def test_single_track_skipped(self):
        segments = make_meeting(self.folder, echo=0.0)
        (self.folder / "system.wav").unlink()
        self.assertIsNone(selfvoice.detect_self(self.folder, segments))


if __name__ == "__main__":
    unittest.main()
