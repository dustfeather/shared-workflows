#!/usr/bin/env bash
# Smoke test for actions-runner-re. runner-image-re.yml runs it inside the
# freshly built image, as the image's default user (runner, the ARC job user),
# before anything is pushed: a failure here keeps the image off GHCR.
set -euo pipefail

say() { printf '\n== %s\n' "$*"; }
die() { printf 'SMOKE FAIL: %s\n' "$*" >&2; exit 1; }

say node
# rea-agents 6.9.0 engines: ^22.23.4 || ^24.22.1 || >=26.12.1, checked
# without an npm dependency. Applied to the node on PATH AND to every node in
# the runner tool cache, since setup-node puts a cached one first on PATH.
in_range() {
  "$1" -e '
  const [maj, min, pat] = process.versions.node.split(".").map(Number);
  const ge = (m, p) => min > m || (min === m && pat >= p);
  const ok = (maj === 22 && ge(23, 4)) || (maj === 24 && ge(22, 1)) ||
             maj > 26 || (maj === 26 && ge(12, 1));
  if (!ok) { console.error("node " + process.version + " outside ^22.23.4 || ^24.22.1 || >=26.12.1"); process.exit(1); }
'
}
node --version
in_range node || die "node on PATH out of range"
shopt -s nullglob
cached=("${RUNNER_TOOL_CACHE:?}"/node/*/x64/bin/node)
shopt -u nullglob
[ "${#cached[@]}" -gt 0 ] || die "no node in the runner tool cache"
for n in "${cached[@]}"; do
  echo "$n $("$n" --version)"
  in_range "$n" || die "tool-cache node out of range: $n"
done

say cargo
cargo --version
rustc --version
rustup target list --installed | grep -Fx x86_64-pc-windows-msvc || die "msvc target not installed"

say cargo-xwin
cargo xwin --version

say rizin
rizin -v

say binutils / procps
objdump --version | head -1
strings --version | head -1
ps --version

say ghidra
: "${GHIDRA_INSTALL_DIR:?GHIDRA_INSTALL_DIR not set}"
echo "GHIDRA_INSTALL_DIR=$GHIDRA_INSTALL_DIR"
# With no arguments analyzeHeadless prints its usage and exits nonzero, so the
# exit status says nothing; the usage text proves the launcher found a JDK and
# started.
out=$("$GHIDRA_INSTALL_DIR/support/analyzeHeadless" 2>&1 || true)
printf '%s\n' "$out" | head -5
grep -q 'analyzeHeadless' <<<"$out" || die "analyzeHeadless printed no usage"
grep -qi 'usage' <<<"$out" || die "analyzeHeadless printed no usage"

say rea-agents doctor
# Audit-wide doctor is never healthy here: it also checks providers this image
# does not carry (Hopper, IDA, jadx, Kuna) and client registrations. So the
# exit status is not the signal; the node and ghidra checks are.
doc=$(npx -y rea-agents@6.9.0 doctor --json) || true
[ -n "$doc" ] || die "rea-agents doctor printed nothing"
jq -c '.scope_checks[] | {name, ok, classification, detail}' <<<"$doc"
for check in node ghidra; do
  jq -e --arg n "$check" '[.scope_checks[] | select(.name == $n)] | length == 1 and all(.ok)' <<<"$doc" >/dev/null \
    || die "rea-agents doctor check '$check' not ok"
done

say cargo xwin cross-compile
# End-to-end: build a hello-world for x86_64-pc-windows-msvc. This downloads
# the Microsoft CRT and Windows SDK into this throwaway container only; the
# image never carries them.
xcache="${XWIN_CACHE_DIR:-$HOME/.cache/cargo-xwin}"
[ ! -e "$xcache" ] || die "MSVC CRT/SDK cache baked into the image: $xcache"
work=$(mktemp -d)
cargo new --quiet --bin "$work/hello"
(cd "$work/hello" && cargo xwin build --quiet --release --target x86_64-pc-windows-msvc)
exe="$work/hello/target/x86_64-pc-windows-msvc/release/hello.exe"
[ -f "$exe" ] || die "no hello.exe"
objdump -f "$exe" | grep -q 'pei-x86-64' || die "hello.exe is not a PE32+ x86-64 image"
rizin -q -c 'iI~machine' "$exe"

say OK
