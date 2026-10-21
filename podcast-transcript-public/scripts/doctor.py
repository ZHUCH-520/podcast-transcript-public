from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from model_catalog import source_for
from runtime_paths import RuntimePaths, build_runtime_paths


@dataclass(frozen=True)
class MachineFacts:
    system: str
    machine: str
    memory_bytes: Optional[int]
    cuda_available: bool
    cuda_memory_bytes: Optional[int]
    ffmpeg_available: bool
    python_supported: bool


def recommend_profile(facts: MachineFacts) -> tuple[str, str]:
    if facts.system == "Darwin" and facts.machine.lower() in {"arm64", "aarch64"}:
        profile = "turbo" if (facts.memory_bytes or 0) >= 16 * 1024**3 else "small"
        return "mlx", profile
    if facts.system in {"Darwin", "Windows", "Linux"}:
        if (
            facts.system in {"Windows", "Linux"}
            and facts.cuda_available
            and (facts.cuda_memory_bytes or 0) >= 8 * 1024**3
        ):
            return "faster", "turbo"
        return "faster", "small"
    raise RuntimeError(f"unsupported platform: {facts.system} {facts.machine}")


def build_report(paths: RuntimePaths, facts: MachineFacts) -> dict:
    backend, profile = recommend_profile(facts)
    state = None
    if paths.state.is_file():
        state = json.loads(paths.state.read_text(encoding="utf-8"))
    source = source_for(profile, backend)
    from diarize import diarization_status

    return {
        "supported": facts.python_supported,
        "system": facts.system,
        "machine": facts.machine,
        "memory_bytes": facts.memory_bytes,
        "backend": backend,
        "recommended_profile": profile,
        "recommended_model": source.repo_id,
        "download_bytes": source.download_bytes,
        "cache_root": str(paths.root),
        "system_dependencies": {
            "ffmpeg": facts.ffmpeg_available if backend == "mlx" else None,
        },
        "installed": state,
        "diarization": diarization_status(paths),
    }


def _mac_memory_bytes() -> Optional[int]:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _windows_memory_bytes() -> Optional[int]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.total_physical)
    except (AttributeError, OSError):
        return None


def _memory_bytes_from_meminfo(text: str) -> Optional[int]:
    for line in text.splitlines():
        if not line.startswith("MemTotal:"):
            continue
        parts = line.split()
        try:
            return int(parts[1]) * 1024
        except (IndexError, ValueError):
            return None
    return None


def _linux_memory_bytes() -> Optional[int]:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    return _memory_bytes_from_meminfo(text)


def _cuda_from_nvidia_smi() -> tuple[bool, Optional[int]]:
    if not shutil.which("nvidia-smi"):
        return False, None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        values = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, None
    runtime_visible = bool(
        ctypes.util.find_library("cublas") and ctypes.util.find_library("cudnn")
    )
    if not values or not runtime_visible:
        return False, None
    return True, max(values) * 1024**2


def collect_machine_facts() -> MachineFacts:
    system = platform.system()
    memory = None
    cuda_available = False
    cuda_memory = None
    if system == "Darwin":
        memory = _mac_memory_bytes()
    elif system == "Windows":
        memory = _windows_memory_bytes()
        cuda_available, cuda_memory = _cuda_from_nvidia_smi()
    elif system == "Linux":
        memory = _linux_memory_bytes()
        cuda_available, cuda_memory = _cuda_from_nvidia_smi()
    return MachineFacts(
        system=system,
        machine=platform.machine(),
        memory_bytes=memory,
        cuda_available=cuda_available,
        cuda_memory_bytes=cuda_memory,
        ffmpeg_available=shutil.which("ffmpeg") is not None,
        python_supported=sys.version_info >= (3, 9),
    )


def _human_report(report: dict) -> str:
    installed = report.get("installed") or {}
    installed_text = installed.get("status", "not installed")
    return "\n".join(
        [
            f"Platform: {report['system']} {report['machine']}",
            f"Backend: {report['backend']}",
            f"Recommended model: {report['recommended_profile']}",
            f"Download bytes: {report['download_bytes']}",
            f"Cache: {report['cache_root']}",
            f"Installed: {installed_text}",
            f"Diarization: {report['diarization']['status']}",
        ]
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the local podcast runtime.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    facts = collect_machine_facts()
    try:
        report = build_report(build_runtime_paths(), facts)
    except RuntimeError as exc:
        payload = {
            "supported": False,
            "system": facts.system,
            "machine": facts.machine,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False) if args.as_json else payload["error"])
        return 2
    if not facts.python_supported:
        report["supported"] = False
        report["error"] = "Python 3.9 or newer is required"
    print(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.as_json
        else _human_report(report)
    )
    return 0 if report["supported"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
