import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from transcribe import load_glossary, resolve_language, stabilize_segments, transcribe_audio


class TranscribeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "episode.wav"
        self.audio.write_bytes(b"RIFF-test")
        self.model = self.root / "model"
        self.model.mkdir()

    def state(self, backend="faster", device="cpu"):
        return {
            "status": "complete",
            "backend": backend,
            "profile": "small",
            "model_path": str(self.model),
            "device": device,
            "compute_type": "int8" if backend == "faster" else "float16",
        }

    def test_faster_contract_writes_text_and_segments(self):
        def runner(audio, model, device, compute_type, language, **_kwargs):
            self.assertEqual(self.audio, audio)
            self.assertEqual(self.model, model)
            self.assertIsNone(language)
            return "zh", [
                {"start": 0.0, "end": 1.5, "text": "第一句。"},
                {"start": 1.5, "end": 3.0, "text": "第二句。"},
            ]

        raw = self.root / "raw-transcript.txt"
        segments = self.root / "segments.json"
        transcribe_audio(
            self.audio,
            raw,
            segments,
            self.state(),
            faster_runner=runner,
        )
        self.assertEqual("第一句。\n第二句。\n", raw.read_text(encoding="utf-8"))
        payload = json.loads(segments.read_text(encoding="utf-8"))
        self.assertEqual("zh", payload["language"])
        self.assertNotIn("speaker", json.dumps(payload).lower())

    def test_mlx_contract_uses_local_model(self):
        seen = {}

        def runner(audio, model, language, **_kwargs):
            seen["model"] = model
            seen["language"] = language
            return "en", [{"start": 0.0, "end": 1.0, "text": "Hello."}]

        transcribe_audio(
            self.audio,
            self.root / "raw.txt",
            self.root / "segments.json",
            self.state("mlx"),
            mlx_runner=runner,
        )
        self.assertEqual(self.model, seen["model"])

    def test_existing_output_is_never_overwritten(self):
        raw = self.root / "raw.txt"
        raw.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            transcribe_audio(
                self.audio,
                raw,
                self.root / "segments.json",
                self.state(),
                faster_runner=lambda *args, **kwargs: ("en", []),
            )
        self.assertEqual("keep", raw.read_text(encoding="utf-8"))

    def test_incomplete_state_is_rejected(self):
        bad = self.state()
        bad["status"] = "partial"
        with self.assertRaisesRegex(RuntimeError, "complete runtime"):
            transcribe_audio(
                self.audio,
                self.root / "raw.txt",
                self.root / "segments.json",
                bad,
            )

    def test_cuda_runtime_failure_reuses_local_model_on_cpu(self):
        calls = []

        class FakeInfo:
            language = "en"

        class FakeModel:
            def __init__(
                self, model_path, *, device, compute_type, local_files_only
            ):
                calls.append(
                    (Path(model_path), device, compute_type, local_files_only)
                )
                if device == "cuda":
                    raise RuntimeError("CUDA unavailable")

            def transcribe(self, *args, **kwargs):
                return iter(()), FakeInfo()

        fake_module = type("M", (), {"WhisperModel": FakeModel})
        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            from transcribe import run_faster

            with self.assertLogs("transcribe", level="WARNING") as captured:
                language, segments = run_faster(
                    self.audio, self.model, "cuda", "float16", None
                )
        self.assertEqual("en", language)
        self.assertEqual([], segments)
        self.assertEqual(
            [
                (self.model, "cuda", "float16", True),
                (self.model, "cpu", "int8", True),
            ],
            calls,
        )
        self.assertTrue(
            any("CPU" in line and "int8" in line for line in captured.output)
        )

    def test_faster_whisper_enables_vad_filter(self):
        seen = {}

        class FakeInfo:
            language = "en"

        class FakeSegment:
            start = 0.0
            end = 1.0
            text = "Hello."

        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, *args, **kwargs):
                seen.update(kwargs)
                return iter([FakeSegment()]), FakeInfo()

        fake_module = type("M", (), {"WhisperModel": FakeModel})
        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            from transcribe import run_faster

            language, segments = run_faster(
                self.audio, self.model, "cpu", "int8", None
            )
        self.assertEqual("en", language)
        self.assertEqual("Hello.", segments[0]["text"])
        self.assertTrue(seen["vad_filter"])
        self.assertTrue(seen["word_timestamps"])
        self.assertFalse(seen["condition_on_previous_text"])
        self.assertEqual(0.3, seen["vad_parameters"]["threshold"])
        self.assertEqual(0, seen["vad_parameters"]["min_speech_duration_ms"])
        self.assertEqual(400, seen["vad_parameters"]["speech_pad_ms"])
        self.assertNotIn("initial_prompt", seen)
        self.assertNotIn("hotwords", seen)

    def test_mixed_language_prompt_and_glossary_hotwords(self):
        seen = {}

        class FakeInfo:
            language = "zh"

        class FakeSegment:
            start = 0.0
            end = 1.0
            text = "你好 GPU。"

        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, *args, **kwargs):
                seen.update(kwargs)
                return iter([FakeSegment()]), FakeInfo()

        glossary = self.root / "names.txt"
        glossary.write_text("# names\n\nGPU\n林夏\n", encoding="utf-8")
        fake_module = type("M", (), {"WhisperModel": FakeModel})
        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            from transcribe import run_faster

            mode, whisper_language = resolve_language("zh-en")
            language, segments = run_faster(
                self.audio,
                self.model,
                "cpu",
                "int8",
                whisper_language,
                initial_prompt="以下是普通话与英语夹杂的播客。中文使用中文标点，英文专名保留原文。",
                hotwords=load_glossary(glossary),
            )
        self.assertEqual(("mixed", "zh"), (mode, whisper_language))
        self.assertEqual("zh", language)
        self.assertEqual("你好 GPU。", segments[0]["text"])
        self.assertEqual("zh", seen["language"])
        self.assertIn("英文专名", seen["initial_prompt"])
        self.assertEqual("GPU, 林夏", seen["hotwords"])
        self.assertFalse(seen["condition_on_previous_text"])

    def test_overlapping_duplicates_collapse_and_later_repeats_stay(self):
        kept = stabilize_segments(
            [
                {"start": 5.0, "end": 6.0, "text": "第二句。"},
                {"start": 0.0, "end": 2.0, "text": "重复。"},
                {"start": 1.0, "end": 3.0, "text": "  重复。 "},
                {"start": 1.2, "end": 2.2, "text": "另一句。"},
                {"start": 10.0, "end": 11.0, "text": "重复。"},
                {"start": 12.0, "end": 12.5, "text": "   "},
                {"start": -1.0, "end": 0.2, "text": "开头。"},
            ]
        )
        self.assertEqual(
            ["开头。", "重复。", "另一句。", "第二句。", "重复。"],
            [item["text"] for item in kept],
        )
        self.assertEqual(0.0, kept[0]["start"])
        self.assertEqual(3.0, kept[1]["end"])
        self.assertEqual("另一句。", kept[2]["text"])

    def test_glossary_rejects_too_many_terms(self):
        path = self.root / "names.txt"
        path.write_text("\n".join(f"词{index}" for index in range(101)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "100"):
            load_glossary(path)
        with self.assertRaisesRegex(ValueError, "unsupported language"):
            resolve_language("zh-en-us")


if __name__ == "__main__":
    unittest.main()
