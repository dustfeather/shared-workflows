# shared-workflows

General-purpose reusable GitHub Actions workflows for cross-cutting CI.
Currently centralizes Claude Code review across every repo so the prompt,
trusted-actor gate, and tool allowlist are fixed in one place.

For browser-extension publishing (Chrome Web Store + Mozilla Add-ons), see
the separate `dustfeather/extension-workflows` repo.

## Workflows

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
