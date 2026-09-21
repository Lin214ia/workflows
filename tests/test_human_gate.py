"""Tests for the shell inside .github/workflows/human-gate.yml.

A workflow step is only ever executed on GitHub, so the only way to test one
locally is to pull the `run:` block out of the YAML and run it here with a
stubbed `gh` on PATH. That is what this file does, and it is a deliberate port
of the same approach in agent-loops' tests/test_human_gate_body_check.py: the
body corpus below is that suite's, so the rule this workflow enforces is pinned
by the same near-miss cases that found it.

No third-party dependency: stdlib `unittest`, and the YAML is sliced by
indentation rather than parsed (the structural assertions in
`TestWorkflowShape` fail loudly if the shape this slicing assumes ever changes).

The workflow is the single source of the marker, the canonical line and the
stake pattern. Nothing here transcribes them -- every value is read back out of
the YAML, so a test cannot pass against a constant the workflow no longer has.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "human-gate.yml"
_SELFTEST = _ROOT / ".github" / "workflows" / "selftest.yml"

# Bodies that DO state both halves: the merge is the owner's, and what is at
# stake in it. The first is the canonical line; the rest are hand-written
# phrasings that tell the same truth, which the check must not re-flag. Between
# them they cover every separator the stake pattern accepts (em dash, double
# hyphen, spaced hyphen, colon).
_COMPLIANT_BODIES = [
    "**For you:**\n> ⚠️ **`human-gate`: merging this is your call** — this starts a $9/month bill on the new API key.\n\nFixes #3",
    "> ⚠️ **`human-gate`** — this one is yours — it points the backup at the new disk, and last week's scans are what is at risk.",
    "> **`human-gate`**: your call to merge -- it changes what the site says on its front page.",
    "intro\n\n  > ⚠️ `human-gate`: the merge is yours - only you can plug in the drive it writes to.\n\ntail",
    "> ⚠️ **`human-gate`** — the merge is yours: it deletes the old exports, and they are not recoverable.",
]

# Bodies that do NOT. Each is a near miss making one condition load-bearing: no
# ownership words; the words split across lines; the statement in prose instead
# of the blockquote the convention standardises on; then the stake misses --
# ownership with no stake at all, the two shapes the fleet used to carry, a
# separator with nothing after it, and a colon sitting BEFORE the ownership
# words rather than introducing a stake after them.
_VIOLATING_BODIES = [
    "**For you:** Nothing to do — merges itself when checks pass.\n\nFixes #3",
    "",
    "> ⚠️ **`human-gate`**: merge requires the verification step in the issue.",
    "This PR is labeled human-gate.\n\nThe merge is yours.",
    "> the decision here is yours\n\nlabeled human-gate elsewhere",
    "This is a `human-gate` PR, so merging it is your call — it spends $9/month on the new API key.",
    "> ⚠️ **`human-gate`: merging this is your call**.",
    "> **`human-gate`**: your call to merge.",
    "> ⚠️ `human-gate` — this one is yours to merge once you have eyeballed the diff.",
    "> ⚠️ **`human-gate`: merging this is your call** —",
    "> ⚠️ **`human-gate`**: this one is yours.",
]

# gh stub for the `body` job: the two `pr view` shapes it uses, plus the comment
# mutation. Every call is logged so mutations are observable.
_BODY_GH_STUB = r"""#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$GH_STUB_DIR/calls.log"
case "$*" in
  *"--json body"*)     cat "$GH_STUB_DIR/body.txt" ;;
  *"--json comments"*) jq -r '.[]' "$GH_STUB_DIR/comments.json" ;;
  *"pr comment"*)      : ;;
  *) echo "unexpected: gh $*" >&2; exit 3 ;;
esac
"""

# gh stub for the `enforce` job: the auto-merge read, the disable, the comment.
_ENFORCE_GH_STUB = r"""#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$GH_STUB_DIR/calls.log"
case "$*" in
  *"--json autoMergeRequest"*) cat "$GH_STUB_DIR/automerge.txt" ;;
  *"pr merge"*)                : ;;
  *"pr comment"*)              : ;;
  *) echo "unexpected: gh $*" >&2; exit 3 ;;
esac
"""


def _run_blocks() -> dict[str, str]:
    """`{job id: the job's single run: | script}`, dedented.

    The YAML is sliced, not parsed, so the suite needs no PyYAML. Correctness of
    the slicing is asserted in TestWorkflowShape rather than assumed.
    """
    lines = _WORKFLOW.read_text().splitlines()
    job_at: list[tuple[int, str]] = [
        (n, re.match(r"^  (\w[\w-]*):$", line).group(1))  # type: ignore[union-attr]
        for n, line in enumerate(lines)
        if re.match(r"^  (\w[\w-]*):$", line)
    ]
    blocks: dict[str, str] = {}
    for index, (start, job) in enumerate(job_at):
        end = job_at[index + 1][0] if index + 1 < len(job_at) else len(lines)
        for i in range(start, end):
            if lines[i].strip() != "run: |":
                continue
            indent = len(lines[i]) - len(lines[i].lstrip())
            out: list[str] = []
            for line in lines[i + 1:end]:
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                out.append(line[indent + 2:] if line.strip() else "")
            blocks[job] = "\n".join(out)
            break
    return blocks


def _shell_const(script: str, name: str) -> str:
    """The value of a single-quoted `name='...'` assignment in a run block."""
    prefix = f"{name}='"
    values = [
        line.strip()[len(prefix):-1]
        for line in script.splitlines()
        if line.strip().startswith(prefix) and line.strip().endswith("'")
    ]
    assert len(values) == 1, f"expected one {name}= assignment, got {values}"
    return values[0]


_BLOCKS = _run_blocks()
_BODY_SCRIPT = _BLOCKS["body"]
_ENFORCE_SCRIPT = _BLOCKS["enforce"]
MARKER = _shell_const(_BODY_SCRIPT, "marker")
CANONICAL = _shell_const(_BODY_SCRIPT, "canonical")
STAKE = _shell_const(_BODY_SCRIPT, "stake")

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
        self.env = {
            "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "GH_STUB_DIR": str(root),
            "HOME": str(root),
            "PR_URL": _PR_URL,
            "GH_TOKEN": "stub",
        }
        self.calls_log = root / "calls.log"

    def run(self, **extra_env: str) -> subprocess.CompletedProcess:
        self.calls_log.write_text("")
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


class BodyJobTest(unittest.TestCase):
    """The `body` job's shell, executed."""

    def setUp(self) -> None:
        self.h = _StepHarness(_BODY_SCRIPT, _BODY_GH_STUB)
        self.addCleanup(self.h.cleanup)

    def _run(self, body: str, comments: list[str]) -> subprocess.CompletedProcess:
        (self.h.root / "body.txt").write_text(body)
        (self.h.root / "comments.json").write_text(json.dumps(comments))
        return self.h.run()

    def test_compliant_bodies_pass_without_commenting(self):
        for body in _COMPLIANT_BODIES:
            with self.subTest(body=body):
                proc = self._run(body, [])
                self.assertEqual(proc.returncode, 0, f"{proc.stdout!r} {proc.stderr!r}")
                self.assertNotIn("pr comment", proc.calls)

    def test_violating_bodies_fail_and_comment_once(self):
        for body in _VIOLATING_BODIES:
            with self.subTest(body=body):
                proc = self._run(body, [])
                self.assertEqual(proc.returncode, 1, f"{proc.stdout!r} {proc.stderr!r}")
                self.assertIn("pr comment", proc.calls)
                self.assertIn(CANONICAL, proc.calls)
                self.assertIn(MARKER, proc.calls)
                # The runner reads ::error:: from stdout, not stderr.
                self.assertIn("::error::", proc.stdout)

    def test_ownership_without_a_stake_is_a_violation(self):
        """The policy this job encodes: the line has to say what the owner is
        evaluating. The same body with a stake appended passes, which is what
        makes the tail the thing being tested and not the wording around it."""
        blind = "> ⚠️ **`human-gate`: merging this is your call**."
        self.assertEqual(self._run(blind, []).returncode, 1)
        with_stake = blind[:-1] + " — it deletes the old exports for good."
        self.assertEqual(self._run(with_stake, []).returncode, 0)

    def test_second_run_does_not_comment_twice(self):
        proc = self._run(_VIOLATING_BODIES[0], [f"{MARKER}\nearlier comment"])
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("pr comment", proc.calls)
        self.assertIn("Already commented", proc.stdout)

    def test_unrelated_comments_do_not_suppress_the_comment(self):
        proc = self._run(_VIOLATING_BODIES[0], ["LGTM", "ping"])
        self.assertIn("pr comment", proc.calls)

    def test_it_never_edits_the_body(self):
        proc = self._run(_VIOLATING_BODIES[0], [])
        self.assertNotIn("pr edit", proc.calls)
        self.assertNotIn("--body-file", proc.calls)

    def test_the_canonical_line_it_prescribes_passes_its_own_check(self):
        """Round-trip. A canonical line and a matcher that drift apart make the
        documented fix for a flagged PR get flagged again."""
        self.assertEqual(self._run(CANONICAL, []).returncode, 0)


class EnforceJobTest(unittest.TestCase):
    """The `enforce` job's shell, executed."""

    def setUp(self) -> None:
        self.h = _StepHarness(_ENFORCE_SCRIPT, _ENFORCE_GH_STUB)
        self.addCleanup(self.h.cleanup)

    def _run(self, automerge: str, owner_note: str = "") -> subprocess.CompletedProcess:
        (self.h.root / "automerge.txt").write_text(automerge)
        return self.h.run(OWNER_NOTE=owner_note)

    def test_no_auto_merge_is_a_quiet_noop(self):
        """The `labeled` event fires when auto-merge is off. Commenting then
        would comment on every label change on every gated PR."""
        for value in ("null\n", "\n"):
            with self.subTest(value=value):
                proc = self._run(value)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("pr merge", proc.calls)
                self.assertNotIn("pr comment", proc.calls)

    def test_auto_merge_is_disabled_and_commented(self):
        proc = self._run('{"enabledAt":"2026-01-01T00:00:00Z"}\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pr merge", proc.calls)
        self.assertIn("--disable-auto", proc.calls)
        self.assertIn("pr comment", proc.calls)
        self.assertIn("human-gate", proc.calls)

    def test_the_owner_note_is_appended_when_given(self):
        note = "Verification here means flashing a Pico 2 and checking the stream."
        proc = self._run('{"enabledAt":"x"}\n', owner_note=note)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(note, proc.calls)

    def test_an_empty_owner_note_adds_no_trailing_space(self):
        proc = self._run('{"enabledAt":"x"}\n', owner_note="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("disabled again.", proc.calls)
        self.assertNotIn("disabled again. \n", proc.calls)


class TestWorkflowShape(unittest.TestCase):
    """Structural invariants of the YAML itself."""

    def test_both_jobs_were_sliced_out(self):
        """If the slicing above silently found nothing, every behavioural test
        would vacuously pass on an empty script."""
        self.assertEqual(set(_BLOCKS), {"enforce", "body"})
        for job, script in _BLOCKS.items():
            self.assertIn("gh pr view", script, job)
            self.assertIn("set -euo pipefail", script, job)

    def test_no_expression_interpolation_inside_any_run_block(self):
        """The script-injection invariant. `${{ github.event... }}` spliced into
        a shell script lets PR text (a title, a body, a branch name) execute as
        code. Every value reaches these scripts through `env:` instead."""
        for job, script in _BLOCKS.items():
            self.assertNotIn("${{", script, f"job {job} interpolates an expression into its shell")

    def test_every_env_name_a_script_reads_is_declared(self):
        """A step reading $FOO that no `env:` sets runs with it empty under
        `set -u`... except that the workflow's own `env:` block is the only
        place it can come from, so a typo in either half is a silent no-op."""
        text = _WORKFLOW.read_text()
        for name in ("PR_URL", "GH_TOKEN", "OWNER_NOTE"):
            self.assertIn(f"{name}: ${{{{", text, f"{name} is not wired through env:")

    def test_runs_on_is_required_with_no_default(self):
        """A default would silently put a caller on billed hosted minutes."""
        text = _WORKFLOW.read_text()
        block = text.split("      runs-on:", 1)[1].split("      owner-note:", 1)[0]
        self.assertIn("required: true", block)
        self.assertNotIn("default:", block)

    def test_the_runner_comes_from_the_input(self):
        text = _WORKFLOW.read_text()
        self.assertEqual(text.count("runs-on: ${{ fromJSON(inputs.runs-on) }}"), 2)

    def test_it_is_a_reusable_workflow_only(self):
        """A `pull_request` trigger here would make this repo's own PRs run the
        gate twice: once directly, once through selftest.yml."""
        text = _WORKFLOW.read_text()
        self.assertIn("  workflow_call:", text)
        self.assertNotIn("  pull_request:", text)

    def test_it_declares_no_permissions_of_its_own(self):
        """A reusable workflow cannot raise the caller's token, only narrow it.
        Declaring permissions here would silently strip a caller that granted
        more, so the grant belongs entirely to the caller."""
        self.assertNotIn("\npermissions:", _WORKFLOW.read_text())

    def test_no_third_party_action_and_no_checkout(self):
        """The PR's own code is never fetched or run by this workflow."""
        self.assertNotIn("uses:", _WORKFLOW.read_text())

    def test_both_jobs_short_circuit_on_the_label(self):
        text = _WORKFLOW.read_text()
        self.assertEqual(
            text.count("contains(github.event.pull_request.labels.*.name, 'human-gate')"), 2
        )

    def test_the_documented_grant_is_contents_write(self):
        """`contents: read` here is not a typo with no consequence: it is the
        permission four consumers copied, and under it `gh pr merge
        --disable-auto` fails with `Resource not accessible by integration
        (disablePullRequestAutoMerge)` — the gate goes red and leaves
        auto-merge armed on a PR only the owner may merge (needle-simulator run
        34901519703). This file and the README are where that value is read
        from, so they are what has to be right."""
        header = _WORKFLOW.read_text().split("name: Human gate", 1)[0]
        self.assertIn("`contents: write` and `pull-requests: write` itself", header)

        # Only this workflow's half of the README. `tier3-gate` documents
        # `contents: read` a few sections up and is correct to: it never
        # disables auto-merge, it only reads through `gh`.
        readme = (_ROOT / "README.md").read_text()
        section = readme.split("### `human-gate`", 1)[1].split("\n## ", 1)[0]
        # The copyable caller snippet must show the grant job-scoped, above the
        # `uses:` it applies to. A snippet granting it at the top of the file
        # would be copied that way into repositories whose other jobs do check
        # out pull-request code.
        self.assertIn(
            "    permissions:\n"
            "      contents: write\n"
            "      pull-requests: write\n"
            "    uses: Lin214ia/workflows",
            section,
        )

    def test_the_selftest_grants_its_calling_job_contents_write(self):
        """The self-test is a caller like any other. Running it on a weaker
        token than the README documents would prove the workflow works under
        permissions no consumer has — the live check would stay green over
        exactly the hole it exists to catch."""
        job = _SELFTEST.read_text().split("  human-gate:", 1)[1]
        self.assertIn("    permissions:", job)
        self.assertIn("      contents: write", job)
        self.assertIn("      pull-requests: write", job)

    def test_the_selftest_keeps_the_file_level_grant_read_only(self):
        """`contents: write` is not operation-specific, so what bounds it is
        which steps see it. Raised at the top of the file it would reach every
        other job the caller ever adds; scoped to the calling job it reaches
        only this workflow's own steps, which check out no PR code."""
        top = _SELFTEST.read_text().split("\njobs:", 1)[0]
        self.assertIn("\npermissions:\n  contents: read\n", top)

    def test_the_selftest_calls_this_workflow_on_pull_request(self):
        """Without it, nothing in this repo ever executes the workflow it ships."""
        text = _SELFTEST.read_text()
        self.assertIn("uses: ./.github/workflows/human-gate.yml", text)
        self.assertIn("pull_request:", text)
        for event in ("auto_merge_enabled", "labeled", "opened", "reopened", "edited"):
            self.assertIn(event, text)


if __name__ == "__main__":
    unittest.main()
