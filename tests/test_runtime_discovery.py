import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "podcast-transcript-public" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from doctor import MachineFacts, build_report, recommend_profile
from model_catalog import MODEL_PROFILES, package_requirements
from runtime_paths import build_runtime_paths


class RuntimeDiscoveryTest(unittest.TestCase):
    def test_macos_cache_is_generic(self):
        paths = build_runtime_paths(
            system="Darwin",
            home=Path("/private/example-home"),
            environ={},
        )
        self.assertEqual(
            Path("/private/example-home/Library/Caches/podcast-transcript-public"),
            paths.root,
        )

    def test_windows_cache_uses_local_app_data(self):
        paths = build_runtime_paths(
            system="Windows",
            home=Path("C:/ignored"),
            environ={"LOCALAPPDATA": "C:/LocalData"},
        )
        self.assertEqual(
            Path("C:/LocalData/podcast-transcript-public"),
            paths.root,
        )

    def test_backend_selection_and_recommendations(self):
        apple = MachineFacts(
            "Darwin", "arm64", 16 * 1024**3, False, None, True, True
        )
        windows_cpu = MachineFacts(
            "Windows", "AMD64", 32 * 1024**3, False, None, False, True
        )
        windows_gpu = MachineFacts(
            "Windows", "AMD64", 32 * 1024**3, True, 10 * 1024**3, False, True
        )
        linux_cpu = MachineFacts(
            "Linux", "x86_64", 32 * 1024**3, False, None, True, True
        )
        linux_gpu = MachineFacts(
            "Linux", "x86_64", 16 * 1024**3, True, 8 * 1024**3, True, True
        )
        linux_small_gpu = MachineFacts(
            "Linux", "x86_64", 64 * 1024**3, True, 4 * 1024**3, True, True
        )
        self.assertEqual(("mlx", "turbo"), recommend_profile(apple))
        self.assertEqual(("faster", "small"), recommend_profile(windows_cpu))
        self.assertEqual(("faster", "turbo"), recommend_profile(windows_gpu))
        self.assertEqual(("faster", "small"), recommend_profile(linux_cpu))
        self.assertEqual(("faster", "turbo"), recommend_profile(linux_gpu))
        self.assertEqual(("faster", "small"), recommend_profile(linux_small_gpu))

    def test_other_platforms_remain_unsupported(self):
        freebsd = MachineFacts(
            "FreeBSD", "amd64", None, False, None, False, True
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported platform"):
            recommend_profile(freebsd)

    def test_linux_cache_uses_xdg_or_home_cache(self):
        home = Path("/var/example-home")
        paths = build_runtime_paths(system="Linux", home=home, environ={})
        self.assertEqual(home / ".cache" / "podcast-transcript-public", paths.root)
        override = build_runtime_paths(
            system="Linux",
            home=home,
            environ={"XDG_CACHE_HOME": "/var/xdg-cache"},
        )
        self.assertEqual(
            Path("/var/xdg-cache/podcast-transcript-public"), override.root
        )

    def test_linux_meminfo_parser_and_reader(self):
        from doctor import _linux_memory_bytes, _memory_bytes_from_meminfo

        self.assertEqual(
            16 * 1024**3,
            _memory_bytes_from_meminfo("MemTotal:       16777216 kB\n"),
        )
        self.assertIsNone(_memory_bytes_from_meminfo("MemFree: 1 kB\n"))
        self.assertIsNone(_memory_bytes_from_meminfo("MemTotal: nope kB\n"))
        value = _linux_memory_bytes()
        if Path("/proc/meminfo").is_file():
            self.assertIsInstance(value, int)
            self.assertGreater(value, 0)
        else:
            self.assertIsNone(value)

    def test_catalog_is_pinned_and_complete(self):
        self.assertEqual({"small", "turbo", "large-v3"}, set(MODEL_PROFILES))
        for profile in MODEL_PROFILES.values():
            for model in (profile.mlx, profile.faster):
                self.assertRegex(model.revision, r"^[0-9a-f]{40}$")
                self.assertGreater(model.download_bytes, 100_000_000)
                self.assertIn("config.json", model.required_files)
        self.assertIn("mlx-whisper==0.4.3", package_requirements("mlx"))
        faster = package_requirements("faster")
        self.assertIn("faster-whisper==1.2.1", faster)
        self.assertIn("av>=11,<19", faster)
        self.assertNotIn("av>=11,<19", package_requirements("mlx"))

    def test_doctor_report_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "never-create"
            paths = build_runtime_paths(
                "Darwin", Path(tmp), {"PODCAST_TRANSCRIPT_CACHE": str(root)}
            )
            report = build_report(
                paths,
                MachineFacts(
                    "Darwin", "arm64", 8 * 1024**3, False, None, True, True
                ),
            )
            json.dumps(report)
            self.assertTrue(report["system_dependencies"]["ffmpeg"])
            self.assertEqual("not installed", report["diarization"]["status"])
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
