#!/usr/bin/env python3
"""Render and validate supported Arcane Manager and Agent Compose configurations and Nix flake."""

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


def make_jinja_env(templates_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    environment.filters["to_json"] = json.dumps
    environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
    return environment


def render_manager(values: dict[str, object]) -> dict[str, object]:
    defaults = yaml.safe_load((ROOT / "roles/arcane/defaults/main.yml").read_text())
    defaults.update(values)
    defaults.update({
        "arcane_app_url": values.get("arcane_app_url", "http://192.168.1.120:3552"),
        "arcane_effective_puid": "1000",
        "arcane_effective_pgid": "1000",
        "arcane_effective_encryption_key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "arcane_newt_secret": "validation-newt-secret",
        "arcane_pangolin_client_secret": "validation-pangolin-client-secret",
    })
    environment = make_jinja_env(ROOT / "roles/arcane/templates")
    text = environment.get_template("compose.yml.j2").render(**defaults)
    return yaml.safe_load(text)


def render_agent(values: dict[str, object]) -> dict[str, object]:
    defaults = yaml.safe_load((ROOT / "roles/arcane_agent/defaults/main.yml").read_text())
    defaults.update(values)
    defaults.update({
        "arcane_agent_manager_api_url": values.get("arcane_agent_manager_api_url", "https://arcane.internal"),
        "arcane_agent_token": values.get("arcane_agent_token", "arc_etkn_validation_token"),
        "arcane_agent_pangolin_endpoint": values.get("arcane_agent_pangolin_endpoint", "https://pangolin.example.test"),
        "arcane_agent_pangolin_client_id": values.get("arcane_agent_pangolin_client_id", "validation-agent-client"),
        "arcane_agent_pangolin_client_secret": values.get(
            "arcane_agent_pangolin_client_secret", "validation-agent-pangolin-secret"
        ),
    })
    environment = make_jinja_env(ROOT / "roles/arcane_agent/templates")
    text = environment.get_template("compose.yml.j2").render(**defaults)
    return yaml.safe_load(text)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_manager_variant(name: str, values: dict[str, object]) -> None:
    compose = render_manager(values)
    services = compose.get("services", {})
    is_lxc = bool(values.get("arcane_lxc_mode", True))
    is_newt = bool(values.get("arcane_newt_enabled", False))
    is_pangolin_client = bool(values.get("arcane_pangolin_client_enabled", False))

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

    if is_pangolin_client:
        require("pangolin-client" in services, f"{name}: pangolin-client service is missing")
        client = services["pangolin-client"]
        require("NET_ADMIN" in client.get("cap_add", []), f"{name}: pangolin-client needs NET_ADMIN")
        require("/dev/net/tun:/dev/net/tun" in client.get("devices", []), f"{name}: pangolin-client needs /dev/net/tun")
        require(
            arcane.get("network_mode") == "service:pangolin-client",
            f"{name}: Arcane must share pangolin-client network namespace",
        )
        require("ports" not in arcane, f"{name}: Arcane must not define ports with service network_mode")
        require("networks" not in arcane, f"{name}: Arcane must not define networks with service network_mode")
        if is_newt:
            require("ports" not in client, f"{name}: Newt mode must not publish pangolin-client ports")
            aliases = client.get("networks", {}).get("arcane-newt", {}).get("aliases", [])
            require("arcane" in aliases, f"{name}: pangolin-client must alias arcane on arcane-newt")
        else:
            require("ports" in client, f"{name}: direct mode must publish Arcane port on pangolin-client")
    else:
        require("pangolin-client" not in services, f"{name}: unexpected pangolin-client service")
        if is_newt:
            require("ports" not in arcane, f"{name}: Newt mode must not publish Arcane ports")
        else:
            require("ports" in arcane, f"{name}: direct mode must publish Arcane port")

    if is_newt:
        require("newt" in services, f"{name}: Newt service is missing")
        require("ports" not in services["newt"], f"{name}: Newt must not publish ports")
        require("arcane-newt" in compose.get("networks", {}), f"{name}: arcane-newt network is missing")
        require("newt-config" in compose.get("secrets", {}), f"{name}: newt-config secret is missing")
    else:
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


def validate_agent_variant(name: str, values: dict[str, object]) -> None:
    compose = render_agent(values)
    services = compose.get("services", {})
    is_pangolin = bool(values.get("arcane_agent_pangolin_enabled", True))
    is_lxc = bool(values.get("arcane_agent_lxc_mode", False))
    override_dns = bool(values.get("arcane_agent_pangolin_override_dns", True))

    require("arcane-agent" in services, f"{name}: arcane-agent service is missing")
    agent = services["arcane-agent"]
    volumes = agent.get("volumes", [])
    env = agent.get("environment", {})

    require(
        str(agent.get("image", "")).startswith("ghcr.io/getarcaneapp/agent:"),
        f"{name}: arcane-agent must use ghcr.io/getarcaneapp/agent image",
    )
    require(agent.get("container_name") == "arcane-agent", f"{name}: unexpected container_name")
    require(agent.get("restart") == "unless-stopped", f"{name}: restart policy must be unless-stopped")
    require("ports" not in agent, f"{name}: arcane-agent must not publish host ports")
    require("/var/run/docker.sock:/var/run/docker.sock" in volumes, f"{name}: docker.sock mount missing")
    require("arcane-agent-data:/app/data" in volumes, f"{name}: arcane-agent-data volume mount missing")
    require("arcane-agent-data" in compose.get("volumes", {}), f"{name}: arcane-agent-data volume missing")
    require(
        "healthcheck" not in agent,
        f"{name}: arcane-agent must not define a healthcheck unsupported by the pinned release",
    )
    for key in ("ARCANE_AGENT", "ARCANE_AGENT_TRANSPORT", "ARCANE_AGENT_TOKEN", "ARCANE_MANAGER_API_URL"):
        require(key in env, f"{name}: missing required env {key}")

    if is_lxc:
        require(agent.get("pid") == "host", f"{name}: LXC agent mode must set pid: host")
        require("/proc:/proc" in volumes, f"{name}: LXC agent mode must mount /proc:/proc")
    else:
        require("pid" not in agent, f"{name}: non-LXC agent mode must not set pid: host")
        require("/proc:/proc" not in volumes, f"{name}: non-LXC agent mode must not mount /proc:/proc")

    if is_pangolin:
        require("pangolin-client" in services, f"{name}: pangolin-client service is missing")
        client = services["pangolin-client"]
        client_env = client.get("environment", {})
        client_cmd = client.get("command", [])
        require(client.get("container_name") == "arcane-agent-pangolin", f"{name}: unexpected pangolin container_name")
        require("ports" not in client, f"{name}: pangolin-client must not publish host ports")
        require("NET_ADMIN" in client.get("cap_add", []), f"{name}: pangolin-client needs NET_ADMIN")
        require("/dev/net/tun:/dev/net/tun" in client.get("devices", []), f"{name}: pangolin-client needs /dev/net/tun")
        require(
            client_env.get("PANGOLIN_OVERRIDE_DNS") == ("true" if override_dns else "false"),
            f"{name}: PANGOLIN_OVERRIDE_DNS mismatch",
        )
        require(
            f"--override-dns={'true' if override_dns else 'false'}" in client_cmd,
            f"{name}: pangolin-client command must set --override-dns",
        )
        require(
            "arcane-agent-pangolin" in compose.get("networks", {}),
            f"{name}: arcane-agent-pangolin network is missing",
        )
        require(
            agent.get("network_mode") == "service:pangolin-client",
            f"{name}: arcane-agent must share pangolin-client network namespace",
        )
        require(
            "pangolin-client" in agent.get("depends_on", []),
            f"{name}: arcane-agent must depend_on pangolin-client",
        )
        if values.get("arcane_agent_pangolin_upstream_dns"):
            require("PANGOLIN_UPSTREAM_DNS" in client_env, f"{name}: PANGOLIN_UPSTREAM_DNS missing")
        if values.get("arcane_agent_pangolin_match_domains"):
            require("PANGOLIN_MATCH_DOMAINS" in client_env, f"{name}: PANGOLIN_MATCH_DOMAINS missing")
        if values.get("arcane_agent_pangolin_tunnel_dns"):
            require(client_env.get("PANGOLIN_TUNNEL_DNS") == "true", f"{name}: PANGOLIN_TUNNEL_DNS missing")
    else:
        require("pangolin-client" not in services, f"{name}: unexpected pangolin-client service")
        require("network_mode" not in agent, f"{name}: direct agent must not set network_mode")
        require("networks" not in compose, f"{name}: direct agent must not define pangolin network")


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
    for playbook in ("arcane-manager.yml", "arcane-agents.yml"):
        subprocess.run([str(ansible), "--syntax-check", playbook], cwd=ROOT, check=True, env=environment)

    common_newt = {
        "arcane_newt_enabled": True,
        "arcane_newt_pangolin_endpoint": "https://pangolin.example.test",
        "arcane_newt_id": "validation-site",
    }
    common_pangolin_client = {
        "arcane_pangolin_client_enabled": True,
        "arcane_pangolin_client_endpoint": "https://pangolin.example.test",
        "arcane_pangolin_client_id": "validation-machine-client",
    }
    manager_variants = {
        "lxc-direct": {"arcane_lxc_mode": True},
        "lxc-newt": {"arcane_lxc_mode": True, **common_newt},
        "lxc-newt-pangolin-client": {"arcane_lxc_mode": True, **common_newt, **common_pangolin_client},
        "vm-direct": {"arcane_lxc_mode": False},
        "vm-direct-pangolin-client": {"arcane_lxc_mode": False, **common_pangolin_client},
        "vm-newt": {"arcane_lxc_mode": False, **common_newt},
        "lxc-full-options": {
            "arcane_lxc_mode": True,
            "arcane_builds_dir": "/srv/arcane/builds",
            "arcane_backups_dir": "/srv/arcane/backups",
            "arcane_database_url": "postgres://arcane:secret@db.internal:5432/arcane",
            "arcane_extra_env": {"LOG_LEVEL": "info"},
        },
    }
    for name, values in manager_variants.items():
        validate_manager_variant(name, values)

    agent_variants = {
        "agent-pangolin": {
            "arcane_agent_pangolin_enabled": True,
            "arcane_agent_lxc_mode": False,
        },
        "agent-pangolin-lxc": {
            "arcane_agent_pangolin_enabled": True,
            "arcane_agent_lxc_mode": True,
            "arcane_agent_pangolin_override_dns": True,
            "arcane_agent_pangolin_upstream_dns": "1.1.1.1,9.9.9.9",
            "arcane_agent_pangolin_match_domains": "*.internal",
            "arcane_agent_pangolin_tunnel_dns": True,
            "arcane_agent_extra_env": {"LOG_LEVEL": "debug"},
        },
        "agent-direct": {
            "arcane_agent_pangolin_enabled": False,
            "arcane_agent_lxc_mode": False,
        },
    }
    for name, values in agent_variants.items():
        validate_agent_variant(name, values)

    validate_flake(require_nix=args.require_nix)
    total_variants = len(manager_variants) + len(agent_variants)
    print(f"validated {total_variants} Compose variants, {len(yaml_files)} YAML files, and Nix flake")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
