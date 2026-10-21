"""Fetch a direct audio URL, or resolve a supported episode page or feed first."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
RESERVED_NAMES = {
    "raw-transcript.txt",
    "segments.json",
    "transcript.md",
    "transcript.srt",
    "transcript.vtt",
}


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def display_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def media_filename(url: str, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in MEDIA_EXTENSIONS:
        suffix = _suffix_for_type(content_type) or ""
    stem = Path(urlparse(url).path).stem
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._")
    if not stem:
        stem = "source-media"
    name = f"{stem[:80]}{suffix}"
    if name in RESERVED_NAMES or not suffix:
        name = f"source-media{suffix or '.bin'}"
    return name


def is_direct_media(url: str, content_type: str) -> bool:
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind.startswith(("audio/", "video/")):
        return True
    if kind in {"application/octet-stream", "binary/octet-stream", ""}:
        return Path(urlparse(url).path).suffix.lower() in MEDIA_EXTENSIONS
    return False


def fetch_media(
    url: str,
    output_dir: Path,
    *,
    opener: Optional[Callable] = None,
    direct_only: bool = False,
) -> Path:
    _reject_unsafe_url(url)
    resolver = _resolver()
    kind = None if direct_only else resolver.classify_page(url)
    if kind in {"xiaoyuzhou-episode", "apple-episode", "xiaoyuzhou-show", "apple-show"}:
        resolved = resolver.resolve_page(url, opener=opener)
        _log_resolution(resolved.kind, resolved.title, resolved.audio_url)
        return fetch_media(
            resolved.audio_url,
            output_dir,
            opener=opener,
            direct_only=True,
        )
    request = Request(url, headers={"User-Agent": "podcast-transcript"})
    open_url = opener or urlopen
    try:
        response = open_url(request, timeout=60)
    except OSError as exc:
        raise RuntimeError(f"could not fetch media: {display_url(url)}") from exc
    with response:
        content_type = _content_type(response)
        if is_direct_media(url, content_type):
            return _write_response(url, content_type, response, output_dir)
        if direct_only:
            raise RuntimeError(
                f"resolved URL is not a direct audio or video file: {display_url(url)}"
            )
        body = resolver.read_limited(response)
        if resolver.looks_like_feed(content_type, body):
            resolved = resolver.resolve_feed(body.decode("utf-8", errors="replace"))
            _log_resolution(resolved.kind, resolved.title, resolved.audio_url)
            return fetch_media(
                resolved.audio_url,
                output_dir,
                opener=opener,
                direct_only=True,
            )
        raise RuntimeError(
            f"URL is not a direct audio or video file: {display_url(url)}"
        )


def _reject_unsafe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("only a public http(s) audio, episode, or feed URL is accepted")
    if parsed.username or parsed.password:
        raise RuntimeError("refusing a URL with embedded credentials")


def _content_type(response) -> str:
    headers = getattr(response, "headers", {}) or {}
    if hasattr(headers, "get"):
        return str(headers.get("Content-Type") or headers.get("content-type") or "")
    return ""


def _log_resolution(kind: str, title: str, audio_url: str) -> None:
    clean_title = " ".join(title.split())
    print(
        f"Resolved {kind} episode {clean_title}: {display_url(audio_url)}",
        file=sys.stderr,
    )


def _resolver():
    directory = _scripts_dir()
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    import resolve_media

    return resolve_media


def _scripts_dir() -> Path:
    source = Path(__file__).resolve().parents[1] / "podcast-transcript-public" / "scripts"
    if (source / "resolve_media.py").is_file():
        return source
    bundled = Path(__file__).resolve().parent / "bundled_scripts"
    if (bundled / "resolve_media.py").is_file():
        return bundled
    raise RuntimeError("media resolver is not installed")


def _write_response(url: str, content_type: str, response, output_dir: Path) -> Path:
    target = output_dir / media_filename(url, content_type)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite: {target.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    handle = os.open(target, flags)
    try:
        with os.fdopen(handle, "wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def _suffix_for_type(content_type: str) -> str:
    kind = content_type.split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/ogg": ".ogg",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    }.get(kind, "")
