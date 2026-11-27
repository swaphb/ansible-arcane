#!/usr/bin/env python3
"""Confirm pinned runtime container images resolve, pull, and pass runtime smoke tests."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANAGER_DEFAULTS = ROOT / "roles/arcane/defaults/main.yml"
AGENT_DEFAULTS = ROOT / "roles/arcane_agent/defaults/main.yml"
AGENT_COMPOSE_TEMPLATE = ROOT / "roles/arcane_agent/templates/compose.yml.j2"
DEBIAN_BOOTSTRAP_RELEASES = ("bookworm", "trixie")


def value(path: Path, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*[\"']?([^\"'\s#]+)", path.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"could not read {name} from {path.relative_to(ROOT)}")
    return match.group(1)


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )


def parse_agent_healthcheck_command() -> list[str] | None:
    """Extract an inline CMD healthcheck test from the agent Compose template if present."""
    template = AGENT_COMPOSE_TEMPLATE.read_text()
    match = re.search(r"^\s*healthcheck:\s*\n(?:\s+.*\n)*?\s+test:\s*\[([^\]]+)\]", template, re.MULTILINE)
    if not match:
        return None
    parts = [part.strip().strip("\"'") for part in match.group(1).split(",") if part.strip()]
    if parts and parts[0] == "CMD":
        return parts[1:]
    if parts and parts[0] == "CMD-SHELL" and len(parts) > 1:
        return ["/bin/sh", "-c", parts[1]]
    return parts or None


def smoke_test_agent_image(image: str, expected_version: str) -> None:
    """Verify the pinned Arcane Remote Agent image starts cleanly and matches readiness checks."""
    version_result = run("docker", "run", "--rm", image, "./arcane-agent", "version", capture=True)
    expected_semver = expected_version.lstrip("v")
    if expected_semver not in version_result.stdout:
        raise RuntimeError(
            f"{image}: expected version {expected_semver!r} in output, got: {version_result.stdout!r}"
        )

    container_id = run(
        "docker",
        "run",
        "-d",
        "-e",
        "ARCANE_AGENT=true",
        "-e",
        "ARCANE_AGENT_TRANSPORT=auto",
        "-e",
        "ARCANE_AGENT_TOKEN=arc_etkn_smoke_test",
        "-e",
        "ARCANE_MANAGER_API_URL=http://127.0.0.1:3552",
        image,
        capture=True,
    ).stdout.strip()

    try:
        initial_state = ""
        for _ in range(15):
            state = run(
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}} {{.State.Restarting}} {{.State.StartedAt}}",
                container_id,
                capture=True,
            ).stdout.strip()
            if state.startswith("running false "):
                initial_state = state
                break
            time.sleep(1)

        if not initial_state:
            logs = subprocess.run(
                ["docker", "logs", container_id],
                text=True,
                capture_output=True,
            )
            raise RuntimeError(
                f"{image}: container did not reach 'running false' state\n{logs.stdout}{logs.stderr}"
            )

        time.sleep(5)
        final_state = run(
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}} {{.State.Restarting}} {{.State.StartedAt}}",
            container_id,
            capture=True,
        ).stdout.strip()
        if final_state != initial_state:
            raise RuntimeError(
                f"{image}: container did not remain stable (initial={initial_state!r}, final={final_state!r})"
            )

        healthcheck_cmd = parse_agent_healthcheck_command()
        if healthcheck_cmd:
            probe = subprocess.run(
                ["docker", "exec", container_id, *healthcheck_cmd],
                text=True,
                capture_output=True,
            )
            if probe.returncode != 0:
                raise RuntimeError(
                    f"{image}: configured Compose healthcheck {healthcheck_cmd!r} failed "
                    f"(exit {probe.returncode}): {probe.stdout}{probe.stderr}"
                )
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"smoke-tested pinned agent image {image} ({expected_version})")


def smoke_test_debian_docker_bootstrap(releases: tuple[str, ...] = DEBIAN_BOOTSTRAP_RELEASES) -> None:
    """Verify official Docker APT repository setup and Compose v2+ CLI plugin on Debian releases."""
    bootstrap_script = r"""
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl >/dev/null
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
arch="$(dpkg --print-architecture)"
. /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian %s stable\n' \
  "$arch" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install --simulate -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
apt-get install -y -qq --no-install-recommends docker-ce-cli docker-compose-plugin >/dev/null
docker compose version
"""
    for release in releases:
        image = f"debian:{release}-slim"
        result = run("docker", "run", "--rm", image, "sh", "-c", bootstrap_script, capture=True)
        compose_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if not re.search(r"version v([2-9]|[1-9][0-9])\.", compose_line):
            raise RuntimeError(
                f"{image}: expected Docker Compose v2+ output, got: {result.stdout!r}"
            )
        print(f"validated Debian {release} Docker Compose bootstrap: {compose_line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-debian-bootstrap",
        action="store_true",
        help="skip Debian 12/13 Docker APT repository and Compose v2 bootstrap smoke tests",
    )
    args = parser.parse_args(argv)

    if not shutil.which("docker"):
        raise RuntimeError("docker is required to validate service images")

    agent_version = value(AGENT_DEFAULTS, "arcane_agent_version")
    agent_image = f"ghcr.io/getarcaneapp/agent:{agent_version}"
    images = tuple(dict.fromkeys((
        f"ghcr.io/getarcaneapp/manager:{value(MANAGER_DEFAULTS, 'arcane_version')}",
        agent_image,
        f"docker.io/fosrl/newt:{value(MANAGER_DEFAULTS, 'arcane_newt_version')}",
        f"docker.io/fosrl/pangolin-cli:{value(MANAGER_DEFAULTS, 'arcane_pangolin_cli_version')}",
        f"docker.io/fosrl/pangolin-cli:{value(AGENT_DEFAULTS, 'arcane_agent_pangolin_cli_version')}",
    )))
    for image in images:
        run("docker", "pull", image)

    smoke_test_agent_image(agent_image, agent_version)
    if not args.skip_debian_bootstrap:
        smoke_test_debian_docker_bootstrap()

    print("validated pinned service images and runtime smoke tests")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
