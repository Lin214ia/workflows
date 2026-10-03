"""Tests for the shell inside .github/workflows/pr-evidence.yml.

A workflow step only ever runs on GitHub, so the `run:` block is sliced out of
the YAML by indentation and executed here. The step calls no `gh`; every input
reaches it through `env:`, so the harness just sets the same variables the
workflow sets. No third-party dependency; the structural assertions fail loudly
if the shape the slicing assumes ever changes.

The interesting cases are the ones that decide whether the check can be gamed
or can freeze a repository: a heading with no proof under it, proof under the
wrong heading, proof inside a fence that only looks like structure, and the
job name the required-check context is built from.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "pr-evidence.yml"
_CALLER = _ROOT / ".github" / "workflows" / "pr-evidence-selftest.yml"

AGENT = "hedgehog-agent[bot]"
FENCE = "```"


def _run_block() -> str:
    """The single `run: |` script in the check job, dedented."""
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
    raise AssertionError("no `run: |` block found in pr-evidence.yml")


_SCRIPT = _run_block()


def _check(body: str, author: str = AGENT, head_ref: str = "claude/x",
           experiment: bool = False) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="pr-evidence-") as tmp:
        step = Path(tmp) / "step.sh"
        step.write_text(_SCRIPT)
        summary = Path(tmp) / "summary.md"
        summary.write_text("")
        proc = subprocess.run(
            ["bash", str(step)],
            env={
                **os.environ,
                "AUTHOR": author,
                "HEAD_REF": head_ref,
                "PR_BODY": body,
                "AGENT_LOGIN": AGENT,
                "EXPERIMENT_LABEL": "experiment",
                "IS_EXPERIMENT": "true" if experiment else "false",
                "GITHUB_STEP_SUMMARY": str(summary),
            },
            capture_output=True, text=True, check=False,
        )
        proc.summary = summary.read_text()  # type: ignore[attr-defined]
        return proc


class WorkflowShape(unittest.TestCase):
    def test_script_is_not_empty(self):
        self.assertGreater(len(_SCRIPT.strip().splitlines()), 20)

    def test_job_name_is_the_pinned_context_half(self):
        text = _WORKFLOW.read_text()
        self.assertRegex(text, r"(?m)^jobs:\n  check:\n")
        self.assertRegex(text, r"(?m)^    name: check$")

    def test_caller_job_id_is_the_other_half(self):
        self.assertRegex(_CALLER.read_text(), r"(?m)^  evidence:\n    uses: \./")

    def test_no_paths_filter_and_no_job_level_if(self):
        # A required check that never starts freezes the repository.
        for path in (_WORKFLOW, _CALLER):
            text = path.read_text()
            self.assertNotRegex(text, r"(?m)^\s*paths(-ignore)?:")
            self.assertNotRegex(text, r"(?m)^    if:")

    def test_caller_covers_every_event_that_changes_the_verdict(self):
        text = _CALLER.read_text()
        self.assertIn("pull_request_target", text)
        for event in ("opened", "edited", "synchronize", "reopened", "labeled", "unlabeled"):
            self.assertIn(event, text)

    def test_no_expression_interpolated_into_the_shell(self):
        self.assertNotIn("${{", _SCRIPT)


class AgentPrs(unittest.TestCase):
    def assertPasses(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def assertFails(self, proc):
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_fenced_block_passes(self):
        body = f"Summary\n\n## Evidence\n{FENCE}\n3 passed\n{FENCE}\n"
        proc = _check(body)
        self.assertPasses(proc)
        self.assertIn("fence", proc.stdout)

    def test_fence_with_language_and_tildes_pass(self):
        self.assertPasses(_check(f"## Evidence\n{FENCE}text\nok\n{FENCE}\n"))
        self.assertPasses(_check("## Evidence\n~~~\nok\n~~~\n"))

    def test_image_passes(self):
        self.assertPasses(_check("## Evidence\n![shot](https://x/y.png)\n"))

    def test_html_image_passes(self):
        self.assertPasses(_check('## Evidence\n<img src="https://x/y.png">\n'))

    def test_table_passes(self):
        body = "## Evidence\n\n| metric | value |\n| --- | ---: |\n| p95 | 120 ms |\n"
        self.assertPasses(_check(body))

    def test_crlf_body_passes(self):
        body = f"## Evidence\r\n{FENCE}\r\nok\r\n{FENCE}\r\n"
        self.assertPasses(_check(body))

    def test_section_may_end_at_next_heading(self):
        body = f"## Evidence\n{FENCE}\nok\n{FENCE}\n\n## Assumptions\nAssumption: x\n"
        self.assertPasses(_check(body))

    def test_empty_body_fails(self):
        proc = _check("")
        self.assertFails(proc)
        self.assertIn("## Evidence", proc.stdout)
        self.assertIn("experiment", proc.stdout)

    def test_heading_without_proof_fails(self):
        self.assertFails(_check("## Evidence\nI ran the tests and they passed.\n"))

    def test_how_it_was_verified_is_not_evidence(self):
        body = f"## How it was verified\n{FENCE}\nok\n{FENCE}\n"
        self.assertFails(_check(body))

    def test_proof_under_another_heading_fails(self):
        body = f"## Evidence\nsee below\n\n## Notes\n{FENCE}\nok\n{FENCE}\n"
        self.assertFails(_check(body))

    def test_proof_before_the_heading_fails(self):
        body = f"{FENCE}\nok\n{FENCE}\n\n## Evidence\ntrust me\n"
        self.assertFails(_check(body))

    def test_empty_fence_fails(self):
        self.assertFails(_check(f"## Evidence\n{FENCE}\n\n{FENCE}\n"))

    def test_heading_inside_a_fence_is_not_a_section(self):
        body = f"Notes\n{FENCE}\n## Evidence\nok\n{FENCE}\n"
        self.assertFails(_check(body))

    def test_heading_inside_a_fence_does_not_end_the_section(self):
        body = f"## Evidence\n{FENCE}\n## not a heading\nok\n{FENCE}\n"
        self.assertPasses(_check(body))

    def test_a_pipe_in_prose_is_not_a_table(self):
        self.assertFails(_check("## Evidence\nran a | b | c and it worked\n"))

    def test_level_three_heading_does_not_end_the_section(self):
        body = f"## Evidence\n### Tests\n{FENCE}\nok\n{FENCE}\n"
        self.assertPasses(_check(body))


class ExemptPrs(unittest.TestCase):
    def test_experiment_label_passes_without_evidence_and_is_marked(self):
        proc = _check("", experiment=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("experiment — excluded from quality metrics", proc.summary)

    def test_other_authors_pass_untouched(self):
        for author in ("Lin214ia", "dependabot[bot]", "release-please[bot]"):
            proc = _check("", author=author)
            self.assertEqual(proc.returncode, 0, author + proc.stdout + proc.stderr)

    def test_dependabot_and_release_please_branches_pass_even_if_agent_authored(self):
        for ref in ("dependabot/npm_and_yarn/x-1.2", "release-please--branches--main"):
            proc = _check("", head_ref=ref)
            self.assertEqual(proc.returncode, 0, ref + proc.stdout + proc.stderr)

    def test_a_probe_is_not_marked_experiment_by_a_substring(self):
        # IS_EXPERIMENT comes from an exact-match `contains` on label names in
        # the workflow; make sure that is what the YAML evaluates.
        text = _WORKFLOW.read_text()
        self.assertIn(
            "contains(github.event.pull_request.labels.*.name, inputs.experiment-label)", text)

    def test_the_default_agent_is_the_hedgehog_app(self):
        text = _WORKFLOW.read_text()
        self.assertRegex(text, re.compile(r"default: hedgehog-agent\[bot\]"))


if __name__ == "__main__":
    unittest.main()
