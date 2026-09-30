"""Tests for the shell inside .github/workflows/tier3-gate.yml.

A workflow step only ever runs on GitHub,
so the `run:` block is sliced out of the YAML by indentation and executed here
with a stubbed `gh` on PATH. No third-party dependency; the structural
assertions fail loudly if the shape the slicing assumes ever changes.

The interesting cases are the ones that decide whether the gate can be evaded:
a repository declaring `.github/**` out of scope, a PR large enough to truncate
the file list, and the job name that the required-check context is built from.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "tier3-gate.yml"
_CALLER = _ROOT / ".github" / "workflows" / "tier3-selftest.yml"


def _run_block() -> str:
    """The single `run: |` script in the gate job, dedented."""
    lines = _WORKFLOW.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "run: |":
            continue
        indent = len(line) - len(line.lstrip())
        out: list[str] = []
        for rest in lines[i + 1:]:
            if rest.strip() and len(rest) - len(rest.lstrip()) <= indent:
                break
            out.append(rest[indent + 2:] if rest.strip() else "")
        return "\n".join(out)
    raise AssertionError("no `run: |` block found in tier3-gate.yml")


class _Harness:
    """The step's script with a `gh` that answers from a fixed file list."""

    def __init__(self, files: list[str]):
        self.dir = Path(tempfile.mkdtemp(prefix="tier3-gate-"))
        (self.dir / "files.txt").write_text("".join(f"{f}\n" for f in files))
        (self.dir / "gh-calls.log").write_text("")
        stub = self.dir / "gh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> "{self.dir}/gh-calls.log"\n'
            f'cat "{self.dir}/files.txt"\n'
        )
        stub.chmod(0o755)
        (self.dir / "step.sh").write_text(_run_block())

    def run(self, **env: str) -> subprocess.CompletedProcess:
        environment = {
            **os.environ,
            "PATH": f"{self.dir}:{os.environ['PATH']}",
            "GH_TOKEN": "x",
            "REPO": "Lin214ia/example",
            "PR_NUMBER": "7",
            "LABEL": "human-gate",
            "HAS_LABEL": "false",
            "CALLER_PATHS": "",
            **env,
        }
        return subprocess.run(
            ["bash", str(self.dir / "step.sh")],
            env=environment, capture_output=True, text=True, check=False,
        )

    @property
    def gh_calls(self) -> str:
        return (self.dir / "gh-calls.log").read_text()


class TestTheGate(unittest.TestCase):
    def test_the_label_passes_without_asking_github_anything(self):
        harness = _Harness(["playwright.config.ts"])
        result = harness.run(HAS_LABEL="true", CALLER_PATHS="playwright.config.ts")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("the owner merges it", result.stdout)
        self.assertEqual(harness.gh_calls, "", "the label short-circuits before any API call")

    def test_an_unrelated_change_passes(self):
        result = _Harness(["src/app.ts", "docs/readme.md"]).run(
            CALLER_PATHS="apps/desktop/playwright.config.ts"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No Tier 3 path", result.stdout)

    def test_a_declared_path_fails_and_names_the_file_and_the_remedy(self):
        result = _Harness(["src/app.ts", "apps/desktop/playwright.config.ts"]).run(
            CALLER_PATHS="apps/desktop/playwright.config.ts"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("apps/desktop/playwright.config.ts", result.stdout)
        self.assertIn("human-gate", result.stdout)
        self.assertNotIn("src/app.ts", result.stdout.split("Tier 3 paths changed")[1])

    def test_a_double_star_pattern_crosses_directories(self):
        result = _Harness(["apps/desktop/tests/e2e/glass-snapshots/a.png"]).run(
            CALLER_PATHS="**/*-snapshots/**"
        )
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_dot_github_is_tier_three_even_when_the_caller_declares_nothing(self):
        result = _Harness([".github/workflows/ci.yml"]).run(CALLER_PATHS="")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(".github/workflows/ci.yml", result.stdout)

    def test_a_repository_cannot_declare_its_own_guard_out_of_scope(self):
        # A caller that lists only its product paths still cannot touch the
        # workflows that judge it.
        result = _Harness([".github/CODEOWNERS"]).run(CALLER_PATHS="src/**")
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_a_truncated_file_list_fails_closed(self):
        result = _Harness([f"src/file{n}.ts" for n in range(3000)]).run(CALLER_PATHS="nothing/**")
        self.assertEqual(result.returncode, 1)
        self.assertIn("3000", result.stdout)

    def test_just_under_the_cap_is_still_judged_on_its_contents(self):
        result = _Harness([f"src/file{n}.ts" for n in range(2999)]).run(CALLER_PATHS="nothing/**")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_blank_line_in_the_caller_paths_matches_nothing(self):
        result = _Harness(["src/app.ts"]).run(CALLER_PATHS="\n\n   \n")
        self.assertEqual(result.returncode, 0, result.stdout)


class TestWorkflowShape(unittest.TestCase):
    def test_the_job_name_the_required_check_context_is_built_from(self):
        # The context is `<caller job id> / <called job name>` = `tier3 / gate`.
        # Renaming either half silently un-requires the check in every ruleset.
        text = _WORKFLOW.read_text()
        self.assertRegex(text, r"(?m)^  gate:$")
        self.assertRegex(text, r"(?m)^    name: gate$")
        self.assertRegex(_CALLER.read_text(), r"(?m)^  tier3:$")

    def test_no_interpolation_reaches_the_shell(self):
        self.assertNotIn("${{", _run_block())

    def test_the_reusable_workflow_declares_no_permissions(self):
        # A called workflow cannot raise the caller's token; declaring
        # permissions here would only mislead.
        self.assertNotRegex(_WORKFLOW.read_text(), r"(?m)^permissions:")

    def test_the_caller_runs_the_base_branch_copy_and_narrows_the_token(self):
        caller = _CALLER.read_text()
        self.assertRegex(caller, r"(?m)^  pull_request_target:")
        self.assertNotRegex(caller, r"(?m)^  pull_request:")
        for trigger in ("opened", "reopened", "synchronize", "edited", "labeled", "unlabeled"):
            self.assertIn(trigger, caller)
        self.assertRegex(caller, r"(?m)^permissions:")
        self.assertIn("contents: read", caller)
        self.assertIn("pull-requests: read", caller)

    def test_the_caller_has_no_paths_filter(self):
        # A required check whose workflow never starts leaves the context
        # pending forever and freezes the repository. Only the trigger block is
        # examined: `paths` is also the name of the input the caller passes.
        caller = _CALLER.read_text()
        trigger = caller.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("paths:", trigger)
        self.assertNotIn("paths-ignore:", trigger)

    def test_runs_on_has_no_default(self):
        text = _WORKFLOW.read_text()
        runs_on = text.split("runs-on:", 1)[1].split("paths:", 1)[0]
        self.assertIn("required: true", runs_on)
        self.assertNotIn("default:", runs_on)


if __name__ == "__main__":
    unittest.main()
