from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


APP_NAME = "podcast-transcript-public"
PARAGRAPH_OR_SPEAKER = re.compile(
    r"\n{2,}|\n(?=(?:Speaker|说话人)\s*[^:\n：]{0,32}[:：])",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"[。！？.!?]+[”’\"')）】》]*")
WHITESPACE = re.compile(r"\s+")


def _last_boundary(
    pattern: re.Pattern[str], text: str, floor: int, end: int
) -> Optional[int]:
    matches = list(pattern.finditer(text, floor, end))
    return matches[-1].end() if matches else None


def choose_boundary(text: str, start: int, hard_end: int, limit: int) -> int:
    floor = start + max(1, int(limit * 0.6))
    floor = min(floor, hard_end)
    for pattern in (PARAGRAPH_OR_SPEAKER, SENTENCE_END, WHITESPACE):
        boundary = _last_boundary(pattern, text, floor, hard_end)
        if boundary is not None and boundary > start:
            return boundary
    return hard_end


def split_text(text: str, limit: int = 4_500) -> list[str]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    chunks = []
    start = 0
    while start < len(text):
        hard_end = min(start + limit, len(text))
        end = (
            hard_end
            if hard_end == len(text)
            else choose_boundary(text, start, hard_end, limit)
        )
        if end <= start:
            end = hard_end
        chunks.append(text[start:end])
        start = end
    if "".join(chunks) != text:
        raise RuntimeError("splitter integrity check failed")
    return chunks


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def transcript_from_segments(segments: list[dict]) -> str:
    parts = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if text:
            parts.append(f"{text}\n")
    return "".join(parts)


def speakers_for_chunks(
    text: str, chunks: list[str], segments: list[dict]
) -> list[list[str]]:
    if transcript_from_segments(segments) != text:
        raise RuntimeError(
            "segments do not match the transcript text; refusing to attach speaker labels"
        )
    spans = []
    offset = 0
    for segment in segments:
        piece = str(segment.get("text", "")).strip()
        if not piece:
            continue
        end = offset + len(piece) + 1
        spans.append((offset, end, segment.get("speaker")))
        offset = end
    grouped = []
    cursor = 0
    for chunk in chunks:
        start = cursor
        end = cursor + len(chunk)
        seen = []
        for span_start, span_end, speaker in spans:
            if not speaker or not (span_start < end and span_end > start):
                continue
            label = str(speaker)
            if label not in seen:
                seen.append(label)
        grouped.append(seen)
        cursor = end
    return grouped


def validate_speaker_names(
    speaker_ids: list[str], mapping: dict[str, str]
) -> dict[str, str]:
    missing = [speaker for speaker in speaker_ids if speaker not in mapping]
    if missing:
        raise ValueError(f"speaker map is missing ids: {missing}")
    normalized = {}
    for speaker in speaker_ids:
        name = str(mapping[speaker]).strip()
        if not name:
            raise ValueError(f"speaker map has an empty name for {speaker}")
        normalized[speaker] = name
    return normalized


def manifest_for(
    source_name: str,
    text: str,
    chunks: list[str],
    limit: int,
    backup_name: Optional[str] = None,
    speakers: Optional[list[list[str]]] = None,
) -> dict:
    items = []
    offset = 0
    for index, chunk in enumerate(chunks):
        end = offset + len(chunk)
        item = {
            "index": index,
            "file": f"chunk-{index:03d}.txt",
            "start": offset,
            "end": end,
            "characters": len(chunk),
            "sha256": sha256_text(chunk),
            "head": chunk[:40],
            "tail": chunk[-40:],
        }
        if speakers is not None:
            item["speakers"] = list(speakers[index])
        items.append(item)
        offset = end
    payload = {
        "format": 1,
        "source_name": source_name,
        "input_characters": len(text),
        "input_sha256": sha256_text(text),
        "limit": limit,
        "backup": backup_name,
        "chunks": items,
    }
    if speakers is not None:
        ordered = []
        for group in speakers:
            for speaker in group:
                if speaker not in ordered:
                    ordered.append(speaker)
        payload["speaker_ids"] = ordered
    return payload


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def _write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _runtime_cache_root() -> Optional[Path]:
    override = os.environ.get("PODCAST_TRANSCRIPT_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Darwin":
        return (Path.home() / "Library" / "Caches" / APP_NAME).resolve()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) / APP_NAME).resolve() if local else None
    return (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_NAME
    ).resolve()


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_paths(source: Path, output_dir: Path) -> tuple[Path, Path]:
    source_path = source.expanduser().resolve()
    output_path = output_dir.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if output_path in {source_path, source_path.parent}:
        raise RuntimeError("output directory conflicts with the source")
    skill_root = Path(__file__).resolve().parents[1]
    cache_root = _runtime_cache_root()
    if _inside(output_path, skill_root) or (
        cache_root is not None and _inside(output_path, cache_root)
    ):
        raise RuntimeError("output directory must be outside the skill and runtime cache")
    if output_path.is_symlink():
        raise RuntimeError("output directory must not be a symlink")
    return source_path, output_path


def _load_segments(path: Path) -> list[dict]:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise RuntimeError("segments file is not valid JSON") from exc
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list):
        raise RuntimeError("segments file is missing a segment list")
    return segments


def split_file(
    source: Path,
    output_dir: Path,
    limit: int = 4_500,
    replace: bool = False,
    segments_path: Optional[Path] = None,
) -> dict:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    source_path, output_path = _validate_paths(source, output_dir)
    if output_path.exists() and not output_path.is_dir():
        raise FileExistsError(f"output path is not a directory: {output_path}")
    if output_path.is_dir() and any(output_path.iterdir()) and not replace:
        raise FileExistsError(f"refusing to replace nonempty output: {output_path.name}")

    backup = None
    if replace and output_path.is_dir() and any(output_path.iterdir()):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = output_path.with_name(f"{output_path.name}.backup-{stamp}")
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup.name}")

    text = _read_text(source_path)
    chunks = split_text(text, limit)
    speakers = None
    if segments_path is not None:
        speakers = speakers_for_chunks(text, chunks, _load_segments(segments_path))
    manifest = manifest_for(
        source_path.name,
        text,
        chunks,
        limit,
        backup.name if backup else None,
        speakers,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_dir() and not any(output_path.iterdir()):
        output_path.rmdir()
    temporary = output_path.with_name(
        f".{output_path.name}.partial-{uuid.uuid4().hex}"
    )
    temporary.mkdir()
    for item, chunk in zip(manifest["chunks"], chunks):
        _write_text(temporary / item["file"], chunk)
    _write_json(temporary / "chunks-manifest.json", manifest)

    restored_parts = []
    for item in manifest["chunks"]:
        restored = _read_text(temporary / item["file"])
        if sha256_text(restored) != item["sha256"]:
            raise RuntimeError(f"chunk hash mismatch: {item['file']}")
        restored_parts.append(restored)
    restored_text = "".join(restored_parts)
    if restored_text != text or sha256_text(restored_text) != manifest["input_sha256"]:
        raise RuntimeError("written chunks do not reproduce the input")

    backup_moved = False
    try:
        if backup is not None:
            os.replace(output_path, backup)
            backup_moved = True
        os.replace(temporary, output_path)
    except BaseException:
        if backup_moved and backup is not None and not output_path.exists():
            os.replace(backup, output_path)
        raise
    return manifest


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("limit must be at least 1")
    return parsed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Split a UTF-8 transcript without changing its text."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=_positive_int, default=4_500)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--segments",
        type=Path,
        help="Optional segments.json. Speaker IDs are copied into the manifest when they match the transcript.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    manifest = split_file(
        args.input,
        args.output_dir,
        limit=args.limit,
        replace=args.replace,
        segments_path=args.segments,
    )
    if args.as_json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Chunks: {len(manifest['chunks'])}")
        print(f"Input SHA-256: {manifest['input_sha256']}")
        if manifest["backup"]:
            print(f"Backup: {manifest['backup']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
