#!/usr/bin/env bash
# Exercises the cache-glob block shipped in node-test.yml and
# deploy-cloudflare.yml. It writes actions/setup-node's `cache-dependency-path`,
# and with `cache` set setup-node FAILS THE STEP rather than skipping when
# nothing in that list resolves -- so a wrong list is a hard failure on, among
# others, the deploy path. There is no way to run it from a local checkout
# (`uses:` resolves through GitHub), which is exactly why it is extracted and
# run here.
#
# The two copies are deliberately NOT byte-identical -- one reads WORKING_DIR,
# the other INSTALL -- so the pnpm-guard's identity check does not apply. Every
# case therefore runs against BOTH slices with both variables set to the same
# value, and asserts they agree: that catches drift without demanding sameness
# the files cannot have.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

mapfile -t SLICED < <(grep -l "cachedeps-slice-begin" .github/workflows/*.yml | sort)
# Anchored: an unanchored match also finds prose MENTIONING the step id --
# guard-tests.yml carries exactly that in a comment -- which would report a
# copy that does not exist and make this check impossible to satisfy.
mapfile -t STEPPED < <(grep -lE "^[[:space:]]*id: cachedeps[[:space:]]*$" .github/workflows/*.yml | sort)
if [ "${SLICED[*]}" != "${STEPPED[*]}" ]; then
  echo "cachedeps: the marked set and the cachedeps-step set disagree." >&2
  echo "  markers: ${SLICED[*]:-none}" >&2
  echo "  steps:   ${STEPPED[*]:-none}" >&2
  echo "A step with no slice is a block nobody tests; a slice in a workflow" >&2
  echo "with no step is a test with nothing shipping behind it. Deriving the" >&2
  echo "set rather than listing it is what stops a third copy arriving" >&2
  echo "unnoticed." >&2
  exit 1
fi
echo "cachedeps: ${#SLICED[@]} marked copies: ${SLICED[*]}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for f in "${SLICED[@]}"; do
  bash tests/cachedeps/extract.sh "$f" > "$tmp/$(basename "$f").sh" || exit 1
done

passed=0
failed=0

# Runs every slice with the same inputs and returns their common output, or
# fails the case when they disagree.
derive() {
  local dir=$1 override=$2 first="" out=""
  for f in "${SLICED[@]}"; do
    local gh="$tmp/out"
    : > "$gh"
    GITHUB_OUTPUT="$gh" WORKING_DIR="$dir" INSTALL="$dir" OVERRIDE="$override" \
      bash "$tmp/$(basename "$f").sh" || { echo "SLICE-FAILED:$f"; return 1; }
    out=$(cat "$gh")
    if [ -z "$first" ]; then
      first=$out
    elif [ "$out" != "$first" ]; then
      echo "DRIFT:$f"
      return 1
    fi
  done
  printf '%s' "$first"
}

check() {
  local name=$1 dir=$2 override=$3 expect=$4 got
  got=$(derive "$dir" "$override")
  if [ "$got" = "$expect" ]; then
    echo "PASS  $name"
    passed=$((passed + 1))
  else
    echo "FAIL  $name"
    echo "      expected:"; printf '%s\n' "$expect" | sed 's/^/        /'
    echo "      got:"; printf '%s\n' "$got" | sed 's/^/        /'
    failed=$((failed + 1))
  fi
}

# Asserts a substring is ABSENT, which is how the churn regression is caught:
# a `*lock*` glob passes every shape assertion above and still keys the cache
# off Cargo.lock.
refute() {
  local name=$1 dir=$2 override=$3 needle=$4 got
  got=$(derive "$dir" "$override")
  if [[ "$got" != *"$needle"* ]]; then
    echo "PASS  $name"
    passed=$((passed + 1))
  else
    echo "FAIL  $name  (found ${needle@Q} in the output)"
    failed=$((failed + 1))
  fi
}

ROOT_ONLY='globs<<CACHEDEPS_EOF
package-lock.json
npm-shrinkwrap.json
yarn.lock
pnpm-lock.yaml
CACHEDEPS_EOF'

SUBDIR='globs<<CACHEDEPS_EOF
app/package-lock.json
app/npm-shrinkwrap.json
app/yarn.lock
app/pnpm-lock.yaml
package-lock.json
npm-shrinkwrap.json
yarn.lock
pnpm-lock.yaml
CACHEDEPS_EOF'

check "root working-dir: one copy, no duplicates"     "."      "" "$ROOT_ONLY"
check "subdirectory: the dir's copy, then the root's" "app"    "" "$SUBDIR"
# `working-dir: app/` is a caller typo away and must not emit `app//...`, which
# hashes nothing and so trips setup-node's hard failure.
check "a trailing slash is stripped"                  "app/"   "" "$SUBDIR"
# An empty value reaches here as "", which unguarded emits "/package-lock.json"
# -- an absolute path that resolves nowhere.
check "an empty value is read as the root"            ""       "" "$ROOT_ONLY"
check "nested dir keeps its full path"                "a/b"    "" "$(printf 'globs<<CACHEDEPS_EOF\na/b/package-lock.json\na/b/npm-shrinkwrap.json\na/b/yarn.lock\na/b/pnpm-lock.yaml\npackage-lock.json\nnpm-shrinkwrap.json\nyarn.lock\npnpm-lock.yaml\nCACHEDEPS_EOF')"

# The override is the documented escape hatch for a layout neither glob finds,
# so it must pass through untouched -- including its newlines.
check "override replaces the derived list"            "app"    "custom/one.lock" "$(printf 'globs<<CACHEDEPS_EOF\ncustom/one.lock\nCACHEDEPS_EOF')"
check "a multi-line override keeps its lines"         "app"    "$(printf 'a/x.lock\nb/y.lock')" "$(printf 'globs<<CACHEDEPS_EOF\na/x.lock\nb/y.lock\nCACHEDEPS_EOF')"

# The delimiter assertions above already cover this, but state it directly:
# an `exit 0` used as a shortcut inside the { } group redirected to
# GITHUB_OUTPUT would skip the closing delimiter and leave the file
# unterminated, which Actions reports as a mangled output, not as this step
# failing.
check "the heredoc delimiter closes on the root path" "."      "" "$ROOT_ONLY"

refute "no bare *lock* glob survives (root)"          "."      "" "*lock*"
refute "no bare *lock* glob survives (subdirectory)"  "app"    "" "*lock*"
# Cargo.lock at the root would satisfy a `*lock*` lookup and key the node cache
# off a Rust file. Nothing in the derived list may match it.
refute "Cargo.lock cannot satisfy the lookup"         "app"    "" "Cargo"

echo
echo "cachedeps: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
