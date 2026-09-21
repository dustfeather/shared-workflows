#!/usr/bin/env bash
# Behavioural tests for the pnpm-version guard shipped in node-test.yml,
# deploy-cloudflare.yml and release-extension.yml.
#
# Why this exists at all: the guard's warn branch exits 0 on a value the
# workflow itself calls wrong. An inverted `-z`, or a typo that stops the guard
# firing, therefore produces a green run everywhere and stays invisible until
# it reaches a pool that exports a shared pnpm store -- which is the same
# "first run passes, a later one dies" property the guard was written to
# defend against, reintroduced inside the defence. Only asserting exit status
# per branch pins it.
D="$(cd "$(dirname "$0")" && pwd)"
"$D/extract.sh" || { echo "extract.sh failed; refusing to run against a stale guard.sh"; exit 2; }

BOOTSTRAP=11.19.0
pass=0; fail=0

t() { # name expected_exit requested store_value(unset with the literal UNSET) [expected_substring]
  local name=$1 want=$2 want_str=${5:-}
  export PNPM_BOOTSTRAP_VERSION="$BOOTSTRAP" REQUESTED=$3
  # `unset` rather than empty: the two are different states to the guard's
  # `${pnpm_config_store_dir:-}`, and a case that only ever exported an empty
  # string would never exercise the truly-absent path a plain runner has.
  if [ "$4" = UNSET ]; then unset pnpm_config_store_dir; else export pnpm_config_store_dir="$4"; fi
  out=$(bash "$D/guard.sh" 2>&1); rc=$?
  if [ "$rc" != "$want" ]; then
    printf 'FAIL  %-54s exit=%s want=%s\n%s\n' "$name" "$rc" "$want" "$out"; fail=$((fail+1)); return
  fi
  if [ -n "$want_str" ]; then
    local neg=0
    case "$want_str" in "!"*) neg=1; want_str=${want_str#!} ;; esac
    local found=1
    [ "${out#*"$want_str"}" = "$out" ] && found=0
    if [ "$found" != "$((1 - neg))" ]; then
      printf 'FAIL  %-54s exit=%s assertion (neg=%s) on: %s\n%s\n' "$name" "$rc" "$neg" "$want_str" "$out"; fail=$((fail+1)); return
    fi
  fi
  printf 'PASS  %-54s exit=%s\n' "$name" "$rc"; pass=$((pass+1))
}

# The default path. "Says nothing" is the claim, and exit 0 is equally what
# running-and-warning looks like, so assert the silence explicitly.
t "default value, no store: silent"            0 "$BOOTSTRAP" UNSET        "!::"
t "default value, store set: silent"           0 "$BOOTSTRAP" /pnpm-store  "!::"

# The warn branch. Both halves are bound: exit 0 AND the annotation, because
# a guard that fell through to `exit 0` without printing would pass an
# exit-status-only case while having stopped guarding.
t "wrong value, no store: warns, does not fail" 0 10.33.4 UNSET \
  "::warning::pnpm-version is '10.33.4' rather than the pinned action's bootstrap 11.19.0"
t "wrong value, no store: not an ::error::"     0 10.33.4 UNSET "!::error::"
# Empty is not set. A pool that exports the variable but leaves it blank has no
# shared store, so it belongs on the warn path, not the refusing one.
t "wrong value, empty store: treated as unset"  0 10.33.4 ""    "::warning::"

# The refusing branch -- the only one that may fail a caller's build.
t "wrong value, store set: refuses"             1 10.33.4 /pnpm-store \
  "::error::pnpm-version is '10.33.4', but pnpm/action-setup at the SHA pinned in this workflow bootstraps 11.19.0"
t "refusal names the fault it prevents"         1 10.33.4 /pnpm-store "ERR_PNPM_BROKEN_PNPM_INSTALL"
t "refusal names the remedy"                    1 10.33.4 /pnpm-store \
  "Remove the pnpm-version input to take the default"
# A version ABOVE the bootstrap is no safer than one below: self-update runs
# either way. Guards written as a minimum rather than an equality pass the
# case above and fail this one.
t "newer-than-bootstrap is still refused"       1 12.0.0  /pnpm-store "::error::"
# Empty input is not the default. A caller passing "" would otherwise slip
# through an equality check written against an unset variable.
t "empty requested value, store set: refuses"   1 ""      /pnpm-store "::error::"

printf '\npnpm-guard: %s passed, %s failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
