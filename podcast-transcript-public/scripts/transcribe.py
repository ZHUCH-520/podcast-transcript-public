from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional


logger = logging.getLogger(__name__)

# Chinese punctuation prompts are short on purpose. They steer the first window
# without pasting a transcript the model might echo.
ZH_INITIAL_PROMPT = "以下是中文播客，请使用中文标点。"
MIXED_INITIAL_PROMPT = "以下是普通话与英语夹杂的播客。中文使用中文标点，英文专名保留原文。"
LANGUAGE_CHOICES = {
    "auto": ("auto", None),
    "detect": ("auto", None),
    "zh": ("zh", "zh"),
    "zh-cn": ("zh", "zh"),
    "zh-hans": ("zh", "zh"),
    "chinese": ("zh", "zh"),
    "cmn": ("zh", "zh"),
    "en": ("en", "en"),
    "english": ("en", "en"),
    "zh-en": ("mixed", "zh"),
    "en-zh": ("mixed", "zh"),
    "mixed": ("mixed", "zh"),
    "bilingual": ("mixed", "zh"),
}

from runtime_paths import (
    RuntimePaths,
    atomic_write_json,
    atomic_write_text,
    build_runtime_paths,
    runtime_python,
)


def resolve_language(value: Optional[str]) -> tuple[str, Optional[str]]:
    if value is None or not str(value).strip():
        return "auto", None
    key = str(value).strip().lower().replace("_", "-")
    if key in LANGUAGE_CHOICES:
        return LANGUAGE_CHOICES[key]
    if re.fullmatch(r"[a-z]{2}", key):
        return key, key
    raise ValueError(f"unsupported language: {value}")


def default_initial_prompt(mode: str) -> Optional[str]:
    if mode == "zh":
        return ZH_INITIAL_PROMPT
    if mode == "mixed":
        return MIXED_INITIAL_PROMPT
    return None


def load_glossary(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    terms = []
    for raw in text.splitlines():
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        if len(term) > 40:
            raise ValueError("glossary term is longer than 40 characters")
        terms.append(term)
    if not terms:
        raise ValueError("glossary file has no terms")
    if len(terms) > 100:
        raise ValueError("glossary has more than 100 terms")
    joined = ", ".join(terms)
    if len(joined) > 800:
        raise ValueError("glossary text is longer than 800 characters")
    return joined


def stabilize_segments(segments: list[dict]) -> list[dict]:
    """Keep every distinct span, and collapse only overlapping identical text.

    Long-audio loops repeat the same words on a timestamp range that overlaps
    the previous segment. A later repeat, after the previous segment has ended,
    is real speech and stays.
    """
    prepared = []
    for item in segments:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = float(item["start"])
        end = float(item["end"])
        if start < 0:
            start = 0.0
        if end < start:
            end = start
        prepared.append({"start": start, "end": end, "text": text})
    prepared.sort(key=lambda item: (item["start"], item["end"], item["text"]))
    kept: list[dict] = []
    removed = 0
    for item in prepared:
        if kept:
            previous = kept[-1]
            same_text = _same_text(previous["text"], item["text"])
            overlaps = item["start"] < previous["end"]
            if same_text and overlaps:
                if item["end"] <= previous["end"] + 30:
                    previous["end"] = max(previous["end"], item["end"])
                removed += 1
                continue
        kept.append(item)
    if removed:
        logger.warning(
            "collapsed %s duplicated overlapping segment(s); later repeats were kept",
            removed,
        )
    return kept


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.split()) == " ".join(right.split())


def transcribe_audio(
    audio_path: Path,
    raw_output: Path,
    segments_output: Path,
    state: dict,
    *,
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    hotwords: Optional[str] = None,
    mlx_runner: Optional[Callable] = None,
    faster_runner: Optional[Callable] = None,
) -> dict:
    if state.get("status") != "complete":
        raise RuntimeError("transcription requires a complete runtime")
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    if raw_output.exists() or segments_output.exists():
        raise FileExistsError("refusing to overwrite transcript output")
    model_path = Path(state["model_path"])
    if not model_path.is_dir():
        raise FileNotFoundError(model_path)

    mode, whisper_language = resolve_language(language)
    prompt = (initial_prompt or "").strip() or default_initial_prompt(mode)
    if mode == "mixed":
        logger.warning(
            "language %s is transcribed as zh with a bilingual punctuation prompt",
            language,
        )
    backend = state["backend"]
    if backend == "mlx":
        runner = mlx_runner or run_mlx
        detected, segments = runner(
            audio_path,
            model_path,
            whisper_language,
            initial_prompt=prompt,
            hotwords=hotwords,
        )
    elif backend == "faster":
        runner = faster_runner or run_faster
        detected, segments = runner(
            audio_path,
            model_path,
            state.get("device", "cpu"),
            state.get("compute_type", "int8"),
            whisper_language,
            initial_prompt=prompt,
            hotwords=hotwords,
        )
    else:
        raise RuntimeError(f"unsupported installed backend: {backend}")

    normalized = stabilize_segments(segments)
    text = "".join(f"{item['text']}\n" for item in normalized)
    payload = {"language": detected, "segments": normalized}
    atomic_write_text(raw_output, text)
    try:
        atomic_write_json(segments_output, payload)
    except BaseException:
        raw_output.unlink(missing_ok=True)
        raise
    return payload


def run_mlx(
    audio: Path,
    model: Path,
    language: Optional[str],
    *,
    initial_prompt: Optional[str] = None,
    hotwords: Optional[str] = None,
):
    import mlx_whisper

    prompt = (initial_prompt or "").strip()
    if hotwords:
        # mlx-whisper 0.4.3 has no hotwords argument. Keep the glossary in the prompt.
        prompt = "\n".join(part for part in (prompt, f"专名：{hotwords}") if part)
    kwargs = {
        "word_timestamps": True,
        "condition_on_previous_text": False,
    }
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["initial_prompt"] = prompt
    result = mlx_whisper.transcribe(
        str(audio), path_or_hf_repo=str(model), **kwargs
    )
    segments = [
        {
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"],
        }
        for segment in result.get("segments", [])
    ]
    return result.get("language") or language or "unknown", segments


def run_faster(
    audio: Path,
    model: Path,
    device: str,
    compute_type: str,
    language: Optional[str],
    *,
    initial_prompt: Optional[str] = None,
    hotwords: Optional[str] = None,
):
    from faster_whisper import WhisperModel

    def execute(selected_device: str, selected_compute: str):
        instance = WhisperModel(
            str(model),
            device=selected_device,
            compute_type=selected_compute,
            local_files_only=True,
        )
        options = {
            "language": language,
            "word_timestamps": True,
            "vad_filter": True,
            # A lower threshold and speech padding keep quiet edges. Long pauses
            # still split, and condition_on_previous_text is off so a loop cannot
            # reprint the previous sentence across the next window.
            "vad_parameters": {
                "threshold": 0.3,
                "min_speech_duration_ms": 0,
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 400,
            },
            "condition_on_previous_text": False,
        }
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        if hotwords:
            options["hotwords"] = hotwords
        stream, info = instance.transcribe(str(audio), **options)
        items = [
            {"start": item.start, "end": item.end, "text": item.text}
            for item in stream
        ]
        return info.language, items

    try:
        return execute(device, compute_type)
    except RuntimeError as exc:
        if device != "cuda":
            raise
        logger.warning(
            "faster-whisper CUDA inference failed (%s); falling back to CPU with int8",
            exc,
        )
        return execute("cpu", "int8")


def _load_state(paths: RuntimePaths) -> dict:
    if not paths.state.is_file():
        raise RuntimeError("local transcription runtime is not installed")
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    if state.get("status") != "complete":
        raise RuntimeError("local transcription runtime is incomplete")
    return state


def _same_executable(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_output_directory(output_dir: Path, paths: RuntimePaths) -> Path:
    selected = output_dir.expanduser().resolve()
    skill_root = Path(__file__).resolve().parents[1]
    cache_root = paths.root.expanduser().resolve()
    if _inside(selected, skill_root) or _inside(selected, cache_root):
        raise RuntimeError("output directory must be outside the skill and runtime cache")
    return selected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe media with an installed local model."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--language",
        help="auto, zh, en, or zh-en. zh-en uses Chinese punctuation and keeps English names.",
    )
    parser.add_argument(
        "--initial-prompt",
        help="Optional first-window prompt. Replaces the built-in Chinese or bilingual prompt.",
    )
    parser.add_argument(
        "--glossary",
        type=Path,
        help="UTF-8 file of names and terms, one per line. Passed as hotwords.",
    )
    parser.add_argument(
        "--inside-runtime", action="store_true", help=argparse.SUPPRESS
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _main(args, argv)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _main(args: argparse.Namespace, argv: Optional[list[str]]) -> int:
    paths = build_runtime_paths()
    state = _load_state(paths)
    python = runtime_python(paths)
    if not args.inside_runtime:
        if not python.is_file():
            raise RuntimeError("installed runtime Python is missing")
        if not _same_executable(Path(sys.executable), python):
            forwarded = list(sys.argv[1:] if argv is None else argv)
            os.execv(
                str(python),
                [
                    str(python),
                    str(Path(__file__).resolve()),
                    *forwarded,
                    "--inside-runtime",
                ],
            )

    output_dir = _validate_output_directory(args.output_dir, paths)
    hotwords = load_glossary(args.glossary.expanduser()) if args.glossary else None
    payload = transcribe_audio(
        args.input.expanduser().resolve(),
        output_dir / "raw-transcript.txt",
        output_dir / "segments.json",
        state,
        language=args.language,
        initial_prompt=args.initial_prompt,
        hotwords=hotwords,
    )
    print(
        json.dumps(
            {
                "language": payload["language"],
                "segment_count": len(payload["segments"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
