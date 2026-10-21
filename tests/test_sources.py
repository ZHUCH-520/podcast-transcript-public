import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "podcast-transcript-public" / "scripts"))
sys.path.insert(0, str(ROOT))

from podcast_transcript.media import fetch_media
from resolve_media import resolve_feed


class Response:
    def __init__(self, body: bytes, content_type: str, url: str = ""):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self._url = url

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size >= len(self._body):
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:size], self._body[size:]
        return data

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


XIAOYUZHOU = (
    "https://www.xiaoyuzhoufm.com/episode/abcdef1234567890abcd"
)
NEXT_DATA = json.dumps(
    {
        "props": {
            "pageProps": {
                "episode": {
                    "title": "示例节目",
                    "enclosure": {
                        "url": "https://media.xyzcdn.net/example.m4a?token=secret"
                    },
                }
            }
        }
    }
)
PAGE = f'<html><script id="__NEXT_DATA__" type="application/json">{NEXT_DATA}</script></html>'
FEED = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>第一期</title>
    <enclosure url="https://cdn.example.com/one.mp3" type="audio/mpeg"/>
  </item>
  <item>
    <title>不要这条</title>
    <enclosure url="https://cdn.example.com/two.mp3"/>
  </item>
</channel></rss>
"""


class SourceResolveTest(unittest.TestCase):
    def test_xiaoyuzhou_episode_downloads_enclosure_without_printing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = []

            def opener(request, timeout=0):
                self.assertIsInstance(request, Request)
                self.assertGreater(timeout, 0)
                seen.append(request.full_url)
                if request.full_url == XIAOYUZHOU:
                    return Response(PAGE.encode("utf-8"), "text/html", XIAOYUZHOU)
                return Response(b"audio", "audio/mp4", request.full_url)

            stderr = StringIO()
            with patch("sys.stderr", stderr):
                saved = fetch_media(XIAOYUZHOU, Path(tmp), opener=opener)
            self.assertEqual(b"audio", saved.read_bytes())
            self.assertEqual("example.m4a", saved.name)
            self.assertEqual(
                [XIAOYUZHOU, "https://media.xyzcdn.net/example.m4a?token=secret"],
                seen,
            )
            self.assertIn("xiaoyuzhou", stderr.getvalue())
            self.assertIn("示例节目", stderr.getvalue())
            self.assertNotIn("token", stderr.getvalue())

    def test_show_pages_and_generic_html_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "show page"):
                fetch_media(
                    "https://www.xiaoyuzhoufm.com/podcast/abcdef1234567890",
                    root,
                    opener=self.fail,
                )
            with self.assertRaisesRegex(RuntimeError, "show page"):
                fetch_media(
                    "https://podcasts.apple.com/cn/podcast/show/id1234567890",
                    root,
                    opener=self.fail,
                )

            def html_opener(request, timeout=0):
                return Response(b"<html>sign in</html>", "text/html", request.full_url)

            with self.assertRaisesRegex(RuntimeError, "not a direct"):
                fetch_media("https://example.com/episode", root, opener=html_opener)

    def test_apple_episode_uses_the_lookup_id_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = []

            def opener(request, timeout=0):
                seen.append(request.full_url)
                if "lookup" in request.full_url:
                    body = json.dumps(
                        {
                            "resultCount": 1,
                            "results": [
                                {
                                    "trackName": "Apple 示例",
                                    "episodeUrl": "https://cdn.example.com/apple.mp3",
                                }
                            ],
                        }
                    ).encode("utf-8")
                    return Response(body, "application/json", request.full_url)
                return Response(b"audio", "audio/mpeg", request.full_url)

            saved = fetch_media(
                "https://podcasts.apple.com/cn/podcast/example/id999?i=1000123456789",
                Path(tmp),
                opener=opener,
            )
            self.assertEqual("apple.mp3", saved.name)
            self.assertEqual(
                "https://itunes.apple.com/lookup?id=1000123456789",
                seen[0],
            )
            self.assertNotIn("id999", seen[0])

    def test_rss_uses_the_first_enclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = []

            def opener(request, timeout=0):
                seen.append(request.full_url)
                if request.full_url.endswith("/feed.xml"):
                    return Response(FEED.encode("utf-8"), "application/rss+xml", request.full_url)
                return Response(b"audio", "audio/mpeg", request.full_url)

            stderr = StringIO()
            with patch("sys.stderr", stderr):
                saved = fetch_media(
                    "https://example.com/feed.xml",
                    Path(tmp),
                    opener=opener,
                )
            self.assertEqual("one.mp3", saved.name)
            self.assertEqual("https://cdn.example.com/one.mp3", seen[1])
            self.assertIn("第一期", stderr.getvalue())
            self.assertNotIn("two.mp3", "".join(seen))

    def test_credential_enclosure_is_refused(self):
        hidden = "https://user:" + "secret@" + "cdn.example.com/one.mp3"
        xml = (
            "<rss><channel><item><title>秘密</title><enclosure url=\""
            + hidden
            + "\"/></item></channel></rss>"
        )
        with self.assertRaisesRegex(RuntimeError, "credentials"):
            resolve_feed(xml)
        with self.assertRaisesRegex(RuntimeError, "no audio enclosure"):
            resolve_feed("<rss><channel><item><title>空</title></item></channel></rss>")


if __name__ == "__main__":
    unittest.main()
