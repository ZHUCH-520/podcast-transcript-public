import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from podcast_transcript.cli import run
from podcast_transcript.skill_install import describe_targets, install_skill


class SkillInstallTest(unittest.TestCase):
    def test_verified_copy_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = install_skill("qoder", scope="user", dest=root)
            skill = (destination / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("name: podcast-transcript-public", skill)
            self.assertTrue((destination / "scripts" / "transcribe.py").is_file())
            self.assertTrue((destination / "scripts" / "resolve_media.py").is_file())
            with self.assertRaises(FileExistsError):
                install_skill("qoder", scope="user", dest=root)
            self.assertIn("name: podcast-transcript-public", skill)

    def test_user_and_project_directories_match_the_verified_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_skill("trae-cn", scope="user", home=root)
            install_skill("qwen", scope="project", project=root)
            self.assertTrue(
                (
                    root
                    / ".trae-cn"
                    / "skills"
                    / "podcast-transcript-public"
                    / "SKILL.md"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / ".qwen"
                    / "skills"
                    / "podcast-transcript-public"
                    / "scripts"
                    / "doctor.py"
                ).is_file()
            )

    def test_unverified_targets_are_not_guessed(self):
        described = describe_targets()
        names = {item["name"] for item in described["not_installed_automatically"]}
        self.assertEqual({"qcoder", "CatPaw", "WorkBuddy"}, names)
        verified = {item["target"] for item in described["verified"]}
        self.assertIn("lingma", verified)
        self.assertIn("kimi", verified)
        self.assertIn("codebuddy", verified)
        self.assertNotIn("catpaw", verified)
        self.assertNotIn("workbuddy", verified)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "qoder"):
                install_skill("qcoder", dest=root)
            with self.assertRaisesRegex(ValueError, "directory"):
                install_skill("workbuddy")
            prepared = install_skill("workbuddy", dest=root / "upload")
            self.assertTrue((prepared / "SKILL.md").is_file())
            self.assertFalse((root / ".workbuddy").exists())
            with self.assertRaisesRegex(ValueError, "lingma"):
                install_skill("qoder-cn", scope="user", home=root)
            install_skill("qoder-cn", scope="project", project=root)
            self.assertTrue(
                (root / ".qoder" / "skills" / "podcast-transcript-public" / "SKILL.md").is_file()
            )
            self.assertFalse((root / ".qoder-cn").exists())

    def test_cli_list_and_project_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                code = run(["install-skill", "--list"])
            self.assertEqual(0, code)
            payload = json.loads(stdout.getvalue())
            self.assertIn("CatPaw", json.dumps(payload, ensure_ascii=False))
            stdout = StringIO()
            with patch("sys.stdout", stdout):
                installed = run(
                    [
                        "install-skill",
                        "--target",
                        "cursor",
                        "--scope",
                        "project",
                        "--dest",
                        str(root),
                    ]
                )
            self.assertEqual(0, installed)
            self.assertTrue((root / "podcast-transcript-public" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
