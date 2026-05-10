# shared-workflows

Reusable GitHub Actions workflows for cross-cutting CI across every repo
under this account. One source of truth for Claude Code review, browser-
extension publishing, and anything else that recurs.

## Workflows

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
repository secret or inherited from an organization secret. `secrets:
inherit` passes it through.

**Reusable workflows run in the caller's context with the caller's secrets.**
The central repo's secrets are not visible to callers; each caller pays for
its own Claude usage with its own token.

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
