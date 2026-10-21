from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from doctor import MachineFacts, build_report, collect_machine_facts, recommend_profile
from model_catalog import ModelSource, package_requirements, source_for
from runtime_paths import (
    RuntimePaths,
    atomic_write_json,
    build_runtime_paths,
    runtime_python,
)


def create_plan(
    paths: RuntimePaths,
    facts: MachineFacts,
    profile: Optional[str] = None,
    backend: Optional[str] = None,
) -> dict:
    recommended_backend, recommended_profile = recommend_profile(facts)
    selected_backend = backend or recommended_backend
    selected_profile = profile or recommended_profile
    if selected_backend == "mlx" and not (
        facts.system == "Darwin"
        and facts.machine.lower() in {"arm64", "aarch64"}
    ):
        raise RuntimeError("mlx backend requires an Apple Silicon Mac")
    source = source_for(selected_profile, selected_backend)
    return {
        "action": "install-local-runtime",
        "backend": selected_backend,
        "profile": selected_profile,
        "recommended": (
            selected_backend == recommended_backend
            and selected_profile == recommended_profile
        ),
        "model": source.repo_id,
        "revision": source.revision,
        "download_bytes": source.download_bytes,
        "cache_root": str(paths.root),
        "packages": package_requirements(selected_backend),
        "network_required": True,
    }


def _validate_model_files(source: ModelSource, model_path: Path) -> None:
    if model_path.is_symlink() or not model_path.is_dir():
        raise RuntimeError("downloaded model is not a safe directory")
    missing = []
    for name in source.required_files:
        candidate = model_path / name
        if candidate.is_symlink() or not candidate.is_file():
            missing.append(name)
    if missing:
        raise RuntimeError(f"downloaded model is missing required files: {missing}")


def _runtime_device(backend: str, facts: MachineFacts) -> tuple[str, str]:
    if backend == "faster":
        if facts.cuda_available:
            return "cuda", "float16"
        return "cpu", "int8"
    if backend == "mlx":
        return "cpu", "float16"
    raise RuntimeError(f"unsupported backend: {backend}")


def _validate_local_model(backend: str, model_path: Path) -> None:
    if backend == "mlx":
        from mlx_whisper.load_models import load_model

        load_model(str(model_path))
        return
    if backend == "faster":
        from faster_whisper import WhisperModel

        WhisperModel(
            str(model_path),
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )
        return
    raise RuntimeError(f"unsupported backend: {backend}")


def _validate_backend_load(
    python: Path,
    backend: str,
    model_path: Path,
    run_command: Callable = subprocess.run,
) -> None:
    run_command(
        [
            str(python),
            str(Path(__file__).resolve()),
            "_validate",
            "--backend",
            backend,
            "--model-path",
            str(model_path),
        ],
        check=True,
    )


def _snapshot_downloader(python: Path) -> Callable[..., str]:
    def download(*, repo_id: str, revision: str, local_dir: str) -> str:
        subprocess.run(
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
        )
        return local_dir

    return download


def install_runtime(
    paths: RuntimePaths,
    facts: MachineFacts,
    *,
    profile: str,
    backend: str,
    accepted: bool,
    run_command: Callable = subprocess.run,
    download_snapshot: Optional[Callable] = None,
    find_program: Callable = shutil.which,
) -> dict:
    if not accepted:
        raise RuntimeError("install requires --accept-download")
    plan = create_plan(paths, facts, profile, backend)
    source = source_for(profile, backend)
    if backend == "mlx" and not find_program("ffmpeg"):
        raise RuntimeError(
            "mlx-whisper requires ffmpeg; install it separately, then rerun setup"
        )
    model_final = paths.models / backend / profile
    model_partial = model_final.with_name(model_final.name + ".partial")
    if model_final.exists():
        raise RuntimeError(f"model directory already exists: {model_final}")
    if model_partial.is_symlink() or (
        model_partial.exists() and not model_partial.is_dir()
    ):
        raise RuntimeError(f"partial model path is not a safe directory: {model_partial}")

    paths.root.mkdir(parents=True, exist_ok=True)
    run_command([sys.executable, "-m", "venv", str(paths.venv)], check=True)
    python = runtime_python(paths, facts.system)
    run_command(
        [str(python), "-m", "pip", "install", *plan["packages"]],
        check=True,
    )
    downloader = download_snapshot or _snapshot_downloader(python)
    downloader(
        repo_id=plan["model"],
        revision=plan["revision"],
        local_dir=str(model_partial),
    )
    _validate_model_files(source, model_partial)
    _validate_backend_load(python, backend, model_partial, run_command)
    model_final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(model_partial, model_final)
    device, compute_type = _runtime_device(backend, facts)
    state = {
        "status": "complete",
        "backend": backend,
        "profile": profile,
        "model": plan["model"],
        "revision": plan["revision"],
        "model_path": str(model_final),
        "device": device,
        "compute_type": compute_type,
    }
    atomic_write_json(paths.state, state)
    return state


def _download_snapshot(repo: str, revision: str, local_dir: Path) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo,
        revision=revision,
        local_dir=str(local_dir),
    )


def _format_plan(plan: dict) -> str:
    packages = ", ".join(plan["packages"])
    return "\n".join(
        [
            f"Backend: {plan['backend']}",
            f"Model profile: {plan['profile']}",
            f"Model: {plan['model']} @ {plan['revision']}",
            f"Download bytes: {plan['download_bytes']}",
            f"Cache: {plan['cache_root']}",
            f"Packages: {packages}",
        ]
    )


def _parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(description="Prepare a local podcast runtime.")
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="Show a read-only setup plan.")
    plan_parser.add_argument("--profile", choices=("small", "turbo", "large-v3"))
    plan_parser.add_argument("--json", action="store_true", dest="as_json")

    install_parser = commands.add_parser(
        "install", help="Install an explicitly approved local runtime."
    )
    install_parser.add_argument(
        "--profile", choices=("small", "turbo", "large-v3"), required=True
    )
    install_parser.add_argument("--accept-download", action="store_true")

    download_parser = commands.add_parser("_download", help=argparse.SUPPRESS)
    download_parser.add_argument("--repo", required=True)
    download_parser.add_argument("--revision", required=True)
    download_parser.add_argument("--local-dir", type=Path, required=True)

    validate_parser = commands.add_parser("_validate", help=argparse.SUPPRESS)
    validate_parser.add_argument("--backend", choices=("mlx", "faster"), required=True)
    validate_parser.add_argument("--model-path", type=Path, required=True)
    return parser, install_parser


def main(argv: Optional[list[str]] = None) -> int:
    parser, install_parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "_download":
        _download_snapshot(args.repo, args.revision, args.local_dir)
        return 0
    if args.command == "_validate":
        _validate_local_model(args.backend, args.model_path)
        return 0

    facts = collect_machine_facts()
    paths = build_runtime_paths()
    if args.command == "plan":
        plan = create_plan(paths, facts, args.profile)
        print(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
            if args.as_json
            else _format_plan(plan)
        )
        return 0

    if not args.accept_download:
        install_parser.error("install requires --accept-download")
    plan = create_plan(paths, facts, args.profile)
    install_runtime(
        paths,
        facts,
        profile=args.profile,
        backend=plan["backend"],
        accepted=True,
    )
    report = build_report(paths, facts)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
