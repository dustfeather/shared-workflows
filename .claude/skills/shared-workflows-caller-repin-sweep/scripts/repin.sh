#!/usr/bin/env bash
# Inventory and repin callers of dustfeather/shared-workflows reusable workflows.
#
#   repin.sh --from v5 --to v6            inventory only (default; changes nothing)
#   repin.sh --from v5 --to v6 --apply    clone, rewrite, commit, push to default branch
#   repin.sh --from v5 --to v6 --verify   fresh clones, LOOSE detector, second opinion
#
# Read SKILL.md next to this script before changing anything here. Two of the
# guards below look redundant and are not: the tag-exists precheck (a ref that
# resolves to nothing is startup_failure in every caller at once, with empty
# logs) and the residue check written against a DIFFERENT pattern than the
# substitution (reusing the sed pattern proves only that sed ran).
set -euo pipefail

LIB_OWNER=dustfeather
LIB_REPO=shared-workflows
OWNERS=(dustfeather ITGuys-RO)

FROM=""; TO=""; MODE=inventory
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to)   TO="$2";   shift 2 ;;
    --apply)  MODE=apply;  shift ;;
    --verify) MODE=verify; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$FROM" ] && [ -n "$TO" ] || { echo "need --from <ref> and --to <ref>" >&2; exit 2; }

command -v gh  >/dev/null || { echo "gh not found" >&2; exit 2; }
command -v git >/dev/null || { echo "git not found" >&2; exit 2; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/repin-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# --- Guard: the destination tag must already exist -------------------------
# Repinning to a tag that has not been cut resolves to nothing: startup_failure,
# zero jobs, empty logs, every caller simultaneously. That reads as an outage,
# not as a bad ref, which is what makes it expensive to diagnose.
if ! dest_sha=$(gh api "repos/$LIB_OWNER/$LIB_REPO/git/ref/tags/$TO" --jq .object.sha 2>/dev/null); then
  echo "REFUSING: tag '$TO' does not resolve in $LIB_OWNER/$LIB_REPO." >&2
  echo "Cut it first. A caller pointed at a nonexistent ref fails startup with empty logs." >&2
  exit 1
fi
echo "destination $TO -> $dest_sha"

# --- Enumerate callers -----------------------------------------------------
# NOT `gh search code`: it is index-backed, lags, and misses private and
# low-traffic repos, returning a short list that looks complete. Enumerate the
# repos, then read their contents.
echo "enumerating repos across: ${OWNERS[*]}"
: > "$WORK/repos"
for owner in "${OWNERS[@]}"; do
  gh repo list "$owner" --limit 500 --no-archived \
     --json nameWithOwner,defaultBranchRef \
     --jq '.[] | select(.defaultBranchRef != null)
           | "\(.nameWithOwner)\t\(.defaultBranchRef.name)"' >> "$WORK/repos"
done
# The library repo's own pins are out of scope: pr-checks.yml names itself in
# its own paths-ignore, so its bump needs a direct push, handled separately.
grep -v "^$LIB_OWNER/$LIB_REPO	" "$WORK/repos" > "$WORK/repos.f" || true
mv "$WORK/repos.f" "$WORK/repos"
echo "repos to inspect: $(wc -l < "$WORK/repos")"

# STRICT: what we rewrite. Anchored on the full library path so a bare `@v5` on
# some unrelated third-party action is never touched.
strict_re="$LIB_OWNER/$LIB_REPO/\.github/workflows/[A-Za-z0-9._-]+\.yml@${FROM}\b"
# LOOSE: independently written, deliberately sloppier — any ref to this library
# at any ref at all. Used to FIND shapes strict misses, never to rewrite.
loose_re="$LIB_OWNER/$LIB_REPO/[^[:space:]\"']*@[A-Za-z0-9._/-]+"

sed_from="$LIB_OWNER/$LIB_REPO/\\.github/workflows/\\([A-Za-z0-9._-]*\\)\\.yml@${FROM}"
sed_to="$LIB_OWNER/$LIB_REPO/.github/workflows/\\1.yml@${TO}"

t_repos=0; t_files=0; t_refs=0; asks=0; fails=()

while IFS=$'\t' read -r slug branch; do
  [ -n "$slug" ] || continue
  d="$WORK/${slug//\//__}"
  if ! git clone -q --depth 1 --branch "$branch" "https://github.com/$slug.git" "$d" 2>/dev/null; then
    echo "SKIP    $slug — clone failed (no access, empty, or renamed)" >&2
    fails+=("$slug"); continue
  fi
  wd="$d/.github/workflows"
  [ -d "$wd" ] || { rm -rf "$d"; continue; }

  # Every reference to this library, whatever its ref. Classify before acting:
  # an exact tag or a SHA is a deliberate freeze and is NOT ours to float.
  mapfile -t all < <(grep -rhoE "$loose_re" "$wd" 2>/dev/null | sort -u || true)
  [ ${#all[@]} -gt 0 ] || { rm -rf "$d"; continue; }

  for ref in "${all[@]}"; do
    r="${ref##*@}"
    case "$r" in
      "$TO")            : ;;                                  # current, nothing to do
      "$FROM")          : ;;                                  # handled by the rewrite below
      v[0-9]*.[0-9]*.*) echo "ASK     $slug — exact tag @$r (deliberate freeze?)"; asks=$((asks+1)) ;;
      main|master)      echo "ASK     $slug — tracks @$r, not a tag";              asks=$((asks+1)) ;;
      v[0-9]*)          echo "FROZEN  $slug — @$r, an older major (not $FROM)" ;;
      */*)              echo "TRANSIENT $slug — @$r looks like an e2e feature pin" ;;
      *)                echo "ASK     $slug — @$r (sha or unrecognised ref)";      asks=$((asks+1)) ;;
    esac
  done

  if [ "$MODE" = verify ]; then
    # Second opinion, on a FRESH clone, with the LOOSE pattern — and deliberately
    # BEFORE the strict scan below, not after it. Gating verification on a strict
    # hit would only ever re-examine repos the rewrite already handles, which is
    # the one population that cannot still be wrong. What verify is for is the
    # opposite case: a ref the strict pattern never matched, so the rewrite
    # skipped it and the repo is half-repinned.
    if grep -rnE "$LIB_OWNER/$LIB_REPO/[^[:space:]\"']*@${FROM}\b" "$wd" 2>/dev/null; then
      echo "RESIDUE $slug — still at @$FROM after a repin" >&2
      fails+=("$slug")
    else
      echo "ok      $slug — no @$FROM refs of any shape"
    fi
    rm -rf "$d"; continue
  fi

  mapfile -t hits < <(grep -rlE "$strict_re" "$wd" 2>/dev/null || true)
  if [ ${#hits[@]} -eq 0 ]; then
    echo "ok      $slug — nothing at @$FROM"
    rm -rf "$d"; continue
  fi
  n=0
  for f in "${hits[@]}"; do
    n=$(( n + $(grep -cE "$strict_re" "$f") ))
  done

  echo "$([ "$MODE" = apply ] && echo 'EDIT   ' || echo 'would  ')$slug — ${#hits[@]} file(s), $n ref(s)"
  t_repos=$((t_repos+1)); t_files=$((t_files+${#hits[@]})); t_refs=$((t_refs+n))
  [ "$MODE" = apply ] || { rm -rf "$d"; continue; }

  for f in "${hits[@]}"; do sed -i "s|$sed_from|$sed_to|g" "$f"; done

  # Residue check, written against the LOOSE pattern on purpose. Using the
  # strict one here would be tautological: sed just removed every match of it,
  # so it can only ever report clean. What survives a rewrite is precisely the
  # shape the strict pattern never saw, and that is a half-repinned repo — the
  # failure that hides longest because the repo still half-works.
  if grep -rqE "$LIB_OWNER/$LIB_REPO/[^[:space:]\"']*@${FROM}\b" "$wd" 2>/dev/null; then
    echo "ABORT   $slug — refs at @$FROM survive the rewrite; NOT committing" >&2
    grep -rnE "$LIB_OWNER/$LIB_REPO/[^[:space:]\"']*@${FROM}\b" "$wd" >&2 || true
    fails+=("$slug"); rm -rf "$d"; continue
  fi

  git -C "$d" add -A
  git -C "$d" commit -q -F - <<MSG
ci: re-pin shared-workflows to @$TO

$FROM froze when $TO was cut and receives no further fixes, so staying on it is
not neutral. Mechanical one-line bump, applied uniformly across every caller and
verified by grep on a fresh clone.
MSG
  # Not `push ... | tail`: a pipeline's status is the LAST command's, so piping
  # into tail reports tail's success and every rejected push looks like it
  # landed. Capture, then test the push's own status.
  if ! push_out=$(git -C "$d" push origin "HEAD:$branch" 2>&1); then
    echo "PUSH FAILED $slug" >&2
    printf '%s\n' "$push_out" | tail -5 >&2
    fails+=("$slug")
  fi
  rm -rf "$d"
done < "$WORK/repos"

echo
if [ "$MODE" = verify ]; then
  [ ${#fails[@]} -eq 0 ] && echo "VERIFIED: no repo still references @$FROM."
else
  echo "repos: $t_repos   files: $t_files   refs: $t_refs"
  [ "$MODE" = apply ] || echo "INVENTORY ONLY — nothing pushed. Re-run with --apply."
fi
[ "$asks" -eq 0 ] || echo "$asks ref(s) need a human decision — see ASK rows above." >&2
if [ ${#fails[@]} -gt 0 ]; then
  echo "NEEDS ATTENTION: ${fails[*]}" >&2
  exit 1
fi
