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
`human-gate` label*. The per-PR human gate itself was abolished on 2026-09-27
(the owner decides direction once and no longer reviews individual pull
requests), so no repository calls this workflow today; it is kept, with its
label configurable through the `label` input, until someone decides whether
Tier 3 needs a replacement mechanism.

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
| `runs-on` | string | no | `'"ubuntu-latest"'` | Runner label(s) as a JSON string, and it **defaults** to hosted `ubuntu-latest`, deliberately: this job does no checkout or build, and some caller repos (`Lin214ia/*`) have no self-hosted runner group at all. A caller with its own group should still pass it explicitly. |
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

**The problem being solved was drift.** Several copies of the same workflow
existed in private repositories and had already diverged (different runner
labels, indentation and comment sentences, and one copy carried a job the others
lacked), and nothing anywhere would have told anyone. A tag or a SHA pin
reintroduces exactly that: several pins that get bumped at different times, i.e.
drift with a version number on it.

**The trade-off is real and stated plainly:** a bad merge to `main` here breaks
every caller at once. What makes that affordable:

- One owner (`Lin214ia`) writes here, and `main` is protected — changes arrive
  through a PR.
- [`ci.yml`](.github/workflows/ci.yml) runs `actionlint` (pinned release,
  checksum-verified) with `shellcheck`, plus tests that extract each job's
  `run:` block and **execute it** against a stubbed `gh`.
- [`selftest.yml`](.github/workflows/selftest.yml) calls the workflow the way a
  consumer does, on every PR to this repo. A `workflow_call` interface error
  surfaces here as a `startup_failure` before it surfaces in the callers.
- The blast radius is a *check*, not a deploy. A broken run blocks nothing
  unless a consumer made it a required status check.

## Development

```bash
actionlint                                  # needs shellcheck on PATH
python3 -m unittest discover -s tests -v    # stdlib only; needs jq for the gh stubs
```

The tests read their inputs **back out of the YAML** rather than transcribing
them, so a test cannot pass against a constant the workflow no longer carries.

## Six months from now, this has broken. What broke?

- **Manual steps.** A new repo does not get a workflow, because adding the caller
  is a thing someone has to remember. *Mitigation:* the caller has nothing in it
  to forget beyond `runs-on`; Dependabot callers are checked by infra's
  session-start health hook (`check_dependabot_automerge`).
- **One-way sync.** A consumer edits its caller (a new event type, say) and the
  improvement never comes back here. *Mitigation:* the caller has nothing in it
  to improve — the only repo-specific value is `runs-on`, and everything else
  lives in this file. An edit worth making is an edit to this repo by
  construction.
- **Drift.** Gone by construction for the shell, which is what drifted before.
  What *can* still drift is a caller's event-type list, which is not
  mechanically pinned across repos. Known, cheap, visible.
- **Silent failure.** The dangerous shape is a run that passes while checking
  nothing: a job skipped by an `if` nobody re-read. *Mitigation:*
  `selftest.yml` executes the real interface on every PR here, and the unit
  tests fail if a job's shell is empty — the slicing that feeds them is
  asserted, not assumed.

## Licence

Public only because GitHub requires it for cross-owner reusable workflows. All rights reserved; no licence is granted.
