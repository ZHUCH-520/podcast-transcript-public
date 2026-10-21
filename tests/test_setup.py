import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import setup as runtime_setup
from doctor import MachineFacts
from runtime_paths import build_runtime_paths


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        self.paths = build_runtime_paths(
            "Darwin",
            Path(self.temp.name),
            {"PODCAST_TRANSCRIPT_CACHE": str(self.root)},
        )
        self.apple = MachineFacts(
            "Darwin", "arm64", 16 * 1024**3, False, None, True, True
        )

    def test_plan_is_read_only(self):
        plan = runtime_setup.create_plan(self.paths, self.apple, "turbo", "mlx")
        self.assertEqual("turbo", plan["profile"])
        self.assertEqual("mlx", plan["backend"])
        self.assertFalse(self.root.exists())

    def test_install_requires_acknowledgement(self):
        with self.assertRaisesRegex(RuntimeError, "accept-download"):
            runtime_setup.install_runtime(
                self.paths,
                self.apple,
                profile="small",
                backend="mlx",
                accepted=False,
                run_command=Mock(),
                download_snapshot=Mock(),
                find_program=lambda name: "/usr/bin/ffmpeg",
            )

    def test_successful_install_writes_state_last(self):
        commands = Mock()

        def fake_command(argv, **kwargs):
            if argv[1:4] == ["-m", "venv", str(self.paths.venv)]:
                (self.paths.venv / "bin").mkdir(parents=True)
                (self.paths.venv / "bin" / "python").write_text(
                    "", encoding="utf-8"
                )
            return Mock(returncode=0)

        commands.side_effect = fake_command

        def fake_download(**kwargs):
            target = Path(kwargs["local_dir"])
            target.mkdir(parents=True)
            (target / "weights.npz").write_bytes(b"model")
            (target / "config.json").write_text("{}", encoding="utf-8")
            return str(target)

        state = runtime_setup.install_runtime(
            self.paths,
            self.apple,
            profile="small",
            backend="mlx",
            accepted=True,
            run_command=commands,
            download_snapshot=fake_download,
            find_program=lambda name: "/usr/bin/ffmpeg",
        )
        self.assertTrue(self.paths.state.is_file())
        self.assertEqual(
            state, json.loads(self.paths.state.read_text(encoding="utf-8"))
        )
        self.assertEqual("complete", state["status"])
        self.assertEqual("cpu", state["device"])
        self.assertEqual("float16", state["compute_type"])
        self.assertTrue(Path(state["model_path"]).is_dir())

    def test_failed_download_never_marks_installed(self):
        def fail_download(**kwargs):
            raise OSError("network interrupted")

        with self.assertRaisesRegex(OSError, "network interrupted"):
            runtime_setup.install_runtime(
                self.paths,
                self.apple,
                profile="small",
                backend="mlx",
                accepted=True,
                run_command=Mock(return_value=Mock(returncode=0)),
                download_snapshot=fail_download,
                find_program=lambda name: "/usr/bin/ffmpeg",
            )
        self.assertFalse(self.paths.state.exists())

    def test_backend_load_failure_preserves_partial_without_state(self):
        def fake_command(argv, **kwargs):
            if argv[1:4] == ["-m", "venv", str(self.paths.venv)]:
                (self.paths.venv / "bin").mkdir(parents=True)
                (self.paths.venv / "bin" / "python").write_text(
                    "", encoding="utf-8"
                )
            if "_validate" in argv:
                raise subprocess.CalledProcessError(1, argv)
            return Mock(returncode=0)

        def fake_download(**kwargs):
            target = Path(kwargs["local_dir"])
            target.mkdir(parents=True)
            (target / "weights.npz").write_bytes(b"model")
            (target / "config.json").write_text("{}", encoding="utf-8")
            return str(target)

        with self.assertRaises(subprocess.CalledProcessError):
            runtime_setup.install_runtime(
                self.paths,
                self.apple,
                profile="small",
                backend="mlx",
                accepted=True,
                run_command=fake_command,
                download_snapshot=fake_download,
                find_program=lambda name: "/usr/bin/ffmpeg",
            )
        self.assertTrue((self.paths.models / "mlx" / "small.partial").is_dir())
        self.assertFalse((self.paths.models / "mlx" / "small").exists())
        self.assertFalse(self.paths.state.exists())

    def _install_faster(self, facts: MachineFacts):
        def fake_command(argv, **kwargs):
            if argv[1:4] == ["-m", "venv", str(self.paths.venv)]:
                (self.paths.venv / "bin").mkdir(parents=True)
                (self.paths.venv / "bin" / "python").write_text(
                    "", encoding="utf-8"
                )
            return Mock(returncode=0)

        def fake_download(**kwargs):
            target = Path(kwargs["local_dir"])
            target.mkdir(parents=True)
            for name in (
                "config.json",
                "model.bin",
                "tokenizer.json",
                "vocabulary.txt",
            ):
                (target / name).write_bytes(b"model")
            return str(target)

        return runtime_setup.install_runtime(
            self.paths,
            facts,
            profile="small",
            backend="faster",
            accepted=True,
            run_command=fake_command,
            download_snapshot=fake_download,
            find_program=lambda name: None,
        )

    def test_faster_install_records_cuda_float16_when_available(self):
        linux_gpu = MachineFacts(
            "Linux", "x86_64", 32 * 1024**3, True, 12 * 1024**3, False, True
        )
        state = self._install_faster(linux_gpu)
        self.assertEqual("cuda", state["device"])
        self.assertEqual("float16", state["compute_type"])

    def test_faster_install_records_cpu_int8_without_cuda(self):
        linux_cpu = MachineFacts(
            "Linux", "x86_64", 32 * 1024**3, False, None, False, True
        )
        state = self._install_faster(linux_cpu)
        self.assertEqual("cpu", state["device"])
        self.assertEqual("int8", state["compute_type"])


if __name__ == "__main__":
    unittest.main()
