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

# Pre-bake ARC runner image for `claude-code-action@v1`

## Problem

`anthropics/claude-code-action@v1` burns 10-25s/job installing Bun (via `oven-sh/setup-bun`) + downloading Claude Code CLI. GitHub-hosted runners amortize via image caching; ARC ephemeral pods (`gha-runner-scale-set`, fresh pod per job) pay it every invocation.

Naive attempts that DON'T help:

- Pre-installing **Node**. Action uses Bun, not Node. `setup-bun` still runs.
- Pre-installing **Bun at different version** than action expects. Without telling action where, `setup-bun` still downloads pinned version.
- Adding `container:` to job. ARC pods already pull fresh runner image/job; layering another container = more boot time, not less.

## Context / Trigger Conditions

All true:

- Caller uses `anthropics/claude-code-action@v1` (or wrapper like `dustfeather/shared-workflows/.github/workflows/claude-code-review.yml`).
- ARC pool: `helm list -n arc-runners` shows `gha-runner-scale-set` releases; `kubectl get autoscalingrunnerset -A` shows sets on upstream `ghcr.io/actions/actions-runner`.
- Job logs show wall-clock in `Install Bun` + `@anthropic-ai/claude-code` install.

## Solution

### Step 1 — Confirm what action installs

Read live `action.yml` at `https://raw.githubusercontent.com/anthropics/claude-code-action/main/action.yml`. Verify each time (drifts):

1. Setup step is `oven-sh/setup-bun` w/ pinned `bun-version` (e.g. `1.3.14`).
2. Two inputs opt out:
   - `path_to_bun_executable` — absolute path to pre-installed Bun. Skips `setup-bun`.
   - `path_to_claude_code_executable` — absolute path to pre-installed Claude Code CLI. Skips download.

Third step ALWAYS runs, CANNOT skip: `bun install --production` inside `${GITHUB_ACTION_PATH}`. Installs action's own JS deps, hits network every time. Pre-bake covers Bun + Claude Code, not this.

### Step 2 — Build derivative image

`FROM` upstream ARC runner image. Keeps `runner` user (UID 1001), `/home/runner/run.sh` entrypoint, and crucially `/home/runner/externals/`. The `init-dind-externals` initContainer (ARC Helm chart) does `cp -r /home/runner/externals/. /home/runner/tmpDir/` from this image; wipe it → DinD breaks.

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

### Step 3 — Build on GitHub-hosted runners, not own pool

Image pulled by same pool that builds it. Never build runner image on runners that depend on it. Run build on `runs-on: ubuntu-latest`, push to GHCR (`ghcr.io/<owner>/actions-runner-claude:latest` + `:sha-${{ github.sha }}`), registry-based buildx cache.

### Step 4 — Wire workflow to pre-baked paths

In reusable review workflow, surface paths as optional inputs:

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

Callers on pre-baked image: `path-to-bun: /usr/local/bin/bun` + `path-to-claude: /usr/local/bin/claude`. Stock-runner callers leave empty, behavior unchanged. Backwards-compatible — minor/patch bump only.

### Step 5 — Roll out to ALL caller ARC scale sets, cross-org

`gha-runner-scale-set` chart sets runner container image from Helm values. Override per release:

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

**Critical**: override only `arc-df-*` and forget `arc-itguys-ro*` → ITGuys-RO PRs pay full install cost AND `path-to-*` inputs point at non-existent binaries → review breaks. Roll image override out to every scale set receiving workflow calls with new inputs, or gate input defaults per caller repo.

### Step 6 — Make image cross-org pullable

ITGuys-RO ARC pods pull `ghcr.io/dustfeather/...` — different owner. Two options:

1. Publish GHCR package **public**. Simplest. Set once in GHCR package settings after first push.
2. Keep private + add `imagePullSecret` to every cross-org release. More moving parts.

Option 1 fine for tooling images w/ no secrets — this qualifies.

## Verification

1. **Image content**: `docker run --rm -it ghcr.io/<owner>/actions-runner-claude:latest bash -c "bun --version && claude --version && gh --version && jq --version && node --version"`.
2. **Action skips Bun install**: post-rollout run log shows `Install Bun` step absent or executing `path_to_bun_executable != ''` branch (echoes "Using custom Bun executable…" — no network).
3. **Action skips Claude Code install**: no `claude-code` download line; `claude --version` from action matches image.
4. **DinD still works**: Docker-using job (e.g. `docker build`) passes on new image → confirms `/home/runner/externals/` survived.

## Example

Cross-org rollout — shared workflow at `dustfeather/shared-workflows`, caller in `ITGuys-RO/some-app`:

1. Merge image Dockerfile + build workflow to `dustfeather/shared-workflows`. First push to `main` → `ghcr.io/dustfeather/actions-runner-claude:latest`.
2. GHCR package settings → switch visibility to **Public**.
3. For each `arc-df-*` (10+) and `arc-itguys-ro*` (2+) release, `helm upgrade --reuse-values` w/ image override.
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

- **Bun version pin drift**: when `claude-code-action` bumps `setup-bun` `bun-version`, bump `BUN_VERSION` in Dockerfile. Mismatch isn't a hard failure (action accepts whatever `bun` is on path the input points to) but stays a footgun on Bun breaking changes.
- **Cannot skip `bun install --production`** inside action's own dir. Action installs own JS deps from `package.json` after `actions/checkout`-ing itself. Pre-baking action's deps into runner image = fragile (version coupling), not worth it.
- **`container:` is not a substitute**. Adds extra container pull+start per job, usually more expensive than install steps it would save.
- **Architectures**: build `linux/amd64` for x86_64 ARC nodes. Add `linux/arm64` when needed — one extra `platforms:` value in `buildx`, GHCR storage doubles.
- **Self-bootstrap hazard**: don't `runs-on:` own self-hosted pool for image build. Broken image → can't rebuild.
- **Action install steps remaining even w/ both inputs set**: composite action still apt installs `bubblewrap` + `socat` when `allowed_non_write_users != ''`. If you use that input, bake those in too.

## References

- `anthropics/claude-code-action` `action.yml`: https://raw.githubusercontent.com/anthropics/claude-code-action/main/action.yml
- ARC `gha-runner-scale-set` chart values: https://github.com/actions/actions-runner-controller/tree/master/charts/gha-runner-scale-set
- Upstream ARC runner image: `ghcr.io/actions/actions-runner`
