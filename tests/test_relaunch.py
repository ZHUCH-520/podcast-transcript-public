import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

from doctor import MachineFacts
from podcast_transcript.cli import execute_run
from runtime_paths import build_runtime_paths, diarization_python, runtime_python


STUB = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["RELAUNCH_MARKER"]).write_text(json.dumps(sys.argv), encoding="utf-8")
args = sys.argv[1:]
if "--output-dir" in args:
    output = Path(args[args.index("--output-dir") + 1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw-transcript.txt").write_text("你好\n", encoding="utf-8")
    (output / "segments.json").write_text(
        json.dumps(
            {
                "language": "zh",
                "segments": [{"start": 0.0, "end": 1.0, "text": "你好"}],
            }
        ),
        encoding="utf-8",
    )
"""


def _install_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STUB, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class RuntimeRelaunchTest(unittest.TestCase):
    def test_cli_run_reexecs_transcribe_inside_the_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            output = root / "episode"
            audio = root / "episode.wav"
            audio.write_bytes(b"RIFF")
            marker = root / "launched.json"
            paths = build_runtime_paths(environ={"PODCAST_TRANSCRIPT_CACHE": str(cache)})
            _install_stub(runtime_python(paths))
            paths.state.parent.mkdir(parents=True, exist_ok=True)
            paths.state.write_text(
                json.dumps({"status": "complete", "backend": "faster"}),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PODCAST_TRANSCRIPT_CACHE"] = str(cache)
            env["RELAUNCH_MARKER"] = str(marker)
            with patch.dict(os.environ, env, clear=True):
                result = execute_run(
                    str(audio),
                    output,
                    language="zh",
                    captions="srt",
                    paths=paths,
                    facts=MachineFacts(
                        "Linux", "x86_64", 16 * 1024**3, False, None, True, True
                    ),
                )
            argv = json.loads(marker.read_text(encoding="utf-8"))
            self.assertIn("--inside-runtime", argv)
            self.assertEqual("zh", argv[argv.index("--language") + 1])
            self.assertTrue(any(item.endswith("transcribe.py") for item in argv))
            self.assertEqual("not requested", result["diarization"])
            self.assertIn("transcript.srt", result["files"])
            self.assertIn("你好", (output / "transcript.srt").read_text(encoding="utf-8"))

    def test_diarize_apply_reexecs_inside_its_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            marker = root / "launched.json"
            paths = build_runtime_paths(environ={"PODCAST_TRANSCRIPT_CACHE": str(cache)})
            _install_stub(diarization_python(paths))
            model = root / "pipeline"
            model.mkdir()
            paths.diarization_state.parent.mkdir(parents=True, exist_ok=True)
            paths.diarization_state.write_text(
                json.dumps({"status": "complete", "model_path": str(model)}),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PODCAST_TRANSCRIPT_CACHE"] = str(cache)
            env["RELAUNCH_MARKER"] = str(marker)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "diarize.py"),
                    "apply",
                    str(root / "episode.wav"),
                    "--segments",
                    str(root / "segments.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertNotIn("NameError", completed.stderr)
            argv = json.loads(marker.read_text(encoding="utf-8"))
            self.assertIn("apply", argv)
            self.assertIn("--inside-runtime", argv)
            self.assertTrue(any(item.endswith("diarize.py") for item in argv))


if __name__ == "__main__":
    unittest.main()
