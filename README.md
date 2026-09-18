# workflows

Reusable GitHub Actions workflows shared by repositories in two owners:
[`Lin214ia`](https://github.com/Lin214ia) (personal) and
[`AyakaRadiology`](https://github.com/AyakaRadiology) (organisation).

**This repository is public on purpose.** A private reusable workflow can only
be called from inside its own owner, and these are called from both — so public
is the only shape that works. Nothing here is secret: no tokens, no runner
addresses, no product code. Every workflow uses only the caller's built-in
`GITHUB_TOKEN` and `gh`, checks out nothing, and runs no third-party action.

## Workflows

| Workflow | What it does |
| --- | --- |
| [`human-gate.yml`](.github/workflows/human-gate.yml) | Enforces the `human-gate` label: disables auto-merge on a labelled PR, and fails the run when the PR body does not tell the owner the merge is theirs and what is at stake in it. |

### `human-gate`

`human-gate` means *an agent may build it, a human merges it*. Two failures gave
the workflow its two jobs:

- **`enforce`** — an agent (or a person) enables auto-merge on a gated PR and
  the gate is carried straight through it. This job disables auto-merge again,
  and comments once when it actually did so.
- **`body`** — disabling auto-merge is *invisible* to whoever reads the PR. A
  fleet audit found 14 open PRs whose `**For you:**` section still read
  "Nothing to do — merges itself when checks pass" while the label had already
  parked them; one of those silently parked a data-loss fix. So the body has to
  say it, and has to name the stake — `human-gate` is only for a merge the owner
  can actually evaluate. This job comments once per PR and fails the run. It
  never edits the body: a script rewriting the author's own statement of what
  the change is would hide the defect rather than report it.

The line a PR body must carry:

```
> ⚠️ **`human-gate`: merging this is your call** — <the stake in one line: what it costs, the risk you are accepting, or the step only you can take>.
```

Hand-written phrasings of the same two halves pass too — the matcher is wider
than that one string. What does *not* pass is a line stopping at "your call":
that asks the owner to approve something he was given nothing to judge by.

#### Caller snippet

```yaml
# Mechanical backstop for the `human-gate` label. The whole implementation is
# shared: https://github.com/Lin214ia/workflows/blob/main/.github/workflows/human-gate.yml
# Only `runs-on` is this repo's. Change behaviour there, not here.
name: Human gate

on:
  pull_request:
    types: [auto_merge_enabled, labeled, opened, reopened, edited]

permissions:
  contents: read
  pull-requests: write

jobs:
  human-gate:
    uses: Lin214ia/workflows/.github/workflows/human-gate.yml@main
    with:
      runs-on: '["self-hosted","Linux","X64"]'
```

The `pull_request` event types matter. `auto_merge_enabled` and `labeled` catch
the gate being applied or bypassed; `opened`, `reopened` and `edited` are what
make the `body` job re-check after the author fixes the body — without `edited`,
a corrected body would stay red until something else re-triggered the workflow.

#### Inputs

| Input | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `runs-on` | string | **yes** | — | Runner label(s) **as a JSON string**: `'"ubuntu-latest"'` or `'["self-hosted","Linux","X64"]'`. Resolved with `fromJSON`, because `runs-on` accepts either a string or a list and a workflow input can only be a string. Keep the exact case your runners register with. |
| `owner-note` | string | no | `''` | One extra sentence appended to the `enforce` comment, for a repo where the owner's step is worth naming (`scl3300-stream` names flashing a Pico 2 + SCL3300). |
| `require-body-statement` | boolean | no | `true` | Runs the `body` job. Leave it true. |

`runs-on` deliberately has **no default**. A default of `ubuntu-latest` would
let a caller that forgot the input land silently on GitHub-hosted minutes, which
this fleet bills against a 3,000 min/month org allowance it has already
exhausted once.

#### Permissions

The caller must grant them:

```yaml
permissions:
  contents: read
  pull-requests: write
```

**A reusable workflow cannot raise the caller's `GITHUB_TOKEN` permissions, only
narrow them.** This workflow therefore declares none of its own — the grant is
entirely the caller's. A caller that forgets does not get a silent skip: the `gh`
calls fail with 403 and the run goes red.

## Why callers pin `@main`

Every consumer pins `@main`, not a tag or a SHA.

**The problem being solved was drift.** Five copies of the same workflow existed,
and they had already diverged: different runner labels (fine — that is now an
input), different YAML indentation, different comment sentences, and — the one
that mattered — only one of them had the `body` job at all. Four repositories
were enforcing a weaker rule than the fifth, and nothing anywhere would have
told anyone. A tag or a SHA pin reintroduces exactly that: five pins that get
bumped at five different times, i.e. drift with a version number on it.

**The trade-off is real and stated plainly:** a bad merge to `main` here breaks
every caller at once. What makes that affordable:

- One owner (`Lin214ia`) writes here, and `main` is protected — changes arrive
  through a PR.
- [`ci.yml`](.github/workflows/ci.yml) runs `actionlint` (pinned release,
  checksum-verified) with `shellcheck`, plus tests that extract each job's
  `run:` block and **execute it** against the same corpus of compliant and
  near-miss PR bodies that pinned the rule in `agent-loops`.
- [`selftest.yml`](.github/workflows/selftest.yml) calls the workflow the way a
  consumer does, on every PR to this repo. A `workflow_call` interface error
  surfaces here as a `startup_failure` before it surfaces in four other repos.
- The blast radius is a *check*, not a deploy. A broken `human-gate` run blocks
  nothing: it is not a required status check in any consumer's ruleset.

## Development

```bash
actionlint                                  # needs shellcheck on PATH
python3 -m unittest discover -s tests -v    # stdlib only; needs jq for the gh stubs
```

The tests read the marker, the canonical line and the stake regex **back out of
the YAML** rather than transcribing them, so a test cannot pass against a
constant the workflow no longer carries. The workflow is the single source of
those three strings; `agent-loops`' `scripts/lib-human-gate.sh` carries the same
values for the sweep that runs outside CI, and its suite pins them equal.

## Six months from now, this has broken. What broke?

- **Manual steps.** A new repo does not get the gate, because adding the caller
  is a thing someone has to remember. *Mitigation:* `agent-loops`'
  `install.sh` seeds the caller from `templates/human-gate.yml` alongside the
  `human-gate` label, so onboarding a repo installs it. Not covered: a repo
  onboarded by hand. Accepted — every current consumer came through the
  installer.
- **One-way sync.** A consumer edits its caller (a new event type, say) and the
  improvement never comes back here. *Mitigation:* the caller has nothing in it
  to improve — the only repo-specific value is `runs-on`, and everything else
  lives in this file. An edit worth making is an edit to this repo by
  construction.
- **Drift.** Gone by construction for the shell, which is what drifted before.
  What *can* still drift: the `agent-loops` sweep's copy of the marker /
  canonical line / stake regex, and the caller stubs' event-type list.
  *Mitigation:* the first is pinned by `agent-loops`'
  `tests/test_human_gate_body_check.py`; the second is not mechanically pinned
  across repos — a caller missing `edited` would simply keep a fixed body red
  until the next event. Known, cheap, visible.
- **Silent failure.** The dangerous shape is a run that passes while checking
  nothing: a job skipped by an `if` nobody re-read, or `require-body-statement`
  quietly set false. *Mitigation:* `selftest.yml` executes the real interface on
  every PR here, and the unit tests fail if either job's shell is empty — the
  slicing that feeds them is asserted, not assumed. Remaining hole: a consumer
  whose caller sets `require-body-statement: false` looks green while enforcing
  half the rule. Nothing here can see that; it is visible in one `grep` across
  the four callers.

## Licence

Public only because GitHub requires it for cross-owner reusable workflows. All rights reserved; no licence is granted.
