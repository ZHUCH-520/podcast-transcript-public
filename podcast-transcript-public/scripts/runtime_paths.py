from __future__ import annotations

import json
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


APP_NAME = "podcast-transcript-public"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    venv: Path
    models: Path
    state: Path
    diarization_venv: Path
    diarization_state: Path


def build_runtime_paths(
    system: Optional[str] = None,
    home: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> RuntimePaths:
    env = dict(os.environ if environ is None else environ)
    os_name = system or platform.system()
    user_home = Path.home() if home is None else home
    override = env.get("PODCAST_TRANSCRIPT_CACHE")
    if override:
        root = Path(override).expanduser()
    elif os_name == "Darwin":
        root = user_home / "Library" / "Caches" / APP_NAME
    elif os_name == "Windows":
        local = env.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is required on Windows")
        root = Path(local) / APP_NAME
    else:
        root = Path(env.get("XDG_CACHE_HOME", user_home / ".cache")) / APP_NAME
    return RuntimePaths(
        root=root,
        venv=root / "venv",
        models=root / "models",
        state=root / "state.json",
        diarization_venv=root / "diarization-venv",
        diarization_state=root / "diarization.json",
    )


def _venv_python(venv: Path, system: Optional[str] = None) -> Path:
    os_name = system or platform.system()
    relative = Path("Scripts") / "python.exe" if os_name == "Windows" else Path("bin") / "python"
    return venv / relative


def runtime_python(paths: RuntimePaths, system: Optional[str] = None) -> Path:
    return _venv_python(paths.venv, system)


def diarization_python(paths: RuntimePaths, system: Optional[str] = None) -> Path:
    return _venv_python(paths.diarization_venv, system)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
