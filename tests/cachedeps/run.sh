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
# Both sets empty compares equal, so the set check alone passes over nothing:
# rename the markers and the step id in one tidy-up pass and this suite goes
# green having exercised zero cases, in the pre-push hook and in the one
# required check on main. tests/pnpm-guard/extract.sh refuses on the same
# shape; extract.sh cannot help here because it is only called per file from
# the set that is empty.
if [ "${#SLICED[@]}" -eq 0 ]; then
  echo "cachedeps: no workflow carries cachedeps-slice markers; refusing to test nothing." >&2
  echo "Either the markers were renamed or the block is gone. Restore them --" >&2
  echo "passing over an empty set is not a pass." >&2
  exit 1
fi
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
  # The step's env: keys live OUTSIDE the markers, so a slice on its own does
  # not record which variable it may read. Exporting both names to every slice
  # would make the copies agree on a mistake instead of catching it: paste
  # deploy-cloudflare's slice into node-test.yml and `${INSTALL:-.}` silently
  # reads "." for every working-dir, because that expansion does not trip
  # `set -u`. In production that emits root-only globs and a caller whose
  # lockfile sits under working-dir gets setup-node's hard failure -- and only
  # that caller, so the default shape stays green. Derived per slice, like the
  # file set.
  bash tests/cachedeps/extract.sh --env-keys "$f" > "$tmp/$(basename "$f").env" || exit 1
  dirvar=$(grep -v '^OVERRIDE$' "$tmp/$(basename "$f").env")
  if [ "$(printf '%s' "$dirvar" | wc -l)" -ne 0 ] || [ -z "$dirvar" ]; then
    echo "cachedeps: $f's cachedeps step must declare OVERRIDE plus exactly one" >&2
    echo "directory variable; it declares: $(tr '\n' ' ' < "$tmp/$(basename "$f").env")" >&2
    exit 1
  fi
  # A slice that never mentions its own variable cannot be reading it.
  if ! grep -q "$dirvar" "$tmp/$(basename "$f").sh"; then
    echo "cachedeps: $f declares \$$dirvar but its slice never references it." >&2
    echo "Most likely the slice was pasted from the other workflow, which is" >&2
    echo "silent in production: \${OTHER:-.} does not trip set -u." >&2
    exit 1
  fi
done

passed=0
failed=0

# Runs every slice with the same inputs and returns their common output, or
# fails the case when they disagree.
derive() {
  local dir=$1 override=$2 first="" out=""
  for f in "${SLICED[@]}"; do
    local gh="$tmp/out" base
    base=$(basename "$f")
    : > "$gh"
    # ONLY this slice's own directory variable is exported. The other name is
    # absent, so a slice reading it falls back to "." and fails the very first
    # case instead of agreeing with its sibling on the wrong answer.
    local var
    var=$(grep -v '^OVERRIDE$' "$tmp/$base.env")
    GITHUB_OUTPUT="$gh" OVERRIDE="$override" \
      env "$var=$dir" bash "$tmp/$base.sh" || { echo "SLICE-FAILED:$f"; return 1; }
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
  if ! got=$(derive "$dir" "$override"); then
    echo "FAIL  $name  ($got)"
    failed=$((failed + 1))
    return
  fi
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
  # The status matters more here than in check(): an absence assertion is
  # satisfied by ANY output that lacks the needle, and derive's failure strings
  # ("SLICE-FAILED:<f>", "DRIFT:<f>") contain neither `*lock*` nor `Cargo`, so
  # a broken slice would report PASS on all three. Assert the status alongside
  # the text, never the text alone -- the rule the pnpm guard already carries.
  if ! got=$(derive "$dir" "$override"); then
    echo "FAIL  $name  ($got)"
    failed=$((failed + 1))
    return
  fi
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
# A floor, not a formality: "0 passed, 0 failed" is what every way of losing
# the cases looks like, and it exits 0 on both counts without it.
if [ "$passed" -eq 0 ]; then
  echo "cachedeps: no case ran; that is a failure, not a pass." >&2
  exit 1
fi
[ "$failed" -eq 0 ]
