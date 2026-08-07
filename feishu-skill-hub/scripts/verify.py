#!/usr/bin/env python3
"""Run local, non-network verification for the Feishu Skill Hub project."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC_SKILL = ROOT / "skills" / "feishu-skill-sync"
ENTRY_POINT = SYNC_SKILL / "scripts" / "skill_sync.py"
FORBIDDEN_NAMES = {".DS_Store", ".env", "library.json"}


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def check_tree() -> None:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        if "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            problems.append(f"symbolic link: {path.relative_to(ROOT)}")
        if path.name in FORBIDDEN_NAMES:
            problems.append(f"forbidden generated/private file: {path.relative_to(ROOT)}")
    if problems:
        raise SystemExit("\n".join(problems))


def main() -> int:
    check_tree()
    run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(SYNC_SKILL / "tests"),
            "-v",
        ]
    )
    run([sys.executable, "-m", "compileall", "-q", str(SYNC_SKILL / "scripts")])
    run([sys.executable, str(ENTRY_POINT), "inspect", str(SYNC_SKILL)])
    if shutil.which("ruff"):
        run(["ruff", "check", str(SYNC_SKILL / "scripts"), str(SYNC_SKILL / "tests")])
    print("All local checks passed. No Feishu network write was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
