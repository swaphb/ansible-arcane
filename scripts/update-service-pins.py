#!/usr/bin/env python3
"""Discover stable service releases and synchronize this repository's pins.

The script deliberately updates files only with --apply.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "automation/service-pins.json"
DEFAULTS = ROOT / "roles/arcane/defaults/main.yml"
DEFAULT_PIN_FILES = (
    "roles/arcane/defaults/main.yml",
    "group_vars/arcane_manager.yml.example",
    "README.md",
)
FLAKE_LOCK = ROOT / "flake.lock"


def github_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ansible-arcane-updater"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (http.client.HTTPException, urllib.error.URLError) as error:
            if attempt == 2:
                raise RuntimeError(f"could not query {url}: {error}") from error
            time.sleep(attempt + 1)
    raise RuntimeError(f"could not query {url}")


def release_key(tag: str) -> tuple[int, ...] | None:
    """Return a numeric key for stable SemVer-like release tags."""
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", tag)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def latest_release(repository: str, prefix: str, discovery: str = "releases") -> str:
    endpoint = "tags" if discovery == "tags" else "releases"
    releases = github_json(f"https://api.github.com/repos/{repository}/{endpoint}?per_page=100")
    if not isinstance(releases, list):
        raise RuntimeError(f"unexpected GitHub API response for {repository}/{endpoint}")

    candidates: list[tuple[tuple[int, ...], str]] = []
    for release in releases:
        tag = release.get("name", "") if discovery == "tags" else release.get("tag_name", "")
        if discovery == "releases" and (release.get("draft") or release.get("prerelease")):
            continue
        if prefix and not tag.startswith(prefix):
            continue
        key = release_key(tag)
        if key is not None:
            candidates.append((key, tag))
    if not candidates:
        raise RuntimeError(f"no stable SemVer release was found for {repository}")
    return max(candidates)[1]


def current_value(variable: str) -> str:
    match = re.search(rf"^{re.escape(variable)}:\s*[\"']?([^\"'\s#]+)", DEFAULTS.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"could not find {variable} in {DEFAULTS.relative_to(ROOT)}")
    return match.group(1)


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f"expected {old!r} in {path.relative_to(ROOT)}")
    path.write_text(text.replace(old, new))


def update_pin(old: str, new: str, relative_paths: list[str] | tuple[str, ...] = DEFAULT_PIN_FILES) -> None:
    for rel in relative_paths:
        replace_exact(ROOT / rel, old, new)


def locked_nixpkgs_rev() -> tuple[str, str, str, str]:
    lock = json.loads(FLAKE_LOCK.read_text())
    node = lock.get("nodes", {}).get("nixpkgs", {})
    locked = node.get("locked", {})
    original = node.get("original", {})
    rev = str(locked.get("rev", ""))
    owner = str(original.get("owner", "NixOS"))
    repo = str(original.get("repo", "nixpkgs"))
    ref = str(original.get("ref", "nixpkgs-unstable"))
    if not rev:
        raise RuntimeError("could not find nixpkgs locked rev in flake.lock")
    return rev, owner, repo, ref


def update_flake_lock(
    apply_changes: bool,
    report: dict[str, dict[str, object]],
    require_nix: bool = False,
) -> None:
    if not FLAKE_LOCK.exists():
        return
    old_rev, owner, repo, ref = locked_nixpkgs_rev()
    if apply_changes:
        nix = shutil.which("nix")
        if not nix:
            if require_nix:
                raise RuntimeError("nix is required to update flake.lock")
            report["skipped"]["nixpkgs"] = "nix is not installed; cannot refresh flake.lock"
            return
        subprocess.run(
            [nix, "--extra-experimental-features", "nix-command flakes", "flake", "update"],
            cwd=ROOT,
            check=True,
        )
        new_rev, _, _, _ = locked_nixpkgs_rev()
        if new_rev == old_rev:
            report["unchanged"]["nixpkgs"] = old_rev[:12]
        else:
            report["updated"]["nixpkgs"] = {"from": old_rev[:12], "to": new_rev[:12]}
        return

    commit = github_json(f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}")
    if not isinstance(commit, dict) or not commit.get("sha"):
        raise RuntimeError(f"could not resolve latest commit for {owner}/{repo}@{ref}")
    latest_rev = str(commit["sha"])
    if latest_rev == old_rev:
        report["unchanged"]["nixpkgs"] = old_rev[:12]
    else:
        report["updated"]["nixpkgs"] = {"from": old_rev[:12], "to": latest_rev[:12]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write discovered pins into tracked files")
    parser.add_argument(
        "--require-nix",
        action="store_true",
        help="fail when --apply is set and nix is unavailable to update flake.lock",
    )
    parser.add_argument("--report", type=Path, help="write a JSON update report")
    args = parser.parse_args(argv)

    if args.apply and args.require_nix and shutil.which("nix") is None:
        raise RuntimeError("nix is required to update flake.lock")

    catalog = json.loads(CATALOG.read_text())["services"]
    report: dict[str, dict[str, object]] = {"updated": {}, "unchanged": {}, "skipped": {}}

    for name, service in catalog.items():
        variable = service["variable"]
        old = current_value(variable)
        latest_release_tag = latest_release(
            service["github_repository"], service["tag_prefix"], service.get("discovery", "releases")
        )
        latest = latest_release_tag.removeprefix("v") if service.get("strip_leading_v") else latest_release_tag
        if latest == old:
            report["unchanged"][name] = old
            continue

        if args.apply:
            update_pin(old, latest, service.get("files", DEFAULT_PIN_FILES))
        report["updated"][name] = {"from": old, "to": latest}

    update_flake_lock(args.apply, report, require_nix=args.require_nix)

    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
