#!/usr/bin/env python3
"""Discover stable service releases and synchronize this repository's pins.

The script deliberately updates files only with --apply.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "automation/service-pins.json"
DEFAULTS = ROOT / "roles/arcane/defaults/main.yml"
EXAMPLE = ROOT / "group_vars/arcane.yml.example"
README = ROOT / "README.md"


def release_key(tag: str) -> tuple[int, ...] | None:
    """Return a numeric key for stable SemVer-like release tags."""
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", tag)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def latest_release(repository: str, prefix: str, discovery: str = "releases") -> str:
    endpoint = "tags" if discovery == "tags" else "releases"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/{endpoint}?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ansible-arcane-updater"},
    )
    releases = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                releases = json.load(response)
            break
        except (http.client.HTTPException, urllib.error.URLError) as error:
            if attempt == 2:
                raise RuntimeError(f"could not query {endpoint} for {repository}: {error}") from error
            time.sleep(attempt + 1)
    assert releases is not None

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


def update_pin(old: str, new: str) -> None:
    for path in (DEFAULTS, EXAMPLE, README):
        replace_exact(path, old, new)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write discovered pins into tracked files")
    parser.add_argument("--report", type=Path, help="write a JSON update report")
    args = parser.parse_args()

    catalog = json.loads(CATALOG.read_text())["services"]
    report: dict[str, object] = {"updated": {}, "unchanged": {}, "skipped": {}}

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
            update_pin(old, latest)
        report["updated"][name] = {"from": old, "to": latest}

    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
