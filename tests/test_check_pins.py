"""Tests for .github/actions/check-pins/check_pins.py and its action wrapper."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / ".github" / "actions" / "check-pins" / "check_pins.py"
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
DIGEST = "a" * 64


def _run(workflow: str, action: str | None = None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "w.yml").write_text(workflow)
        if action is not None:
            d = root / ".github" / "actions" / "x"
            d.mkdir(parents=True)
            (d / "action.yml").write_text(action)
        return subprocess.run(
            [sys.executable, str(_SCRIPT), str(root)], capture_output=True, text=True
        )


def _step(uses: str) -> str:
    return f"jobs:\n  j:\n    steps:\n      - uses: {uses}\n"


class TestCheckPins(unittest.TestCase):
    def test_sha_with_version_comment_passes(self) -> None:
        self.assertEqual(_run(_step(f"actions/checkout@{SHA} # v7.0.1")).returncode, 0)
        self.assertEqual(_run(_step(f"actions/checkout@{SHA} # v7")).returncode, 0)

    def test_tag_fails_with_file_and_line(self) -> None:
        r = _run(_step("actions/checkout@v7"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(".github/workflows/w.yml:4: actions/checkout@v7", r.stderr)

    def test_branch_and_short_sha_fail(self) -> None:
        self.assertEqual(_run(_step("a/b@main")).returncode, 1)
        self.assertEqual(_run(_step(f"a/b@{SHA[:7]} # v1")).returncode, 1)

    def test_sha_without_version_comment_fails(self) -> None:
        r = _run(_step(f"a/b@{SHA}"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("version comment", r.stderr)
        self.assertEqual(_run(_step(f"a/b@{SHA} # latest")).returncode, 1)

    def test_local_refs_are_exempt(self) -> None:
        self.assertEqual(_run(_step("./.github/workflows/x.yml")).returncode, 0)

    def test_docker_needs_a_digest(self) -> None:
        self.assertEqual(_run(_step(f"docker://alpine@sha256:{DIGEST}")).returncode, 0)
        self.assertEqual(_run(_step("docker://alpine:3.20")).returncode, 1)

    def test_reusable_workflow_job_uses_is_checked(self) -> None:
        r = _run("jobs:\n  j:\n    uses: o/r/.github/workflows/f.yml@main\n")
        self.assertEqual(r.returncode, 1)

    def test_composite_action_files_are_scanned(self) -> None:
        r = _run(_step(f"a/b@{SHA} # v1"), action=_step("c/d@v2"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(".github/actions/x/action.yml:4", r.stderr)

    def test_commented_uses_is_ignored(self) -> None:
        self.assertEqual(_run("# uses: a/b@main\n" + _step(f"a/b@{SHA} # v1")).returncode, 0)

    def test_this_repository_is_fully_pinned(self) -> None:
        r = subprocess.run([sys.executable, str(_SCRIPT), str(_ROOT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestActionWrapper(unittest.TestCase):
    def test_composite_runs_the_script_and_ci_dogfoods_it(self) -> None:
        action = (_ROOT / ".github" / "actions" / "check-pins" / "action.yml").read_text()
        self.assertIn("using: composite", action)
        self.assertIn("check_pins.py", action)
        ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("uses: ./.github/actions/check-pins", ci)
        self.assertIn("name: actionlint + shellcheck", ci)


if __name__ == "__main__":
    unittest.main()
