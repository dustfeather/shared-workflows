# Pre-baked ARC self-hosted runner image with the tooling needed by
# `claude-code-review.yml` and the other reusable workflows in this repo.
#
# Layered on top of the upstream ARC runner image so the `runner` user,
# `/home/runner/run.sh` entrypoint, and `externals/` dir (consumed by the
# `init-dind-externals` initContainer) stay intact.
#
# What this bakes in beyond the base image:
#   - git (already in base, kept for clarity), jq, gh
#   - Node.js (NodeSource major release)
#   - Bun pinned to the version anthropics/claude-code-action@v1 expects
#   - @anthropic-ai/claude-code globally (Claude Code CLI)
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
ARG NODE_MAJOR=22

# System packages: jq for the CodeQL gate's response parsing, gh for the
# review tools the action invokes, plus the usual curl/ca-certificates
# bootstrap. NodeSource provides a recent Node runtime so the global npm
# install of @anthropic-ai/claude-code works.
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

USER runner
