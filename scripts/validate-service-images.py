#!/usr/bin/env python3
"""Confirm pinned runtime container images resolve and can be pulled."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "roles/arcane/defaults/main.yml"


def value(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*[\"']?([^\"'\s#]+)", DEFAULTS.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"could not read {name} from defaults")
    return match.group(1)


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if not shutil.which("docker"):
        raise RuntimeError("docker is required to validate service images")
    images = (
        f"ghcr.io/getarcaneapp/manager:{value('arcane_version')}",
        f"docker.io/fosrl/newt:{value('arcane_newt_version')}",
    )
    for image in images:
        run("docker", "pull", image)
    print("validated pinned service images")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
