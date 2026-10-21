import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import diarize
from doctor import MachineFacts
from model_catalog import (
    DIARIZATION_COMPONENTS,
    DIARIZATION_PACKAGES,
    GATED_DIARIZATION_REPOS,
)
from runtime_paths import build_runtime_paths


PIPELINE_CONFIG = """version: 3.1.0

pipeline:
  name: pyannote.audio.pipelines.SpeakerDiarization
  params:
    clustering: AgglomerativeClustering
    embedding: pyannote/wespeaker-voxceleb-resnet34-LM
    embedding_batch_size: 32
    embedding_exclude_overlap: true
    segmentation: pyannote/segmentation-3.0
    segmentation_batch_size: 32

params:
  clustering:
    method: centroid
    min_cluster_size: 12
    threshold: 0.7045654963945799
  segmentation:
    min_duration_off: 0.0
"""


class DiarizeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = build_runtime_paths(
            "Linux",
            self.root,
            {"PODCAST_TRANSCRIPT_CACHE": str(self.root / "runtime")},
        )
        self.linux_cpu = MachineFacts(
            "Linux", "x86_64", 16 * 1024**3, False, None, True, True
        )
        self.token = "test-token-value"

    def test_plan_is_read_only_and_pins_pyannote(self):
        plan = diarize.create_diarization_plan(self.paths, self.linux_cpu)
        self.assertEqual("pyannote", plan["backend"])
        self.assertEqual("cpu", plan["device"])
        self.assertTrue(plan["hf_token_required"])
        self.assertEqual("HF_TOKEN", plan["token_env"])
        self.assertEqual(list(GATED_DIARIZATION_REPOS), plan["gated_repos"])
        self.assertEqual(list(DIARIZATION_PACKAGES), plan["packages"])
        self.assertIn("pyannote.audio==3.3.2", plan["packages"])
        self.assertEqual(
            sum(component.download_bytes for component in DIARIZATION_COMPONENTS),
            plan["download_bytes"],
        )
        for component in DIARIZATION_COMPONENTS:
            self.assertRegex(component.revision, r"^[0-9a-f]{40}$")
            self.assertIn("pyannote", component.directory_name)
        self.assertFalse((self.root / "runtime").exists())

    def test_device_choice_covers_mac_cpu_and_cuda(self):
        mac = MachineFacts(
            "Darwin", "arm64", 16 * 1024**3, True, 8 * 1024**3, True, True
        )
        linux_gpu = MachineFacts(
            "Linux", "x86_64", 32 * 1024**3, True, 12 * 1024**3, False, True
        )
        self.assertEqual("cpu", diarize.select_diarization_device(mac)[0])
        self.assertEqual("cuda", diarize.select_diarization_device(linux_gpu)[0])
        self.assertEqual("cpu", diarize.select_diarization_device(self.linux_cpu)[0])
        with self.assertLogs("diarize", level="WARNING") as captured:
            self.assertEqual(
                "cpu",
                diarize.resolve_runtime_device("cuda", cuda_available=False),
            )
        self.assertTrue(any("CPU" in line for line in captured.output))
        self.assertEqual(
            "cuda", diarize.resolve_runtime_device("cuda", cuda_available=True)
        )

    def test_install_requires_approval_and_token(self):
        with self.assertRaisesRegex(RuntimeError, "accept-download"):
            diarize.install_diarization(
                self.paths,
                self.linux_cpu,
                accepted=False,
                environ={"HF_TOKEN": self.token},
                run_command=self.fail,
                download_snapshot=self.fail,
            )
        with self.assertRaisesRegex(RuntimeError, "HF_TOKEN"):
            diarize.install_diarization(
                self.paths,
                self.linux_cpu,
                accepted=True,
                environ={},
                run_command=self.fail,
                download_snapshot=self.fail,
            )
        self.assertFalse(self.paths.diarization_state.exists())

    def _fake_command(self, commands):
        def fake_command(argv, **kwargs):
            commands.append([str(part) for part in argv])
            if argv[1:3] == ["-m", "venv"]:
                bindir = self.paths.diarization_venv / "bin"
                bindir.mkdir(parents=True)
                (bindir / "python").write_text("", encoding="utf-8")
            if "_validate" in argv:
                return subprocess.CompletedProcess(argv, 0)
            return subprocess.CompletedProcess(argv, 0)

        return fake_command

    def _fake_download(self):
        def fake_download(*, repo_id, revision, local_dir, token):
            self.assertEqual(self.token, token)
            target = Path(local_dir)
            target.mkdir(parents=True, exist_ok=True)
            component = next(
                item for item in DIARIZATION_COMPONENTS if item.repo_id == repo_id
            )
            self.assertEqual(component.revision, revision)
            for name in component.required_files:
                path = target / name
                if name == "config.yaml" and component.role == "pipeline":
                    path.write_text(PIPELINE_CONFIG, encoding="utf-8")
                else:
                    path.write_bytes(b"model")
            return str(target)

        return fake_download

    def test_install_writes_local_config_without_storing_token(self):
        commands = []
        state = diarize.install_diarization(
            self.paths,
            self.linux_cpu,
            accepted=True,
            environ={"HF_TOKEN": self.token},
            run_command=self._fake_command(commands),
            download_snapshot=self._fake_download(),
        )
        encoded = json.dumps(state)
        self.assertNotIn(self.token, encoded)
        self.assertNotIn(self.token, " ".join(" ".join(argv) for argv in commands))
        self.assertEqual("cpu", state["device"])
        self.assertEqual("complete", state["status"])
        config = (
            self.paths.models
            / "diarization"
            / "pyannote-speaker-diarization-3.1"
            / "config.yaml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("pyannote/segmentation-3.0", config)
        self.assertNotIn("pyannote/wespeaker-voxceleb-resnet34-LM", config)
        self.assertNotIn(".partial/", config)
        self.assertIn("pyannote-segmentation-3.0/pytorch_model.bin", config)
        self.assertIn("pyannote-wespeaker-voxceleb-resnet34-LM/pytorch_model.bin", config)
        self.assertTrue(self.paths.diarization_state.is_file())

    def test_validation_failure_does_not_mark_installed(self):
        def fake_command(argv, **kwargs):
            if argv[1:3] == ["-m", "venv"]:
                bindir = self.paths.diarization_venv / "bin"
                bindir.mkdir(parents=True)
                (bindir / "python").write_text("", encoding="utf-8")
            if "_validate" in argv:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0)

        with self.assertRaises(subprocess.CalledProcessError):
            diarize.install_diarization(
                self.paths,
                self.linux_cpu,
                accepted=True,
                environ={"HF_TOKEN": self.token},
                run_command=fake_command,
                download_snapshot=self._fake_download(),
            )
        self.assertFalse(self.paths.diarization_state.exists())
        self.assertTrue(
            (
                self.paths.models
                / "diarization"
                / "pyannote-speaker-diarization-3.1.partial"
            ).is_dir()
        )

    def _segments(self, speakers=False):
        audio = self.root / "episode.wav"
        audio.write_bytes(b"RIFF")
        segments = self.root / "segments.json"
        payload = {
            "language": "en",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "Hello."},
                {"start": 1.2, "end": 2.0, "text": "Welcome."},
            ],
        }
        if speakers:
            payload["segments"][0]["speaker"] = "SPEAKER_00"
        segments.write_text(json.dumps(payload), encoding="utf-8")
        return audio, segments

    def _install_marker(self):
        model = self.paths.models / "diarization" / "model"
        model.mkdir(parents=True)
        diarize.atomic_write_json(
            self.paths.diarization_state,
            {
                "status": "complete",
                "backend": "pyannote",
                "model_path": str(model),
                "device": "cpu",
            },
        )

    def test_missing_runtime_leaves_segments_unchanged(self):
        audio, segments = self._segments(speakers=True)
        before = segments.read_text(encoding="utf-8")
        result = diarize.apply_diarization(audio, segments, self.paths)
        self.assertEqual("unavailable", result["status"])
        self.assertEqual([], result["speaker_ids"])
        self.assertEqual(before, segments.read_text(encoding="utf-8"))
        env = dict(os.environ)
        env["PODCAST_TRANSCRIPT_CACHE"] = str(self.root / "empty-cache")
        with patch.dict(os.environ, env, clear=True):
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = diarize.main(
                    ["apply", str(audio), "--segments", str(segments)]
                )
        self.assertEqual(0, code)
        self.assertIn("unavailable", stdout.getvalue())
        self.assertEqual(before, segments.read_text(encoding="utf-8"))

    def test_import_error_degrades_without_writing(self):
        audio, segments = self._segments()
        before = segments.read_text(encoding="utf-8")
        self._install_marker()

        def missing():
            raise ImportError("pyannote is not installed")

        result = diarize.apply_diarization(
            audio, segments, self.paths, runner=missing
        )
        self.assertEqual("unavailable", result["status"])
        self.assertEqual(before, segments.read_text(encoding="utf-8"))

    def test_apply_writes_speaker_labels_from_overlap(self):
        audio, segments = self._segments()
        self._install_marker()

        def runner():
            return [
                {"start": 0.0, "end": 1.1, "speaker": "SPEAKER_00"},
                {"start": 0.9, "end": 2.2, "speaker": "SPEAKER_01"},
            ]

        result = diarize.apply_diarization(
            audio, segments, self.paths, runner=runner
        )
        payload = json.loads(segments.read_text(encoding="utf-8"))
        self.assertEqual(["SPEAKER_00", "SPEAKER_01"], result["speaker_ids"])
        self.assertEqual("SPEAKER_00", payload["segments"][0]["speaker"])
        self.assertEqual("SPEAKER_01", payload["segments"][1]["speaker"])
        self.assertEqual("Hello.", payload["segments"][0]["text"])
        self.assertEqual("en", payload["language"])
        with self.assertRaises(FileExistsError):
            diarize.apply_diarization(audio, segments, self.paths, runner=runner)
        self.assertEqual("Hello.", json.loads(segments.read_text(encoding="utf-8"))["segments"][0]["text"])

    def test_assign_speakers_breaks_ties_by_earlier_turn(self):
        segments = [{"start": 0.0, "end": 2.0, "text": "Both."}]
        turns = [
            {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
            {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        ]
        labeled = diarize.assign_speakers(segments, turns)
        self.assertEqual("SPEAKER_00", labeled[0]["speaker"])
        unlabeled = diarize.assign_speakers(
            [{"start": 5.0, "end": 6.0, "text": "Silence."}],
            turns,
        )
        self.assertNotIn("speaker", unlabeled[0])


if __name__ == "__main__":
    unittest.main()
