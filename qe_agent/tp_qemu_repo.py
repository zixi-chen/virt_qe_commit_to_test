"""Clone or update the local tp-qemu test provider via git."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

DEFAULT_TP_QEMU_GIT_URL = "https://github.com/autotest/tp-qemu.git"


def _run_git(args: list[str], *, cwd: Optional[Path] = None, verbose: bool = False) -> None:
    if verbose:
        print(f"[tp-qemu] git {' '.join(args)}", file=sys.stderr)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed (exit {proc.returncode}): {err}")


def ensure_tp_qemu_repo(
    local_root: Path,
    repo_url: str,
    *,
    branch: Optional[str] = None,
    verbose: bool = False,
) -> None:
    """
    If local_root is missing: git clone repo_url into local_root.
    If local_root exists and is a git work tree: git pull --ff-only (optional branch checkout).
    """
    url = (repo_url or "").strip()
    if not url:
        raise ValueError("tp-qemu repo URL is empty; set TP_QEMU_GIT_URL or pass a non-empty URL.")

    root = Path(local_root).expanduser().resolve()
    branch = branch.strip() if branch else None

    if not root.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        clone_cmd = ["clone"]
        if branch:
            clone_cmd.extend(["-b", branch])
        clone_cmd.extend([url, str(root)])
        _run_git(clone_cmd, verbose=verbose)
        if verbose:
            print(f"[tp-qemu] cloned into {root}", file=sys.stderr)
        return

    git_dir = root / ".git"
    if not git_dir.exists():
        raise RuntimeError(
            f"{root} exists but is not a git checkout (no .git). "
            "Remove the directory or point --tests-root to a valid tp-qemu clone."
        )

    if branch:
        _run_git(["-C", str(root), "fetch", "origin", branch], verbose=verbose)
        _run_git(["-C", str(root), "checkout", branch], verbose=verbose)
        _run_git(["-C", str(root), "pull", "--ff-only", "origin", branch], verbose=verbose)
    else:
        _run_git(["-C", str(root), "pull", "--ff-only"], verbose=verbose)

    if verbose:
        print(f"[tp-qemu] updated {root}", file=sys.stderr)


def should_sync_tp_qemu() -> bool:
    """Sync by default; set TP_QEMU_AUTO_SYNC=0/false/no/off to skip (e.g. offline)."""
    raw = os.getenv("TP_QEMU_AUTO_SYNC")
    if raw is None or not str(raw).strip():
        return True
    lower = str(raw).strip().lower()
    return lower not in ("0", "false", "no", "off")
