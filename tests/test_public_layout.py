import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "podcast-transcript-public"
PAGES = (ROOT / "docs" / "index.html", ROOT / "docs" / "zh.html")
RUNTIME_SCRIPTS = {
    "doctor.py",
    "setup.py",
    "transcribe.py",
    "split_transcript.py",
    "diarize.py",
}
REPOSITORY_URL = "https://github.com/ZHUCH-520/podcast-transcript-public"
SKILL_URL = REPOSITORY_URL + "/tree/main/podcast-transcript-public"


class LinkAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


class AssetAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.remote_assets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "img", "source", "video", "audio"}:
            value = values.get("src")
            if value and value.startswith(("http://", "https://", "//")):
                self.remote_assets.append(value)
        if tag == "link" and "stylesheet" in values.get("rel", ""):
            value = values.get("href")
            if value and value.startswith(("http://", "https://", "//")):
                self.remote_assets.append(value)


class PublicLayoutTest(unittest.TestCase):
    def test_repository_and_skill_are_separate(self):
        expected = [
            ROOT / "README.md",
            ROOT / "docs" / "index.html",
            ROOT / "docs" / "zh.html",
            SKILL / "SKILL.md",
            SKILL / "agents" / "openai.yaml",
            SKILL / "scripts" / "__init__.py",
        ]
        self.assertEqual([], [str(path) for path in expected if not path.is_file()])

    def test_relative_links_resolve(self):
        for html in PAGES:
            with self.subTest(page=html.name):
                self.assertTrue(html.is_file(), str(html))
                audit = LinkAudit()
                audit.feed(html.read_text(encoding="utf-8"))
                local = [
                    href
                    for href in audit.links
                    if not href.startswith(("#", "http:", "https:"))
                ]
                missing = [
                    (html.parent / href.split("#", 1)[0]).resolve()
                    for href in local
                ]
                self.assertEqual(
                    [], [str(path) for path in missing if not path.exists()]
                )

    def test_english_is_default_and_pages_link_to_each_other(self):
        english = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        chinese_path = ROOT / "docs" / "zh.html"
        self.assertTrue(chinese_path.is_file(), str(chinese_path))
        chinese = chinese_path.read_text(encoding="utf-8")
        self.assertIn('<html lang="en">', english)
        self.assertIn('<html lang="zh-CN">', chinese)
        self.assertIn('href="./zh.html"', english)
        self.assertIn('href="./index.html"', chinese)
        for page in (english, chinese):
            self.assertIn('hreflang="zh-Hans"', page)
            self.assertIn('hreflang="en"', page)
            self.assertIn('hreflang="x-default"', page)
        self.assertIn('aria-current="page">EN</a>', english)
        self.assertIn('aria-current="page">中文</a>', chinese)

    def test_readme_is_english_and_links_both_introductions(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "Turn podcast audio, video, episode links, or rough transcripts",
            readme,
        )
        self.assertIn("[English introduction](./docs/index.html)", readme)
        self.assertIn("[中文介绍](./docs/zh.html)", readme)
        self.assertIn(
            "Install this skill for me: "
            "https://github.com/ZHUCH-520/podcast-transcript-public/"
            "tree/main/podcast-transcript-public",
            readme,
        )
        self.assertIn("Ask your assistant", readme)
        self.assertLess(
            readme.index("### Ask your assistant"),
            readme.index("### Command line"),
        )

    def test_skill_frontmatter_uses_public_name(self):
        skill_file = SKILL / "SKILL.md"
        self.assertTrue(skill_file.is_file(), str(skill_file))
        text = skill_file.read_text(encoding="utf-8")
        self.assertIn("name: podcast-transcript-public", text)

    def test_runtime_scripts_ship_inside_the_skill(self):
        scripts = SKILL / "scripts"
        missing = sorted(
            name for name in RUNTIME_SCRIPTS if not (scripts / name).is_file()
        )
        self.assertEqual([], missing)

    def test_user_docs_explain_first_use_and_all_model_choices(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "docs" / "zh.html").read_text(encoding="utf-8")
        english = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        for text in (readme, chinese, english):
            for token in (
                "Apple Silicon",
                "Intel Mac",
                "Windows",
                "small",
                "turbo",
                "large-v3",
            ):
                self.assertIn(token, text)
        for text in (chinese,):
            self.assertRegex(text, r"粗转录|粗稿")
            self.assertRegex(text, r"不用下载模型|无需下载模型|跳过.*模型")
        for text in (readme, english):
            self.assertRegex(text, re.compile(r"rough transcript", re.IGNORECASE))
            self.assertRegex(
                text,
                re.compile(
                    r"skip(?:s|ped)?.{0,80}(?:Whisper|model download)",
                    re.IGNORECASE | re.DOTALL,
                ),
            )

    def test_install_docs_use_the_published_repository_url(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pages = [path.read_text(encoding="utf-8") for path in PAGES]
        for text in (readme, *pages):
            self.assertIn(REPOSITORY_URL, text)
            self.assertNotIn("<GitHub 仓库地址>", text)

    def test_page_repository_links_work_from_github_pages(self):
        english = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        chinese = (ROOT / "docs" / "zh.html").read_text(encoding="utf-8")
        for page in (english, chinese):
            self.assertIn(f'href="{SKILL_URL}"', page)
            self.assertIn(f'href="{REPOSITORY_URL}"', page)
            self.assertNotIn(
                'href="../podcast-transcript-public/SKILL.md"', page
            )
            self.assertNotIn('href="../README.md"', page)
            self.assertNotIn('href="../README.zh.md"', page)
            self.assertNotIn("podcast-trancript-public", page)
        self.assertIn(f'href="{REPOSITORY_URL}#readme"', english)
        self.assertIn(
            f'href="{REPOSITORY_URL}/blob/main/README.zh.md"',
            chinese,
        )

    def test_skill_requires_plan_and_approval_before_install(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for token in ("setup.py plan", "explicit approval", "setup.py install"):
            self.assertIn(token, text)
        plan_at = text.index("setup.py plan")
        approval_at = text.index("explicit approval", plan_at)
        install_at = text.index("setup.py install", approval_at)
        self.assertLess(plan_at, approval_at)
        self.assertLess(approval_at, install_at)
        self.assertIn("split_transcript.py", text)

    def test_maintenance_language_is_absent_from_reader_copy(self):
        combined = "\n".join(
            [
                (ROOT / "README.md").read_text(encoding="utf-8"),
                *(path.read_text(encoding="utf-8") for path in PAGES),
            ]
        )
        for phrase in (
            "独立公开版",
            "不会覆盖已有",
            "面向朋友",
            "现有工作流",
            "这个版本",
        ):
            self.assertNotIn(phrase, combined)

    def test_page_loads_no_remote_assets(self):
        audit = AssetAudit()
        for page in PAGES:
            audit.feed(page.read_text(encoding="utf-8"))
        self.assertEqual([], audit.remote_assets)

    def test_public_text_has_no_user_directory_or_email(self):
        patterns = [
            re.compile("/" + "Users/" + r"[^/\s]+/"),
            re.compile("/" + "home/" + r"[^/\s]+/"),
            re.compile(r"[A-Za-z]:\\" + r"Users\\[^\\\s]+\\"),
            re.compile(
                r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE
            ),
        ]
        suffixes = {".md", ".html", ".yaml", ".yml", ".json", ".py"}
        for path in ROOT.rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes:
                text = path.read_text(encoding="utf-8")
                for pattern in patterns:
                    self.assertIsNone(
                        pattern.search(text), str(path.relative_to(ROOT))
                    )


if __name__ == "__main__":
    unittest.main()
