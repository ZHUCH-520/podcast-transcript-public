"""Optional local speaker diarization.

Uses pyannote.audio 3.3.2 and the pinned speaker-diarization-3.1 pipeline.
WhisperX is not used: it would transcribe again, skip the MLX runtime, and
download extra alignment models. pyannote.audio 4.x is not used: it requires
Python 3.10 or newer and exports OpenTelemetry by default.

Download is opt-in. The gated Hugging Face repos need HF_TOKEN, which is never
written to state or transcripts. Later runs load the local pipeline only. When
this runtime is missing, speaker labels stay unset and transcription continues.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional

from doctor import MachineFacts
from model_catalog import (
    DIARIZATION_COMPONENTS,
    DIARIZATION_PACKAGES,
    GATED_DIARIZATION_REPOS,
    DiarizationComponent,
    diarization_component,
)
from runtime_paths import (
    RuntimePaths,
    atomic_write_json,
    build_runtime_paths,
    diarization_python,
)


logger = logging.getLogger(__name__)

SPEAKER_LABEL = re.compile(r"^SPEAKER_\d{2,}$")
_EMBEDDING_LINE = re.compile(
    r"^(\s*embedding:\s*)pyannote/wespeaker-voxceleb-resnet34-LM\s*$",
    re.MULTILINE,
)
_SEGMENTATION_LINE = re.compile(
    r"^(\s*segmentation:\s*)pyannote/segmentation-3.0\s*$",
    re.MULTILINE,
)


class DiarizationUnavailable(RuntimeError):
    pass


def select_diarization_device(facts: MachineFacts) -> tuple[str, str]:
    if facts.system == "Darwin":
        return "cpu", "pyannote runs on CPU on Mac, including Apple Silicon"
    if facts.cuda_available:
        return "cuda", "CUDA is available to the diarization runtime"
    return "cpu", "CUDA is not available"


def resolve_runtime_device(requested: str, *, cuda_available: bool) -> str:
    if requested == "cuda" and cuda_available:
        return "cuda"
    if requested == "cuda":
        logger.warning(
            "pyannote CUDA is not available in this PyTorch build; falling back to CPU"
        )
        return "cpu"
    return "cpu"


def diarization_status(paths: RuntimePaths) -> dict:
    path = paths.diarization_state
    if not path.is_file():
        return {"status": "not installed"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "incomplete"}
    if state.get("status") != "complete":
        return {"status": "incomplete"}
    return {
        "status": "complete",
        "backend": state.get("backend"),
        "device": state.get("device"),
        "model": state.get("model"),
    }


def create_diarization_plan(paths: RuntimePaths, facts: MachineFacts) -> dict:
    device, reason = select_diarization_device(facts)
    models = [
        {
            "role": component.role,
            "model": component.repo_id,
            "revision": component.revision,
            "download_bytes": component.download_bytes,
        }
        for component in DIARIZATION_COMPONENTS
    ]
    return {
        "action": "install-diarization",
        "backend": "pyannote",
        "device": device,
        "device_reason": reason,
        "packages": list(DIARIZATION_PACKAGES),
        "models": models,
        "download_bytes": sum(component.download_bytes for component in DIARIZATION_COMPONENTS),
        "cache_root": str(paths.root),
        "network_required": True,
        "hf_token_required": True,
        "token_env": "HF_TOKEN",
        "gated_repos": list(GATED_DIARIZATION_REPOS),
        "notice": (
            "Accept the gated model conditions, then set HF_TOKEN. "
            "The token is used only for the download and is not stored. "
            "The default PyTorch wheel is the CPU build; diarization uses CUDA "
            "only when that wheel exposes it, and otherwise logs a CPU fallback. "
            "This command does not install FFmpeg, CUDA, or a system package manager."
        ),
    }


def normalize_speaker(label: object) -> str:
    text = str(label).strip()
    if SPEAKER_LABEL.fullmatch(text):
        return text
    if re.fullmatch(r"\d{1,4}", text):
        return f"SPEAKER_{int(text):02d}"
    visible = text[:40]
    raise ValueError(f"unexpected speaker label: {visible}")


def _overlap(start: float, end: float, turn_start: float, turn_end: float) -> float:
    return max(0.0, min(end, turn_end) - max(start, turn_start))


def assign_speakers(segments: list[dict], turns: list[dict]) -> list[dict]:
    labeled = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        best_overlap = 0.0
        best_start = None
        best_speaker = None
        for turn in turns:
            amount = _overlap(start, end, float(turn["start"]), float(turn["end"]))
            if amount <= 0:
                continue
            speaker = normalize_speaker(turn["speaker"])
            turn_start = float(turn["start"])
            if best_speaker is None or amount > best_overlap or (
                amount == best_overlap
                and (turn_start, speaker) < (best_start, best_speaker)
            ):
                best_overlap = amount
                best_start = turn_start
                best_speaker = speaker
        item = dict(segment)
        item.pop("speaker", None)
        if best_speaker is not None:
            item["speaker"] = best_speaker
        labeled.append(item)
    return labeled


def localize_pipeline_config(
    text: str, segmentation_bin: Path, embedding_bin: Path
) -> str:
    for path in (segmentation_bin, embedding_bin):
        if "pyannote" not in path.as_posix():
            raise RuntimeError(
                "local diarization paths must include pyannote so the pipeline can detect the model type"
            )

    def replace(pattern: re.Pattern[str], path: Path, source: str) -> str:
        def replacer(match: re.Match[str]) -> str:
            return match.group(1) + path.as_posix()

        updated, count = pattern.subn(replacer, source, count=1)
        if count != 1:
            raise RuntimeError("pipeline config is missing segmentation or embedding")
        return updated

    updated = replace(_SEGMENTATION_LINE, segmentation_bin, text)
    updated = replace(_EMBEDDING_LINE, embedding_bin, updated)
    if (
        "pyannote/segmentation-3.0" in updated
        or "pyannote/wespeaker-voxceleb-resnet34-LM" in updated
    ):
        raise RuntimeError("pipeline config still points at the Hugging Face hub")
    return updated


def _hf_token(environ: Mapping[str, str]) -> str:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = str(environ.get(key, "")).strip()
        if value:
            return value
    raise RuntimeError(
        "diarization download requires HF_TOKEN after you accept the gated pyannote model conditions"
    )


def _validate_component_files(component: DiarizationComponent, model_path: Path) -> None:
    if model_path.is_symlink() or not model_path.is_dir():
        raise RuntimeError("downloaded diarization model is not a safe directory")
    missing = []
    for name in component.required_files:
        candidate = model_path / name
        if candidate.is_symlink() or not candidate.is_file():
            missing.append(name)
    if missing:
        raise RuntimeError(f"downloaded diarization model is missing required files: {missing}")


def _load_diarization_state(paths: RuntimePaths) -> Optional[dict]:
    if not paths.diarization_state.is_file():
        return None
    try:
        state = json.loads(paths.diarization_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("status") != "complete":
        return None
    model_path = Path(str(state.get("model_path", "")))
    if not model_path.is_dir():
        return None
    return state


def _snapshot_downloader(python: Path, run_command: Callable):
    def download(*, repo_id: str, revision: str, local_dir: str, token: str) -> str:
        env = os.environ.copy()
        env["HF_TOKEN"] = token
        run_command(
            [
                str(python),
                str(Path(__file__).resolve()),
                "_download",
                "--repo",
                repo_id,
                "--revision",
                revision,
                "--local-dir",
                local_dir,
            ],
            check=True,
            env=env,
        )
        return local_dir

    return download


def _download_snapshot(repo: str, revision: str, local_dir: Path) -> None:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HF_TOKEN is required to download diarization models")
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=repo,
            revision=revision,
            local_dir=str(local_dir),
            token=token,
        )
    except Exception as exc:
        message = str(exc).replace(token, "[redacted]")
        raise RuntimeError(f"diarization model download failed: {message}") from None


def _validate_local_pipeline(model_path: Path) -> None:
    from pyannote.audio import Pipeline

    Pipeline.from_pretrained(str(model_path))


def install_diarization(
    paths: RuntimePaths,
    facts: MachineFacts,
    *,
    accepted: bool,
    environ: Optional[Mapping[str, str]] = None,
    run_command: Callable = subprocess.run,
    download_snapshot: Optional[Callable] = None,
) -> dict:
    if not accepted:
        raise RuntimeError("diarization install requires --accept-download")
    if sys.version_info < (3, 9):
        raise RuntimeError("diarization requires Python 3.9 or newer")
    env = os.environ if environ is None else environ
    token = _hf_token(env)
    plan = create_diarization_plan(paths, facts)
    root = paths.models / "diarization"
    partials = []
    for component in DIARIZATION_COMPONENTS:
        final = root / component.directory_name
        partial = final.with_name(final.name + ".partial")
        if final.exists():
            raise RuntimeError(f"diarization model directory already exists: {final.name}")
        if partial.is_symlink() or (partial.exists() and not partial.is_dir()):
            raise RuntimeError(f"partial diarization path is not a safe directory: {partial.name}")
        partials.append((component, final, partial))

    paths.root.mkdir(parents=True, exist_ok=True)
    run_command([sys.executable, "-m", "venv", str(paths.diarization_venv)], check=True)
    python = diarization_python(paths, facts.system)
    run_command(
        [str(python), "-m", "pip", "install", *plan["packages"]],
        check=True,
    )
    downloader = download_snapshot or _snapshot_downloader(python, run_command)
    for component, _final, partial in partials:
        downloader(
            repo_id=component.repo_id,
            revision=component.revision,
            local_dir=str(partial),
            token=token,
        )
        _validate_component_files(component, partial)

    pipeline = diarization_component("pipeline")
    segmentation = diarization_component("segmentation")
    embedding = diarization_component("embedding")
    pipeline_partial = root / (pipeline.directory_name + ".partial")
    config_path = pipeline_partial / "config.yaml"
    localized = localize_pipeline_config(
        config_path.read_text(encoding="utf-8"),
        root / (segmentation.directory_name + ".partial") / "pytorch_model.bin",
        root / (embedding.directory_name + ".partial") / "pytorch_model.bin",
    )
    config_path.write_text(localized, encoding="utf-8")
    run_command(
        [
            str(python),
            str(Path(__file__).resolve()),
            "_validate",
            "--model-path",
            str(pipeline_partial),
        ],
        check=True,
    )
    installed_config = _retarget_installed_config(localized)
    for component, final, partial in partials:
        if component.role == "pipeline":
            continue
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, final)
    config_path.write_text(installed_config, encoding="utf-8")
    pipeline_final = root / pipeline.directory_name
    pipeline_final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(pipeline_partial, pipeline_final)

    state = {
        "status": "complete",
        "backend": "pyannote",
        "model": pipeline.repo_id,
        "revision": pipeline.revision,
        "model_path": str(pipeline_final),
        "device": plan["device"],
        "packages": plan["packages"],
    }
    atomic_write_json(paths.diarization_state, state)
    return state


def _retarget_installed_config(text: str) -> str:
    updated = text
    for component in DIARIZATION_COMPONENTS:
        updated = updated.replace(
            f"{component.directory_name}.partial/",
            f"{component.directory_name}/",
        )
    if ".partial/" in updated:
        raise RuntimeError("diarization config still points at a partial model")
    return updated


def run_pyannote(audio: Path, model_path: Path, device: str) -> list[dict]:
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise DiarizationUnavailable("pyannote is not installed") from exc

    selected = resolve_runtime_device(device, cuda_available=torch.cuda.is_available())
    pipeline = Pipeline.from_pretrained(str(model_path))
    try:
        pipeline.to(torch.device(selected))
    except RuntimeError as exc:
        if selected != "cuda":
            raise
        logger.warning(
            "pyannote CUDA inference failed (%s); falling back to CPU",
            exc,
        )
        pipeline.to(torch.device("cpu"))
    annotation = pipeline(str(audio))
    turns = []
    for turn, _track, speaker in annotation.itertracks(yield_label=True):
        turns.append(
            {
                "start": float(turn.start),
                "end": float(turn.end),
                "speaker": normalize_speaker(speaker),
            }
        )
    return turns


def apply_diarization(
    audio_path: Path,
    segments_path: Path,
    paths: RuntimePaths,
    *,
    replace: bool = False,
    runner: Optional[Callable] = None,
) -> dict:
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    if not segments_path.is_file():
        raise FileNotFoundError(segments_path)
    original = segments_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(original)
    except json.JSONDecodeError as exc:
        raise RuntimeError("segments.json is not valid JSON") from exc
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise RuntimeError("segments.json is missing a segment list")

    state = _load_diarization_state(paths)
    if state is None:
        return {
            "status": "unavailable",
            "reason": "diarization is not installed",
            "segment_count": len(segments),
            "speaker_ids": [],
        }
    if any(isinstance(item, dict) and item.get("speaker") for item in segments) and not replace:
        raise FileExistsError("refusing to replace existing speaker labels")

    execute = runner or (
        lambda: run_pyannote(audio_path, Path(state["model_path"]), str(state.get("device", "cpu")))
    )
    try:
        turns = execute()
    except (DiarizationUnavailable, ImportError) as exc:
        return {
            "status": "unavailable",
            "reason": str(exc) or "pyannote is not installed",
            "segment_count": len(segments),
            "speaker_ids": [],
        }

    labeled = assign_speakers(segments, turns)
    speaker_ids = []
    for segment in labeled:
        speaker = segment.get("speaker")
        if speaker and speaker not in speaker_ids:
            speaker_ids.append(speaker)
    payload["segments"] = labeled
    payload["diarization"] = {"backend": "pyannote", "speaker_ids": speaker_ids}
    atomic_write_json(segments_path, payload)
    return {
        "status": "complete",
        "segment_count": len(labeled),
        "speaker_ids": speaker_ids,
    }


def _format_plan(plan: dict) -> str:
    models = ", ".join(item["model"] for item in plan["models"])
    packages = ", ".join(plan["packages"])
    return "\n".join(
        [
            f"Backend: {plan['backend']}",
            f"Device: {plan['device']} ({plan['device_reason']})",
            f"Models: {models}",
            f"Download bytes: {plan['download_bytes']}",
            f"Packages: {packages}",
            f"Token: {plan['token_env']} (not stored)",
            plan["notice"],
        ]
    )


def _parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(description="Optional local speaker diarization.")
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="Show a read-only diarization plan.")
    plan_parser.add_argument("--json", action="store_true", dest="as_json")

    install_parser = commands.add_parser(
        "install", help="Install diarization after explicit approval."
    )
    install_parser.add_argument("--accept-download", action="store_true")

    apply_parser = commands.add_parser(
        "apply", help="Add speaker IDs to an existing segments.json."
    )
    apply_parser.add_argument("audio", type=Path)
    apply_parser.add_argument("--segments", type=Path, required=True)
    apply_parser.add_argument("--replace", action="store_true")
    apply_parser.add_argument(
        "--inside-runtime", action="store_true", help=argparse.SUPPRESS
    )

    download_parser = commands.add_parser("_download", help=argparse.SUPPRESS)
    download_parser.add_argument("--repo", required=True)
    download_parser.add_argument("--revision", required=True)
    download_parser.add_argument("--local-dir", type=Path, required=True)

    validate_parser = commands.add_parser("_validate", help=argparse.SUPPRESS)
    validate_parser.add_argument("--model-path", type=Path, required=True)
    return parser, install_parser


def _same_executable(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser, install_parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "_download":
        _download_snapshot(args.repo, args.revision, args.local_dir)
        return 0
    if args.command == "_validate":
        _validate_local_pipeline(args.model_path)
        return 0

    paths = build_runtime_paths()
    if args.command == "plan":
        from doctor import collect_machine_facts

        plan = create_diarization_plan(paths, collect_machine_facts())
        print(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
            if args.as_json
            else _format_plan(plan)
        )
        return 0

    if args.command == "install":
        from doctor import collect_machine_facts

        if not args.accept_download:
            install_parser.error("diarization install requires --accept-download")
        state = install_diarization(
            paths,
            collect_machine_facts(),
            accepted=True,
        )
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    python = diarization_python(paths)
    if not args.inside_runtime:
        if not python.is_file() or _load_diarization_state(paths) is None:
            print(
                json.dumps(
                    {
                        "status": "unavailable",
                        "reason": "diarization is not installed",
                        "speaker_ids": [],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if not _same_executable(Path(sys.executable), python):
            forwarded = list(sys.argv[1:] if argv is None else argv)
            os.execv(
                str(python),
                [str(python), str(Path(__file__).resolve()), *forwarded, "--inside-runtime"],
            )

    result = apply_diarization(
        args.audio.expanduser().resolve(),
        args.segments.expanduser().resolve(),
        paths,
        replace=args.replace,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
