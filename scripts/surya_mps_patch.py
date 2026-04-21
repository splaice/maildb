"""Apply / revert / status the surya MPS .max() crash fix locally.

Vendors the diff from https://github.com/datalab-to/surya/pull/493 against
the surya-ocr install in the active virtualenv. Necessary because the fix
hasn't shipped in any tagged release yet (latest published surya-ocr is
0.17.1 as of this writing).

Usage:
    uv run python scripts/surya_mps_patch.py status
    uv run python scripts/surya_mps_patch.py apply
    uv run python scripts/surya_mps_patch.py revert

The script is idempotent. `apply` is a no-op if already applied; `revert`
is a no-op if not applied. Re-run `apply` after any `uv sync` that
reinstalls surya-ocr.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_FILE = REPO_ROOT / "scripts" / "patches" / "surya-mps-fix.patch"
SENTINEL = "def safe_max_item("  # added by the patch in surya/common/util.py


def surya_root() -> Path:
    """Path to the installed surya package (parent of `surya/`).

    `patch -p1` is run from this directory because the diff paths are
    `surya/common/...` (one leading component to strip).
    """
    files = metadata.files("surya-ocr")
    if files is None:
        msg = "surya-ocr is not installed in this environment"
        raise SystemExit(msg)
    for f in files:
        if str(f).startswith("surya/__init__.py"):
            # f is relative to site-packages
            return f.locate().resolve().parent.parent
    msg = "could not locate surya/__init__.py inside surya-ocr's metadata"
    raise SystemExit(msg)


def is_applied(root: Path) -> bool:
    util = root / "surya" / "common" / "util.py"
    if not util.exists():
        return False
    return SENTINEL in util.read_text(encoding="utf-8")


def cmd_status() -> int:
    root = surya_root()
    applied = is_applied(root)
    print(f"surya install:  {root}")
    print(f"surya version:  {metadata.version('surya-ocr')}")
    print(f"patch source:   {PATCH_FILE.relative_to(REPO_ROOT)}")
    print(f"patch applied:  {'YES' if applied else 'NO'}")
    return 0 if applied else 1


def _run_patch(root: Path, *extra: str) -> subprocess.CompletedProcess:
    cmd = ["patch", "-p1", "-i", str(PATCH_FILE), *extra]
    return subprocess.run(cmd, cwd=root, check=False, capture_output=True, text=True)


def cmd_apply() -> int:
    root = surya_root()
    if is_applied(root):
        print("already applied; nothing to do")
        return 0

    # Dry run first to catch context mismatches before touching files
    dry = _run_patch(root, "--dry-run")
    if dry.returncode != 0:
        print("patch dry-run failed (surya version may have moved):", file=sys.stderr)
        print(dry.stdout, file=sys.stderr)
        print(dry.stderr, file=sys.stderr)
        return 2

    real = _run_patch(root)
    if real.returncode != 0:
        print("patch apply failed:", file=sys.stderr)
        print(real.stdout, file=sys.stderr)
        print(real.stderr, file=sys.stderr)
        return 2

    if not is_applied(root):
        print("patch returned 0 but sentinel not found — aborting", file=sys.stderr)
        return 3
    print("applied surya MPS fix to", root)
    return 0


def cmd_revert() -> int:
    root = surya_root()
    if not is_applied(root):
        print("patch is not applied; nothing to do")
        return 0

    real = _run_patch(root, "-R")
    if real.returncode != 0:
        print("patch revert failed:", file=sys.stderr)
        print(real.stdout, file=sys.stderr)
        print(real.stderr, file=sys.stderr)
        return 2

    if is_applied(root):
        print("patch reported success but sentinel still present", file=sys.stderr)
        return 3
    print("reverted surya MPS fix from", root)
    return 0


def main() -> int:
    if not shutil.which("patch"):
        print("the `patch` binary is required", file=sys.stderr)
        return 4
    if not PATCH_FILE.exists():
        print(f"patch file missing: {PATCH_FILE}", file=sys.stderr)
        return 4

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "apply", "revert"])
    args = parser.parse_args()

    return {
        "status": cmd_status,
        "apply": cmd_apply,
        "revert": cmd_revert,
    }[args.action]()


if __name__ == "__main__":
    sys.exit(main())
