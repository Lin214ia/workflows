"""Tests for the shell inside .github/workflows/dependabot-automerge.yml.

A workflow step only ever executes on GitHub, so the `run:` blocks are sliced
out of the YAML and run here with a stubbed `gh` on PATH. This workflow has one
job with several `run:` steps, so the slicer keys blocks by the step's `name:`
and returns every one.

The workflow is the single source of the markers, the label name and the
classification rules. Nothing here transcribes them as separate constants
except where reading them back out of the YAML would be more brittle than the
string itself (e.g. the label colour); those are asserted directly against the
workflow text in TestWorkflowShape.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "dependabot-automerge.yml"

MAJOR_MARKER = "major update: left for an agent to review"
HELD_MARKER = "held dependency update: left for an agent to review with evidence"
LABEL = "dependabot-major"


def _step_run_blocks() -> dict[str, str]:
    """`{step name: the step's run: | script}`, dedented.

    Steps are found by `      - name: ...` (6-space indent, one job deep) and
    each one's `run: |` block runs until the next line at or above the step's
    own indentation.
    """
    lines = _WORKFLOW.read_text().splitlines()
    step_at: list[tuple[int, str]] = []
    for n, line in enumerate(lines):
        m = re.match(r"^      - name: (.+)$", line)
        if m:
            step_at.append((n, m.group(1)))

    blocks: dict[str, str] = {}
    for index, (start, step_name) in enumerate(step_at):
        end = step_at[index + 1][0] if index + 1 < len(step_at) else len(lines)
        for i in range(start, end):
            if lines[i].strip() != "run: |":
                continue
            indent = len(lines[i]) - len(lines[i].lstrip())
            out: list[str] = []
            for line in lines[i + 1 : end]:
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                out.append(line[indent + 2 :] if line.strip() else "")
            blocks[step_name] = "\n".join(out)
            break
    return blocks


_BLOCKS = _step_run_blocks()
_GATE_SCRIPT = _BLOCKS["Classify update (patch/minor vs major, hold-patterns)"]
_MAJOR_COMMENT_SCRIPT = _BLOCKS["Flag major update for review (comment once)"]
_HELD_COMMENT_SCRIPT = _BLOCKS["Flag held dependency update for review (comment once)"]

_PR_URL = "https://example.test/pull/42"


class _StepHarness:
    """One temp dir holding a step script, a gh stub and its call log."""

    def __init__(self, script: str, stub: str):
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.root = root
        self.script = root / "step.sh"
        self.script.write_text(script)
        bindir = root / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(stub)
        gh.chmod(0o755)
        import os

        self.env = {
            "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "GH_STUB_DIR": str(root),
            "HOME": str(root),
            "PR_URL": _PR_URL,
            "GH_TOKEN": "stub",
            "REPO": "example/repo",
        }
        self.calls_log = root / "calls.log"
        self.calls_log.write_text("")

    def run(self, **extra_env: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["bash", str(self.script)],
            env={**self.env, **extra_env},
            capture_output=True,
            text=True,
            timeout=60,
        )
        proc.calls = self.calls_log.read_text()  # type: ignore[attr-defined]
        return proc

    def cleanup(self) -> None:
        self._dir.cleanup()


# gh stub for the `gate` step: no gh calls expected, just $GITHUB_OUTPUT.
_GATE_GH_STUB = r"""#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$GH_STUB_DIR/calls.log"
echo "unexpected: gh $*" >&2
exit 3
"""

# gh stub for the two comment-flagging steps: the comments read + the comment
# mutation.
_COMMENT_GH_STUB = r"""#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$GH_STUB_DIR/calls.log"
case "$*" in
  *"--json comments"*) jq -r '.[]' "$GH_STUB_DIR/comments.json" ;;
  *"pr comment"*)      : ;;
  *) echo "unexpected: gh $*" >&2; exit 3 ;;
esac
"""


class GateStepTest(unittest.TestCase):
    """The classify step, executed against fetch-metadata's two outputs."""

    def setUp(self) -> None:
        self.h = _StepHarness(_GATE_SCRIPT, _GATE_GH_STUB)
        self.addCleanup(self.h.cleanup)

    def _run(
        self,
        update_type: str,
        dep_names: str = "some-package",
        hold_patterns: str = "",
        arm_majors: str = "false",
    ) -> subprocess.CompletedProcess:
        github_output = self.h.root / "github_output"
        github_output.write_text("")
        proc = self.h.run(
            UPDATE_TYPE=update_type,
            DEP_NAMES=dep_names,
            HOLD_PATTERNS=hold_patterns,
            ARM_MAJORS=arm_majors,
            GITHUB_OUTPUT=str(github_output),
        )
        proc.outputs = dict(  # type: ignore[attr-defined]
            line.split("=", 1) for line in github_output.read_text().splitlines() if line
        )
        return proc

    def test_patch_is_armed(self):
        proc = self._run("version-update:semver-patch")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["auto"], "true")
        self.assertEqual(proc.outputs["major"], "false")
        self.assertEqual(proc.outputs["held"], "false")

    def test_minor_is_armed(self):
        proc = self._run("version-update:semver-minor")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["auto"], "true")

    def test_major_is_not_armed_by_default(self):
        proc = self._run("version-update:semver-major")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["auto"], "false")
        self.assertEqual(proc.outputs["major"], "true")
        self.assertEqual(proc.outputs["held"], "false")

    def test_major_is_armed_when_arm_majors_true(self):
        proc = self._run("version-update:semver-major", arm_majors="true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["auto"], "true")
        self.assertEqual(proc.outputs["major"], "true")

    def test_hold_pattern_match_is_never_armed(self):
        proc = self._run(
            "version-update:semver-patch",
            dep_names="@cornerstonejs/core,other-package",
            hold_patterns="@cornerstonejs/*",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["held"], "true")
        self.assertEqual(proc.outputs["auto"], "false")

    def test_hold_pattern_non_match_is_unaffected(self):
        proc = self._run(
            "version-update:semver-patch",
            dep_names="unrelated-package",
            hold_patterns="@cornerstonejs/*,pico-sdk",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["held"], "false")
        self.assertEqual(proc.outputs["auto"], "true")

    def test_grouped_pr_reporting_major_is_not_armed(self):
        """A grouped PR's update-type is the worst case across the whole
        group (fetch-metadata's documented behaviour) -- this step trusts that
        and does not parse individual dependencies for their own bump size."""
        proc = self._run(
            "version-update:semver-major",
            dep_names="package-a,package-b,package-c",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["auto"], "false")
        self.assertEqual(proc.outputs["major"], "true")

    def test_held_takes_priority_over_major_with_arm_majors(self):
        proc = self._run(
            "version-update:semver-major",
            dep_names="@cornerstonejs/core",
            hold_patterns="@cornerstonejs/*",
            arm_majors="true",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.outputs["held"], "true")
        self.assertEqual(proc.outputs["auto"], "false")


class MajorCommentStepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _StepHarness(_MAJOR_COMMENT_SCRIPT, _COMMENT_GH_STUB)
        self.addCleanup(self.h.cleanup)

    def _run(self, comments: list[str]) -> subprocess.CompletedProcess:
        import json

        (self.h.root / "comments.json").write_text(json.dumps(comments))
        return self.h.run()

    def test_comments_once(self):
        proc = self._run([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pr comment", proc.calls)
        self.assertIn(MAJOR_MARKER, proc.calls)

    def test_does_not_comment_twice(self):
        proc = self._run([MAJOR_MARKER])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("pr comment", proc.calls)


class HeldCommentStepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = _StepHarness(_HELD_COMMENT_SCRIPT, _COMMENT_GH_STUB)
        self.addCleanup(self.h.cleanup)

    def _run(self, comments: list[str]) -> subprocess.CompletedProcess:
        import json

        (self.h.root / "comments.json").write_text(json.dumps(comments))
        return self.h.run()

    def test_comments_once(self):
        proc = self._run([])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pr comment", proc.calls)
        self.assertIn(HELD_MARKER, proc.calls)

    def test_does_not_comment_twice(self):
        proc = self._run([HELD_MARKER])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("pr comment", proc.calls)


class TestWorkflowShape(unittest.TestCase):
    """Structural invariants of the YAML itself."""

    def test_expected_steps_were_sliced_out(self):
        self.assertEqual(
            set(_BLOCKS),
            {
                "Classify update (patch/minor vs major, hold-patterns)",
                "Ensure the dependabot-major label exists",
                "Flag major update for review (comment once)",
                "Flag held dependency update for review (comment once)",
            },
        )

    def test_no_expression_interpolation_inside_any_run_block(self):
        """Script-injection invariant: PR-controlled
        text (dependency names, PR body) must never be spliced into a shell
        script. Every value reaches these scripts through `env:` instead."""
        for step, script in _BLOCKS.items():
            self.assertNotIn(
                "${{", script, f"step {step!r} interpolates an expression into its shell"
            )

    def test_it_is_a_reusable_workflow_only(self):
        text = _WORKFLOW.read_text()
        self.assertIn("  workflow_call:", text)
        self.assertNotIn("  pull_request:", text)
        self.assertNotIn("  pull_request_target:", text)

    def test_it_declares_no_permissions_of_its_own(self):
        """A reusable workflow cannot raise the caller's token, only narrow
        it; the grant belongs entirely to the caller (see README.md)."""
        self.assertNotIn("\npermissions:", _WORKFLOW.read_text())

    def test_no_third_party_action_except_fetch_metadata(self):
        """No checkout, and no action beyond the one that reads Dependabot's
        own trusted metadata -- this workflow must never fetch or run PR
        code, since it runs under pull_request_target in every caller."""
        uses = re.findall(r"uses:\s*(\S+)", _WORKFLOW.read_text())
        self.assertEqual(uses, ["dependabot/fetch-metadata@v3"])

    def test_runs_on_has_a_default(self):
        """This input has a default: some callers
        (Lin214ia/*) have no self-hosted runner group, and this job does no
        checkout or build, so hosted minutes for it are cheap."""
        text = _WORKFLOW.read_text()
        block = text.split("      runs-on:", 1)[1].split("      hold-patterns:", 1)[0]
        self.assertIn("default: '\"ubuntu-latest\"'", block)

    def test_arm_majors_defaults_false(self):
        text = _WORKFLOW.read_text()
        block = text.split("      arm-majors:", 1)[1]
        self.assertIn("default: false", block)

    def test_the_job_is_scoped_to_dependabot(self):
        text = _WORKFLOW.read_text()
        self.assertIn("if: github.actor == 'dependabot[bot]'", text)

    def test_label_name_and_colour_are_stable(self):
        """Public interface: a caller or a ruleset could filter on this label
        name, so renaming it here is a breaking change across every caller."""
        text = _WORKFLOW.read_text()
        self.assertIn(f"gh label create {LABEL}", text)
        self.assertIn("--force", text)


if __name__ == "__main__":
    unittest.main()
