# shared-workflows

Reusable GitHub Actions workflows for cross-cutting CI across every repo
under this account. One source of truth for Claude Code review, browser-
extension publishing, and anything else that recurs.

## Workflows

### `node-test.yml` — Node.js lint / typecheck / test / build

Reusable workflow that installs dependencies and runs `lint`,
`typecheck`, `test`, and (opt-in) `build` package.json scripts —
skipping any that don't exist with a notice. Auto-detects the package
manager from the lockfile (pnpm > yarn > bun > npm) and supports an
explicit override. Defaults to Node 24.

Pass `with: run-build: true` for projects (e.g. browser extensions)
where the build itself is the most useful PR-time check; that way the
PR-time `tests.yml` shim subsumes what a separate `build.yml` would
have done.

### `python-test.yml` — Python ruff + pytest

Reusable workflow for ruff lint, ruff format check, and pytest. Defaults
to Python 3.14.

### `publish-chrome.yml` — Chrome Web Store publish

Reusable workflow for releasing browser extensions to the Chrome Web
Store. Handles 429 + 5xx retries (with `Retry-After` honored), parses
`uploadState` and `status[]` body fields (HTTP 200 + `uploadState:
FAILURE` is a real silent-failure mode in naive code), staggers parallel
releases deterministically by repo hash, and skip-with-warning when
secrets are missing.

### `publish-firefox.yml` — Mozilla Add-ons (AMO) publish

Same shape as Chrome but for AMO. Honors the actual `Retry-After` HTTP
header (the older `.retry_after` JSON-body pattern is wrong), uses the
multipart-split pattern for `release_notes` (AMO rejects translatable
fields combined with multipart source uploads), and the same
deterministic stagger.

### `dependabot-auto-merge.yml` — auto-merge Dependabot PRs

Reusable workflow that merges a Dependabot PR after Claude (or any
trusted reviewer) approves it. Two modes:

- `mode: auto` (default) — calls `gh pr merge --auto`. Pair with a
  `pull_request_review` trigger in your shim. Requires GitHub's
  auto-merge feature (Pro+ for private repos, Free only for public).
- `mode: direct` — calls `gh pr merge` immediately after CI succeeds.
  Pair with a `workflow_run` trigger watching your CI workflow. For
  Free private repos where `--auto` isn't available (see
  `github-private-repo-auto-merge-workaround` skill).

Auto-detects the merge method from the repo's allowed methods (priority:
squash > rebase > merge); override via `merge-method:`.

### Recommended PR-checks shape

For the cleanest gate (review skipped if tests/build/CodeQL fail), each
caller combines `node-test.yml` + `claude-code-review.yml` into one
workflow with `needs:` ordering:

```yaml
name: PR checks
on:
  pull_request:
    types: [opened, synchronize]
    paths-ignore:
      - '.github/workflows/pr-checks.yml'

permissions: { contents: read }
concurrency:
  group: pr-checks-${{ github.ref }}
  cancel-in-progress: true

jobs:
  tests:
    permissions: { contents: read }
    uses: dustfeather/shared-workflows/.github/workflows/node-test.yml@v1
    with:
      run-build: true   # extension repos / projects with a meaningful build script
  review:
    needs: tests   # review skips if tests fail
    permissions:
      contents: read
      pull-requests: write
      issues: read
      id-token: write
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
    secrets: inherit  # use explicit pass for cross-owner — see "Cross-owner callers"
```

`needs: tests` gates review on tests/build passing. The central review
workflow itself adds a cheap bash gate that skips when CodeQL has
already failed (no agent spin-up, no Claude tokens) — treating
IN_PROGRESS as "proceed" so reviews aren't blocked waiting for CodeQL.

### `claude-code-review.yml` — automatic PR review

Runs Claude Code Action on every PR (after a trusted-actor / allowed-bot
gate). Uses a structured prompt that splits dependabot bumps from human PRs:

- **Dependabot track**: classifies the bump (patch/minor/major), checks the
  changelog for breaking changes, approves or requests changes via
  `gh pr review` based on the rules.
- **Standard track**: leaves inline comments + a top-level summary; never
  auto-approves human PRs.

### `claude.yml` — interactive `@claude` mention

Picks up `@claude` mentions in issues, issue comments, PR review comments,
and PR reviews from a trusted actor and hands the conversation to Claude
Code Action.

## Usage

Each calling repo has a tiny shim. The shim handles trigger configuration
(which can't be inside a `workflow_call` workflow); the central workflow
handles all the logic.

### `.github/workflows/claude-code-review.yml` (shim)

```yaml
name: Claude Code Review
on:
  pull_request:
    types: [opened, synchronize]
    # Avoid the claude-code-action self-modify validation guard tripping
    # on PRs that change this very file. Keep the path narrow — broader
    # filters like '.github/**' would also skip review on workflow PRs
    # that are unrelated to this file.
    paths-ignore:
      - '.github/workflows/claude-code-review.yml'

jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
    secrets: inherit
```

### `.github/workflows/claude.yml` (shim)

```yaml
name: Claude Code
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened, assigned]
  pull_request_review:
    types: [submitted]

jobs:
  claude:
    uses: dustfeather/shared-workflows/.github/workflows/claude.yml@v1
    secrets: inherit
```

## Secrets

Each calling repo must have `CLAUDE_CODE_OAUTH_TOKEN` set — either as a
repository secret or inherited from an organization secret. The shim then
needs to pass it through.

**Reusable workflows run in the caller's context with the caller's secrets.**
The central repo's secrets are not visible to callers; each caller pays for
its own Claude usage with its own token.

### Same-owner callers — `secrets: inherit` works

When the calling repo is owned by the same account as `shared-workflows`
(i.e. another `dustfeather/*` repo), the shim can use:

```yaml
jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
    secrets: inherit
```

`inherit` carries every secret the caller can see — both repo-level and
org-level — into the called workflow.

### Cross-owner callers — explicit secret pass required

When the caller lives under a **different owner** (e.g. an `ITGuys-RO/*`
repo calling `dustfeather/shared-workflows`), `secrets: inherit` does NOT
reliably pass **org-level secrets with "selected" visibility** across the
owner boundary. You'll see this error from the called workflow's first
job:

```
Error when evaluating 'secrets'. .github/workflows/claude-code-review.yml
(Line: N, Col: M): Secret CLAUDE_CODE_OAUTH_TOKEN is required, but not
provided while calling.
```

The fix is to pass the secret explicitly. The reference is resolved in
the **caller's** context (where the org secret IS visible to the repo)
and forwarded as a named secret:

```yaml
jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

This works in both cases (same-owner and cross-owner), so when in doubt,
prefer the explicit form. `inherit` is just a convenience shortcut for
the same-owner case.

## Configuration

Both workflows accept optional `with:` inputs:

| Input | Workflow | Default | Purpose |
|-------|----------|---------|---------|
| `trusted-actors` | both | `dustfeather` | Comma-separated GitHub actors allowed to trigger |
| `allowed-bots` | review | `dependabot[bot]` | Bots whose PRs trigger reviews |
| `show-full-output` | review | `true` | Surface every tool call in the action log |

Example (private repo where another teammate should also be able to invoke):

```yaml
jobs:
  review:
    uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
    with:
      trusted-actors: "dustfeather,collaborator-handle"
    secrets: inherit
```

## Versioning

Callers pin to `@v1` (the moving major-version tag, GitHub Actions
convention). Backwards-compatible improvements move `v1` forward;
breaking changes (new required inputs, changed defaults) bump to `v2`.

## Extension publishing usage

For each browser-extension repo's `release.yml` (after a `build` job that
uploads an artifact named `extensions` containing the packaged `.zip`,
`.xpi`, source `.zip`, and `release-notes.txt`):

```yaml
publish-chrome:
  needs: build
  uses: dustfeather/shared-workflows/.github/workflows/publish-chrome.yml@v1
  with:
    zip-name: my-ext-chrome-${{ needs.build.outputs.tag }}.zip
  secrets: inherit

publish-firefox:
  needs: build
  uses: dustfeather/shared-workflows/.github/workflows/publish-firefox.yml@v1
  with:
    xpi-name: my-ext-firefox-${{ needs.build.outputs.tag }}.xpi
    source-name: source-${{ needs.build.outputs.tag }}.zip
    addon-id: my-ext@dustfeather
  secrets: inherit
```

`secrets: inherit` passes through `CHROME_*` and `AMO_*` secrets from the
calling repo. If a repo doesn't have a given store's secrets configured,
the matching publish job emits a `::warning::` and exits 0 cleanly.

## History

This repo subsumes the earlier `dustfeather/extension-workflows` (which
was extension-publish-only). All publish workflows now live here so
there's one home for shared CI.
