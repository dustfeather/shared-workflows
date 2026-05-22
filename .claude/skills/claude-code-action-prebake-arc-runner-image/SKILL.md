---
name: claude-code-action-prebake-arc-runner-image
description: |
  Pre-bake a self-hosted GitHub Actions Runner Controller (ARC) container
  image so `anthropics/claude-code-action@v1` skips its in-job Bun and
  Claude Code installs. Use when: (1) caller repos use ARC ephemeral
  runners (`gha-runner-scale-set` Helm chart, `autoscalingrunnerset`
  CRDs) instead of GitHub-hosted runners and reviews repeatedly waste
  10-25s per job pulling Bun + Claude Code; (2) deciding whether
  pre-baking Node into the runner image will help speed up
  `claude-code-action` (it won't — the action uses Bun, not Node);
  (3) you want to bake `gh`/`jq`/Node/Bun/`@anthropic-ai/claude-code`
  into a derivative image like `ghcr.io/<owner>/actions-runner-claude`
  and wire it into Helm-managed ARC scale sets across MULTIPLE GitHub
  orgs (e.g. `dustfeather/*` + `ITGuys-RO/*`); (4) you tried baking
  Node only and saw no speedup; (5) you tried baking a different Bun
  version than the action expects and the action still ran
  `setup-bun`; (6) you can't figure out which action inputs let you
  skip the installs. Covers the two action inputs that actually skip
  installs, the unavoidable `bun install --production` network hit,
  the cross-org GHCR pull caveat, and what NOT to break in the base
  ARC runner image (the `externals/` dir consumed by the
  `init-dind-externals` initContainer).
author: Claude Code
version: 1.0.0
date: 2026-05-23
---

# Pre-bake the ARC runner image for `claude-code-action@v1`

## Problem

`anthropics/claude-code-action@v1` spends 10-25 seconds at the top of
every job installing Bun (via `oven-sh/setup-bun`) and downloading the
Claude Code CLI. On GitHub-hosted runners this is amortized by aggressive
image caching, but on ARC self-hosted ephemeral runners (the
`gha-runner-scale-set` Helm chart, where each job is a fresh pod) the
cost lands on every single invocation.

Naive attempts that DON'T help:

- Pre-installing **Node** into the runner image. The action's runtime
  is Bun, not Node. `setup-bun` still runs.
- Pre-installing **Bun at a different version** than the action expects.
  Without telling the action where to find it, `setup-bun` still runs
  and downloads the pinned version anyway.
- Adding a `container:` directive to the job. ARC ephemeral pods already
  pull a fresh runner image per job; layering another container on top
  is extra boot time and registry traffic, not less.

## Context / Trigger Conditions

Apply this skill when ALL of the following are true:

- The caller workflow uses `anthropics/claude-code-action@v1` (or one of
  its reusable wrappers like
  `dustfeather/shared-workflows/.github/workflows/claude-code-review.yml`).
- The runner pool is ARC-managed: `helm list -n arc-runners` shows
  releases of `gha-runner-scale-set`, and
  `kubectl get autoscalingrunnerset -A` shows runner sets backed by the
  upstream `ghcr.io/actions/actions-runner` image.
- Job timing logs show meaningful wall-clock spent in steps named
  `Install Bun` and an install of `@anthropic-ai/claude-code`.

## Solution

### Step 1 — Confirm what the action actually installs

Read the live `action.yml` at
`https://raw.githubusercontent.com/anthropics/claude-code-action/main/action.yml`.
Two facts to verify each time (they can drift across versions):

1. The setup step is `oven-sh/setup-bun` with a pinned `bun-version` —
   note the version (e.g. `1.3.14`).
2. The action exposes two inputs that opt out of its own installs:
   - `path_to_bun_executable` — absolute path to a pre-installed Bun.
     When set, the `setup-bun` step is skipped.
   - `path_to_claude_code_executable` — absolute path to a
     pre-installed Claude Code CLI. When set, the action skips its own
     download.

A third install step the action ALWAYS runs and you CANNOT skip:
`bun install --production` inside `${GITHUB_ACTION_PATH}`. This installs
the action's own JS deps and hits the network every time. Pre-bake helps
amortize Bun + Claude Code but not this.

### Step 2 — Build the derivative image

`FROM` the upstream ARC runner image so the `runner` user (UID 1001),
the `/home/runner/run.sh` entrypoint, and crucially the
`/home/runner/externals/` directory all stay intact. The
`init-dind-externals` initContainer the ARC Helm chart adds does
`cp -r /home/runner/externals/. /home/runner/tmpDir/` from this image;
if you wipe or replace that directory, DinD breaks.

Skeleton:

```dockerfile
ARG BASE_TAG=latest
FROM ghcr.io/actions/actions-runner:${BASE_TAG}

USER root

ARG BUN_VERSION=1.3.14   # match claude-code-action's setup-bun pin
ARG NODE_MAJOR=22

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg jq unzip; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -; \
    apt-get install -y --no-install-recommends gh nodejs; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl -fsSL https://bun.sh/install | bash -s "bun-v${BUN_VERSION}"; \
    install -m 0755 /root/.bun/bin/bun /usr/local/bin/bun; \
    rm -rf /root/.bun; \
    bun --version

RUN set -eux; \
    npm install -g @anthropic-ai/claude-code; \
    if [ ! -e /usr/local/bin/claude ] && [ -e /usr/bin/claude ]; then \
        ln -s /usr/bin/claude /usr/local/bin/claude; \
    fi; \
    claude --version

USER runner
```

### Step 3 — Build on GitHub-hosted runners, not on your own pool

The image will be pulled by the same pool that builds it. Bootstrap risk:
never build the runner image on the runners that depend on it. Run the
build workflow on `runs-on: ubuntu-latest`, push to GHCR
(`ghcr.io/<owner>/actions-runner-claude:latest` and
`:sha-${{ github.sha }}`), use a registry-based buildx cache.

### Step 4 — Wire the workflow to the pre-baked paths

In the reusable review workflow, surface the two paths as optional
inputs and pass them through:

```yaml
inputs:
  path-to-bun:
    type: string
    default: ""
  path-to-claude:
    type: string
    default: ""

# ...

- uses: anthropics/claude-code-action@v1
  with:
    path_to_bun_executable: ${{ inputs.path-to-bun }}
    path_to_claude_code_executable: ${{ inputs.path-to-claude }}
```

Callers on the pre-baked image set
`path-to-bun: /usr/local/bin/bun` and
`path-to-claude: /usr/local/bin/claude`. Callers on stock runners leave
them empty and behavior is unchanged. This is a backwards-compatible
change — only a minor/patch bump is needed.

### Step 5 — Roll out to ALL caller ARC scale sets, across orgs

The `gha-runner-scale-set` chart sets the runner container image from
Helm values. Override per release:

```yaml
template:
  spec:
    containers:
      - name: runner
        image: ghcr.io/<owner>/actions-runner-claude:latest
```

```bash
helm -n arc-runners upgrade --reuse-values <release> \
    oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
    --version <chart-ver> \
    -f values-override.yaml
```

**Critical**: if you only override `arc-df-*` (dustfeather-owned) and
forget the `arc-itguys-ro*` releases (ITGuys-RO-owned), reviews on
ITGuys-RO PRs still pay the full install cost AND the `path-to-*`
workflow inputs point at binaries that don't exist on those pods —
breaking the review. Roll the image override out to every scale set
that will receive workflow calls with the new inputs, or gate the input
defaults per caller repo.

### Step 6 — Make the image cross-org pullable

ARC pods on the ITGuys-RO scale sets pull `ghcr.io/dustfeather/...` —
a different GitHub owner. Two options:

1. Publish the GHCR package with **public** visibility. Simplest. Set
   once in the GHCR package settings after the first push.
2. Keep it private and add an `imagePullSecret` to every cross-org
   `gha-runner-scale-set` release. More moving parts.

Option 1 is fine for tooling images that contain no secrets — this one
qualifies.

## Verification

1. **Image content**: `docker run --rm -it
   ghcr.io/<owner>/actions-runner-claude:latest \
   bash -c "bun --version && claude --version && gh --version && jq --version && node --version"`.
2. **Action skips Bun install**: after rollout, the workflow run log
   shows the `Install Bun` step as either absent or executing the
   `path_to_bun_executable != ''` branch (which only echoes "Using
   custom Bun executable…" — no network).
3. **Action skips Claude Code install**: no `claude-code` download line
   in the run log; `claude --version` from the action matches what's in
   the image.
4. **DinD still works on the pre-baked image**: a job that uses Docker
   (e.g. `docker build`) on the new image still passes — confirms
   `/home/runner/externals/` survived.

## Example

Cross-org rollout sketch for a repo that owns the shared workflow at
`dustfeather/shared-workflows` and a caller in `ITGuys-RO/some-app`:

1. Merge the image Dockerfile + build workflow to
   `dustfeather/shared-workflows`. First push to `main` produces
   `ghcr.io/dustfeather/actions-runner-claude:latest`.
2. In the GHCR package settings, switch package visibility to
   **Public**.
3. For each of the 10+ `arc-df-*` and 2+ `arc-itguys-ro*` releases,
   `helm upgrade --reuse-values` with the image override.
4. In `ITGuys-RO/some-app/.github/workflows/review.yml`:
   ```yaml
   jobs:
     review:
       uses: dustfeather/shared-workflows/.github/workflows/claude-code-review.yml@v1
       with:
         path-to-bun: /usr/local/bin/bun
         path-to-claude: /usr/local/bin/claude
       secrets:
         CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
   ```

## Notes

- **Bun version pin drift**: when `claude-code-action` bumps its
  `setup-bun` `bun-version`, bump `BUN_VERSION` in the Dockerfile to
  match. A mismatch is not a hard failure (the action accepts whatever
  `bun` is on the path the input points to) but stays a footgun
  whenever Bun has breaking changes.
- **You cannot skip `bun install --production`** inside the action's
  own dir. That's the action installing its own JS deps from
  `package.json` after `actions/checkout`-ing itself. Pre-baking the
  action's dependencies into the runner image is fragile (version
  coupling) and not worth it.
- **`container:` is not a substitute** for the runner image override on
  ARC. Adding `container:` to the job makes the runner pod pull AND
  start an additional container per job, which usually costs more than
  the install steps it would save.
- **Architectures**: build `linux/amd64` if your ARC node is x86_64.
  Add `linux/arm64` if/when you add an arm node — `buildx` makes this
  one extra `platforms:` value, but the GHCR storage doubles.
- **Self-bootstrap hazard**: don't `runs-on:` your own self-hosted
  pool for the image build. If the image is broken, you can no longer
  rebuild it.
- **Action install steps that remain even with both inputs set**: the
  composite action still does an apt install of `bubblewrap` + `socat`
  when `allowed_non_write_users != ''`. If you use that input, bake
  those packages in too.

## References

- `anthropics/claude-code-action` `action.yml`:
  https://raw.githubusercontent.com/anthropics/claude-code-action/main/action.yml
- ARC `gha-runner-scale-set` chart values reference:
  https://github.com/actions/actions-runner-controller/tree/master/charts/gha-runner-scale-set
- Upstream ARC runner image: `ghcr.io/actions/actions-runner`
