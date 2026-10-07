# workflows

Reusable GitHub Actions workflows shared by repositories in two owners:
[`Lin214ia`](https://github.com/Lin214ia) (personal) and
[`AyakaRadiology`](https://github.com/AyakaRadiology) (organisation).

**This repository is public on purpose.** A private reusable workflow can only
be called from inside its own owner, and these are called from both — so public
is the only shape that works. Nothing here is secret: no tokens, no runner
addresses, no product code. Every workflow uses the caller's built-in
`GITHUB_TOKEN` and `gh`, checks out nothing, and runs no third-party action
(`dependabot-automerge.yml` additionally mints a GitHub App token with GitHub's
official `actions/create-github-app-token`, from credentials the caller passes).

## Workflows

| Workflow | What it does |
| --- | --- |
| [`dependabot-automerge.yml`](.github/workflows/dependabot-automerge.yml) | Arms GitHub's native auto-merge on Dependabot PRs for semver patch/minor updates; leaves majors (and any caller-held dependency) for an agent to review, with a one-time comment and, for majors, the `dependabot-major` label. |
| [`pr-evidence.yml`](.github/workflows/pr-evidence.yml) | Required check `evidence / check`: a PR authored by the agent identity must have a `## Evidence` section with a fenced block, image or table; `experiment`-labelled probes pass and are excluded from quality metrics. |

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
    uses: Lin214ia/workflows/.github/workflows/dependabot-automerge.yml@<full commit sha> # v1.0.0
    with:
      runs-on: '["self-hosted","Linux","X64"]'
      hold-patterns: '@cornerstonejs/*'
    secrets:
      app-id: ${{ secrets.RELEASE_BOT_APP_ID }}
      app-private-key: ${{ secrets.RELEASE_BOT_PRIVATE_KEY }}
```

#### Inputs

| Input | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `runs-on` | string | no | `'"ubuntu-latest"'` | Runner label(s) as a JSON string, and it **defaults** to hosted `ubuntu-latest`, deliberately: this job does no checkout or build, and some caller repos (`Lin214ia/*`) have no self-hosted runner group at all. A caller with its own group should still pass it explicitly. |
| `hold-patterns` | string | no | `''` | Comma-separated dependency-name globs, matched with bash `case`. |
| `arm-majors` | boolean | no | `false` | Do not set `true` without an explicit owner decision — it is a separate policy change from rolling this workflow out. |

#### Secrets (merge identity)

| Secret | Required | Notes |
| --- | --- | --- |
| `app-id` | no | Id of a GitHub App installed on the caller repo with Contents and Pull requests write. |
| `app-private-key` | no | That App's private key (PEM). |

Auto-merge is armed with a token minted from this App, so the merge is
attributed to the App and fires `push` workflows (post-merge CI on `main`,
release-please). A merge armed with `GITHUB_TOKEN` fires none of them (GitHub's
loop prevention), which is what Dependabot merges did before. The job runs
under `pull_request_target` from `dependabot[bot]`, so `secrets.*` in the
caller resolves against the **Dependabot** secret store: set them with
`gh secret set --app dependabot` (or as org Dependabot secrets), not only as
Actions secrets. If either is empty the job emits a `::warning` and falls back
to `GITHUB_TOKEN` rather than failing, so rollout can precede provisioning;
the warning says what is lost.

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

### `pr-evidence`

Direction: Harry, chat 2026-10-03 (raise agent work quality to 9/10).

Makes agent work quality measurable. A PR authored by `hedgehog-agent[bot]`
fails unless its body has a `## Evidence` section holding a fenced code block
(non-empty), a markdown or HTML image, or a table. A PR labelled `experiment`
(load tests, probes, anything not meant to merge) passes and writes
"experiment — excluded from quality metrics" to the job summary, so metrics can
filter on the label. Other authors, Dependabot and release-please pass untouched.

Rule for tooling: **probe PRs carry the `experiment` label.**

Required-check context: **`evidence / check`** (caller job id `evidence`, called
job `check`; both halves are pinned by `tests/test_pr_evidence.py`). The workflow
has no path filter so it reports on every PR. Caller snippet:

```yaml
name: PR evidence

on:
  pull_request_target:
    types: [opened, edited, synchronize, reopened, labeled, unlabeled]

permissions:
  contents: read
  pull-requests: read

jobs:
  evidence:
    uses: Lin214ia/workflows/.github/workflows/pr-evidence.yml@<full commit sha> # v1.0.0
    with:
      runs-on: '["self-hosted","Linux","X64"]'
```

Inputs: `runs-on` (required), `agent-login` (default `hedgehog-agent[bot]`),
`experiment-label` (default `experiment`).

### `check-pins` (composite action)

Fails if any `uses:` in the caller's `.github/workflows/*.yml` or
`.github/actions/**/action.yml` is not a full 40-hex commit SHA followed by a
`# vX.Y.Z` (or `# vX`) comment, which Dependabot needs to bump it. Local `./`
refs and `docker://` images pinned by `@sha256:` digest are exempt. Errors list
`file:line`. Add it to an existing always-on lint job (after checkout):

```yaml
- uses: Lin214ia/workflows/.github/actions/check-pins@<full commit sha> # v1.1.0
```

## Releases: callers pin a SHA

Direction (Harry, chat 2026-10-07): consumers pin a release, not `@main`.

```yaml
uses: Lin214ia/workflows/.github/workflows/<file>@<full commit sha> # v1.2.3
```

Always the full 40-character commit SHA of a release tag, with the version in
the trailing comment. Dependabot's `github-actions` ecosystem reads that
comment and opens a PR for each new release, so the pin moves forward on
purpose, one reviewed (and CI-gated) bump at a time.

[`release-please.yml`](.github/workflows/release-please.yml) keeps one open
`chore(main): release X.Y.Z` PR, versioned from Conventional Commit titles
(`feat` minor, `fix` patch, `!` major). Merging it tags `vX.Y.Z` and creates the
GitHub release; a follow-up job then moves the major tag `v1` to that release
for anyone who accepts a floating ref. Nothing merges the release PR
automatically. The workflow uses `GITHUB_TOKEN`, so the release PR and tag
trigger no other workflows; CI is dispatched onto the release branch instead.

**The original reason for `@main` was drift:** several copies of the same
workflow existed in private repositories and had diverged. A SHA pin brings back
the risk of callers sitting on different versions, which is why Dependabot (not
memory) moves them and why a breaking change must be a major release.

**The trade-off of any shared workflow is real:** a bad release breaks every
caller that bumps to it. What makes that affordable:

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
