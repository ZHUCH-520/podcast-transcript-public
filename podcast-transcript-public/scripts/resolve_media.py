"""Resolve a public episode page or RSS feed to an audio URL.

This module does not download audio. Callers fetch the returned URL only after
the user has asked for that episode.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


PAGE_LIMIT = 2_000_000
XIAOYUZHOU_HOSTS = {"xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"}
APPLE_HOSTS = {"podcasts.apple.com"}
LOOKUP_HOST = "itunes.apple.com"


@dataclass(frozen=True)
class ResolvedAudio:
    audio_url: str
    title: str
    kind: str


def display_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urlunsplit_public(parsed.scheme, netloc, parsed.path)


def urlunsplit_public(scheme: str, netloc: str, path: str) -> str:
    from urllib.parse import urlunparse

    return urlunparse((scheme, netloc, path, "", "", ""))


def classify_page(url: str) -> Optional[str]:
    parsed = _http_url(url)
    if parsed is None:
        return None
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host in XIAOYUZHOU_HOSTS:
        if len(parts) == 2 and parts[0] == "episode" and _episode_token(parts[1]):
            return "xiaoyuzhou-episode"
        if parts and parts[0] == "podcast":
            return "xiaoyuzhou-show"
        return None
    if host in APPLE_HOSTS:
        episode_id = _apple_episode_id(parsed)
        if episode_id:
            return "apple-episode"
        if any(part.startswith("id") and part[2:].isdigit() for part in parts):
            return "apple-show"
    return None


def resolve_page(
    url: str,
    *,
    opener: Optional[Callable] = None,
) -> ResolvedAudio:
    kind = classify_page(url)
    if kind in {"xiaoyuzhou-show", "apple-show"}:
        raise RuntimeError(
            "this is a show page, not one episode; pass an episode URL or an RSS feed"
        )
    if kind == "xiaoyuzhou-episode":
        return _resolve_xiaoyuzhou(url, opener)
    if kind == "apple-episode":
        return _resolve_apple(url, opener)
    raise RuntimeError("URL is not a supported episode page")


def resolve_feed(xml_text: str) -> ResolvedAudio:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise RuntimeError("feed is not valid XML") from exc
    local = _local(root.tag)
    if local == "rss":
        return _rss_item(root)
    if local == "feed":
        return _atom_entry(root)
    raise RuntimeError("feed has no audio enclosure")


def looks_like_feed(content_type: str, body: bytes) -> bool:
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind in {
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
    }:
        return True
    sample = body.lstrip()[:200].lower()
    return sample.startswith((b"<?xml", b"<rss", b"<feed"))


def read_limited(response, limit: int = PAGE_LIMIT) -> bytes:
    chunks = []
    total = 0
    while True:
        block = response.read(65536)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise RuntimeError("page or feed is larger than 2 MB; refusing to parse it")
        chunks.append(block)
    return b"".join(chunks)


def _resolve_xiaoyuzhou(url: str, opener: Optional[Callable]) -> ResolvedAudio:
    body, final_url = _fetch_text(url, opener, allowed_hosts=XIAOYUZHOU_HOSTS)
    payload = _next_data(body)
    episode = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("episode")
    )
    if not isinstance(episode, dict):
        raise RuntimeError("xiaoyuzhou episode page did not include episode data")
    enclosure = episode.get("enclosure") if isinstance(episode.get("enclosure"), dict) else {}
    audio_url = enclosure.get("url") or ""
    if not audio_url:
        media = episode.get("media") if isinstance(episode.get("media"), dict) else {}
        source = media.get("source") if isinstance(media.get("source"), dict) else {}
        audio_url = source.get("url") or ""
    title = str(episode.get("title") or "episode")
    _require_public_audio(audio_url)
    if urlparse(final_url).hostname not in XIAOYUZHOU_HOSTS:
        raise RuntimeError("xiaoyuzhou page redirected away from xiaoyuzhoufm.com")
    return ResolvedAudio(audio_url=audio_url, title=title, kind="xiaoyuzhou")


def _resolve_apple(url: str, opener: Optional[Callable]) -> ResolvedAudio:
    parsed = _http_url(url)
    episode_id = _apple_episode_id(parsed) if parsed is not None else None
    if not episode_id:
        raise RuntimeError("Apple Podcasts URL has no episode id")
    lookup = f"https://{LOOKUP_HOST}/lookup?id={episode_id}"
    body, final_url = _fetch_text(lookup, opener, allowed_hosts={LOOKUP_HOST})
    if urlparse(final_url).hostname != LOOKUP_HOST:
        raise RuntimeError("Apple lookup redirected away from itunes.apple.com")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Apple lookup did not return JSON") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        raise RuntimeError("Apple lookup did not find that episode")
    episode = results[0]
    audio_url = str(episode.get("episodeUrl") or "")
    title = str(episode.get("trackName") or episode.get("collectionName") or "episode")
    _require_public_audio(audio_url)
    return ResolvedAudio(audio_url=audio_url, title=title, kind="apple")


def _fetch_text(url: str, opener: Optional[Callable], *, allowed_hosts: set[str]) -> tuple[str, str]:
    parsed = _http_url(url)
    if parsed is None or (parsed.hostname or "").lower() not in allowed_hosts:
        raise RuntimeError("refusing to fetch a page outside the supported hosts")
    if parsed.username or parsed.password:
        raise RuntimeError("refusing a URL with embedded credentials")
    request = Request(url, headers={"User-Agent": "podcast-transcript"})
    open_url = opener or urlopen
    try:
        response = open_url(request, timeout=60)
    except OSError as exc:
        raise RuntimeError(f"could not fetch page: {display_url(url)}") from exc
    with response:
        final_url = response.geturl() if hasattr(response, "geturl") else url
        body = read_limited(response)
    return body.decode("utf-8", errors="replace"), final_url


def _rss_item(root: ElementTree.Element) -> ResolvedAudio:
    channel = _child(root, "channel")
    item = _child(channel, "item") if channel is not None else None
    if item is None:
        raise RuntimeError("RSS feed has no episode")
    enclosure = _child(item, "enclosure")
    audio_url = enclosure.get("url", "") if enclosure is not None else ""
    if not audio_url:
        audio_url = _media_content_url(item)
    title = (_text(_child(item, "title")) or "episode").strip()
    _require_enclosure(audio_url)
    return ResolvedAudio(audio_url=audio_url, title=title, kind="rss")


def _atom_entry(root: ElementTree.Element) -> ResolvedAudio:
    entry = _child(root, "entry")
    if entry is None:
        raise RuntimeError("Atom feed has no episode")
    audio_url = ""
    for child in list(entry):
        if _local(child.tag) != "link":
            continue
        rel = (child.get("rel") or "").lower()
        type_ = (child.get("type") or "").lower()
        href = child.get("href") or ""
        if rel == "enclosure" or type_.startswith("audio/") or type_.startswith("video/"):
            audio_url = href
            break
    title = (_text(_child(entry, "title")) or "episode").strip()
    _require_enclosure(audio_url)
    return ResolvedAudio(audio_url=audio_url, title=title, kind="rss")


def _media_content_url(item: ElementTree.Element) -> str:
    for child in list(item):
        if _local(child.tag) == "content" and child.get("url"):
            return child.get("url") or ""
    return ""


def _next_data(html: str) -> dict:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise RuntimeError("xiaoyuzhou episode page did not include episode data")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("xiaoyuzhou episode data was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("xiaoyuzhou episode data was not valid JSON")
    return payload


def _require_enclosure(url: str) -> None:
    if not url:
        raise RuntimeError("feed has no audio enclosure")
    _require_public_audio(url)


def _require_public_audio(url: str) -> None:
    parsed = _http_url(url)
    if parsed is None:
        raise RuntimeError("episode audio URL is not a public http(s) URL")
    if parsed.username or parsed.password:
        raise RuntimeError("refusing a URL with embedded credentials")


def _http_url(url: str):
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed


def _apple_episode_id(parsed) -> Optional[str]:
    values = parse_qs(parsed.query).get("i") or []
    if len(values) != 1 or not re.fullmatch(r"[0-9]{4,20}", values[0]):
        return None
    return values[0]


def _episode_token(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9]{8,40}", value) is not None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(parent: Optional[ElementTree.Element], name: str) -> Optional[ElementTree.Element]:
    if parent is None:
        return None
    for child in list(parent):
        if _local(child.tag) == name:
            return child
    return None


def _text(node: Optional[ElementTree.Element]) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve a Xiaoyuzhou episode, Apple Podcasts episode, or RSS/Atom feed to an audio URL."
    )
    parser.add_argument("url")
    args = parser.parse_args(argv)
    try:
        kind = classify_page(args.url)
        if kind in {"xiaoyuzhou-episode", "apple-episode", "xiaoyuzhou-show", "apple-show"}:
            resolved = resolve_page(args.url)
        else:
            raise RuntimeError(
                "pass a Xiaoyuzhou or Apple Podcasts episode URL; RSS feeds are resolved while downloading"
            )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "kind": resolved.kind,
                "title": " ".join(resolved.title.split()),
                "audio_url": display_url(resolved.audio_url),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
