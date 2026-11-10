import hashlib
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from huiji import recheck, transcribe
from huiji.config import Settings


def seg(start, end, speaker="SPEAKER_00"):
    return {"start": start, "end": end, "speaker": speaker}


class RecheckTests(unittest.TestCase):
    def setUp(self):
        self.voices = {"SPEAKER_00": np.array([1., 0.]), "SPEAKER_01": np.array([0., 1.])}

    def test_changes_to_more_similar_speaker(self):
        result = recheck.refine([seg(0, 2)], self.voices, lambda a, b: [0, 1])
        self.assertEqual((result.speakers, result.checked, result.changed), (["SPEAKER_01"], 1, 1))

    def test_keeps_when_margin_is_too_small(self):
        result = recheck.refine([seg(0, 2)], self.voices, lambda a, b: [0.6, 0.8])
        self.assertEqual(result.speakers, ["SPEAKER_00"])

    def test_margin_boundary(self):
        voices = {"SPEAKER_00": np.array([0., 1.]), "SPEAKER_01": np.array([0.25, np.sqrt(0.9375)])}
        self.assertEqual(recheck.refine([seg(0, 1)], voices, lambda a, b: [1, 0]).changed, 1)

    def test_only_checks_half_to_four_seconds(self):
        embed = Mock(return_value=[0, 1])
        result = recheck.refine([seg(0, 0.499), seg(1, 1.5), seg(2, 6), seg(7, 11.001)], self.voices, embed)
        self.assertEqual(result.checked, 2)
        self.assertEqual(embed.call_args_list, [unittest.mock.call(1, 1.5), unittest.mock.call(2, 6)])

    def test_one_voice_does_not_embed(self):
        embed = Mock()
        result = recheck.refine([seg(0, 2)], {"SPEAKER_00": self.voices["SPEAKER_00"]}, embed)
        self.assertEqual((result.checked, result.changed), (0, 0))
        embed.assert_not_called()

    def test_ties_choose_lower_number(self):
        voices = {"SPEAKER_10": np.array([0., 1.]), "SPEAKER_2": np.array([0., 1.]),
                  "SPEAKER_00": np.array([1., 0.])}
        self.assertEqual(recheck.refine([seg(0, 1)], voices, lambda a, b: [0, 1]).speakers, ["SPEAKER_2"])

    def test_does_not_cross_confirmed_track_sides(self):
        for a, b in [(0.6, 0.3), (0.3, 0.6)]:
            result = recheck.refine([seg(0, 2)], self.voices, lambda a, b: [0, 1],
                                    {"SPEAKER_00": a, "SPEAKER_01": b})
            self.assertEqual(result.changed, 0)

    def test_unknown_track_side_allows_change(self):
        result = recheck.refine([seg(0, 2)], self.voices, lambda a, b: [0, 1],
                                {"SPEAKER_00": 0.6, "SPEAKER_01": 0.4})
        self.assertEqual(result.changed, 1)

    def test_invalid_embeddings_keep_original(self):
        with patch('sys.stderr', new_callable=io.StringIO):
            for vector in [None, [0, 0], [np.nan, 1], [np.inf, 1]]:
                self.assertEqual(recheck.refine([seg(0, 2)], self.voices, lambda a, b: vector).changed, 0)
        with patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertEqual(recheck.refine([seg(0, 2)], self.voices, Mock(side_effect=RuntimeError('測試'))).changed, 0)
            self.assertIn('計算失敗', err.getvalue())

    def test_voices_weight_longest_segments_up_to_sixty_seconds(self):
        embed = Mock(side_effect=[[1, 0], [0, 1]])
        voices = recheck.build_voices([seg(0, 10), seg(10, 50), seg(50, 80)], embed)
        self.assertEqual(embed.call_args_list, [unittest.mock.call(10, 50), unittest.mock.call(50, 70)])
        np.testing.assert_allclose(voices['SPEAKER_00'], np.array([2., 1.]) / np.sqrt(5))

    def test_failed_reference_is_skipped(self):
        with patch('sys.stderr', new_callable=io.StringIO):
            voices = recheck.build_voices([seg(0, 20), seg(20, 30)], Mock(side_effect=[None, [1, 0]]))
        np.testing.assert_allclose(voices['SPEAKER_00'], [1, 0])

    def test_recheck_builds_then_refines_without_changing_input(self):
        segments = [seg(0, 20), seg(20, 40, 'SPEAKER_01'), seg(40, 41)]
        embed = lambda a, b: [1, 0] if a == 0 else [0, 1]
        result = recheck.recheck(segments, embed)
        self.assertEqual(result.speakers, ['SPEAKER_00', 'SPEAKER_01', 'SPEAKER_01'])
        self.assertEqual(segments[-1]['speaker'], 'SPEAKER_00')


SRT = ('\ufeff1\r\n00:00:00,001 --> 00:00:02,003\r\n[SPEAKER_00]:  原文  \r\n'
       '內文的 [SPEAKER_00]: 不改\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\n'
       '[SPEAKER_01]: Hello\r\n\r\n3\r\n00:00:05,000 --> 00:00:06,000\r\n無標籤\r\n')


class SrtTests(unittest.TestCase):
    def test_only_rewrites_selected_block_tag(self):
        expected = SRT.replace('[SPEAKER_00]:  原文', '[SPEAKER_01]:  原文')
        self.assertEqual(recheck.relabel_srt(SRT, ['SPEAKER_01', 'SPEAKER_01', None]), expected)
        segments, _ = recheck._srt_entries(SRT)
        self.assertEqual(segments[0]['start'], 0.001)

    def test_file_keeps_bytes_and_reads_track_shares(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            srt = folder / 'work.srt'
            srt.write_bytes(SRT.encode('utf-8'))
            for name in ['mic.wav', 'system.wav']:
                (folder / name).touch()
            with patch.object(recheck, 'audio_embed', return_value=Mock()), \
                    patch('huiji.selfvoice.mic_shares', return_value={'SPEAKER_00': (0.7, 10)}) as shares, \
                    patch.object(recheck, 'recheck', return_value=recheck.Result(['SPEAKER_01', 'SPEAKER_01', None], 2, 1)) as refine:
                result = recheck.recheck_speakers(folder, srt, folder / 'work.wav')
            shares.assert_called_once()
            self.assertEqual(refine.call_args.args[2], {'SPEAKER_00': 0.7})
            self.assertEqual(result.changed, 1)
            self.assertEqual(srt.read_bytes(), SRT.replace('[SPEAKER_00]:  原文', '[SPEAKER_01]:  原文').encode('utf-8'))

    def test_model_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'work.srt'
            path.write_bytes(SRT.encode('utf-8'))
            with patch.object(recheck, 'audio_embed', side_effect=ImportError('測試')), \
                    patch('sys.stderr', new_callable=io.StringIO) as err:
                self.assertIsNone(recheck.recheck_speakers(Path(d), path, Path(d) / 'work.wav'))
            self.assertIn('重新確認失敗', err.getvalue())
            self.assertEqual(path.read_bytes(), SRT.encode('utf-8'))

    def test_replace_failure_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'work.srt'
            path.write_bytes(SRT.encode('utf-8'))
            with patch.object(recheck, 'audio_embed', return_value=Mock()), \
                    patch.object(recheck, 'recheck', return_value=recheck.Result(['SPEAKER_01', 'SPEAKER_01', None], 2, 1)), \
                    patch.object(Path, 'replace', side_effect=OSError('無法取代')), \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertIsNone(recheck.recheck_speakers(Path(d), path, Path(d) / 'work.wav'))
            self.assertEqual(path.read_bytes(), SRT.encode('utf-8'))
            self.assertEqual(list(Path(d).iterdir()), [path])


class AudioTests(unittest.TestCase):
    def test_reads_float32_and_slices_by_time(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'work.wav'
            samples = np.zeros(32000, dtype='<i2')
            samples[8000:16000] = -32768
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(samples.tobytes())
            extractor = Mock()
            extractor.compute.return_value = [1, 0]
            sherpa = Mock()
            sherpa.SpeakerEmbeddingExtractor.return_value = extractor
            with patch.dict('sys.modules', {'sherpa_onnx': sherpa}), \
                    patch.object(recheck, 'ensure_model', return_value=Path('model.onnx')):
                embed = recheck.audio_embed(path)
                self.assertEqual(embed(0.5, 1), [1, 0])
            stream = extractor.create_stream.return_value
            args = stream.accept_waveform.call_args.kwargs
            self.assertEqual(args['sample_rate'], 16000)
            self.assertEqual(args['waveform'].dtype, np.float32)
            np.testing.assert_array_equal(args['waveform'], np.full(8000, -1, dtype=np.float32))
            stream.input_finished.assert_called_once()


class DownloadTests(unittest.TestCase):
    def test_failed_downloads_remove_temporary_file(self):
        for data, size, digest in [(b'x', 2, hashlib.sha256(b'x').hexdigest()),
                                   (b'xx', 2, hashlib.sha256(b'yy').hexdigest()),
                                   (b'xxx', 2, hashlib.sha256(b'xx').hexdigest())]:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as d, \
                    patch.object(recheck.config, 'APP_DIR', Path(d)), \
                    patch.object(recheck, 'MODEL_SIZE', size), patch.object(recheck, 'MODEL_SHA256', digest), \
                    patch('urllib.request.urlopen', return_value=io.BytesIO(data)):
                with self.assertRaises(ValueError):
                    recheck.ensure_model()
                self.assertEqual(list((Path(d) / 'models').iterdir()), [])

    def test_network_error_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as d, patch.object(recheck.config, 'APP_DIR', Path(d)), \
                patch('urllib.request.urlopen', side_effect=OSError('離線')):
            with self.assertRaises(OSError):
                recheck.ensure_model()
            self.assertEqual(list((Path(d) / 'models').iterdir()), [])

    def test_verified_download_is_reused(self):
        with tempfile.TemporaryDirectory() as d, patch.object(recheck.config, 'APP_DIR', Path(d)), \
                patch.object(recheck, 'MODEL_SIZE', 2), \
                patch.object(recheck, 'MODEL_SHA256', hashlib.sha256(b'xx').hexdigest()), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(b'xx')) as download:
            path = recheck.ensure_model()
            self.assertEqual(path.read_bytes(), b'xx')
            self.assertEqual(recheck.ensure_model(), path)
            download.assert_called_once()
            self.assertEqual(list(path.parent.iterdir()), [path])


class TranscribeTests(unittest.TestCase):
    def _run(self, diarize=True, failure=False):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            work, exe = folder / 'work.wav', folder / 'whisper.exe'
            work.touch()
            exe.touch()
            (folder / 'whisper').mkdir()
            (folder / 'whisper' / 'work.srt').write_bytes(SRT.encode('utf-8'))
            proc = Mock(stdout=iter([]), returncode=1 if failure else 0)
            calls = []

            def confirm(*args):
                self.assertTrue(work.exists())
                calls.append('確認')

            with patch.object(transcribe, 'prepare_audio', return_value=work), \
                    patch.object(transcribe.subprocess, 'Popen', return_value=proc), \
                    patch.object(transcribe, 'merge_speakers', side_effect=lambda *args: calls.append('合併')), \
                    patch.object(recheck, 'recheck_speakers', side_effect=confirm) as check, \
                    patch.object(transcribe, 'segments_from_srt', return_value=[]) as parsed:
                progress = Mock()
                if failure:
                    with self.assertRaises(transcribe.TranscribeError):
                        transcribe.transcribe(Settings(whisper_exe=str(exe), diarize=diarize), folder, 6, progress)
                else:
                    transcribe.transcribe(Settings(whisper_exe=str(exe), diarize=diarize), folder, 6, progress)
                    parsed.assert_called_once()
                self.assertFalse(work.exists())
                if diarize and not failure:
                    self.assertEqual(calls, ['合併', '確認'])
                    progress.assert_called_with(-2)
                else:
                    check.assert_not_called()

    def test_recheck_runs_before_audio_cleanup(self):
        self._run()

    def test_diarize_disabled_skips_recheck(self):
        self._run(diarize=False)

    def test_failed_transcription_cleans_audio(self):
        self._run(failure=True)


if __name__ == '__main__':
    unittest.main()
