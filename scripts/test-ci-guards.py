#!/usr/bin/env python3
"""Regression tests for CI Nix installation and --require-nix enforcement."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
WORKFLOWS_DIR = ROOT / ".gitea/workflows"


def load_script_module(name: str, path: Path, *, stub_deps: bool = False) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    if not stub_deps:
        spec.loader.exec_module(module)
        return module

    stubs: dict[str, types.ModuleType] = {}
    for dep in ("yaml", "jinja2"):
        if dep not in sys.modules:
            try:
                __import__(dep)
            except ImportError:
                stub = types.ModuleType(dep)
                if dep == "yaml":
                    stub.YAMLError = Exception  # type: ignore[attr-defined]
                if dep == "jinja2":
                    stub.Environment = object  # type: ignore[attr-defined]
                    stub.FileSystemLoader = object  # type: ignore[attr-defined]
                    stub.StrictUndefined = object  # type: ignore[attr-defined]
                stubs[dep] = stub

    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


def find_validation_python() -> str:
    """Return a Python executable that has PyYAML and Jinja2 installed."""
    candidates: list[str] = [sys.executable]
    ansible = shutil.which("ansible-playbook")
    if ansible:
        first_line = Path(ansible).read_text(errors="ignore").splitlines()[0]
        if first_line.startswith("#!"):
            shebang_parts = first_line[2:].strip().split()
            if shebang_parts:
                if Path(shebang_parts[0]).name == "env" and len(shebang_parts) > 1:
                    resolved = shutil.which(shebang_parts[1])
                    if resolved:
                        candidates.append(resolved)
                else:
                    candidates.append(shebang_parts[0])

    for candidate in candidates:
        probe = subprocess.run(
            [candidate, "-c", "import yaml, jinja2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            return candidate
    raise RuntimeError("could not find a Python interpreter with PyYAML and Jinja2")


def find_ansible_playbook(python_executable: str) -> Path:
    sibling = Path(python_executable).with_name("ansible-playbook")
    if sibling.exists():
        return sibling
    discovered = shutil.which("ansible-playbook")
    if discovered:
        return Path(discovered)
    raise RuntimeError("ansible-playbook is required to run CLI regression tests")


class TestValidateComposeVariantsNixGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script_module(
            "validate_compose_variants",
            SCRIPTS_DIR / "validate-compose-variants.py",
            stub_deps=True,
        )

    def test_validate_flake_requires_nix_when_flag_enabled(self) -> None:
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "nix is required for flake validation"):
                self.module.validate_flake(require_nix=True)

    def test_validate_flake_allows_missing_nix_by_default(self) -> None:
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            self.module.validate_flake(require_nix=False)

    def test_cli_require_nix_fails_when_nix_is_missing(self) -> None:
        python_executable = find_validation_python()
        ansible_playbook = find_ansible_playbook(python_executable)

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = Path(tmpdir)
            (bin_dir / "ansible-playbook").symlink_to(ansible_playbook)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir)

            result = subprocess.run(
                [python_executable, str(SCRIPTS_DIR / "validate-compose-variants.py"), "--require-nix"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0, msg=f"expected non-zero exit, stdout={result.stdout!r}")
        self.assertIn("nix is required for flake validation", result.stderr)


class TestUpdateServicePinsNixGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script_module(
            "update_service_pins",
            SCRIPTS_DIR / "update-service-pins.py",
        )

    def test_update_flake_lock_requires_nix_when_apply_and_require_nix(self) -> None:
        report: dict[str, dict[str, object]] = {"updated": {}, "unchanged": {}, "skipped": {}}
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "nix is required to update flake.lock"):
                self.module.update_flake_lock(apply_changes=True, report=report, require_nix=True)

    def test_update_flake_lock_skips_when_require_nix_is_false(self) -> None:
        report: dict[str, dict[str, object]] = {"updated": {}, "unchanged": {}, "skipped": {}}
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            self.module.update_flake_lock(apply_changes=True, report=report, require_nix=False)
        self.assertIn("nixpkgs", report["skipped"])

    def test_cli_apply_require_nix_fails_when_nix_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as empty_path:
            env = os.environ.copy()
            env["PATH"] = empty_path

            result = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "update-service-pins.py"), "--apply", "--require-nix"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0, msg=f"expected non-zero exit, stdout={result.stdout!r}")
        self.assertIn("nix is required to update flake.lock", result.stderr)


class TestWorkflowHardening(unittest.TestCase):
    WORKFLOWS = ("validate.yml", "service-updates.yml")

    def test_workflows_do_not_pipe_curl_into_sh_and_verify_nix(self) -> None:
        curl_pipe_sh = re.compile(r"curl[^\n]*\|[^\n]*\bsh\b")
        for filename in self.WORKFLOWS:
            text = (WORKFLOWS_DIR / filename).read_text()
            with self.subTest(workflow=filename):
                self.assertIsNone(
                    curl_pipe_sh.search(text),
                    f"{filename} must not pipe curl directly into sh",
                )
                self.assertIn(
                    "curl -fsSL https://nixos.org/nix/install -o /tmp/nix-install.sh",
                    text,
                )
                self.assertIn("test -s /tmp/nix-install.sh", text)
                self.assertIn("USER=root sh /tmp/nix-install.sh --no-daemon --yes", text)
                self.assertIn("rm -f /tmp/nix-install.sh", text)
                self.assertIn("nix --version", text)
                self.assertRegex(
                    text,
                    r"scripts/validate-compose-variants\.py[^\n]*--require-nix",
                )

    def test_service_updates_workflow_passes_require_nix_to_updater(self) -> None:
        text = (WORKFLOWS_DIR / "service-updates.yml").read_text()
        self.assertRegex(
            text,
            r"scripts/update-service-pins\.py[\s\\]+[^\n]*--apply[^\n]*--require-nix",
        )

    def test_validate_workflow_runs_ci_guard_tests(self) -> None:
        text = (WORKFLOWS_DIR / "validate.yml").read_text()
        self.assertIn("scripts/test-ci-guards.py", text)


if __name__ == "__main__":
    unittest.main()
