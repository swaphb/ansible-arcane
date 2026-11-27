#!/usr/bin/env python3
"""Render and validate supported Arcane Compose configurations and Nix flake."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


ROOT = Path(__file__).resolve().parent.parent


def render(values: dict[str, object]) -> dict[str, object]:
    defaults = yaml.safe_load((ROOT / "roles/arcane/defaults/main.yml").read_text())
    defaults.update(values)
    defaults.update({
        "arcane_app_url": values.get("arcane_app_url", "http://192.168.1.120:3552"),
        "arcane_effective_puid": "1000",
        "arcane_effective_pgid": "1000",
        "arcane_effective_encryption_key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "arcane_newt_secret": "validation-newt-secret",
    })
    environment = Environment(
        loader=FileSystemLoader(str(ROOT / "roles/arcane/templates")),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    environment.filters["to_json"] = json.dumps
    environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    text = environment.get_template("compose.yml.j2").render(**defaults)
    return yaml.safe_load(text)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_variant(name: str, values: dict[str, object]) -> None:
    compose = render(values)
    services = compose.get("services", {})
    is_lxc = bool(values.get("arcane_lxc_mode", True))
    is_newt = bool(values.get("arcane_newt_enabled", False))

    require("arcane" in services, f"{name}: Arcane service is missing")
    arcane = services["arcane"]
    volumes = arcane.get("volumes", [])

    if is_lxc:
        require(
            "/sys/fs/cgroup:/sys/fs/cgroup:ro" in volumes,
            f"{name}: LXC mode must mount /sys/fs/cgroup:ro",
        )
        require("cgroup" not in arcane, f"{name}: LXC mode must not set cgroup: host")
    else:
        require(arcane.get("cgroup") == "host", f"{name}: VM mode must set cgroup: host")
        require(
            "/sys/fs/cgroup:/sys/fs/cgroup:ro" not in volumes,
            f"{name}: VM mode must not mount /sys/fs/cgroup:ro",
        )

    if is_newt:
        require("ports" not in arcane, f"{name}: Newt mode must not publish Arcane ports")
        require("newt" in services, f"{name}: Newt service is missing")
        require("arcane-newt" in compose.get("networks", {}), f"{name}: arcane-newt network is missing")
        require("newt-config" in compose.get("secrets", {}), f"{name}: newt-config secret is missing")
    else:
        require("ports" in arcane, f"{name}: direct mode must publish Arcane port")
        require("newt" not in services, f"{name}: direct mode must not include Newt service")

    if values.get("arcane_builds_dir"):
        require(f"{values['arcane_builds_dir']}:/builds" in volumes, f"{name}: builds mount missing")
    if values.get("arcane_backups_dir"):
        require(f"{values['arcane_backups_dir']}:/backups" in volumes, f"{name}: backups mount missing")
    if values.get("arcane_database_url"):
        require(
            "ARCANE_DATABASE_URL" in arcane.get("environment", {}),
            f"{name}: external database URL missing",
        )


def validate_flake(require_nix: bool = False) -> None:
    flake_nix = ROOT / "flake.nix"
    flake_lock = ROOT / "flake.lock"
    require(flake_nix.exists(), "flake.nix is missing")
    require(flake_lock.exists(), "flake.lock is missing")

    lock = json.loads(flake_lock.read_text())
    nixpkgs = lock.get("nodes", {}).get("nixpkgs", {}).get("locked", {})
    require(bool(nixpkgs.get("rev")), "flake.lock is missing nixpkgs locked rev")
    require(bool(nixpkgs.get("narHash")), "flake.lock is missing nixpkgs locked narHash")

    nix = shutil.which("nix")
    if not nix:
        if require_nix:
            raise RuntimeError("nix is required for flake validation")
        return
    subprocess.run(
        [nix, "--extra-experimental-features", "nix-command flakes", "flake", "check", "--no-build", "--all-systems"],
        cwd=ROOT,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-nix",
        action="store_true",
        help="fail if nix is not available for flake validation",
    )
    args = parser.parse_args(argv)

    yaml_files = sorted(
        path for path in ROOT.rglob("*")
        if path.suffix in {".yml", ".yaml"}
        and ".git" not in path.parts
    )
    for path in yaml_files:
        yaml.safe_load(path.read_text())

    ansible = Path(sys.executable).with_name("ansible-playbook")
    if not ansible.exists():
        discovered = shutil.which("ansible-playbook")
        ansible = Path(discovered) if discovered else ansible
    if not ansible.exists():
        raise RuntimeError("ansible-playbook is required for syntax validation")
    environment = os.environ.copy()
    environment.setdefault("ANSIBLE_LOCAL_TEMP", "/tmp/ansible-local")
    Path(environment["ANSIBLE_LOCAL_TEMP"]).mkdir(parents=True, exist_ok=True)
    subprocess.run([str(ansible), "--syntax-check", "site.yml"], cwd=ROOT, check=True, env=environment)

    common_newt = {
        "arcane_newt_enabled": True,
        "arcane_newt_pangolin_endpoint": "https://pangolin.example.test",
        "arcane_newt_id": "validation-site",
    }
    variants = {
        "lxc-direct": {"arcane_lxc_mode": True},
        "lxc-newt": {"arcane_lxc_mode": True, **common_newt},
        "vm-direct": {"arcane_lxc_mode": False},
        "vm-newt": {"arcane_lxc_mode": False, **common_newt},
        "lxc-full-options": {
            "arcane_lxc_mode": True,
            "arcane_builds_dir": "/srv/arcane/builds",
            "arcane_backups_dir": "/srv/arcane/backups",
            "arcane_database_url": "postgres://arcane:secret@db.internal:5432/arcane",
            "arcane_extra_env": {"LOG_LEVEL": "info"},
        },
    }
    for name, values in variants.items():
        validate_variant(name, values)
    validate_flake(require_nix=args.require_nix)
    print(f"validated {len(variants)} Compose variants, {len(yaml_files)} YAML files, and Nix flake")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
