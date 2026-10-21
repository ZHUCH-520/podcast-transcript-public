import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from split_transcript import (
    speakers_for_chunks,
    split_file,
    split_text,
    validate_speaker_names,
)


class SplitTranscriptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_mixed_language_text_is_preserved_character_for_character(self):
        text = (
            "Speaker A: Hello.\r\n\r\n"
            "说话人 B：第一句。第二句！\n\n"
            "Speaker A: Yeah?  好。\n"
        ) * 8
        chunks = split_text(text, limit=96)
        self.assertEqual(text, "".join(chunks))
        self.assertTrue(all(0 < len(chunk) <= 96 for chunk in chunks))

    def test_long_text_without_boundaries_uses_a_hard_cut(self):
        text = "字" * 10_001
        chunks = split_text(text, limit=4_500)
        self.assertEqual([4_500, 4_500, 1_001], [len(chunk) for chunk in chunks])
        self.assertEqual(text, "".join(chunks))

    def test_short_interjections_and_whitespace_are_not_stripped(self):
        text = "嗯。\n\nYeah.\n\n  好。\n"
        chunks = split_text(text, limit=9)
        self.assertEqual(text, "".join(chunks))
        self.assertIn("  ", "".join(chunks))

    def test_manifest_uses_stable_names_hashes_and_no_absolute_source_path(self):
        source = self.root / "private" / "raw-transcript.txt"
        source.parent.mkdir()
        source.write_text("第一段。\n\nSecond paragraph.\n", encoding="utf-8")
        output = self.root / "chunks"
        manifest = split_file(source, output, limit=12)
        files = [item["file"] for item in manifest["chunks"]]
        self.assertEqual(
            [f"chunk-{index:03d}.txt" for index in range(len(files))],
            files,
        )
        restored = "".join(
            (output / name).read_text(encoding="utf-8") for name in files
        )
        original = source.read_text(encoding="utf-8")
        self.assertEqual(original, restored)
        self.assertEqual(
            hashlib.sha256(original.encode("utf-8")).hexdigest(),
            manifest["input_sha256"],
        )
        encoded = json.dumps(manifest, ensure_ascii=False)
        self.assertEqual(source.name, manifest["source_name"])
        self.assertNotIn(str(source.parent), encoded)

    def test_empty_input_creates_only_a_zero_chunk_manifest(self):
        source = self.root / "raw.txt"
        source.write_text("", encoding="utf-8")
        output = self.root / "chunks"
        manifest = split_file(source, output)
        self.assertEqual([], manifest["chunks"])
        self.assertEqual([], list(output.glob("chunk-*.txt")))
        self.assertTrue((output / "chunks-manifest.json").is_file())

    def test_file_roundtrip_preserves_crlf_bytes(self):
        source = self.root / "raw.txt"
        original = "line one\r\nline two。\r\n".encode("utf-8")
        source.write_bytes(original)
        output = self.root / "chunks"
        manifest = split_file(source, output, limit=10)
        restored = b"".join(
            (output / item["file"]).read_bytes() for item in manifest["chunks"]
        )
        self.assertEqual(original, restored)

    def test_nonempty_output_is_refused_without_change(self):
        source = self.root / "raw.txt"
        source.write_text("new", encoding="utf-8")
        output = self.root / "chunks"
        output.mkdir()
        old = output / "chunk-000.txt"
        old.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "nonempty"):
            split_file(source, output)
        self.assertEqual("keep", old.read_text(encoding="utf-8"))

    def test_replace_moves_old_output_to_a_backup(self):
        source = self.root / "raw.txt"
        source.write_text("new transcript", encoding="utf-8")
        output = self.root / "chunks"
        output.mkdir()
        (output / "old.txt").write_text("old transcript", encoding="utf-8")
        split_file(source, output, replace=True)
        backups = list(self.root.glob("chunks.backup-*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(
            "old transcript",
            (backups[0] / "old.txt").read_text(encoding="utf-8"),
        )

    def test_segments_add_speaker_ids_without_changing_text(self):
        text = "Hello.\nNext thought.\n"
        source = self.root / "raw-transcript.txt"
        source.write_text(text, encoding="utf-8")
        segments = self.root / "segments.json"
        segments.write_text(
            json.dumps(
                {
                    "language": "en",
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "Hello.",
                            "speaker": "SPEAKER_00",
                        },
                        {
                            "start": 1.0,
                            "end": 2.0,
                            "text": "Next thought.",
                            "speaker": "SPEAKER_01",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        manifest = split_file(source, self.root / "chunks", limit=8, segments_path=segments)
        restored = "".join(
            (self.root / "chunks" / item["file"]).read_text(encoding="utf-8")
            for item in manifest["chunks"]
        )
        self.assertEqual(text, restored)
        self.assertEqual(["SPEAKER_00", "SPEAKER_01"], manifest["speaker_ids"])
        self.assertEqual("SPEAKER_00", manifest["chunks"][0]["speakers"][0])
        self.assertIn("SPEAKER_01", manifest["chunks"][-1]["speakers"])

    def test_speaker_overlap_follows_segment_spans(self):
        text = "Hello.\nNext.\n"
        segments = [
            {"start": 0, "end": 1, "text": "Hello.", "speaker": "SPEAKER_00"},
            {"start": 1, "end": 2, "text": "Next.", "speaker": "SPEAKER_01"},
        ]
        grouped = speakers_for_chunks(text, ["Hello.\nN", "ext.\n"], segments)
        self.assertEqual([["SPEAKER_00", "SPEAKER_01"], ["SPEAKER_01"]], grouped)

    def test_mismatched_segments_are_rejected(self):
        source = self.root / "raw.txt"
        source.write_text("Hello.\n", encoding="utf-8")
        segments = self.root / "segments.json"
        segments.write_text(
            json.dumps({"segments": [{"text": "Different."}]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            split_file(source, self.root / "chunks", segments_path=segments)
        self.assertFalse((self.root / "chunks").exists())

    def test_speaker_name_map_requires_one_label_per_id(self):
        mapping = validate_speaker_names(
            ["SPEAKER_00", "SPEAKER_01"],
            {"SPEAKER_00": " Host ", "SPEAKER_01": "Speaker B"},
        )
        self.assertEqual(
            {"SPEAKER_00": "Host", "SPEAKER_01": "Speaker B"},
            mapping,
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_speaker_names(["SPEAKER_00"], {})
        with self.assertRaisesRegex(ValueError, "empty"):
            validate_speaker_names(["SPEAKER_00"], {"SPEAKER_00": "  "})


if __name__ == "__main__":
    unittest.main()
