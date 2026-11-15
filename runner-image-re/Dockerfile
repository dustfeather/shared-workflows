# Reverse-engineering runner image: ghcr.io/dustfeather/actions-runner-re.
# Layered on the shared actions-runner-claude image (runner-image/), so it
# keeps everything that one bakes (gh, jq, kubectl, Node, Temurin JDK 21 in
# the tool cache, claude, bun, Playwright deps) and adds:
#
#   - Node from the official nodejs.org tarball, inside rea-agents' engines
#     range (^22.23.4 || ^24.22.1 || >=26.12.1). The base image's NodeSource
#     node floats with the NodeSource 26.x channel and its tool-cache node is
#     26.3.0, which is outside that range. Node 24 goes first on PATH
#     (/usr/local), and the runner tool cache is replaced with in-range 24.x
#     and 26.x, so plain `node` and `actions/setup-node` both stay in range.
#   - Rust via rustup (exact toolchain), with target x86_64-pc-windows-msvc
#     and the llvm-tools component, for the runner user.
#   - cargo-xwin, plus clang/lld/llvm, which it drives. The Microsoft CRT and
#     Windows SDK that cargo-xwin downloads are NOT baked: redistributing them
#     from a public image is not allowed. Jobs fetch them on first use
#     (`cargo xwin build` does it automatically, or `cargo xwin cache xwin`)
#     and should cache ~/.cache/cargo-xwin themselves.
#   - Ghidra (official release zip, sha256-checked) at GHIDRA_INSTALL_DIR. It
#     runs on the Temurin JDK 21 the base image already puts on PATH.
#   - rizin (official static x86_64 build, sha256-checked) on PATH.
#   - binutils (objdump, strings) and procps. Both come from Ubuntu's archive
#     at whatever version the base's apt state resolves; they are named here
#     so the dependency is explicit rather than inherited by accident.
#
# Everything with a version is pinned by an ARG below; the downloaded
# artifacts are verified against a pinned sha256. Bump an ARG and its hash
# together.

# runner-image-re.yml passes the base by digest so a build is reproducible.
# The default only applies to local builds.
ARG BASE_IMAGE=ghcr.io/dustfeather/actions-runner-claude:latest
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
LABEL org.opencontainers.image.base.name="${BASE_IMAGE}" \
      org.opencontainers.image.source="https://github.com/dustfeather/shared-workflows" \
      org.opencontainers.image.description="actions-runner-claude plus Rust (MSVC target), cargo-xwin, Ghidra, rizin"

USER root

# clang/lld/llvm for cargo-xwin (clang-cl, lld-link, llvm-lib), binutils and
# procps named explicitly (see header).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        clang lld llvm binutils procps xz-utils unzip; \
    rm -rf /var/lib/apt/lists/*; \
    clang --version; \
    for b in lld-link objdump strings ps; do command -v "$b"; done

# Node from the official tarballs, each verified against its pinned
# SHASUMS256 line.
#
# NODE_VERSION is unpacked straight into /usr/local, which precedes the
# base's NodeSource /usr/bin/node on PATH. Unpacked rather than symlinked from
# elsewhere: npm derives its global prefix from the real path of the node
# binary, so /usr/local makes `sudo npm i -g` / `corepack enable` land in
# /usr/local/bin, on PATH. Packages the base installed globally under /usr
# (the claude CLI) stay where they are and now run on this node.
#
# The runner tool cache is REPLACED, not added to: the base bakes node 26.3.0
# there, outside rea-agents' range, and setup-node with `node-version: 26`
# (node-test.yml's default) would pick it over anything newer because
# check-latest defaults to false. So the cache holds exactly NODE_VERSION and
# NODE26_VERSION, both in range (layout <toolcache>/node/<ver>/x64 + a
# sibling x64.complete marker).
ARG NODE_VERSION=24.22.3
ARG NODE_SHA256=4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb
ARG NODE26_VERSION=26.13.1
ARG NODE26_SHA256=5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864
RUN set -eux; \
    fetch() { \
        curl -fsSL --retry 5 --retry-all-errors -o "/tmp/node-v$1.tar.xz" \
            "https://nodejs.org/dist/v$1/node-v$1-linux-x64.tar.xz"; \
        echo "$2  /tmp/node-v$1.tar.xz" | sha256sum -c -; \
    }; \
    fetch "${NODE_VERSION}" "${NODE_SHA256}"; \
    fetch "${NODE26_VERSION}" "${NODE26_SHA256}"; \
    tar -xJf "/tmp/node-v${NODE_VERSION}.tar.xz" -C /usr/local --strip-components=1; \
    rm -rf "${RUNNER_TOOL_CACHE}/node"; \
    for v in "${NODE_VERSION}" "${NODE26_VERSION}"; do \
        tc="${RUNNER_TOOL_CACHE}/node/${v}"; \
        mkdir -p "${tc}/x64"; \
        tar -xJf "/tmp/node-v${v}.tar.xz" -C "${tc}/x64" --strip-components=1; \
        touch "${tc}/x64.complete"; \
        [ "$("${tc}/x64/bin/node" --version)" = "v${v}" ]; \
        rm -f "/tmp/node-v${v}.tar.xz"; \
    done; \
    chown -R runner:runner "${RUNNER_TOOL_CACHE}/node"; \
    [ "$(command -v node)" = /usr/local/bin/node ]; \
    [ "$(node --version)" = "v${NODE_VERSION}" ]; \
    [ "$(npm prefix -g)" = /usr/local ]

# rizin, official static x86_64 build. The tarball is rooted at its install
# prefix (bin/ include/ lib/ share/), so it is unpacked straight into
# /usr/local, which also puts the binaries on PATH.
ARG RIZIN_VERSION=0.9.1
ARG RIZIN_SHA256=9102249a9f0b6319c5334a2e5cf8d9cc3f2035e1d3def027c41f6a90f647e8cf
RUN set -eux; \
    tarball="rizin-v${RIZIN_VERSION}-static-x86_64.tar.xz"; \
    curl -fsSL --retry 5 --retry-all-errors -o "/tmp/${tarball}" \
        "https://github.com/rizinorg/rizin/releases/download/v${RIZIN_VERSION}/${tarball}"; \
    echo "${RIZIN_SHA256}  /tmp/${tarball}" | sha256sum -c -; \
    tar -xJf "/tmp/${tarball}" -C /usr/local; \
    rm -f "/tmp/${tarball}"; \
    rizin -v | grep -F "${RIZIN_VERSION}"

# Ghidra, official release zip from NationalSecurityAgency/ghidra. The zip
# name carries the release date, so both are pinned along with the hash.
# Owned by runner: Ghidra writes nothing under its install dir at run time,
# but a job that installs an extension into it would otherwise need sudo.
ARG GHIDRA_VERSION=12.1.4
ARG GHIDRA_DATE=20260921
ARG GHIDRA_SHA256=ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db
ENV GHIDRA_INSTALL_DIR=/opt/ghidra_${GHIDRA_VERSION}_PUBLIC
RUN set -eux; \
    zip="ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_DATE}.zip"; \
    curl -fsSL --retry 5 --retry-all-errors -o "/tmp/${zip}" \
        "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/${zip}"; \
    echo "${GHIDRA_SHA256}  /tmp/${zip}" | sha256sum -c -; \
    unzip -q "/tmp/${zip}" -d /opt; \
    rm -f "/tmp/${zip}"; \
    [ -x "${GHIDRA_INSTALL_DIR}/support/analyzeHeadless" ]; \
    grep -Fx "application.version=${GHIDRA_VERSION}" "${GHIDRA_INSTALL_DIR}/Ghidra/application.properties"; \
    chown -R runner:runner "${GHIDRA_INSTALL_DIR}"

USER runner

# Rust for the runner user (the job user), at the conventional
# ~/.rustup / ~/.cargo, matching GitHub-hosted runners. rustup-init comes from
# rustup's versioned archive, checked against the pinned sha256; the toolchain
# itself is pinned by exact version, not `stable`, so a rebuild is
# reproducible. Profile minimal (rustc, cargo, rust-std) plus the MSVC target
# and llvm-tools, which cargo-xwin uses for assembly dependencies.
#
# cargo-xwin is built from crates.io with --locked at an exact version. The
# registry and git caches it leaves behind are dropped so they are not in the
# layer; jobs repopulate their own.
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_INIT_SHA256=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55
ARG RUST_VERSION=1.100.0
ARG CARGO_XWIN_VERSION=0.23.1
ENV CARGO_HOME=/home/runner/.cargo \
    RUSTUP_HOME=/home/runner/.rustup \
    PATH=/home/runner/.cargo/bin:${PATH}
RUN set -eux; \
    curl -fsSL --retry 5 --retry-all-errors -o /tmp/rustup-init \
        "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init"; \
    echo "${RUSTUP_INIT_SHA256}  /tmp/rustup-init" | sha256sum -c -; \
    chmod +x /tmp/rustup-init; \
    /tmp/rustup-init -y --no-modify-path --profile minimal \
        --default-toolchain "${RUST_VERSION}" \
        --target x86_64-pc-windows-msvc \
        --component llvm-tools; \
    rm -f /tmp/rustup-init; \
    rustc --version | grep -F "rustc ${RUST_VERSION} "; \
    rustup target list --installed | grep -Fx x86_64-pc-windows-msvc; \
    cargo install --locked "cargo-xwin@${CARGO_XWIN_VERSION}"; \
    rm -rf "${CARGO_HOME}/registry" "${CARGO_HOME}/git"; \
    cargo xwin --version | grep -F "${CARGO_XWIN_VERSION}"
