import tempfile
import unittest
from pathlib import Path

import numpy as np

from huiji import speakers, transcribe


def vec(*xs):
    v = np.array(xs, dtype=float)
    return v / np.linalg.norm(v)


def seg(start, end, spk):
    return {"start": start, "end": end, "speaker": spk, "text": "x"}


class MergeMapTests(unittest.TestCase):
    def test_merges_similar_and_renumbers(self):
        segs = [seg(0, 30, "SPEAKER_03"), seg(30, 60, "SPEAKER_01"), seg(60, 90, "SPEAKER_05")]
        emb = {"SPEAKER_03": vec(1, 0, 0), "SPEAKER_01": vec(0, 1, 0), "SPEAKER_05": vec(0.9, 0.1, 0)}
        self.assertEqual(speakers.merge_map(segs, emb),
                         {"SPEAKER_03": "SPEAKER_00", "SPEAKER_01": "SPEAKER_01", "SPEAKER_05": "SPEAKER_00"})

    def test_keeps_distinct_people(self):
        segs = [seg(0, 30, "SPEAKER_00"), seg(30, 60, "SPEAKER_01")]
        emb = {"SPEAKER_00": vec(1, 0), "SPEAKER_01": vec(0.55, 0.835)}  # 相似度 0.55
        self.assertEqual(len(set(speakers.merge_map(segs, emb).values())), 2)

    def test_minor_speaker_uses_lower_threshold(self):
        segs = [seg(0, 60, "SPEAKER_00"), seg(60, 63, "SPEAKER_01")]
        emb = {"SPEAKER_00": vec(1, 0), "SPEAKER_01": vec(0.5, 0.866)}  # 相似度 0.5，只講 3 秒
        self.assertEqual(set(speakers.merge_map(segs, emb).values()), {"SPEAKER_00"})

    def test_never_merges_mic_with_system_speaker(self):
        segs = [seg(0, 30, "SPEAKER_00"), seg(30, 60, "SPEAKER_01")]
        emb = {"SPEAKER_00": vec(1, 0), "SPEAKER_01": vec(0.95, 0.31)}
        mapping = speakers.merge_map(segs, emb, {"SPEAKER_00": 0.95, "SPEAKER_01": 0.02})
        self.assertEqual(len(set(mapping.values())), 2)

    def test_missing_embedding_left_alone(self):
        segs = [seg(0, 30, "SPEAKER_00"), seg(30, 31, "SPEAKER_01")]
        mapping = speakers.merge_map(segs, {"SPEAKER_00": vec(1, 0)})
        self.assertEqual(mapping, {"SPEAKER_00": "SPEAKER_00", "SPEAKER_01": "SPEAKER_01"})


class MergeSrtTests(unittest.TestCase):
    def test_rewrites_work_srt(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            out = folder / "whisper"
            out.mkdir()
            srt = out / "work.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:20,000\n[SPEAKER_02]: 你好\n\n"
                           "2\n00:00:20,000 --> 00:00:40,000\n[SPEAKER_00]: 大家好\n\n"
                           "3\n00:00:40,000 --> 00:00:50,000\n[SPEAKER_01]: 再見\n", encoding="utf-8")
            for name, v in (("SPEAKER_00", vec(1, 0)), ("SPEAKER_01", vec(0, 1)), ("SPEAKER_02", vec(0.95, 0.31))):
                np.savetxt(folder / f"work.wav_diarize_pyannote_v3.1_{name}.txt", v)
            transcribe.merge_speakers(folder, srt)
            got = [s["speaker"] for s in transcribe.parse_srt(srt.read_text(encoding="utf-8"))]
            self.assertEqual(got, ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01"])
            self.assertEqual(len(list(out.glob("work*_SPEAKER_*.txt"))), 3)
            self.assertEqual(list(folder.glob("*.txt")), [])


if __name__ == "__main__":
    unittest.main()
