# actions-runner-re

`ghcr.io/dustfeather/actions-runner-re`: the shared `actions-runner-claude`
image (`../runner-image/`) plus reverse-engineering and Windows
cross-compilation tooling. Built by `.github/workflows/runner-image-re.yml` on
a push touching this directory or that workflow, every Monday at 06:00 UTC
(two hours after the base rebuilds), and on manual dispatch. Each build
smoke-tests the image with `smoke.sh` before pushing `:latest` and
`:sha-<commit>`.

## What it adds

| Tool | Version source | Notes |
|---|---|---|
| Node | `NODE_VERSION`, `NODE26_VERSION` (official tarballs, sha256) | 24.x in `/usr/local`, first on PATH. The runner tool cache is replaced with exactly these two, so `setup-node` cannot pick the base's out-of-range 26.3.0. Both must stay inside `rea-agents`' engines range `^22.23.4 \|\| ^24.22.1 \|\| >=26.12.1`; `smoke.sh` checks every cached node. |
| Rust | `RUST_VERSION` via rustup `RUSTUP_VERSION` (sha256) | `~/.cargo`, `~/.rustup` of the `runner` user; target `x86_64-pc-windows-msvc`, component `llvm-tools`. |
| cargo-xwin | `CARGO_XWIN_VERSION` (crates.io, `--locked`) | Uses the distro `clang`, `lld`, `llvm`. |
| Ghidra | `GHIDRA_VERSION` + `GHIDRA_DATE` (release zip, sha256) | `GHIDRA_INSTALL_DIR=/opt/ghidra_<ver>_PUBLIC`; runs on the base's Temurin JDK 21. |
| rizin | `RIZIN_VERSION` (static x86_64 release, sha256) | Unpacked into `/usr/local`. |
| binutils, procps | Ubuntu archive | `objdump`, `strings`, `ps`. |

To bump a downloaded tool, change its version ARG and its sha256 ARG in the
same commit; the build refuses a mismatched hash.

## The Microsoft CRT and Windows SDK are not in the image

`cargo xwin` downloads them on first use. Redistributing them from a public
image is not allowed, so jobs fetch them and should cache them, e.g.:

```yaml
- uses: actions/cache@v6
  with:
    path: ~/.cache/cargo-xwin
    key: cargo-xwin-${{ runner.os }}
- run: cargo xwin build --release --target x86_64-pc-windows-msvc
```

`smoke.sh` builds a hello-world this way inside the throwaway test container,
so every published image has been proven to cross-compile.
