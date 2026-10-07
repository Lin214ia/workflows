"""Pins the essential shape of .github/workflows/release-please.yml.

The workflow only executes on GitHub, so what can be checked here is the
contract consumers depend on: release type, first version, a moving major tag,
and that every `uses:` is pinned to a full commit SHA.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = (_ROOT / ".github" / "workflows" / "release-please.yml").read_text()
_CONFIG = json.loads((_ROOT / "release-please-config.json").read_text())
_MANIFEST = json.loads((_ROOT / ".release-please-manifest.json").read_text())
_CI = (_ROOT / ".github" / "workflows" / "ci.yml").read_text()


class TestReleaseConfig(unittest.TestCase):
    def test_simple_release_starting_at_1_0_0(self) -> None:
        pkg = _CONFIG["packages"]["."]
        self.assertEqual(pkg["release-type"], "simple")
        self.assertEqual(pkg["initial-version"], "1.0.0")

    def test_tags_carry_no_component_prefix(self) -> None:
        # Consumers and the v<major> job both rely on plain `vX.Y.Z`.
        self.assertIs(_CONFIG["include-component-in-tag"], False)

    def test_manifest_is_empty_or_carries_a_semver_for_the_root(self) -> None:
        # Empty before the first release; the release PR then writes {".": "X.Y.Z"}.
        # Both states must pass, or every release PR would fail its own CI.
        self.assertLessEqual(set(_MANIFEST), {"."})
        if "." in _MANIFEST:
            self.assertRegex(_MANIFEST["."], r"^\d+\.\d+\.\d+$")


class TestReleaseWorkflow(unittest.TestCase):
    def test_runs_on_push_to_main_only(self) -> None:
        self.assertRegex(_WORKFLOW, r"(?m)^on:\n  push:\n    branches: \[main\]\n")

    def test_every_action_is_pinned_to_a_full_sha_with_a_version_comment(self) -> None:
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)(.*)$", _WORKFLOW)
        self.assertTrue(uses)
        for ref, rest in uses:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"@[0-9a-f]{40}$")
                self.assertRegex(rest, r"#\s*v\d")

    def test_moves_the_major_tag_after_a_release(self) -> None:
        self.assertIn("git tag -f", _WORKFLOW)
        self.assertIn('git push --force origin "refs/tags/v${MAJOR}"', _WORKFLOW)
        self.assertIn("needs.release-please.outputs.released == 'true'", _WORKFLOW)

    def test_release_pr_never_auto_merges(self) -> None:
        self.assertNotIn("--auto", _WORKFLOW)

    def test_ci_is_dispatched_onto_the_release_branch(self) -> None:
        self.assertIn("gh workflow run ci.yml", _WORKFLOW)
        self.assertRegex(_CI, r"(?m)^  workflow_dispatch:\n")


if __name__ == "__main__":
    unittest.main()
