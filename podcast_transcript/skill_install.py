"""Copy the single SKILL.md folder into verified assistant skill directories."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


SKILL_NAME = "podcast-transcript-public"


@dataclass(frozen=True)
class SkillTarget:
    key: str
    product: str
    user_parts: Optional[tuple[str, ...]]
    project_parts: tuple[str, ...]
    source: str


# Paths below were read from the product's own documentation.
VERIFIED_TARGETS = (
    SkillTarget(
        "qoder",
        "Qoder",
        (".qoder", "skills"),
        (".qoder", "skills"),
        "https://docs.qoder.com/extensions/skills",
    ),
    SkillTarget(
        "qoder-cn",
        "Qoder CN CLI",
        None,
        (".qoder", "skills"),
        "https://docs.qoder.cn/cli/config-scope",
    ),
    SkillTarget(
        "lingma",
        "Tongyi Lingma and Qoder CN IDE",
        (".lingma", "skills"),
        (".lingma", "skills"),
        "https://docs.qoder.cn/user-guide/skills",
    ),
    SkillTarget(
        "codebuddy",
        "CodeBuddy",
        (".codebuddy", "skills"),
        (".codebuddy", "skills"),
        "https://www.codebuddy.ai/docs/cli/skills",
    ),
    SkillTarget(
        "trae",
        "TRAE (international docs)",
        (".trae", "skills"),
        (".trae", "skills"),
        "https://docs.trae.ai/ide/skills",
    ),
    SkillTarget(
        "trae-cn",
        "TRAE CN",
        (".trae-cn", "skills"),
        (".trae", "skills"),
        "https://docs.trae.cn/ide/skills",
    ),
    SkillTarget(
        "qwen",
        "Qwen Code",
        (".qwen", "skills"),
        (".qwen", "skills"),
        "https://qwenlm.github.io/qwen-code-docs/en/users/features/skills/",
    ),
    SkillTarget(
        "kimi",
        "Kimi Code CLI",
        (".kimi-code", "skills"),
        (".kimi-code", "skills"),
        "https://www.kimi.com/code/docs/kimi-code-cli/customization/skills.html",
    ),
    SkillTarget(
        "claude",
        "Claude Code",
        (".claude", "skills"),
        (".claude", "skills"),
        "repository README: Claude Code skill directory",
    ),
    SkillTarget(
        "cursor",
        "Cursor",
        (".cursor", "skills"),
        (".cursor", "skills"),
        "repository README: Cursor skill directory",
    ),
)


UNVERIFIED = (
    {
        "name": "qcoder",
        "reason": "Not a separate product. The documented name is Qoder; use --target qoder.",
    },
    {
        "name": "CatPaw",
        "reason": (
            "No SKILL.md directory was available from CatPaw's own docs during this check. "
            "Secondary notes mention .catpaw/rules, which is a rules file rather than this skill folder."
        ),
    },
    {
        "name": "WorkBuddy",
        "reason": (
            "The official skills page describes uploading a skill package and a project skills "
            "directory, but it does not name that directory. Pass --target workbuddy --dest to "
            "prepare a folder for manual upload. Third-party home-directory paths are not used."
        ),
    },
)


def target_by_key(key: str) -> SkillTarget:
    for target in VERIFIED_TARGETS:
        if target.key == key:
            return target
    known = ", ".join(target.key for target in VERIFIED_TARGETS)
    raise ValueError(f"unsupported skill target: {key}. Verified targets: {known}")


def skill_source() -> Path:
    repo_skill = Path(__file__).resolve().parents[1] / SKILL_NAME
    if (repo_skill / "SKILL.md").is_file() and (repo_skill / "scripts" / "doctor.py").is_file():
        return repo_skill
    template = Path(__file__).resolve().parent / "skill_template"
    scripts = Path(__file__).resolve().parent / "bundled_scripts"
    if (template / "SKILL.md").is_file() and (scripts / "doctor.py").is_file():
        return template
    raise RuntimeError("skill files are not installed")


def install_skill(
    target: str,
    *,
    scope: str = "user",
    dest: Optional[Path] = None,
    home: Optional[Path] = None,
    project: Optional[Path] = None,
) -> Path:
    if target == "qcoder":
        raise ValueError("qcoder is not a product name; use --target qoder")
    if scope not in {"user", "project"}:
        raise ValueError("scope must be user or project")
    if target == "workbuddy":
        if dest is None:
            raise ValueError(UNVERIFIED[2]["reason"])
        root = dest.expanduser().resolve()
    else:
        spec = target_by_key(target)
        if dest is not None:
            root = dest.expanduser().resolve()
        else:
            parts = spec.user_parts if scope == "user" else spec.project_parts
            if not parts:
                raise ValueError(
                    "Qoder CN IDE uses ~/.lingma/skills; install with --target lingma. "
                    "Qoder CN CLI documents project skills at .qoder/skills. "
                    "A user-level Qoder CN skills directory was not on the current skills page."
                )
            base = home or Path.home() if scope == "user" else project or Path.cwd()
            root = base.joinpath(*parts)
    destination = root / SKILL_NAME
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite: {destination}")
    source = skill_source()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (source / "scripts" / "doctor.py").is_file():
        shutil.copytree(source, destination, ignore=_ignore_skill_junk)
    else:
        _assemble_template(source, destination)
    skill_file = destination / "SKILL.md"
    if not skill_file.is_file() or not (destination / "scripts" / "doctor.py").is_file():
        raise RuntimeError("skill install did not copy SKILL.md and scripts")
    return destination


def _assemble_template(template: Path, destination: Path) -> None:
    scripts = Path(__file__).resolve().parent / "bundled_scripts"
    shutil.copytree(
        template,
        destination,
        ignore=_ignore_skill_junk,
    )
    shutil.copytree(scripts, destination / "scripts", ignore=_ignore_skill_junk)


def _ignore_skill_junk(_directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".DS_Store"}
    ignored.update(name for name in names if name.endswith((".pyc", ".pyo")))
    return ignored


def describe_targets() -> dict:
    return {
        "verified": [
            {
                "target": item.key,
                "product": item.product,
                "user": (
                    "~/" + "/".join(item.user_parts)
                    if item.user_parts
                    else "not listed; Qoder CN IDE uses ~/.lingma/skills"
                ),
                "project": "./" + "/".join(item.project_parts),
                "source": item.source,
            }
            for item in VERIFIED_TARGETS
        ],
        "not_installed_automatically": list(UNVERIFIED),
    }
