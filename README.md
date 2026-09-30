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
| [`tier3-gate.yml`](.github/workflows/tier3-gate.yml) | Fails a pull request that changes Tier 3 paths — the files that decide whether other changes are correct — unless it carries the `human-gate` label. |
| [`human-gate.yml`](.github/workflows/human-gate.yml) | Enforces the `human-gate` label: disables auto-merge on a labelled PR, and fails the run when the PR body does not tell the owner the merge is theirs and what is at stake in it. |
| [`dependabot-automerge.yml`](.github/workflows/dependabot-automerge.yml) | Arms GitHub's native auto-merge on Dependabot PRs for semver patch/minor updates; leaves majors (and any caller-held dependency) for an agent to review, with a one-time comment and, for majors, the `dependabot-major` label. |

### `tier3-gate`

Tier 3 is **the things that judge other changes**: CI workflows, test and
Playwright configuration, time budgets, screenshot baselines, sudoers, runner
tables, release plumbing, CODEOWNERS. A change to one of those cannot be judged
by the thing it changes, which is how the September 2026 CI week happened — a
Playwright parallelisation merged on its own green run and took the workstation
down for a day, and a 3D plate was shrunk to a quarter of its depth by a PR
whose pixel test it had just rewritten.

This workflow asserts one implication: *touching Tier 3 implies the
`human-gate` label*. It never re-asserts what `human-gate.yml` owns — the stake
line, disabling auto-merge — so the two checks cannot disagree about one label.

Caller snippet (the job id is half of the required-check context, `tier3 / gate`):

```yaml
name: Tier 3 gate

on:
  pull_request_target:
    types: [opened, reopened, synchronize, edited, labeled, unlabeled]

permissions:
  contents: read
  pull-requests: read

jobs:
  tier3:
    uses: Lin214ia/workflows/.github/workflows/tier3-gate.yml@main
    with:
      runs-on: '["self-hosted","Linux","X64"]'
      paths: |
        apps/desktop/playwright.config.ts
        **/*-snapshots/**
```

Three details are not style choices:

- **`pull_request_target`, not `pull_request`.** Under `pull_request` GitHub
  runs the workflow file from the PR's merge ref, so a PR that edits its own
  caller is judged by its own edit — and `.github/**` is exactly what this
  guards. `pull_request_target` runs the base branch's copy, which is safe here
  because nothing is checked out and no PR code is executed. The token defaults
  to write under that trigger, so the caller narrows it explicitly.
- **No `paths:` filter on the caller.** A required check whose workflow never
  starts leaves its context pending forever and freezes the repository. The gate
  always runs and passes fast when nothing matches.
- **`.github/**` is always Tier 3**, whatever `paths` says, so a repository
  cannot declare its own guard out of scope. A file list long enough to hit the
  API's 3000-file cap also counts as touching Tier 3: a truncated list cannot
  prove the absence of one.

**Rollout order matters.** Land the caller on the repository's default branch
and watch the `tier3 / gate` context report on a real pull request *first*, then
add that context to the repository's required checks. The reverse order leaves
every PR waiting for a context that has never reported.

**What it does not do.** Agents here act with the owner's own GitHub identity,
so an agent can add the label and merge. This stops the accidental and the
automated paths — auto-merge, the self-merge sweep — and makes the requirement
loud. Distinguishing the owner from an agent holding his token needs a second
account or a bot identity, which is a separate decision.

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
    # Job-scoped on purpose: see Permissions below. `contents: write` is what
    # `gh pr merge --disable-auto` needs; the top of the file stays read-only so
    # every other job in it does.
    permissions:
      contents: write
      pull-requests: write
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

**The calling job** must grant them:

```yaml
jobs:
  human-gate:
    permissions:
      contents: write
      pull-requests: write
    uses: Lin214ia/workflows/.github/workflows/human-gate.yml@main
```

**A reusable workflow cannot raise the caller's `GITHUB_TOKEN` permissions, only
narrow them.** This workflow therefore declares none of its own — the grant is
entirely the caller's. A caller that forgets does not get a silent skip: the `gh`
calls fail and the run goes red.

`contents: **write**`, not `read`. This is measured, not guessed. Turning
auto-merge *off* with `gh pr merge --disable-auto` is a merge-capable operation
on contents — the same scope that turning it *on* needs. Under `contents: read`
the `enforce` job finds auto-merge enabled, prints that it is disabling it, and
then dies on

```
GraphQL: Resource not accessible by integration (disablePullRequestAutoMerge)
```

so the gate goes red *and* leaves auto-merge armed on a PR only the owner may
merge. That is run `34901519703` in `AyakaRadiology/needle-simulator` and run
`35449316166` in `Lin214ia/infra`.

**Scope it to the job, not to the file.** `contents: write` is not
operation-specific: a token holding it can push to the default branch, not only
disable auto-merge. What bounds the exposure here is *which steps see it*. A
job-scoped grant is handed only to this reusable workflow's own steps, which
live in this repository, use nothing but the built-in `GITHUB_TOKEN` and `gh`,
and **check out no pull-request code** — there is no `actions/checkout` and no
third-party action anywhere in `human-gate.yml`, so no code from the pull
request ever runs beside that token. Raising the grant at the top of the caller
file instead would hand the same write to every other job in it, including any
that does check the PR out. Keep the file-level block at `contents: read`.

Both jobs of this workflow — `enforce` and `body` — run with whatever the
calling job grants, because the reusable workflow declares no `permissions:` of
its own. `body` only reads and comments, so the write is surplus to it; that is
the cost of the grant being per-call rather than per-job, and it is bounded by
the same two facts above.

### `dependabot-automerge`

Direction (Harry, 2026-09-27 chat): patch/minor Dependabot PRs merge
themselves; majors are left for an agent to review weekly; applied across
every repo running Dependabot.

Same shape as the fleet's original single-repo copy
(`AyakaRadiology/needle-simulator`, now a thin caller of this file): it reads
`dependabot/fetch-metadata`'s `update-type` and `dependency-names`, arms
`gh pr merge --auto --squash` for semver patch/minor, and otherwise leaves the
PR alone with a one-time comment. Two inputs generalise what used to be
hardcoded per repo:

- **`hold-patterns`** — comma-separated dependency-name globs (bash `case`
  matching) that are never armed regardless of update-type, e.g. needle-
  simulator's `@cornerstonejs/*` (a CS3D minor has broken the desktop app's
  rendering in ways CI does not catch) or scl3300-stream's `pico-sdk`
  submodule (its own `dependabot.yml` documents gitsubmodule bumps as "not
  auto-merge candidates").
- **`arm-majors`** — off by default. A major update gets the
  `dependabot-major` label (created idempotently) and a one-time "left for an
  agent to review" comment instead of being armed. Setting this `true` arms
  majors too (still labelled) but is a separate decision from adopting this
  workflow at all — no caller in this fleet sets it.

A grouped PR (`.github/dependabot.yml` `groups:`) is judged by
`update-type` alone, which `fetch-metadata` documents as the worst case across
the whole group — a major bump anywhere in the group reports major and the PR
is left unarmed, with no per-dependency parsing needed here.

Caller snippet:

```yaml
name: Dependabot auto-merge

on:
  pull_request_target:
    types: [opened, synchronize, reopened]

permissions:
  contents: write
  pull-requests: write

jobs:
  dependabot-automerge:
    if: github.actor == 'dependabot[bot]'
    uses: Lin214ia/workflows/.github/workflows/dependabot-automerge.yml@main
    with:
      runs-on: '["self-hosted","Linux","X64"]'
      hold-patterns: '@cornerstonejs/*'
```

#### Inputs

| Input | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `runs-on` | string | no | `'"ubuntu-latest"'` | Same JSON-string shape as `human-gate.yml`'s input, but **defaults** to hosted `ubuntu-latest` — unlike that one, deliberately: this job does no checkout or build, and some caller repos (`Lin214ia/*`) have no self-hosted runner group at all. A caller with its own group should still pass it explicitly. |
| `hold-patterns` | string | no | `''` | Comma-separated dependency-name globs, matched with bash `case`. |
| `arm-majors` | boolean | no | `false` | Do not set `true` without an explicit owner decision — it is a separate policy change from rolling this workflow out. |

#### Permissions

The caller must grant them:

```yaml
permissions:
  contents: write
  pull-requests: write
```

`contents: write` is what lets `gh pr merge --auto` arm the merge and
`gh label create`/`gh pr edit --add-label` manage the `dependabot-major`
label; `pull-requests: write` is for the review comments. As with the other
workflows here, this file declares neither permission itself — a reusable
workflow can only narrow a caller's token, never raise it.

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
