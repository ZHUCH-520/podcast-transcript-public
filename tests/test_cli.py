import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from doctor import MachineFacts
from podcast_transcript.captions import (
    export_segments,
    format_timestamp,
    render_markdown,
    render_srt,
    render_vtt,
)
from podcast_transcript.cli import execute_run, run, scripts_dir
from podcast_transcript.media import display_url, fetch_media
from runtime_paths import build_runtime_paths


class Response:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size >= len(self._body):
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:size], self._body[size:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class CaptionTest(unittest.TestCase):
    def test_timestamps_and_speaker_cues(self):
        self.assertEqual("01:01:01,250", format_timestamp(3661.25, ","))
        self.assertEqual("00:00:00.000", format_timestamp(-1, "."))
        payload = {
            "segments": [
                {"start": 0, "end": 1.5, "text": "Hello.", "speaker": "SPEAKER_00"},
                {"start": 1.5, "end": 1.5, "text": "Next."},
            ]
        }
        markdown = render_markdown(payload)
        self.assertIn("**SPEAKER_00**: Hello.", markdown)
        self.assertIn("Next.", markdown)
        self.assertNotIn("SPEAKER_00: Next", markdown)
        srt = render_srt(payload)
        self.assertIn("00:00:00,000 --> 00:00:01,500", srt)
        self.assertIn("SPEAKER_00: Hello.", srt)
        self.assertIn("00:00:01,501", srt)
        vtt = render_vtt(payload)
        self.assertTrue(vtt.startswith("WEBVTT\n"))
        self.assertIn("00:00:00.000 --> 00:00:01.500", vtt)

    def test_export_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "segments.json"
            source.write_text(
                json.dumps({"segments": [{"start": 0, "end": 1, "text": "Hi."}]}),
                encoding="utf-8",
            )
            destination = root / "captions.srt"
            export_segments(source, "srt", destination)
            with self.assertRaises(FileExistsError):
                export_segments(source, "srt", destination)
            self.assertIn("Hi.", destination.read_text(encoding="utf-8"))


class MediaFetchTest(unittest.TestCase):
    def test_direct_media_is_saved_and_pages_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seen = {}

            def opener(request, timeout=0):
                self.assertIsInstance(request, Request)
                self.assertGreater(timeout, 0)
                seen["url"] = request.full_url
                return Response(b"ID3audio", "audio/mpeg")

            saved = fetch_media(
                "https://example.com/shows/episode.mp3?token=secret",
                root,
                opener=opener,
            )
            self.assertEqual(b"ID3audio", saved.read_bytes())
            self.assertEqual("episode.mp3", saved.name)
            self.assertIn("token=secret", seen["url"])
            self.assertNotIn("token", display_url(seen["url"]))

            def html_opener(request, timeout=0):
                return Response(b"<html>", "text/html")

            with self.assertRaisesRegex(RuntimeError, "not a direct"):
                fetch_media("https://example.com/episode", root, opener=html_opener)
            credential = "https://token:secret@" + "example.com/episode.mp3"
            with self.assertRaisesRegex(RuntimeError, "credentials"):
                fetch_media(credential, root, opener=self.fail)


class RunCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "episode"
        self.paths = build_runtime_paths(
            "Linux",
            self.root,
            {"PODCAST_TRANSCRIPT_CACHE": str(self.root / "cache")},
        )
        self.facts = MachineFacts(
            "Linux", "x86_64", 16 * 1024**3, False, None, True, True
        )
        self.audio = self.root / "episode.wav"
        self.audio.write_bytes(b"RIFF")

    def _ready(self):
        model = self.root / "model"
        model.mkdir()
        from runtime_paths import atomic_write_json

        atomic_write_json(
            self.paths.state,
            {
                "status": "complete",
                "backend": "faster",
                "profile": "small",
                "model_path": str(model),
                "device": "cpu",
                "compute_type": "int8",
            },
        )

    def _runner(self, calls, *, transcribe_code=0, diarize_install_code=0):
        def runner(command):
            calls.append(list(command))
            if command[0] == "transcribe.py" and transcribe_code == 0:
                output = Path(command[command.index("--output-dir") + 1])
                (output / "raw-transcript.txt").write_text("Hello.\n", encoding="utf-8")
                segments = {
                    "language": "en",
                    "segments": [{"start": 0.0, "end": 1.0, "text": "Hello."}],
                }
                (output / "segments.json").write_text(
                    json.dumps(segments), encoding="utf-8"
                )
            if command[0] == "diarize.py" and command[1] == "apply":
                segments = Path(command[command.index("--segments") + 1])
                payload = json.loads(segments.read_text(encoding="utf-8"))
                payload["segments"][0]["speaker"] = "SPEAKER_00"
                segments.write_text(json.dumps(payload), encoding="utf-8")
            if command[0] == "diarize.py" and command[1] == "install":
                return diarize_install_code
            if command[0] == "transcribe.py":
                return transcribe_code
            return 0

        return runner

    def test_run_refuses_download_without_consent(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "accept-download"):
            execute_run(
                str(self.audio),
                self.output,
                paths=self.paths,
                facts=self.facts,
                runner=self._runner(calls),
            )
        self.assertEqual([], calls)
        self.assertFalse(self.output.exists())

    def test_run_installs_transcribes_and_writes_captions(self):
        calls = []
        result = execute_run(
            str(self.audio),
            self.output,
            accept_download=True,
            captions="both",
            paths=self.paths,
            facts=self.facts,
            runner=self._runner(calls),
        )
        self.assertEqual(
            ["setup.py", "install", "--profile", "small", "--accept-download"],
            calls[0],
        )
        self.assertEqual("transcribe.py", calls[1][0])
        self.assertEqual("not requested", result["diarization"])
        self.assertEqual("Hello.\n", (self.output / "raw-transcript.txt").read_text(encoding="utf-8"))
        self.assertIn("Hello.", (self.output / "transcript.md").read_text(encoding="utf-8"))
        self.assertTrue((self.output / "transcript.srt").is_file())
        self.assertTrue((self.output / "transcript.vtt").is_file())
        self.assertNotIn(str(self.output), (self.output / "transcript.md").read_text(encoding="utf-8"))

    def test_existing_runtime_skips_install_and_optional_diarization_degrades(self):
        self._ready()
        calls = []
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            result = execute_run(
                str(self.audio),
                self.output,
                diarize_requested=True,
                paths=self.paths,
                facts=self.facts,
                runner=self._runner(calls),
            )
        self.assertEqual(["transcribe.py"], [command[0] for command in calls])
        self.assertEqual("skipped", result["diarization"])
        self.assertIn("not installed", stderr.getvalue())
        self.assertIn("Hello.", (self.output / "transcript.md").read_text(encoding="utf-8"))

    def test_run_forwards_mixed_language_prompt_and_glossary(self):
        self._ready()
        calls = []
        glossary = self.root / "names.txt"
        glossary.write_text("GPU\n", encoding="utf-8")
        execute_run(
            str(self.audio),
            self.output,
            language="zh-en",
            initial_prompt="保留英文专名。",
            glossary=glossary,
            paths=self.paths,
            facts=self.facts,
            runner=self._runner(calls),
        )
        command = calls[0]
        self.assertEqual(
            [
                "transcribe.py",
                str(self.audio),
                "--output-dir",
                str(self.output),
                "--language",
                "zh-en",
                "--initial-prompt",
                "保留英文专名。",
                "--glossary",
                str(glossary),
            ],
            command,
        )

    def test_diarize_apply_labels_markdown_when_install_is_approved(self):
        calls = []
        result = execute_run(
            str(self.audio),
            self.output,
            accept_download=True,
            diarize_requested=True,
            paths=self.paths,
            facts=self.facts,
            runner=self._runner(calls),
        )
        names = [command[0:2] for command in calls]
        self.assertIn(["diarize.py", "install"], names)
        self.assertIn(["diarize.py", "apply"], names)
        self.assertEqual("applied", result["diarization"])
        markdown = (self.output / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("**SPEAKER_00**: Hello.", markdown)

    def test_diarize_install_failure_keeps_the_transcript(self):
        calls = []
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            result = execute_run(
                str(self.audio),
                self.output,
                accept_download=True,
                diarize_requested=True,
                paths=self.paths,
                facts=self.facts,
                runner=self._runner(calls, diarize_install_code=1),
            )
        self.assertEqual("skipped", result["diarization"])
        self.assertIn("install failed", stderr.getvalue())
        self.assertTrue((self.output / "raw-transcript.txt").is_file())
        self.assertFalse(any(command[1] == "apply" for command in calls if command[0] == "diarize.py"))

    def test_url_is_fetched_only_after_consent_and_existing_output_is_kept(self):
        self._ready()
        calls = []
        fetched = []

        def fetch(url, output):
            fetched.append(url)
            target = output / "episode.mp3"
            target.write_bytes(b"audio")
            return target

        execute_run(
            "https://example.com/episode.mp3",
            self.output,
            paths=self.paths,
            facts=self.facts,
            runner=self._runner(calls),
            fetch=fetch,
        )
        self.assertEqual(["https://example.com/episode.mp3"], fetched)
        self.assertEqual(str(self.output / "episode.mp3"), calls[0][1])
        with self.assertRaises(FileExistsError):
            execute_run(
                str(self.audio),
                self.output,
                paths=self.paths,
                facts=self.facts,
                runner=self._runner([]),
            )
        self.assertEqual(
            "Hello.\n", (self.output / "raw-transcript.txt").read_text(encoding="utf-8")
        )

    def test_transcription_failure_does_not_write_markdown(self):
        self._ready()
        with self.assertRaises(subprocess.CalledProcessError):
            execute_run(
                str(self.audio),
                self.output,
                paths=self.paths,
                facts=self.facts,
                runner=self._runner([], transcribe_code=1),
            )
        self.assertFalse((self.output / "transcript.md").exists())

    def test_output_inside_the_skill_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "outside the skill"):
            execute_run(
                str(self.audio),
                ROOT / "podcast-transcript-public" / "job",
                paths=self.paths,
                facts=self.facts,
                runner=self._runner([]),
            )

    def test_cli_exports_and_splits_without_downloading(self):
        segments = self.root / "segments.json"
        segments.write_text(
            json.dumps({"segments": [{"start": 0, "end": 1, "text": "Hi."}]}),
            encoding="utf-8",
        )
        stdout = StringIO()
        with patch("sys.stdout", stdout):
            code = run(
                ["export", str(segments), "--format", "vtt", "--output", str(self.root / "out.vtt")]
            )
        self.assertEqual(0, code)
        self.assertTrue((self.root / "out.vtt").read_text(encoding="utf-8").startswith("WEBVTT"))
        source = self.root / "raw.txt"
        source.write_text("Hello.\n\nNext.\n", encoding="utf-8")
        chunks = self.root / "chunks"
        split_code = run(
            ["split", str(source), "--output-dir", str(chunks), "--limit", "8"]
        )
        self.assertEqual(0, split_code)
        self.assertTrue((chunks / "chunk-000.txt").is_file())

    def test_package_metadata_points_at_the_existing_scripts(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('podcast-transcript = "podcast_transcript.cli:main"', pyproject)
        self.assertIn('requires-python = ">=3.9"', pyproject)
        self.assertIn("podcast-transcript-public/scripts", pyproject)
        self.assertTrue((scripts_dir() / "doctor.py").is_file())
        self.assertTrue((scripts_dir() / "diarize.py").is_file())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("pipx install", readme)
        self.assertIn("podcast-transcript run", readme)
        self.assertIn("Claude Code", readme)
        self.assertIn("Cursor", readme)
        for page in (ROOT / "docs" / "index.html", ROOT / "docs" / "zh.html"):
            self.assertIn("podcast-transcript", page.read_text(encoding="utf-8"))


class ReadOnlyCommandTest(unittest.TestCase):
    def test_doctor_json_stays_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["PODCAST_TRANSCRIPT_CACHE"] = str(Path(tmp) / "cache")
            stdout = StringIO()
            with patch.dict(os.environ, env, clear=True), patch("sys.stdout", stdout):
                code = run(["doctor", "--json"])
            self.assertEqual(0, code)
            payload = json.loads(stdout.getvalue())
            self.assertIn("backend", payload)
            self.assertFalse((Path(tmp) / "cache").exists())


if __name__ == "__main__":
    unittest.main()
