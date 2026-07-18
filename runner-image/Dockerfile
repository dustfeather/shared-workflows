# Pre-baked ARC self-hosted runner image. Carries the tooling needed by
# `claude-code-review.yml` and the other reusable workflows in this repo,
# AND the generic in-cluster-deploy tooling (kubectl, envsubst) that scale
# sets use to `kubectl apply` manifests against the cluster they run in
# (e.g. ITGuys-RO/alpaca-opus-trading's deploy.yml). It is the standard
# runner image for both the review workflows and in-cluster CI deploys.
#
# Layered on top of the upstream ARC runner image so the `runner` user,
# `/home/runner/run.sh` entrypoint, and `externals/` dir (consumed by the
# `init-dind-externals` initContainer) stay intact.
#
# What this bakes in beyond the base image:
#   - git (already in base, kept for clarity), jq, gh
#   - kubectl (Kubernetes apt repo, minor-pinned) + gettext-base (envsubst),
#     for workflows that `kubectl apply` to the cluster they run in. The
#     binary alone is inert without RBAC: each ARC scale set runs under its
#     own `<release>-gha-rs-no-permission` SA, so capability is gated per
#     scale set, not by the image.
#   - Node.js (NodeSource major release) for the image's own build-time and
#     PATH use (npm global installs, claude-code CLI).
#   - A second Node, the official tarball, unpacked into the runner
#     tool-cache (/opt/hostedtoolcache/node/<ver>/x64). `actions/setup-node`
#     resolves `node-version:` from there with zero download, so callers of
#     node-test.yml skip the ~20 s per-job Node fetch. RUNNER_TOOL_CACHE is
#     pinned (see ENV below) so this baked cache is actually consulted.
#   - Bun pinned to the version anthropics/claude-code-action@v1 expects
#   - @anthropic-ai/claude-code globally (Claude Code CLI)
#   - Python 3 + pip, with `jsonschema`, `PyJWT`, and `cryptography`
#     globally installed (jsonschema is used by dustfeather/dustfeather's
#     refresh-badges workflow; PyJWT+cryptography cover GitHub App / JWT
#     signing flows from Python — cheap enough to keep in the shared
#     image so any future Python-using workflow gets them for free).
#   - code-review-graph: structural code-graph CLI + MCP server consumed by
#     claude-code-review.yml so the review agent gets blast-radius / impact
#     context (its `serve` is wired into the action via `--mcp-config`).
#   - rtk (Rust Token Killer): compresses command output before it reaches the
#     model. claude.yml + claude-code-review.yml wire it as a PreToolUse hook
#     (`rtk hook claude`) via the action's `settings` input; baked here so any
#     workflow on this runner can use it.
#   - Pre-warmed Bun install cache for anthropics/claude-code-action@v1's
#     own runtime deps (~140 packages incl. @anthropic-ai/claude-agent-sdk,
#     @modelcontextprotocol/sdk, octokit), so the per-job
#     `cd $GITHUB_ACTION_PATH && bun install --production` it runs resolves
#     from /home/runner/.bun/install/cache (~1-2 s) instead of downloading
#     (~20 s).
#
# Wired into the review workflow via `path_to_bun_executable` and
# `path_to_claude_code_executable`, so the action skips its own Bun and
# Claude Code installs at runtime.

ARG BASE_TAG=latest
FROM ghcr.io/actions/actions-runner:${BASE_TAG}

USER root

# Keep BUN_VERSION in sync with anthropics/claude-code-action@v1's
# `setup-bun` step (currently bun-version: 1.3.14). If the action upgrades,
# bump here too — mismatched versions are not a hard failure (the action
# accepts whatever bun is on PATH) but staying aligned avoids surprises.
ARG BUN_VERSION=1.3.14
ARG NODE_MAJOR=26

# Full Node version baked into the runner tool-cache (see the tool-cache RUN
# block below). This is what `setup-node` callers (e.g. node-test.yml's
# default `node-version: 26`) resolve to with zero download. With
# `check-latest: false` (setup-node default) any cached 26.x satisfies a
# `node-version: 26` request, so this stays in use until the image is
# rebuilt. Keep the major aligned with what callers request; bump the patch
# to adopt a newer 26.x.
ARG NODE_TOOLCACHE_VERSION=26.3.0

# kubectl minor track (Kubernetes apt repo path is per-minor). Keep within
# ±1 minor of the target cluster's API server — `kubectl version` warns on
# wider skew, though the basic verbs the deploy uses (get/create/apply)
# tolerate it. Bump when the cluster upgrades.
ARG KUBECTL_MINOR=1.33

# Force GitHub Actions runner to execute JavaScript actions on Node 24
# instead of the action's declared `runs.using` runtime (node16/node20).
# Set at image level so every job using this runner inherits it without
# per-workflow plumbing.
ENV FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true

# Pin the runner tool-cache location. On GitHub-hosted runners this is set
# to /opt/hostedtoolcache; on self-hosted runners it is unset by default and
# `actions/setup-node` falls back to the ephemeral $RUNNER_WORK/_tool dir,
# which is wiped each job — meaning a baked tool-cache would never be found.
# Pinning it here makes setup-node consult the image-baked Node below.
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache

# System packages: jq for the CodeQL gate's response parsing, gh for the
# review tools the action invokes, plus the usual curl/ca-certificates
# bootstrap. NodeSource provides a recent Node runtime so the global npm
# install of @anthropic-ai/claude-code works. python3 + pip are added for
# any workflow that needs a one-off script runtime (no system jsonschema
# package on Debian 12 newer than 4.10, so we install via pip below).
#
# libatomic1 is required by the official Node tarball baked into the runner
# tool-cache below (setup-node callers). The NodeSource `nodejs` package
# pulls it in transitively, but the tarball Node ships no deps, so without
# it `node` from the tool-cache dies with `error while loading shared
# libraries: libatomic.so.1: cannot open shared object file` (exit 127) —
# breaking any setup-node job, e.g. ITGuys-RO/alpaca-opus-trading's deploy.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg jq zip unzip \
        libatomic1 \
        python3 python3-pip; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${KUBECTL_MINOR}/deb/Release.key" \
        | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg; \
    chmod a+r /etc/apt/keyrings/kubernetes-apt-keyring.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${KUBECTL_MINOR}/deb/ /" \
        > /etc/apt/sources.list.d/kubernetes.list; \
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -; \
    apt-get install -y --no-install-recommends gh nodejs kubectl gettext-base; \
    rm -rf /var/lib/apt/lists/*; \
    kubectl version --client; \
    command -v envsubst; \
    if [ ! -e /usr/local/bin/python ] && [ -e /usr/bin/python3 ]; then \
        ln -s /usr/bin/python3 /usr/local/bin/python; \
    fi; \
    if [ ! -e /usr/local/bin/pip ] && [ -e /usr/bin/pip3 ]; then \
        ln -s /usr/bin/pip3 /usr/local/bin/pip; \
    fi

# Bake the official Node tarball into the runner tool-cache so
# `actions/setup-node` finds it locally instead of downloading from
# github.com/actions/node-versions every job (~20 s observed on ARC). The
# layout setup-node expects: <toolcache>/node/<version>/x64 holding the
# unpacked tarball, plus a sibling <version>/x64.complete marker file that
# marks the entry as fully installed. `node --version` from the cache path
# verifies the bake.
#
# chown the WHOLE tool-cache root (not just node/) to `runner`: the dir is
# created root-owned by this `mkdir -p` running as root, but the non-root
# `runner` job user must be able to create sibling tool dirs at runtime —
# e.g. `actions/setup-python` does `mkdir ${RUNNER_TOOL_CACHE}/Python` and
# without write access on the root it dies with
# `mkdir: cannot create directory '/opt/hostedtoolcache/Python': Permission
# denied`, failing every setup-python job. Owning the root fixes setup-python
# (and any future setup-*) while still covering the baked node/ tree.
RUN set -eux; \
    arch=x64; \
    dest="${RUNNER_TOOL_CACHE}/node/${NODE_TOOLCACHE_VERSION}/${arch}"; \
    mkdir -p "$dest"; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_TOOLCACHE_VERSION}/node-v${NODE_TOOLCACHE_VERSION}-linux-x64.tar.gz" \
        | tar -xz -C "$dest" --strip-components=1; \
    touch "${RUNNER_TOOL_CACHE}/node/${NODE_TOOLCACHE_VERSION}/${arch}.complete"; \
    chown -R runner:runner "${RUNNER_TOOL_CACHE}"; \
    "$dest/bin/node" --version

# Bun. The official installer drops to ~/.bun; move into /usr/local/bin so
# it lives on PATH for both root and the `runner` user with no shell rc
# manipulation. `bun-v${BUN_VERSION}` pins the release tag.
RUN set -eux; \
    curl -fsSL https://bun.sh/install | bash -s "bun-v${BUN_VERSION}"; \
    install -m 0755 /root/.bun/bin/bun /usr/local/bin/bun; \
    rm -rf /root/.bun; \
    bun --version

# Claude Code CLI globally. `npm i -g` lands the binary in
# /usr/bin/claude on Debian Node packages — symlink to /usr/local/bin to
# match the bun path style and make the workflow input value uniform.
RUN set -eux; \
    npm install -g @anthropic-ai/claude-code; \
    if [ ! -e /usr/local/bin/claude ] && [ -e /usr/bin/claude ]; then \
        ln -s /usr/bin/claude /usr/local/bin/claude; \
    fi; \
    claude --version

# Python libraries used by reusable workflows. Keep this list small and
# justified — every entry blows up image size for every runner pull.
# jsonschema is consumed by dustfeather/dustfeather's refresh-badges
# renderer; --break-system-packages is required because Debian 12 ships
# Python under PEP 668 (externally-managed-environment). This is safe
# inside an ephemeral runner image where there is no system Python user
# to disturb.
#
# --ignore-installed is required because the upstream actions-runner base
# now preinstalls some of these (e.g. PyJWT) as dpkg-managed Debian
# packages with no pip RECORD file. Without it, pip tries to uninstall the
# Debian copy first and dies with "Cannot uninstall PyJWT x.y.z, RECORD
# file not found. Hint: The package was installed by debian." With it, pip
# installs the pinned versions into /usr/local/lib/.../dist-packages, which
# precedes Debian's /usr/lib/.../dist-packages on sys.path, so imports
# resolve to the pinned versions (the verify step below confirms).
ARG JSONSCHEMA_VERSION=4.26.0
ARG PYJWT_VERSION=2.13.0
ARG CRYPTOGRAPHY_VERSION=48.0.0
RUN set -eux; \
    python -m pip install --no-cache-dir --break-system-packages --ignore-installed \
        "jsonschema==${JSONSCHEMA_VERSION}" \
        "PyJWT==${PYJWT_VERSION}" \
        "cryptography==${CRYPTOGRAPHY_VERSION}"; \
    EXP_JSONSCHEMA="${JSONSCHEMA_VERSION}" EXP_PYJWT="${PYJWT_VERSION}" EXP_CRYPTO="${CRYPTOGRAPHY_VERSION}" \
    python -c "import os, sys, jsonschema, jwt, cryptography; \
checks = {'jsonschema': (jsonschema.__version__, os.environ['EXP_JSONSCHEMA']), \
'PyJWT': (jwt.__version__, os.environ['EXP_PYJWT']), \
'cryptography': (cryptography.__version__, os.environ['EXP_CRYPTO'])}; \
[print(n, got) for n, (got, exp) in checks.items()]; \
bad = {n: got for n, (got, exp) in checks.items() if got != exp}; \
sys.exit('VERSION MISMATCH (Debian copy shadowing pip install?): %s' % bad) if bad else None"

# code-review-graph: parses a repo into a structural graph and serves it over
# MCP. claude-code-review.yml builds the graph (`code-review-graph build`) and
# registers `code-review-graph serve` with the action via `--mcp-config`, so the
# review agent can query blast radius / impact / review context instead of
# re-reading the whole diff. Pinned so image rebuilds are reproducible; bump to
# adopt a newer release. Separate RUN from the pinned-libs block above because
# its dependency closure (tree-sitter parsers, fastmcp, networkx) is unrelated.
ARG CRG_VERSION=2.3.5
RUN set -eux; \
    python -m pip install --no-cache-dir --break-system-packages \
        "code-review-graph==${CRG_VERSION}"; \
    code-review-graph --version

# rtk (Rust Token Killer): single static binary, pinned. Wired into the claude
# workflows as a PreToolUse Bash hook (`rtk hook claude`) that rewrites commands
# to their token-compressed `rtk ...` equivalents — so this binary can rewrite
# every Bash command the review agent runs (incl. the mutating `gh pr review`).
# Static musl build → no libc coupling to the base image. Telemetry is disabled
# per-job via the workflow env (RTK_TELEMETRY_DISABLED), so no network egress
# from the hook. SHA256-pinned against the release `checksums.txt`: the tarball
# is verified before install so a tampered/typosquatted release fails the build
# rather than landing a trusted command rewriter. Bump RTK_SHA256 with the
# version (get it from the release's checksums.txt for the musl asset).
ARG RTK_VERSION=0.42.0
ARG RTK_SHA256=cdd4f87ac97ce958f71b53a991880d6adcc41cc5bca1044175a64630980152be
RUN set -eux; \
    curl -fsSL "https://github.com/rtk-ai/rtk/releases/download/v${RTK_VERSION}/rtk-x86_64-unknown-linux-musl.tar.gz" \
        -o /tmp/rtk.tar.gz; \
    echo "${RTK_SHA256}  /tmp/rtk.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/rtk.tar.gz -C /tmp; \
    install -m 0755 "$(find /tmp -maxdepth 2 -type f -name rtk | head -1)" /usr/local/bin/rtk; \
    rm -rf /tmp/rtk.tar.gz; \
    rtk --version

USER runner

# Pre-warm Bun's install cache with claude-code-action@v1's runtime deps so
# the action's per-job `bun install --production` (run inside
# $GITHUB_ACTION_PATH) resolves from local cache (~1-2 s) instead of
# downloading ~140 packages (~20 s). Cache lives at
# /home/runner/.bun/install/cache and persists in this image layer; the
# ephemeral action checkout dir is discarded but the resolved packages
# stay in cache for the next job.
#
# If claude-code-action@v1 bumps any of its deps after this layer is
# built, the per-job install just downloads the deltas — still much
# faster than the cold install.
RUN set -eux; \
    git clone --depth=1 --branch v1 https://github.com/anthropics/claude-code-action.git /tmp/cca; \
    cd /tmp/cca; \
    bun install --production; \
    rm -rf /tmp/cca
