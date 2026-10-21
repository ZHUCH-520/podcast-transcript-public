"""Command-line entry point that calls the skill's existing scripts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from podcast_transcript.captions import export_segments, render_markdown, render_srt, render_vtt, write_exclusive
from podcast_transcript.media import fetch_media, is_http_url


Runner = Callable[[list[str]], int]


def scripts_dir() -> Path:
    source = Path(__file__).resolve().parents[1] / "podcast-transcript-public" / "scripts"
    if (source / "doctor.py").is_file():
        return source
    bundled = Path(__file__).resolve().parent / "bundled_scripts"
    if (bundled / "doctor.py").is_file():
        return bundled
    raise RuntimeError("transcription scripts are not installed")


def load_scripts():
    directory = str(scripts_dir())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import diarize
    import doctor
    import setup
    import split_transcript
    import transcribe

    return doctor, setup, transcribe, diarize, split_transcript


def script_runner(command: list[str]) -> int:
    script = scripts_dir() / command[0]
    if not script.is_file():
        raise RuntimeError(f"missing script: {command[0]}")
    completed = subprocess.run(
        [sys.executable, str(script), *command[1:]],
        check=False,
    )
    return int(completed.returncode)


def execute_run(
    source: str,
    output_dir: Path,
    *,
    language: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    glossary: Optional[Path] = None,
    profile: Optional[str] = None,
    accept_download: bool = False,
    diarize_requested: bool = False,
    captions: Optional[str] = None,
    paths=None,
    facts=None,
    runner: Runner = script_runner,
    fetch: Callable[..., Path] = fetch_media,
) -> dict:
    doctor, setup, transcribe, _diarize, _split = load_scripts()
    if paths is None:
        from runtime_paths import build_runtime_paths

        paths = build_runtime_paths()
    if facts is None:
        facts = doctor.collect_machine_facts()
    output = transcribe._validate_output_directory(output_dir, paths)
    planned = _planned_outputs(output, captions)
    existing = [path.name for path in planned if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite: " + ", ".join(existing))
    local_media = None
    if not is_http_url(source):
        local_media = Path(source).expanduser().resolve()
        if not local_media.is_file():
            raise FileNotFoundError(local_media)

    report = doctor.build_report(paths, facts)
    ready = (report.get("installed") or {}).get("status") == "complete"
    if not ready:
        plan = setup.create_plan(paths, facts, profile)
        if not accept_download:
            print(setup._format_plan(plan), file=sys.stderr)
            raise RuntimeError(
                "no local model is installed; re-run with --accept-download to approve the download"
            )
        selected = plan["profile"]
        print(setup._format_plan(plan), file=sys.stderr)
        _invoke(
            runner,
            ["setup.py", "install", "--profile", selected, "--accept-download"],
        )

    output.mkdir(parents=True, exist_ok=True)
    media = local_media if local_media is not None else fetch(source, output)
    transcribe_command = ["transcribe.py", str(media), "--output-dir", str(output)]
    if language:
        transcribe_command.extend(["--language", language])
    if initial_prompt:
        transcribe_command.extend(["--initial-prompt", initial_prompt])
    if glossary:
        transcribe_command.extend(["--glossary", str(glossary)])
    _invoke(runner, transcribe_command)
    raw_transcript = output / "raw-transcript.txt"
    segments_path = output / "segments.json"
    if not raw_transcript.is_file() or not segments_path.is_file():
        raise RuntimeError("transcription did not write raw-transcript.txt and segments.json")

    diarization = "not requested"
    if diarize_requested:
        diarization = _maybe_diarize(
            media,
            segments_path,
            report,
            accept_download=accept_download,
            runner=runner,
        )

    payload = json.loads(segments_path.read_text(encoding="utf-8"))
    files = ["raw-transcript.txt", "segments.json"]
    write_exclusive(output / "transcript.md", render_markdown(payload))
    files.append("transcript.md")
    if captions in {"srt", "both"}:
        write_exclusive(output / "transcript.srt", render_srt(payload))
        files.append("transcript.srt")
    if captions in {"vtt", "both"}:
        write_exclusive(output / "transcript.vtt", render_vtt(payload))
        files.append("transcript.vtt")
    return {"diarization": diarization, "files": files}


def _planned_outputs(output: Path, captions: Optional[str]) -> list[Path]:
    planned = [
        output / "raw-transcript.txt",
        output / "segments.json",
        output / "transcript.md",
    ]
    if captions in {"srt", "both"}:
        planned.append(output / "transcript.srt")
    if captions in {"vtt", "both"}:
        planned.append(output / "transcript.vtt")
    return planned


def _maybe_diarize(
    media: Path,
    segments: Path,
    report: dict,
    *,
    accept_download: bool,
    runner: Runner,
) -> str:
    status = (report.get("diarization") or {}).get("status")
    if status != "complete":
        if not accept_download:
            print(
                "warning: diarization is not installed; continuing without speaker labels. "
                "Re-run with --diarize --accept-download to approve that download.",
                file=sys.stderr,
            )
            return "skipped"
        try:
            _invoke(runner, ["diarize.py", "install", "--accept-download"])
        except subprocess.CalledProcessError:
            print(
                "warning: diarization install failed; continuing without speaker labels.",
                file=sys.stderr,
            )
            return "skipped"
    try:
        _invoke(runner, ["diarize.py", "apply", str(media), "--segments", str(segments)])
    except subprocess.CalledProcessError:
        print(
            "warning: diarization failed; continuing without speaker labels.",
            file=sys.stderr,
        )
        return "skipped"
    return "applied"


def _invoke(runner: Runner, command: list[str]) -> None:
    code = runner(command)
    if code != 0:
        raise subprocess.CalledProcessError(code, command)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe a local file, direct media URL, episode page, or RSS feed."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser(
        "run",
        help="Transcribe audio or video to raw text, segments, and simple Markdown.",
    )
    run_parser.add_argument(
        "input",
        help="Local audio/video, a direct media URL, a Xiaoyuzhou or Apple episode page, or an RSS/Atom feed.",
    )
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument(
        "--language",
        help="auto, zh, en, or zh-en. zh-en keeps Chinese punctuation and English names.",
    )
    run_parser.add_argument("--initial-prompt")
    run_parser.add_argument(
        "--glossary",
        type=Path,
        help="UTF-8 names and terms, one per line.",
    )
    run_parser.add_argument("--profile", choices=("small", "turbo", "large-v3"))
    run_parser.add_argument("--accept-download", action="store_true")
    run_parser.add_argument("--diarize", action="store_true")
    run_parser.add_argument("--captions", choices=("srt", "vtt", "both"))

    export_parser = commands.add_parser(
        "export",
        help="Write Markdown, SRT, or VTT from an existing segments.json.",
    )
    export_parser.add_argument("segments", type=Path)
    export_parser.add_argument("--format", required=True, choices=("md", "srt", "vtt"))
    export_parser.add_argument("--output", type=Path, required=True)

    doctor_parser = commands.add_parser("doctor", help="Show the local runtime report.")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    plan_parser = commands.add_parser("plan", help="Show a read-only model setup plan.")
    plan_parser.add_argument("--profile", choices=("small", "turbo", "large-v3"))
    plan_parser.add_argument("--json", action="store_true", dest="as_json")

    setup_parser = commands.add_parser(
        "setup", help="Install a local model after explicit approval."
    )
    setup_parser.add_argument("--profile", choices=("small", "turbo", "large-v3"), required=True)
    setup_parser.add_argument("--accept-download", action="store_true")

    split_parser = commands.add_parser(
        "split", help="Split a transcript with scripts/split_transcript.py."
    )
    split_parser.add_argument("args", nargs=argparse.REMAINDER)

    diarize_parser = commands.add_parser(
        "diarize", help="Plan, install, or apply optional speaker diarization."
    )
    diarize_parser.add_argument("args", nargs=argparse.REMAINDER)

    install_parser = commands.add_parser(
        "install-skill",
        help="Copy SKILL.md and scripts into a verified assistant skill directory.",
    )
    install_parser.add_argument("--target")
    install_parser.add_argument("--scope", choices=("user", "project"), default="user")
    install_parser.add_argument("--dest", type=Path)
    install_parser.add_argument("--list", action="store_true", dest="list_targets")
    return parser


def run(argv: Optional[list[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = execute_run(
                args.input,
                args.output_dir,
                language=args.language,
                initial_prompt=args.initial_prompt,
                glossary=args.glossary,
                profile=args.profile,
                accept_download=args.accept_download,
                diarize_requested=args.diarize,
                captions=args.captions,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "export":
            export_segments(args.segments, args.format, args.output)
            print(args.output.name)
            return 0
        if args.command == "doctor":
            doctor, *_rest = load_scripts()
            return int(doctor.main(["--json"] if args.as_json else []))
        if args.command == "plan":
            _doctor, setup, *_rest = load_scripts()
            plan_args = ["plan"]
            if args.profile:
                plan_args.extend(["--profile", args.profile])
            if args.as_json:
                plan_args.append("--json")
            return int(setup.main(plan_args))
        if args.command == "setup":
            if not args.accept_download:
                parser.error("setup requires --accept-download")
            _doctor, setup, *_rest = load_scripts()
            return int(
                setup.main(
                    ["install", "--profile", args.profile, "--accept-download"]
                )
            )
        if args.command == "split":
            *_ignored, split_mod = load_scripts()
            forwarded = _strip_remainder(args.args)
            return int(split_mod.main(forwarded))
        if args.command == "diarize":
            return script_runner(["diarize.py", *_strip_remainder(args.args)])
        if args.command == "install-skill":
            from podcast_transcript.skill_install import describe_targets, install_skill

            if args.list_targets:
                print(json.dumps(describe_targets(), ensure_ascii=False, indent=2))
                return 0
            if not args.target:
                parser.error("install-skill requires --target or --list")
            destination = install_skill(
                args.target,
                scope=args.scope,
                dest=args.dest,
            )
            print(str(destination))
            return 0
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed ({exc.returncode})", file=sys.stderr)
        return exc.returncode or 1
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _strip_remainder(values: list[str]) -> list[str]:
    if values and values[0] == "--":
        return values[1:]
    return values


def main(argv: Optional[list[str]] = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
